"""
getdeal1k.py — Crawl deal từ 2 nguồn:
  1. deal1k.vn     (Playwright intercept API)
  2. shopee1k.com  (requests + parse Next.js JSON trong HTML)

Union data, dedup theo (shop_id, item_id).
Mỗi lần chạy xoá data cũ, ghi data mới hoàn toàn.
Output: data.json

Cài đặt:
  pip install playwright requests
  playwright install chromium
"""

import json
import os
import random
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import quote, urlencode

import requests
from playwright.sync_api import sync_playwright, Response

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
AFFILIATE_ID = "17351620126"
OUTPUT_FILE  = "data.json"

SALE_SLOTS   = [0, 2, 9, 12, 15, 17, 19, 21]
PRICE_RANGES = ["1K", "9K", "29K"]

VN_TZ    = timezone(timedelta(hours=7))
S2_URL   = "https://shopee1k.com"
IMG_BASE = "https://down-bs-vn.img.susercontent.com/"

S2_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
]

S2_BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "Referer": "https://shopee1k.com/",
}


# ─────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────
def get_slots_to_fetch() -> list[tuple[int, str]]:
    """
    Trả về 4 slot [(unix_ts_utc, "HH:00")]:
      - Slot hiện tại (lớn nhất <= giờ VN hiện tại)
      - 3 slot kế tiếp theo vòng tròn

    Ví dụ 21:30 VN → [(ts_21h, "21:00"), (ts_00h_mai, "00:00"),
                       (ts_02h_mai, "02:00"), (ts_09h_mai, "09:00")]
    """
    now_vn       = datetime.now(VN_TZ)
    current_hour = now_vn.hour

    # Tìm index slot hiện tại
    current_idx = None
    for i, slot in enumerate(SALE_SLOTS):
        if current_hour >= slot:
            current_idx = i

    # Chưa đến slot đầu tiên trong ngày → slot hiện tại là slot cuối ngày hôm qua
    if current_idx is None:
        current_idx   = len(SALE_SLOTS) - 1
        use_yesterday = True
    else:
        use_yesterday = False

    result = []
    for offset in range(4):
        idx        = current_idx + offset
        day_offset = idx // len(SALE_SLOTS)    # 0 = hôm nay/hôm qua, 1 = ngày sau
        slot_hour  = SALE_SLOTS[idx % len(SALE_SLOTS)]

        if use_yesterday:
            base_day = now_vn - timedelta(days=1) + timedelta(days=day_offset)
        else:
            base_day = now_vn + timedelta(days=day_offset)

        slot_dt = base_day.replace(hour=slot_hour, minute=0, second=0, microsecond=0)
        ts      = int(slot_dt.astimezone(timezone.utc).timestamp())
        result.append((ts, f"{slot_hour:02d}:00"))

    return result


def build_sub_id(price: int, time_slot: str, crawled_at: datetime) -> str:
    if price <= 1000:
        prefix = "1k"
    elif price <= 9000:
        prefix = "9k"
    else:
        prefix = "ot"
    hh   = time_slot.split(":")[0] if ":" in time_slot else time_slot
    ddmm = crawled_at.strftime("%d%m")
    return f"{prefix}{hh}{ddmm}"


def build_aff_link(shop_id: str, item_id: str, price: int, time_slot: str, crawled_at: datetime) -> str:
    landing = f"https://shopee.vn/opaanlp/{shop_id}/{item_id}"
    sub_id  = build_sub_id(price, time_slot, crawled_at)
    return (
        f"https://s.shopee.vn/an_redir"
        f"?origin_link={quote(landing, safe='')}"
        f"&affiliate_id={AFFILIATE_ID}"
        f"&sub_id={sub_id}"
    )


def normalize(shop_id: str, item_id: str, title: str, price: int,
              original_price: int, discount_pct: int, quantity: int,
              time_slot: str, image_url: str, crawled_at: datetime) -> dict:
    """Schema chuẩn dùng chung cho cả 2 nguồn."""
    return {
        "_shop_id":       shop_id,
        "_item_id":       item_id,
        "title":          title.strip(),
        "price":          price,
        "original_price": original_price,
        "discount_pct":   discount_pct,
        "quantity":       quantity,
        "time_slot":      time_slot,   # dạng "HH:00", ví dụ "17:00"
        "image_url":      image_url,
        "product_link":   build_aff_link(shop_id, item_id, price, time_slot, crawled_at),
    }


