import os
import sys
import time
import re
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

def send_telegram_deal(store, title, price, link, coupon=None):
    final_link = convert_to_affiliate_link(link)

    coupon_text = f"\n🏷 *Coupon:* `{coupon}`" if coupon else ""
    trick_note = (
        "\n💡 *Tip:* If it shows 'Out of Stock', open the link, go to your "
        "Profile / Wishlist section, and add it to your cart from there!"
    ) if "firstcry" in store.lower() else ""

    caption = (
        f"🔥 *HOT WHEELS ALERT | {store}*\n\n"
        f"🏎 *{title}*\n"
        f"💰 *Price:* ₹{price:.0f}"
        f"{coupon_text}"
        f"{trick_note}\n\n"
        f"🔗 [Click Here to Buy]({final_link})"
    )

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": caption,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        r = requests.post(api_url, json=payload, timeout=10)
        if r.status_code == 200:
            print(f"   ✅ Sent to Telegram: {title[:35]}...", flush=True)
        else:
            print(f"   ⚠️ Telegram Error: {r.text}", flush=True)
    except Exception as e:
        print(f"   ⚠️ Telegram connection error: {e}", flush=True)

def scrape_deals():
    print("🚀 Starting Mass-Market Deal Scraper...", flush=True)
    
    with sync_playwright() as p:
        # Launch browser with anti-detection flags
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage"
            ]
        )
        
        # Spoof Indian locale and timezone so Amazon.in & FirstCry serve Indian catalog
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            extra_http_headers={
                "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Upgrade-Insecure-Requests": "1"
            }
        )
        
        # Mask webdriver property
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        for target in TARGET_URLS:
            store = target["store"]
            url = target["url"]
            print(f"\n-> Scanning {store} ({url})...", flush=True)

            try:
                response = page.goto(url, timeout=45000, wait_until="domcontentloaded")
                time.sleep(4)
                
                # Scroll down to trigger lazy-loaded product cards
                page.evaluate("window.scrollBy(0, 1200)")
                time.sleep(2)

                page_title = page.title()
                status_code = response.status if response else "Unknown"
                print(f"   [Debug] HTTP {status_code} | Page Title: '{page_title}'", flush=True)

                html = page.content()
                soup = BeautifulSoup(html, "html.parser")

                if "amazon" in store.lower():
                    # Locate all search result cards with a valid ASIN
                    items = soup.select("div[data-component-type='s-search-result'], div.s-result-item[data-asin]")
                    valid_items = [i for i in items if i.get("data-asin")]
                    print(f"   Found {len(valid_items)} product cards on Amazon.", flush=True)

                    if len(valid_items) == 0 and ("captcha" in html.lower() or "robot" in html.lower()):
                        print("   ⚠️ Amazon served a Bot/CAPTCHA check page.", flush=True)

                    matched_count = 0
                    for item in valid_items:
                        if matched_count >= 5:  # Cap at 5 alerts per run during testing so Telegram doesn't flood
                            break
                        try:
                            title_elem = item.select_one("h2 span") or item.select_one("h2")
                            price_elem = item.select_one(".a-price-whole")
                            link_elem = item.select_one("h2 a") or item.select_one("a.a-link-normal.s-no-outline")

                            if not title_elem or not price_elem or not link_elem:
                                continue

                            title = title_elem.get_text(strip=True)
                            price_str = re.sub(r"[^\d.]", "", price_elem.get_text(strip=True)).rstrip(".")
                            if not price_str:
                                continue
                            price = float(price_str)

                            link = link_elem.get("href", "")
                            if link and not link.startswith("http"):
                                link = f"https://www.amazon.in{link}"

                            if "hot wheels" in title.lower():
                                print(f"   🎯 MATCH: {title[:50]}... at ₹{price}", flush=True)
                                send_telegram_deal(store, title, price, link)
                                matched_count += 1
                                time.sleep(1)
                        except Exception:
                            continue

                elif "firstcry" in store.lower():
                    # Broad fallback selectors for FirstCry product grids
                    items = soup.select("div.list_block, div.lft.list_cel, div.main_prod_box, li.child_block")
                    print(f"   Found {len(items)} product cards on FirstCry.", flush=True)

                    matched_count = 0
                    # If standard grid classes changed, fallback to scanning all product links directly
                    if not items:
                        links = soup.find_all("a", href=re.compile(r"/product-detail", re.I))
                        print(f"   [Fallback] Found {len(links)} direct product links on FirstCry.", flush=True)
                        seen_urls = set()
                        for a_tag in links:
                            if matched_count >= 5:
                                break
                            href = a_tag.get("href", "")
                            title = a_tag.get("title") or a_tag.get_text(strip=True)
                            if not title or "hot wheels" not in title.lower() or href in seen_urls:
                                continue
                            seen_urls.add(href)
                            full_link = href if href.startswith("http") else f"https://www.firstcry.com{href}"
                            
                            # Look for price in parent container
                            parent_text = a_tag.parent.parent.get_text(" ", strip=True) if a_tag.parent and a_tag.parent.parent else ""
                            price_match = re.search(r"(?:Rs\.?|₹)\s*([0-9,]+(?:\.[0-9]+)?)", parent_text, re.I) or re.search(r"\b([1-9][0-9]{2,4})\b", parent_text)
                            price = float(price_match.group(1).replace(",", "")) if price_match else 0

                            print(f"   🎯 MATCH: {title[:50]}... at ₹{price}", flush=True)
                            send_telegram_deal(store, title, price, full_link)
                            matched_count += 1
                            time.sleep(1)
                    else:
                        for item in items:
                            if matched_count >= 5:
                                break
                            try:
                                a_tag = item.find("a", href=True)
                                if not a_tag:
                                    continue
                                title = a_tag.get("title") or item.get_text(" ", strip=True)
                                if "hot wheels" not in title.lower():
                                    continue

                                link = a_tag["href"]
                                if not link.startswith("http"):
                                    link = f"https://www.firstcry.com{link}"

                                item_text = item.get_text(" ", strip=True)
                                price_match = re.search(r"([0-9,]+(?:\.[0-9]+)?)", item_text)
                                price = float(price_match.group(1).replace(",", "")) if price_match else 0

                                print(f"   🎯 MATCH: {title[:50]}... at ₹{price}", flush=True)
                                send_telegram_deal(store, title, price, link)
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
