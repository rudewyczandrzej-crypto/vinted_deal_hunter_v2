import os
import re
import json
import html
import time
import logging
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from supabase import create_client, Client
from groq import Groq

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

load_dotenv()

# =========================
# CONFIG
# =========================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

VINTED_BASE_URL = os.getenv("VINTED_BASE_URL", "https://www.vinted.pl").strip().rstrip("/")
VINTED_LOCALE = os.getenv("VINTED_LOCALE", "pl").strip()
VINTED_CURRENCY = os.getenv("VINTED_CURRENCY", "PLN").strip()

# Alert if direct Vinted API returns empty raw results for all searches this many cycles in a row.
EMPTY_ALERT_THRESHOLD_CYCLES = int(os.getenv("EMPTY_ALERT_THRESHOLD_CYCLES", "3"))

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()

CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))
MAX_ITEMS_PER_SEARCH = int(os.getenv("MAX_ITEMS_PER_SEARCH", "50"))
MIN_AI_SCORE_TO_SEND = int(os.getenv("MIN_AI_SCORE_TO_SEND", "2"))
MIN_DEAL_SCORE_TO_SEND = int(os.getenv("MIN_DEAL_SCORE_TO_SEND", "0"))
FEEDBACK_LEARNING_ENABLED = os.getenv("FEEDBACK_LEARNING_ENABLED", "true").lower() in ["1", "true", "yes", "y"]
FEEDBACK_TYPES = {
    "good": "👍 Хороша",
    "bad": "👎 Погана",
    "wrong": "🚫 Не той товар",
    "expensive": "💸 Не вигідно",
    "suspicious": "⚠️ Підозріло",
}

# Dynamic AI filters. When you add a search in Telegram, Groq creates a JSON filter
# and the bot stores it in Supabase searches.filter_json.
DYNAMIC_AI_FILTERS_ENABLED = os.getenv("DYNAMIC_AI_FILTERS_ENABLED", "true").lower() in ["1", "true", "yes", "y"]
FILTER_GENERATION_MODEL = os.getenv("FILTER_GENERATION_MODEL", GROQ_MODEL).strip()

# v8 Simple Mode:
# The old category filters became too strict and could silently skip good deals.
# In Simple Mode, the database filter is mostly descriptive. The bot uses Vinted search + price + freshness,
# then sends candidates to AI for scoring. Hard blocking is limited to clear accessory-only titles.
SIMPLE_FILTER_MODE = os.getenv("SIMPLE_FILTER_MODE", "true").lower() in ["1", "true", "yes", "y"]
# v9 Broad Search Mode:
# Vinted search_text can miss good offers when the query is too exact, e.g.
# "apple watch se 2" may not catch "Apple Watch SE (gen 2)".
# This mode queries a few broad variants and lets AI/deal score decide.
BROAD_SEARCH_MODE = os.getenv("BROAD_SEARCH_MODE", "true").lower() in ["1", "true", "yes", "y"]
MAX_QUERY_VARIANTS = int(os.getenv("MAX_QUERY_VARIANTS", "5"))


# New freshness filter.
ONLY_RECENT_MINUTES = int(os.getenv("ONLY_RECENT_MINUTES", "360"))

# If Apify does not return age/date:
# true  = skip item, safer, avoids old listings
# false = allow item, may send old listings
SKIP_UNKNOWN_AGE = os.getenv("SKIP_UNKNOWN_AGE", "false").lower() in ["1", "true", "yes", "y"]

# Optional hard quality filter.
# Default is false in v3 because the bot should work for electronics, shoes, clothes, collectibles, toys, etc.
# AI evaluation handles quality risks after category-aware filtering.
REJECT_BAD_CONDITIONS = os.getenv("REJECT_BAD_CONDITIONS", "false").lower() in ["1", "true", "yes", "y"]

# v4 catch-more mode:
# Words like "ryski" / "ślady użytkowania" should not silently block an offer.
# They are moved to quality_risk_any and evaluated by AI, so good cheap offers can still be sent with a warning.
SOFTEN_MINOR_WEAR_WORDS = os.getenv("SOFTEN_MINOR_WEAR_WORDS", "true").lower() in ["1", "true", "yes", "y"]
MINOR_WEAR_WORDS = [
    x.strip().lower()
    for x in os.getenv(
        "MINOR_WEAR_WORDS",
        "ryska,ryski,rysy,porysowany,porysowana,porysowane,"
        "ślady użytkowania,slady uzytkowania,ślady uzytkowania,slady użytkowania,"
        "normalne ślady,normalne slady,drobne ślady,drobne slady,"
        "minimalne ślady,minimalne slady,małe ryski,male ryski,"
        "bez pudełka,bez pudelka,brak pudełka,brak pudelka"
    ).split(",")
    if x.strip()
]

BAD_CONDITIONS = [
    x.strip().lower()
    for x in os.getenv("BAD_CONDITIONS", "zadowalający,zadowalajacy").split(",")
    if x.strip()
]

BAD_KEYWORDS = [
    x.strip().lower()
    for x in os.getenv(
        "BAD_KEYWORDS",
        "uszkodzony,uszkodzona,uszkodzone,"
        "pęknięty,pekniety,pęknięta,peknieta,pęknięcie,pekniecie,"
        "zbity ekran,zbita szybka,zbita,pęknięta szybka,peknieta szybka,"
        "nie działa,nie dziala,niedziała,niedziala,"
        "części,czesci,na części,na czesci,"
        "blokada,icloud,apple id,appleid,"
        "zablokowany,zablokowana,zablokowane,"
        "zablokowany apple id,zablokowane apple id,blokada apple id,"
        "blokada icloud,icloud lock,activation lock,"
        "brak hasła,brak hasla,nie znam hasła,nie znam hasla,"
        "wylogowany nie jest,nie wylogowany,nie wylogowana,"
        "locked,account locked,apple id locked,"
        "cracked,broken,damaged,for parts,not working"
    ).split(",")
    if x.strip()
]

# Category/product filter.
# This lets you keep broad searches like:
# /add ipad до 1100
# /add apple watch se до 500
# /add redmi pad pro до 850
# and reject accessories like etui/folia/szkło/ładowarka/pasek.
REJECT_ACCESSORIES = os.getenv("REJECT_ACCESSORIES", "true").lower() in ["1", "true", "yes", "y"]

ACCESSORY_KEYWORDS = [
    x.strip().lower()
    for x in os.getenv(
        "ACCESSORY_KEYWORDS",
        "etui,case,cover,pokrowiec,obudowa,"
        "szkło,szklo,szkiełko,szkielko,folia,ochronna,ochronne,"
        "kabel,przewód,przewod,ładowarka,ladowarka,charger,zasilacz,"
        "pasek,strap,bransoleta,bransoletka,band,"
        "rysik,stylus,apple pencil,pencil,"
        "klawiatura,keyboard,"
        "pudełko,pudelko,box,samo pudełko,samo pudelko,"
        "uchwyt,stojak,holder"
    ).split(",")
    if x.strip()
]

# Allowlist model filter.
# Instead of blocking every old model, the bot allows only the models/phrases we care about.
ENABLE_MODEL_ALLOWLIST = os.getenv("ENABLE_MODEL_ALLOWLIST", "true").lower() in ["1", "true", "yes", "y"]

IPAD_ALLOWED_KEYWORDS = [
    x.strip().lower()
    for x in os.getenv(
        "IPAD_ALLOWED_KEYWORDS",
        "ipad 9,ipad 9 gen,ipad 9 generacji,ipad 9th,ipad 2021,"
        "ipad 10,ipad 10 gen,ipad 10 generacji,ipad 10th,ipad 2022,"
        "ipad a16,ipad 11,ipad 11 gen,ipad 11 generacji,ipad 2025,ipad 10.9,ipad 10,9"
    ).split(",")
    if x.strip()
]

APPLE_WATCH_SE_ALLOWED_KEYWORDS = [
    x.strip().lower()
    for x in os.getenv(
        "APPLE_WATCH_SE_ALLOWED_KEYWORDS",
        "apple watch se,watch se,se 2,se 2 generacji,se 2gen,se 2 gen,"
        "se 2nd,se second,se 2022,se 2023,apple watch se 2,apple watch se2"
    ).split(",")
    if x.strip()
]

REDMI_PAD_PRO_ALLOWED_KEYWORDS = [
    x.strip().lower()
    for x in os.getenv(
        "REDMI_PAD_PRO_ALLOWED_KEYWORDS",
        "redmi pad pro,xiaomi redmi pad pro,pad pro 12.1,pad pro 12,1,"
        "redmi pad pro 6/128,redmi pad pro 8/256,redmi pad pro 8gb,redmi pad pro 6gb"
    ).split(",")
    if x.strip()
]

DEFAULT_COUNTRY_DOMAIN = "vinted.pl"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("vinted-ai-deal-hunter")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY")

if not GROQ_API_KEY:
    raise RuntimeError("Missing GROQ_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
groq_client = Groq(api_key=GROQ_API_KEY)

vinted_session = requests.Session()
vinted_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": f"{VINTED_BASE_URL}/catalog",
})

EMPTY_ALL_SEARCHES_CYCLES = 0
LAST_HEALTH_ALERT_TS = 0.0
HEALTH_ALERT_COOLDOWN_SECONDS = 1800


# =========================
# HELPERS
# =========================

def escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def normalize_price(value: Any) -> Optional[float]:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, dict):
        for key in ["amount", "value", "price", "numeric"]:
            if key in value:
                return normalize_price(value[key])
        return None

    text = str(value)
    text = text.replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None

    try:
        return float(match.group(1))
    except ValueError:
        return None


def extract_number(text: str) -> Optional[float]:
    match = re.search(r"(\d+(?:[,.]\d+)?)", text)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def parse_add_command(raw_text: str) -> Tuple[Optional[str], Optional[float]]:
    text = raw_text.replace("/add", "", 1).strip()

    if not text:
        return None, None

    max_price = None
    keyword = text

    if "|" in text:
        parts = [p.strip() for p in text.split("|", 1)]
        keyword = parts[0]
        max_price = extract_number(parts[1])
        return keyword.strip(), max_price

    price_patterns = [
        r"\bдо\s+(\d+(?:[,.]\d+)?)",
        r"\bmax\s+(\d+(?:[,.]\d+)?)",
        r"\bprice\s+(\d+(?:[,.]\d+)?)",
        r"\bц[іi]на\s+(\d+(?:[,.]\d+)?)",
    ]

    for pattern in price_patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            max_price = float(m.group(1).replace(",", "."))
            keyword = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
            return keyword.strip(), max_price

    parts = text.split()
    if len(parts) >= 2 and re.fullmatch(r"\d+(?:[,.]\d+)?", parts[-1]):
        max_price = float(parts[-1].replace(",", "."))
        keyword = " ".join(parts[:-1]).strip()

    return keyword.strip(), max_price




def normalize_search_keyword(value: Any) -> str:
    """Normalize a search keyword so the bot can detect exact duplicates."""
    text = str(value or "").lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_price(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def same_price(a: Any, b: Any) -> bool:
    return normalize_price(a) == normalize_price(b)

def get_first_existing(data: Dict[str, Any], keys: List[str], default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] not in [None, ""]:
            return data[key]
    return default


def deep_find_key(data: Any, possible_keys: List[str]) -> Any:
    if isinstance(data, dict):
        for key in possible_keys:
            if key in data and data[key] not in [None, ""]:
                return data[key]
        for value in data.values():
            found = deep_find_key(value, possible_keys)
            if found not in [None, ""]:
                return found

    if isinstance(data, list):
        for item in data:
            found = deep_find_key(item, possible_keys)
            if found not in [None, ""]:
                return found

    return None


def flatten_text_values(data: Any, limit: int = 120) -> str:
    values = []

    def walk(x: Any):
        if len(values) >= limit:
            return
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            s = x.strip()
            if 0 < len(s) <= 120:
                values.append(s)

    walk(data)
    return " | ".join(values)


def parse_age_minutes_from_text(text: str) -> Optional[int]:
    if not text:
        return None

    t = text.lower()

    m = re.search(r"(\d+)\s*(min|min\.|minut|minuty|minuta)", t)
    if m:
        return int(m.group(1))

    m = re.search(r"(\d+)\s*(h|godz|godz\.|godzin|godziny|godzinę)", t)
    if m:
        return int(m.group(1)) * 60

    m = re.search(r"(\d+)\s*(d|dzień|dni|dnia)", t)
    if m:
        return int(m.group(1)) * 24 * 60

    if any(word in t for word in ["tydz", "tydzień", "tygodni", "mies", "miesiąc", "rok", "lat"]):
        return 999999

    if any(word in t for word in ["przed chwilą", "teraz", "now", "just now"]):
        return 0

    return None


def parse_datetime_to_age_minutes(value: Any) -> Optional[int]:
    if value is None:
        return None

    now = datetime.now(timezone.utc)

    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts = ts / 1000
        try:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return max(0, int((now - dt).total_seconds() / 60))
        except Exception:
            return None

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None

        text_age = parse_age_minutes_from_text(s)
        if text_age is not None:
            return text_age

        if re.fullmatch(r"\d{10,13}", s):
            return parse_datetime_to_age_minutes(int(s))

        try:
            iso = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0, int((now - dt.astimezone(timezone.utc)).total_seconds() / 60))
        except Exception:
            return None

    return None