# ─────────────────────────────────────────────
# Nguồn 1: deal1k.vn (Playwright intercept)
# ─────────────────────────────────────────────
def parse_s1_response(payload: dict, slot_label: str, crawled_at: datetime) -> list[dict]:
    """
    slot_label luôn lấy từ get_slots_to_fetch() — KHÔNG dùng activeSlot trong
    response vì activeSlot phản ánh slot đang hiển thị trên UI, không phải slot
    của request đang gọi. Ví dụ 21:30 gọi API slot 00:00 nhưng activeSlot vẫn
    trả về 1778xxx (timestamp 21h).
    """
    rows = []
    for item in payload.get("products", []):
        shopee_url = item.get("shopeeUrl", "")
        m = re.search(r"/product/(\d+)/(\d+)", shopee_url)
        if not m:
            continue
        shop_id = m.group(1)
        item_id = m.group(2)

        price        = int(item.get("price") or 0)
        original_raw = item.get("originalPrice") or 0
        # originalPrice dạng cents (×100000): 2000000000 = 20.000đ
        orig_price   = int(original_raw / 100000) if original_raw > 1_000_000 else int(original_raw)

        rows.append(normalize(
            shop_id, item_id,
            item.get("name") or "",
            price, orig_price,
            int(item.get("discount") or 0),
            int(item.get("stock") or 0),
            slot_label,                        # ← dùng label từ caller, không từ response
            item.get("image") or "",
            crawled_at,
        ))
    return rows


def crawl_s1(slots: list[tuple[int, str]], crawled_at: datetime) -> list[dict]:
    print("\n[S1] deal1k.vn — bắt đầu crawl ...")
    all_rows: list[dict]           = []
    api_results: dict[tuple, dict] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Linux; Android 13; SM-G981B) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/146.0.0.0 Mobile Safari/537.36"
            ),
            viewport={"width": 390, "height": 844},
        )
        page = context.new_page()

        def on_response(response: Response):
            if "/api/flash-deals" not in response.url:
                return
            st_match = re.search(r"startTime=(\d+)", response.url)
            if not st_match:
                return
            st = int(st_match.group(1))
            for pr in PRICE_RANGES:
                if f"priceRange={pr}" in response.url:
                    try:
                        raw      = response.text()
                        data     = json.loads(raw)
                        payload  = data.get("data") or data
                        items    = payload.get("products") or []
                        api_results[(pr, st)] = payload
                        print(f"  [S1] {pr} startTime={st} → {len(items)} sản phẩm")
                    except Exception as e:
                        print(f"  [S1] {pr} parse lỗi — {e}")
                    break

        page.on("response", on_response)

        print("  [S1] Vào trang chủ deal1k.vn ...")
        page.goto("https://deal1k.vn", wait_until="networkidle", timeout=30000)

        for start_time, slot_label in slots:
            for price_range in PRICE_RANGES:
                params  = urlencode({
                    "mode": "flash-sale", "priceRange": price_range,
                    "page": 1, "pageSize": 100, "startTime": start_time,
                })
                api_url = f"/api/flash-deals?{params}"
                print(f"  [S1] slot={slot_label} priceRange={price_range} ...")
                page.evaluate(
                    f"fetch('{api_url}', {{method:'GET',headers:{{'accept':'*/*'}}}})"
                )
                page.wait_for_timeout(2000)

        browser.close()

    # Parse — dùng slot_label từ get_slots_to_fetch, KHÔNG từ response
    for start_time, slot_label in slots:
        for price_range in PRICE_RANGES:
            payload = api_results.get((price_range, start_time))
            if not payload or not payload.get("products"):
                print(f"  [S1] slot={slot_label} {price_range} — không có data")
                continue
            rows = parse_s1_response(payload, slot_label, crawled_at)
            print(f"  [S1] slot={slot_label} {price_range} → {len(rows)} sản phẩm")
            all_rows.extend(rows)

    print(f"[S1] Tổng: {len(all_rows)} sản phẩm")
    return all_rows


