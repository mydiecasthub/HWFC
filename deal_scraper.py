import os
import sys
import time
import json
import re
import html
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# Force unbuffered logs for GitHub Actions
sys.stdout.reconfigure(line_buffering=True)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
CUELINKS_API_KEY = os.environ.get("CUELINKS_API_KEY", "")

CACHE_FILE = "seen_deals.json"

# Persistent HTTP session for parallel 0.15s FirstCry PDP live-stock probes
HTTP_SESSION = requests.Session()
adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
HTTP_SESSION.mount("https://", adapter)
HTTP_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache"
})

# Official Indian Retail MRP Rules (Fallback ceilings if scraped_mrp is missing)
MRP_RULES = {
    "Hot Wheels": {
        "mainline": 179,
        "5_pack": 899,
        "super_rig": 999,
        "color_reveal": 999,
        "silver_series": 299,
        "themed_6_pack": 1999,
        "premium_single": 549,
        "car_culture_2_pack": 1099,
        "team_transport": 1499,
        "diorama_or_box_set": 2999,
        "premium_5_pack": 3999,
        "monster_truck": 399,
        "f1_premium": 799,
        "f1_2_pack": 1599,
    },
    "Matchbox": {
        "mainline": 179,
        "moving_parts": 399,
        "working_rigs": 599,
        "collectors": 649,
        "5_pack": 899,
        "convoy": 999,
        "8_or_9_pack": 1599,
    },
    "Majorette": {
        "street": 199,
        "premium": 399,
        "deluxe": 599,
        "tune_ups": 699,
        "street_5_pack": 899,
        "trailer_set": 1199,
        "edition_5_pack": 1699,
    }
}

# DEDICATED FIRSTCRY TARGET URLS
# Includes dedicated Mainline snipers ("250", "die cast free wheel", "toy car") + New Arrivals + F1/Premiums
TARGET_URLS = [
    # 1. High-Priority F1, Ferrari & Premium Grails
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20williams%20alpine", "scrolls": 2},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20formula", "scrolls": 2},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20ferrari", "scrolls": 2},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20premium", "scrolls": 3},

    # 2. Dedicated Mainline Snipers (Catches 161/250 Escort, 245/250 Cuda, etc. buried in normal search!)
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20250", "scrolls": 6},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20die%20cast%20free%20wheel", "scrolls": 6},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20toy%20car", "scrolls": 6},

    # 3. Full Brand Catalogs & Multi-Packs
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%205%20pack", "scrolls": 3},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels", "scrolls": 6},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20monster%20truck", "scrolls": 2},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=majorette", "scrolls": 4},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=majorette%20tune%20ups", "scrolls": 2},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=matchbox", "scrolls": 4},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=matchbox%20moving%20parts", "scrolls": 2},
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
        trimmed_list = list(seen_set)[-1000:]
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


def cap_with_scraped_mrp(expected_mrp, price, scraped_mrp):
    if scraped_mrp and 150 <= scraped_mrp < expected_mrp:
        expected_mrp = round(scraped_mrp)
    return price <= expected_mrp, expected_mrp