def get_item_age_minutes(raw: Dict[str, Any]) -> Tuple[Optional[int], str]:
    """
    Try to detect listing age safely.

    Important direct-mode fix:
    Do NOT scan every nested timestamp in raw JSON, because Vinted returns many unrelated
    old timestamps in metadata. We only trust explicit item date/relative age fields.
    """

    # 1. Direct explicit item-level fields only.
    explicit_keys = [
        "createdAt", "created_at", "created",
        "created_at_ts", "created_ts",
        "publishedAt", "published_at", "published",
        "updatedAt", "updated_at",
        "uploadedAt", "uploaded_at",
        "date", "time",
        "relativeDate", "relative_date",
        "createdAgo", "created_ago",
        "addedAgo", "added_ago",
        "added", "dodane"
    ]

    for key in explicit_keys:
        if isinstance(raw, dict) and key in raw and raw[key] not in [None, ""]:
            age = parse_datetime_to_age_minutes(raw[key])
            if age is not None:
                return age, f"explicit_field={key}:{raw[key]}"

    # 2. Direct nested item object, only if known wrappers exist.
    for wrapper_key in ["item", "listing", "product"]:
        nested = raw.get(wrapper_key) if isinstance(raw, dict) else None
        if isinstance(nested, dict):
            for key in explicit_keys:
                if key in nested and nested[key] not in [None, ""]:
                    age = parse_datetime_to_age_minutes(nested[key])
                    if age is not None:
                        return age, f"nested_field={wrapper_key}.{key}:{nested[key]}"

    # 3. Text-based relative age from visible listing text.
    # This catches "Dodane 8 min.", "Uploaded 8 min ago", "2 godz." if present.
    visible_text_parts = []
    for key in [
        "title", "name", "description", "desc", "status", "condition",
        "createdAgo", "created_ago", "addedAgo", "added_ago",
        "relativeDate", "relative_date", "added", "dodane"
    ]:
        if isinstance(raw, dict) and key in raw and isinstance(raw[key], str):
            visible_text_parts.append(raw[key])

    # Some Vinted direct responses may include visible text in a few nested safe places.
    for wrapper_key in ["item", "listing", "product"]:
        nested = raw.get(wrapper_key) if isinstance(raw, dict) else None
        if isinstance(nested, dict):
            for key in ["title", "description", "status", "createdAgo", "addedAgo", "relativeDate", "added", "dodane"]:
                if key in nested and isinstance(nested[key], str):
                    visible_text_parts.append(nested[key])

    visible_text = " | ".join(visible_text_parts)
    age = parse_age_minutes_from_text(visible_text)
    if age is not None:
        return age, "visible_text"

    return None, "unknown"

def is_recent_item(raw: Dict[str, Any]) -> Tuple[bool, str]:
    age_minutes, source = get_item_age_minutes(raw)

    if age_minutes is None:
        if SKIP_UNKNOWN_AGE:
            return False, f"unknown age skipped ({source})"
        return True, f"unknown age allowed ({source})"

    if age_minutes <= ONLY_RECENT_MINUTES:
        return True, f"{age_minutes} min old ({source})"

    return False, f"{age_minutes} min old, older than {ONLY_RECENT_MINUTES} min ({source})"


def item_searchable_text(raw: Dict[str, Any]) -> str:
    """
    Build text from title/description/condition and raw small text fragments.
    Used to catch risky words like locked Apple ID / iCloud / damaged.
    """
    title = str(get_first_existing(raw, ["title", "name", "itemTitle", "productTitle"], ""))
    description = str(get_first_existing(raw, ["description", "desc"], ""))
    condition = str(get_first_existing(raw, ["condition", "status"], ""))
    brand = str(get_first_existing(raw, ["brand", "brandTitle", "brand_name"], ""))
    flat = flatten_text_values(raw)
    return f"{title} | {description} | {condition} | {brand} | {flat}".lower()


