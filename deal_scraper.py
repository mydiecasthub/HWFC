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

# MRP Rules Dictionary
MRP_RULES = {
    "Hot Wheels": {
        "mainline": 179,
        "5_pack": 899,
        "premium_single": 599,
        "silver_or_pop": 349,
        "team_transport": 1499,
        "diorama_or_box_set": 2499,
        "monster_truck": 399,
        "f1_premium": 699,
    },
    "Matchbox": {
        "mainline": 199,
        "moving_parts": 299,
    },
    "Majorette": {
        "street": 179,
        "premium": 399,
        "deluxe": 599,
        "collection": 899,
    }
}

TARGET_URLS = [
    {"store": "Amazon.in", "url": "https://www.amazon.in/s?k=Hot+Wheels"},
    {"store": "Amazon.in", "url": "https://www.amazon.in/s?k=Matchbox+cars"},
    {"store": "Amazon.in", "url": "https://www.amazon.in/s?k=Majorette+cars"},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels"},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=matchbox"},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=majorette"}
]

def load_seen_deals():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()

def save_seen_deals(seen_set):
    try:
        trimmed_list = list(seen_set)[-300:]
        with open(CACHE_FILE, "w") as f:
            json.dump(trimmed_list, f)
    except Exception as e:
        print(f"⚠️ Failed to save cache: {e}", flush=True)

def determine_brand(title):
    t = title.lower()
    if "matchbox" in t:
        return "Matchbox"
    elif "majorette" in t:
        return "Majorette"
    else:
        return "Hot Wheels"

def classify_and_check_mrp(brand, title, price):
    t = title.lower()
    if any(skip in t for skip in [
        "case of", "collector case", "blind box", "track", "playset", "play set", 
        "protector", "lapel pin", "magnet", "rotiform", "1:36", "1:43", "1:24", "1:18", "1/43", "1/24", "1/18"
    ]):
        return False, None

    for num_cars in range(2, 11):
        if (f"pack of {num_cars}" in t or f"{num_cars} pack" in t or f"{num_cars}pc" in t or 
            f"{num_cars} pcs" in t or f"set of {num_cars}" in t or f"set {num_cars}" in t or 
            f"{num_cars} cars" in t or f"{num_cars} -car" in t):
            if brand == "Hot Wheels" and any(k in t for k in ["formula 1", "f1"]):
                per_car_mrp = MRP_RULES["Hot Wheels"]["f1_premium"]
            elif brand == "Hot Wheels" and any(k in t for k in ["car culture", "boulevard", "premium", "pop culture"]):
                per_car_mrp = MRP_RULES["Hot Wheels"]["premium_single"]
            elif brand == "Hot Wheels" and ("silver series" in t or "performance trucks" in t or "streets of japan" in t):
                per_car_mrp = MRP_RULES["Hot Wheels"]["silver_or_pop"]
            else:
                per_car_mrp = MRP_RULES["Hot Wheels"]["mainline"]
            expected_mrp = per_car_mrp * num_cars
            return price <= expected_mrp, expected_mrp

    if brand == "Hot Wheels":
        if "mainline" in t and not any(k in t for k in ["5 pack", "5-pack", "gift pack", "premium", "silver", "f1"]):
            return price <= MRP_RULES["Hot Wheels"]["mainline"], MRP_RULES["Hot Wheels"]["mainline"]
        if any(k in t for k in ["team transport", "fleet flyer", "retro rig", "camion de transport"]):
            return price <= MRP_RULES["Hot Wheels"]["team_transport"], MRP_RULES["Hot Wheels"]["team_transport"]
        elif any(k in t for k in ["diorama", "box set", "collector set", "heritage gift pack"]):
            return price <= MRP_RULES["Hot Wheels"]["diorama_or_box_set"], MRP_RULES["Hot Wheels"]["diorama_or_box_set"]
        elif any(k in t for k in ["5 pack", "5-pack", "pack of 5", "5 pcs", "gift pack", "giftpack"]) and "silver" not in t and "premium" not in t:
            return price <= MRP_RULES["Hot Wheels"]["5_pack"], MRP_RULES["Hot Wheels"]["5_pack"]
        elif "monster truck" in t:
            return price <= MRP_RULES["Hot Wheels"]["monster_truck"], MRP_RULES["Hot Wheels"]["monster_truck"]
        elif any(k in t for k in ["formula 1", "f1"]) and "premium" in t:
            return price <= MRP_RULES["Hot Wheels"]["f1_premium"], MRP_RULES["Hot Wheels"]["f1_premium"]
        elif any(k in t for k in ["premium", "car culture", "boulevard", "pop culture"]):
            return price <= MRP_RULES["Hot Wheels"]["premium_single"], MRP_RULES["Hot Wheels"]["premium_single"]
        elif "silver series" in t or "pantone" in t or "performance trucks" in t:
            return price <= MRP_RULES["Hot Wheels"]["silver_or_pop"], MRP_RULES["Hot Wheels"]["silver_or_pop"]
        else:
            return price <= MRP_RULES["Hot Wheels"]["mainline"], MRP_RULES["Hot Wheels"]["mainline"]
    elif brand == "Matchbox":
        if "moving parts" in t:
            return price <= MRP_RULES["Matchbox"]["moving_parts"], MRP_RULES["Matchbox"]["moving_parts"]
        else:
            return price <= MRP_RULES["Matchbox"]["mainline"], MRP_RULES["Matchbox"]["mainline"]
    elif brand == "Majorette":
        if any(k in t for k in ["showcase", "display showcase"]) or price > 650:
            expected = MRP_RULES["Majorette"]["collection"]
        elif any(k in t for k in ["deluxe", "collector box"]) or (price > 450 and price <= 650):
            expected = MRP_RULES["Majorette"]["deluxe"]
        elif any(k in t for k in ["blister", "vintage", "jdm legends", "mercedes-amg", "racing", "tune-ups", "castheads", "porsche"]) or (price > 250 and price <= 450):
            expected = MRP_RULES["Majorette"]["premium"]
        else:
            expected = MRP_RULES["Majorette"]["street"]
        return price <= expected, expected

    return False, None