def classify_and_check_mrp(brand, title, price, scraped_mrp=None):
    t = re.sub(r'[®™©–—/|]', ' ', title.lower())
    t = re.sub(r'\s+', ' ', t).strip()

    # STRICT ACCESSORY & NON-DIECAST FILTER: Block stands, cases, mats, tracks, keychains, etc.
    if re.search(r'\b(stand|stands|protector|protectors|acrylic|keychain|magnet|organizer|storage|shelf|rack|mat)\b', t, re.I):
        return False, None

    if any(skip in t for skip in [
        "display stand", "stand for", "cars not included", "car not included", "acrylic case",
        "storage box", "wall mount", "case of", "collector case", "blind box", "track",
        "playset", "play set", "petrol station", "garage", "carry case", "launcher",
        "lapel pin", "rotiform", "1:36", "1:43", "1:24", "1:18", "1/43", "1/24", "1/18",
        "red line club", "rlc", "elite 64", "conventions", "skate", "fingerboard",
        "monster jam", "rc ", "remote control", "puzzle", "book", "sticker", "t-shirt", "bag"
    ]):
        return False, None

    # FIRSTCRY TRUST RULE: Always use the MRP scraped from the FirstCry card/page when available!
    if scraped_mrp and scraped_mrp >= 150:
        return price <= scraped_mrp, round(scraped_mrp)

    # --- 0. FOOLPROOF MAINLINE NUMBERING DETECTOR ---
    if brand == "Hot Wheels":
        if re.search(r'\b([1-9][0-9]?|1[0-9]{2}|2[0-4][0-9]|250)\s*/\s*250\b', title):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["mainline"], price, scraped_mrp)
        if re.search(r'\b([1-9]|10)\s*/\s*(10|5)\b', title):
            if not any(s in t for s in ["silver series", "silver label", "car culture", "pop culture", "boulevard", "real riders", "premium", "ultra hots", "neon speeders", "pantone"]):
                return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["mainline"], price, scraped_mrp)

    f1_keywords = [
        "formula 1", "formula one", "f1", "race team",
        "atlassian", "williams racing", "williams jkd", "bwt alpine", "alpine formula",
        "oracle red bull", "red bull racing", "scuderia ferrari", "ferrari hp",
        "mercedes-amg petronas", "petronas formula", "mclaren formula", "aston martin aramco",
        "visa cash app", "racing bulls", "vcarb", "kick sauber", "stake f1", "moneygram haas", "haas f1",
        "hrv11", "jkd81", "jkd82", "jkd83", "jkd84", "jkd85", "jkd86", "jkd87", "jkd88", "jkd89", "jdk81",
        "rb20", "rb21", "sf-24", "sf-25", "w15", "w16", "mcl38", "mcl39", "amr24", "amr25",
        "a524", "a525", "fw46", "fw47", "vf-24", "vf-25",
        "verstappen", "perez", "leclerc", "hamilton", "norris", "piastri", "russell", "antonelli",
        "alonso", "stroll", "sainz", "albon", "gasly", "doohan", "colapinto", "tsunoda", "lawson",
        "hulkenberg", "bortoleto", "ocon", "bearman"
    ]

    car_culture_and_boulevard_keywords = [
        "boulevard", "car culture", "pop culture", "modern classics", "morden classics",
        "terra trek", "world tour", "race day", "japan historics", "exotic envy", "jay leno",
        "spettacolare", "slide street", "drag strip", "ronin run", "mountain drifters", "auto strasse",
        "forza horizon", "nismo 270r", "kool kombi", "mattel 80th", "replica from popular movie"
    ]

    # --- 1. BRAND-SPECIFIC MULTI-CAR PACKS & SPECIAL BOX SETS ---
    if brand == "Hot Wheels":
        if any(k in t for k in ["team transport", "kousoku", "fleet flyer", "retro rig", "aero lift", "sakura sprinter", "euro hauler", "carry on", "transport hauler"]):
            return price <= MRP_RULES["Hot Wheels"]["team_transport"], MRP_RULES["Hot Wheels"]["team_transport"]

        if any(k in t for k in ["super rig", "super rigs", "heavy doody", "hauling rig", "transporter vehicle with 1"]):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["super_rig"], price, scraped_mrp)

        if any(k in t for k in ["car culture", "premium bundle", "collector bundle", "real riders"]) and any(p in t for p in ["5-pack", "5 pack", "5-car pack", "5 car pack", "5 cars", "bundle", "set of 5", "5 1:64"]):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["premium_5_pack"], price, scraped_mrp)

        if any(k in t for k in ["5 pack", "5-pack", "5-car pack", "5 car pack", "pack of 5", "5 pcs", "gift pack", "giftpack", "set of 5", "set of 5 toy cars"]) and "silver" not in t and "car culture" not in t and "real riders" not in t:
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["5_pack"], price, scraped_mrp)

        if any(k in t for k in ["6-pack", "6 pack", "6-car pack", "pack of 6", "6 cars", "6pc", "6 pcs", "set of 6"]) and any(s in t for s in ["silver", "zamac", "japanese", "european", "streets of japan", "themed", "anniversary", "celebrations", "legends"]):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["themed_6_pack"], price, scraped_mrp)

        if any(k in t for k in ["2-pack", "2 pack", "2-car pack", "pack of 2", "set of 2"]) and any(f in t for f in f1_keywords):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["f1_2_pack"], price, scraped_mrp)

        if any(k in t for k in ["2-pack", "2 pack", "2-car pack", "pack of 2", "2 cars", "set of 2"]) and any(p in t for p in ["car culture", "premium", "real riders", "unique pairings"]):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["car_culture_2_pack"], price, scraped_mrp)

        if "color reveal" in t or "colour reveal" in t:
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["color_reveal"], price, scraped_mrp)

        if any(k in t for k in ["diorama", "display set", "premium display", "collector set", "box set", "heritage gift pack"]):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["diorama_or_box_set"], price, scraped_mrp)

    elif brand == "Matchbox":
        if any(k in t for k in ["8 pack", "8-pack", "pack of 8", "9 pack", "9-pack", "pack of 9"]):
            return cap_with_scraped_mrp(MRP_RULES["Matchbox"]["8_or_9_pack"], price, scraped_mrp)
        if any(k in t for k in ["5 pack", "5-pack", "5-car pack", "pack of 5", "5 pcs", "gift pack", "set of 5"]):
            return cap_with_scraped_mrp(MRP_RULES["Matchbox"]["5_pack"], price, scraped_mrp)
        if "convoy" in t:
            return cap_with_scraped_mrp(MRP_RULES["Matchbox"]["convoy"], price, scraped_mrp)
        if any(k in t for k in ["working rigs", "working rig", "adventure pack"]):
            return cap_with_scraped_mrp(MRP_RULES["Matchbox"]["working_rigs"], price, scraped_mrp)

    elif brand == "Majorette":
        if any(k in t for k in ["5 piece", "5-piece", "5 pack", "5-pack", "5-car pack", "pack of 5", "5 pcs", "gift pack", "gift set", "car set", "set of 5"]):
            if any(ed in t for ed in ["porsche", "black edition", "vintage", "jdm", "racing", "mercedes", "lamborghini", "deluxe", "premium", "limited", "color changer"]):
                return cap_with_scraped_mrp(MRP_RULES["Majorette"]["edition_5_pack"], price, scraped_mrp)
            else:
                return cap_with_scraped_mrp(MRP_RULES["Majorette"]["street_5_pack"], price, scraped_mrp)
        if any(k in t for k in ["trailer", "isuzu", "transporter", "hauler", "truck"]):
            return cap_with_scraped_mrp(MRP_RULES["Majorette"]["trailer_set"], price, scraped_mrp)

    # --- 2. GENERAL MULTI-PACK MULTIPLIER LOOP ---
    for num_cars in range(2, 21):
        if (f"pack of {num_cars}" in t or f"{num_cars} pack" in t or f"{num_cars}-pack" in t or f"{num_cars}-car pack" in t or
                f"{num_cars} car pack" in t or f"{num_cars}pc" in t or f"{num_cars} pcs" in t or f"set of {num_cars}" in t or
                f"set {num_cars}" in t or f"{num_cars} cars" in t or f"{num_cars}-car" in t or f"{num_cars} piece" in t or f"{num_cars}-piece" in t):
            if brand == "Hot Wheels":
                if any(k in t for k in f1_keywords) and "premium" in t and not any(cc in t for cc in car_culture_and_boulevard_keywords):
                    per_car_mrp = MRP_RULES["Hot Wheels"]["f1_premium"]
                elif any(k in t for k in ["car culture", "boulevard", "premium", "pop culture", "real riders", "replica from popular movie"]):
                    per_car_mrp = MRP_RULES["Hot Wheels"]["premium_single"]
                elif any(k in t for k in [
                    "silver series", "silver label", "ultra hots", "neon speeders", "vintage racing",
                    "celebrations", "themed entertainment", "pantone", "performance trucks",
                    "streets of japan", "automotive", "fast & furious", "fast and furious"
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
            return cap_with_scraped_mrp(expected_mrp, price, scraped_mrp)

    # --- 3. SINGLE CARDS CLASSIFICATION ---
    if brand == "Hot Wheels":
        if "monster truck" in t:
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["monster_truck"], price, scraped_mrp)
        elif any(cc in t for cc in car_culture_and_boulevard_keywords):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["premium_single"], price, scraped_mrp)
        elif any(k in t for k in f1_keywords) and (
            any(p in t for p in ["premium", "car culture", "collectible", "driver", "real riders", "team", "one", "jkd", "hrv"])
            or (scraped_mrp and round(scraped_mrp) == 799)
        ):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["f1_premium"], price, scraped_mrp)
        elif any(k in t for k in [
            "real riders", "metal/metal", "metal metal", "premium toy car, truck or van"
        ]) or ("premium" in t and "silver" not in t):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["premium_single"], price, scraped_mrp)
        elif any(k in t for k in [
            "silver series", "silver label", "silver-label", "ultra hots", "neon speeders",
            "vintage racing", "vintage grand prix", "vintage 6", "vintage 7", "exclusive wheels",
            "themed packaging", "celebrations", "themed entertainment", "automotive",
            "pantone", "performance trucks", "streets of japan", "anniversary",
            "classic tv series batmobile", "batman forever", "batman classic", "arkham", "85th anniversary"
        ]):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["silver_series"], price, scraped_mrp)
        else:
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["mainline"], price, scraped_mrp)

    elif brand == "Matchbox":
        if "collectors" in t or "true grip" in t or "sky busters" in t:
            return cap_with_scraped_mrp(MRP_RULES["Matchbox"]["collectors"], price, scraped_mrp)
        elif "moving parts" in t:
            return cap_with_scraped_mrp(MRP_RULES["Matchbox"]["moving_parts"], price, scraped_mrp)
        else:
            return cap_with_scraped_mrp(MRP_RULES["Matchbox"]["mainline"], price, scraped_mrp)

    elif brand == "Majorette":
        if any(k in t for k in ["tune-ups", "tune ups", "tune up"]):
            expected = MRP_RULES["Majorette"]["tune_ups"]
        elif any(k in t for k in ["deluxe", "collector box", "monster rockerz", "city bus", "city trailer", "aeroplane", "airplane", "airport", "aircraft", "boeing", "airbus", "a350", "a 350"]):
            expected = MRP_RULES["Majorette"]["deluxe"]
        elif any(k in t for k in [
            "premium", "racing", "vintage", "jdm", "castheads", "cast heads", "color changer",
            "colour changer", "showroom", "the originals", "volkswagen", "porsche", "mercedes-amg",
            "black edition", "limited edition", "japanese", "italian"
        ]):
            expected = MRP_RULES["Majorette"]["premium"]
        else:
            expected = MRP_RULES["Majorette"]["street"]
        return cap_with_scraped_mrp(expected, price, scraped_mrp)

    return False, None


