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

# Major FirstCry fulfillment hubs to defeat Global Phantom Stock (South Hub, West Hub, North Hub)
PRIMARY_PINCODE = "560001"
FALLBACK_PINCODE = "400001"
NORTH_PINCODE = "110001"

UNSERVICEABLE_REGEX = re.compile(
    r"cannot\s+be\s+delivered|not\s+deliverable|undeliverable|"
    r"currently\s+out\s+of\s+stock\s+in\s+your\s+area|not\s+serviceable",
    re.I
)

# Persistent 25-connection pool for ultra-fast parallel FirstCry PDP probes
HTTP_SESSION = requests.Session()
adapter = requests.adapters.HTTPAdapter(pool_connections=25, pool_maxsize=25)
HTTP_SESSION.mount("https://", adapter)
HTTP_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache"
})
HTTP_SESSION.cookies.update({
    "fc_pincode": PRIMARY_PINCODE,
    "pincode": PRIMARY_PINCODE,
    "usr_pincode": PRIMARY_PINCODE,
    "DelPincode": PRIMARY_PINCODE
})

# Official Indian Retail MRP Rules (Monster Trucks, Bikes, Planes & Helicopters excluded)
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

# VIP PRIORITY KEYWORDS: Any card matching these gets evaluated, probed & alerted FIRST!
PRIORITY_KEYWORDS = [
    "audi", "mclaren", "ferrari", "lamborghini", "bmw", "bwt alpine", "alpine",
    "stake f1", "kick sauber", "sauber", "moneygram haas", "haas", "mercedes",
    "plymouth", "buick", "porsche", "toyota", "mustang", "aston martin",
    "vintage club", "silver series", "f&f", "fast and furious", "fast & furious",
    "corvette", "forza", "segundo", "el segundo", "vintage racing", "pagani",
    "chevy", "chevrolet", "mazda", "honda", "lincoln", "mitsubishi", "road trip",
    "oracle red bull", "red bull", "dodge", "ford", "lotus", "subaru", "pontiac",
    "williams", "formula", "f1", "silvia", "nissan", "skyline", "supra", "gtr", "gt-r",
    "deutschland"
]

# DIRECT VIP PRODUCT URLS: Verified via real Playwright DOM + Color Variant expansion before search pages!
VIP_DIRECT_URLS = [
    {
        "title": "Hot Wheels Ferrari Free Wheel Model With Smooth Rolling Wheels Diecast Toy Cars Pack of 5 - Multicolor",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-ferrari-free-wheel-model-with-smooth-rolling-wheels-diecast-toy-cars-pack-of-5-multicolor/23033009/product-detail"
    },
    {
        "title": "Hot Wheels Premium BWT Alpine Formula One Team F1 Team Car",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-premium-bwt-alpine-formula-one-team-f1-team-car/19363241/product-detail"
    },
]

# SPLIT & OPTIMIZED TARGET URLS (Includes Deutschland, F1, Ferrari, Williams, Alpine, Premium & Silver Series)
TARGET_URLS = [
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20deutschland", "scrolls": 3},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20formula", "scrolls": 4},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20f1", "scrolls": 3},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20ferrari", "scrolls": 3},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20williams", "scrolls": 2},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20alpine", "scrolls": 2},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20premium", "scrolls": 6},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20silver", "scrolls": 4},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20250", "scrolls": 5},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20die%20cast%20free%20wheel", "scrolls": 5},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels", "scrolls": 5},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%205%20pack", "scrolls": 3},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=majorette", "scrolls": 4},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=matchbox", "scrolls": 4},
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


def is_priority_casting(title):
    t = title.lower()
    return any(pk in t for pk in PRIORITY_KEYWORDS)


def cap_with_scraped_mrp(expected_mrp, price, scraped_mrp):
    if scraped_mrp and 150 <= scraped_mrp < expected_mrp:
        expected_mrp = round(scraped_mrp)
    return price <= expected_mrp, expected_mrp