def extract_coupon(card_text):
    coupon_match = re.search(r"(?:coupon|code|save)\s*:?\s*([A-Z0-9]{4,12})", card_text, re.I)
    if coupon_match:
        return coupon_match.group(1)
    return None

def normalize_and_clean_url(raw_url, domain_prefix=""):
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
    if not raw_url or not raw_url.startswith("http"):
        return raw_url
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
    junk_patterns = [
        r"Added to Cart", r"Shortlisted", r"Already Viewed", r"Out of Stock", r"Notify Me",
        r"\b\d+\s*Left\b", r"Bestseller", r"\bNew\b", r"INTERNATIONAL", r"Sponsored"
    ]
    cleaned = raw_title
    for pattern in junk_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.split(
        r"\b\d+\s*\+?\s*Years?\b|Sizes?\s*:|Perfect for|Set of \d+|\(\d+\s*Ratings?\)|ADD TO CART|Club Price|M\.R\.P",
        cleaned,
        flags=re.IGNORECASE
    )[0]
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -.,|")
    return cleaned

def send_telegram_deal(store, title, price, expected_mrp, link, image_path=None, coupon=None):
    final_link = convert_to_affiliate_link(link)
    clean_title = clean_product_title(title)

    mrp_status = "At MRP" if price == expected_mrp else "Below MRP"

    caption_lines = [
        f"🚨 {clean_title} @ ₹{price:.0f} [{store}] - *{mrp_status}*",
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
        if image_path and os.path.exists(image_path):
            api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            with open(image_path, "rb") as img_file:
                files = {"photo": img_file}
                data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "Markdown"}
                r = requests.post(api_url, data=data, files=files, timeout=20)
                if r.status_code == 200:
                    print(f"   📸 Sent Screenshot [{store}]: {clean_title[:35]} @ ₹{price:.0f} ({mrp_status})", flush=True)
                    return

        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": caption,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False
        }
        r = requests.post(api_url, json=payload, timeout=10)
        if r.status_code == 200:
            print(f"   ✅ Sent Text [{store}]: {clean_title[:35]} @ ₹{price:.0f} ({mrp_status})", flush=True)
    except Exception as e:
        print(f"   ⚠️ Telegram connection error: {e}", flush=True)

