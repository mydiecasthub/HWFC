import os
import sys
import time
import json
import re
import html
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

# MRP Rules Dictionary (Fully calibrated to exact retail MRPs)
MRP_RULES = {
    "Hot Wheels": {
        # Basics
        "mainline": 179,               # Mainlines, Basic Themed Assortments, Mystery Models
        "5_pack": 899,                 # Standard 5-Packs
        "color_reveal": 999,           # Color Reveal 2-Pack Drum
        
        # Silver-Label Premium
        "silver_series": 299,          # Automotive, Vintage Racing, Celebrations, Entertainment, Batman, Ultra Hots, Neon Speeders
        "themed_6_pack": 1999,         # Silver Series / Themed 6-Packs (Japanese, Euro, ZAMAC, Streets of Japan)
        
        # Gold-Label Premium
        "premium_single": 549,         # Boulevard, Car Culture, Pop Culture, Premium Fast & Furious
        "car_culture_2_pack": 1099,    # Car Culture 2-Packs
        "team_transport": 1499,        # Team Transport
        "diorama_or_box_set": 2999,    # Premium Display Sets / Collector Diorama Sets
        "premium_5_pack": 3999,        # Fast & Furious / Car Culture 5-Car Bundles
        
        # Other Lines
        "monster_truck": 399,          # 1:64 Monster Trucks
        "f1_premium": 699,             # Formula 1 Premium
    },
    "Matchbox": {
        "mainline": 179,               # Basic Mainlines & Themed singles (e.g. Matchbox The Movie)
        "moving_parts": 399,           # Moving Parts (299 older / 399 current MRP)
        "working_rigs": 599,           # Real Working Rigs & Adventure Packs
        "collectors": 649,             # Matchbox Collectors Series (True Grip / Metal Base)
        "5_pack": 899,                 # Standard 5-Pack
        "convoy": 999,                 # Matchbox Convoys (Truck + Trailer)
        "8_or_9_pack": 1599,           # 8-Pack / 9-Pack Gift Sets
    },
    "Majorette": {
        "street": 199,                 # Basic Street Cars (179 older / 199 current MRP)
        "premium": 399,                # Premium, Racing, Vintage, JDM Legends, CastHeads, Color Changers, Showroom
        "deluxe": 599,                 # Deluxe Cars (with collector box), Monster Rockerz, City Bus/Trailer
        "tune_ups": 699,               # Tune-Ups Series
        "street_5_pack": 899,          # 5-Piece Basic Street Cars Set
        "trailer_set": 1199,           # Trailer + 2 Die-Cast Cars Set
        "edition_5_pack": 1699,        # 5-Piece Porsche / Black / Vintage / Racing / JDM Gift Packs
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

def classify_and_check_mrp(brand, title, price, scraped_mrp=None):
    t = title.lower()
    if any(skip in t for skip in [
        "case of", "collector case", "blind box", "track", "playset", "play set", "petrol station",
        "garage", "carry case", "launcher", "protector", "lapel pin", "magnet", "rotiform",
        "1:36", "1:43", "1:24", "1:18", "1/43", "1/24", "1/18", "red line club", "rlc", "elite 64",
        "conventions", "skate", "fingerboard", "monster jam", "rc ", "remote control"
    ]):
        return False, None

    # Known Hot Wheels 5-Pack assortment names (for when FirstCry omits '5 Pack' from the title)
    hw_5pack_themes = [
        "hw torque", "motor show", "street beasts", "retro racers", "nightburnerz",
        "x-raycers", "exotics", "hot trucks", "mud studs", "hw flames", "hw city",
        "hw track", "action pack", "stunt", "car meet", "mopar", "mustang", "corvette", "nissan"
    ]

    if brand == "Hot Wheels":
        # Check if FirstCry's scraped MRP explicitly confirms a standard 5-Pack (899), Silver Series (299), or Premium (549)
        if scraped_mrp and round(scraped_mrp) in [299, 399, 549, 699, 899, 999, 1099, 1499, 1999, 2999, 3999]:
            return price <= scraped_mrp, round(scraped_mrp)

        # Gold-Label Premium 5-Pack Bundles (F&F / Car Culture)
        if any(k in t for k in ["fast & furious", "fast and furious", "car culture", "premium bundle", "movie franchise"]) and any(p in t for p in ["5-pack", "5 pack", "5 cars", "bundle", "set of 5", "5 1:64"]):
            return price <= MRP_RULES["Hot Wheels"]["premium_5_pack"], MRP_RULES["Hot Wheels"]["premium_5_pack"]

        # Silver-Label Themed 6-Packs
        if any(k in t for k in ["6-pack", "6 pack", "pack of 6", "6 cars", "6pc", "6 pcs", "set of 6"]) and any(s in t for s in ["silver", "zamac", "japanese", "european", "streets of japan", "themed", "anniversary", "celebrations", "legends"]):
            return price <= MRP_RULES["Hot Wheels"]["themed_6_pack"], MRP_RULES["Hot Wheels"]["themed_6_pack"]

        # Gold-Label Car Culture 2-Packs
        if any(k in t for k in ["2-pack", "2 pack", "pack of 2", "2 cars", "set of 2"]) and any(p in t for p in ["car culture", "premium", "real riders"]):
            return price <= MRP_RULES["Hot Wheels"]["car_culture_2_pack"], MRP_RULES["Hot Wheels"]["car_culture_2_pack"]

        # Basics: Color Reveal 2-Pack
        if "color reveal" in t or "colour reveal" in t:
            return price <= MRP_RULES["Hot Wheels"]["color_reveal"], MRP_RULES["Hot Wheels"]["color_reveal"]

        # Gold-Label Team Transport
        if any(k in t for k in ["team transport", "fleet flyer", "retro rig", "aero lift", "sakura sprinter", "euro hauler", "carry on"]):
            return price <= MRP_RULES["Hot Wheels"]["team_transport"], MRP_RULES["Hot Wheels"]["team_transport"]

        # Gold-Label Premium Display Sets / Dioramas
        if any(k in t for k in ["diorama", "display set", "premium display", "collector set", "box set", "heritage gift pack"]):
            return price <= MRP_RULES["Hot Wheels"]["diorama_or_box_set"], MRP_RULES["Hot Wheels"]["diorama_or_box_set"]

        # Basics: Standard 5-Packs (explicit keyword OR known 5-pack theme priced between ₹400 and ₹899)
        if (any(k in t for k in ["5 pack", "5-pack", "pack of 5", "5 pcs", "gift pack", "giftpack", "set of 5"]) or (
            any(theme in t for theme in hw_5pack_themes) and 400 <= price <= 899
        )) and "silver" not in t and "premium" not in t:
            return price <= MRP_RULES["Hot Wheels"]["5_pack"], MRP_RULES["Hot Wheels"]["5_pack"]

    elif brand == "Matchbox":
        if any(k in t for k in ["8 pack", "8-pack", "pack of 8", "9 pack", "9-pack", "pack of 9"]):
            return price <= MRP_RULES["Matchbox"]["8_or_9_pack"], MRP_RULES["Matchbox"]["8_or_9_pack"]
        if any(k in t for k in ["5 pack", "5-pack", "pack of 5", "5 pcs", "gift pack"]):
            return price <= MRP_RULES["Matchbox"]["5_pack"], MRP_RULES["Matchbox"]["5_pack"]
        if "convoy" in t:
            return price <= MRP_RULES["Matchbox"]["convoy"], MRP_RULES["Matchbox"]["convoy"]
        if any(k in t for k in ["working rigs", "working rig", "adventure pack"]):
            return price <= MRP_RULES["Matchbox"]["working_rigs"], MRP_RULES["Matchbox"]["working_rigs"]

    elif brand == "Majorette":
        # Majorette 5-Piece Gift Packs (Edition vs Street)
        if any(k in t for k in ["5 piece", "5-piece", "5 pack", "5-pack", "pack of 5", "5 pcs", "gift pack", "gift set", "car set"]):
            if any(ed in t for ed in ["porsche", "black edition", "vintage", "jdm", "racing", "mercedes", "lamborghini", "deluxe", "premium", "limited", "color changer"]):
                return price <= MRP_RULES["Majorette"]["edition_5_pack"], MRP_RULES["Majorette"]["edition_5_pack"]
            else:
                return price <= MRP_RULES["Majorette"]["street_5_pack"], MRP_RULES["Majorette"]["street_5_pack"]
        # Majorette Trailer + 2 Cars Sets
        if "trailer" in t and any(k in t for k in ["2 die", "2 cars", "wagon"]):
            return price <= MRP_RULES["Majorette"]["trailer_set"], MRP_RULES["Majorette"]["trailer_set"]

    # --- 2. GENERAL MULTI-PACK MULTIPLIER LOOP (For seller bundles of 2 to 10 cars) ---
    for num_cars in range(2, 11):
        if (f"pack of {num_cars}" in t or f"{num_cars} pack" in t or f"{num_cars}-pack" in t or f"{num_cars}pc" in t or 
            f"{num_cars} pcs" in t or f"set of {num_cars}" in t or f"set {num_cars}" in t or 
            f"{num_cars} cars" in t or f"{num_cars}-car" in t or f"{num_cars} piece" in t or f"{num_cars}-piece" in t):
            if brand == "Hot Wheels":
                if any(k in t for k in ["formula 1", "f1"]) and "premium" in t:
                    per_car_mrp = MRP_RULES["Hot Wheels"]["f1_premium"]
                elif any(k in t for k in ["car culture", "boulevard", "premium", "pop culture"]):
                    per_car_mrp = MRP_RULES["Hot Wheels"]["premium_single"]
                elif any(k in t for k in [
                    "silver series", "silver label", "ultra hots", "neon speeders", "vintage racing",
                    "celebrations", "pantone", "performance trucks", "streets of japan", "batman", "fast & furious", "fast and furious"
                ]):
                    per_car_mrp = MRP_RULES["Hot Wheels"]["silver_series"]
                else:
                    per_car_mrp = MRP_RULES["Hot Wheels"]["mainline"]
            elif brand == "Matchbox":
                per_car_mrp = MRP_RULES["Matchbox"]["moving_parts"] if "moving parts" in t else MRP_RULES["Matchbox"]["mainline"]
            else:
                if "deluxe" in t:
                    per_car_mrp = MRP_RULES["Majorette"]["deluxe"]
                elif any(k in t for k in ["premium", "racing", "vintage", "jdm", "castheads", "color changer"]):
                    per_car_mrp = MRP_RULES["Majorette"]["premium"]
                else:
                    per_car_mrp = MRP_RULES["Majorette"]["street"]
            expected_mrp = per_car_mrp * num_cars
            return price <= expected_mrp, expected_mrp

    # --- 3. SINGLE CARDS CLASSIFICATION ---
    if brand == "Hot Wheels":
        if "monster truck" in t:
            return price <= MRP_RULES["Hot Wheels"]["monster_truck"], MRP_RULES["Hot Wheels"]["monster_truck"]
        elif any(k in t for k in ["formula 1", "f1"]) and "premium" in t:
            return price <= MRP_RULES["Hot Wheels"]["f1_premium"], MRP_RULES["Hot Wheels"]["f1_premium"]
        elif any(k in t for k in ["boulevard", "car culture", "pop culture", "real riders", "metal/metal"]) or (
            "premium" in t and "silver" not in t
        ):
            return price <= MRP_RULES["Hot Wheels"]["premium_single"], MRP_RULES["Hot Wheels"]["premium_single"]
        elif any(k in t for k in [
            "silver series", "silver label", "ultra hots", "neon speeders", "vintage racing",
            "celebrations", "themed entertainment", "batman", "pantone", "performance trucks",
            "streets of japan", "anniversary", "fast & furious", "fast and furious", "gran turismo", "forza"
        ]):
            return price <= MRP_RULES["Hot Wheels"]["silver_series"], MRP_RULES["Hot Wheels"]["silver_series"]
        else:
            return price <= MRP_RULES["Hot Wheels"]["mainline"], MRP_RULES["Hot Wheels"]["mainline"]

    elif brand == "Matchbox":
        if "collectors" in t or "true grip" in t:
            return price <= MRP_RULES["Matchbox"]["collectors"], MRP_RULES["Matchbox"]["collectors"]
        elif "moving parts" in t:
            return price <= MRP_RULES["Matchbox"]["moving_parts"], MRP_RULES["Matchbox"]["moving_parts"]
        else:
            return price <= MRP_RULES["Matchbox"]["mainline"], MRP_RULES["Matchbox"]["mainline"]

    elif brand == "Majorette":
        if any(k in t for k in ["tune-ups", "tune ups", "tune up"]):
            expected = MRP_RULES["Majorette"]["tune_ups"]
        elif any(k in t for k in ["deluxe", "collector box", "monster rockerz", "city bus", "city trailer", "aeroplane", "airplane"]):
            expected = MRP_RULES["Majorette"]["deluxe"]
        elif any(k in t for k in [
            "premium", "racing", "vintage", "jdm", "castheads", "cast heads", "color changer",
            "colour changer", "showroom", "the originals", "volkswagen", "porsche", "mercedes-amg",
            "black edition", "limited edition", "japanese", "italian"
        ]):
            expected = MRP_RULES["Majorette"]["premium"]
        else:
            expected = MRP_RULES["Majorette"]["street"]
        return price <= expected, expected

    return False, None

def extract_amazon_coupon(card_text):
    code_match = re.search(r"(?:with\s+coupon|use\s+code|with\s+code|promo\s+code)\s*:?\s*([A-Z0-9]{5,12})\b", card_text, re.I)
    if code_match and code_match.group(1).upper() not in ["COUPON", "APPLIED", "SAVINGS", "DETAILS"]:
        return code_match.group(1).upper()
    
    clip_match = re.search(r"(Save\s+(?:₹\s*\d+|\d+%)\s+with\s+coupon)", card_text, re.I)
    if clip_match:
        return f"{clip_match.group(1)} (Clip on page)"
    return None

def extract_firstcry_pdp_details(context, product_url, fallback_price, expected_mrp):
    pdp_page = context.new_page()
    coupon_code = None
    best_price = fallback_price
    screenshot_path = None

    try:
        pdp_page.goto(product_url, timeout=30000, wait_until="domcontentloaded")
        time.sleep(2.5)

        pdp_text = pdp_page.locator("body").inner_text()

        code_patterns = [
            r"(?:Use\s+Code|Coupon\s+Code|Promo\s+Code|Code)\s*[:\-]?\s*\b([A-Z0-9]{5,12})\b",
            r"\b([A-Z0-9]{5,10})\b\s*(?:to\s+avail|for\s+extra|off\s+on|at\s+checkout)",
            r"Apply\s+code\s+\b([A-Z0-9]{5,12})\b"
        ]
        ignore_words = {
            "FIRSTCRY", "DETAILS", "APPLIED", "COUPON", "OFFERS", "AVAILABLE", 
            "CHECKOUT", "SAVINGS", "DISCOUNT", "ELIGIBLE", "PRODUCTS", "MINIMUM"
        }

        for pat in code_patterns:
            matches = re.findall(pat, pdp_text)
            for m in matches:
                candidate = m.strip().upper()
                if candidate not in ignore_words and any(c.isalpha() for c in candidate):
                    coupon_code = candidate
                    break
            if coupon_code:
                break

        if not coupon_code:
            offer_elems = pdp_page.locator(".cpn_code, .offer_code, .promo-code, .coup_code, [class*='coupon'], [class*='offer']")
            for idx in range(min(offer_elems.count(), 8)):
                el_txt = offer_elems.nth(idx).inner_text()
                m_dom = re.search(r"\b([A-Z]{2,6}[0-9]{2,6}[A-Z0-9]*)\b", el_txt)
                if m_dom and m_dom.group(1) not in ignore_words:
                    coupon_code = m_dom.group(1)
                    break

        if coupon_code:
            pct_match = re.search(rf"(\d{{2}})%\s*Off.*?(?:{coupon_code})", pdp_text, re.I | re.S)
            if not pct_match:
                pct_match = re.search(rf"(?:{coupon_code}).*?(\d{{2}})%\s*Off", pdp_text, re.I | re.S)
            if pct_match:
                pct = float(pct_match.group(1))
                if 10 <= pct <= 70 and expected_mrp:
                    calculated_price = round(expected_mrp * (1 - pct / 100.0))
                    if calculated_price < best_price:
                        best_price = calculated_price

        best_p_match = re.search(r"(?:Best\s+Price|Get\s+it\s+(?:for|at)|After\s+Coupon|Club\s+Price)\s*[:₹Rs.]*\s*([1-9][0-9]{1,4}(?:\.[0-9]{1,2})?)", pdp_text, re.I)
        if best_p_match:
            candidate_p = float(best_p_match.group(1))
            if 40 < candidate_p < best_price:
                best_price = candidate_p

        pdp_container = pdp_page.locator("div.prod-info-wrap, div.pdp-main, div.product-detail-sec, div.main_prod_sec").first
        screenshot_path = f"temp_fc_pdp_{int(time.time()*1000)}.png"
        if pdp_container.count() > 0:
            pdp_container.scroll_into_view_if_needed()
            time.sleep(0.3)
            pdp_container.screenshot(path=screenshot_path)
        else:
            pdp_page.screenshot(path=screenshot_path, clip={"x": 50, "y": 80, "width": 1000, "height": 580})

    except Exception as e:
        print(f"   ⚠️ FirstCry PDP check warning: {e}", flush=True)
    finally:
        pdp_page.close()

    return coupon_code, best_price, screenshot_path

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
    clean_title = html.escape(clean_product_title(title))
    safe_store = html.escape(store)

    if round(price) < round(expected_mrp):
        savings = round(expected_mrp - price)
        header_banner = f"🔥 <b>BELOW MRP DEAL (Save ₹{savings} | MRP: ₹{expected_mrp:.0f})</b> 🔥"
        mrp_status = "Below MRP"
    else:
        header_banner = f"✅ <b>AT MRP ALERT (MRP: ₹{expected_mrp:.0f})</b> ✅"
        mrp_status = "At MRP"

    if "amazon" in store.lower():
        link_display_text = "🔗 link.amazon"
    elif "firstcry" in store.lower():
        link_display_text = "🔗 link.firstcry"
    else:
        link_display_text = f"🔗 link.{store.lower().replace(' ', '')}"

    formatted_link = f'<a href="{final_link}">{link_display_text}</a>'

    caption_lines = [
        header_banner,
        "",
        f"🚨 <b>{clean_title} @ ₹{price:.0f} [{safe_store}]</b>",
        formatted_link
    ]

    if coupon:
        safe_coupon = html.escape(str(coupon))
        caption_lines.append(f"\n📍 <b>Coupon:</b> <code>{safe_coupon}</code>")

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
                data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}
                r = requests.post(api_url, data=data, files=files, timeout=20)
                if r.status_code == 200:
                    print(f"   📸 Sent Screenshot [{store}]: {clean_title[:35]} @ ₹{price:.0f} ({mrp_status})", flush=True)
                    return

        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": caption,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        r = requests.post(api_url, json=payload, timeout=10)
        if r.status_code == 200:
            print(f"   ✅ Sent Text [{store}]: {clean_title[:35]} @ ₹{price:.0f} ({mrp_status})", flush=True)
    except Exception as e:
        print(f"   ⚠️ Telegram connection error: {e}", flush=True)

def scrape_deals():
    print("🚀 Starting Fully-Calibrated Deal Scraper...", flush=True)
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

                            card.scroll_into_view_if_needed()
                            time.sleep(0.3)

                            card_text = card.inner_text()
                            if any(oos in card_text.lower() for oos in ["notify me", "out of stock", "currently unavailable"]):
                                continue

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

                            coupon = extract_amazon_coupon(card_text)

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
                            _, expected_mrp = classify_and_check_mrp(brand, title_text, price)
                            if not expected_mrp:
                                continue

                            coupon, final_price, pdp_img_path = extract_firstcry_pdp_details(context, clean_link, price, expected_mrp)

                            if final_price > expected_mrp:
                                if pdp_img_path and os.path.exists(pdp_img_path):
                                    os.remove(pdp_img_path)
                                continue

                            if pdp_img_path and os.path.exists(pdp_img_path):
                                img_path = pdp_img_path
                            else:
                                img_path = f"temp_fc_{i}.png"
                                card.screenshot(path=img_path)

                            send_telegram_deal(store, title_text, final_price, expected_mrp, clean_link, image_path=img_path, coupon=coupon)

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
