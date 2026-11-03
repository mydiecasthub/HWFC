import os
import sys
import time
import re
import random
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# Force unbuffered logs for GitHub Actions
sys.stdout.reconfigure(line_buffering=True)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
CUELINKS_API_KEY = os.environ.get("CUELINKS_API_KEY", "")

TARGET_URLS = [
    {
        "store": "Amazon.in",
        "url": "https://www.amazon.in/s?k=Hot+Wheels"
    },
    {
        "store": "FirstCry",
        "url": "https://www.firstcry.com/search?q=hot%20wheels"
    }
]

def convert_to_affiliate_link(raw_url):
    """Converts a raw URL into a tracked Cuelinks affiliate link using API v3."""
    if not raw_url or not raw_url.startswith("http") or not CUELINKS_API_KEY:
        return raw_url

    api_endpoint = "https://developers.cuelinks.com/pub_api/v3/links/convert"
    headers = {
        "Authorization": f"Token {CUELINKS_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {"url": raw_url}

    try:
        response = requests.post(api_endpoint, json=payload, headers=headers, timeout=5)
        if response.status_code in [200, 201]:
            data = response.json()
            return data.get("tracking_url") or data.get("url", raw_url)
        else:
            print(f"⚠️ Cuelinks API Warning ({response.status_code}): {response.text}", flush=True)
    except Exception as e:
        print(f"⚠️ Failed to connect to Cuelinks API: {e}", flush=True)

    return raw_url

def clean_product_title(raw_title):
    """Strips out FirstCry/Amazon UI noise and keeps only the clean model name."""
    junk_patterns = [
        r"Added to Cart", r"Shortlisted", r"Already Viewed",
        r"\b\d+\s*Left\b", r"Bestseller", r"\bNew\b", r"INTERNATIONAL"
    ]
    cleaned = raw_title
    for pattern in junk_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

    # Cut off everything after age markers, descriptions, ratings, or size specs
    cleaned = re.split(
        r"\b\d+\s*\+?\s*Years?\b|Sizes?\s*:|Perfect for|Set of \d+|\(\d+\s*Ratings?\)|ADD TO CART|Club Price",
        cleaned,
        flags=re.IGNORECASE
    )[0]
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -.,|")
    return cleaned

def send_telegram_deal(store, title, price, link, image_url=None, coupon=None):
    final_link = convert_to_affiliate_link(link)
    clean_title = clean_product_title(title)

    # Short format matching your reference screenshot
    caption_lines = [
        f"🚨 {clean_title}@{price:.0f} -",
        f"{final_link}"
    ]

    if coupon:
        caption_lines.append(f"\n📍 Coupon: {coupon}")

    if "firstcry" in store.lower():
        caption_lines.append(
            "\n📍 It may show as \"Out of Stock\" when you first open the link. "
            "Don't worry! After clicking the link, go to your Profile section "
            "and add the product to your cart from there"
        )

    caption_lines.append("\n🚨 Share with your collector buddies!")
    caption = "\n".join(caption_lines)

    try:
        # Send Photo card first
        if image_url and image_url.startswith("http"):
            api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "photo": image_url,
                "caption": caption
            }
            r = requests.post(api_url, json=payload, timeout=15)
            if r.status_code == 200:
                print(f"   📸 Sent Photo Alert: {clean_title[:40]} @ ₹{price:.0f}", flush=True)
                return
            else:
                print(f"   ⚠️ Photo send failed ({r.status_code}), falling back to text...", flush=True)

        # Fallback to text message
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": caption,
            "disable_web_page_preview": False
        }
        r = requests.post(api_url, json=payload, timeout=10)
        if r.status_code == 200:
            print(f"   ✅ Sent Text Alert: {clean_title[:40]} @ ₹{price:.0f}", flush=True)
        else:
            print(f"   ⚠️ Telegram Error: {r.text}", flush=True)
    except Exception as e:
        print(f"   ⚠️ Telegram connection error: {e}", flush=True)

def parse_amazon_html(html, store):
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select("div[data-component-type='s-search-result'], div.s-result-item[data-asin]")
    valid_items = [i for i in items if i.get("data-asin")]
    print(f"   Found {len(valid_items)} product cards on Amazon.", flush=True)

    matched_count = 0
    for item in valid_items:
        if matched_count >= 5:
            break
        try:
            title_elem = item.select_one("h2 span") or item.select_one("h2")
            price_elem = item.select_one(".a-price-whole")
            link_elem = item.select_one("h2 a") or item.select_one("a.a-link-normal.s-no-outline")
            img_elem = item.select_one("img.s-image")

            if not title_elem or not price_elem or not link_elem:
                continue

            title = title_elem.get_text(strip=True)
            if "hot wheels" not in title.lower():
                continue

            price_str = re.sub(r"[^\d.]", "", price_elem.get_text(strip=True)).rstrip(".")
            if not price_str:
                continue
            price = float(price_str)

            link = link_elem.get("href", "")
            if link and not link.startswith("http"):
                link = f"https://www.amazon.in{link}"

            img_url = img_elem.get("src") if img_elem else None

            send_telegram_deal(store, title, price, link, image_url=img_url)
            matched_count += 1
            time.sleep(1)
        except Exception:
            continue
    return len(valid_items)

