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

# Official Indian Retail MRP Rules (Strict ceilings across all lines)
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

TARGET_URLS = [
    {"store": "Amazon.in", "url": "https://www.amazon.in/s?k=Hot+Wheels", "pages": 3},
    {"store": "Amazon.in", "url": "https://www.amazon.in/s?k=Hot+Wheels&s=date-desc-rank", "pages": 2},
    {"store": "Amazon.in", "url": "https://www.amazon.in/s?k=Hot+Wheels&rh=p_36%3A12000-56000", "pages": 3},
    {"store": "Amazon.in", "url": "https://www.amazon.in/s?k=Hot+Wheels&rh=p_36%3A50000-155000", "pages": 2},
    {"store": "Amazon.in", "url": "https://www.amazon.in/s?k=Hot+Wheels+5+Pack", "pages": 2},
    {"store": "Amazon.in", "url": "https://www.amazon.in/s?k=Hot+Wheels+Formula+1", "pages": 2},
    {"store": "Amazon.in", "url": "https://www.amazon.in/s?k=Hot+Wheels+Race+Team+Premium", "pages": 1},
    {"store": "Amazon.in", "url": "https://www.amazon.in/s?k=Hot+Wheels+Silver+Series", "pages": 2},
    {"store": "Amazon.in", "url": "https://www.amazon.in/s?k=Hot+Wheels+Premium", "pages": 2},
    {"store": "Amazon.in", "url": "https://www.amazon.in/s?k=Hot+Wheels+Pop+Culture", "pages": 2},
    {"store": "Amazon.in", "url": "https://www.amazon.in/s?k=Hot+Wheels+Car+Culture", "pages": 2},
    {"store": "Amazon.in", "url": "https://www.amazon.in/s?k=Hot+Wheels+2+Pack+Team+Transport", "pages": 2},
    {"store": "Amazon.in", "url": "https://www.amazon.in/s?k=Hot+Wheels+Forza+Gran+Turismo", "pages": 1},
    {"store": "Amazon.in", "url": "https://www.amazon.in/s?k=Hot+Wheels+Batmobile+Batman", "pages": 2},
    {"store": "Amazon.in", "url": "https://www.amazon.in/s?k=Matchbox+cars", "pages": 2},
    {"store": "Amazon.in", "url": "https://www.amazon.in/s?k=Majorette+cars", "pages": 2},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels"},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%205%20pack"},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20ferrari"},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20premium"},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20formula"},
    {"store": "FirstCry", "url": "https://www.firstcry.com/search?q=hot%20wheels%20williams%20alpine"},
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
        trimmed_list = list(seen_set)[-800:]
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
    # If the store prints a valid official MRP that is LOWER than our ceiling (e.g. ₹699 on older Super Rigs), use the lower MRP
    if scraped_mrp and 150 <= scraped_mrp < expected_mrp:
        expected_mrp = round(scraped_mrp)
    return price <= expected_mrp, expected_mrp


