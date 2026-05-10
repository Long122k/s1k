"""
getdeal1k.py — Crawl & merge từ 2 nguồn:
  1. shopee1k.com  (HTML + Next.js JSON)
  2. addlivetag.com (REST API)

Dedup theo (shop_id, item_id).
Output: data.json
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
AFFILIATE_ID = "17351620126"
OUTPUT_FILE  = "data.json"
TIMEOUT      = 15

# ── Source 1: shopee1k.com ────────────────────
S1_URL = "https://shopee1k.com"
IMG_BASE = "https://down-bs-vn.img.susercontent.com/"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.118 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
]

S1_BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "Referer": "https://shopee1k.com/",
}

# ── Source 2: addlivetag.com ──────────────────
S2_URL = "https://addlivetag.com/api/data_dealxk.php"
S2_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/143.0.0.0",
    "Referer": "https://addlivetag.com/deal1k.html",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json",
}


# ─────────────────────────────────────────────
# Shared helpers
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


# ─────────────────────────────────────────────
# Source 1: shopee1k.com
# ─────────────────────────────────────────────
def s1_fetch() -> str:
    headers = {**S1_BASE_HEADERS, "User-Agent": random.choice(USER_AGENTS)}
    resp = requests.get(S1_URL, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def s1_extract_payload(html: str) -> dict:
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
    for script in scripts:
        if "self.__next_f.push" not in script or "bundles" not in script:
            continue
        m = re.search(r'self\.__next_f\.push\(\[1,\s*("(?:[^"\\]|\\.)*")\]\)', script, re.DOTALL)
        if not m:
            continue
        inner = json.loads(m.group(1))
        m2 = re.search(r"^\d+:(\[.*\]|\{.*\})", inner, re.DOTALL)
        if not m2:
            continue
        data    = json.loads(m2.group(1))
        payload = data[3] if isinstance(data, list) and len(data) > 3 else data
        if "bundles" in payload:
            return payload
    raise ValueError("[S1] Không tìm thấy JSON payload")


def s1_parse(payload: dict, crawled_at: datetime) -> list[dict]:
    rows = []
    for bundle in payload.get("bundles", []):
        time_slot = bundle.get("slot", {}).get("timeLabel", "")
        for item in bundle.get("products", []):
            img_key   = item.get("img", "")
            image_url = img_key if img_key.startswith("http") else f"{IMG_BASE}{img_key}_tn"
            shop_id   = str(item.get("shop_id", ""))
            item_id   = str(item.get("item_id", ""))
            price     = int(item.get("price", 0))
            rows.append({
                "shop_id":      shop_id,
                "item_id":      item_id,
                "title":        item.get("title", "").strip(),
                "price":        price,
                "quantity":     int(item.get("amount", 0)),
                "time_slot":    time_slot,
                "image_url":    image_url,
                "product_link": build_aff_link(shop_id, item_id, price, time_slot, crawled_at),
            })
    return rows


def crawl_s1(crawled_at: datetime) -> list[dict]:
    print("[S1] Fetching shopee1k.com ...")
    try:
        html    = s1_fetch()
        payload = s1_extract_payload(html)
        rows    = s1_parse(payload, crawled_at)
        print(f"[S1] OK — {len(rows)} sản phẩm")
        return rows
    except Exception as e:
        print(f"[S1] FAILED — {e}")
        return []


# ─────────────────────────────────────────────
# Source 2: addlivetag.com
# ─────────────────────────────────────────────
def s2_fetch() -> list[dict]:
    resp = requests.get(S2_URL, headers=S2_HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError("[S2] Response không phải dạng list")
    return data


def s2_parse(raw: list[dict], crawled_at: datetime) -> list[dict]:
    rows = []
    for item in raw:
        shop_id   = str(item.get("shopid", ""))
        item_id   = str(item.get("itemid", ""))
        price     = int(item.get("price", 0))
        time_slot = item.get("sale_slot", "")

        # Ảnh: nguồn này đã trả về URL đầy đủ
        image_url = item.get("img", "")

        rows.append({
            "shop_id":      shop_id,
            "item_id":      item_id,
            "title":        item.get("title", "").strip(),
            "price":        price,
            "quantity":     int(item.get("amount", 0)),
            "time_slot":    time_slot,
            "image_url":    image_url,
            "product_link": build_aff_link(shop_id, item_id, price, time_slot, crawled_at),
        })
    return rows


def crawl_s2(crawled_at: datetime) -> list[dict]:
    print("[S2] Fetching addlivetag.com ...")
    try:
        raw  = s2_fetch()
        rows = s2_parse(raw, crawled_at)
        print(f"[S2] OK — {len(rows)} sản phẩm")
        return rows
    except Exception as e:
        print(f"[S2] FAILED — {e}")
        return []


# ─────────────────────────────────────────────
# Merge + Dedup
# ─────────────────────────────────────────────
def merge(s1_rows: list[dict], s2_rows: list[dict]) -> list[dict]:
    """
    Ưu tiên S1 (shopee1k.com). Nếu cùng (shop_id, item_id) thì bỏ S2.
    """
    seen = {(r["shop_id"], r["item_id"]) for r in s1_rows}
    merged = list(s1_rows)

    added = 0
    for row in s2_rows:
        key = (row["shop_id"], row["item_id"])
        if key not in seen:
            seen.add(key)
            merged.append(row)
            added += 1

    print(f"[Merge] S1={len(s1_rows)} | S2 mới thêm={added} | Tổng={len(merged)}")
    return merged


# ─────────────────────────────────────────────
# Write
# ─────────────────────────────────────────────
def write_json(rows: list[dict], filepath: str) -> None:
    # Bỏ shop_id / item_id khỏi output cuối (internal keys, không cần ở frontend)
    output = [{k: v for k, v in r.items() if k not in ("shop_id", "item_id")} for r in rows]
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[Write] {len(output)} rows → {filepath}")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    crawled_at = datetime.now()

    s1 = crawl_s1(crawled_at)
    s2 = crawl_s2(crawled_at)

    merged = merge(s1, s2)
    write_json(merged, OUTPUT_FILE)


if __name__ == "__main__":
    main()