def scrape_deals():
    print("🚀 Starting Mass-Market Deal Scraper v2 (Photo + Clean Text)...", flush=True)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage"
            ]
        )
        
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            extra_http_headers={
                "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"'
            }
        )
        
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        for target in TARGET_URLS:
            store = target["store"]
            url = target["url"]
            print(f"\n-> Scanning {store} ({url})...", flush=True)

            try:
                if "amazon" in store.lower():
                    # Step 1: Visit Amazon homepage first to get valid session cookies & avoid 503
                    print("   [Amazon] Warming up session on homepage...", flush=True)
                    page.goto("https://www.amazon.in/", timeout=30000, wait_until="domcontentloaded")
                    time.sleep(random.uniform(2.0, 3.5))

                    # Step 2: Navigate to search page with referer
                    response = page.goto(url, timeout=45000, wait_until="domcontentloaded", referer="https://www.amazon.in/")
                    time.sleep(3)
                    status_code = response.status if response else "Unknown"
                    print(f"   [Debug] HTTP {status_code} | Page Title: '{page.title()}'", flush=True)

                    count = parse_amazon_html(page.content(), store)

                    # Step 3: Fallback if 503 still occurs
                    if count == 0:
                        print("   [Amazon] Retrying via mobile user-agent session...", flush=True)
                        s = requests.Session()
                        s.headers.update({
                            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
                            "Accept-Language": "en-IN,en-GB;q=0.9",
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                        })
                        s.get("https://www.amazon.in/", timeout=15)
                        time.sleep(1)
                        r_mob = s.get(url, timeout=20)
                        print(f"   [Debug Mobile Fallback] HTTP {r_mob.status_code}", flush=True)
                        if r_mob.status_code == 200:
                            parse_amazon_html(r_mob.text, store)

                elif "firstcry" in store.lower():
                    response = page.goto(url, timeout=45000, wait_until="domcontentloaded")
                    time.sleep(4)
                    page.evaluate("window.scrollBy(0, 1000)")
                    time.sleep(2)

                    status_code = response.status if response else "Unknown"
                    print(f"   [Debug] HTTP {status_code} | Page Title: '{page.title()}'", flush=True)

                    soup = BeautifulSoup(page.content(), "html.parser")
                    items = soup.select("div.list_block, div.lft.list_cel, div.main_prod_box, li.child_block")
                    print(f"   Found {len(items)} product cards on FirstCry.", flush=True)

                    matched_count = 0
                    for item in items:
                        if matched_count >= 5:
                            break
                        try:
                            # 1. Extract clean title from anchor title or image alt attribute
                            title = ""
                            for a_tag in item.find_all("a", href=True):
                                t_attr = a_tag.get("title", "").strip()
                                if t_attr and "hot wheels" in t_attr.lower():
                                    title = t_attr
                                    break

                            img_elem = item.find("img")
                            if not title and img_elem and img_elem.get("alt"):
                                title = img_elem.get("alt", "").strip()

                            if not title:
                                title_div = item.select_one(".li_txt1, .p_list_text, .list_title")
                                title = title_div.get_text(" ", strip=True) if title_div else item.get_text(" ", strip=True)

                            title = clean_product_title(title)
                            if "hot wheels" not in title.lower():
                                continue

                            # 2. Extract product link
                            link_tag = item.find("a", href=re.compile(r"/hot-wheels|/product-detail", re.I)) or item.find("a", href=True)
                            if not link_tag:
                                continue
                            link = link_tag["href"]
                            if not link.startswith("http"):
                                link = f"https://www.firstcry.com{link}"

                            # 3. Extract product image URL
                            img_url = None
                            if img_elem:
                                for attr in ["src", "data-src", "data-original"]:
                                    val = img_elem.get(attr, "")
                                    if val and ("fcglcdn.com" in val or "firstcry" in val or val.startswith("http") or val.startswith("//")):
                                        img_url = "https:" + val if val.startswith("//") else val
                                        break

                            # 4. Extract accurate Sale Price (strictly >= ₹50 so it never picks '5' or '1')
                            full_card_text = item.get_text(" ", strip=True)
                            price = 0.0

                            # Pattern A: Sale price right before MRP & (% Off) e.g., "3599.1 3999 (10% Off)"
                            pm_discount = re.search(r"\b([1-9][0-9]{1,4}(?:\.[0-9]{1,2})?)\s+[1-9][0-9]{1,4}(?:\.[0-9]{1,2})?\s*\(\d+%\s*Off\)", full_card_text, re.I)
                            # Pattern B: Price right before "Club Price:" e.g., "1399 Club Price: 1399"
                            pm_club = re.search(r"\b([1-9][0-9]{1,4}(?:\.[0-9]{1,2})?)\s*Club Price", full_card_text, re.I)

                            if pm_discount:
                                price = float(pm_discount.group(1))
                            elif pm_club:
                                price = float(pm_club.group(1))
                            else:
                                price_elem = item.select_one(".r-price, .rupee, .sp_amt, .actual-price")
                                if price_elem:
                                    pm_el = re.search(r"([1-9][0-9]{1,4}(?:\.[0-9]{1,2})?)", price_elem.get_text().replace(",", ""))
                                    if pm_el:
                                        price = float(pm_el.group(1))

                            if price < 50:
                                continue

                            send_telegram_deal(store, title, price, link, image_url=img_url)
                            matched_count += 1
                            time.sleep(1)
                        except Exception:
                            continue

            except Exception as e:
                print(f"⚠️ Error scraping {store}: {e}", flush=True)

        browser.close()
    print("\n🏁 Deal scan complete.", flush=True)

if __name__ == "__main__":
    scrape_deals()
