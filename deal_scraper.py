import os
import sys
import time
import json
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
AMAZON_AFFILIATE_TAG = os.environ.get("AMAZON_AFFILIATE_TAG", "")

CACHE_FILE = "seen_deals.json"

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

def load_seen_deals():
    """Loads previously sent deal IDs/links from cache file."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()

def save_seen_deals(seen_set):
    """Saves updated seen deals back to cache file (keeps last 300 items)."""
    try:
        trimmed_list = list(seen_set)[-300:]
        with open(CACHE_FILE, "w") as f:
            json.dump(trimmed_list, f)
    except Exception as e:
        print(f"⚠️ Failed to save cache: {e}", flush=True)

def normalize_and_clean_url(raw_url, domain_prefix=""):
    """Fixes protocol bugs and cleans Amazon/FirstCry URLs."""
    if not raw_url:
        return ""
    
    url = raw_url.strip()
    if url.startswith("//"):
        url = "https:" + url
    elif not url.startswith("http"):
        url = f"{domain_prefix.rstrip('/')}/{url.lstrip('/')}"

    url = re.sub(r"^https?://[^/]+//+", "https://", url)

    if "amazon.in" in url:
        asin_match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
        if asin_match:
            asin = asin_match.group(1)
            base_amzn = f"https://www.amazon.in/dp/{asin}"
            if AMAZON_AFFILIATE_TAG:
                return f"{base_amzn}?tag={AMAZON_AFFILIATE_TAG}"
            return base_amzn

    if "firstcry.com" in url:
        return url.split("?")[0]

    return url

def convert_to_affiliate_link(raw_url):
    """Converts merchant URLs using direct Amazon Tag or Cuelinks v3 API."""
    if not raw_url or not raw_url.startswith("http"):
        return raw_url

    # 1. Amazon Direct Affiliate Tag + is.gd shortener
    if "amazon.in" in raw_url and AMAZON_AFFILIATE_TAG:
        try:
            r_short = requests.get(
                f"https://is.gd/create.php?format=simple&url={requests.utils.quote(raw_url)}",
                timeout=6
            )
            if r_short.status_code == 200 and r_short.text.startswith("http"):
                return r_short.text.strip()
        except Exception:
            pass
        return raw_url

    # 2. Cuelinks API v3 for FirstCry / others
    if CUELINKS_API_KEY:
        v3_endpoint = "https://developers.cuelinks.com/pub_api/v3/links/convert"
        v3_headers = {
            "Authorization": f"Token {CUELINKS_API_KEY}",
            "Content-Type": "application/json"
        }
        v3_payload = {"url": raw_url, "shorten": True}

        try:
            r = requests.post(v3_endpoint, json=v3_payload, headers=v3_headers, timeout=8)
            if r.status_code in [200, 201]:
                resp_json = r.json()
                data_obj = resp_json.get("data") if isinstance(resp_json.get("data"), dict) else resp_json
                cuelink = data_obj.get("short_url") or data_obj.get("tracking_url") or data_obj.get("url")
                if cuelink and cuelink != raw_url:
                    return cuelink
        except Exception:
            pass

    # 3. Fallback shortener
    if len(raw_url) > 42:
        try:
            r_short = requests.get(
                f"https://is.gd/create.php?format=simple&url={requests.utils.quote(raw_url)}",
                timeout=6
            )
            if r_short.status_code == 200 and r_short.text.startswith("http"):
                return r_short.text.strip()
        except Exception:
            pass

    return raw_url

def clean_product_title(raw_title):
    """Strips UI junk and keeps clean model name."""
    junk_patterns = [
        r"Added to Cart", r"Shortlisted", r"Already Viewed",
        r"\b\d+\s*Left\b", r"Bestseller", r"\bNew\b", r"INTERNATIONAL", r"Sponsored"
    ]
    cleaned = raw_title
    for pattern in junk_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

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

    caption_lines = [
        f"🚨 {clean_title} @ ₹{price:.0f} [{store}] -",
        f"{final_link}"
    ]

    if coupon:
        caption_lines.append(f"\n📍 Coupon: {coupon}")

    if "firstcry" in store.lower():
        caption_lines.append(
            "\n💡 Showing \"Out of Stock\"? Just open the link, head to your "
            "Profile → Recently Viewed, and click Add to Cart from there!"
        )

    caption_lines.append("\n🚨 Share with your collector buddies!")
    caption = "\n".join(caption_lines)

    try:
        if image_url and image_url.startswith("http"):
            api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "photo": image_url,
                "caption": caption
            }
            r = requests.post(api_url, json=payload, timeout=15)
            if r.status_code == 200:
                print(f"   📸 Sent Photo Alert [{store}]: {clean_title[:40]} @ ₹{price:.0f}", flush=True)
                return

        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": caption,
            "disable_web_page_preview": False
        }
        r = requests.post(api_url, json=payload, timeout=10)
        if r.status_code == 200:
            print(f"   ✅ Sent Text Alert [{store}]: {clean_title[:40]} @ ₹{price:.0f}", flush=True)
    except Exception as e:
        print(f"   ⚠️ Telegram connection error: {e}", flush=True)

def scrape_deals():
    print("🚀 Starting Mass-Market Deal Scraper (Full Uncapped Run)...", flush=True)
    seen_deals = load_seen_deals()
    new_seen_count = 0
    
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
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
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
                    page.goto("https://www.amazon.in/", timeout=30000, wait_until="domcontentloaded")
                    time.sleep(random.uniform(2.0, 3.5))
                    page.goto(url, timeout=45000, wait_until="domcontentloaded", referer="https://www.amazon.in/")
                    time.sleep(3)

                    soup = BeautifulSoup(page.content(), "html.parser")
                    items = soup.select("div[data-component-type='s-search-result'], div.s-result-item[data-asin]")
                    valid_items = [i for i in items if i.get("data-asin")]
                    print(f"   Found {len(valid_items)} product cards on Amazon.", flush=True)

                    for item in valid_items:
                        try:
                            asin = item.get("data-asin", "").strip()
                            if not asin or asin in seen_deals:
                                continue

                            img_elem = item.select_one("img.s-image")
                            h2_texts = [h.get_text(" ", strip=True) for h in item.select("h2") if h.get_text(strip=True)]
                            title = " ".join(h2_texts)
                            if len(title) <= 15 and img_elem and img_elem.get("alt"):
                                title = img_elem.get("alt").strip()

                            title = re.sub(r"^(Hot Wheels\s+)+", "Hot Wheels ", title, flags=re.I)
                            if "hot wheels" not in title.lower():
                                continue

                            price_elem = item.select_one(".a-price-whole")
                            if not price_elem:
                                continue

                            price = float(re.sub(r"[^\d.]", "", price_elem.get_text(strip=True)).rstrip("."))
                            clean_link = f"https://www.amazon.in/dp/{asin}"
                            img_url = img_elem.get("src") if img_elem else None

                            send_telegram_deal(store, title, price, clean_link, image_url=img_url)
                            seen_deals.add(asin)
                            new_seen_count += 1
                            time.sleep(1)
                        except Exception:
                            continue

                elif "firstcry" in store.lower():
                    page.goto(url, timeout=45000, wait_until="domcontentloaded")
                    time.sleep(4)
                    page.evaluate("window.scrollBy(0, 1000)")
                    time.sleep(2)

                    soup = BeautifulSoup(page.content(), "html.parser")
                    items = soup.select("div.list_block, div.lft.list_cel, div.main_prod_box, li.child_block")
                    print(f"   Found {len(items)} product cards on FirstCry.", flush=True)

                    for item in items:
                        try:
                            link_tag = item.find("a", href=re.compile(r"/hot-wheels|/product-detail", re.I)) or item.find("a", href=True)
                            if not link_tag:
                                continue
                            clean_link = normalize_and_clean_url(link_tag["href"], "https://www.firstcry.com")
                            
                            deal_key = clean_link.split("?")[0]
                            if not deal_key or deal_key in seen_deals:
                                continue

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

                            img_url = None
                            if img_elem:
                                for attr in ["src", "data-src", "data-original"]:
                                    val = img_elem.get(attr, "")
                                    if val and ("fcglcdn.com" in val or "firstcry" in val or val.startswith("http") or val.startswith("//")):
                                        img_url = "https:" + val if val.startswith("//") else val
                                        break

                            full_card_text = item.get_text(" ", strip=True)
                            price = 0.0
                            pm_discount = re.search(r"\b([1-9][0-9]{1,4}(?:\.[0-9]{1,2})?)\s+[1-9][0-9]{1,4}(?:\.[0-9]{1,2})?\s*\(\d+%\s*Off\)", full_card_text, re.I)
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

                            send_telegram_deal(store, title, price, clean_link, image_url=img_url)
                            seen_deals.add(deal_key)
                            new_seen_count += 1
                            time.sleep(1)
                        except Exception:
                            continue

            except Exception as e:
                print(f"⚠️ Error scraping {store}: {e}", flush=True)

        browser.close()

    save_seen_deals(seen_seen_deals if 'seen_seen_deals' in locals() else seen_deals)
    print(f"\n🏁 Deal scan complete. Added {new_seen_count} new deals to cache.", flush=True)

if __name__ == "__main__":
    scrape_deals()
