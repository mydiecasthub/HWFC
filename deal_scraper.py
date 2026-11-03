import os
import sys
import time
import re
import requests
from playwright.sync_api import sync_playwright

# Force unbuffered logs for GitHub Actions
sys.stdout.reconfigure(line_buffering=True)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
CUELINKS_API_KEY = os.environ.get("CUELINKS_API_KEY", "")

TARGET_URLS = [
    {
        "store": "Amazon.in",
        "url": "https://www.amazon.in/s?k=Hot+Wheels&rh=p_n_deal_type%3A26903607031"
    },
    {
        "store": "FirstCry",
        "url": "https://www.firstcry.com/hot-wheels/0/0/all/10/0_arr_v-desc/1"
    }
]

def convert_to_affiliate_link(raw_url):
    """
    Takes any raw product URL and converts it into a tracked 
    Cuelinks affiliate link using the official API v3 endpoint.
    """
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
            tracked_url = data.get("tracking_url") or data.get("url", raw_url)
            return tracked_url
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
        if r.status_code != 200:
            print(f"⚠️ Telegram Error: {r.text}", flush=True)
    except Exception as e:
        print(f"⚠️ Telegram connection error: {e}", flush=True)

def scrape_deals():
    print("🚀 Starting Mass-Market Deal Scraper...", flush=True)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        for target in TARGET_URLS:
            store = target["store"]
            url = target["url"]
            print(f"-> Scanning {store} ({url})...", flush=True)

            try:
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                time.sleep(3)

                page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                time.sleep(2)

                if "amazon" in store.lower():
                    items = page.locator("div.s-result-item[data-component-type='s-search-result']").all()
                    print(f"   Found {len(items)} items on Amazon search page.", flush=True)

                    for item in items[:20]:
                        try:
                            title_elem = item.locator("h2 a span").first
                            price_whole = item.locator(".a-price-whole").first
                            link_elem = item.locator("h2 a").first

                            if not title_elem.is_visible() or not price_whole.is_visible():
                                continue

                            title = title_elem.inner_text().strip()
                            price_str = price_whole.inner_text().replace(",", "").strip()
                            price = float(price_str)
                            link = link_elem.get_attribute("href")

                            if link and not link.startswith("http"):
                                link = f"https://www.amazon.in{link}"

                            if "hot wheels" in title.lower():
                                print(f"   🎯 MATCH: {title} at ₹{price}", flush=True)
                                send_telegram_deal(store, title, price, link)
                        except Exception:
                            continue

                elif "firstcry" in store.lower():
                    items = page.locator(".lft.list_cel").all()
                    print(f"   Found {len(items)} items on FirstCry page.", flush=True)

                    for item in items[:20]:
                        try:
                            title_elem = item.locator(".p_list_text").first
                            price_elem = item.locator(".lft.r-price").first
                            link_elem = item.locator("a").first

                            if not title_elem.is_visible():
                                continue

                            title = title_elem.inner_text().strip()
                            raw_price_text = price_elem.inner_text() if price_elem.is_visible() else "0"
                            
                            price_match = re.search(r"([0-9,]+)", raw_price_text)
                            if not price_match:
                                continue
                            price = float(price_match.group(1).replace(",", ""))

                            link = link_elem.get_attribute("href")
                            if link and not link.startswith("http"):
                                link = f"https://www.firstcry.com{link}"

                            if "hot wheels" in title.lower():
                                print(f"   🎯 MATCH: {title} at ₹{price}", flush=True)
                                send_telegram_deal(store, title, price, link)
                        except Exception:
                            continue

            except Exception as e:
                print(f"⚠️ Error scraping {store}: {e}", flush=True)

        browser.close()
    print("🏁 Deal scan complete.", flush=True)

if __name__ == "__main__":
    scrape_deals()