def classify_and_check_mrp(brand, title, price, scraped_mrp=None):
    t = re.sub(r'[®™©–—/|]', ' ', title.lower())
    t = re.sub(r'\s+', ' ', t).strip()

    # 1. STRICT BLOCK: MONSTER TRUCKS, BIKES, PLANES & HELICOPTERS
    if re.search(
        r'\b(monster\s*truck|monster\s*trucks|big\s*rig|big\s*rigs|monster\s*mover|monster\s*rockerz|'
        r'rhinomite|mega\s*wrex|demo\s*derby|monster\s*jam|'
        r'bike|bikes|motorcycle|motorbike|ducati|harley|vespa|scrambler|superbike|dirt\s*bike|motocross|bmx|scooter|'
        r'plane|planes|aeroplane|airplane|aircraft|fighter\s*jet|helicopter|chopper|sky\s*busters|skybusters|'
        r'airbus|boeing|propeller|aviation|biplane|seaplane|water\s*bomber)\b',
        t,
        re.I
    ):
        return False, None

    # 2. STRICT ACCESSORY & NON-DIECAST FILTER
    if re.search(r'\b(stand|stands|protector|protectors|acrylic|keychain|magnet|organizer|storage|shelf|rack|mat)\b', t, re.I):
        return False, None

    if any(skip in t for skip in [
        "display stand", "stand for", "cars not included", "car not included", "acrylic case",
        "storage box", "wall mount", "case of", "collector case", "blind box", "track",
        "playset", "play set", "petrol station", "garage", "carry case", "launcher",
        "lapel pin", "rotiform", "1:36", "1:43", "1:24", "1:18", "1/43", "1/24", "1/18",
        "red line club", "rlc", "elite 64", "conventions", "skate", "fingerboard",
        "rc ", "remote control", "puzzle", "book", "sticker", "t-shirt", "bag"
    ]):
        return False, None

    if scraped_mrp and scraped_mrp >= 150:
        return price <= scraped_mrp, round(scraped_mrp)

    if brand == "Hot Wheels":
        if re.search(r'\b([1-9][0-9]?|1[0-9]{2}|2[0-4][0-9]|250)\s*/\s*250\b', title):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["mainline"], price, scraped_mrp)
        if re.search(r'\b([1-9]|10)\s*/\s*(10|5)\b', title):
            if not any(s in t for s in ["silver series", "silver label", "car culture", "pop culture", "boulevard", "real riders", "premium", "ultra hots", "neon speeders", "pantone", "deutschland"]):
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
        "deutschland", "deutschland design", "forza horizon", "nismo 270r", "kool kombi",
        "mattel 80th", "replica from popular movie", "road trip"
    ]

    if brand == "Hot Wheels":
        if any(k in t for k in ["team transport", "kousoku", "fleet flyer", "retro rig", "aero lift", "sakura sprinter", "euro hauler", "carry on", "transport hauler"]):
            return price <= MRP_RULES["Hot Wheels"]["team_transport"], MRP_RULES["Hot Wheels"]["team_transport"]

        if any(k in t for k in ["super rig", "super rigs", "heavy doody", "hauling rig", "transporter vehicle with 1"]):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["super_rig"], price, scraped_mrp)

        if any(k in t for k in ["car culture", "premium bundle", "collector bundle", "real riders", "deutschland"]) and any(p in t for p in ["5-pack", "5 pack", "5-car pack", "5 car pack", "5 cars", "bundle", "set of 5", "5 1:64"]):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["premium_5_pack"], price, scraped_mrp)

        if any(k in t for k in ["5 pack", "5-pack", "5-car pack", "5 car pack", "pack of 5", "5 pcs", "gift pack", "giftpack", "set of 5", "set of 5 toy cars"]) and "silver" not in t and "car culture" not in t and "real riders" not in t and "deutschland" not in t:
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["5_pack"], price, scraped_mrp)

        if any(k in t for k in ["6-pack", "6 pack", "6-car pack", "pack of 6", "6 cars", "6pc", "6 pcs", "set of 6"]) and any(s in t for s in ["silver", "zamac", "japanese", "european", "streets of japan", "themed", "anniversary", "celebrations", "legends"]):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["themed_6_pack"], price, scraped_mrp)

        if any(k in t for k in ["2-pack", "2 pack", "2-car pack", "pack of 2", "set of 2"]) and any(f in t for f in f1_keywords):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["f1_2_pack"], price, scraped_mrp)

        if any(k in t for k in ["2-pack", "2 pack", "2-car pack", "pack of 2", "2 cars", "set of 2"]) and any(p in t for p in ["car culture", "premium", "real riders", "unique pairings", "deutschland"]):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["car_culture_2_pack"], price, scraped_mrp)

        if "color reveal" in t or "colour reveal" in t:
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["color_reveal"], price, scraped_mrp)

        if any(k in t for k in ["diorama", "display set", "premium display", "collector set", "box set", "heritage gift pack", "container set"]):
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

    for num_cars in range(2, 21):
        if (f"pack of {num_cars}" in t or f"{num_cars} pack" in t or f"{num_cars}-pack" in t or f"{num_cars}-car pack" in t or
                f"{num_cars} car pack" in t or f"{num_cars}pc" in t or f"{num_cars} pcs" in t or f"set of {num_cars}" in t or
                f"set {num_cars}" in t or f"{num_cars} cars" in t or f"{num_cars}-car" in t or f"{num_cars} piece" in t or f"{num_cars}-piece" in t):
            if brand == "Hot Wheels":
                if any(k in t for k in f1_keywords) and "premium" in t and not any(cc in t for cc in car_culture_and_boulevard_keywords):
                    per_car_mrp = MRP_RULES["Hot Wheels"]["f1_premium"]
                elif any(k in t for k in ["car culture", "boulevard", "premium", "pop culture", "real riders", "replica from popular movie", "road trip", "deutschland"]):
                    per_car_mrp = MRP_RULES["Hot Wheels"]["premium_single"]
                elif any(k in t for k in [
                    "silver series", "silver label", "ultra hots", "neon speeders", "vintage racing",
                    "vintage club", "celebrations", "themed entertainment", "pantone", "performance trucks",
                    "streets of japan", "automotive", "fast & furious", "fast and furious", "f&f", "forza"
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

    if brand == "Hot Wheels":
        if any(cc in t for cc in car_culture_and_boulevard_keywords):
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
            "vintage racing", "vintage club", "vintage grand prix", "vintage 6", "vintage 7", "exclusive wheels",
            "themed packaging", "celebrations", "themed entertainment", "automotive",
            "pantone", "performance trucks", "streets of japan", "anniversary",
            "classic tv series batmobile", "batman forever", "batman classic", "arkham", "85th anniversary",
            "fast & furious", "fast and furious", "f&f"
        ]):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["silver_series"], price, scraped_mrp)
        else:
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["mainline"], price, scraped_mrp)

    elif brand == "Matchbox":
        if "collectors" in t or "true grip" in t:
            return cap_with_scraped_mrp(MRP_RULES["Matchbox"]["collectors"], price, scraped_mrp)
        elif "moving parts" in t:
            return cap_with_scraped_mrp(MRP_RULES["Matchbox"]["moving_parts"], price, scraped_mrp)
        else:
            return cap_with_scraped_mrp(MRP_RULES["Matchbox"]["mainline"], price, scraped_mrp)

    elif brand == "Majorette":
        if any(k in t for k in ["tune-ups", "tune ups", "tune up"]):
            expected = MRP_RULES["Majorette"]["tune_ups"]
        elif any(k in t for k in ["deluxe", "collector box", "city bus", "city trailer"]):
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
    Ultra-fast (~0.12s) HTTP probe that isolates the primary product's stock state
    without getting tricked by sibling Color Swatch OOS JSON or hidden Notify Me modals.
    """
    try:
        fc_id_m = re.search(r"/(\d{5,12})/product-detail", product_url)
        pid = fc_id_m.group(1) if fc_id_m else None

        probe_url = f"{product_url}?_ts={int(time.time() * 1000)}"
        r = HTTP_SESSION.get(probe_url, timeout=3.2)
        if r.status_code != 200:
            return False, 0.0, None
        html_text = r.text

        if "ADD TO CART" not in html_text.upper() and "GO TO CART" not in html_text.upper():
            return False, 0.0, None

        # 1. Check product-specific JSON state if pid is present (avoids sibling color swatch false negatives)
        if pid:
            pid_oos_pattern = re.compile(
                rf'"{pid}"[^{{}}]*?(?:"isOutOfStock"\s*:\s*true|"StockStatus"\s*:\s*"Out\s*of\s*Stock"|'
                rf'"InStock"\s*:\s*false|"SoldOut"\s*:\s*"Y")',
                re.I | re.S
            )
            if pid_oos_pattern.search(html_text):
                return False, 0.0, None

        # 2. Check Schema.org / OpenGraph availability for the main PDP
        if re.search(r'schema\.org/OutOfStock|"availability"\s*:\s*"https?://schema\.org/OutOfStock"', html_text, re.I):
            return False, 0.0, None

        soup = BeautifulSoup(html_text, "html.parser")

        # 3. Check if the primary Add-to-Cart wrapper is explicitly hidden via inline style/class
        has_active_atc = False
        for el in soup.find_all(["div", "span", "button", "a"]):
            txt = (el.get_text(" ", strip=True) or "").upper()
            if txt in ("ADD TO CART", "GO TO CART"):
                curr = el
                hidden = False
                for _ in range(5):
                    if not curr:
                        break
                    st = (curr.get("style") or "").lower().replace(" ", "")
                    cl = " ".join(curr.get("class") or []).lower().split()
                    if "display:none" in st or "visibility:hidden" in st or "dn" in cl or "hide" in cl or "d-none" in cl:
                        hidden = True
                        break
                    curr = curr.parent
                if not hidden:
                    has_active_atc = True
                    break

        if not has_active_atc:
            return False, 0.0, None

        # 4. Check if primary OOS banner (not popup modal) is explicitly shown via inline style
        for el in soup.find_all(["div", "span", "p", "button"]):
            txt = (el.get_text(" ", strip=True) or "").upper()
            if txt in ("CURRENTLY OUT OF STOCK", "SOLD OUT"):
                el_id = (el.get("id") or "").lower()
                el_cls = " ".join(el.get("class") or []).lower()
                if "popup" in el_id or "modal" in el_id or "popup" in el_cls or "modal" in el_cls:
                    continue
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
                    return False, 0.0, None

        body_text = soup.get_text(" ", strip=True)
        if UNSERVICEABLE_REGEX.search(body_text):
            return False, 0.0, None

        live_mrp = None
        mrp_m = re.search(r"MRP\s*[:\-]?\s*₹?\s*([1-9][0-9]{2,4}(?:\.[0-9]{1,2})?)", body_text, re.I)
        if mrp_m:
            live_mrp = float(mrp_m.group(1))

        live_price = 0.0
        club_m = re.search(r"Club\s*(?:Price)?\s*[:₹Rs.\s]*([1-9][0-9]{1,4}(?:\.[0-9]{1,2})?)", body_text, re.I)
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


def verify_pdp_pincode_serviceability(pdp_page, product_url):
    """
    Tests pincode deliverability directly on the FirstCry PDP DOM across South, West, and North hubs.
    Returns (is_deliverable, is_phantom_stock).
    """
    for test_pin in [PRIMARY_PINCODE, FALLBACK_PINCODE, NORTH_PINCODE]:
        try:
            pin_result = pdp_page.evaluate(
                """(pin) => {
                    const input = document.querySelector(
                        "input#nonlpincode, input#pincode, input[placeholder*='Pin'], input[placeholder*='pincode'], input[name*='pincode'], input.pincode-input"
                    );
                    if (input && input.offsetParent !== null) {
                        input.value = '';
                        input.focus();
                        input.value = pin;
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        const candidates = document.querySelectorAll("span, button, a, div, input[type='button']");
                        for (const btn of candidates) {
                            const t = (btn.innerText || btn.value || '').trim().toUpperCase();
                            if ((t === 'CHECK' || t === 'APPLY' || t === 'CHANGE') && btn.offsetParent !== null) {
                                btn.click();
                                return 'CLICKED';
                            }
                        }
                    }
                    return 'NO_INPUT';
                }""",
                test_pin
            )

            if pin_result == "CLICKED":
                time.sleep(0.9)

            has_unserviceable_err = pdp_page.evaluate(
                """() => {
                    const errSelectors = [
                        '#pin_err', '.pin-err', '.pincode-error', '.err_msg',
                        '[id*="pin"][id*="err"]', '[class*="undeliverable"]', '[class*="not-deliverable"]'
                    ];
                    for (const sel of errSelectors) {
                        const el = document.querySelector(sel);
                        if (el && el.offsetParent !== null && el.innerText.trim().length > 0) {
                            const txt = el.innerText.toLowerCase();
                            if (txt.includes('cannot be delivered') || txt.includes('not deliverable') || txt.includes('undeliverable') || txt.includes('out of stock') || txt.includes('not serviceable')) {
                                return true;
                            }
                        }
                    }
                    const bodyTxt = document.body.innerText.toLowerCase();
                    if (
                        bodyTxt.includes('cannot be delivered to') ||
                        bodyTxt.includes('not deliverable to') ||
                        bodyTxt.includes('product is undeliverable') ||
                        bodyTxt.includes('currently out of stock in your area')
                    ) {
                        return true;
                    }
                    return false;
                }"""
            )

            if not has_unserviceable_err:
                return True, False

        except Exception:
            return True, False

    print(f"   🚫 [PHANTOM STOCK BLOCKED - Undeliverable to {PRIMARY_PINCODE}, {FALLBACK_PINCODE} & {NORTH_PINCODE}]: {product_url}", flush=True)
    return False, True


def extract_firstcry_pdp_details(context, product_url, fallback_price, expected_mrp, discover_variants=True):
    pdp_page = context.new_page()
    coupon_code = None
    best_price = fallback_price
    pdp_mrp = expected_mrp
    screenshot_path = None
    is_buyable = True
    is_phantom = False
    stock_left_str = None
    sibling_urls = []
    scraped_title = None

    try:
        pdp_page.goto(product_url, timeout=25000, wait_until="domcontentloaded")
        time.sleep(1.0)

        # Extract true page title and any Color Swatch variant URLs on the page
        page_meta = pdp_page.evaluate(
            """() => {
                const h1 = document.querySelector('h1, #prod_name, .prod-title, .pdp-title');
                const title = h1 ? h1.innerText.trim() : document.title.split(' Online')[0].replace(/^Buy\\s+/i, '').trim();
                const variants = [];
                document.querySelectorAll('a[href*="product-detail"], [data-url*="product-detail"], [onclick*="product-detail"]').forEach(el => {
                    let u = el.getAttribute('href') || el.getAttribute('data-url') || '';
                    if (!u) {
                        const oc = el.getAttribute('onclick') || '';
                        const m = oc.match(/https?:\\/\\/[^'"]+product-detail|\\/[^'"]+product-detail/);
                        if (m) u = m[0];
                    }
                    if (u && (el.closest('[class*="color"], [id*="color"], [class*="swatch"], [class*="variant"], .color-sec, .clr_sec') || u.includes('pack-of-5'))) {
                        variants.push(u);
                    }
                });
                return { title, variants };
            }"""
        )
        scraped_title = page_meta.get("title")
        if discover_variants:
            sibling_urls = page_meta.get("variants") or []

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
                    if (txt === 'ADD TO CART' || txt === 'GO TO CART' || txt === 'BUY NOW') {
                        hasVisibleAddToCart = true;
                    }
                    if (
                        txt === 'NOTIFY ME' ||
                        txt === 'OUT OF STOCK' ||
                        txt === 'CURRENTLY OUT OF STOCK' ||
                        txt === 'SOLD OUT' ||
                        txt === 'UNDELIVERABLE' ||
                        txt.includes('NOTIFY ME WHEN AVAILABLE') ||
                        txt.includes('CANNOT BE DELIVERED')
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
            return False, False, None, best_price, pdp_mrp, None, None, scraped_title, sibling_urls

        # Verify Pincode Serviceability to block Global Phantom Stock (e.g., 5 Left but Undeliverable everywhere)
        is_deliverable, is_phantom = verify_pdp_pincode_serviceability(pdp_page, product_url)
        if not is_deliverable:
            return False, is_phantom, None, best_price, pdp_mrp, None, None, scraped_title, sibling_urls

        pdp_page.evaluate("window.scrollBy(0, 650)")
        time.sleep(0.5)

        pdp_text = pdp_page.locator("body").inner_text()

        left_m = re.search(r"\b(?:Only\s+)?([1-9][0-9]?)\s+Left\b", pdp_text, re.I)
        if left_m:
            stock_left_str = f"Only {left_m.group(1)} Left!"

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

        sp_match = re.search(r"₹\s*([1-9][0-9]{1,4}(?:\.[0-9]{1,2})?)\s*MRP", pdp_text, re.I)
        if sp_match:
            sp_val = float(sp_match.group(1))
            if sp_val > pdp_mrp and sp_val > 10000:
                sp_val = round(sp_val / 100.0)
            if 40 < sp_val and (best_price <= 40 or sp_val < best_price):
                best_price = round(sp_val)

        club_p_match = re.search(r"Club\s*(?:Price)?\s*[:₹Rs.\s]*([1-9][0-9]{1,4}(?:\.[0-9]{1,2})?)", pdp_text, re.I)
        if club_p_match:
            cp_val = float(club_p_match.group(1))
            if cp_val > pdp_mrp and cp_val > 10000:
                cp_val = round(cp_val / 100.0)
            if 40 < cp_val and (best_price <= 40 or cp_val < best_price):
                best_price = round(cp_val)

        if best_price <= 40 and pdp_mrp:
            best_price = pdp_mrp

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
        time.sleep(0.2)

        pdp_container = pdp_page.locator("div.prod-info-wrap, div.pdp-main, div.product-detail-sec, div.main_prod_sec").first
        screenshot_path = f"temp_fc_pdp_{int(time.time()*1000)}.png"
        if pdp_container.count() > 0:
            pdp_container.scroll_into_view_if_needed()
            time.sleep(0.2)
            pdp_container.screenshot(path=screenshot_path)
        else:
            pdp_page.screenshot(path=screenshot_path, clip={"x": 50, "y": 80, "width": 1000, "height": 580})

    except Exception as e:
        print(f"   ⚠️ FirstCry PDP check warning: {e}", flush=True)
    finally:
        pdp_page.close()

    return is_buyable, is_phantom, coupon_code, best_price, pdp_mrp, screenshot_path, stock_left_str, scraped_title, sibling_urls


def normalize_and_clean_url(raw_url, domain_prefix="https://www.firstcry.com"):
    if not raw_url:
        return ""
    url = raw_url.strip()
    if "fclinks.firstcry.com" in url:
        try:
            r = HTTP_SESSION.get(url, allow_redirects=True, timeout=6)
            if r.url and "firstcry.com" in r.url:
                url = r.url
        except Exception:
            pass
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
            r = requests.post(v3_endpoint, json=v3_payload, headers=v3_headers, timeout=6)
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
    start_ts = time.time()
    print("🚀 Starting Ultra-Fast FirstCry Sniper (VIP Playwright Watchlist + Color Swatch Expansion + 20-Thread Probe)...", flush=True)
    seen_deals = load_seen_deals()
    new_seen_count = 0
    cache_dirty = False
    seen_cards_this_run = set()
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

        # Pre-inject primary hub pincode cookies into Playwright browser context
        context.add_cookies([
            {"name": "fc_pincode", "value": PRIMARY_PINCODE, "domain": ".firstcry.com", "path": "/"},
            {"name": "pincode", "value": PRIMARY_PINCODE, "domain": ".firstcry.com", "path": "/"},
            {"name": "usr_pincode", "value": PRIMARY_PINCODE, "domain": ".firstcry.com", "path": "/"},
            {"name": "DelPincode", "value": PRIMARY_PINCODE, "domain": ".firstcry.com", "path": "/"},
        ])

        page = context.new_page()

        # --- PHASE 0: DIRECT PLAYWRIGHT VERIFICATION OF VIP WATCHLIST + COLOR VARIANTS ---
        if VIP_DIRECT_URLS:
            print(f"\n⚡ Checking {len(VIP_DIRECT_URLS)} Direct VIP Watchlist URLs via Playwright DOM...", flush=True)
            vip_queue = list(VIP_DIRECT_URLS)
            visited_vip = set()

            while vip_queue:
                vip = vip_queue.pop(0)
                try:
                    v_url = normalize_and_clean_url(vip["url"], "https://www.firstcry.com")
                    if not v_url or v_url in visited_vip:
                        continue
                    visited_vip.add(v_url)
                    seen_cards_this_run.add(v_url)

                    fc_id_m = re.search(r"/(\d{5,12})/product-detail", v_url)
                    deal_key = f"fc_{fc_id_m.group(1)}" if fc_id_m else v_url
                    title_text = vip.get("title") or "Hot Wheels VIP Collector Casting"
                    brand = determine_brand(title_text)
                    _, expected_mrp = classify_and_check_mrp(brand, title_text, 179)
                    if not expected_mrp:
                        expected_mrp = 899

                    is_buyable, is_phantom, coupon, final_price, pdp_mrp, pdp_img_path, pdp_stock_left, dom_title, sibling_urls = extract_firstcry_pdp_details(
                        context, v_url, 0.0, expected_mrp, discover_variants=True
                    )

                    if dom_title and len(dom_title) > 8:
                        title_text = dom_title
                        brand = determine_brand(title_text)
                        _, recalc_mrp = classify_and_check_mrp(brand, title_text, final_price or 179, scraped_mrp=pdp_mrp)
                        if recalc_mrp:
                            expected_mrp = recalc_mrp

                    title_key = f"fc_title_{re.sub(r'[^a-z0-9]', '', clean_product_title(title_text).lower())[:65]}_{deal_key}"

                    # Queue any newly discovered Color Swatch variants from this PDP
                    for sib in sibling_urls:
                        sib_clean = normalize_and_clean_url(sib, "https://www.firstcry.com")
                        if sib_clean and sib_clean not in visited_vip:
                            vip_queue.append({"title": "", "url": sib_clean})

                    if is_buyable:
                        if deal_key not in seen_deals and title_key not in seen_deals:
                            cap_mrp = pdp_mrp or expected_mrp
                            if final_price <= cap_mrp:
                                seen_deals.add(deal_key)
                                seen_deals.add(title_key)
                                save_seen_deals(seen_deals)
                                send_telegram_deal(
                                    "FirstCry", title_text, final_price, cap_mrp, v_url,
                                    image_path=pdp_img_path, coupon=coupon, stock_left=pdp_stock_left
                                )
                                new_seen_count += 1
                    else:
                        if not is_phantom and (deal_key in seen_deals or title_key in seen_deals):
                            seen_deals.discard(deal_key)
                            seen_deals.discard(title_key)
                            cache_dirty = True
                            print(f"   🔄 [VIP OOS] Cleared from cache for future restock: {clean_product_title(title_text)[:40]}", flush=True)

                    if pdp_img_path and os.path.exists(pdp_img_path):
                        os.remove(pdp_img_path)
                except Exception as e:
                    print(f"   ⚠️ VIP probe warning: {e}", flush=True)

        # --- PHASE 1: SEARCH URL SCANNING ---
        for target in TARGET_URLS:
            store = target["store"]
            url = target["url"]
            num_scrolls = target.get("scrolls", 3)
            print(f"\n-> Scanning {store} ({url})...", flush=True)

            try:
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                time.sleep(1.0)
                for _ in range(num_scrolls):
                    page.evaluate("window.scrollBy(0, 1800)")
                    time.sleep(0.45)

                # INSTANT 0.02s JAVASCRIPT BATCH EXTRACTION OF ALL CARDS ON THE PAGE
                raw_cards = page.evaluate(
                    """() => {
                        let nodes = document.querySelectorAll('div.list_block');
                        if (!nodes || nodes.length === 0) {
                            nodes = document.querySelectorAll('div.lft.list_cel, li.child_block');
                        }
                        const results = [];
                        nodes.forEach((card, idx) => {
                            const text = card.innerText || '';
                            const linkEl = card.querySelector("a[href*='hot-wheels'], a[href*='matchbox'], a[href*='majorette'], a[href*='product-detail']") || card.querySelector("a[href]");
                            if (!linkEl) return;
                            const href = linkEl.getAttribute('href') || '';
                            let title = linkEl.getAttribute('title') || '';
                            if (!title) {
                                const tEl = card.querySelector('.li_txt1, .p_list_text, .list_title');
                                title = tEl ? tEl.innerText.trim() : text.split('\\n')[0];
                            }
                            const spEl = card.querySelector('.r1, .rupee, .club_price, .sp_amt');
                            const mrpEl = card.querySelector('.r2, del, strike, .mrp_amt');
                            results.push({
                                index: idx,
                                text: text,
                                href: href,
                                title: title,
                                spText: spEl ? spEl.innerText : '',
                                mrpText: mrpEl ? mrpEl.innerText : ''
                            });
                        });
                        return results;
                    }"""
                )

                print(f"   Extracted {len(raw_cards)} cards in 0.02s.", flush=True)

                oos_candidates_to_probe = []
                in_stock_candidates = []

                for c_data in raw_cards:
                    try:
                        card_text = c_data["text"]
                        if not any(b in card_text.lower() for b in ["hot wheels", "matchbox", "majorette"]):
                            continue

                        clean_link = normalize_and_clean_url(c_data["href"], "https://www.firstcry.com")
                        if not clean_link or clean_link in seen_cards_this_run:
                            continue
                        seen_cards_this_run.add(clean_link)

                        fc_id_match = re.search(r"/(\d{5,12})/product-detail", clean_link)
                        deal_key = f"fc_{fc_id_match.group(1)}" if fc_id_match else clean_link.split("?")[0].rstrip("/")

                        title_text = c_data["title"]
                        # Include deal_key in title_key so sibling Color Swatches never block each other
                        title_key = f"fc_title_{re.sub(r'[^a-z0-9]', '', clean_product_title(title_text).lower())[:65]}_{deal_key}"

                        # Drop Monster Trucks, Bikes, Planes, Helicopters, Tracks & Stands immediately
                        brand = determine_brand(title_text)
                        _, prelim_mrp = classify_and_check_mrp(brand, title_text, 150)
                        if not prelim_mrp:
                            continue

                        priority_rank = 0 if is_priority_casting(title_text) else 1

                        card_left_m = re.search(r"\b([1-9][0-9]?)\s*Left\b", card_text, re.I)
                        card_stock_left = f"Only {card_left_m.group(1)} Left!" if card_left_m else None

                        is_card_oos = any(oos in card_text.lower() for oos in ["notify me", "out of stock", "sold out", "undeliverable"])
                        if is_card_oos:
                            if clean_link not in probed_oos_urls_this_run:
                                probed_oos_urls_this_run.add(clean_link)
                                oos_candidates_to_probe.append({
                                    "priority_rank": priority_rank,
                                    "index": c_data["index"],
                                    "clean_link": clean_link,
                                    "deal_key": deal_key,
                                    "title_key": title_key,
                                    "title_text": title_text,
                                    "card_stock_left": card_stock_left
                                })
                            continue

                        if not deal_key or deal_key in seen_deals or title_key in seen_deals:
                            continue

                        in_stock_candidates.append({
                            "priority_rank": priority_rank,
                            "index": c_data["index"],
                            "card_text": card_text,
                            "sp_text": c_data["spText"],
                            "mrp_text": c_data["mrpText"],
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

                # Sort OOS candidates so VIP Priority Castings get HTTP-probed FIRST
                oos_candidates_to_probe.sort(key=lambda x: (x["priority_rank"], x["index"]))

                if oos_candidates_to_probe:
                    print(f"   ⚡ 20-Thread probing {len(oos_candidates_to_probe)} unique OOS cards...", flush=True)
                    with ThreadPoolExecutor(max_workers=20) as executor:
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
                                            "priority_rank": cand["priority_rank"],
                                            "index": cand["index"],
                                            "card_text": "",
                                            "sp_text": "",
                                            "mrp_text": "",
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

                # Sort In-Stock Candidates so VIP Priority Castings alert on Telegram FIRST
                in_stock_candidates.sort(key=lambda x: (x["priority_rank"], x["index"]))

                idx_ptr = 0
                while idx_ptr < len(in_stock_candidates):
                    item = in_stock_candidates[idx_ptr]
                    idx_ptr += 1
                    try:
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

                        if price <= 40 and card_text:
                            if item["mrp_text"]:
                                m_val = re.search(r"([1-9][0-9]{2,4}(?:\.[0-9]{1,2})?)", item["mrp_text"].replace(",", ""))
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
                            elif item["sp_text"]:
                                sp_m = re.search(r"([1-9][0-9]{1,4}(?:\.[0-9]{1,2})?)", item["sp_text"].replace(",", ""))
                                if sp_m:
                                    price = float(sp_m.group(1))
                            elif pm_plain:
                                price = float(pm_plain.group(1))

                        brand = determine_brand(title_text)
                        _, expected_mrp = classify_and_check_mrp(brand, title_text, price if price > 40 else 179, scraped_mrp=scraped_mrp)
                        if not expected_mrp:
                            continue

                        is_buyable, is_phantom, coupon, final_price, pdp_mrp, pdp_img_path, pdp_stock_left, dom_title, sibling_urls = extract_firstcry_pdp_details(
                            context, clean_link, price, expected_mrp, discover_variants=True
                        )

                        if dom_title and len(dom_title) > 8:
                            title_text = dom_title
                            brand = determine_brand(title_text)
                            _, recalc_mrp = classify_and_check_mrp(brand, title_text, final_price or 179, scraped_mrp=pdp_mrp)
                            if recalc_mrp:
                                expected_mrp = recalc_mrp
                            title_key = f"fc_title_{re.sub(r'[^a-z0-9]', '', clean_product_title(title_text).lower())[:65]}_{deal_key}"

                        # Queue any newly discovered Color Swatch variants on this PDP
                        for sib in sibling_urls:
                            sib_clean = normalize_and_clean_url(sib, "https://www.firstcry.com")
                            if sib_clean and sib_clean not in seen_cards_this_run:
                                seen_cards_this_run.add(sib_clean)
                                sib_id_m = re.search(r"/(\d{5,12})/product-detail", sib_clean)
                                sib_key = f"fc_{sib_id_m.group(1)}" if sib_id_m else sib_clean
                                if sib_key not in seen_deals:
                                    in_stock_candidates.append({
                                        "priority_rank": 0,
                                        "index": 999,
                                        "card_text": "",
                                        "sp_text": "",
                                        "mrp_text": "",
                                        "clean_link": sib_clean,
                                        "deal_key": sib_key,
                                        "title_key": f"fc_title_variant_{sib_key}",
                                        "title_text": title_text,
                                        "card_stock_left": None,
                                        "live_price": 0.0,
                                        "live_mrp": None
                                    })

                        if not is_buyable:
                            if pdp_img_path and os.path.exists(pdp_img_path):
                                os.remove(pdp_img_path)
                            if is_phantom:
                                seen_deals.add(deal_key)
                                seen_deals.add(title_key)
                                cache_dirty = True
                            elif deal_key in seen_deals or title_key in seen_deals:
                                seen_deals.discard(deal_key)
                                seen_deals.discard(title_key)
                                cache_dirty = True
                            continue

                        if pdp_mrp and pdp_mrp >= 150:
                            expected_mrp = pdp_mrp

                        if final_price <= 40 or final_price > expected_mrp:
                            if pdp_img_path and os.path.exists(pdp_img_path):
                                os.remove(pdp_img_path)
                            continue

                        seen_deals.add(deal_key)
                        seen_deals.add(title_key)
                        save_seen_deals(seen_deals)

                        final_stock_left = pdp_stock_left or card_stock_left
                        send_telegram_deal(
                            store, title_text, final_price, expected_mrp, clean_link,
                            image_path=pdp_img_path, coupon=coupon, stock_left=final_stock_left
                        )

                        if pdp_img_path and os.path.exists(pdp_img_path):
                            os.remove(pdp_img_path)

                        new_seen_count += 1
                        time.sleep(0.3)
                    except Exception:
                        continue

            except Exception as e:
                print(f"⚠️ Error scraping {store}: {e}", flush=True)

        browser.close()

    if cache_dirty or new_seen_count > 0:
        save_seen_deals(seen_deals)
    elapsed = time.time() - start_ts
    print(f"\n🏁 FirstCry scan complete in {elapsed:.1f}s! Added {new_seen_count} new/restocked deals to cache.", flush=True)


if __name__ == "__main__":
    scrape_deals()