def check_firstcry_live_stock_fast(product_url):
    """
    Ultra-fast (~0.15s) HTTP probe that bypasses FirstCry's 15-minute Search CDN cache.
    Accurately detects if an 'Out of Stock' search card has just restocked on the live PDP.
    """
    try:
        probe_url = f"{product_url}?_ts={int(time.time() * 1000)}"
        r = HTTP_SESSION.get(probe_url, timeout=4)
        if r.status_code != 200:
            return False, 0.0, None
        html_text = r.text

        # Check explicit JS/JSON out-of-stock flags in FirstCry's PDP source
        if re.search(r'"isOutOfStock"\s*:\s*true|"StockStatus"\s*:\s*"Out\s*of\s*Stock"|Currently\s+Out\s+of\s+Stock', html_text, re.I):
            return False, 0.0, None

        soup = BeautifulSoup(html_text, "html.parser")

        # Check if the Notify Me wrapper is actively shown (not hidden with display:none or dn/hide class)
        for el in soup.find_all(attrs={"id": re.compile(r"notify|oos|outofstock", re.I)}):
            style = (el.get("style") or "").lower().replace(" ", "")
            cls = " ".join(el.get("class") or []).lower()
            if "display:none" not in style and "dn" not in cls and "hide" not in cls:
                return False, 0.0, None

        # Verify 'ADD TO CART' button exists and its container is not display:none
        has_active_atc = False
        for el in soup.find_all(["div", "span", "button", "a"]):
            txt = (el.get_text(" ", strip=True) or "").upper()
            if txt == "ADD TO CART":
                # Walk up 3 parent levels to ensure none have display:none or 'dn'
                curr = el
                hidden = False
                for _ in range(4):
                    if not curr:
                        break
                    st = (curr.get("style") or "").lower().replace(" ", "")
                    cl = " ".join(curr.get("class") or []).lower().split()
                    if "display:none" in st or "dn" in cl or "hide" in cl or "d-none" in cl:
                        hidden = True
                        break
                    curr = curr.parent
                if not hidden:
                    has_active_atc = True
                    break

        if not has_active_atc:
            return False, 0.0, None

        body_text = soup.get_text(" ", strip=True)

        live_mrp = None
        mrp_m = re.search(r"MRP\s*[:\-]?\s*₹?\s*([1-9][0-9]{2,4}(?:\.[0-9]{1,2})?)", body_text, re.I)
        if mrp_m:
            live_mrp = float(mrp_m.group(1))

        live_price = 0.0
        club_m = re.search(r"Club\s*Price\s*[:₹Rs.\s]*([1-9][0-9]{1,4}(?:\.[0-9]{1,2})?)", body_text, re.I)
        sp_m = re.search(r"₹\s*([1-9][0-9]{1,4}(?:\.[0-9]{1,2})?)\s+MRP", body_text, re.I)
        if club_m:
            live_price = float(club_m.group(1))
            if live_mrp and live_price > live_mrp and live_price > 10000:
                live_price = round(live_price / 100.0)
        elif sp_m:
            live_price = float(sp_m.group(1))
        elif live_mrp:
            live_price = live_mrp

        if live_price > 40:
            return True, live_price, live_mrp
    except Exception:
        pass
    return False, 0.0, None