def scrape_deals():
    print("🚀 Starting Aligned & In-Stock Filtered Deal Scraper...", flush=True)
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
            timezone_id="Asia/Kolkata"
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
                    time.sleep(2)
                    page.goto(url, timeout=45000, wait_until="domcontentloaded", referer="https://www.amazon.in/")
                    time.sleep(3)

                    product_elements = page.locator("div[data-component-type='s-search-result']")
                    count = product_elements.count()
                    print(f"   Found {count} product cards on Amazon.", flush=True)

                    for i in range(count):
                        try:
                            card = product_elements.nth(i)
                            asin = card.get_attribute("data-asin")
                            if not asin or asin in seen_deals:
                                continue

                            # Scroll Amazon card into view so lazy-loaded images & badges align properly
                            card.scroll_into_view_if_needed()
                            time.sleep(0.3)

                            card_text = card.inner_text()
                            
                            # Skip out-of-stock items
                            if any(oos in card_text.lower() for oos in ["notify me", "out of stock", "currently unavailable"]):
                                continue

                            # Extract clean title from h2 tag instead of dumping entire card text
                            h2_el = card.locator("h2").first
                            title_text = h2_el.inner_text().strip() if h2_el.count() > 0 else card_text.split("\n")[0]

                            if not any(b in title_text.lower() for b in ["hot wheels", "matchbox", "majorette"]):
                                continue

                            price_elem = card.locator(".a-price-whole").first
                            if price_elem.count() == 0:
                                continue
                            price_str = re.sub(r"[^\d.]", "", price_elem.inner_text()).rstrip(".")
                            if not price_str:
                                continue
                            price = float(price_str)

                            brand = determine_brand(title_text)
                            is_valid_mrp, expected_mrp = classify_and_check_mrp(brand, title_text, price)
                            if not is_valid_mrp:
                                continue

                            coupon = extract_coupon(card_text)

                            # Target inner container (.s-card-container) for tight, aligned screenshot
                            inner_box = card.locator(".s-card-container").first
                            target_shot = inner_box if inner_box.count() > 0 else card

                            img_path = f"temp_deal_{asin}.png"
                            target_shot.screenshot(path=img_path)

                            clean_link = f"https://www.amazon.in/dp/{asin}"
                            send_telegram_deal(store, title_text, price, expected_mrp, clean_link, image_path=img_path, coupon=coupon)

                            if os.path.exists(img_path):
                                os.remove(img_path)

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

                    product_elements = page.locator("div.list_block")
                    if product_elements.count() == 0:
                        product_elements = page.locator("div.lft.list_cel, li.child_block")
                    count = product_elements.count()
                    print(f"   Found {count} product cards on FirstCry.", flush=True)

                    for i in range(count):
                        try:
                            card = product_elements.nth(i)
                            card.scroll_into_view_if_needed()
                            time.sleep(0.3)
                            
                            card_text = card.inner_text()
                            
                            # Skip out-of-stock items
                            if any(oos in card_text.lower() for oos in ["notify me", "out of stock", "sold out"]):
                                continue

                            if not any(b in card_text.lower() for b in ["hot wheels", "matchbox", "majorette"]):
                                continue

                            link_el = card.locator("a[href*='hot-wheels'], a[href*='matchbox'], a[href*='majorette'], a[href*='product-detail']").first
                            if link_el.count() == 0:
                                link_el = card.locator("a[href]").first
                            if link_el.count() == 0:
                                continue
                            raw_href = link_el.get_attribute("href")
                            clean_link = normalize_and_clean_url(raw_href, "https://www.firstcry.com")
                            deal_key = clean_link.split("?")[0]

                            if not deal_key or deal_key in seen_deals:
                                continue

                            # Extract clean title from link title attribute or heading
                            title_text = link_el.get_attribute("title") or ""
                            if not title_text:
                                title_el = card.locator(".li_txt1, .p_list_text, .list_title").first
                                title_text = title_el.inner_text().strip() if title_el.count() > 0 else card_text.split("\n")[0]

                            price = 0.0
                            pm_discount = re.search(r"\b([1-9][0-9]{1,4}(?:\.[0-9]{1,2})?)\s+[1-9][0-9]{1,4}(?:\.[0-9]{1,2})?\s*\(\d+%\s*Off\)", card_text, re.I)
                            pm_club = re.search(r"\b([1-9][0-9]{1,4}(?:\.[0-9]{1,2})?)\s*Club Price", card_text, re.I)
                            if pm_discount:
                                price = float(pm_discount.group(1))
                            elif pm_club:
                                price = float(pm_club.group(1))

                            if price <= 10:
                                continue

                            brand = determine_brand(title_text)
                            is_valid_mrp, expected_mrp = classify_and_check_mrp(brand, title_text, price)
                            if not is_valid_mrp:
                                continue

                            coupon = extract_coupon(card_text)

                            img_path = f"temp_fc_{i}.png"
                            card.screenshot(path=img_path)

                            send_telegram_deal(store, title_text, price, expected_mrp, clean_link, image_path=img_path, coupon=coupon)

                            if os.path.exists(img_path):
                                os.remove(img_path)

                            seen_deals.add(deal_key)
                            new_seen_count += 1
                            time.sleep(1)
                        except Exception:
                            continue

            except Exception as e:
                print(f"⚠️ Error scraping {store}: {e}", flush=True)

        browser.close()

    save_seen_deals(seen_deals)
    print(f"\n🏁 Deal scan complete. Added {new_seen_count} new in-stock deals to cache.", flush=True)

if __name__ == "__main__":
    scrape_deals()