def passes_quality_filter(raw: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Reject obviously bad electronics before spending Groq tokens.
    """
    if not REJECT_BAD_CONDITIONS:
        return True, "quality filter disabled"

    text = item_searchable_text(raw)

    condition = str(get_first_existing(raw, ["condition", "status"], "")).strip().lower()
    if condition and any(bad == condition or bad in condition for bad in BAD_CONDITIONS):
        return False, f"bad condition: {condition}"

    for keyword in BAD_KEYWORDS:
        if keyword and keyword in text:
            return False, f"bad keyword: {keyword}"

    return True, "quality ok"


def detect_search_profile(search_keyword: str) -> str:
    """
    Map user's broad search into product profile.
    """
    k = (search_keyword or "").lower()

    if "apple watch" in k or "watch se" in k:
        return "apple_watch_se"

    if "redmi" in k and "pad" in k:
        return "redmi_pad_pro"

    if "ipad" in k:
        return "ipad"

    return "generic"


def has_any(text: str, words: List[str]) -> bool:
    return any(word in text for word in words if word)


# Accessory words are tricky on Vinted.
# Example: "Apple Watch + pasek i ładowarka" is a good full item with accessories,
# but "pasek do Apple Watch" is only an accessory.
# Therefore single accessory words are NOT hard rejects anymore.
# We hard-reject only accessory-only context in the title.
SOFT_ACCESSORY_REJECT_TERMS = [
    "pasek", "strap", "bransoleta", "bransoletka", "band",
    "ładowarka", "ladowarka", "charger", "kabel", "przewód", "przewod",
    "pudełko", "pudelko", "box",
    "etui", "case", "cover", "pokrowiec",
    "szkło", "szklo", "szkiełko", "szkielko", "folia",
    "rysik", "stylus", "apple pencil", "klawiatura", "keyboard",
    "sznurówki", "sznurowki", "wkładki", "wkladki",
]

HARD_ACCESSORY_ONLY_PHRASES = [
    "samo pudełko", "samo pudelko", "same pudełko", "same pudelko",
    "pudełko samo", "pudelko samo", "tylko pudełko", "tylko pudelko",
    "sam pasek", "samy pasek", "tylko pasek", "same paski", "zestaw pasków", "zestaw paskow",
    "sam kabel", "tylko kabel", "sama ładowarka", "sama ladowarka", "tylko ładowarka", "tylko ladowarka",
    "samo etui", "tylko etui", "samo szkło", "samo szklo", "tylko szkło", "tylko szklo",
    "sama folia", "tylko folia", "sama bransoleta", "tylko bransoleta",
    "same sznurówki", "same sznurowki", "tylko sznurówki", "tylko sznurowki",
]

def remove_soft_accessory_terms(words: List[str]) -> List[str]:
    """Do not hard-reject offers just because description says accessories are included."""
    cleaned = []
    soft = set(SOFT_ACCESSORY_REJECT_TERMS)
    for word in as_list(words):
        w = str(word).strip()
        wl = w.lower()
        # Keep explicit accessory-only phrases as hard rejects.
        if wl in HARD_ACCESSORY_ONLY_PHRASES:
            cleaned.append(w)
            continue
        # Move single ambiguous terms away from hard-reject logic.
        if wl in soft:
            continue
        cleaned.append(w)
    return cleaned

def move_soft_accessory_rejects_to_risk(data: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize AI-generated filters so accessories are context risks, not blind blockers."""
    reject = as_list(data.get("reject_any"))
    risks = as_list(data.get("quality_risk_any"))
    kept = []
    moved = []
    soft = set(SOFT_ACCESSORY_REJECT_TERMS)
    hard = set(HARD_ACCESSORY_ONLY_PHRASES)

    for word in reject:
        w = str(word).strip()
        wl = w.lower()
        if wl in hard:
            kept.append(w)
        elif wl in soft:
            moved.append(w)
        else:
            kept.append(w)

    if moved:
        data["reject_any"] = kept
        seen = {str(x).lower() for x in risks}
        for w in moved:
            if w.lower() not in seen:
                risks.append(w)
                seen.add(w.lower())
        data["quality_risk_any"] = risks
    return data

def accessory_only_reason(raw: Dict[str, Any], profile: Dict[str, Any], search_keyword: str) -> Optional[str]:
    """Return reason only when title strongly looks like accessory-only offer.

    This protects good listings like "Apple Watch 10 + pasek i ładowarka",
    while still rejecting "pasek do Apple Watch" / "samo pudełko".
    """
    title = str(get_first_existing(raw, ["title", "name", "itemTitle", "productTitle"], "")).lower()
    title = re.sub(r"\s+", " ", title).strip()
    if not title:
        return None

    for phrase in HARD_ACCESSORY_ONLY_PHRASES:
        if phrase in title:
            return f"accessory-only phrase in title: {phrase}"

    category = str(profile.get("product_category") or infer_query_category(search_keyword)).lower()

    watch_terms = ["pasek", "strap", "bransoleta", "bransoletka", "band", "etui", "case", "szkło", "szklo", "folia", "ładowarka", "ladowarka", "kabel"]
    electronics_terms = ["etui", "case", "cover", "pokrowiec", "szkło", "szklo", "folia", "ładowarka", "ladowarka", "kabel", "rysik", "stylus", "klawiatura", "keyboard"]
    footwear_terms = ["sznurówki", "sznurowki", "wkładki", "wkladki"]

    terms = []
    product_words = []
    if category == "watches" or "watch" in search_keyword.lower():
        terms = watch_terms
        product_words = ["apple watch", "watch", "zegarek"]
    elif category == "electronics" or any(x in search_keyword.lower() for x in ["ipad", "iphone", "tablet", "redmi pad"]):
        terms = electronics_terms
        product_words = ["ipad", "iphone", "tablet", "redmi pad", "apple watch", "watch"]
    elif category == "footwear":
        terms = footwear_terms
        product_words = ["nike", "adidas", "dunk", "jordan", "buty", "sneakers"]
    else:
        return None

    # If the title starts with an accessory word, it is usually accessory-only.
    for term in terms:
        if re.search(rf"^(?:nowy|nowa|nowe|oryginalny|oryginalna|zestaw|komplet)?\s*{re.escape(term)}\b", title):
            return f"title starts with accessory term: {term}"

    # "pasek do Apple Watch", "etui do iPad", etc. are accessory-only.
    for term in terms:
        if re.search(rf"\b{re.escape(term)}\b.{0,40}\b(do|for)\b.{0,40}\b({'|'.join(re.escape(x) for x in product_words)})\b", title):
            # Do not reject if title clearly starts with the product, e.g. "Apple Watch 10 + pasek do niego".
            first_product_pos = min([title.find(x) for x in product_words if x in title] or [10**9])
            first_term_pos = title.find(term)
            if first_term_pos != -1 and first_term_pos < first_product_pos:
                return f"accessory-for-product title: {term} do/for product"

    return None


def passes_product_profile_filter(raw: Dict[str, Any], search_keyword: str) -> Tuple[bool, str]:
    if SIMPLE_FILTER_MODE:
        return True, "simple mode: legacy product filter bypassed"

    """
    Simpler product filter:
    - uses title + description + brand + raw text from Vinted
    - avoids overly strict filters for iPad/Redmi
    - but Apple Watch search is specifically for SE 2, not SE 1
    - lets Groq AI make the final judgement after basic matching
    """
    if not REJECT_ACCESSORIES:
        return True, "product filter disabled"

    text = item_searchable_text(raw)
    profile = detect_search_profile(search_keyword)

    title = str(get_first_existing(raw, ["title", "name", "itemTitle", "productTitle"], "")).lower()
    description = str(get_first_existing(raw, ["description", "desc"], "")).lower()
    brand = str(get_first_existing(raw, ["brand", "brandTitle", "brand_name"], "")).lower()
    condition = str(get_first_existing(raw, ["condition", "status"], "")).lower()

    combined = f"{title} | {description} | {brand} | {condition} | {text}"

    def title_has_accessory() -> Optional[str]:
        for keyword in ACCESSORY_KEYWORDS:
            if keyword and keyword in title:
                return keyword
        return None

    if profile == "ipad":
        if "ipad" not in combined:
            return False, "missing ipad keyword in title/description"

        if has_any(combined, ["iphone", "macbook", "airpods", "apple watch"]):
            return False, "wrong Apple product"

        accessory = title_has_accessory()
        if accessory:
            device_hints = [
                "gb", "wifi", "wi-fi", "cellular", "tablet", "generacji",
                "gen", "a16", "2021", "2022", "10.9", "10,9"
            ]
            if not has_any(combined, device_hints):
                return False, f"likely ipad accessory only: {accessory}"

        return True, "ipad broad filter ok"

    if profile == "apple_watch_se":
        # We are looking specifically for Apple Watch SE 2.
        # Sellers may write SE 2 only in description, so check combined text.
        if not ("apple" in combined and "watch" in combined):
            return False, "missing apple watch keywords in title/description"

        # Reject obvious other lines.
        if has_any(combined, [
            "series 1", "series 2", "series 3", "series 4", "series 5",
            "series 6", "series 7", "series 8", "series 9", "series 10",
            "series 11", "ultra"
        ]):
            return False, "wrong apple watch series"

        # SE 2 allowlist. This is intentionally stricter than before.
        # Plain "Apple Watch SE 40mm" is probably SE 1, so reject it.
        se2_hints = [
            "se 2", "se2", "se 2gen", "se 2 gen", "se gen 2", "se gen2",
            "gen2", "gen 2", "2gen", "2 gen", "se (gen2)",
            "se 2 generacji", "se 2. generacji",
            "2 generacji", "2. generacji", "drugiej generacji",
            "2nd gen", "2nd generation", "second generation",
            "se 2022", "se 2023", "apple watch se 2", "apple watch se2"
        ]

        if not has_any(combined, se2_hints):
            return False, "apple watch is not clearly SE 2"

        accessory = title_has_accessory()
        if accessory:
            device_hints = [
                "se 2", "2 generacji", "2. generacji", "gps", "40mm", "44mm",
                "40 mm", "44 mm", "watch se", "kondycja baterii", "bateria", "zegarek"
            ]
            if not has_any(combined, device_hints):
                return False, f"likely apple watch accessory only: {accessory}"

        return True, "apple watch se 2 filter ok"

    if profile == "redmi_pad_pro":
        # Keep Redmi strict: must be Redmi + Pad + Pro.
        if not ("redmi" in combined and "pad" in combined):
            return False, "missing redmi pad keywords in title/description"

        if "pro" not in combined:
            return False, "missing pro keyword for redmi pad pro"

        if has_any(combined, ["redmi note", "xiaomi note", "telefon", "smartfon", "phone"]):
            return False, "wrong redmi product"

        accessory = title_has_accessory()
        if accessory:
            device_hints = ["tablet", "gb", "6/128", "8/256", "12.1", "12,1", "hyperos", "android"]
            if not has_any(combined, device_hints):
                return False, f"likely redmi accessory only: {accessory}"

        return True, "redmi pad pro broad filter ok"

    return True, "generic profile ok"

def normalize_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    title = get_first_existing(raw, ["title", "name", "itemTitle", "productTitle"], "No title")
    url = get_first_existing(raw, ["url", "itemUrl", "link", "productUrl", "item_url"], "")
    item_id = get_first_existing(raw, ["id", "itemId", "item_id", "productId"], url)

    price_raw = get_first_existing(
        raw,
        ["price", "priceAmount", "amount", "totalPrice", "total_price", "priceWithCurrency"],
        None,
    )
    price = normalize_price(price_raw)

    currency = get_first_existing(raw, ["currency", "priceCurrency"], "PLN")
    brand = get_first_existing(raw, ["brand", "brandTitle", "brand_name"], "")
    size = get_first_existing(raw, ["size", "sizeTitle"], "")
    condition = get_first_existing(raw, ["condition", "status"], "")
    description = get_first_existing(raw, ["description", "desc"], "")

    seller = get_first_existing(raw, ["seller", "sellerName", "user", "username"], "")
    location = get_first_existing(raw, ["location", "city", "country"], "")

    image = get_first_existing(raw, ["image", "imageUrl", "photo", "thumbnail", "photoUrl"], "")
    images = get_first_existing(raw, ["images", "photos", "photoUrls", "imageUrls"], [])

    if isinstance(seller, dict):
        seller = get_first_existing(seller, ["login", "username", "name"], "")

    age_minutes, age_source = get_item_age_minutes(raw)

    return {
        "id": str(item_id),
        "title": str(title),
        "url": str(url),
        "price": price,
        "currency": str(currency),
        "brand": str(brand),
        "size": str(size),
        "condition": str(condition),
        "description": str(description),
        "seller": str(seller),
        "location": str(location),
        "image": str(image),
        "images": images,
        "age_minutes": age_minutes,
        "age_source": age_source,
        "query_used": str(raw.get("_vinted_query_used", "")),
        "raw": raw,
    }



# =========================
# DYNAMIC AI FILTERS
# =========================

def as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip().lower() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [x.strip().lower() for x in value.split(",") if x.strip()]
    return []


def get_filter_profile(search: Dict[str, Any]) -> Dict[str, Any]:
    raw = search.get("filter_json") or search.get("ai_filter") or {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def compact_match_text(value: str) -> str:
    """
    Compact text for tolerant matching.

    Vinted sellers often write the same model in different ways:
    - SE 2
    - SE2
    - SE (gen2)
    - SE gen 2
    - 2 generacji

    Normal substring matching misses some of these. This helper removes
    punctuation/spaces so required groups can still match obvious variants.
    """
    return re.sub(r"[^a-z0-9ąćęłńóśźż]+", "", (value or "").lower())


def phrase_match_variants(phrase: str) -> List[str]:
    phrase = (phrase or "").lower().strip()
    if not phrase:
        return []

    variants = {phrase}
    compact = compact_match_text(phrase)
    if compact:
        variants.add(compact)

    # Polish/market shorthand variants for generations:
    # "2 gen" <-> "2gen" <-> "gen2"
    m = re.fullmatch(r"(\d{1,2})\s*gen", phrase.replace(".", " ").strip())
    if m:
        n = m.group(1)
        variants.update([f"{n}gen", f"gen{n}", f"gen {n}", f"{n} gen"])

    m = re.fullmatch(r"gen\s*(\d{1,2})", phrase.replace(".", " ").strip())
    if m:
        n = m.group(1)
        variants.update([f"gen{n}", f"{n}gen", f"gen {n}", f"{n} gen"])

    # "se 2" <-> "se2"
    m = re.fullmatch(r"se\s*(\d{1,2})", phrase.replace(".", " ").strip())
    if m:
        n = m.group(1)
        variants.update([f"se{n}", f"se {n}"])

    return [v for v in variants if v]


def phrase_exists(text: str, phrase: str) -> bool:
    text_l = (text or "").lower()
    text_compact = compact_match_text(text_l)

    for variant in phrase_match_variants(phrase):
        if variant in text_l:
            return True
        variant_compact = compact_match_text(variant)
        # Avoid compact matching for very short generic tokens like "9" or "10".
        if len(variant_compact) >= 3 and variant_compact in text_compact:
            return True
    return False


def text_contains_any(text: str, keywords: List[str]) -> Optional[str]:
    text = (text or "").lower()
    for kw in keywords:
        kw = (kw or "").lower().strip()
        if kw and phrase_exists(text, kw):
            return kw
    return None


def text_contains_all_groups(text: str, groups: Any) -> Tuple[bool, str]:
    """
    groups example: [["ipad", "i pad"], ["10 gen", "10 generacji", "10th", "2022"]]
    At least one phrase from every group must exist. Uses tolerant matching
    for variants like SE (gen2), SE2, SE gen 2, 2gen.
    """
    text = (text or "").lower()
    if not isinstance(groups, list):
        return True, "no required groups"

    for group in groups:
        words = as_list(group)
        if not words:
            continue
        if not any(phrase_exists(text, w) for w in words):
            return False, "missing one required group: " + "/".join(words[:6])
    return True, "required groups ok"



def infer_query_category(keyword: str) -> str:
    """Local category classifier used as a hard safety net around the AI-generated filter.

    Important: watches must be detected before generic electronics, otherwise
    queries like "Apple Watch 10" can accidentally inherit iPad rules.
    """
    k = (keyword or "").lower()

    watch_terms = ["apple watch", "iwatch", "zegarek", "zegarek apple", "watch se", "watch series", "series 9", "series 10", "g-shock", "casio", "seiko", "garmin", "smartwatch"]
    footwear = ["buty", "sneakers", "sneaker", "nike", "adidas", "dunk", "jordan", "yeezy", "air force", "new balance", "asics", "puma", "reebok", "vans", "converse", "trampki"]
    clothing = ["kurtka", "bluza", "hoodie", "spodnie", "koszulka", "t-shirt", "tshirt", "sweter", "sukienka", "płaszcz", "plaszcz", "czapka", "czapeczka", "tech fleece"]
    collectibles = ["funko", "pop", "figurka", "figurki", "lego", "pokemon", "pokémon", "karta", "karty", "hot wheels", "manga", "komiks", "resorak", "model", "zabawka", "figurine"]
    beauty = ["perfumy", "kosmetyk", "kosmetyki", "krem", "serum", "makeup", "makijaż", "makijaz", "pomadka", "paleta"]
    bags = ["plecak", "torba", "torebka", "portfel", "walizka", "nerka", "saszetka"]
    books = ["książka", "ksiazka", "book", "księga", "manga tom", "komiks tom"]
    home = ["lampa", "krzesło", "krzeslo", "stolik", "dywan", "pościel", "posciel", "kubek", "talerz"]
    electronics = ["ipad", "iphone", "macbook", "airpods", "redmi pad", "tablet", "telefon", "smartfon", "laptop", "kamera", "aparat", "konsola", "ps5", "xbox", "switch", "kindle"]

    def any_in(words):
        return any(w in k for w in words)

    if any_in(watch_terms):
        return "watches"
    if any_in(footwear):
        return "footwear"
    if any_in(collectibles):
        return "collectibles"
    if any_in(beauty):
        return "beauty"
    if any_in(bags):
        return "bags"
    if any_in(books):
        return "books"
    if any_in(home):
        return "home"
    if any_in(clothing):
        return "clothing"
    if any_in(electronics):
        return "electronics"
    return "generic"


def infer_product_intent(keyword: str) -> str:
    """More specific product family used to stop cross-product leaks inside one broad area."""
    k = (keyword or "").lower()
    if "apple watch" in k or "iwatch" in k or "watch series" in k:
        return "apple_watch"
    if "ipad" in k or "i pad" in k:
        return "ipad"
    if "iphone" in k:
        return "iphone"
    if "airpods" in k:
        return "airpods"
    if "macbook" in k:
        return "macbook"
    if "redmi pad" in k or ("redmi" in k and "pad" in k):
        return "redmi_pad"
    if "funko" in k or " pop" in f" {k}" or k.startswith("pop "):
        return "funko_pop"
    if "lego" in k:
        return "lego"
    if "nike" in k and "dunk" in k:
        return "nike_dunk"
    return infer_query_category(keyword)

def build_ai_filter_prompt(keyword: str, max_price: Optional[float]) -> str:
    category_hint = infer_query_category(keyword)
    return f"""
Ти створюєш JSON-фільтр для Telegram-бота, який шукає товари на Vinted у Польщі.
Користувач НЕ буде сам писати фільтри. Він дає тільки людський запит.
Твоя задача — спочатку зрозуміти категорію товару, а потім згенерувати правила саме під цю категорію.

Запит користувача: {keyword}
Максимальна ціна, якщо є: {max_price} PLN
Підказка категорії від локального класифікатора: {category_hint}

Поверни ТІЛЬКИ валідний JSON без markdown. Формат:
{{
  "product_category": "electronics / watches / footwear / clothing / collectibles / beauty / bags / toys / books / home / generic",
  "vinted_query": "короткий пошуковий запит для Vinted польською/англійською, без ціни",
  "filter_summary_ua": "коротко українською що саме шукаємо і що відсікаємо; без вигаданих гарантій/чеків",
  "required_groups": [
    ["синоніми бренду або головного товару"],
    ["синоніми моделі/серії/версії, якщо користувач її вказав"]
  ],
  "include_any": ["додаткові корисні слова, які можуть підтвердити правильний товар"],
  "reject_any": ["тільки явні фрази, які означають НЕПРАВИЛЬНИЙ товар або accessory-only оголошення, наприклад samo pudełko / tylko pasek"],
  "wrong_product_any": ["інші схожі товари або моделі, які не підходять"],
  "quality_risk_any": ["ризикові або неоднозначні слова саме для цієї категорії, які AI має оцінити, але не блокувати мовчки"],
  "min_ai_score": 3,
  "message_to_seller_pl": "коротке питання продавцю польською, що перевірити перед покупкою"
}}

ГОЛОВНЕ ПРАВИЛО:
- НІКОЛИ не використовуй правила від іншої категорії.
- Якщо користувач шукає кросівки Nike Dunk Low — НЕ додавай ipad, tablet, iCloud, Apple ID, 10 gen, A2696, A2757, 10.9.
- Якщо користувач шукає Apple Watch — НЕ додавай iPad-ознаки: 10.9, 10,9, A2696, A2757, A2777, tablet, iPad.
- Якщо користувач шукає Apple Watch Series 9/10 — модельні слова мають бути Series 9/Series 10, S9/S10, а НЕ iPad 10 gen або iPad 2022.
- Якщо користувач шукає Funko Pop або фігурку — НЕ додавай технічні фільтри типу iCloud/ładowarka/Apple ID.
- Якщо користувач шукає одяг/взуття — НЕ відсікай charger, iCloud, kabel, якщо це не має сенсу для категорії.

Як будувати фільтр:
1. Визнач категорію товару.
2. Вибери широке vinted_query, яке реально дасть результати на Vinted.
3. required_groups мають бути логічними AND-групами: мінімум одна фраза з кожної групи повинна бути в оголошенні.
4. Не роби один величезний required_groups зі словами різних категорій.
5. Не вимагай розмір взуття/одягу, якщо користувач не вказав розмір.
6. Не вимагай чек/оригінальну коробку/гарантію, якщо користувач прямо цього не просив.
7. Краще пропустити сумнівну оферту на AI-оцінку, ніж мовчки її заблокувати.
8. Не додавай одиночні слова аксесуарів у reject_any, якщо вони можуть означати комплект. Наприклад для Apple Watch слова pasek/ładowarka/kabel/pudełko мають бути quality_risk_any, бо оголошення може бути "Apple Watch + pasek i ładowarka". У reject_any давай тільки accessory-only фрази: "samo pudełko", "tylko pasek", "pasek do Apple Watch", "ładowarka do Apple Watch".
9. Те саме для інших категорій: коробка/зарядка/ремінець/шнурівки/аксесуари не мають автоматично вбивати офер, якщо головний товар теж присутній.

Приклади категорій:

A) Запит: nike dunk low до 150
Очікувано:
- product_category: footwear
- vinted_query: "nike dunk low"
- required_groups: [["nike"], ["dunk"], ["low"]]
- reject_any: ["etui", "case", "brelok", "miniaturka", "zdjęcie", "plakat"]
- wrong_product_any: ["dunk high", "air force", "jordan", "yeezy", "adidas"]
- quality_risk_any: ["podróbka", "fake", "replika", "zniszczone", "dziura", "odklejona podeszwa"]

B) Запит: funko pop harry potter до 60
Очікувано:
- product_category: collectibles
- vinted_query: "funko pop harry potter"
- required_groups: [["funko", "pop"], ["harry potter", "potter"]]
- reject_any: ["koszulka", "bluza", "plakat", "naklejka", "brelok"]
- wrong_product_any: ["lego", "książka", "ksiazka", "dvd", "gra"]
- quality_risk_any: ["uszkodzone pudełko", "brak pudełka", "podróbka", "fake"]

C) Запит: lego star wars do 100
- product_category: collectibles
- vinted_query: "lego star wars"
- required_groups: [["lego"], ["star wars"]]
- reject_any: ["instrukcja", "pudełko samo", "samo pudełko", "naklejki"]
- quality_risk_any: ["niekompletne", "braki", "bez figurek", "części"]

D) Запит: kurtka nike tech fleece do 200
- product_category: clothing
- vinted_query: "nike tech fleece"
- required_groups: [["nike"], ["tech fleece"]]
- wrong_product_any: ["spodnie", "shorts"] якщо користувач чітко написав kurtka/bluza
- quality_risk_any: ["plamy", "dziura", "zmechacona", "podróbka", "fake"]

E) Запит: ipad 10 gen do 1000
- product_category: electronics
- vinted_query: "ipad 10"
- required_groups: [["ipad", "i pad"], ["10 gen", "10 generacji", "10th", "2022", "10.9", "10,9", "A2696", "A2757", "A2777"]]
- reject_any: ["samo pudełko", "tylko pudełko", "etui do iPad", "szkło do iPad", "ładowarka do iPad"]
- quality_risk_any: ["etui", "case", "szkło", "folia", "kabel", "ładowarka", "pudełko", "rysik", "klawiatura", "icloud", "apple id", "blokada", "uszkodzony", "pęknięty", "zbity", "nie działa"]

F) Запит: apple watch se 2 do 400
- product_category: watches
- vinted_query: "apple watch se"
- required_groups: [["apple watch", "watch"], ["se"], ["2 gen", "gen2", "gen 2", "2gen", "2 generacji", "2. generacji", "drugiej generacji", "2022", "2023", "se 2", "se2", "se gen2", "se (gen2)"]]
- reject_any: ["sam pasek", "tylko pasek", "pasek do Apple Watch", "samo pudełko", "tylko ładowarka"]
- quality_risk_any: ["pasek", "strap", "bransoleta", "etui", "szkło", "ładowarka", "kabel", "pudełko", "blokada", "uszkodzony", "pęknięty", "zbity", "nie działa", "kondycja baterii"]


G) Запит: apple watch 10 do 1100
- product_category: watches
- vinted_query: "apple watch 10"
- required_groups: [["apple watch", "watch"], ["series 10", "s10", "10"]]
- reject_any: ["sam pasek", "tylko pasek", "pasek do Apple Watch", "samo pudełko", "tylko pudełko", "sama ładowarka", "tylko ładowarka"]
- wrong_product_any: ["series 8", "series 7", "series 6", "se", "ultra"]
- quality_risk_any: ["pasek", "strap", "bransoleta", "etui", "szkło", "folia", "ładowarka", "kabel", "pudełko", "blokada", "uszkodzony", "pęknięty", "zbity", "nie działa", "nie dziala", "digital crown", "kondycja baterii"]
""".strip()


def local_category_filter(keyword: str, max_price: Optional[float]) -> Dict[str, Any]:
    k = (keyword or "").lower().strip()
    category = infer_query_category(k)

    reject_common_market_noise = ["zdjęcie", "zdjecie", "plakat", "naklejka", "brelok", "miniaturka"]

    if category == "footwear":
        required = []
        if "nike" in k: required.append(["nike"])
        if "dunk" in k: required.append(["dunk"])
        if "low" in k: required.append(["low"])
        if "jordan" in k: required.append(["jordan"])
        if "air force" in k or "af1" in k: required.append(["air force", "af1"])
        if not required and keyword: required = [[keyword]]
        return {
            "product_category": "footwear",
            "vinted_query": keyword,
            "filter_summary_ua": "Шукаю конкретне взуття/кросівки, без аксесуарів, фейків і явно знищеного стану.",
            "required_groups": required,
            "include_any": ["buty", "sneakers", "rozmiar", "size"],
            "reject_any": reject_common_market_noise + ["pudełko samo", "samo pudełko", "tylko pudełko", "same sznurówki", "same sznurowki", "tylko sznurówki", "tylko sznurowki"],
            "wrong_product_any": [],
            "quality_risk_any": ["pudełko", "pudelko", "sznurówki", "sznurowki", "wkładki", "wkladki", "podróbka", "podrobka", "fake", "replika", "zniszczone", "dziura", "odklejona podeszwa", "brudne"],
            "min_ai_score": MIN_AI_SCORE_TO_SEND,
            "message_to_seller_pl": "Cześć, czy buty są oryginalne i w jakim są dokładnie stanie? Czy możesz wysłać więcej zdjęć podeszwy, metki i wnętrza?"
        }

    if category == "collectibles":
        required = []
        if "funko" in k or "pop" in k: required.append(["funko", "pop"])
        if "lego" in k: required.append(["lego"])
        if "pokemon" in k: required.append(["pokemon", "pokémon"])
        # Add a broad second group from remaining meaningful terms only if obvious.
        for term in ["harry potter", "star wars", "marvel", "dc", "naruto", "dragon ball", "one piece", "disney"]:
            if term in k: required.append([term])
        if not required and keyword: required = [[keyword]]
        return {
            "product_category": "collectibles",
            "vinted_query": keyword,
            "filter_summary_ua": "Шукаю колекційний товар/фігурку, відсікаю одяг, плакати, наклейки та інші нецільові товари.",
            "required_groups": required,
            "include_any": ["figurka", "kolekcjonerskie", "collector", "oryginalne"],
            "reject_any": reject_common_market_noise + ["koszulka", "bluza", "spodnie", "czapka", "książka", "ksiazka", "dvd", "gra"],
            "wrong_product_any": [],
            "quality_risk_any": ["pudełko", "pudelko", "uszkodzone pudełko", "brak pudełka", "brak pudelka", "podróbka", "podrobka", "fake", "niekompletne", "braki"],
            "min_ai_score": MIN_AI_SCORE_TO_SEND,
            "message_to_seller_pl": "Cześć, czy figurka jest oryginalna i w jakim stanie jest pudełko? Czy możesz wysłać dodatkowe zdjęcia z każdej strony?"
        }

    if category == "watches":
        required = []
        vinted_query = keyword
        wrong = []
        kl = k.replace("series", "").strip()
        if "apple watch" in k or "iwatch" in k:
            required.append(["apple watch", "watch"])
            if "se" in k:
                required.append(["se"])
                if "2" in k or "drug" in k:
                    required.append(["2 gen", "gen2", "gen 2", "2gen", "2 generacji", "2. generacji", "drugiej generacji", "2022", "2023", "se 2", "se2", "se gen2", "se (gen2)"])
                vinted_query = "apple watch se"
            elif "10" in k:
                required.append(["series 10", "s10", "10"])
                vinted_query = "apple watch 10"
                wrong = ["series 9", "series 8", "series 7", "series 6", "se", "ultra"]
            elif "9" in k:
                required.append(["series 9", "s9", "9"])
                vinted_query = "apple watch 9"
                wrong = ["series 10", "series 8", "series 7", "series 6", "se", "ultra"]
        if not required and keyword:
            required = [[keyword]]
        return {
            "product_category": "watches",
            "vinted_query": vinted_query,
            "filter_summary_ua": "Шукаю годинник з твого запиту, відсікаю ремінці, чохли, зарядки, коробки та явно інші моделі.",
            "required_groups": required,
            "include_any": ["koperta", "mm", "gps", "cellular", "kondycja baterii"],
            "reject_any": reject_common_market_noise + ["sam pasek", "tylko pasek", "samy pasek", "pasek do apple watch", "strap do apple watch", "bransoleta do apple watch", "samo pudełko", "samo pudelko", "tylko pudełko", "tylko pudelko", "sama ładowarka", "sama ladowarka", "tylko ładowarka", "tylko ladowarka"],
            "wrong_product_any": wrong,
            "quality_risk_any": ["pasek", "strap", "bransoleta", "bransoletka", "etui", "case", "szkło", "szklo", "folia", "ładowarka", "ladowarka", "kabel", "pudełko", "pudelko", "blokada", "icloud", "apple id", "uszkodzony", "pęknięty", "pekniety", "zbity", "nie działa", "nie dziala", "digital crown", "bateria słaba", "slaba bateria", "nie paruje"],
            "min_ai_score": MIN_AI_SCORE_TO_SEND,
            "message_to_seller_pl": "Cześć, czy zegarek jest w pełni sprawny, wylogowany z Apple ID i jaka jest kondycja baterii?"
        }

    if category in ["clothing", "bags", "beauty", "books", "home"]:
        return {
            "product_category": category,
            "vinted_query": keyword,
            "filter_summary_ua": "Шукаю товар з твого запиту, відсікаю очевидно неправильні речі та ризиковий стан.",
            "required_groups": [[keyword]] if keyword else [],
            "include_any": [],
            "reject_any": reject_common_market_noise,
            "wrong_product_any": [],
            "quality_risk_any": ["podróbka", "podrobka", "fake", "uszkodzony", "zniszczony", "plamy", "dziura"],
            "min_ai_score": MIN_AI_SCORE_TO_SEND,
            "message_to_seller_pl": "Cześć, czy oferta jest aktualna? Czy możesz napisać, jaki jest dokładny stan i wysłać dodatkowe zdjęcia?"
        }

    # Electronics and generic fallback
    required = [[keyword.lower()]] if keyword else []
    if "ipad" in k and ("10" in k or "десят" in k or "gener" in k):
        required = [["ipad", "i pad"], ["10", "10 gen", "10 generacji", "10th", "2022", "10.9", "10,9"]]
    elif "apple watch" in k and "se" in k:
        required = [["apple watch", "watch"], ["se"], ["2", "gen2", "gen 2", "2gen", "2 gen", "2 generacji", "2022", "2023", "se gen2", "se (gen2)"]]
    return {
        "product_category": category,
        "vinted_query": "apple watch se" if ("apple watch" in k and "se" in k) else keyword,
        "filter_summary_ua": "Шукаю товар з твого запиту, відсікаю явно неправильні товари та ризикові оголошення.",
        "required_groups": required,
        "include_any": [],
        "reject_any": ["samo pudełko", "samo pudelko", "tylko pudełko", "tylko pudelko", "etui do", "case do", "szkło do", "szklo do", "folia do", "ładowarka do", "ladowarka do"],
        "wrong_product_any": [],
        "quality_risk_any": ["etui", "case", "cover", "pokrowiec", "szkło", "szklo", "folia", "kabel", "ładowarka", "ladowarka", "charger", "pudełko", "pudelko", "rysik", "stylus", "klawiatura", "uchwyt", "stojak", "uszkodzony", "pęknięty", "zbity", "icloud", "blokada", "nie działa"],
        "min_ai_score": MIN_AI_SCORE_TO_SEND,
        "message_to_seller_pl": "Dzień dobry, czy oferta jest aktualna? Czy przedmiot jest w pełni sprawny i czy można prosić o dodatkowe zdjęcia?"
    }


CATEGORY_FORBIDDEN_TERMS = {
    "footwear": ["ipad", "iphone", "icloud", "apple id", "a2696", "a2757", "a2777", "10.9", "10,9", "ładowarka", "ladowarka", "charger", "kabel", "tablet", "macbook"],
    "collectibles": ["ipad", "iphone", "icloud", "apple id", "a2696", "a2757", "a2777", "10.9", "10,9", "ładowarka", "ladowarka", "charger", "kabel", "tablet"],
    "clothing": ["ipad", "iphone", "icloud", "apple id", "a2696", "a2757", "a2777", "10.9", "10,9", "ładowarka", "ladowarka", "charger", "kabel", "tablet"],
    "bags": ["ipad", "iphone", "icloud", "apple id", "a2696", "a2757", "a2777", "10.9", "10,9", "ładowarka", "ladowarka", "charger", "kabel", "tablet"],
    "beauty": ["ipad", "iphone", "icloud", "apple id", "a2696", "a2757", "a2777", "10.9", "10,9", "ładowarka", "ladowarka", "charger", "kabel", "tablet"],
    "books": ["ipad", "iphone", "icloud", "apple id", "a2696", "a2757", "a2777", "10.9", "10,9", "ładowarka", "ladowarka", "charger", "kabel", "tablet"],
    "home": ["ipad", "iphone", "icloud", "apple id", "a2696", "a2757", "a2777", "10.9", "10,9", "ładowarka", "ladowarka", "charger", "kabel", "tablet"],
    # Apple Watch can mention iCloud/Apple ID as risk, but must never inherit iPad model codes.
    "watches": ["ipad", "i pad", "tablet", "a2696", "a2757", "a2777", "10.9", "10,9", "rysik", "klawiatura"],
}

INTENT_FORBIDDEN_TERMS = {
    "apple_watch": ["ipad", "i pad", "tablet", "a2696", "a2757", "a2777", "10.9", "10,9", "10 gen", "10 generacji", "10th generation", "rysik", "klawiatura"],
    "ipad": ["pasek", "strap", "bransoleta", "series 9", "series 10", "apple watch", "watch se", "samo pasek"],
    "nike_dunk": ["ipad", "i pad", "tablet", "icloud", "apple id", "a2696", "a2757", "a2777", "10.9", "10,9", "ładowarka", "ladowarka", "charger", "kabel"],
    "funko_pop": ["ipad", "i pad", "tablet", "icloud", "apple id", "a2696", "a2757", "a2777", "10.9", "10,9", "ładowarka", "ladowarka", "charger", "kabel"],
    "lego": ["ipad", "i pad", "tablet", "icloud", "apple id", "a2696", "a2757", "a2777", "10.9", "10,9", "ładowarka", "ladowarka", "charger", "kabel"],
}


def flatten_filter_terms(data: Dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False).lower()


def filter_has_category_leak(keyword: str, data: Dict[str, Any]) -> bool:
    """Detect AI filters that mixed rules from another category/product family."""
    category = infer_query_category(keyword)
    intent = infer_product_intent(keyword)
    serialized = flatten_filter_terms(data)
    forbidden = set(CATEGORY_FORBIDDEN_TERMS.get(category, [])) | set(INTENT_FORBIDDEN_TERMS.get(intent, []))
    return any(term and term in serialized for term in forbidden)


def force_local_category_consistency(keyword: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Keep the AI output, but force category/product-family consistency.

    If the AI output contains strong foreign-category terms, fallback is safer than
    trying to surgically fix a poisoned required_groups list.
    """
    local_category = infer_query_category(keyword)
    data["product_category"] = local_category

    # Keep Vinted query short and close to the user's intent.
    vq = str(data.get("vinted_query") or keyword).lower()
    if filter_has_category_leak(keyword, {"vinted_query": vq}):
        data["vinted_query"] = local_category_filter(keyword, None).get("vinted_query", keyword)

    # A strong leak anywhere in required/wrong/reject means the full filter is unreliable.
    risky_parts = {
        "required_groups": data.get("required_groups"),
        "reject_any": data.get("reject_any"),
        "wrong_product_any": data.get("wrong_product_any"),
        "include_any": data.get("include_any"),
    }
    if filter_has_category_leak(keyword, risky_parts):
        logger.warning("AI filter category/product leak detected for keyword=%s. Using local category fallback.", keyword)
        return local_category_filter(keyword, None)

    return data

def move_minor_wear_from_reject_to_risk(data: Dict[str, Any]) -> Dict[str, Any]:
    if not SOFTEN_MINOR_WEAR_WORDS:
        return data

    reject = as_list(data.get("reject_any"))
    risks = as_list(data.get("quality_risk_any"))

    kept_reject = []
    moved = []
    for word in reject:
        w = str(word).strip()
        wl = w.lower()
        if any(minor and minor in wl for minor in MINOR_WEAR_WORDS):
            moved.append(w)
        else:
            kept_reject.append(w)

    if moved:
        data["reject_any"] = kept_reject
        # preserve order and avoid duplicates
        seen = set(x.lower() for x in risks)
        for w in moved:
            if w.lower() not in seen:
                risks.append(w)
                seen.add(w.lower())
        data["quality_risk_any"] = risks

    return data


def sanitize_ai_filter(keyword: str, data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return local_category_filter(keyword, None)

    data.setdefault("product_category", infer_query_category(keyword))
    data.setdefault("vinted_query", keyword)
    data.setdefault("filter_summary_ua", "AI створив фільтр для цього пошуку.")
    data.setdefault("required_groups", [[keyword.lower()]] if keyword else [])
    data.setdefault("include_any", [])
    data.setdefault("reject_any", [])
    data.setdefault("wrong_product_any", [])
    data.setdefault("quality_risk_any", [])
    data.setdefault("min_ai_score", MIN_AI_SCORE_TO_SEND)
    data.setdefault("message_to_seller_pl", "Cześć, czy oferta jest aktualna? Czy możesz wysłać więcej informacji i zdjęć?")

    data = move_minor_wear_from_reject_to_risk(data)
    data = move_soft_accessory_rejects_to_risk(data)
    data = force_local_category_consistency(keyword, data)
    data = move_soft_accessory_rejects_to_risk(data)

    # Final safety: if AI produced empty required groups, use local fallback groups.
    if not as_list(data.get("required_groups")):
        local = local_category_filter(keyword, None)
        data["required_groups"] = local.get("required_groups", [[keyword.lower()]])

    return data

def fallback_filter(keyword: str, max_price: Optional[float]) -> Dict[str, Any]:
    return local_category_filter(keyword, max_price)

def simple_mode_filter(keyword: str, max_price: Optional[float]) -> Dict[str, Any]:
    """Very permissive v8 filter.

    It avoids model/category overfitting. Vinted query brings candidates; AI deal scoring decides.
    This is meant for testing and for learning via feedback buttons.
    """
    k = (keyword or "").lower().strip()
    category = infer_query_category(k)
    vq = keyword.strip() if keyword else ""

    # Search queries should be broad enough to catch seller wording variants.
    if "apple watch" in k and "se" in k:
        vq = "apple watch se"
    elif "apple watch" in k and ("series 10" in k or "watch 10" in k or re.search(r"\b10\b", k)):
        vq = "apple watch 10"
    elif "apple watch" in k and ("series 9" in k or "watch 9" in k or re.search(r"\b9\b", k)):
        vq = "apple watch 9"
    elif "ipad" in k and re.search(r"\b10\b|10gen|10 gen|10 generacji", k):
        vq = "ipad 10"
    elif "ipad" in k and re.search(r"\b11\b|11gen|11 gen|11 generacji", k):
        vq = "ipad 11"
    elif "redmi" in k and "pad" in k:
        vq = "redmi pad"

    return {
        "product_category": category,
        "vinted_query": vq,
        "filter_summary_ua": "v8 Simple Mode: фільтр максимально широкий; бот майже не блокує оферти до AI-оцінки, щоб не пропускати хороші варіанти.",
        "required_groups": [],
        "include_any": [],
        "reject_any": [
            "samo pudełko", "samo pudelko", "tylko pudełko", "tylko pudelko",
            "sam pasek", "tylko pasek", "sama ładowarka", "sama ladowarka",
            "tylko ładowarka", "tylko ladowarka", "samo etui", "tylko etui",
            "samo szkło", "samo szklo", "tylko szkło", "tylko szklo",
            "same sznurówki", "same sznurowki", "tylko sznurówki", "tylko sznurowki"
        ],
        "wrong_product_any": [],
        "quality_risk_any": [
            "pasek", "bransoleta", "ładowarka", "ladowarka", "kabel", "pudełko", "pudelko",
            "etui", "case", "szkło", "szklo", "folia", "ryski", "ślady użytkowania",
            "uszkodzony", "pęknięty", "pekniety", "zbity", "nie działa", "nie dziala",
            "blokada", "icloud", "apple id", "nie paruje", "digital crown", "kondycja baterii",
            "podróbka", "podrobka", "fake", "replika", "braki", "niekompletne"
        ],
        "min_ai_score": MIN_AI_SCORE_TO_SEND,
        "message_to_seller_pl": "Cześć, czy oferta jest aktualna i czy przedmiot jest w pełni sprawny? Czy można prosić o dodatkowe zdjęcia oraz potwierdzenie modelu/stanu?"
    }

def generate_filter_with_ai(keyword: str, max_price: Optional[float]) -> Dict[str, Any]:
    if SIMPLE_FILTER_MODE:
        return simple_mode_filter(keyword, max_price)

    if not DYNAMIC_AI_FILTERS_ENABLED:
        return fallback_filter(keyword, max_price)

    try:
        completion = groq_client.chat.completions.create(
            model=FILTER_GENERATION_MODEL,
            messages=[
                {"role": "system", "content": "You generate category-aware marketplace search filters for Vinted. Never mix rules between categories. Return valid JSON only."},
                {"role": "user", "content": build_ai_filter_prompt(keyword, max_price)},
            ],
            temperature=0.15,
        )
        content = completion.choices[0].message.content or "{}"
        data = safe_json_loads(content)
        if not isinstance(data, dict):
            raise ValueError("AI did not return object")

        data.setdefault("vinted_query", keyword)
        data.setdefault("filter_summary_ua", "AI створив фільтр для цього пошуку.")
        data.setdefault("required_groups", [])
        data.setdefault("include_any", [])
        data.setdefault("reject_any", [])
        data.setdefault("wrong_product_any", [])
        data.setdefault("quality_risk_any", [])
        data.setdefault("min_ai_score", MIN_AI_SCORE_TO_SEND)
        data.setdefault("message_to_seller_pl", "Dzień dobry, czy oferta jest aktualna i czy przedmiot jest w pełni sprawny?")
        return sanitize_ai_filter(keyword, data)
    except Exception as e:
        logger.error("AI filter generation failed: %s", e)
        return fallback_filter(keyword, max_price)


def passes_dynamic_ai_filter(raw: Dict[str, Any], search: Dict[str, Any]) -> Tuple[bool, str]:
    profile = get_filter_profile(search)
    if not profile:
        # Old searches without generated filter still work with old generic filtering.
        return passes_product_profile_filter(raw, search.get("keyword", ""))

    text = item_searchable_text(raw)
    title = str(get_first_existing(raw, ["title", "name", "itemTitle", "productTitle"], "")).lower()
    combined = f"{title} | {text}".lower()

    accessory_reason = accessory_only_reason(raw, profile, search.get("keyword", ""))
    if accessory_reason:
        return False, accessory_reason

    if SIMPLE_FILTER_MODE:
        # Do not block by required_groups / wrong_product_any in Simple Mode.
        # Vinted query + price + recency decide the candidate set; AI decides quality/deal score.
        return True, "v8 simple mode: passed to AI scoring"

    # Hard-reject only clear wrong products and explicit reject words.
    # Single accessory words like pasek/ładowarka/pudełko are removed from hard rejects,
    # because they may simply mean accessories are included with the main item.
    reject_words = remove_soft_accessory_terms(as_list(profile.get("reject_any"))) + as_list(profile.get("wrong_product_any"))
    bad = text_contains_any(combined, reject_words)
    if bad:
        return False, f"AI DB filter rejected keyword: {bad}"

    ok, reason = text_contains_all_groups(combined, profile.get("required_groups"))
    if not ok:
        return False, f"AI DB filter: {reason}"

    # include_any is optional; it boosts confidence but does not block by itself.
    return True, "AI DB filter ok"

# =========================
# DATABASE
# =========================

def ensure_user(telegram_id: str) -> None:
    existing = supabase.table("users").select("id").eq("telegram_id", telegram_id).execute()

    if existing.data:
        return

    supabase.table("users").insert({
        "telegram_id": telegram_id,
    }).execute()




def find_duplicate_active_search(telegram_id: str, keyword: str, max_price: Optional[float]) -> Optional[Dict[str, Any]]:
    """Return an existing active search if the same user already has the same keyword and price."""
    try:
        result = (
            supabase.table("searches")
            .select("*")
            .eq("telegram_id", telegram_id)
            .eq("active", True)
            .execute()
        )
    except Exception as e:
        logger.warning("Could not check duplicate searches: %s", e)
        return None

    target_keyword = normalize_search_keyword(keyword)
    for row in result.data or []:
        if normalize_search_keyword(row.get("keyword")) == target_keyword and same_price(row.get("max_price"), max_price):
            return row
    return None


def clear_active_searches(telegram_id: str) -> int:
    """Deactivate all active searches for one Telegram chat. Sent-item history is kept."""
    active = get_active_searches(telegram_id)
    if not active:
        return 0

    supabase.table("searches").update({"active": False}).eq("telegram_id", telegram_id).eq("active", True).execute()
    return len(active)

def add_search(telegram_id: str, keyword: str, max_price: Optional[float]) -> Dict[str, Any]:
    duplicate = find_duplicate_active_search(telegram_id, keyword, max_price)
    if duplicate:
        duplicate = dict(duplicate)
        duplicate["_already_exists"] = True
        return duplicate

    ai_filter = generate_filter_with_ai(keyword, max_price)
    vinted_query = str(ai_filter.get("vinted_query") or keyword).strip() or keyword
    min_ai_score = int(ai_filter.get("min_ai_score") or MIN_AI_SCORE_TO_SEND)

    payload = {
        "telegram_id": telegram_id,
        "keyword": keyword,
        "vinted_query": vinted_query,
        "max_price": max_price,
        "country": "pl",
        "active": True,
        "filter_json": ai_filter,
        "filter_summary": str(ai_filter.get("filter_summary_ua") or ""),
        "min_ai_score": min_ai_score,
    }

    try:
        result = supabase.table("searches").insert(payload).execute()
    except Exception as e:
        logger.error("Could not insert dynamic filter search. Did you run the new supabase.sql migration? %s", e)
        # Fallback for old database schema. Search will still work, but filters will not be stored.
        result = supabase.table("searches").insert({
            "telegram_id": telegram_id,
            "keyword": keyword,
            "max_price": max_price,
            "country": "pl",
            "active": True,
        }).execute()

    return result.data[0]


def get_active_searches(telegram_id: Optional[str] = None) -> List[Dict[str, Any]]:
    query = supabase.table("searches").select("*").eq("active", True)

    if telegram_id:
        query = query.eq("telegram_id", telegram_id)

    result = query.order("created_at", desc=True).execute()
    return result.data or []


def get_search_by_id(search_id: int, telegram_id: str) -> Optional[Dict[str, Any]]:
    result = (
        supabase.table("searches")
        .select("*")
        .eq("id", search_id)
        .eq("telegram_id", telegram_id)
        .execute()
    )
    return result.data[0] if result.data else None


def deactivate_search(search_id: int, telegram_id: str) -> bool:
    existing = get_search_by_id(search_id, telegram_id)
    if not existing:
        return False

    supabase.table("searches").update({"active": False}).eq("id", search_id).eq("telegram_id", telegram_id).execute()
    return True


def regenerate_search_filter(search_id: int, telegram_id: str) -> Optional[Dict[str, Any]]:
    existing = get_search_by_id(search_id, telegram_id)
    if not existing:
        return None

    ai_filter = generate_filter_with_ai(existing.get("keyword", ""), existing.get("max_price"))
    payload = {
        "filter_json": ai_filter,
        "filter_summary": str(ai_filter.get("filter_summary_ua") or ""),
        "vinted_query": str(ai_filter.get("vinted_query") or existing.get("keyword", "")),
        "min_ai_score": int(ai_filter.get("min_ai_score") or MIN_AI_SCORE_TO_SEND),
    }
    supabase.table("searches").update(payload).eq("id", search_id).eq("telegram_id", telegram_id).execute()
    return ai_filter


def was_item_sent(telegram_id: str, item_id: str) -> bool:
    result = (
        supabase.table("sent_items")
        .select("id")
        .eq("telegram_id", telegram_id)
        .eq("item_id", item_id)
        .execute()
    )
    return bool(result.data)


def mark_item_sent(
    telegram_id: str,
    search_id: int,
    item_id: str,
    url: str,
    item_json: Optional[Dict[str, Any]] = None,
    ai_json: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    payload = {
        "telegram_id": telegram_id,
        "search_id": search_id,
        "item_id": item_id,
        "url": url,
        "item_json": item_json or {},
        "ai_json": ai_json or {},
        "ai_score": int((ai_json or {}).get("score") or 0),
        "deal_score": int((ai_json or {}).get("deal_score") or 0),
    }

    try:
        result = supabase.table("sent_items").insert(payload).execute()
        if result.data:
            return int(result.data[0]["id"])
    except Exception as e:
        logger.warning("Could not mark item as sent with extended columns: %s", e)
        # Fallback for older DB schema. This should only happen if supabase.sql was not run.
        try:
            result = supabase.table("sent_items").insert({
                "telegram_id": telegram_id,
                "search_id": search_id,
                "item_id": item_id,
                "url": url,
            }).execute()
            if result.data:
                return int(result.data[0]["id"])
        except Exception:
            logger.warning("Could not mark item as sent, probably duplicate.")

    return None


def get_sent_item(sent_item_id: int, telegram_id: str) -> Optional[Dict[str, Any]]:
    try:
        result = (
            supabase.table("sent_items")
            .select("*")
            .eq("id", sent_item_id)
            .eq("telegram_id", telegram_id)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error("Could not read sent item: %s", e)
        return None


def save_offer_feedback(
    telegram_id: str,
    search_id: int,
    sent_item_id: int,
    feedback_type: str,
    note: str = "",
) -> None:
    payload = {
        "telegram_id": telegram_id,
        "search_id": search_id,
        "sent_item_id": sent_item_id,
        "feedback_type": feedback_type,
        "note": note,
    }
    try:
        supabase.table("offer_feedback").upsert(
            payload,
            on_conflict="telegram_id,sent_item_id"
        ).execute()
    except Exception as e:
        logger.error("Could not save feedback. Did you run supabase.sql? %s", e)


def save_filter_learning_log(
    telegram_id: str,
    search_id: int,
    sent_item_id: int,
    feedback_type: str,
    old_filter: Dict[str, Any],
    new_filter: Dict[str, Any],
    summary: str,
) -> None:
    try:
        supabase.table("filter_learning_logs").insert({
            "telegram_id": telegram_id,
            "search_id": search_id,
            "sent_item_id": sent_item_id,
            "feedback_type": feedback_type,
            "old_filter_json": old_filter,
            "new_filter_json": new_filter,
            "summary": summary,
        }).execute()
    except Exception as e:
        logger.warning("Could not save learning log: %s", e)


# =========================
# VINTED DIRECT API
# =========================

class VintedFetchError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def init_vinted_session() -> None:
    """
    Initializes cookies. Vinted often requires a normal page request before API calls.
    """
    try:
        response = vinted_session.get(
            f"{VINTED_BASE_URL}/catalog",
            timeout=20,
            headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        logger.info("Initialized Vinted session: status=%s", response.status_code)
    except Exception as e:
        logger.warning("Could not initialize Vinted session: %s", e)


def build_vinted_params(keyword: str, max_price: Optional[float]) -> Dict[str, Any]:
    """
    Direct Vinted catalog API params.
    We keep price filtering local because Vinted parameter names can change.
    """
    return {
        "search_text": keyword,
        "order": "newest_first",
        "per_page": MAX_ITEMS_PER_SEARCH,
        "page": 1,
        "locale": VINTED_LOCALE,
        "currency": VINTED_CURRENCY,
    }


def detect_vinted_block(response: requests.Response) -> Optional[str]:
    text = (response.text or "")[:2000].lower()

    if response.status_code in [401, 403, 429]:
        return f"HTTP {response.status_code}"

    if "captcha" in text or "cf-challenge" in text or "cloudflare" in text:
        return "captcha/cloudflare detected"

    if "access denied" in text or "forbidden" in text:
        return "access denied"

    return None




def compact_query(q: str) -> str:
    q = re.sub(r"[^\w\sąćęłńóśźżĄĆĘŁŃÓŚŹŻ.-]+", " ", str(q or "").lower())
    q = re.sub(r"\s+", " ", q).strip()
    return q


def build_vinted_query_variants(active_search: Dict[str, Any]) -> List[str]:
    """
    Build a few Vinted search_text variants.
    Important: Vinted text search can be narrower than the app UI. If we query only
    one exact string, good offers can be invisible to the bot before any AI filter runs.
    """
    base = compact_query(active_search.get("vinted_query") or active_search.get("keyword") or "")
    original = compact_query(active_search.get("keyword") or "")
    queries: List[str] = []

    def add(q: str) -> None:
        q = compact_query(q)
        if q and q not in queries:
            queries.append(q)

    add(base)
    add(original)

    text = f"{base} {original}".lower()

    # Apple Watch variants. Use broad query first so Vinted can return seller spelling variants.
    if "apple" in text and "watch" in text:
        if "se" in text:
            add("apple watch se")
            add("apple watch se 2")
            add("apple watch gen 2")
            add("apple watch")
        elif "10" in text or "series 10" in text or "s10" in text:
            add("apple watch series 10")
            add("apple watch 10")
            add("apple watch")
        elif "9" in text or "series 9" in text or "s9" in text:
            add("apple watch series 9")
            add("apple watch 9")
            add("apple watch")
        else:
            add("apple watch")

    # iPad variants. Broad iPad catches titles like "iPad 2022 10.9 256GB".
    if "ipad" in text or "i pad" in text:
        if "10" in text or "2022" in text:
            add("ipad 10")
            add("ipad 2022")
            add("ipad 10.9")
            add("ipad")
        elif "11" in text or "2025" in text or "a16" in text:
            add("ipad 11")
            add("ipad a16")
            add("ipad 2025")
            add("ipad")
        else:
            add("ipad")

    # Redmi Pad variants.
    if "redmi" in text and "pad" in text:
        add("redmi pad")
        add("redmi pad pro")
        add("xiaomi redmi pad")

    # Footwear / sneakers.
    if "nike" in text and "dunk" in text:
        add("nike dunk low" if "low" in text else "nike dunk")
        add("nike dunk")
    if "jordan" in text:
        add("air jordan")
        add("jordan")

    # Collectibles.
    if "funko" in text or "pop" in text:
        add("funko pop")
        if original and original != "funko pop":
            add(original)
    if "lego" in text:
        add("lego")

    # Generic broad fallback: first 2-3 meaningful words without price-like tokens.
    tokens = [t for t in re.split(r"\s+", original or base) if t and not re.fullmatch(r"\d{2,5}", t) and t not in {"do", "zl", "zł", "pln"}]
    if len(tokens) >= 2:
        add(" ".join(tokens[:3]))
        add(" ".join(tokens[:2]))

    if not BROAD_SEARCH_MODE:
        return queries[:1]

    return queries[:max(1, MAX_QUERY_VARIANTS)]


def direct_vinted_multi_request(active_search: Dict[str, Any], max_price: Optional[float]) -> Tuple[List[Dict[str, Any]], List[str]]:
    variants = build_vinted_query_variants(active_search)
    merged: List[Dict[str, Any]] = []
    seen: set = set()
    errors: List[str] = []

    for q in variants:
        try:
            raw_items = direct_vinted_request(q, max_price)
        except VintedFetchError as e:
            errors.append(f"{q}: {e.kind}")
            logger.warning("Vinted query variant failed: %s -> %s %s", q, e.kind, e.message)
            continue

        logger.info("Vinted query variant '%s' returned %s raw items", q, len(raw_items))
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            item_id = str(get_first_existing(item, ["id", "item_id", "url"], ""))
            if not item_id:
                title = str(get_first_existing(item, ["title", "name"], ""))
                price = str(get_first_existing(item, ["price", "price_amount"], ""))
                item_id = f"{title}|{price}"
            if item_id in seen:
                continue
            seen.add(item_id)
            item["_vinted_query_used"] = q
            merged.append(item)

    if not merged and errors:
        raise VintedFetchError("all_query_variants_failed", "; ".join(errors[:8]))

    return merged, variants

def direct_vinted_request(keyword: str, max_price: Optional[float]) -> List[Dict[str, Any]]:
    endpoint = f"{VINTED_BASE_URL}/api/v2/catalog/items"
    params = build_vinted_params(keyword, max_price)

    logger.info("Direct Vinted request: %s params=%s", endpoint, params)

    response = vinted_session.get(endpoint, params=params, timeout=30)

    block_reason = detect_vinted_block(response)
    if block_reason:
        # Try once with refreshed cookies.
        logger.warning("Vinted blocked or rate-limited request: %s. Refreshing session and retrying once.", block_reason)
        init_vinted_session()
        response = vinted_session.get(endpoint, params=params, timeout=30)
        block_reason = detect_vinted_block(response)
        if block_reason:
            raise VintedFetchError(
                "blocked",
                f"Vinted direct API blocked request ({block_reason}). Status={response.status_code}"
            )

    if response.status_code >= 500:
        raise VintedFetchError(
            "server_error",
            f"Vinted server error {response.status_code}: {response.text[:300]}"
        )

    if response.status_code >= 400:
        raise VintedFetchError(
            "http_error",
            f"Vinted HTTP error {response.status_code}: {response.text[:300]}"
        )

    try:
        data = response.json()
    except Exception:
        raise VintedFetchError(
            "bad_json",
            f"Vinted returned non-JSON response. First chars: {response.text[:300]}"
        )

    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            return items

        # Sometimes APIs wrap differently. Treat this as format break.
        keys = ", ".join(list(data.keys())[:15])
        raise VintedFetchError(
            "api_changed",
            f"Vinted API format changed or unexpected. JSON keys: {keys}"
        )

    if isinstance(data, list):
        return data

    raise VintedFetchError(
        "api_changed",
        f"Vinted API returned unexpected type: {type(data).__name__}"
    )


def normalize_vinted_direct_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize Vinted direct API item to our existing format.
    """
    item_id = get_first_existing(raw, ["id", "item_id"], "")
    title = get_first_existing(raw, ["title", "name"], "No title")

    url = get_first_existing(raw, ["url"], "")
    if url and isinstance(url, str) and url.startswith("/"):
        url = f"{VINTED_BASE_URL}{url}"
    if not url and item_id:
        # Fallback URL, not always perfect but usually usable.
        slug = re.sub(r"[^a-z0-9]+", "-", str(title).lower()).strip("-")
        url = f"{VINTED_BASE_URL}/items/{item_id}-{slug}"

    price_raw = get_first_existing(raw, ["price", "price_amount", "total_item_price"], None)
    price = normalize_price(price_raw)

    currency = VINTED_CURRENCY
    if isinstance(price_raw, dict):
        currency = str(get_first_existing(price_raw, ["currency_code", "currency"], VINTED_CURRENCY))

    brand = get_first_existing(raw, ["brand_title", "brand", "brand_name"], "")
    if isinstance(brand, dict):
        brand = get_first_existing(brand, ["title", "name"], "")

    size = get_first_existing(raw, ["size_title", "size"], "")
    status = get_first_existing(raw, ["status", "condition", "status_title"], "")

    description = get_first_existing(raw, ["description", "desc"], "")
    seller = get_first_existing(raw, ["user", "seller"], "")
    if isinstance(seller, dict):
        seller = get_first_existing(seller, ["login", "username", "name"], "")

    location = get_first_existing(raw, ["city", "location", "country"], "")

    photo = get_first_existing(raw, ["photo"], "")
    image = ""
    if isinstance(photo, dict):
        image = get_first_existing(photo, ["url", "full_size_url", "thumb_url"], "")
    else:
        image = get_first_existing(raw, ["image", "imageUrl", "thumbnail"], "")

    photos = get_first_existing(raw, ["photos"], [])
    images = []
    if isinstance(photos, list):
        for p in photos:
            if isinstance(p, dict):
                u = get_first_existing(p, ["url", "full_size_url", "thumb_url"], "")
                if u:
                    images.append(u)

    age_minutes, age_source = get_item_age_minutes(raw)

    return {
        "id": str(item_id or url),
        "title": str(title),
        "url": str(url),
        "price": price,
        "currency": str(currency),
        "brand": str(brand),
        "size": str(size),
        "condition": str(status),
        "description": str(description),
        "seller": str(seller),
        "location": str(location),
        "image": str(image),
        "images": images,
        "age_minutes": age_minutes,
        "age_source": age_source,
        "query_used": str(raw.get("_vinted_query_used", "")),
        "raw": raw,
    }


def fetch_vinted_items(keyword: str, max_price: Optional[float], search: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    active_search = search or {"keyword": keyword, "max_price": max_price}

    # v9: fetch multiple broad Vinted query variants.
    # This fixes the main issue where a perfect offer exists in the app,
    # but the exact API search_text does not return it.
    raw_items, used_queries = direct_vinted_multi_request(active_search, max_price)
    logger.info("Broad Vinted search variants used: %s; merged raw items=%s", used_queries, len(raw_items))

    filtered_recent = []
    skipped_old = 0
    skipped_unknown = 0
    skipped_quality = 0
    skipped_product = 0

    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue

        ok, reason = is_recent_item(raw_item)

        if not ok:
            if "unknown age" in reason:
                skipped_unknown += 1
            else:
                skipped_old += 1
            logger.info("Skipped item by age: %s", reason)
            continue

        quality_ok, quality_reason = passes_quality_filter(raw_item)
        if not quality_ok:
            skipped_quality += 1
            logger.info("Skipped item by quality: %s", quality_reason)
            continue

        product_ok, product_reason = passes_dynamic_ai_filter(raw_item, active_search)
        if not product_ok:
            skipped_product += 1
            logger.info("Skipped item by AI DB product filter: %s", product_reason)
            continue

        filtered_recent.append(raw_item)

    logger.info(
        "Direct Vinted filter: input=%s kept=%s skipped_old=%s skipped_unknown=%s skipped_quality=%s skipped_product=%s",
        len(raw_items), len(filtered_recent), skipped_old, skipped_unknown, skipped_quality, skipped_product
    )

    normalized = [normalize_vinted_direct_item(item) for item in filtered_recent if isinstance(item, dict)]

    if max_price:
        normalized = [
            item for item in normalized
            if item["price"] is not None and item["price"] <= float(max_price)
        ]

    return normalized

# =========================
# AI EVALUATION - GROQ
# =========================

def safe_json_loads(text: str) -> Dict[str, Any]:
    text = text.strip()

    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def evaluate_item_with_ai(item: Dict[str, Any], search: Dict[str, Any]) -> Dict[str, Any]:
    images_info = "немає даних про фото"
    images = item.get("images")
    if isinstance(images, list):
        images_info = f"кількість фото: {len(images)}"
    elif item.get("image"):
        images_info = "є мінімум одне фото"

    prompt = f"""
Ти AI-агент для оцінки оголошень з Vinted у Польщі.

ВАЖЛИВО:
- Ти НЕ бачиш фото напряму.
- Не відсікай автоматично хороший дешевий товар тільки через дрібні подряпини; познач це як ризик.
- Оцінюй товар відповідно до його категорії: техніка, взуття, одяг, колекційні фігурки/Funko/Lego, косметика, сумки тощо.
- Для техніки перевіряй правильну модель, блокування акаунта, стан екрана, батарею якщо доступно.
- Для взуття перевіряй оригінальність, модель, розмір якщо вказаний, стан підошви/верху.
- Для Funko/фігурок/Lego перевіряй оригінальність, комплектність, стан коробки, чи це не плакат/наклейка/одяг.
- Deal score — це не те саме, що безпека. Дешевий товар з дрібним ризиком може мати високий deal_score, але нижчий safety score.

Задача користувача:
- шукає: {search.get("keyword")}
- максимальна ціна: {search.get("max_price")} PLN

Оголошення:
- title: {item.get("title")}
- price: {item.get("price")} {item.get("currency")}
- brand: {item.get("brand")}
- size: {item.get("size")}
- condition: {item.get("condition")}
- seller: {item.get("seller")}
- location: {item.get("location")}
- age_minutes: {item.get("age_minutes")}
- images_info: {images_info}
- description: {item.get("description")}
- url: {item.get("url")}

Оціни оголошення.
score 0-10 = наскільки товар підходить і безпечний.
deal_score 0-10 = наскільки це вигідна ціна/угода для такого товару.

Ризики:
- Для техніки: uszkodzony / nie działa / części / iCloud / blokada / pęknięty ekran = сильний ризик.
- Для взуття: podróbka/fake/replika, dziura, odklejona podeszwa, дуже знищений стан = сильний ризик.
- Для Funko/фігурок/Lego: fake, brak pudełka, uszkodzone pudełko, niekompletne, braki = ризик, але не завжди автоматичний бан.
- ryski / drobne rysy = мʼякий ризик, не обовʼязково погана оферта.
- занадто короткий опис або мало фото = мʼякий/середній ризик.

Відповідай тільки JSON без markdown:
{{
  "score": 0-10,
  "deal_score": 0-10,
  "verdict": "дуже вигідна / хороша / нормальна / ризикована / не варто",
  "reason": "коротке пояснення українською",
  "deal_reason": "чому такий deal_score українською",
  "risk_flags": ["..."],
  "message_to_seller_pl": "коротке повідомлення польською до продавця"
}}
"""

    try:
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": "You are a careful marketplace deal evaluator. Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )

        content = completion.choices[0].message.content or "{}"
        result = safe_json_loads(content)

        return {
            "score": int(result.get("score", 0)),
            "deal_score": int(result.get("deal_score", result.get("score", 0))),
            "verdict": str(result.get("verdict", "н/д")),
            "reason": str(result.get("reason", "")),
            "deal_reason": str(result.get("deal_reason", "")),
            "risk_flags": result.get("risk_flags", []),
            "message_to_seller_pl": str(result.get("message_to_seller_pl", "")),
        }

    except Exception as e:
        logger.error("Groq AI evaluation failed: %s", e)
        return {
            "score": 5,
            "deal_score": 5,
            "verdict": "не вдалося повністю оцінити",
            "reason": "AI-оцінка Groq не спрацювала, але оголошення підходить під базовий фільтр.",
            "deal_reason": "Немає точної AI-оцінки вигідності.",
            "risk_flags": [],
            "message_to_seller_pl": "Dzień dobry, czy oferta jest nadal aktualna? Czy można prosić o więcej zdjęć i informacje o stanie technicznym?",
        }


# =========================
# TELEGRAM MESSAGE FORMAT
# =========================

def format_item_message(item: Dict[str, Any], ai: Dict[str, Any], search: Dict[str, Any]) -> str:
    risks = ai.get("risk_flags") or []
    risks_text = ", ".join([str(r) for r in risks]) if risks else "не знайдено"

    price_text = "н/д"
    if item.get("price") is not None:
        price_text = f'{item.get("price")} {item.get("currency") or "PLN"}'

    url = item.get("url") or ""

    age_text = "н/д"
    if item.get("age_minutes") is not None:
        age_text = f'{item.get("age_minutes")} хв тому'
    else:
        age_text = "Vinted не віддав точний час, але товар прийшов з newest_first"

    deal_reason = ai.get("deal_reason") or "—"

    message = f"""
🔥 <b>Нова оферта з Vinted</b>

🔎 <b>Пошук:</b> {escape(search.get("keyword"))}
📦 <b>Назва:</b> {escape(item.get("title"))}
💰 <b>Ціна:</b> {escape(price_text)}
🏷 <b>Бренд:</b> {escape(item.get("brand") or "н/д")}
📌 <b>Стан:</b> {escape(item.get("condition") or "н/д")}
🕒 <b>Додано:</b> {escape(age_text)}

🤖 <b>AI-оцінка:</b> {escape(ai.get("score"))}/10
📊 <b>Deal score:</b> {escape(ai.get("deal_score"))}/10
✅ <b>Вердикт:</b> {escape(ai.get("verdict"))}
🧠 <b>Причина:</b> {escape(ai.get("reason"))}
💸 <b>Чому такий deal score:</b> {escape(deal_reason)}
⚠️ <b>Ризики:</b> {escape(risks_text)}

✉️ <b>Написати продавцю:</b>
<code>{escape(ai.get("message_to_seller_pl"))}</code>
"""

    if url:
        message += f'\n🔗 <a href="{escape(url)}">Відкрити оголошення</a>'

    return message.strip()


def feedback_keyboard(sent_item_id: Optional[int]) -> Optional[InlineKeyboardMarkup]:
    if not sent_item_id:
        return None
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👍 Хороша", callback_data=f"fb|{sent_item_id}|good"),
            InlineKeyboardButton("👎 Погана", callback_data=f"fb|{sent_item_id}|bad"),
        ],
        [
            InlineKeyboardButton("🚫 Не той товар", callback_data=f"fb|{sent_item_id}|wrong"),
            InlineKeyboardButton("💸 Не вигідно", callback_data=f"fb|{sent_item_id}|expensive"),
        ],
        [InlineKeyboardButton("⚠️ Підозріло", callback_data=f"fb|{sent_item_id}|suspicious")],
    ])