# ─────────────────────────────────────────────
# Nguồn 2: shopee1k.com (requests + Next.js JSON)
# ─────────────────────────────────────────────
def s2_fetch_html() -> str:
    headers = {**S2_BASE_HEADERS, "User-Agent": random.choice(S2_USER_AGENTS)}
    resp    = requests.get(S2_URL, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.text


def s2_extract_payload(html: str) -> dict:
    """
    shopee1k.com dùng Next.js SSR — data nhúng trong script tag:
        self.__next_f.push([1, "4:[..., {bundles: [...]}]"])
    Mỗi bundle có slot riêng với timeLabel dạng "17:00".
    """
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
    for script in scripts:
        if "self.__next_f.push" not in script or "bundles" not in script:
            continue
        m = re.search(
            r'self\.__next_f\.push\(\[1,\s*("(?:[^"\\]|\\.)*")\]\)',
            script, re.DOTALL
        )
        if not m:
            continue
        inner = json.loads(m.group(1))
        m2    = re.search(r"^\d+:(\[.*\]|\{.*\})", inner, re.DOTALL)
        if not m2:
            continue
        data    = json.loads(m2.group(1))
        payload = data[3] if isinstance(data, list) and len(data) > 3 else data
        if "bundles" in payload:
            return payload
    raise ValueError("[S2] Không tìm thấy JSON payload trong HTML")


def crawl_s2(crawled_at: datetime) -> list[dict]:
    print("\n[S2] shopee1k.com — bắt đầu crawl ...")
    try:
        html    = s2_fetch_html()
        payload = s2_extract_payload(html)
    except Exception as e:
        print(f"[S2] FAILED — {e}")
        return []

    rows = []
    for bundle in payload.get("bundles", []):
        # time_slot lấy thẳng từ slot.timeLabel của bundle, dạng "17:00"
        time_slot = bundle.get("slot", {}).get("timeLabel", "")

        for item in bundle.get("products", []):
            img_key   = item.get("img", "")
            image_url = img_key if img_key.startswith("http") else f"{IMG_BASE}{img_key}_tn"
            shop_id   = str(item.get("shop_id", ""))
            item_id   = str(item.get("item_id", ""))
            price     = int(item.get("price", 0))

            rows.append(normalize(
                shop_id, item_id,
                item.get("title") or "",
                price,
                int(item.get("original_price", 0)),
                int(item.get("percent", 0)),
                int(item.get("amount", 0)),
                time_slot,
                image_url,
                crawled_at,
            ))

    print(f"[S2] Tổng: {len(rows)} sản phẩm")
    return rows


# ─────────────────────────────────────────────
# Union + Dedup
# ─────────────────────────────────────────────
def union_dedup(s1_rows: list[dict], s2_rows: list[dict]) -> list[dict]:
    """
    Ưu tiên S1. S2 chỉ bổ sung sản phẩm chưa có trong S1.
    Key dedup: (shop_id, item_id) — không phân biệt nguồn.
    """
    seen   = {(r["_shop_id"], r["_item_id"]) for r in s1_rows}
    merged = list(s1_rows)
    added  = 0

    for row in s2_rows:
        key = (row["_shop_id"], row["_item_id"])
        if key not in seen and row["_shop_id"] and row["_item_id"]:
            seen.add(key)
            merged.append(row)
            added += 1

    print(f"\n[Union] S1={len(s1_rows)} | S2 thêm mới={added} | Tổng={len(merged)}")
    return merged


# ─────────────────────────────────────────────
# Write — xoá cũ, ghi mới hoàn toàn
# ─────────────────────────────────────────────
def write_json(rows: list[dict]) -> None:
    # Xoá file cũ nếu tồn tại
    if os.path.exists(OUTPUT_FILE):
        os.remove(OUTPUT_FILE)
        print(f"[Write] Đã xoá {OUTPUT_FILE} cũ")

    # Bỏ internal keys (_shop_id, _item_id) trước khi ghi
    output = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[Write] Đã ghi {len(output)} sản phẩm → {OUTPUT_FILE}")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    crawled_at = datetime.now(VN_TZ)
    slots      = get_slots_to_fetch()

    print("=== Slots sẽ crawl ===")
    for ts, label in slots:
        print(f"  {label}  (startTime={ts})")

    s1 = crawl_s1(slots, crawled_at)
    s2 = crawl_s2(crawled_at)

    merged = union_dedup(s1, s2)
    write_json(merged)


if __name__ == "__main__":
    main()