def extract_firstcry_pdp_details(context, product_url, fallback_price, expected_mrp):
    """
    Opens the FirstCry PDP and performs:
    - LAYER 0: Strict Live 'Add to Cart' DOM Visibility Check
    - LAYER 1: Exact MRP & Club Price extraction
    - LAYER 2: Coupon extraction (including BUYNOW / 30% OFF / 4EXTRA) & screenshot
    """
    pdp_page = context.new_page()
    coupon_code = None
    best_price = fallback_price
    pdp_mrp = expected_mrp
    screenshot_path = None
    is_buyable = True
    stock_left_str = None

    try:
        pdp_page.goto(product_url, timeout=30000, wait_until="domcontentloaded")
        time.sleep(1.3)

        # --- LAYER 0: STRICT DOM BUYABILITY VERIFICATION ---
        buyability_status = pdp_page.evaluate(
            """() => {
                function isVisible(el) {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 5 && rect.height > 5;
                }

                let hasVisibleAddToCart = false;
                let hasVisibleOutOfStock = false;

                const elements = document.querySelectorAll('button, a, div, span, p, input');
                for (const el of elements) {
                    if (!isVisible(el)) continue;
                    const txt = (el.innerText || el.value || '').trim().toUpperCase();
                    if (txt === 'ADD TO CART' || txt === 'BUY NOW') {
                        hasVisibleAddToCart = true;
                    }
                    if (
                        txt === 'NOTIFY ME' ||
                        txt === 'OUT OF STOCK' ||
                        txt === 'CURRENTLY OUT OF STOCK' ||
                        txt === 'SOLD OUT' ||
                        txt.includes('NOTIFY ME WHEN AVAILABLE')
                    ) {
                        if (el.children.length <= 2) {
                            hasVisibleOutOfStock = true;
                        }
                    }
                }
                return { hasVisibleAddToCart, hasVisibleOutOfStock };
            }"""
        )

        if buyability_status.get("hasVisibleOutOfStock") or not buyability_status.get("hasVisibleAddToCart"):
            print(f"   🛑 [Aborted Alert - PDP is Out of Stock / Notify Me]: {product_url}", flush=True)
            return False, None, best_price, pdp_mrp, None, None

        pdp_page.evaluate("window.scrollBy(0, 650)")
        time.sleep(0.7)

        pdp_text = pdp_page.locator("body").inner_text()

        # Check for low stock badge (e.g. "Only 2 Left" or "10 Left")
        left_m = re.search(r"\b(?:Only\s+)?([1-9][0-9]?)\s+Left\b", pdp_text, re.I)
        if left_m:
            stock_left_str = f"Only {left_m.group(1)} Left!"

        # --- LAYER 1: Extract Exact MRP Printed on FirstCry Product Page ---
        scraped_pdp_mrp = None

        mrp_el = pdp_page.locator(".pdp-mrp, .mrp-price, .prod-mrp, del, strike, [class*='mrp']").first
        if mrp_el.count() > 0:
            mrp_el_txt = mrp_el.inner_text() or ""
            m_dom = re.search(r"([1-9][0-9]{2,4}(?:\.[0-9]{1,2})?)", mrp_el_txt.replace(",", ""))
            if m_dom:
                scraped_pdp_mrp = round(float(m_dom.group(1)))

        if not scraped_pdp_mrp:
            pdp_mrp_match = re.search(
                r"MRP\s*[:\-]?\s*₹?\s*([1-9][0-9]{2,4}(?:\.[0-9]{1,2})?)|₹?\s*[1-9][0-9]{1,4}(?:\.[0-9]{1,2})?\s+MRP\s*[:\-]?\s*₹?\s*([1-9][0-9]{2,4})",
                pdp_text,
                re.I
            )
            if pdp_mrp_match:
                val = pdp_mrp_match.group(1) or pdp_mrp_match.group(2)
                scraped_pdp_mrp = round(float(val))

        if scraped_pdp_mrp and scraped_pdp_mrp >= 150:
            pdp_mrp = scraped_pdp_mrp

        club_p_match = re.search(r"Club\s*Price\s*[:₹Rs.\s]*([1-9][0-9]{1,4}(?:\.[0-9]{1,2})?)", pdp_text, re.I)
        if club_p_match:
            cp_val = float(club_p_match.group(1))
            if cp_val > pdp_mrp and cp_val > 10000:
                cp_val = round(cp_val / 100.0)
            if 40 < cp_val < best_price:
                best_price = round(cp_val)

        # --- LAYER 2: Extract Product Discount Coupon from 'Offers and Discounts' ---
        ignore_words = {
            "FIRSTCRY", "DETAILS", "APPLIED", "COUPON", "OFFERS", "AVAILABLE",
            "CHECKOUT", "SAVINGS", "DISCOUNT", "DISCOUNTS", "ELIGIBLE", "PRODUCTS",
            "MINIMUM", "SHIPPING", "DELIVERY", "PINCODE", "BENEFITS", "CASHBACK",
            "VALID", "TERMS", "CONDITIONS", "CLICK", "HERE", "SELECT", "COLOR",
            "COLOUR", "RETURN", "CHECK", "MEMB", "MEMBER", "MEMBERS", "FEDERAL",
            "CREDIT", "DEBIT", "CARDS", "INSTANT", "BANK", "FLAT", "EXTRA", "CLUB",
            "SHORTLIST", "ADDTOCART", "SHARE", "GIFT", "WRAP", "BESTSELLER",
            "GET50CT", "CASHBACK", "PAYTM", "MOBIKWIK", "AMAZON"
        }

        offers_found = []
        offers_block_match = re.search(
            r"Offers\s+and\s+Discounts(.*?)(?:Bank\s+Offers|Product\s+Description|Specifications|You\s+May\s+Also\s+Like|$)",
            pdp_text,
            re.I | re.S
        )
        offers_section_text = offers_block_match.group(1) if offers_block_match else ""

        if offers_section_text:
            offer_cards = re.split(r"T\s*&\s*C\s*>?", offers_section_text)
            for idx in range(len(offer_cards) - 1):
                card_header = offer_cards[idx]
                card_footer = offer_cards[idx + 1]

                if any(cb_word in card_header.lower() for cb_word in ["cashback", "club cash", "wallet", "paytm"]):
                    continue

                code_m = re.search(r"^\s*\n*\s*\b([A-Z0-9]{5,12})\b", card_footer)
                if code_m:
                    c = code_m.group(1).strip().upper()
                    if c not in ignore_words and not any(bad in c for bad in ["GET", "CASH", "PAY", "CB"]):
                        pcts_in_card = [float(p) for p in re.findall(r"(\d{1,2}(?:\.\d+)?)\s*%\s*OFF", card_header, re.I)]
                        card_pct = max(pcts_in_card) if pcts_in_card else 0.0
                        is_extra = "extra" in card_header.lower() or "add'l" in card_header.lower() or "additional" in card_header.lower()
                        offers_found.append((card_pct, c, is_extra))

            if not offers_found:
                sec_pcts = [float(p) for p in re.findall(r"(\d{1,2})\s*%\s*OFF", offers_section_text, re.I) if 4 <= float(p) <= 65]
                max_sec_pct = max(sec_pcts) if sec_pcts else 0.0
                for line in offers_section_text.splitlines():
                    clean_line = re.sub(r"[^A-Z0-9]", "", line.strip())
                    if 5 <= len(clean_line) <= 12 and clean_line == line.strip().upper().replace(" ", "") and clean_line not in ignore_words:
                        if not any(bad in clean_line for bad in ["GET", "CASH", "PAY", "CB"]):
                            if any(ch.isalpha() for ch in clean_line):
                                is_extra = "extra" in clean_line.lower()
                                offers_found.append((max_sec_pct, clean_line, is_extra))

        if offers_found:
            offers_found.sort(key=lambda x: x[0], reverse=True)
            best_pct, coupon_code, is_extra = offers_found[0]

            if is_extra and 1 <= best_pct <= 15:
                calculated_price = round(best_price * (1 - best_pct / 100.0))
                if 40 < calculated_price < best_price:
                    best_price = calculated_price
            elif 10 <= best_pct <= 65 and pdp_mrp:
                calculated_price = round(pdp_mrp * (1 - best_pct / 100.0))
                if 40 < calculated_price < best_price:
                    best_price = calculated_price

        pdp_page.evaluate("window.scrollTo(0, 0)")
        time.sleep(0.25)

        pdp_container = pdp_page.locator("div.prod-info-wrap, div.pdp-main, div.product-detail-sec, div.main_prod_sec").first
        screenshot_path = f"temp_fc_pdp_{int(time.time()*1000)}.png"
        if pdp_container.count() > 0:
            pdp_container.scroll_into_view_if_needed()
            time.sleep(0.25)
            pdp_container.screenshot(path=screenshot_path)
        else:
            pdp_page.screenshot(path=screenshot_path, clip={"x": 50, "y": 80, "width": 1000, "height": 580})

    except Exception as e:
        print(f"   ⚠️ FirstCry PDP check warning: {e}", flush=True)
    finally:
        pdp_page.close()

    return is_buyable, coupon_code, best_price, pdp_mrp, screenshot_path, stock_left_str