def classify_and_check_mrp(brand, title, price, scraped_mrp=None, store=""):
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

    is_amazon = "amazon" in store.lower()

    # FIRSTCRY TRUST RULE: Always use the MRP scraped from the FirstCry card/page when available!
    if not is_amazon and scraped_mrp and scraped_mrp >= 150:
        return price <= scraped_mrp, round(scraped_mrp)

    # --- 0. FOOLPROOF MAINLINE NUMBERING DETECTOR (Matches 1/250 to 250/250, e.g. 183/250, 198/250, 207/250) ---
    if brand == "Hot Wheels" and re.search(r'\b([1-9][0-9]?|1[0-9]{2}|2[0-4][0-9]|250)\s*/\s*250\b', title):
        target_mrp = MRP_RULES["Hot Wheels"]["mainline"]
        return cap_with_scraped_mrp(target_mrp, price, scraped_mrp)

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

    # Car Culture / Boulevard / Pop Culture series names (MRP: ₹549)
    car_culture_and_boulevard_keywords = [
        "boulevard", "car culture", "pop culture", "modern classics", "morden classics",
        "terra trek", "world tour", "race day", "japan historics", "exotic envy", "jay leno",
        "spettacolare", "slide street", "drag strip", "ronin run", "mountain drifters", "auto strasse",
        "forza horizon", "nismo 270r", "s14", "silvia", "kool kombi", "mattel 80th", "replica from popular movie"
    ]

    pop_culture_franchises = [
        "forza", "gran turismo", "cyberpunk", "back to the future", "time machine",
        "ghostbusters", "ecto-1", "teenage mutant ninja turtles", "tmnt", "party wagon",
        "top gun", "knight rider", "k.i.t.t", "kitt", "jurassic", "mandalorian", "star wars",
        "marvel", "deadpool", "spider-man", "avengers", "punisher", "x-men", "guardians of the galaxy",
        "batman", "batmobile", "dark knight", "the batman", "dc comics", "justice league",
        "fast & furious", "fast and furious", "barbie", "snoopy", "peanuts", "rugrats", "spongebob",
        "beavis", "butt-head", "mtv", "beatles", "yellow submarine", "led zeppelin", "iron maiden",
        "ac dc", "rolling stones", "grateful dead", "kiss", "motley crue", "halo", "warthog",
        "super mario", "mario kart", "street fighter", "overwatch", "world of warcraft",
        "shell", "gulf", "mobil 1", "valvoline", "pennzoil", "mooneyes", "goodyear", "hks", "advan",
        "falken", "greddy", "bilstein", "yokohama", "toyo tires", "nismo", "mugen", "spoon"
    ]

    # --- 1. BRAND-SPECIFIC MULTI-CAR PACKS & SPECIAL BOX SETS ---
    if brand == "Hot Wheels":
        # Block fake Amazon "Complete Set of 5" Silver Series single-car traps (e.g. Surfs Up @ ₹499)
        if "complete set" in t and any(s in t for s in ["silver series", "surfs up", "surf s up"]):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["silver_series"], price, scraped_mrp)

        # Gold-Label Team Transport -> ₹1,499
        if any(k in t for k in ["team transport", "kousoku", "fleet flyer", "retro rig", "aero lift", "sakura sprinter", "euro hauler", "carry on", "transport hauler"]):
            return price <= MRP_RULES["Hot Wheels"]["team_transport"], MRP_RULES["Hot Wheels"]["team_transport"]

        # Super Rigs / Heavy Haulers -> ₹999 (or ₹699 if Amazon prints M.R.P: ₹699)
        if any(k in t for k in ["super rig", "super rigs", "heavy doody", "hauling rig", "transporter vehicle with 1"]):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["super_rig"], price, scraped_mrp)

        # Gold-Label Premium 5-Pack Collector Bundles (F&F / Car Culture) -> ₹3,999
        if any(k in t for k in ["car culture", "premium bundle", "collector bundle", "real riders"]) and any(p in t for p in ["5-pack", "5 pack", "5-car pack", "5 car pack", "5 cars", "bundle", "set of 5", "5 1:64"]):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["premium_5_pack"], price, scraped_mrp)

        # Basics: Standard 5-Packs (STRICT: Must explicitly say 5-Pack / Pack of 5 / Set of 5 / 5-Car Pack!) -> ₹899
        if any(k in t for k in ["5 pack", "5-pack", "5-car pack", "5 car pack", "pack of 5", "5 pcs", "gift pack", "giftpack", "set of 5", "set of 5 toy cars"]) and "silver" not in t and "car culture" not in t and "real riders" not in t:
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["5_pack"], price, scraped_mrp)

        # Silver-Label Themed 6-Packs -> ₹1,999
        if any(k in t for k in ["6-pack", "6 pack", "6-car pack", "pack of 6", "6 cars", "6pc", "6 pcs", "set of 6"]) and any(s in t for s in ["silver", "zamac", "japanese", "european", "streets of japan", "themed", "anniversary", "celebrations", "legends"]):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["themed_6_pack"], price, scraped_mrp)

        # Formula 1 Premium 2-Packs -> ₹1,599
        if any(k in t for k in ["2-pack", "2 pack", "2-car pack", "pack of 2", "set of 2"]) and any(f in t for f in f1_keywords):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["f1_2_pack"], price, scraped_mrp)

        # Gold-Label Car Culture 2-Packs -> ₹1,099
        if any(k in t for k in ["2-pack", "2 pack", "2-car pack", "pack of 2", "2 cars", "set of 2"]) and any(p in t for p in ["car culture", "premium", "real riders", "unique pairings"]):
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["car_culture_2_pack"], price, scraped_mrp)

        # Basics: Color Reveal 2-Pack -> ₹999
        if "color reveal" in t or "colour reveal" in t:
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["color_reveal"], price, scraped_mrp)

        # Gold-Label Premium Display Sets / Dioramas -> ₹2,999
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

    # --- 3. SINGLE CARDS CLASSIFICATION (STRICT LINE MATCHING) ---
    if brand == "Hot Wheels":
        if "monster truck" in t:
            return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["monster_truck"], price, scraped_mrp)

        # Check Car Culture / Boulevard / Modern Classics BEFORE F1
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

        elif any(f in t for f in pop_culture_franchises):
            if scraped_mrp and round(scraped_mrp) == 549:
                return price <= MRP_RULES["Hot Wheels"]["premium_single"], MRP_RULES["Hot Wheels"]["premium_single"]
            else:
                return cap_with_scraped_mrp(MRP_RULES["Hot Wheels"]["silver_series"], price, scraped_mrp)

        # Silver-Label Singles (Includes Vintage Racing / Vintage Grand Prix / Exclusive Wheels) -> ₹299
        elif any(k in t for k in [
            "silver series", "silver label", "silver-label", "ultra hots", "neon speeders",
            "vintage racing", "vintage grand prix", "vintage 6", "vintage 7", "exclusive wheels",
            "themed packaging", "celebrations", "themed entertainment", "automotive",
            "pantone", "performance trucks", "streets of japan", "anniversary",
            "classic tv series batmobile", "batman forever", "the batman", "batman classic", "arkham", "85th anniversary"
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


def extract_amazon_coupon(card_text):
    code_match = re.search(r"(?:with\s+coupon|use\s+code|with\s+code|promo\s+code)\s*:?\s*([A-Z0-9]{5,12})\b", card_text, re.I)
    if code_match and code_match.group(1).upper() not in ["COUPON", "APPLIED", "SAVINGS", "DETAILS"]:
        return code_match.group(1).upper()

    clip_match = re.search(r"(Save\s+(?:₹\s*\d+|\d+%)\s+with\s+coupon)", card_text, re.I)
    if clip_match:
        return f"{clip_match.group(1)} (Clip on page)"
    return None


def extract_amazon_delivery(card_text):
    paid_match = re.search(r"(?:\+\s*)?₹\s*([1-9][0-9]{0,3}(?:\.[0-9]{1,2})?)\s*(?:delivery|shipping)", card_text, re.I)
    if paid_match:
        amt = float(paid_match.group(1))
        return f"₹{amt:.0f}"

    if re.search(r"\bFREE\s+delivery\b", card_text, re.I):
        return "FREE"

    return None


def capture_square_amazon_card(page, card, img_path):
    """
    1. Hides clutter rows (Ages, bank offers, Add to Cart button).
    2. Anchors the top of the screenshot directly to 16px ABOVE the product <img> element
       so the top of the car photo is NEVER clipped!
    3. Pads the horizontal width so the aspect ratio stays in Telegram's un-cropped zone.
    """
    try:
        clip_box = card.evaluate(
            """el => {
                // 1. Hide bottom clutter rows so the card height is compact
                const nodes = el.querySelectorAll('.a-row, .s-padding-left-small > div, span, div');
                for (const d of nodes) {
                    const txt = (d.innerText || '').trim();
                    if (/^(Ages:|Up to 5% back|Or fastest delivery|\\d+K?\\+ bought in past month|Service:|Add to cart)/i.test(txt)) {
                        if (d.children.length <= 4 && !d.querySelector('img')) {
                            d.style.display = 'none';
                        }
                    }
                }
                const btns = el.querySelectorAll('button, .a-button, [data-action="s-add-to-cart"]');
                for (const b of btns) b.style.display = 'none';

                // 2. Scroll the product image itself to y = 180px in the 1400px viewport
                const img = el.querySelector('img.s-image') || el;
                const imgRect1 = img.getBoundingClientRect();
                window.scrollBy(0, imgRect1.top - 180);

                const cardRect = el.getBoundingClientRect();
                const imgRect2 = img.getBoundingClientRect();
                const topY = Math.min(cardRect.y, imgRect2.y) - 16;
                const bottomY = cardRect.bottom + 8;

                return {
                    x: cardRect.x,
                    y: topY,
                    width: cardRect.width,
                    height: bottomY - topY
                };
            }"""
        )
        time.sleep(0.4)

        if clip_box and clip_box["width"] > 50 and clip_box["height"] > 50:
            raw_w = clip_box["width"]
            raw_h = min(clip_box["height"], 500)
            # Expand horizontal width to at least 82% of height so Telegram mobile never center-crops the vertical image!
            target_w = max(raw_w + 16, round(raw_h * 0.82))
            pad_x = (target_w - raw_w) / 2.0

            x = max(0, clip_box["x"] - pad_x)
            y = max(0, clip_box["y"])
            w = min(1366 - x, target_w)
            h = min(1400 - y, raw_h)

            page.screenshot(path=img_path, clip={"x": x, "y": y, "width": w, "height": h})
            return
    except Exception:
        pass

    card.screenshot(path=img_path)


def extract_firstcry_pdp_details(context, product_url, fallback_price, expected_mrp):
    pdp_page = context.new_page()
    coupon_code = None
    best_price = fallback_price
    pdp_mrp = expected_mrp
    screenshot_path = None

    try:
        pdp_page.goto(product_url, timeout=30000, wait_until="domcontentloaded")
        time.sleep(2.0)
        pdp_page.evaluate("window.scrollBy(0, 650)")
        time.sleep(1.2)

        pdp_text = pdp_page.locator("body").inner_text()

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
                        pct_m = re.search(r"(\d{1,2})%\s*OFF", card_header, re.I)
                        card_pct = float(pct_m.group(1)) if pct_m else 0.0
                        is_extra = "extra" in card_header.lower() or "add'l" in card_header.lower() or "additional" in card_header.lower()
                        offers_found.append((card_pct, c, is_extra))

            if not offers_found:
                sec_pcts = [float(p) for p in re.findall(r"(\d{1,2})%\s*OFF", offers_section_text, re.I) if 5 <= float(p) <= 65]
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
        time.sleep(0.3)

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

    return coupon_code, best_price, pdp_mrp, screenshot_path


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

    if "amazon.in" in raw_url:
        asin_match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", raw_url)
        if asin_match:
            asin = asin_match.group(1)
            base_amzn = f"https://www.amazon.in/dp/{asin}"
            if AMAZON_AFFILIATE_TAG:
                return f"{base_amzn}?tag={AMAZON_AFFILIATE_TAG}"
            return base_amzn
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


def send_telegram_deal(store, title, price, expected_mrp, link, image_path=None, coupon=None, delivery=None):
    clean_title = html.escape(clean_product_title(title))
    safe_store = html.escape(store)

    if round(price) < round(expected_mrp):
        savings = round(expected_mrp - price)
        header_banner = f"🔥 <b>BELOW MRP DEAL (Save ₹{savings} | MRP: ₹{expected_mrp:.0f})</b> 🔥"
        mrp_status = "Below MRP"
    else:
        header_banner = f"✅ <b>AT MRP ALERT (MRP: ₹{expected_mrp:.0f})</b> ✅"
        mrp_status = "At MRP"

    if "amazon.in" in store.lower() or "amazon" in link:
        asin_match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", link)
        if asin_match:
            asin = asin_match.group(1)
            final_link = f"https://www.amazon.in/dp/{asin}"
            if AMAZON_AFFILIATE_TAG:
                final_link += f"?tag={AMAZON_AFFILIATE_TAG}"
            link_display_text = f"https://link.amazon/{asin}"
        else:
            final_link = convert_to_affiliate_link(link)
            link_display_text = "https://link.amazon/deal"
    else:
        final_link = convert_to_affiliate_link(link)
        link_display_text = "🔗 link.firstcry" if "firstcry" in store.lower() else f"🔗 link.{store.lower().replace(' ', '')}"

    formatted_link = f'<a href="{final_link}">{link_display_text}</a>'

    inline_ship = ""
    if delivery and delivery.upper() != "FREE":
        inline_ship = f" (+ {html.escape(str(delivery))} Ship)"

    caption_lines = [
        header_banner,
        "",
        f"🚨 <b>{clean_title} @ ₹{price:.0f}{inline_ship} [{safe_store}]</b>",
        formatted_link
    ]

    if delivery:
        safe_delivery = html.escape(str(delivery))
        caption_lines.append(f"🚚 <b>Delivery:</b> {safe_delivery}")

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
                    print(f"   📸 Sent Screenshot [{store}]: {clean_title[:35]} @ ₹{price:.0f} ({mrp_status} | Delivery: {delivery} | Coupon: {coupon})", flush=True)
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
            print(f"   ✅ Sent Text [{store}]: {clean_title[:35]} @ ₹{price:.0f} ({mrp_status} | Delivery: {delivery} | Coupon: {coupon})", flush=True)
    except Exception as e:
        print(f"   ⚠️ Telegram connection error: {e}", flush=True)


def scrape_deals():
    print("🚀 Starting Complete All-Lines Deal Scraper (with Restock Tracker)...", flush=True)
    seen_deals = load_seen_deals()
    new_seen_count = 0
    cache_dirty = False

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
            viewport={"width": 1366, "height": 1400},
            locale="en-IN",
            timezone_id="Asia/Kolkata"
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        try:
            page.goto("https://www.amazon.in/", timeout=30000, wait_until="domcontentloaded")
            time.sleep(1.5)
        except Exception:
            pass

        for target in TARGET_URLS:
            store = target["store"]
            url = target["url"]
            max_pages = target.get("pages", 1)
            print(f"\n-> Scanning {store} ({url})...", flush=True)

            try:
                if "amazon" in store.lower():
                    for amz_page in range(1, max_pages + 1):
                        paged_url = f"{url}&page={amz_page}" if "?" in url else f"{url}?page={amz_page}"
                        if max_pages > 1:
                            print(f"   -> Amazon Page {amz_page}/{max_pages}: {paged_url}", flush=True)

                        page.goto(paged_url, timeout=45000, wait_until="domcontentloaded", referer="https://www.amazon.in/")
                        time.sleep(2.5)

                        page.add_style_tag(content="#navbar-main, #nav-belt, .s-Quantities-bar, .a-sticky-top { position: static !important; }")

                        for _ in range(2):
                            page.evaluate("window.scrollBy(0, 1800)")
                            time.sleep(0.8)

                        product_elements = page.locator("div[data-component-type='s-search-result']")
                        count = product_elements.count()
                        print(f"      Found {count} product cards on Amazon (Page {amz_page}).", flush=True)
                        if count == 0:
                            break

                        for i in range(count):
                            try:
                                card = product_elements.nth(i)
                                asin = card.get_attribute("data-asin")
                                if not asin:
                                    continue

                                card_text = card.inner_text()
                                if not any(b in card_text.lower() for b in ["hot wheels", "matchbox", "majorette"]):
                                    continue

                                h2_texts = [t.strip() for t in card.locator("h2").all_inner_texts() if t.strip()]
                                if len(h2_texts) >= 2:
                                    if h2_texts[0].lower() in h2_texts[1].lower():
                                        title_text = h2_texts[1]
                                    else:
                                        title_text = f"{h2_texts[0]} {h2_texts[1]}"
                                elif len(h2_texts) == 1:
                                    title_text = h2_texts[0]
                                else:
                                    title_text = card_text.split("\n")[0]

                                if not any(b in title_text.lower() for b in ["hot wheels", "matchbox", "majorette"]):
                                    if "matchbox" in card_text.lower():
                                        title_text = f"Matchbox {title_text}"
                                    elif "majorette" in card_text.lower():
                                        title_text = f"Majorette {title_text}"
                                    else:
                                        title_text = f"Hot Wheels {title_text}"

                                amz_title_key = f"amz_title_{re.sub(r'[^a-z0-9]', '', clean_product_title(title_text).lower())[:65]}"

                                # RESTOCK TRACKER 1 (Amazon OOS)
                                if any(oos in card_text.lower() for oos in ["notify me", "out of stock", "currently unavailable"]):
                                    if asin in seen_deals or amz_title_key in seen_deals:
                                        seen_deals.discard(asin)
                                        seen_deals.discard(amz_title_key)
                                        cache_dirty = True
                                        print(f"   🔄 [Amazon OOS] Cleared from cache for future restock: {clean_product_title(title_text)[:35]}", flush=True)
                                    continue

                                price_elem = card.locator(".a-price-whole").first
                                if price_elem.count() == 0:
                                    if asin in seen_deals or amz_title_key in seen_deals:
                                        seen_deals.discard(asin)
                                        seen_deals.discard(amz_title_key)
                                        cache_dirty = True
                                    continue

                                price_str = re.sub(r"[^\d.]", "", price_elem.inner_text()).rstrip(".")
                                if not price_str:
                                    continue
                                price = float(price_str)

                                scraped_mrp = None
                                mrp_match = re.search(r"M\.?R\.?P\.?\s*:?\s*₹?\s*([1-9][0-9,]{1,5})", card_text, re.I)
                                if mrp_match:
                                    try:
                                        scraped_mrp = float(mrp_match.group(1).replace(",", ""))
                                    except Exception:
                                        scraped_mrp = None

                                brand = determine_brand(title_text)
                                is_valid_mrp, expected_mrp = classify_and_check_mrp(
                                    brand, title_text, price, scraped_mrp=scraped_mrp, store=store
                                )

                                # RESTOCK TRACKER 2 (Amazon Price Jump > MRP)
                                if not is_valid_mrp:
                                    if asin in seen_deals or amz_title_key in seen_deals:
                                        seen_deals.discard(asin)
                                        seen_deals.discard(amz_title_key)
                                        cache_dirty = True
                                        print(f"   🔄 [Amazon Price > MRP] Cleared from cache for future price drop: {clean_product_title(title_text)[:35]}", flush=True)
                                    continue

                                if asin in seen_deals or amz_title_key in seen_deals:
                                    continue

                                coupon = extract_amazon_coupon(card_text)
                                delivery = extract_amazon_delivery(card_text)

                                inner_box = card.locator(".s-card-container").first
                                target_shot = inner_box if inner_box.count() > 0 else card

                                img_path = f"temp_deal_{asin}.png"
                                capture_square_amazon_card(page, target_shot, img_path)

                                seen_deals.add(asin)
                                seen_deals.add(amz_title_key)
                                save_seen_deals(seen_deals)

                                clean_link = f"https://www.amazon.in/dp/{asin}"
                                send_telegram_deal(
                                    store, title_text, price, expected_mrp, clean_link,
                                    image_path=img_path, coupon=coupon, delivery=delivery
                                )

                                if os.path.exists(img_path):
                                    os.remove(img_path)

                                new_seen_count += 1
                                time.sleep(1)
                            except Exception:
                                continue

                elif "firstcry" in store.lower():
                    page.goto(url, timeout=45000, wait_until="domcontentloaded")
                    time.sleep(3)
                    for _ in range(4):
                        page.evaluate("window.scrollBy(0, 1400)")
                        time.sleep(1.2)

                    product_elements = page.locator("div.list_block")
                    if product_elements.count() == 0:
                        product_elements = page.locator("div.lft.list_cel, li.child_block")
                    count = product_elements.count()
                    print(f"   Found {count} product cards on FirstCry.", flush=True)

                    for i in range(count):
                        try:
                            card = product_elements.nth(i)
                            card.scroll_into_view_if_needed()
                            time.sleep(0.25)

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

                            # RESTOCK TRACKER (FirstCry OOS)
                            if any(oos in card_text.lower() for oos in ["notify me", "out of stock", "sold out"]):
                                if deal_key in seen_deals or title_key in seen_deals:
                                    seen_deals.discard(deal_key)
                                    seen_deals.discard(title_key)
                                    cache_dirty = True
                                    print(f"   🔄 [FirstCry OOS] Cleared from cache for future restock: {clean_product_title(title_text)[:35]}", flush=True)
                                continue

                            if not deal_key or deal_key in seen_deals or title_key in seen_deals:
                                continue

                            price = 0.0
                            scraped_mrp = None

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
                            _, expected_mrp = classify_and_check_mrp(brand, title_text, price, scraped_mrp=scraped_mrp, store=store)
                            if not expected_mrp:
                                continue

                            coupon, final_price, pdp_mrp, pdp_img_path = extract_firstcry_pdp_details(context, clean_link, price, expected_mrp)
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

                            send_telegram_deal(store, title_text, final_price, expected_mrp, clean_link, image_path=img_path, coupon=coupon)

                            if os.path.exists(img_path):
                                os.remove(img_path)

                            new_seen_count += 1
                            time.sleep(1)
                        except Exception:
                            continue

            except Exception as e:
                print(f"⚠️ Error scraping {store}: {e}", flush=True)

        browser.close()

    if cache_dirty or new_seen_count > 0:
        save_seen_deals(seen_deals)
    print(f"\n🏁 Deal scan complete. Added {new_seen_count} new/restocked deals to cache.", flush=True)


if __name__ == "__main__":
    scrape_deals()