# =========================
# DEAL CHECKER
# =========================

async def process_search(
    application: Application,
    search: Dict[str, Any],
    manual: bool = False,
) -> int:
    telegram_id = str(search["telegram_id"])
    search_id = int(search["id"])
    keyword = search["keyword"]
    max_price = search.get("max_price")

    sent_count = 0

    try:
        items = fetch_vinted_items(keyword, max_price, search)

        if not items:
            if manual:
                await application.bot.send_message(
                    chat_id=telegram_id,
                    text=(
                        f"Нічого свіжого не знайшов по пошуку: {keyword}\n"
                        f"Фільтр: останні {ONLY_RECENT_MINUTES} хв, max items: {MAX_ITEMS_PER_SEARCH}. Якщо Vinted не віддав час — newest_first."
                    ),
                )
            return 0

        for item in items:
            item_id = item.get("id") or item.get("url")

            if not item_id:
                continue

            if was_item_sent(telegram_id, str(item_id)):
                continue

            ai = evaluate_item_with_ai(item, search)
            score = int(ai.get("score", 0))
            deal_score = int(ai.get("deal_score", 0))
            min_score = int(search.get("min_ai_score") or get_filter_profile(search).get("min_ai_score") or MIN_AI_SCORE_TO_SEND)

            if score < min_score:
                logger.info("Skipped low AI score item WITHOUT marking sent: %s score=%s min=%s", item.get("title"), score, min_score)
                continue

            if MIN_DEAL_SCORE_TO_SEND and deal_score < MIN_DEAL_SCORE_TO_SEND:
                logger.info("Skipped low deal score item WITHOUT marking sent: %s deal_score=%s min=%s", item.get("title"), deal_score, MIN_DEAL_SCORE_TO_SEND)
                continue

            sent_item_id = mark_item_sent(
                telegram_id,
                search_id,
                str(item_id),
                item.get("url") or "",
                item_json=item,
                ai_json=ai,
            )

            msg = format_item_message(item, ai, search)

            await application.bot.send_message(
                chat_id=telegram_id,
                text=msg,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
                reply_markup=feedback_keyboard(sent_item_id),
            )

            sent_count += 1
            time.sleep(0.5)

    except VintedFetchError as e:
        logger.error("Vinted fetch error: %s %s", e.kind, e.message)
        logger.error(traceback.format_exc())

        if manual:
            await application.bot.send_message(
                chat_id=telegram_id,
                text=f"⚠️ Проблема з Vinted direct при пошуку '{keyword}': {e.kind}\n{e.message}",
            )
        else:
            raise

    except Exception as e:
        logger.error("Search processing error: %s", e)
        logger.error(traceback.format_exc())

        if manual:
            await application.bot.send_message(
                chat_id=telegram_id,
                text=f"Помилка при перевірці пошуку '{keyword}': {e}",
            )
        else:
            raise

    return sent_count


