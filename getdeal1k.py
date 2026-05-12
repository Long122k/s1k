"""
getdeal1k.py — Crawl deal từ deal1k.vn
- Dùng Playwright intercept network để bắt API response trực tiếp
- Không cần tự build headers/cookie — browser làm hết
- Output: data.json

Cài đặt:
  pip install playwright
  playwright install chromium
"""

import json
from datetime import datetime, timezone, timedelta
from urllib.parse import quote, urlencode

from playwright.sync_api import sync_playwright, Response

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
AFFILIATE_ID = "17351620126"
OUTPUT_FILE  = "data.json"
BASE_URL     = "https://deal1k.vn"

SALE_SLOTS   = [0, 2, 9, 12, 15, 17, 19, 21]
PRICE_RANGES = ["1K", "9K", "29K"]

VN_TZ = timezone(timedelta(hours=7))


# ─────────────────────────────────────────────
# Tính startTime cho 2 slot: hiện tại + tiếp theo
# ─────────────────────────────────────────────
def get_slots_to_fetch() -> list[tuple[int, str]]:
    """
    Trả về list [(timestamp_utc, label)] cho 4 khung giờ:
      - Slot hiện tại      : slot lớn nhất <= giờ hiện tại
      - Slot tiếp theo x3  : 3 slot kế tiếp sau đó

    Ví dụ 10:20 với SALE_SLOTS=[0,2,9,12,15,17,19,21]:
      → [09:00, 12:00, 15:00, 17:00]
    """
    now_vn       = datetime.now(VN_TZ)
    current_hour = now_vn.hour

    # Tìm index của slot hiện tại
    current_idx = None
    for i, slot in enumerate(SALE_SLOTS):
        if current_hour >= slot:
            current_idx = i
        else:
            break

    # Nếu chưa đến slot đầu tiên trong ngày → lấy slot cuối hôm qua
    if current_idx is None:
        current_idx = len(SALE_SLOTS) - 1
        use_yesterday = True
    else:
        use_yesterday = False

    result = []
    for offset in range(4):
        idx = current_idx + offset
        # Tính ngày offset (wrap sang ngày hôm sau nếu vượt quá cuối SALE_SLOTS)
        day_offset = idx // len(SALE_SLOTS)
        slot_hour  = SALE_SLOTS[idx % len(SALE_SLOTS)]

        if use_yesterday:
            base_day = now_vn - timedelta(days=1) + timedelta(days=day_offset)
        else:
            base_day = now_vn + timedelta(days=day_offset)

        slot_dt = base_day.replace(hour=slot_hour, minute=0, second=0, microsecond=0)
        ts      = int(slot_dt.astimezone(timezone.utc).timestamp())
        result.append((ts, f"{slot_hour:02d}:00"))

    return result


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
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


def parse_response(data: dict, crawled_at: datetime, slot_label: str) -> list[dict]:
    """
    Parse toàn bộ response của 1 priceRange.
    - shop_id / item_id lấy từ shopeeUrl
    - time_slot lấy từ activeSlot × timeSlots
    - originalPrice đang dạng cents → chia 100000
    - Dùng affiliateUrl của API nếu có, fallback về tự build
    """
    import re as _re

    # Xác định time_slot label từ activeSlot + timeSlots
    active_ts  = data.get("activeSlot", 0)
    time_slots = data.get("timeSlots", [])


    rows = []
    for item in data.get("products", []):
        # Parse shop_id / item_id từ shopeeUrl
        shopee_url = item.get("shopeeUrl", "")
        m = _re.search(r"/product/(\d+)/(\d+)", shopee_url)
        if not m:
            continue
        shop_id = m.group(1)
        item_id = m.group(2)

        price         = int(item.get("price") or 0)
        original_raw  = item.get("originalPrice") or 0
        # originalPrice dạng cents (x100000) — nếu > 1 triệu thì chia, không thì dùng thẳng
        original_price = int(original_raw / 100000) if original_raw > 1_000_000 else int(original_raw)

        rows.append({
            "_shop_id":      shop_id,
            "_item_id":      item_id,
            "title":         (item.get("name") or "").strip(),
            "price":         price,
            "original_price": original_price,
            "discount_pct":  int(item.get("discount") or 0),
            "quantity":      int(item.get("stock") or 0),
            "time_slot":     slot_label,
            "image_url":     item.get("image") or "",
            "product_link":  build_aff_link(shop_id, item_id, price, slot_label, crawled_at),
        })
    return rows


