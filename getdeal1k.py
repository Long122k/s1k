"""
shopee1k.com crawler
Cài đặt: pip install requests
Chạy:    python shopee1k.py
"""

import json
import random
import re
from datetime import datetime
from urllib.parse import quote

import requests

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
URL          = "https://shopee1k.com"
AFFILIATE_ID = "17351620126"

USER_AGENTS = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Safari macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    # Chrome Android (mobile)
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.118 Mobile Safari/537.36",
    # Safari iOS
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
]

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "Referer": "https://shopee1k.com/",
}

IMG_BASE = "https://down-bs-vn.img.susercontent.com/"


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def build_sub_id(price: int, time_slot: str, crawled_at: datetime) -> str:
    """
    Prefix theo giá:
        price <= 1000  → "1k"
        price <= 9000  → "9k"
        còn lại        → "ot"

    Ví dụ: price=1000, time_slot="17:00", ngày 09/05 → "1k170905"
    """
    if price <= 1000:
        prefix = "1k"
    elif price <= 9000:
        prefix = "9k"
    else:
        prefix = "ot"

    hh   = time_slot.split(":")[0] if ":" in time_slot else time_slot
    ddmm = crawled_at.strftime("%d%m")

    return f"{prefix}{hh}{ddmm}"


def build_aff_link(
    shop_id: str,
    item_id: str,
    price: int,
    time_slot: str,
    crawled_at: datetime,
) -> str:
    """
    https://s.shopee.vn/an_redir
        ?origin_link=<ENCODED_LANDING>
        &affiliate_id=<AFFILIATE_ID>
        &sub_id=<sub_id>
    """
    landing = f"https://shopee.vn/opaanlp/{shop_id}/{item_id}"
    sub_id  = build_sub_id(price, time_slot, crawled_at)

    return (
        f"https://s.shopee.vn/an_redir"
        f"?origin_link={quote(landing, safe='')}"
        f"&affiliate_id={AFFILIATE_ID}"
        f"&sub_id={sub_id}"
    )


# ─────────────────────────────────────────────
# Step 1: Fetch
# ─────────────────────────────────────────────
def fetch(url: str) -> str:
    headers = {**BASE_HEADERS, "User-Agent": random.choice(USER_AGENTS)}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.text


# ─────────────────────────────────────────────
# Step 2: Parse
# ─────────────────────────────────────────────
def extract_payload(html: str) -> dict:
    """
    Next.js nhúng data vào script tag dạng:
        self.__next_f.push([1, "4:[\"$\",\"$Lb\",null,{...}]"])
    Script chứa key "bundles" là script cần tìm.
    """
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)

    for script in scripts:
        if "self.__next_f.push" not in script or "bundles" not in script:
            continue

        m = re.search(
            r'self\.__next_f\.push\(\[1,\s*("(?:[^"\\]|\\.)*")\]\)',
            script,
            re.DOTALL,
        )
        if not m:
            continue

        inner = json.loads(m.group(1))
        m2 = re.search(r"^\d+:(\[.*\]|\{.*\})", inner, re.DOTALL)
        if not m2:
            continue

        data = json.loads(m2.group(1))
        payload = data[3] if isinstance(data, list) and len(data) > 3 else data
        if "bundles" in payload:
            return payload

    raise ValueError("Không tìm thấy JSON payload trong HTML")


def parse(payload: dict, crawled_at: datetime) -> list[dict]:
    """
    payload["bundles"] là list 2 phần tử:
        bundle[0] → khung 17:00  (hiển thị trên UI)
        bundle[1] → khung 19:00  (bị ẩn trên UI, nhưng có đủ data trong JSON)
    """
    rows = []

    for bundle in payload.get("bundles", []):
        time_slot = bundle.get("slot", {}).get("timeLabel", "")

        for item in bundle.get("products", []):
            img_key = item.get("img", "")
            image_url = img_key if img_key.startswith("http") else f"{IMG_BASE}{img_key}_tn"

            shop_id = item.get("shop_id", "")
            item_id = item.get("item_id", "")
            price   = int(item.get("price", 0))

            rows.append({
                "title":        item.get("title", "").strip(),
                "price":        price,
                "quantity":     int(item.get("amount", 0)),
                "time_slot":    time_slot,
                "image_url":    image_url,
                "product_link": build_aff_link(shop_id, item_id, price, time_slot, crawled_at),
            })

    return rows


# ─────────────────────────────────────────────
# Step 3: Write JSON
# ─────────────────────────────────────────────
def write_json(rows: list[dict], filepath: str) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    crawled_at = datetime.now()

    html    = fetch(URL)
    payload = extract_payload(html)
    rows    = parse(payload, crawled_at)

    output  = f"data.json"
    write_json(rows, output)
    print(f"Saved {len(rows)} rows → {output}")


if __name__ == "__main__":
    main()