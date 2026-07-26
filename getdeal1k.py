"""
getdeal1k.py — Crawl deal flash sale từ 2 nguồn Next.js SSR:
  1. shopee1k.com     (nhiều slot linh hoạt cùng lúc: 12:30, 13:00, 15:00 …)
  2. nghienshopee.net (chỉ slot đang diễn ra)

Cả 2 nhúng data trong <script>self.__next_f.push([1,"…"])</script> với cùng
schema sản phẩm (item_id, shop_id, title, img, price, original_price, percent,
amount, time, bucket). Ta union + dedup theo (shop_id, item_id, time_slot),
GIỮ NGUYÊN khung giờ dạng "HH:MM" (không ép về :00).

Output: data.json
  {generated_at, current_slot, next_slot, slots:[{label,status}], deals:[…]}

Cài đặt:  pip install requests
"""

import json
import os
import random
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

import requests

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
AFFILIATE_ID = "17351620126"
OUTPUT_FILE  = os.environ.get("OUTPUT_FILE", "data.json")

VN_TZ    = timezone(timedelta(hours=7))
IMG_BASE = "https://down-vn.img.susercontent.com/"

# Nguồn crawl — thêm/bớt tại đây. Xử lý hoàn toàn giống nhau.
SOURCES = [
    "https://shopee1k.com/",
    "https://nghienshopee.net/",
]

# Chỉ giữ deal giá tốt (đúng tinh thần Gia1K); bỏ bucket "Other" = hàng nguyên giá
ALLOWED_BUCKETS = {"1k", "9k", "29k"}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
]


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def to_int(v) -> int:
    try:
        return int(float(str(v)))
    except (ValueError, TypeError):
        return 0


def normalize_slot(label: str) -> str:
    """Chuẩn hóa nhãn giờ về 'HH:MM' (pad 0 cho giờ), GIỮ phút linh hoạt.
    '12:30' → '12:30' | '9:00' → '09:00' | '13:0' → '13:00'."""
    if not label:
        return ""
    m = re.match(r"\s*(\d{1,2}):(\d{1,2})", str(label))
    if m:
        return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    return str(label).strip()


def slot_minutes(label: str) -> int:
    """'HH:MM' → phút trong ngày, để sắp xếp/so sánh slot."""
    m = re.match(r"(\d{1,2}):(\d{2})", label or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else 0


def norm_status(vi: str) -> str:
    """statusLabel tiếng Việt → khóa gọn cho web."""
    s = (vi or "").lower()
    if "đang" in s:
        return "live"
    if "sắp" in s:
        return "upcoming"
    if "kết thúc" in s or "đã" in s:
        return "ended"
    return ""


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


# ─────────────────────────────────────────────
# Fetch + parse (chung cho mọi nguồn Next.js SSR)
# ─────────────────────────────────────────────
def fetch_html(url: str) -> str:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        "Referer": url,
        "User-Agent": random.choice(USER_AGENTS),
    }
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.text


def decode_next_payload(html: str) -> str:
    """Ghép toàn bộ chuỗi đã decode trong self.__next_f.push([1, "..."])."""
    parts = []
    for script in re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL):
        if "__next_f.push" not in script:
            continue
        for m in re.finditer(r'self\.__next_f\.push\(\[1,\s*("(?:[^"\\]|\\.)*")\]\)', script, re.DOTALL):
            try:
                parts.append(json.loads(m.group(1)))
            except (json.JSONDecodeError, ValueError):
                pass
    return "".join(parts)


def extract_array(big: str, key: str):
    """Trích một mảng JSON theo key (vd '"bundles":[') bằng quét ngoặc cân bằng."""
    start = big.find(key)
    if start < 0:
        return None
    start += len(key) - 1
    depth = 0
    for j in range(start, len(big)):
        c = big[j]
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(big[start:j + 1])
                except (json.JSONDecodeError, ValueError):
                    return None
    return None


def parse_bundles(big: str) -> list[tuple[str, str, list]]:
    """Trả về [(slot_label, status_vi, [products])].
    - shopee1k.com: có "bundles" (nhiều slot).
    - nghienshopee.net: chỉ "items" + "slot" (1 slot)."""
    bundles = extract_array(big, '"bundles":[')
    out = []
    if isinstance(bundles, list) and bundles:
        for b in bundles:
            slot  = b.get("slot", {}) or {}
            prods = b.get("products") or b.get("items") or []
            out.append((normalize_slot(slot.get("timeLabel", "")),
                        slot.get("statusLabel", ""), prods))
        return out

    items = extract_array(big, '"items":[') or []
    m = re.search(r'"slot":\{"id":"[^"]*","timeLabel":"([^"]*)","statusLabel":"([^"]*)"', big)
    label  = normalize_slot(m.group(1)) if m else ""
    status = m.group(2) if m else ""
    return [(label, status, items)]