async def send_health_alert(application: Application, telegram_id: str, title: str, details: str) -> None:
    """
    Notify user when direct Vinted access probably broke.
    Cooldown prevents spam.
    """
    global LAST_HEALTH_ALERT_TS

    now = time.time()
    if now - LAST_HEALTH_ALERT_TS < HEALTH_ALERT_COOLDOWN_SECONDS:
        return

    LAST_HEALTH_ALERT_TS = now

    text = f"""
⚠️ <b>Vinted bot health alert</b>

<b>{escape(title)}</b>

{escape(details)}

Ймовірно, треба втрутитись: Vinted міг дати 403/captcha, змінити API або повертати порожні результати.
"""

    try:
        await application.bot.send_message(
            chat_id=telegram_id,
            text=text.strip(),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.error("Failed to send health alert: %s", e)


async def scheduled_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    global EMPTY_ALL_SEARCHES_CYCLES

    application = context.application
    searches = get_active_searches()

    if not searches:
        logger.info("No active searches.")
        return

    logger.info("Scheduled check started. Searches: %s", len(searches))

    total_sent = 0
    had_vinted_error = False
    all_searches_empty_or_failed = True

    # Use first user's telegram_id as alert recipient.
    alert_telegram_id = str(searches[0]["telegram_id"])

    for search in searches:
        try:
            sent = await process_search(application, search, manual=False)
            total_sent += sent

            # If no exception, at least direct call worked.
            # Empty detection is approximate; /debug can confirm.
            all_searches_empty_or_failed = False

        except VintedFetchError as e:
            had_vinted_error = True
            logger.error("Vinted health error during scheduled check: %s %s", e.kind, e.message)
            await send_health_alert(
                application,
                alert_telegram_id,
                f"Vinted direct error: {e.kind}",
                e.message,
            )
        except Exception as e:
            had_vinted_error = True
            logger.error("Unexpected scheduled check error: %s", e)
            logger.error(traceback.format_exc())
            await send_health_alert(
                application,
                alert_telegram_id,
                "Unexpected bot error",
                str(e),
            )

    if all_searches_empty_or_failed and not had_vinted_error:
        EMPTY_ALL_SEARCHES_CYCLES += 1
    else:
        EMPTY_ALL_SEARCHES_CYCLES = 0

    if EMPTY_ALL_SEARCHES_CYCLES >= EMPTY_ALERT_THRESHOLD_CYCLES:
        await send_health_alert(
            application,
            alert_telegram_id,
            "Vinted повертає порожні результати",
            f"Уже {EMPTY_ALL_SEARCHES_CYCLES} циклів підряд усі пошуки виглядають порожніми. Можливо, Vinted змінив API або блокує запити.",
        )

    logger.info("Scheduled check finished. Sent: %s", total_sent)


# =========================
# COMMAND HANDLERS
# =========================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat

    if not user or not chat:
        return

    telegram_id = str(chat.id)
    ensure_user(telegram_id)

    text = f"""
👋 Привіт! Я <b>Vinted AI Deal Hunter</b>.

Я шукаю свіжі оферти напряму на Vinted без Apify, оцінюю їх через Groq AI, рахую Deal score і вчуся на твоєму фідбеку.

<b>Зараз фільтр:</b>
оголошення приблизно за останні <b>{ONLY_RECENT_MINUTES} хв.</b>\nТакож відсікаю ризики: <b>Zadowalający, uszkodzony, pęknięty, iCloud/Apple ID lock</b>\nІ відкидаю аксесуари: <b>etui, folia, szkło, ładowarka, pasek</b>\nФільтр спрощений: перевіряю назву + опис + бренд, а фінальну оцінку дає AI.

<b>Команди:</b>

/add ipad до 1200
/add iphone 13 | 1000
/list
/delete ID
/clear
/check
/filter ID
/refreshfilter ID
/debugsearch ID
/debug ipad
/help

Під кожною офертою будуть кнопки фідбеку: 👍 👎 🚫 💸 ⚠️. Так бот поступово покращує filter_json.

<b>Приклад:</b>
<code>/add ipad 10 генерації до 1200</code>
"""

    await update.message.reply_text(text.strip(), parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = f"""
<b>Як користуватись:</b>

1. Додати пошук:
<code>/add ipad до 1200</code>

2. Подивитися активні пошуки:
<code>/list</code>

3. Видалити один пошук:
<code>/delete 3</code>

4. Очистити всі активні пошуки:
<code>/clear</code>

5. Перевірити вручну:
<code>/check</code>

6. Подивитись AI-фільтр у базі:
<code>/filter 3</code>

7. Перегенерувати AI-фільтр:
<code>/refreshfilter 3</code>
<code>/debugsearch 3</code> — показати, чому останні raw-офери проходять або відсікаються

<b>v9 Broad Search:</b> бот шукає кількома варіантами запиту, наприклад apple watch se 2 + apple watch se + apple watch, щоб не пропускати різні написання продавців.

8. Тест Vinted direct:
<code>/debug ipad</code>

<b>Фільтр свіжості:</b>
останні {ONLY_RECENT_MINUTES} хв.

<b>Формати /add:</b>
<code>/add ipad до 1200</code>
<code>/add iphone 13 | 1000</code>
<code>/add apple watch se max 500</code>
"""

    await update.message.reply_text(text.strip(), parse_mode=ParseMode.HTML)


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.message

    if not chat or not message:
        return

    telegram_id = str(chat.id)
    ensure_user(telegram_id)

    keyword, max_price = parse_add_command(message.text or "")

    if not keyword:
        await message.reply_text(
            "Напиши так:\n/add ipad до 1200\nабо\n/add iphone 13 | 1000"
        )
        return

    await message.reply_text("🤖 Перевіряю дублікати і створюю AI-фільтр, якщо такого пошуку ще немає...")
    created = add_search(telegram_id, keyword, max_price)

    price_text = f"до {max_price} PLN" if max_price else "без ліміту ціни"
    profile = get_filter_profile(created)
    summary = profile.get("filter_summary_ua") or created.get("filter_summary") or "AI-фільтр створено."
    vinted_query = created.get("vinted_query") or profile.get("vinted_query") or keyword

    if created.get("_already_exists"):
        await message.reply_text(
            f"ℹ️ Такий активний пошук уже є: #{created['id']}\n"
            f"🔎 Твій запит: {created.get('keyword') or keyword}\n"
            f"🔍 Запит для Vinted: {vinted_query}\n"
            f"💰 {price_text}\n\n"
            f"Я не створював дубль. Подивитись правила: /filter {created['id']}",
        )
        return

    await message.reply_text(
        f"✅ Додав пошук #{created['id']}:\n"
        f"🔎 Твій запит: {keyword}\n"
        f"🔍 Запит для Vinted: {vinted_query}\n"
        f"💰 {price_text}\n"
        f"🧠 AI-фільтр: {summary}\n"
        f"🕒 Тільки останні {ONLY_RECENT_MINUTES} хв.\n\n"
        f"Подивитись правила: /filter {created['id']}\n"
        f"Перевірити вручну: /check",
    )


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.message

    if not chat or not message:
        return

    telegram_id = str(chat.id)
    searches = get_active_searches(telegram_id)

    if not searches:
        await message.reply_text("У тебе поки немає активних пошуків. Додай: /add ipad до 1200")
        return

    lines = [f"📋 <b>Твої активні пошуки:</b>\n🕒 Фільтр: останні {ONLY_RECENT_MINUTES} хв.\n"]

    for s in searches:
        price = s.get("max_price")
        price_text = f"до {price} PLN" if price else "без ліміту"
        vq = s.get("vinted_query") or get_filter_profile(s).get("vinted_query") or s.get("keyword")
        lines.append(
            f"#{s['id']} — <b>{escape(s['keyword'])}</b> — {escape(price_text)}\n"
            f"   🔍 Vinted: <code>{escape(vq)}</code>"
        )

    lines.append("\nВидалити один: <code>/delete ID</code>\nОчистити всі: <code>/clear</code>")

    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.message

    if not chat or not message:
        return

    telegram_id = str(chat.id)
    args = context.args

    if not args or not args[0].isdigit():
        await message.reply_text("Напиши так: /delete 3")
        return

    search_id = int(args[0])
    ok = deactivate_search(search_id, telegram_id)

    if not ok:
        await message.reply_text("Не знайшов такого активного пошуку.")
        return

    await message.reply_text(f"🗑 Видалив пошук #{search_id}")




async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.message

    if not chat or not message:
        return

    telegram_id = str(chat.id)
    ensure_user(telegram_id)

    count = clear_active_searches(telegram_id)
    if count == 0:
        await message.reply_text("У тебе не було активних пошуків для очищення.")
        return

    await message.reply_text(
        f"🧹 Очистив активні пошуки: {count}.\n"
        "Історію вже надісланих оферт не чіпав, щоб не спамити старими оголошеннями."
    )

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.message

    if not chat or not message:
        return

    telegram_id = str(chat.id)
    searches = get_active_searches(telegram_id)

    if not searches:
        await message.reply_text("У тебе немає активних пошуків. Додай: /add ipad до 1200")
        return

    await message.reply_text(f"🔍 Перевіряю Vinted. Вікно свіжості: останні {ONLY_RECENT_MINUTES} хв. Беру до {MAX_ITEMS_PER_SEARCH} оголошень на пошук...")

    total_sent = 0

    for search in searches:
        sent = await process_search(context.application, search, manual=True)
        total_sent += sent

    if total_sent == 0:
        await message.reply_text("Поки не знайшов нових нормальних свіжих оферт.")
    else:
        await message.reply_text(f"✅ Готово. Нових оферт: {total_sent}")


async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.message

    if not chat or not message:
        return

    keyword = " ".join(context.args).strip() or "ipad"

    await message.reply_text(f"Тестую Vinted direct по запиту: {keyword}")

    try:
        raw_items = direct_vinted_request(keyword, None)

        if not raw_items:
            await message.reply_text("Vinted direct повернув 0 raw items.")
            return

        first_raw = raw_items[0]
        first = normalize_vinted_direct_item(first_raw)
        ok, reason = is_recent_item(first_raw)
        quality_ok, quality_reason = passes_quality_filter(first_raw)
        product_ok, product_reason = passes_product_profile_filter(first_raw, keyword)

        text = f"""
<b>Перший item:</b>

title: {escape(first.get("title"))}
price: {escape(first.get("price"))}
url: {escape(first.get("url"))}
brand: {escape(first.get("brand"))}
condition: {escape(first.get("condition"))}
age_minutes: {escape(first.get("age_minutes"))}
recent_filter: {escape(ok)}
recent_reason: {escape(reason)}
quality_filter: {escape(quality_ok)}
quality_reason: {escape(quality_reason)}
product_filter: {escape(product_ok)}
product_reason: {escape(product_reason)}
"""

        await message.reply_text(text.strip(), parse_mode=ParseMode.HTML)

    except VintedFetchError as e:
        await message.reply_text(
            f"⚠️ Vinted direct problem: {escape(e.kind)}\\n{escape(e.message)}",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await message.reply_text(f"Debug error: {e}")



async def debugsearch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show why latest raw Vinted items for one saved search are kept or skipped."""
    chat = update.effective_chat
    message = update.message
    if not chat or not message:
        return

    telegram_id = str(chat.id)
    if not context.args or not context.args[0].isdigit():
        await message.reply_text("Напиши так: /debugsearch 21")
        return

    search = get_search_by_id(int(context.args[0]), telegram_id)
    if not search:
        await message.reply_text("Не знайшов такого пошуку.")
        return

    keyword = str(search.get("vinted_query") or search.get("keyword") or "").strip()
    max_price = search.get("max_price")

    await message.reply_text(f"🔬 Діагностика #{search.get('id')}: {keyword}\nБеру raw items з Vinted і показую, що бот з ними робить.")

    try:
        raw_items = direct_vinted_request(keyword, max_price)
        if not raw_items:
            await message.reply_text("Vinted повернув 0 raw items. Можливо, Vinted API тимчасово не віддає результати або запит занадто вузький.")
            return

        lines = [f"🔬 <b>Debug search #{escape(search.get('id'))}</b>", f"Raw items: {len(raw_items)}", ""]
        for idx, raw in enumerate(raw_items[:10], start=1):
            if not isinstance(raw, dict):
                continue
            item = normalize_vinted_direct_item(raw)
            recent_ok, recent_reason = is_recent_item(raw)
            quality_ok, quality_reason = passes_quality_filter(raw)
            product_ok, product_reason = passes_dynamic_ai_filter(raw, search)
            status = "✅ піде на AI" if (recent_ok and quality_ok and product_ok) else "⛔ буде пропущено"
            lines.append(
                f"<b>{idx}. {escape(item.get('title') or '—')}</b>\n"
                f"💰 {escape(item.get('price') or '—')} | 🕒 {escape(recent_reason)}\n"
                f"{status}\n"
                f"quality: {escape(quality_reason)}\n"
                f"product: {escape(product_reason)}\n"
            )

        text = "\n".join(lines)
        if len(text) > 3900:
            text = text[:3900] + "\n...обрізав, бо Telegram має ліміт повідомлення."
        await message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except VintedFetchError as e:
        await message.reply_text(f"⚠️ Vinted problem: {escape(e.kind)}\n{escape(e.message)}", parse_mode=ParseMode.HTML)
    except Exception as e:
        await message.reply_text(f"Debugsearch error: {e}")


async def filter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.message
    if not chat or not message:
        return

    telegram_id = str(chat.id)
    if not context.args or not context.args[0].isdigit():
        await message.reply_text("Напиши так: /filter 3")
        return

    search = get_search_by_id(int(context.args[0]), telegram_id)
    if not search:
        await message.reply_text("Не знайшов такого пошуку.")
        return

    profile = get_filter_profile(search)
    if not profile:
        await message.reply_text("У цього пошуку ще немає AI-фільтра в базі. Можеш створити: /refreshfilter ID")
        return

    def short_list(name: str, value: Any, limit: int = 20) -> str:
        items = []
        if isinstance(value, list):
            for x in value:
                if isinstance(x, list):
                    items.append(" / ".join([str(i) for i in x[:8]]))
                else:
                    items.append(str(x))
        text = ", ".join(items[:limit])
        return text or "—"

    text = f"""
🧠 <b>AI-фільтр пошуку #{escape(search.get('id'))}</b>

🔎 <b>Твій запит:</b> {escape(search.get('keyword'))}
🔍 <b>Vinted query:</b> <code>{escape(profile.get('vinted_query') or search.get('vinted_query') or search.get('keyword'))}</code>
💰 <b>Max price:</b> {escape(search.get('max_price') or 'без ліміту')}

<b>Опис:</b>
{escape(profile.get('filter_summary_ua') or search.get('filter_summary') or '—')}

<b>Обовʼязкові групи:</b>
{escape(short_list('required', profile.get('required_groups')))}

<b>Відсікаю:</b>
{escape(short_list('reject', profile.get('reject_any')))}

<b>Інші неправильні товари:</b>
{escape(short_list('wrong', profile.get('wrong_product_any')))}

<b>Ризики якості:</b>
{escape(short_list('risk', profile.get('quality_risk_any')))}

♻️ Перегенерувати: <code>/refreshfilter {escape(search.get('id'))}</code>
"""
    await message.reply_text(text.strip(), parse_mode=ParseMode.HTML)


async def refreshfilter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.message
    if not chat or not message:
        return

    telegram_id = str(chat.id)
    if not context.args or not context.args[0].isdigit():
        await message.reply_text("Напиши так: /refreshfilter 3")
        return

    search_id = int(context.args[0])
    await message.reply_text("♻️ Перегенеровую AI-фільтр і записую в базу...")

    profile = regenerate_search_filter(search_id, telegram_id)
    if not profile:
        await message.reply_text("Не знайшов такого пошуку.")
        return

    await message.reply_text(
        f"✅ AI-фільтр оновлено для пошуку #{search_id}.\n"
        f"🔍 Vinted query: {profile.get('vinted_query')}\n"
        f"🧠 {profile.get('filter_summary_ua')}\n\n"
        f"Подивитись правила: /filter {search_id}"
    )


# =========================
# FEEDBACK LEARNING
# =========================

def build_feedback_learning_prompt(search: Dict[str, Any], sent_item: Dict[str, Any], feedback_type: str) -> str:
    old_filter = get_filter_profile(search)
    item = sent_item.get("item_json") or {}
    ai = sent_item.get("ai_json") or {}
    feedback_label = FEEDBACK_TYPES.get(feedback_type, feedback_type)

    return f"""
Ти оновлюєш JSON-фільтр для Vinted-бота після фідбеку користувача.

Пошук користувача:
- keyword: {search.get('keyword')}
- max_price: {search.get('max_price')}

Старий filter_json:
{json.dumps(old_filter, ensure_ascii=False)}

Оголошення, на яке користувач дав фідбек:
- feedback: {feedback_label}
- title: {item.get('title')}
- price: {item.get('price')} {item.get('currency')}
- brand: {item.get('brand')}
- condition: {item.get('condition')}
- description: {item.get('description')}
- ai_score: {ai.get('score')}
- deal_score: {ai.get('deal_score')}
- ai_reason: {ai.get('reason')}
- risks: {ai.get('risk_flags')}

Задача:
- Якщо feedback = good, не звужуй фільтр. Можеш тільки покращити summary.
- Якщо feedback = wrong, додай точні ознаки неправильного товару в wrong_product_any або reject_any.
- Якщо feedback = bad/suspicious, додай ризикові слова в quality_risk_any або reject_any.
- Якщо feedback = expensive, не ламай фільтр товару; можеш трохи підвищити min_ai_score, але максимум до 6.
- Не додавай занадто загальні слова типу apple, ipad, watch, tablet у reject_any.
- Не вимагай гарантію/чек, якщо користувач прямо цього не просив.
- Не блокуй дрібні ryski автоматично; краще залиш це як risk для AI-оцінки.

Поверни ТІЛЬКИ валідний JSON:
{{
  "filter_json": {{...повний оновлений filter_json...}},
  "summary_ua": "коротко що змінилось"
}}
""".strip()


def learn_from_feedback(telegram_id: str, sent_item_id: int, feedback_type: str) -> str:
    sent_item = get_sent_item(sent_item_id, telegram_id)
    if not sent_item:
        return "Не знайшов цю оферту в базі."

    search_id = int(sent_item.get("search_id") or 0)
    search = get_search_by_id(search_id, telegram_id)
    if not search:
        return "Оферту знайшов, але активний пошук уже не існує або не належить цьому Telegram ID."

    save_offer_feedback(telegram_id, search_id, sent_item_id, feedback_type)

    if not FEEDBACK_LEARNING_ENABLED:
        return "Фідбек збережено, але автонавчання вимкнене через FEEDBACK_LEARNING_ENABLED=false."

    if feedback_type == "good":
        return "Фідбек збережено. Фільтр не звужував, бо оферта хороша."

    old_filter = get_filter_profile(search)
    if not old_filter:
        return "Фідбек збережено, але для цього пошуку немає filter_json."

    try:
        completion = groq_client.chat.completions.create(
            model=FILTER_GENERATION_MODEL,
            messages=[
                {"role": "system", "content": "You update marketplace JSON filters from user feedback. Return valid JSON only."},
                {"role": "user", "content": build_feedback_learning_prompt(search, sent_item, feedback_type)},
            ],
            temperature=0.1,
        )
        data = safe_json_loads(completion.choices[0].message.content or "{}")
        new_filter = data.get("filter_json") or old_filter
        summary = str(data.get("summary_ua") or "Фільтр оновлено на основі фідбеку.")

        # Basic safety defaults.
        if not isinstance(new_filter, dict):
            return "Фідбек збережено, але AI повернув неправильний формат фільтра."
        new_filter.setdefault("vinted_query", old_filter.get("vinted_query") or search.get("vinted_query") or search.get("keyword"))
        new_filter.setdefault("filter_summary_ua", old_filter.get("filter_summary_ua") or search.get("filter_summary") or "AI-фільтр")
        new_filter.setdefault("required_groups", old_filter.get("required_groups") or [])
        new_filter.setdefault("include_any", old_filter.get("include_any") or [])
        new_filter.setdefault("reject_any", old_filter.get("reject_any") or [])
        new_filter.setdefault("wrong_product_any", old_filter.get("wrong_product_any") or [])
        new_filter.setdefault("quality_risk_any", old_filter.get("quality_risk_any") or [])
        new_filter.setdefault("min_ai_score", old_filter.get("min_ai_score") or MIN_AI_SCORE_TO_SEND)

        payload = {
            "filter_json": new_filter,
            "filter_summary": str(new_filter.get("filter_summary_ua") or summary),
            "vinted_query": str(new_filter.get("vinted_query") or search.get("vinted_query") or search.get("keyword")),
            "min_ai_score": int(new_filter.get("min_ai_score") or MIN_AI_SCORE_TO_SEND),
        }
        supabase.table("searches").update(payload).eq("id", search_id).eq("telegram_id", telegram_id).execute()
        save_filter_learning_log(telegram_id, search_id, sent_item_id, feedback_type, old_filter, new_filter, summary)
        return f"Фідбек збережено. {summary}"
    except Exception as e:
        logger.error("Feedback learning failed: %s", e)
        return f"Фідбек збережено, але автооновлення фільтра не вдалось: {e}"


async def feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()

    chat = query.message.chat if query.message else None
    if not chat:
        return

    telegram_id = str(chat.id)
    data = query.data or ""
    parts = data.split("|")
    if len(parts) != 3 or parts[0] != "fb" or not parts[1].isdigit():
        await query.message.reply_text("Не зрозумів фідбек.")
        return

    sent_item_id = int(parts[1])
    feedback_type = parts[2]
    if feedback_type not in FEEDBACK_TYPES:
        await query.message.reply_text("Невідомий тип фідбеку.")
        return

    result = learn_from_feedback(telegram_id, sent_item_id, feedback_type)
    await query.message.reply_text(f"✅ {FEEDBACK_TYPES[feedback_type]}\n{result}")

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

# =========================
# MAIN
# =========================

def main() -> None:
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add", add_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("delete", delete_command))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CommandHandler("filter", filter_command))
    application.add_handler(CommandHandler("refreshfilter", refreshfilter_command))
    application.add_handler(CommandHandler("debug", debug_command))
    application.add_handler(CommandHandler("debugsearch", debugsearch_command))
    application.add_handler(CallbackQueryHandler(feedback_callback, pattern=r"^fb\|"))

    application.job_queue.run_repeating(
        scheduled_check,
        interval=CHECK_INTERVAL_SECONDS,
        first=30,
        name="scheduled_vinted_check",
    )

    init_vinted_session()
    logger.info("Bot started in DIRECT VINTED mode. Freshness filter: ONLY_RECENT_MINUTES=%s SKIP_UNKNOWN_AGE=%s", ONLY_RECENT_MINUTES, SKIP_UNKNOWN_AGE)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