def normalize_and_clean_url(raw_url, domain_prefix="https://www.firstcry.com"):
    if not raw_url:
        return ""
    url = raw_url.strip()
    if url.startswith("//"):
        url = "https:" + url
    elif not url.startswith("http"):
        url = f"{domain_prefix.rstrip('/')}/{url.lstrip('/')}"
    url = re.sub(r"^https?://[^/]+//+", "https://", url)
    if "firstcry.com" in url:
        return url.split("?")[0]
    return url


def convert_to_affiliate_link(raw_url):
    if not raw_url or not raw_url.startswith("http"):
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


def send_telegram_deal(store, title, price, expected_mrp, link, image_path=None, coupon=None, stock_left=None):
    clean_title = html.escape(clean_product_title(title))
    safe_store = html.escape(store)

    if round(price) < round(expected_mrp):
        savings = round(expected_mrp - price)
        header_banner = f"🔥 <b>BELOW MRP DEAL (Save ₹{savings} | MRP: ₹{expected_mrp:.0f})</b> 🔥"
        mrp_status = "Below MRP"
    else:
        header_banner = f"✅ <b>AT MRP ALERT (MRP: ₹{expected_mrp:.0f})</b> ✅"
        mrp_status = "At MRP"

    final_link = convert_to_affiliate_link(link)
    formatted_link = f'<a href="{final_link}">🔗 link.firstcry</a>'

    caption_lines = [
        header_banner,
        "",
        f"🚨 <b>{clean_title} @ ₹{price:.0f} [{safe_store}]</b>",
        formatted_link
    ]

    if stock_left:
        caption_lines.append(f"⚡ <b>Stock:</b> {html.escape(stock_left)} — Grab Fast!")

    if coupon:
        safe_coupon = html.escape(str(coupon))
        caption_lines.append(f"\n📍 <b>Coupon:</b> <code>{safe_coupon}</code>")

    caption_lines.append(
        "\n💡 Showing \"Out of Stock\"? Just open the link, head to your "
        "Profile → Recently Viewed, and click Add to Cart from there!"
    )
    caption_lines.append("\n🚨 Grab it fast!")
    caption = "\n".join(caption_lines)

    try:
        if image_path and os.path.exists(image_path):
            api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            with open(image_path, "rb") as img_file:
                files = {"photo": img_file}
                data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}
                r = requests.post(api_url, data=data, files=files, timeout=20)
                if r.status_code == 200:
                    print(f"   📸 Sent Screenshot [{store}]: {clean_title[:35]} @ ₹{price:.0f} ({mrp_status} | Stock: {stock_left} | Coupon: {coupon})", flush=True)
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
            print(f"   ✅ Sent Text [{store}]: {clean_title[:35]} @ ₹{price:.0f} ({mrp_status} | Stock: {stock_left} | Coupon: {coupon})", flush=True)
    except Exception as e:
        print(f"   ⚠️ Telegram connection error: {e}", flush=True)