def dedup(rows: list[dict]) -> list[dict]:
    seen, result = set(), []
    for row in rows:
        key = (row["_shop_id"], row["_item_id"])
        if key not in seen and row["_shop_id"] and row["_item_id"]:
            seen.add(key)
            result.append(row)
    return result


def write_json(rows: list[dict]) -> None:
    output = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[Write] {len(output)} sản phẩm → {OUTPUT_FILE}")


# ─────────────────────────────────────────────
# Crawl bằng Playwright intercept
# ─────────────────────────────────────────────
def crawl() -> list[dict]:
    slots = get_slots_to_fetch()
    for ts, label in slots:
        print(f"[Slot] {label}  |  startTime={ts}")

    crawled_at   = datetime.now()
    all_rows     = []
    # Lưu responses theo (priceRange, startTime)
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

        # ── Intercept: bắt response của API flash-deals ──
        def on_response(response: Response):
            if "/api/flash-deals" not in response.url:
                return
            # Lấy startTime từ URL để làm key
            st_match = __import__('re').search(r'startTime=(\d+)', response.url)
            if not st_match:
                return
            st = int(st_match.group(1))
            for pr in PRICE_RANGES:
                if f"priceRange={pr}" in response.url:
                    try:
                        raw_text = response.text()
                        data     = json.loads(raw_text)
                        payload  = data.get("data") or data
                        items    = payload.get("products") or []
                        api_results[(pr, st)] = payload
                        print(f"[Intercept] priceRange={pr} startTime={st} → {len(items)} sản phẩm")
                    except Exception as e:
                        print(f"[Intercept] priceRange={pr} parse lỗi — {e}")
                    break

        page.on("response", on_response)

        # ── Vào trang chủ để lấy session ──
        print("[Browser] Vào trang chủ ...")
        page.goto(BASE_URL, wait_until="networkidle", timeout=30000)

        # ── Trigger API calls: 2 slots × 3 priceRanges ──
        for start_time, slot_label in slots:
            for price_range in PRICE_RANGES:
                params = urlencode({
                    "mode":       "flash-sale",
                    "priceRange": price_range,
                    "page":       1,
                    "pageSize":   100,
                    "startTime":  start_time,
                })
                api_url = f"/api/flash-deals?{params}"
                print(f"[Browser] slot={slot_label} priceRange={price_range} ...")
                page.evaluate(f"""
                    fetch('{api_url}', {{
                        method: 'GET',
                        headers: {{ 'accept': '*/*' }}
                    }})
                """)
                page.wait_for_timeout(2000)

        browser.close()

    # ── Parse kết quả: loop 2 slots × 3 priceRanges ──
    for start_time, slot_label in slots:
        for price_range in PRICE_RANGES:
            data = api_results.get((price_range, start_time))
            if not data or not data.get("products"):
                print(f"[Parse] slot={slot_label} priceRange={price_range} — không có data")
                continue
            rows = parse_response(data, crawled_at, slot_label)
            print(f"[Parse] slot={slot_label} priceRange={price_range} → {len(rows)} sản phẩm")
            all_rows.extend(rows)

    return all_rows


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    all_rows = crawl()
    # deduped  = dedup(all_rows)
    # print(f"[Dedup] {len(all_rows)} → {len(deduped)} (bỏ {len(all_rows) - len(deduped)} trùng)")
    write_json(all_rows)


if __name__ == "__main__":
    main()