def parse_source(url: str, crawled_at: datetime) -> tuple[list[dict], dict]:
    """Crawl 1 nguồn → (rows, {slot_label: status_key})."""
    print(f"\n[Crawl] {url}")
    html    = fetch_html(url)
    big     = decode_next_payload(html)
    bundles = parse_bundles(big)

    rows, slot_status, skipped = [], {}, 0
    for label, status_vi, products in bundles:
        skey = norm_status(status_vi)
        if skey == "ended":
            continue                       # bỏ slot đã kết thúc
        if label:
            slot_status[label] = skey
        for it in products:
            shop_id = str(it.get("shop_id", "")).strip()
            item_id = str(it.get("item_id", "")).strip()
            if not shop_id or not item_id:
                continue
            if str(it.get("bucket", "")).lower() not in ALLOWED_BUCKETS:
                skipped += 1
                continue

            img       = it.get("img", "") or ""
            image_url = img if img.startswith("http") else f"{IMG_BASE}{img}"
            price     = to_int(it.get("price"))

            rows.append({
                "_shop_id":       shop_id,
                "_item_id":       item_id,
                "title":          (it.get("title") or "").strip(),
                "price":          price,
                "original_price": to_int(it.get("original_price")),
                "discount_pct":   to_int(it.get("percent")),
                "quantity":       to_int(it.get("amount")),
                "time_slot":      label,
                "status":         skey,
                "image_url":      image_url,
                "product_link":   build_aff_link(shop_id, item_id, price, label, crawled_at),
            })

    slots_str = ", ".join(f"{l}({s})" for l, s in slot_status.items()) or "—"
    print(f"[Crawl] slot: {slots_str} | giữ {len(rows)} deal, bỏ {skipped} nguyên giá")
    return rows, slot_status


# ─────────────────────────────────────────────
# Union + build payload
# ─────────────────────────────────────────────
def crawl_all(crawled_at: datetime) -> dict:
    all_rows: list[dict] = []
    slot_status: dict = {}

    for url in SOURCES:
        try:
            rows, sstat = parse_source(url, crawled_at)
        except Exception as e:
            print(f"[Crawl] LỖI {url} — {e}")
            continue
        all_rows.extend(rows)
        for label, skey in sstat.items():
            # 'live' được ưu tiên nếu 2 nguồn báo khác nhau
            if label not in slot_status or skey == "live":
                slot_status[label] = skey

    # Dedup theo (shop_id, item_id, time_slot) — cùng SP ở nhiều khung vẫn giữ
    seen, deals = set(), []
    for r in all_rows:
        k = (r["_shop_id"], r["_item_id"], r["time_slot"])
        if k in seen:
            continue
        seen.add(k)
        deals.append({k2: v for k2, v in r.items() if not k2.startswith("_")})

    # Sắp theo khung giờ tăng dần, trong khung thì giá thấp lên đầu
    deals.sort(key=lambda d: (slot_minutes(d["time_slot"]), d["price"]))

    slots = sorted(
        ({"label": l, "status": s} for l, s in slot_status.items() if l),
        key=lambda x: slot_minutes(x["label"]),
    )
    current_slot = next((s["label"] for s in slots if s["status"] == "live"), "")
    next_slot    = next((s["label"] for s in slots if s["status"] == "upcoming"), "")

    return {
        "generated_at": crawled_at.isoformat(),
        "current_slot": current_slot,
        "next_slot":    next_slot,
        "slots":        slots,
        "deals":        deals,
    }


# ─────────────────────────────────────────────
# Write — ghi nguyên tử (temp + replace)
# ─────────────────────────────────────────────
def write_json(payload: dict) -> None:
    tmp = OUTPUT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OUTPUT_FILE)
    print(f"\n[Write] {len(payload['deals'])} deal | slot={[s['label'] for s in payload['slots']]} → {OUTPUT_FILE}")


def main():
    crawled_at = datetime.now(VN_TZ)
    payload    = crawl_all(crawled_at)

    if not payload["deals"]:
        print("[Main] Không có deal nào — GIỮ NGUYÊN data.json cũ, không ghi đè.")
        return

    write_json(payload)


if __name__ == "__main__":
    main()