def scrape_deals():
    print("🚀 Starting High-Speed FirstCry Mainline & Grail Sniper...", flush=True)
    seen_deals = load_seen_deals()
    new_seen_count = 0
    cache_dirty = False
    probed_oos_urls_this_run = set()

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
            viewport={"width": 1366, "height": 900},
            locale="en-IN",
            timezone_id="Asia/Kolkata"
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        for target in TARGET_URLS:
            store = target["store"]
            url = target["url"]
            num_scrolls = target.get("scrolls", 3)
            print(f"\n-> Scanning {store} ({url})...", flush=True)

            try:
                page.goto(url, timeout=45000, wait_until="domcontentloaded")
                time.sleep(1.6)
                for _ in range(num_scrolls):
                    page.evaluate("window.scrollBy(0, 1600)")
                    time.sleep(0.65)

                product_elements = page.locator("div.list_block")
                if product_elements.count() == 0:
                    product_elements = page.locator("div.lft.list_cel, li.child_block")
                count = product_elements.count()
                print(f"   Found {count} product cards on FirstCry.", flush=True)

                oos_candidates_to_probe = []
                in_stock_candidates = []

                for i in range(count):
                    try:
                        card = product_elements.nth(i)
                        card_text = card.inner_text()
                        if not any(b in card_text.lower() for b in ["hot wheels", "matchbox", "majorette"]):
                            continue

                        link_el = card.locator("a[href*='hot-wheels'], a[href*='matchbox'], a[href*='majorette'], a[href*='product-detail']").first
                        if link_el.count() == 0:
                            link_el = card.locator("a[href]").first
                        if link_el.count() == 0:
                            continue
                        raw_href = link_el.get_attribute("href")
                        clean_link = normalize_and_clean_url(raw_href, "https://www.firstcry.com")

                        fc_id_match = re.search(r"/(\d{5,12})/product-detail", clean_link)
                        if fc_id_match:
                            deal_key = f"fc_{fc_id_match.group(1)}"
                        else:
                            deal_key = clean_link.split("?")[0].rstrip("/")

                        title_text = link_el.get_attribute("title") or ""
                        if not title_text:
                            title_el = card.locator(".li_txt1, .p_list_text, .list_title").first
                            title_text = title_el.inner_text().strip() if title_el.count() > 0 else card_text.split("\n")[0]

                        title_key = f"fc_title_{re.sub(r'[^a-z0-9]', '', clean_product_title(title_text).lower())[:65]}"

                        # Quick pre-filter so we never waste HTTP probes on tracks/stands/non-diecast
                        brand = determine_brand(title_text)
                        _, prelim_mrp = classify_and_check_mrp(brand, title_text, 150)
                        if not prelim_mrp:
                            continue

                        card_left_m = re.search(r"\b([1-9][0-9]?)\s*Left\b", card_text, re.I)
                        card_stock_left = f"Only {card_left_m.group(1)} Left!" if card_left_m else None

                        is_card_oos = any(oos in card_text.lower() for oos in ["notify me", "out of stock", "sold out"])
                        if is_card_oos:
                            if clean_link not in probed_oos_urls_this_run:
                                probed_oos_urls_this_run.add(clean_link)
                                oos_candidates_to_probe.append({
                                    "index": i,
                                    "clean_link": clean_link,
                                    "deal_key": deal_key,
                                    "title_key": title_key,
                                    "title_text": title_text,
                                    "card_stock_left": card_stock_left
                                })
                            else:
                                if deal_key in seen_deals or title_key in seen_deals:
                                    seen_deals.discard(deal_key)
                                    seen_deals.discard(title_key)
                                    cache_dirty = True
                            continue

                        if not deal_key or deal_key in seen_deals or title_key in seen_deals:
                            continue

                        in_stock_candidates.append({
                            "index": i,
                            "card_text": card_text,
                            "clean_link": clean_link,
                            "deal_key": deal_key,
                            "title_key": title_key,
                            "title_text": title_text,
                            "card_stock_left": card_stock_left,
                            "live_price": 0.0,
                            "live_mrp": None
                        })
                    except Exception:
                        continue

                # --- PARALLEL 10-THREAD LIVE PDP SNIPER FOR OUT-OF-STOCK SEARCH CARDS ---
                if oos_candidates_to_probe:
                    print(f"   ⚡ Parallel-probing {len(oos_candidates_to_probe)} OOS cards for un-cached live restocks...", flush=True)
                    with ThreadPoolExecutor(max_workers=10) as executor:
                        future_to_cand = {
                            executor.submit(check_firstcry_live_stock_fast, c["clean_link"]): c
                            for c in oos_candidates_to_probe
                        }
                        for future in as_completed(future_to_cand):
                            cand = future_to_cand[future]
                            try:
                                is_live, live_p, live_m = future.result()
                                if is_live:
                                    print(f"   🎯 [UN-CACHED LIVE RESTOCK SNIPED!] {clean_product_title(cand['title_text'])[:45]}", flush=True)
                                    if cand["deal_key"] not in seen_deals and cand["title_key"] not in seen_deals:
                                        in_stock_candidates.append({
                                            "index": cand["index"],
                                            "card_text": "",
                                            "clean_link": cand["clean_link"],
                                            "deal_key": cand["deal_key"],
                                            "title_key": cand["title_key"],
                                            "title_text": cand["title_text"],
                                            "card_stock_left": cand["card_stock_left"],
                                            "live_price": live_p,
                                            "live_mrp": live_m
                                        })
                                else:
                                    if cand["deal_key"] in seen_deals or cand["title_key"] in seen_deals:
                                        seen_deals.discard(cand["deal_key"])
                                        seen_deals.discard(cand["title_key"])
                                        cache_dirty = True
                                        print(f"   🔄 [FirstCry OOS] Cleared from cache for future restock: {clean_product_title(cand['title_text'])[:35]}", flush=True)
                            except Exception:
                                pass

                # --- PROCESS ALL VERIFIED IN-STOCK CANDIDATES ---
                for item in in_stock_candidates:
                    try:
                        i = item["index"]
                        card = product_elements.nth(i)
                        card_text = item["card_text"]
                        clean_link = item["clean_link"]
                        deal_key = item["deal_key"]
                        title_key = item["title_key"]
                        title_text = item["title_text"]
                        card_stock_left = item["card_stock_left"]
                        price = item["live_price"]
                        scraped_mrp = item["live_mrp"]

                        if deal_key in seen_deals or title_key in seen_deals:
                            continue

                        if price <= 40:
                            sp_el = card.locator(".r1, .rupee, .club_price, .sp_amt").first
                            mrp_card_el = card.locator(".r2, del, strike, .mrp_amt").first
                            if mrp_card_el.count() > 0:
                                m_txt = mrp_card_el.inner_text() or ""
                                m_val = re.search(r"([1-9][0-9]{2,4}(?:\.[0-9]{1,2})?)", m_txt.replace(",", ""))
                                if m_val:
                                    scraped_mrp = float(m_val.group(1))

                            if not scraped_mrp:
                                mrp_search = re.search(r"MRP\.?\s*[:\-]?\s*₹?\s*([1-9][0-9]{2,4}(?:\.[0-9]{1,2})?)", card_text, re.I)
                                if mrp_search:
                                    scraped_mrp = float(mrp_search.group(1).replace(",", ""))

                            pm_discount = re.search(
                                r"₹?\s*([1-9][0-9]{1,4}(?:\.[0-9]{1,2})?)\s*[\r\n\s]+₹?\s*([1-9][0-9]{2,4}(?:\.[0-9]{1,2})?)\s*[\r\n\s]*\(\d+%\s*Off\)",
                                card_text,
                                re.I
                            )
                            pm_club = re.search(r"Club\s*Price\s*[:₹Rs.\s]*([1-9][0-9]{1,4}(?:\.[0-9]{1,2})?)|([1-9][0-9]{1,4}(?:\.[0-9]{1,2})?)\s*Club\s*Price", card_text, re.I)
                            pm_plain = re.search(r"₹\s*([1-9][0-9]{1,4}(?:\.[0-9]{1,2})?)", card_text)

                            if pm_discount:
                                price = float(pm_discount.group(1))
                                if not scraped_mrp:
                                    scraped_mrp = float(pm_discount.group(2))
                            elif pm_club:
                                price = float(pm_club.group(1) or pm_club.group(2))
                            elif sp_el.count() > 0:
                                sp_txt = sp_el.inner_text() or ""
                                sp_m = re.search(r"([1-9][0-9]{1,4}(?:\.[0-9]{1,2})?)", sp_txt.replace(",", ""))
                                if sp_m:
                                    price = float(sp_m.group(1))
                            elif pm_plain:
                                price = float(pm_plain.group(1))

                        if price <= 40:
                            continue

                        brand = determine_brand(title_text)
                        _, expected_mrp = classify_and_check_mrp(brand, title_text, price, scraped_mrp=scraped_mrp)
                        if not expected_mrp:
                            continue

                        is_buyable, coupon, final_price, pdp_mrp, pdp_img_path, pdp_stock_left = extract_firstcry_pdp_details(
                            context, clean_link, price, expected_mrp
                        )

                        if not is_buyable:
                            if pdp_img_path and os.path.exists(pdp_img_path):
                                os.remove(pdp_img_path)
                            if deal_key in seen_deals or title_key in seen_deals:
                                seen_deals.discard(deal_key)
                                seen_deals.discard(title_key)
                                cache_dirty = True
                            continue

                        if pdp_mrp and pdp_mrp >= 150:
                            expected_mrp = pdp_mrp

                        if final_price > expected_mrp:
                            if pdp_img_path and os.path.exists(pdp_img_path):
                                os.remove(pdp_img_path)
                            continue

                        if pdp_img_path and os.path.exists(pdp_img_path):
                            img_path = pdp_img_path
                        else:
                            img_path = f"temp_fc_{i}.png"
                            card.screenshot(path=img_path)

                        seen_deals.add(deal_key)
                        seen_deals.add(title_key)
                        save_seen_deals(seen_deals)

                        final_stock_left = pdp_stock_left or card_stock_left
                        send_telegram_deal(
                            store, title_text, final_price, expected_mrp, clean_link,
                            image_path=img_path, coupon=coupon, stock_left=final_stock_left
                        )

                        if os.path.exists(img_path):
                            os.remove(img_path)

                        new_seen_count += 1
                        time.sleep(0.5)
                    except Exception:
                        continue

            except Exception as e:
                print(f"⚠️ Error scraping {store}: {e}", flush=True)

        browser.close()

    if cache_dirty or new_seen_count > 0:
        save_seen_deals(seen_deals)
    print(f"\n🏁 FirstCry scan complete. Added {new_seen_count} new/restocked deals to cache.", flush=True)


if __name__ == "__main__":
    scrape_deals()
