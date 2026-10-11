#!/usr/bin/env python3
"""World Wonders - Facebook post bot (tourist attractions). Text via Cloudflare Workers AI / a pool of OpenRouter keys, images via Cloudflare Workers AI (FLUX.2 klein 4B)."""
import base64
import hashlib
import io
import json
import math
import os
import random
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

CF_ACCOUNT = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
CF_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
CF_MODEL = os.environ.get("CF_IMAGE_MODEL", "").strip() or "@cf/black-forest-labs/flux-2-klein-4b"
# text + image QA on Cloudflare too (same 10,000 free neurons per day); OpenRouter models stay as fallback
CF_TEXT_MODEL = os.environ.get("CF_TEXT_MODEL", "").strip() or "@cf/google/gemma-4-26b-a4b-it"
CF_CHECKER_MODEL = os.environ.get("CF_CHECKER_MODEL", "").strip() or "@cf/qwen/qwen3-30b-a3b-fp8"
CF_TEXT = os.environ.get("CF_TEXT", "1").strip() == "1" and bool(CF_ACCOUNT and CF_TOKEN)
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
def _models(name, default):
    return [m.strip() for m in os.environ.get(name, default).split(",") if m.strip()]


# comma-separated lists: the first model is tried first, the next ones are fallbacks
WRITER_MODELS = _models("WRITER_MODEL", "")    # empty = automatic: best free models
CHECKER_MODELS = _models("CHECKER_MODEL", "")  # empty = automatic: a different free model family
IMAGE_MODELS = [CF_MODEL]
PAGE_NAME = os.environ.get("PAGE_NAME", "Places To Withness")   # printed on the picture: use Latin letters
SEND_NOTES = os.environ.get("SEND_NOTES", "0") == "1"
WATERMARK = os.environ.get("WATERMARK", "AI-generated Image")

# Facebook Page posting (optional: runs only when both values are set)
FB_PAGE_ID = os.environ.get("FB_PAGE_ID", "").strip()
FB_PAGE_TOKEN = os.environ.get("FB_PAGE_TOKEN", "").strip()
FB_VERSION = os.environ.get("FB_GRAPH_VERSION", "v26.0").strip()
FB_DISCLOSURE = os.environ.get("FB_DISCLOSURE", "")
try:
    FB_EVERY_HOURS = max(1, int(os.environ.get("FB_EVERY_HOURS", "1") or 1))
except ValueError:
    FB_EVERY_HOURS = 1

HERE = Path(__file__).parent
HISTORY = HERE / "history.json"
FONT_PATH = HERE / "fonts" / "Anton-Regular.ttf"
FALLBACK_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W, H = 1080, 1350          # final post size (4:5), photo fills the whole frame
GEN_W, GEN_H = 1024, 1280    # generate 4:5, then resize to the final size

RECENT_CUTOFF = 2100   # no age limit for tourist places (the old history rule is switched off)

WHITE, RED, GOLD = (255, 255, 255), (226, 38, 38), (240, 190, 90)

POST_LANG = os.environ.get("POST_LANG", "en").strip().lower()   # language of the caption: bn = Bengali, en = English
LANG_NAME = {"en": "English (English, written in English script)", "en": "English"}.get(POST_LANG, POST_LANG)
SKILL_FILE = HERE / "skills" / "fb_seo_writing.md"
BRAND_TAG = os.environ.get("BRAND_TAG", "#WorldWonders").strip()

# Topical map: 6 pillars, each a list of Wikipedia article titles (the spokes).
# The bot rotates through the pillars, so the page stays focused on one niche and balanced across its parts.
CLUSTERS = {
    "ancient-wonders": [
        "Machu Picchu", "Petra", "Angkor Wat", "Great Pyramid of Giza", "Colosseum", "Chichen Itza", "Stonehenge",
        "Acropolis of Athens", "Pompeii", "Persepolis", "Borobudur", "Teotihuacan", "Karnak", "Abu Simbel temples",
        "Valley of the Kings", "Mohenjo-daro", "Ajanta Caves", "Ellora Caves", "Sigiriya", "Bagan", "Hampi",
        "Konark Sun Temple", "Leptis Magna", "Ephesus", "Delphi", "Terracotta Army", "Moai", "Newgrange", "Tikal",
        "Great Zimbabwe", "Lalibela", "Mycenae", "Knossos", "Hadrian's Wall", "Pont du Gard", "Baalbek",
        "Carnac stones", "G\u00f6bekli Tepe", "Nazca Lines", "Ayutthaya Historical Park", "Somapura Mahavihara",
        "Sixty Dome Mosque", "Mahasthangarh", "Kantajew Temple", "Ancient Corinth", "Mesa Verde National Park",
    ],
    "natural-wonders": [
        "Grand Canyon", "Victoria Falls", "Niagara Falls", "Iguazu Falls", "Angel Falls", "Mount Everest", "Mount Fuji",
        "Matterhorn", "Uluru", "Giant's Causeway", "Cliffs of Moher", "Lake Baikal", "Lake Titicaca", "Salar de Uyuni",
        "Dead Sea", "Danakil Depression", "Pamukkale", "Cappadocia", "Waitomo Caves", "Antelope Canyon",
        "Mount Kilimanjaro", "Ngorongoro Crater", "Perito Moreno Glacier", "Zhangjiajie National Forest Park",
        "Geirangerfjord", "Milford Sound", "Plitvice Lakes National Park", "Jiuzhaigou Valley", "Fingal's Cave",
        "Socotra", "Mount Roraima", "Table Mountain", "Mount Kinabalu", "Fly Geyser", "Reed Flute Cave",
        "Son Doong Cave", "Zion National Park", "Bryce Canyon National Park", "Lake Louise (Alberta)", "Skaftafell",
    ],
    "iconic-landmarks": [
        "Eiffel Tower", "Statue of Liberty", "Big Ben", "Tower Bridge", "Sydney Opera House", "Burj Khalifa",
        "Golden Gate Bridge", "Brooklyn Bridge", "Leaning Tower of Pisa", "Trevi Fountain", "Charles Bridge",
        "Brandenburg Gate", "Christ the Redeemer (statue)", "Petronas Towers", "Marina Bay Sands", "Tokyo Skytree",
        "Great Wall of China", "Space Needle", "CN Tower", "Gateway of India", "Victoria Memorial, Kolkata",
        "Rialto Bridge", "Sydney Harbour Bridge", "Millau Viaduct", "Ponte Vecchio", "Atomium", "Prague Astronomical Clock",
        "Louvre", "Piazza San Marco", "Spanish Steps", "Sagrada Fam\u00edlia", "Park G\u00fcell", "Burj Al Arab",
        "Oriental Pearl Tower", "Tokyo Tower", "Auckland Sky Tower", "Table Bay", "Ahsan Manzil", "Shaheed Minar",
    ],
    "palaces-and-castles": [
        "Palace of Versailles", "Neuschwanstein Castle", "Alhambra", "Forbidden City", "Red Fort", "Amer Fort",
        "Mysore Palace", "Hawa Mahal", "Edinburgh Castle", "Prague Castle", "Windsor Castle", "Himeji Castle",
        "Topkap\u0131 Palace", "Sch\u00f6nbrunn Palace", "Peterhof Palace", "Ch\u00e2teau de Chambord", "Mont-Saint-Michel",
        "Kronborg", "Malbork Castle", "Hohenzollern Castle", "Bran Castle", "Lalbagh Fort", "Golconda", "Mehrangarh",
        "Jaisalmer Fort", "Gwalior Fort", "Potala Palace", "Gyeongbokgung", "Grand Palace (Bangkok)",
        "Alc\u00e1zar of Seville", "Pena Palace", "Bel\u00e9m Tower", "Buckingham Palace", "Eltz Castle", "Matsumoto Castle",
        "Chittorgarh Fort", "Rani ki Vav", "Fatehpur Sikri", "Qutb Minar", "Humayun's Tomb",
    ],
    "sacred-and-heritage": [
        "Taj Mahal", "Sheikh Zayed Grand Mosque", "Sultan Ahmed Mosque", "Shwedagon Pagoda", "Golden Temple",
        "Lotus Temple", "Kiyomizu-dera", "Fushimi Inari-taisha", "Wat Arun", "Meenakshi Temple", "St. Peter's Basilica",
        "Cologne Cathedral", "Milan Cathedral", "St Mark's Basilica", "Saint Basil's Cathedral", "Westminster Abbey",
        "Kinkaku-ji", "T\u014ddai-ji", "Temple of Heaven", "Jokhang", "Mahabodhi Temple", "Hassan II Mosque",
        "Shah Mosque (Isfahan)", "Sheikh Lotfollah Mosque", "Brihadisvara Temple, Thanjavur", "Khajuraho Group of Monuments",
        "Hampi", "Angkor Thom", "Wat Pho", "Haw Par Villa", "Sensoji", "Notre-Dame de Paris", "Chartres Cathedral",
        "Duomo di Firenze", "Florence Cathedral", "Pantheon, Rome", "Basilica Cistern", "Great Mosque of Djenn\u00e9",
        "Hagar Qim", "Itsukushima Shrine",
    ],
    "islands-and-parks": [
        "Santorini", "Bora Bora", "Phi Phi Islands", "Amalfi Coast", "Cinque Terre", "Ha Long Bay", "Galápagos Islands",
        "Great Barrier Reef", "Cox's Bazar", "Saint Martin's Island", "Sundarbans", "Ratargul Swamp Forest",
        "Banff National Park", "Yellowstone National Park", "Yosemite National Park", "Serengeti National Park",
        "Torres del Paine National Park", "Kruger National Park", "Lake Como", "Lake Bled", "Hallstatt", "Lofoten",
        "Faroe Islands", "Jeju Island", "Palawan", "Komodo National Park", "Raja Ampat Islands", "Whitsunday Islands",
        "Fiordland National Park", "Plitvice Lakes National Park", "Phang Nga Bay", "Maui", "Capri", "Mykonos",
        "Zanzibar", "Gal\u00e1pagos National Park", "Okavango Delta", "Etosha National Park", "Chitwan National Park",
        "Kaziranga National Park",
    ],
}

# backup searches, used only when the curated pillar has nothing left
CLUSTER_KEYWORDS = {
    "ancient-wonders": ["ancient ruins archaeological site", "UNESCO World Heritage archaeological site", "ancient temple complex", "rock-cut cave temples"],
    "natural-wonders": ["waterfall tourist attraction", "canyon national park", "glacier tourist attraction", "mountain tourist destination", "cave tourist attraction"],
    "iconic-landmarks": ["famous landmark tower", "famous bridge tourist attraction", "observation tower", "monument landmark city"],
    "palaces-and-castles": ["castle tourist attraction", "royal palace museum", "fort UNESCO World Heritage Site"],
    "sacred-and-heritage": ["famous temple tourist attraction", "cathedral tourist attraction", "mosque architecture landmark", "pagoda UNESCO"],
    "islands-and-parks": ["island tourist destination", "national park wildlife", "marine park coral reef", "beach destination"],
}

WRITER_BASE = """You are a travel-content writer, strict fact checker and Facebook SEO copywriter for a page about the best places to visit in the world: landmarks, natural wonders, heritage sites, palaces, islands and parks.
You create ORIGINAL Facebook posts, each about ONE place. The caption is written in <<LANG>>. The headline lines and the subhook (printed on the picture) are ALWAYS English capital letters.

SOURCE-GROUNDING RULES (absolute):
- You receive SOURCE TEXT from Wikipedia. Use ONLY facts that are stated in it. Never add a number, date, measurement, name, cause or "first / biggest / oldest / only / most visited" claim that the SOURCE TEXT does not state. Use no outside knowledge for facts.
- Write every number, year and measurement with ordinary digits 0-9, exactly as in the SOURCE TEXT. Do no arithmetic and no unit conversion (if the source says miles, write miles).
- Every proper name (place, person, dynasty, river, mountain, organisation) must appear in the SOURCE TEXT. If the output language is not English: write each place name in <<LANG>> followed by the English name in brackets the first time it appears, and the English name must always appear at least once, because people search for it.
- Pick the single most surprising concrete fact the SOURCE TEXT clearly states (a number, size, age, record, odd detail, a contrast) and build the post around it. Return {"skip": true, "reason": "..."} only if the article has no such fact at all.
- "evidence": 3 to 6 objects {"claim": "...", "quote": "..."} where quote is copied WORD FOR WORD from the SOURCE TEXT (6 to 30 words). Together they must cover EVERY number, year and name used in the headline and in the caption.
- Never put citation markers like [1] or URLs in any text field.
- Do not invent visitor information. Season, access, tickets, opening hours, safety, visa and price advice only if the SOURCE TEXT states it exactly.

TOPIC RULE:
- The post must be about a place a traveller can visit: a landmark, monument, site, natural attraction, park, island, palace, temple, bridge, tower, district.
- Return skip for articles about a person, an event, a war, a disaster, politics, a religious dispute, a disputed border, or a place that the source describes mainly through tragedy or violence, or that is closed to visitors.

<<SEO_RULES>>

IMAGE RULES:
- The picture shows the PLACE itself, accurate to what the SOURCE TEXT describes (its structure, material, setting, landscape, climate). The place is the clear hero, fully in frame. Natural daylight or golden hour. No people close to the camera, no writing, no signs, no logos, no brand names.
- Give TWO different visual ideas for the same place: image_prompt_a is the main picture, image_prompt_b is another angle or moment. Each is ONE vivid description of 40-70 words in English: the place FIRST, then the setting, the light, the weather, the mood.

HEADLINE RULES (printed on the picture, English):
- headline_lines: 2 or 3 ALL-CAPS lines forming ONE short curiosity hook, 8-12 words in total, each line MAX 28 characters. It teases a surprise and does NOT retell the whole fact. Use only a number or year that the SOURCE TEXT states. Never name living people.
- Examples: ["[2,000] YEARS OLD", "AND STILL HIDDEN FROM", "MOST MAPS"], ["A CITY CARVED", "INTO [ROSE-RED]", "CLIFFS"].
- Red highlight: wrap ONE key word or number (two short groups at most) in [square brackets]. The brackets hug the words. Never put a whole line in brackets. No parentheses.
- subhook: 3-6 words, ALL CAPS, a short teaser under the headline. It adds NO new fact.
- The headline must be truthful and supported by the source.

CAPTION RULES (write like a real person, not like an AI):
- 150-230 words in <<LANG>>. If the language is Bengali: natural, conversational modern Bengali (cholito bhasha), short sentences mixed with a few longer ones, no stiff translated phrasing, no stock phrases such as "প্রকৃতির লীলাভূমি", "স্বর্গের টুকরো", "অপরূপ সৌন্দর্য" unless the source says it.
- Structure: the direct-answer first line, then 3-5 short paragraphs separated by a blank line (write \\n\\n inside the JSON string), then ONE easy question about the place as the last line.
- Mix very short sentences (2-6 words) with long ones (25+ words). Use contractions and plain words. No lists of three adjectives, no moral at the end.
- Never use: delve, tapestry, testament, realm, pivotal, crucial, moreover, furthermore, additionally, notably, ultimately, breathtaking, nestled, stunning, "a must-visit", "hidden gem", "bucket list".
- Plain text only. No emojis, no markdown, no hashtags, no em dashes, no lists inside the caption.

JSON FORMAT: output valid JSON only. Inside string values write paragraph breaks as the escaped characters \\n (never real line breaks) and write every double quote inside text as \\" . No comments, no trailing commas.

Return ONLY one JSON object, no markdown fences, with keys:
skip, topic (the English name of the place, exactly as in the source title), country (English), fact, headline_lines (array), subhook, image_prompt_a, image_prompt_b, caption, hashtags (array of exactly 2), evidence (array described above)."""


def build_writer_system():
    """The writer prompt = grounding rules + the Facebook SEO/writing rules from skills/fb_seo_writing.md (edit that file to tune the style)."""
    try:
        rules = SKILL_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        rules = ""
        log("skills/fb_seo_writing.md not found: writing without the SEO rules")
    return WRITER_BASE.replace("<<SEO_RULES>>", rules).replace("<<LANG>>", LANG_NAME)


CHECKER_SYSTEM = """You are a strict fact checker. You receive SOURCE TEXT (from Wikipedia, in English) and a DRAFT social post (headline in English, caption possibly in another language: compare the MEANING).

Decide whether every factual claim in the draft's headline and caption is supported by the SOURCE TEXT.

Flag ONLY hard factual problems: a number, date, measurement, name or event that is not in the source; wrong cause and effect; "first / only / oldest / biggest / most visited" style claims the source does not make; numbers attached to the wrong thing; practical visitor advice (season, tickets, access, safety) that the source does not state.

Do NOT flag rhetorical questions, metaphors, transitions, mood, short summaries of what the source says, or the closing question. Wording like "stunning" is not a factual claim unless it changes the facts.

Do NOT use outside knowledge to approve a claim. If the source does not say it, it is unsupported.

Return ONLY one JSON object, no markdown fences:
{"faithful": true or false, "unsupported": ["..."], "hook_score": 1-10}

hook_score rates how strongly the headline would stop a Facebook scroller (10 = jaw-dropping specific number and contrast, 1 = vague travel-brochure line)."""

CHECKER_WEB_SYSTEM = CHECKER_SYSTEM.replace(
    "Do NOT use outside knowledge to approve a claim. If the source does not say it, it is unsupported.",
    "You also have web search. Confirm the key numbers, dates and names of the headline with reliable web sources "
    "(official tourism or heritage sites, UNESCO, museums, reputable encyclopedias or newspapers). "
    "If a reliable source contradicts the SOURCE TEXT on a number, date or name, mark the draft unfaithful and list the contradiction. "
    "A claim must still appear in the SOURCE TEXT to be approved.",
)

STYLE_SUFFIX = (
    "professional travel photograph, natural daylight or golden hour, vivid but natural colors, 35mm lens, "
    "the place fully visible, sharp and accurately depicted, no people close to the camera"
)

NEGATIVE_PROMPT = (
    "deformed, distorted, blurry, low resolution, text, letters, watermark, logo, cartoon, painting, "
    "3d render, oversaturated, crowd, close-up faces"
)

# ----------------------------------------------------------------- helpers
def log(*a):
    print(*a, flush=True)


def load_history():
    if HISTORY.exists():
        try:
            return json.loads(HISTORY.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_history(items):
    HISTORY.write_text(json.dumps(items[-400:], ensure_ascii=False, indent=1), encoding="utf-8")


def _json_candidates(text):
    """Yield every balanced top-level {...} block, ignoring braces inside strings."""
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth, in_str, esc, j = 0, False, False, i
        while j < n:
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    yield text[i : j + 1]
                    break
            j += 1
        i = j + 1 if depth == 0 and j < n else i + 1


def _parse_loose(candidate):
    attempts = [candidate]
    attempts.append(re.sub(r",\s*([}\]])", r"\1", candidate))  # trailing commas
    attempts.append(attempts[-1].replace("\u201c", '"').replace("\u201d", '"'))  # smart quotes used as delimiters
    for c in attempts:
        try:
            return json.loads(c, strict=False)  # strict=False allows raw line breaks inside strings
        except ValueError:
            continue
    return None


def extract_json(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    text = re.sub(r"```(?:json)?", "", text)
    if "{" not in text:
        raise ValueError("no JSON found")
    good = None
    for cand in _json_candidates(text):
        obj = _parse_loose(cand)
        if isinstance(obj, dict) and obj:
            good = obj  # keep the LAST valid object (final answer after any draft)
    if good is None:
        raise ValueError("invalid JSON (could not repair)")
    return good


# ---- OpenRouter key pool: many keys, round-robin, exhausted keys are parked automatically
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
KEYSTATE = HERE / "keystate.json"
WEB_CHECK = os.environ.get("WEB_CHECK", "0") == "1"


def _read_keys():
    raw = os.environ.get("OPENROUTER_API_KEYS", "") or os.environ.get("OPENROUTER_API_KEY", "")
    return [k.strip() for k in re.split(r"[\s,;]+", raw) if k.strip()]


OR_KEYS = _read_keys()
_KS = {"rr": 0, "keys": {}}


def _fp(key):
    return hashlib.sha256(key.encode()).hexdigest()[:8]


def load_keystate():
    """Nothing is remembered between runs: a key that was tired yesterday gets a fresh chance today."""
    global _KS
    _KS = {"rr": random.randrange(1000), "keys": {}, "demoted": {}}


def save_keystate():
    pass


def _parked(fp):
    return _KS["keys"].get(fp, {}).get("dead_until", 0) > time.time()


def _park(fp, seconds, reason):
    e = _KS["keys"].setdefault(fp, {})
    e["dead_until"] = int(time.time() + seconds)
    e["reason"] = reason


def _next_midnight():
    """Seconds until the next UTC midnight (+5 min), when OpenRouter's daily free limit resets."""
    now = datetime.now(timezone.utc)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return (nxt - now).total_seconds()


def keys_usable():
    return [k for k in OR_KEYS if not _parked(_fp(k))]


class ModelBusy(RuntimeError):
    """The model's provider is overloaded or rate-limited upstream. The key is fine; try another model."""


_BUSY = {}
_REASONING_MODELS = set()   # models whose catalogue entry supports a reasoning parameter
_NO_REASON_PARAM = set()   # models that rejected that parameter


# neurons per million tokens (input, output) from the Workers AI pricing page; used only for the usage estimate in the log
CF_RATES = {
    "@cf/google/gemma-4-26b-a4b-it": (9091, 27273),
    "@cf/qwen/qwen3-30b-a3b-fp8": (4625, 30475),
    "@cf/meta/llama-3.1-8b-instruct-fp8-fast": (4119, 34868),
    "@cf/openai/gpt-oss-20b": (18182, 27273),
    "@cf/openai/gpt-oss-120b": (31818, 68182),
}
_NEURONS = {"text": 0.0, "image": 0.0, "calls": 0}
_CF_KWARGS_REJECTED = [False]


def cf_call(messages, model, temperature=0.3, max_tokens=800):
    """One Workers AI chat request through the OpenAI-compatible endpoint (also takes image_url parts for vision models)."""
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/ai/v1/chat/completions"
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if not _CF_KWARGS_REJECTED[0]:
        body["chat_template_kwargs"] = {"enable_thinking": False}  # we want the answer, not paid hidden thinking
    last = None
    for _ in range(2):
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {CF_TOKEN}", "Content-Type": "application/json"},
                              json=body, timeout=150)
        except requests.RequestException as e:
            raise ModelBusy(f"{model} network error: {e}")
        text = r.text[:300]
        low = text.lower()
        if r.status_code == 200:
            try:
                data = r.json()
            except ValueError:
                raise RuntimeError(f"model rejected the request: not JSON: {text[:120]}")
            if isinstance(data, dict) and isinstance(data.get("result"), dict) and "choices" in data["result"]:
                data = data["result"]
            ch = (data.get("choices") or [{}])[0] if isinstance(data, dict) else {}
            msg = ch.get("message") or {}
            content = msg.get("content") or ""
            usage = data.get("usage") or {} if isinstance(data, dict) else {}
            pt = usage.get("prompt_tokens") or sum(len(str(m.get("content", ""))) for m in messages) // 4
            ct = usage.get("completion_tokens") or len(content) // 4
            rin, rout = CF_RATES.get(model, (9091, 27273))
            n = pt * rin / 1e6 + ct * rout / 1e6
            _NEURONS["text"] += n
            _NEURONS["calls"] += 1
            log(f"[cloudflare] {model} ok ({pt}+{ct} tokens, about {n:.0f} neurons)")
            if not content.strip():
                if ch.get("finish_reason") == "length":
                    raise RuntimeError(f"model rejected: {model} spent the whole {max_tokens}-token limit on hidden reasoning")
                content = msg.get("reasoning_content") or msg.get("reasoning") or ""
                if not content.strip():
                    raise RuntimeError(f"model rejected: {model} returned an empty answer")
            return content
        last = f"HTTP {r.status_code}: {text[:200]}"
        log(f"[cloudflare] {model} failed: {last}")
        if r.status_code == 429 or "neurons" in low or "daily free allocation" in low or "4006" in low:
            if "neurons" in low or "allocation" in low or "4006" in low:
                _BUSY[model] = time.time() + 86400
                raise ModelBusy(f"{model}: Workers AI daily free allocation used up")
            _BUSY[model] = time.time() + 120
            raise ModelBusy(f"{model} is rate limited ({last[:90]})")
        if r.status_code in (500, 502, 503, 504):
            _BUSY[model] = time.time() + 300
            raise ModelBusy(f"{model} is busy ({last[:90]})")
        if r.status_code == 400 and "chat_template_kwargs" in body and ("chat_template" in low or "enable_thinking" in low or "unknown" in low or "extra" in low):
            _CF_KWARGS_REJECTED[0] = True
            body.pop("chat_template_kwargs", None)
            log("[cloudflare] the thinking switch was rejected, retrying without it")
            continue
        raise RuntimeError(f"model rejected the request: {last}")
    raise RuntimeError(f"model rejected the request: {last}")


def or_call(messages, model, temperature=0.3, max_tokens=800, plugins=None):
    """One OpenRouter request. Starts at the next key in the rotation and moves on if a key is out of credit."""
    if model.startswith("@cf/"):
        return cf_call(messages, model, temperature, max_tokens)
    if not OR_KEYS:
        raise RuntimeError("no OpenRouter keys configured (secret OPENROUTER_API_KEYS)")
    usable = keys_usable()
    if not usable:  # never give up without trying: forget the rests and use every key again
        log("all OpenRouter keys were resting; trying them all again")
        _KS["keys"].clear()
        usable = keys_usable()
    start = _KS["rr"] % len(usable)
    _KS["rr"] += 1
    order = usable[start:] + usable[:start]
    last = None
    for key in order[:10]:
        fp = _fp(key)
        body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if model in _REASONING_MODELS and model not in _NO_REASON_PARAM:
            body["reasoning"] = {"enabled": False}  # we want the answer, not pages of hidden thinking
        if plugins:
            body["plugins"] = plugins
        try:
            r = requests.post(
                OR_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                         "HTTP-Referer": "https://github.com", "X-Title": PAGE_NAME},
                json=body,
                timeout=150,
            )
        except requests.RequestException as e:
            last = f"network error: {e}"
            log(f"[openrouter {fp}] {last}")
            continue
        try:
            data = r.json()
        except ValueError:
            data = {}
        err = data.get("error") if isinstance(data, dict) else None
        code = r.status_code
        if err and not data.get("choices") and str(err.get("code", "")).isdigit():
            code = int(err["code"])
        if code == 200 and isinstance(data, dict) and data.get("choices"):
            usage = data.get("usage") or {}
            log(f"[openrouter {fp}] {model} ok ({usage.get('total_tokens', '?')} tokens)")
            if fp in _KS["keys"]:
                _KS["keys"][fp]["r429"] = 0
            ch = data["choices"][0]
            msg = ch.get("message") or {}
            content = msg.get("content") or ""
            if not content.strip() and ch.get("finish_reason") == "length":
                raise RuntimeError(f"model rejected: {model} spent the whole {max_tokens}-token limit on hidden reasoning")
            if not content.strip():
                reasoning = msg.get("reasoning") or ""
                log(f"[openrouter {fp}] {model} returned EMPTY content (finish_reason={ch.get('finish_reason')}, "
                    f"reasoning_chars={len(reasoning)}); if finish_reason is 'length' the token limit was too small")
                content = reasoning  # some reasoning models leave the final JSON in the reasoning field
            elif ch.get("finish_reason") == "length":
                log(f"[openrouter {fp}] {model} was cut off at the token limit")
            return content
        text = str(err or r.text)[:200]
        last = f"HTTP {code}: {text}"
        log(f"[openrouter {fp}] {model} failed: {last}")
        low = text.lower()
        meta = (err or {}).get("metadata") if isinstance(err, dict) else None
        etype = str((meta or {}).get("error_type", "")).lower() if isinstance(meta, dict) else ""
        if ("provider returned error" in low or "upstream" in low or "overloaded" in low or "provider_overloaded" in etype
                or code in (502, 503, 504)):
            _BUSY[model] = time.time() + 600
            raise ModelBusy(f"{model} is busy upstream ({last[:90]})")
        if code == 402:
            _park(fp, _next_midnight(), "no credit (try again tomorrow)")
            log(f"[openrouter {fp}] no credit, key rests until the next UTC day")
        elif code == 401 or (code == 403 and "moderation" not in low and "flagged" not in low):
            _park(fp, 30 * 86400, "invalid or forbidden key")
        elif code == 429:
            e = _KS["keys"].setdefault(fp, {})
            e["r429"] = e.get("r429", 0) + 1
            if "per-day" in low or "per day" in low or "daily" in low or e["r429"] >= 3:
                e["r429"] = 0
                _park(fp, _next_midnight(), "daily limit reached (resets at UTC midnight)")
                log(f"[openrouter {fp}] daily limit reached, key rests until the next UTC day")
            else:
                _park(fp, 120, "rate limited for 2 minutes")
        elif code == 400 and "reasoning" in body and "reasoning" in low:
            _NO_REASON_PARAM.add(model)  # this model does not accept the switch: retry without it
            log(f"[{model}] does not accept the reasoning switch, retrying without it")
            continue
        elif code in (400, 403, 404):
            raise RuntimeError(f"model rejected the request: {last}")
        time.sleep(0.5)
    raise RuntimeError(f"all tried OpenRouter keys failed: {last}")


def chat(system, user, model, temperature=0.9, max_tokens=3000, plugins=None):
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return or_call(messages, model, temperature, max_tokens, plugins)


# ---- automatic choice of the best FREE models (no credit is spent unless you enable a paid fallback)
FAMILY_BONUS = {"deepseek": 30, "qwen": 26, "gpt-oss": 25, "glm": 24, "kimi": 24, "llama": 20, "mistral": 14, "gemma": 12, "nvidia": 12}
FAMILY_DEFAULT_SIZE = {"deepseek": 600, "kimi": 1000, "glm": 300, "qwen": 100, "gpt-oss": 120, "llama": 70, "mistral": 24, "gemma": 27, "nvidia": 50}
SKIP_NAME = re.compile(r"guard|safety|moderat|embed|tts|audio|whisper|image|diffusion|ocr|coder|code|roleplay|uncensor", re.I)
SLOW_NAME = re.compile(r"r1|reason|think", re.I)
STATIC_FREE = [
    "deepseek/deepseek-chat-v3.1:free", "qwen/qwen3-235b-a22b:free", "openai/gpt-oss-120b:free",
    "meta-llama/llama-3.3-70b-instruct:free", "google/gemma-3-27b-it:free", "mistralai/mistral-small-3.2-24b-instruct:free",
]


def _family(model):
    mid = model.lower()
    for fam in FAMILY_BONUS:
        if fam in mid:
            return fam
    return mid.split("/")[0]


def _score(m):
    mid = m["id"].lower()
    sizes = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)b", mid)]
    fam = _family(mid)
    size = max(sizes) if sizes else FAMILY_DEFAULT_SIZE.get(fam, 20)
    size_score = min(math.log2(size + 1) * 6, 50)
    if fam not in FAMILY_BONUS:  # unknown lab: trust a big number in the name less
        size_score *= 0.6
    score = FAMILY_BONUS.get(fam, 0) + size_score
    score += min(max((m.get("created", 0) - 1735689600) / (30 * 86400), 0) * 0.5, 20)  # newer is better (months since 2025-01)
    if "instruct" in mid or "chat" in mid:
        score += 3
    if SLOW_NAME.search(mid) or m.get("reasoning"):
        score -= 20
    elif m.get("rparam"):
        score -= 6  # hybrid models: usable with thinking switched off, but plain instruct models are safer
    for pref in _models("PREFER_MODELS", ""):
        if pref.lower() in mid:
            score += 100
    return score


def discover_models():
    """Ask OpenRouter which models are free right now and rank them; falls back to a static list."""
    try:
        r = requests.get("https://openrouter.ai/api/v1/models", timeout=60)
        r.raise_for_status()
        free = []
        for m in r.json().get("data", []):
            mid = m.get("id", "")
            pr = m.get("pricing") or {}
            try:
                is_free = float(pr.get("prompt", 1)) == 0 and float(pr.get("completion", 1)) == 0
            except (TypeError, ValueError):
                is_free = False
            if not (is_free or mid.endswith(":free")) or mid.startswith("openrouter/") or SKIP_NAME.search(mid):
                continue
            arch = m.get("architecture") or {}
            if (arch.get("output_modalities") or ["text"]) != ["text"]:
                continue
            exp = m.get("expiration_date")
            if exp:
                try:
                    if datetime.fromisoformat(str(exp)[:10]).replace(tzinfo=timezone.utc) < datetime.now(timezone.utc) + timedelta(days=14):
                        continue  # going away soon
                except ValueError:
                    pass
            desc = str(m.get("description") or "").lower()
            if re.search(r"coding agent|coding model|code generation|software engineering agent|agentic coding", desc):
                continue
            ctx = m.get("context_length") or 0
            if ctx and ctx < 16000:
                continue
            free.append({"id": mid, "created": m.get("created") or 0, "vision": "image" in (arch.get("input_modalities") or []),
                         "reasoning": "reasoning" in desc,
                         "rparam": "reasoning" in (m.get("supported_parameters") or []) or "include_reasoning" in (m.get("supported_parameters") or [])})
            if "reasoning" in (m.get("supported_parameters") or []) or "include_reasoning" in (m.get("supported_parameters") or []):
                _REASONING_MODELS.add(mid)
        if free:
            free.sort(key=_score, reverse=True)
            return [m["id"] for m in free], [m["id"] for m in free if m["vision"]]
    except Exception as e:  # noqa: BLE001
        log("could not read the OpenRouter model list:", e)
    return list(STATIC_FREE), []


def _demoted(model):
    return _KS.setdefault("demoted", {}).get(model, 0) > time.time()


def _demote(model, seconds):
    _KS.setdefault("demoted", {})[model] = int(time.time() + seconds)
    log(f"model {model} rests for {seconds // 3600} h")


def setup_models():
    global WRITER_MODELS, CHECKER_MODELS, QA_MODELS
    ranked, vision = ([], [])
    if OR_KEYS:
        ranked, vision = discover_models()
        ranked = [m for m in ranked if not _demoted(m)]
    paid = _models("PAID_FALLBACK_MODEL", "")  # optional: only used if every free model fails
    cf_w = [CF_TEXT_MODEL] if CF_TEXT else []
    cf_c = [CF_CHECKER_MODEL] if CF_TEXT else []
    if not WRITER_MODELS:
        WRITER_MODELS = list(dict.fromkeys(cf_w + ranked[:6] + paid))
    if not CHECKER_MODELS:
        fam = _family(ranked[0]) if ranked else ""
        others = [m for m in ranked if _family(m) != fam][:4]
        CHECKER_MODELS = list(dict.fromkeys(cf_c + (others or ranked[:4]) + ranked[:2] + paid))
    if not QA_MODELS:
        QA_MODELS = list(dict.fromkeys(cf_w + [m for m in vision if not _demoted(m)][:3]))
    log("writer models:", WRITER_MODELS[:3], "| checker models:", CHECKER_MODELS[:3], "| image QA models:", QA_MODELS[:2])


def chat_json(system, user, models, temperature, tries=2, max_tokens=3000, plugins=None):
    last = None
    for model in models:
        if _BUSY.get(model, 0) > time.time() and len(models) > 1:
            continue  # busy upstream a moment ago, use the next model first
        bad = 0
        for i in range(tries):
            try:
                raw = chat(system, user, model, temperature, max_tokens, plugins)
            except Exception as e:  # noqa: BLE001
                last = e
                log(f"[{model}] call failed: {e}")
                if str(e).startswith("model rejected"):
                    _demote(model, 24 * 3600)
                break  # go to next model
            try:
                return extract_json(raw)
            except ValueError as e:
                last = e
                bad += 1
                flat = (raw or "").strip().replace("\n", " ")
                snippet = flat[:240] + (" ... " + flat[-120:] if len(flat) > 400 else flat[240:])
                log(f"[{model}] bad JSON ({i + 1}/{tries}): {e} | reply length {len(flat)} | reply was: {snippet!r}")
        if bad >= tries:
            _demote(model, 6 * 3600)
    raise RuntimeError(f"no model returned valid JSON: {last}")


# ---- free evidence: Wikipedia (no API key, no cost)
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_UA = os.environ.get("WIKI_UA", "WorldWondersBot/1.0 (travel facts page; GitHub Actions)")
SHOW_SOURCE = os.environ.get("SHOW_SOURCE", "0") == "1"
BANNED_TITLE = re.compile(r"(?!x)x")   # nothing is banned by name
CUT_HEADINGS = re.compile(r"\n=+ *(References|See also|External links|Notes|Further reading|Bibliography|Footnotes|Sources|Citations) *=+", re.I)
YEAR_RE = re.compile(r"\b(?:1[5-9]\d\d|20\d\d)\b")
NUM_RE = re.compile(
    r"([$\u00a3\u20ac])?\s?(\d[\d,]*(?:\.\d+)?)(s)?\b\s?(cents?|dollars?|pounds?|pence|pennies|shillings?|francs?|marks?|yen|percent|%|mph|km/h|kph|hp|horsepower|miles?|kilometres?|kilometers?|knots?|tons?|tonnes?|kg|cylinders?|passengers?)?",
    re.I,
)


_WIKI_LAST = [0.0]


def wiki_get(params):
    """Polite, rate-limit friendly Wikipedia call: spaced requests and patient retries on 429/503."""
    p = {"format": "json", "formatversion": "2", **params}
    last = None
    for i in range(5):
        wait = 1.3 - (time.time() - _WIKI_LAST[0])
        if wait > 0:
            time.sleep(wait)
        _WIKI_LAST[0] = time.time()
        r = requests.get(WIKI_API, params=p, headers={"User-Agent": WIKI_UA, "Accept-Encoding": "gzip"}, timeout=30)
        if r.status_code in (429, 503):
            try:
                delay = float(r.headers.get("Retry-After", ""))
            except ValueError:
                delay = 0
            delay = max(delay, 8 * (i + 1))
            last = f"HTTP {r.status_code}"
            log(f"wikipedia busy ({r.status_code}), waiting {delay:.0f}s")
            time.sleep(min(delay, 60))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"wikipedia kept refusing: {last}")


def wiki_search(query, limit=10):
    data = wiki_get({"action": "query", "list": "search", "srsearch": query, "srlimit": limit, "srnamespace": 0})
    return [x["title"] for x in data.get("query", {}).get("search", [])]


def wiki_extract(title):
    data = wiki_get({"action": "query", "prop": "extracts", "explaintext": 1, "redirects": 1, "titles": title})
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        return ""
    text = pages[0].get("extract", "") or ""
    m = CUT_HEADINGS.search(text)
    if m:
        text = text[: m.start()]
    return text.strip()[:14000]


SHOW_SUBHOOK = os.environ.get("SHOW_SUBHOOK", "1") == "1"
STRICT_RULES = os.environ.get("STRICT_RULES", "0") == "1"   # 1 = copy/structure/percentage rules reject posts

SENSITIVE_TITLE = re.compile(
    r"racis|nudity|murder|assassin|massacre|genocide|rape|sexual|porn|suicide|execution|terror|war crime|holocaust|slavery|"
    r"abuse|torture|lynch|nazi|genital|prostitut|crash|accident|disaster|bombing|missile|weapon|concentration camp|"
    r"killing fields|battle of|siege of|referendum|impeach",
    re.I,
)

RECENT_CLUSTERS = []       # pillars used by the latest posts (filled from history.json)
_PICKED = [""]             # pillar of the article picked this run

_TRIED = set()
_LAST_SOURCE = []   # the last article we fetched, the last article fetched


TYPES_BY_CLUSTER = {
    "ancient-wonders": ["ancient ruins", "archaeological site", "ancient temple", "rock-cut cave temples", "prehistoric monument", "ancient city", "megalithic site"],
    "natural-wonders": ["waterfall", "canyon", "glacier", "mountain peak", "cave", "volcano", "lake", "hot springs", "desert", "gorge"],
    "iconic-landmarks": ["famous landmark", "bridge", "observation tower", "monument", "historic square", "historic district", "famous museum"],
    "palaces-and-castles": ["castle", "royal palace", "fort", "historic mansion", "citadel"],
    "sacred-and-heritage": ["temple", "cathedral", "mosque", "pagoda", "monastery", "historic church", "World Heritage Site"],
    "islands-and-parks": ["island", "national park", "beach", "coral reef", "nature reserve", "botanical garden", "wildlife sanctuary"],
}
COUNTRIES = (
    "Afghanistan, Albania, Algeria, Andorra, Angola, Argentina, Armenia, Australia, Austria, Azerbaijan, Bahamas, Bahrain, Bangladesh, Barbados, Belarus, Belgium, Belize, "
    "Benin, Bhutan, Bolivia, Bosnia and Herzegovina, Botswana, Brazil, Brunei, Bulgaria, Burkina Faso, Burundi, Cambodia, Cameroon, Canada, Cape Verde, Chad, Chile, China, "
    "Colombia, Costa Rica, Croatia, Cuba, Cyprus, Czech Republic, Denmark, Dominican Republic, Ecuador, Egypt, El Salvador, Estonia, Eswatini, Ethiopia, Fiji, Finland, France, "
    "Gabon, Gambia, Georgia, Germany, Ghana, Greece, Greenland, Guatemala, Guyana, Haiti, Honduras, Hungary, Iceland, India, Indonesia, Iran, Iraq, Ireland, Israel, Italy, "
    "Ivory Coast, Jamaica, Japan, Jordan, Kazakhstan, Kenya, Kyrgyzstan, Laos, Latvia, Lebanon, Lesotho, Libya, Liechtenstein, Lithuania, Luxembourg, Madagascar, Malawi, Malaysia, "
    "Maldives, Mali, Malta, Mauritania, Mauritius, Mexico, Moldova, Monaco, Mongolia, Montenegro, Morocco, Mozambique, Myanmar, Namibia, Nepal, Netherlands, New Zealand, Nicaragua, "
    "Niger, Nigeria, North Macedonia, Norway, Oman, Pakistan, Palau, Panama, Papua New Guinea, Paraguay, Peru, Philippines, Poland, Portugal, Qatar, Romania, Russia, Rwanda, "
    "Samoa, San Marino, Saudi Arabia, Senegal, Serbia, Seychelles, Singapore, Slovakia, Slovenia, Solomon Islands, South Africa, South Korea, Spain, Sri Lanka, Sudan, Suriname, "
    "Sweden, Switzerland, Syria, Taiwan, Tajikistan, Tanzania, Thailand, Togo, Tonga, Trinidad and Tobago, Tunisia, Turkey, Turkmenistan, Uganda, Ukraine, United Arab Emirates, "
    "United Kingdom, United States, Uruguay, Uzbekistan, Vanuatu, Venezuela, Vietnam, Yemen, Zambia, Zimbabwe, Scotland, Wales, Sicily, Tuscany, Patagonia, Siberia, Tibet, "
    "Hawaii, Alaska, Bavaria, Andalusia, Provence, Himalayas, Rajasthan, Kerala, Bali, Java, Sumatra, Borneo, Yunnan, Sichuan, Hokkaido, Kyoto"
).split(", ")
PLACE_WORDS = re.compile(r"\b(located|situated|visitors?|tourists?|tourism|attraction|heritage|national park|temple|castle|palace|fort|waterfall|mountain|island|beach|"
                         r"lake|cave|canyon|glacier|monument|cathedral|mosque|museum|ruins|archaeological|UNESCO|reserve|garden|bridge|tower)\b", re.I)
MIN_PLACE_WORDS = int(os.environ.get("MIN_PLACE_WORDS", "6"))
CURATED_SHARE = float(os.environ.get("CURATED_SHARE", "0.35"))   # how often a hand-picked title is tried before the endless search


def _looks_like_place(text):
    head = text[:300].lower()
    if "may refer to" in head or "disambiguation" in head:
        return False
    return len(PLACE_WORDS.findall(text)) >= MIN_PLACE_WORDS


def _accept(t, cluster, how, min_len, check_place):
    _TRIED.add(t)
    text = wiki_extract(t)
    if len(text) < min_len or (check_place and not _looks_like_place(text)):
        log(f"skipped {t!r}: not found, too short or not a visitable place")
        return None
    log(f"article {t!r} accepted ({how}, pillar {cluster}, {len(text)} characters)")
    _LAST_SOURCE[:] = [t, text]
    _PICKED[0] = cluster
    return t, text


def _choose_cluster():
    """Topical-map rotation: take the pillar used least in the latest posts, so the page stays balanced across its pillars."""
    counts = Counter(RECENT_CLUSTERS[-12:])
    names = list(CLUSTERS)
    random.shuffle(names)
    return min(names, key=lambda n: counts.get(n, 0))


def pick_source(used, level=0):
    """Endless supply: pillar by rotation, then either a hand-picked title or a fresh Wikipedia search (attraction type x country).
    Titles already posted are skipped, so the page never runs out of places."""
    cluster = _choose_cluster()
    pool = [t for t in CLUSTERS[cluster] if t not in used and t not in _TRIED]
    random.shuffle(pool)
    if pool and random.random() < CURATED_SHARE:
        for t in pool[:2]:
            got = _accept(t, cluster, "curated", 1200, False)
            if got:
                return got
    for _ in range(3):
        query = f"{random.choice(TYPES_BY_CLUSTER[cluster])} {random.choice(COUNTRIES)}"
        log("search:", query)
        titles = [
            t for t in wiki_search(query, 15)
            if not re.match(r"(List of|Timeline of|Index of|Outline of)", t) and not BANNED_TITLE.search(t)
            and not SENSITIVE_TITLE.search(t) and t not in used and t not in _TRIED
        ]
        for t in titles[:3]:
            got = _accept(t, cluster, "search", 1500, True)
            if got:
                return got
    for t in pool[:3]:
        got = _accept(t, cluster, "curated", 1200, False)
        if got:
            return got
    raise ValueError("no usable article this time")


def _norm(s):
    s = s.lower()
    for a, b in (("\u2019", "'"), ("\u2018", "'"), ("\u201c", '"'), ("\u201d", '"'), ("\u2013", "-"), ("\u2014", "-")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9$\u00a3\u20ac%.' -]", " ", s)).strip()


def quote_supported(quote, src_norm, src_tokens):
    q = _norm(quote)
    qt = q.split()
    if len(qt) < 4:
        return False
    if q in src_norm:
        return True
    qset, n = set(qt), len(qt)
    for i in range(0, max(1, len(src_tokens) - n + 1)):
        if len(qset & set(src_tokens[i : i + n + 3])) / len(qset) >= 0.9:
            return True
    return False


BN_DIGITS = str.maketrans("\u09e6\u09e7\u09e8\u09e9\u09ea\u09eb\u09ec\u09ed\u09ee\u09ef", "0123456789")


def numbers_in(text, factual_only):
    out = set()
    text = re.sub(r"(\d)([\u0980-\u09ff])", r"\1 \2", str(text).translate(BN_DIGITS))
    for m in NUM_RE.finditer(text):
        cur, num, decade, unit = m.group(1), m.group(2), m.group(3), m.group(4)
        n = num.replace(",", "").rstrip(".")
        if not n:
            continue
        if not factual_only:
            out.add(n)
            continue
        if decade:  # "1930s" style decades are not exact claims
            continue
        try:
            val = float(n)
        except ValueError:
            continue
        if cur or unit or YEAR_RE.fullmatch(n) or val >= 100:
            out.add(n)
    return out


def verify_grounding(post, source):
    """Deterministic check: every quote must be in the source and every key number must come from it."""
    problems = []
    evidence = post.get("evidence")
    if not isinstance(evidence, list) or len(evidence) < 2:
        return ["evidence missing"]
    src_norm = _norm(source)
    src_tokens = src_norm.split()
    for item in evidence:
        quote = item.get("quote", "") if isinstance(item, dict) else str(item)
        if not quote_supported(quote, src_norm, src_tokens):
            problems.append(f"quote not found in source: {quote[:70]!r}")
    text = " ".join(post["headline_lines"]) + " " + post["caption"]
    missing = numbers_in(text, True) - numbers_in(source, False)
    if missing:
        problems.append(f"numbers not in source: {sorted(missing)}")
    return problems


VOICES = [
    "warm and curious, like a friend who just came back from the trip",
    "calm and precise, with one vivid comparison",
    "amused and a bit incredulous, with one light aside",
    "matter-of-fact, quietly impressed",
    "enthusiastic but never exaggerated",
]

SHAPES = [
    "Open with the surprising number or detail, then explain what the place is and where it is, then the lesser-known detail, then end with the question.",
    "Open with what the place is and why it is famous in one line, then the odd detail most guides skip, then a short paragraph with the number and its meaning for a visitor, then the question.",
    "Open with a one-line claim most people would doubt, prove it in two short paragraphs, then name the nearby or related places the source mentions, then the question.",
    "Open in the middle of the scene (what a visitor sees), jump back to explain how the place came to be, finish with the question.",
]


def write_post(title, source, recent):
    system = build_writer_system()
    user = (
        f"SOURCE ARTICLE TITLE: {title}\n\nSOURCE TEXT:\n{source}\n\n"
        f"Do NOT repeat or closely resemble any of these recent topics: {json.dumps(recent)}\n"
        f"OUTPUT LANGUAGE for the caption: {LANG_NAME}.\n"
        f"VOICE for the caption: {random.choice(VOICES)}.\nSHAPE for the caption: {random.choice(SHAPES)}\n"
        "Write the post using ONLY the SOURCE TEXT. Return the JSON object now."
    )
    return chat_json(system, user, WRITER_MODELS, 0.7, tries=1, max_tokens=5000)


def check_faithful(post, source):
    draft = {"headline_lines": post["headline_lines"], "caption": post["caption"]}
    user = f"SOURCE TEXT:\n{source}\n\nDRAFT:\n{json.dumps(draft, ensure_ascii=False)}"
    if WEB_CHECK:
        return chat_json(CHECKER_WEB_SYSTEM, user, CHECKER_MODELS, 0.1, max_tokens=1500, plugins=[{"id": "web", "max_results": 3}])
    return chat_json(CHECKER_SYSTEM, user, CHECKER_MODELS, 0.1, max_tokens=1500)


def ensure_highlight(lines):
    """If the model forgot [brackets], auto-highlight numbers, prices and years in red."""
    if any("[" in l for l in lines):
        return lines
    pat = re.compile(r"(\$?£?€?\d[\d,\.]*\s?(?:CENTS?|PENNIES|PENCE|DOLLARS?|POUNDS?|%|YEARS?|HOURS?|DAYS?|WEEKS?|MPH|KM/H|KPH|HP|MILES?|KM|TONS?|TONNES?)?)", re.I)
    return [pat.sub(lambda m: f"[{m.group(1).strip()}]" + (" " if m.group(1).endswith(" ") else ""), l) for l in lines]


def _runs(line):
    """Split a line into (text, is_red) runs. Brackets only switch the colour on and off and never survive."""
    runs, red, buf = [], False, ""
    for ch in line:
        if ch == "[":
            if buf:
                runs.append((buf, red))
                buf = ""
            red = True
        elif ch == "]":
            if buf:
                runs.append((buf, red))
                buf = ""
            red = False
        else:
            buf += ch
    if buf:
        runs.append((buf, red))
    return runs


def fix_brackets(line):
    """Repair common model mistakes: $[100] -> [$100], [1930,] -> [1930],, unbalanced brackets, parentheses."""
    line = re.sub(r"([$\u00a3\u20ac])\[", r"[\1", line)
    line = re.sub(r"([,.;:!?]+)\]", r"]\1", line)
    if line.count("[") != line.count("]"):
        line = line.replace("[", "").replace("]", "")
    return line.replace("(", "").replace(")", "")


def limit_red(lines, max_runs=2):
    """Keep at most `max_runs` red groups, so the headline never turns into a red wall."""
    out, runs = [], 0
    for line in lines:
        res = ""
        for text, red in _runs(line):
            if red:
                runs += 1
                res += f"[{text}]" if runs <= max_runs else text
            else:
                res += text
        out.append(res)
    return out


def rewrap_headline(lines, width=30):
    """Re-wrap headline words into lines of at most `width` characters, keeping the red words red."""
    chars = []
    for line in lines:
        for text, red in _runs(line):
            chars.extend((c, red) for c in text)
        chars.append((" ", False))
    words, cur = [], []
    for ch, red in chars:
        if ch.isspace():
            if cur:
                words.append(cur)
                cur = []
        else:
            cur.append((ch, red))
    if cur:
        words.append(cur)

    def plain(ws):
        return " ".join("".join(c for c, _ in w) for w in ws)

    out, cur_line = [], []
    for w in words:
        if cur_line and len(plain(cur_line + [w])) > width:
            out.append(cur_line)
            cur_line = []
        cur_line.append(w)
    if cur_line:
        out.append(cur_line)

    rendered = []
    for ws in out:
        seq = []
        for i, w in enumerate(ws):
            if i:
                seq.append((" ", ws[i - 1][-1][1] and w[0][1]))
            seq.extend(w)
        text, i = "", 0
        while i < len(seq):
            j = i
            while j < len(seq) and seq[j][1] == seq[i][1]:
                j += 1
            chunk = "".join(c for c, _ in seq[i:j])
            text += f"[{chunk}]" if seq[i][1] else chunk
            i = j
        rendered.append(text)
    return rendered


def validate_post(post, level=0):
    """level 0 = normal, 1 = relaxed, 2 = minimal. Formatting is always repaired; only level 0 is picky."""
    lines = post.get("headline_lines")
    if isinstance(lines, str):
        lines = [lines]
    if not isinstance(lines, list) or not [l for l in lines if str(l).strip()]:
        if level == 0:
            return "headline missing"
        lines = [str(post.get("topic") or "WORLD TRAVEL").upper()]
    lines = [fix_brackets(str(l)).strip() for l in lines if str(l).strip()]
    if len(lines) == 1 or len(lines) > 4 or any(len(re.sub(r"[\[\]]", "", l)) > 32 for l in lines):
        lines = rewrap_headline(lines)
    lines = limit_red(lines)[:4]
    post["headline_lines"] = lines
    words = len(" ".join(lines).split())
    if level == 0:
        years = [int(y) for y in YEAR_RE.findall(" ".join(lines))]
        m_year = re.search(r"\d{4}", str(post.get("year") or ""))
        if any(y > RECENT_CUTOFF for y in years) or (m_year and int(m_year.group()) > RECENT_CUTOFF and "bc" not in str(post.get("year")).lower()):
            return f"too recent (limit {RECENT_CUTOFF})"
        if not 6 <= words <= 15:
            return f"headline word count {words}"
    cwords = len(str(post.get("caption", "")).split())
    if level == 0 and not 120 <= cwords <= 450:
        return f"caption word count {cwords}"
    if level == 1 and cwords < 60:
        return f"caption word count {cwords}"
    if level == 2 and cwords < 25:
        return f"caption word count {cwords}"
    cap = re.sub(r"\s?\[\d{1,2}\]", "", post["caption"])
    cap = re.sub(r"\s*[\u2014\u2013]\s*", ", ", cap)  # no em/en dashes (AI tell)
    cap = re.sub(r"[*_#`]+", "", cap).strip()
    if POST_LANG == "bn":
        letters = [c for c in cap if c.isalpha()]
        if letters and sum(1 for c in letters if "\u0980" <= c <= "\u09ff") / len(letters) < 0.5:
            return "caption is not written in Bengali"
    post["caption"] = cap
    post["headline_lines"] = ensure_highlight(lines)
    return None


def scrub_numbers(post, source, title):
    """Never publish an amount or year the source does not contain: drop such sentences, or use a neutral headline."""
    known = numbers_in(source, False)

    def unsupported(text):
        return numbers_in(text, True) - known

    if unsupported(" ".join(post["headline_lines"])):
        log("headline had numbers that are not in the source; using a neutral headline")
        post["headline_lines"] = ensure_highlight(rewrap_headline(["THE STORY OF", f"[{title.upper()}]"]))
    kept, dropped = [], 0
    for para in re.split(r"\n+", post["caption"]):
        sents = re.split(r"(?<=[.?!\u0964])\s+", para.strip())
        good = [x for x in sents if x and not unsupported(x)]
        dropped += len(sents) - len(good)
        if good:
            kept.append(" ".join(good))
    if dropped:
        log(f"dropped {dropped} caption sentence(s) with numbers that are not in the source")
    post["caption"] = "\n\n".join(kept) if kept else post["caption"]


def add_hashtags(post):
    tags = []
    for t in list(post.get("hashtags") or []) + [BRAND_TAG]:
        t = "#" + re.sub(r"[^A-Za-z0-9_\u0980-\u09FF]", "", str(t))
        if len(t) > 3 and t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    tail = " ".join(tags[:3])
    if SHOW_SOURCE and post.get("source_title"):
        label = "\u09a4\u09a5\u09cd\u09af\u09b8\u09c2\u09a4\u09cd\u09b0" if POST_LANG == "bn" else "Source"
        tail = f'{label}: Wikipedia, "{post["source_title"]}"\n' + tail
    post["caption"] = post["caption"].rstrip() + "\n\n" + tail


RUN_BUDGET_MIN = float(os.environ.get("RUN_BUDGET_MINUTES", "20"))


HEDGE_WORDS = re.compile(r"\b(reportedly|according to|accounts|about|roughly|estimated|around|some sources|records suggest)\b", re.I)


def _words(text):
    return re.sub(r"[^a-z0-9$%. ]+", " ", text.lower().replace("\u2019", "'")).split()


def check_originality(post, source):
    """Own words, story shape, nothing modern, cautious wording for percentages. Returns a problem text or None."""
    cap = post["caption"]
    paras = [x.strip() for x in re.split(r"\n+", cap) if x.strip()]
    if len(paras) < 3:
        return f"caption has {len(paras)} paragraphs, needs 3 or more"
    post["caption"] = "\n\n".join(paras)
    first = re.split(r"(?<=[.?!\u0964])\s", paras[0], maxsplit=1)[0]
    if len(first) > 140:
        return f"first sentence is {len(first)} characters, limit 140"
    cap_years = [int(y) for y in YEAR_RE.findall(cap)]
    if any(y > RECENT_CUTOFF for y in cap_years):
        return f"caption mentions a year after {RECENT_CUTOFF}"
    src = _words(source)
    grams = {" ".join(src[i : i + 6]) for i in range(max(0, len(src) - 5))}
    cw = _words(cap)
    for i in range(max(0, len(cw) - 5)):
        g = " ".join(cw[i : i + 6])
        if g in grams:
            return f"caption copies the source: '{g}'"
    head = " ".join(post["headline_lines"])
    if ("%" in head or re.search(r"\b\d+\s*(?:to|-)\s*\d+\b", head)) and not HEDGE_WORDS.search(cap):
        return "percentage or range in the headline, but the caption has no cautious wording"
    return None


AI_MARKERS = re.compile(
    r"\b(delve[sd]?|tapestry|testament|pivotal|crucial|moreover|furthermore|additionally|notably|ultimately|underscor\w+|paved the way|"
    r"ignit\w+|spark(?:ed|s|ing) (?:a|an|the)|it'?s worth noting|in conclusion|then versus now|landscape of|realm of|"
    r"play(?:s|ed) a (?:crucial|key|vital|significant) role|a turning point|serves as|stands as|here'?s the thing)\b", re.I)


def _sentences(text):
    return [x.strip() for x in re.split(r"(?<=[.!?\u0964])\s+", text.replace("\n", " ")) if x.strip()]


def ai_likeness(text):
    """Rough local meter (0 = very human, 100+ = very machine-like). Low variety, tidy rhythm and stock phrases raise it."""
    sents = _sentences(text)
    if len(sents) < 4:
        return 50.0
    lens = [len(x.split()) for x in sents]
    mean = sum(lens) / len(lens)
    sd = (sum((n - mean) ** 2 for n in lens) / len(lens)) ** 0.5
    cv = sd / max(mean, 1)
    short = sum(1 for n in lens if n <= 6) / len(lens)
    long_ = sum(1 for n in lens if n >= 25) / len(lens)
    starts = {}
    for x in sents:
        w = x.split()[0].lower()
        starts[w] = starts.get(w, 0) + 1
    score = max(0.0, 0.65 - cv) * 100
    score += 15 if short < 0.10 else 0
    score += 10 if long_ < 0.05 else 0
    score += 10 if max(starts.values()) / len(sents) > 0.3 else 0
    score += 8 * len(AI_MARKERS.findall(text))
    score += 20 if re.match(r"\s*(picture|imagine)\b", text, re.I) else 0
    score += 8 * max(0, text.count("?") - 1)
    return score


DEAI = [
    (r"\bIt'?s worth noting that\s*", ""), (r"\bIn conclusion,\s*", ""), (r"\b(?:Moreover|Furthermore|Additionally),\s*", "Also, "),
    (r"\bNotably,\s*", ""), (r"\bUltimately,\s*", ""), (r"\bdelve into\b", "dig into"), (r"\btestament to\b", "proof of"),
    (r"\btapestry\b", "mix"), (r"\blandscape of\b", "world of"), (r"\brealm of\b", "world of"), (r"\bpivotal\b", "big"),
    (r"\bcrucial\b", "big"), (r"\bpaved the way for\b", "led to"), (r"\bunderscores?\b", "shows"),
]


def deai(text):
    """Deterministic clean-up: no 'Picture...' opener, no template lines, one question at most (the last), no stock phrases."""
    text = re.sub(r"^\s*(?:Picture|Imagine)(?: this)?[:,]?\s+", "", text)
    text = text[:1].upper() + text[1:] if text else text
    for pat, rep_ in DEAI:
        text = re.sub(pat, rep_, text, flags=re.I)
    paras = [x.strip() for x in re.split(r"\n+", text) if x.strip()]
    out = []
    for i, para in enumerate(paras):
        sents = _sentences(para)
        keep = []
        for j, x in enumerate(sents):
            last = i == len(paras) - 1 and j == len(sents) - 1
            if re.match(r"(Then versus now|Then:)", x, re.I):
                continue
            if x.endswith("?") and not last:
                continue  # rhetorical questions in the middle read like a template
            keep.append(x)
        if keep:
            out.append(" ".join(keep))
    return "\n\n".join(out) if out else text


HUMANIZE_SYSTEM = """You are an editor who makes machine-sounding text read like a real person wrote it, without changing a single fact.
You get a CAPTION. Rewrite it so that it:
- keeps every fact, number, year and name exactly; adds no new fact, number or name;
- uses uneven sentence lengths (some under 6 words, some over 25), fragments, contractions and plain words;
- sounds like one person with an opinion talking to a friend, not like a report or an encyclopedia;
- has no summary, no moral and no lesson at the end; at most ONE question in the whole text, and only as the very last sentence;
- never uses: Picture, Imagine, delve, tapestry, testament, pivotal, crucial, moreover, furthermore, additionally, notably, ultimately, underscore, "paved the way", "ignited", "sparked a", "It's worth noting", "Then versus now";
- has 3 to 6 paragraphs of different lengths separated by a blank line (write \\n\\n inside the JSON string);
- has a first sentence under 120 characters that holds the surprising fact;
- stays between {lo} and {hi} words; no em dashes, no emojis, no hashtags.
Return ONLY JSON: {{"caption": "..."}}"""


def humanize_caption(post, source):
    """Free and never blocking: if anything goes wrong the original caption (cleaned) is used."""
    original = deai(post["caption"])
    if POST_LANG != "en":
        return original  # the English-only humanizer would damage Bengali
    best, best_score = original, ai_likeness(original)
    start_score = best_score
    allowed = numbers_in(source, False)
    try:
        if best_score >= 18:
            words = len(original.split())
            system = HUMANIZE_SYSTEM.format(lo=max(110, int(words * 0.8)), hi=int(words * 1.15) + 20)
            models = list(CHECKER_MODELS) or list(WRITER_MODELS)
            for k in range(2):
                order = models[k % max(len(models), 1):] + models[: k % max(len(models), 1)]
                try:
                    out = chat_json(system, f"CAPTION:\n{best if k else original}", order, 1.0, tries=1, max_tokens=2500)
                except Exception as e:  # noqa: BLE001
                    log("human-voice editor unavailable:", str(e)[:100])
                    break
                cand = deai(str(out.get("caption", "")).strip())
                cand = re.sub(r"\s*[\u2014\u2013]\s*", ", ", cand)
                if len(cand.split()) < max(70, int(0.6 * words)) or numbers_in(cand, True) - allowed:
                    log("human-voice rewrite dropped (too short or it changed the numbers)")
                    continue
                sc = ai_likeness(cand)
                if sc < best_score:
                    best, best_score = cand, sc
                if best_score < 18:
                    break
    except Exception as e:  # noqa: BLE001
        log("human-voice step skipped:", e)
    log(f"AI-likeness meter: {start_score:.0f} -> {best_score:.0f} (lower is more human)")
    return best


def emergency_post(title, source):
    """Last resort without any AI: a plain, attributed post from the article's own opening lines."""
    flat = re.sub(r"\s+", " ", re.sub(r"=+[^=]+=+", " ", source[:4000]))
    sents = [x for x in re.split(r"(?<=[.!?])\s+", flat) if 40 < len(x) < 320][:4]
    caption = "\n\n".join(sents) + f'\n\nWhat part of this surprises you most?\n\nSource: Wikipedia, "{title}"\n#WorldTravel #TravelFacts'
    return {
        "topic": title, "source_title": title, "year": "", "country": "",
        "headline_lines": ensure_highlight(rewrap_headline(["THE STORY OF", f"[{title.upper()}]"])),
        "cluster": _PICKED[0], "subhook": "A PLACE WORTH KNOWING", "caption": caption,
        "image_prompt_a": f"{title}, a famous travel destination, wide view, natural daylight, travel photograph",
        "image_prompt_b": f"{title}, the same destination at golden hour, a different angle, travel photograph",
    }


def build_post(recent, used):
    """Tries hard, then softer, then softest, then a plain post: a run always ends with something worth publishing."""
    deadline = time.time() + RUN_BUDGET_MIN * 60
    for attempt in range(1, 13):
        if time.time() > deadline:
            log(f"time budget of {RUN_BUDGET_MIN:.0f} minutes used up")
            break
        level = 0 if attempt <= 4 else (1 if attempt <= 8 else 2)
        try:
            title, source = pick_source(used, level)
            used.add(title)
            log(f"attempt {attempt} (level {level}): source article: {title}")
            post = write_post(title, source, recent)
            if post.get("skip"):
                log("writer skipped:", post.get("reason"))
                continue
            if not post.get("caption"):
                raise ValueError("missing caption")
            post.setdefault("topic", title)
            if not post.get("topic"):
                post["topic"] = title
            problem = validate_post(post, level)
            if problem:
                log("rejected:", problem)
                continue
            note = check_originality(post, source)
            if note:
                log("note (not blocking):", note)
            if level == 0:
                hard = [x for x in verify_grounding(post, source)
                        if "numbers not in source" in x.lower() or ("quote not found in source" in x.lower() and len(x) > 60)]
                if hard:
                    log("grounding failed:", hard)
                    continue
                verdict = check_faithful(post, source)
                if not verdict.get("faithful"):
                    bad = [u for u in (verdict.get("unsupported") or [])
                           if any(k in u.lower() for k in ("not in the source", "unsupported", "wrong", "missing", "not supported"))]
                    if bad:
                        log("checker: unsupported claims:", bad)
                        continue
                try:
                    if float(verdict.get("hook_score", 0)) < 5:
                        log("rejected: weak hook", verdict.get("hook_score"))
                        continue
                except (TypeError, ValueError):
                    pass
            else:
                scrub_numbers(post, source, title)
            post["caption"] = humanize_caption(post, source)
            post["source_title"] = title
            post["cluster"] = _PICKED[0]
            post["confidence"] = f"level {level}"
            post["sources"] = [f"Wikipedia: {title}"]
            post["issues"] = []
            add_hashtags(post)
            return post
        except Exception as e:  # noqa: BLE001
            log("attempt failed:", e)
    if _LAST_SOURCE:
        title, source = _LAST_SOURCE
        log(f"EMERGENCY POST from the article {title!r}: the AI writer did not deliver")
        return emergency_post(title, source)
    raise RuntimeError("no article could be fetched (Wikipedia unreachable)")


# ----------------------------------------------------------------- image
NEGATIVE_SD = (
    "deformed, distorted face, ugly, bad anatomy, extra fingers, mutated hands, poorly drawn hands, poorly drawn face, "
    "blurry, lowres, text, watermark, logo, cartoon, anime, painting, 3d render, oversaturated, duplicate, cropped, crowd, "
    "modern clothing, modern buildings, factory, cars, electric lights, straw hat"
)
FACELESS = os.environ.get("FACELESS", "0") == "1"
IMAGE_QA = os.environ.get("IMAGE_QA", "1") == "1"
GUIDANCE = os.environ.get("GUIDANCE", "2")   # LCM models want about 1-2; "0" = do not send
QA_MODELS = _models("IMAGE_QA_MODEL", "")      # empty = automatic: free vision models
QA_PROMPT = (
    "You are a strict quality reviewer for AI-generated travel photographs. Reject the image if you see ANY of: "
    "a distorted, melted or asymmetric face; fused, extra or missing fingers or malformed hands; extra or missing limbs; "
    "two heads or duplicated people; garbled letters only if readable writing is clearly visible in the picture (a picture with no writing is fine); impossible or merged objects; a cartoon, anime or painted look. "
    'Return ONLY JSON: {"ok": true or false, "score": 1-10, "problems": ["..."]}. Score 8 or more only if it looks like a clean real photograph.'
)
_QA_OFF = [False]


def _small_model(model):
    return any(t in model.lower() for t in ("dreamshaper", "lcm", "sd15", "stable-diffusion"))


def _gen_size(model):
    """FLUX.2 klein is billed per 512x512 output tile, so the default is small (768x960) and is upscaled to the final post size."""
    try:
        w, h = os.environ.get("GEN_SIZE", "768x960").lower().split("x")
        w, h = int(w), int(h)
        return max(256, min(1536, w // 16 * 16)), max(256, min(1536, h // 16 * 16))
    except ValueError:
        return 768, 960


def _shorten(text, n):
    return " ".join(str(text or "").replace("\n", " ").split()[:n]).rstrip(".,;")


def _era_mode(post):
    """'mono' (sepia/black-and-white) before 1950, 'faded' colour film after. Old styles hide the weaknesses of a small model."""
    y = str(post.get("year") or "")
    m = re.search(r"\d{3,4}", y)
    if "bc" in y.lower() or not m:
        return "mono"
    return "faded" if int(m.group()) >= 1950 else "mono"


def build_image_prompt(post, variant=0, model=None):
    """The writer's scene for this story (idea A, or idea B on every second candidate), shaped for the model that will draw it."""
    model = model or IMAGE_MODELS[0]
    text = (post.get("image_prompt_b") if variant == 1 and post.get("image_prompt_b") else post.get("image_prompt_a")) or ""
    if not text:  # older field names
        text = f"{post.get('image_subject') or post.get('image_prompt') or ''}, {post.get('image_scene') or ''}"
    era = _shorten(f"{post.get('year') or ''} {post.get('country') or ''}", 4)
    if era and era.lower() in text.lower():
        era = ""  # the country is already in the scene text
    if _small_model(model):  # SD-1.5 reads only about 60 words: key subject first
        style = ("black and white archival photograph, sepia tone, early documentary photo, grainy" if _era_mode(post) == "mono"
                 else "faded color photograph, 1970s film look, soft grain")
        return f"{_shorten(text, 38)}, {era}, {style}, sharp focus, detailed"[:520].lstrip(", ")
    return (f"{text.strip().rstrip('.')}. {(era + '. ') if era else ''}{STYLE_SUFFIX}. Vertical 4:5 frame: the main subject sits in the upper two thirds "
            "and the lowest part of the frame is a darker foreground such as a path, grass, water or stone. "
            "Sharp focus, natural look, fine film grain, photorealistic, high detail. No watermarks.")[:1500]


class CFQuotaExceeded(RuntimeError):
    """The free daily Workers AI allocation (10,000 neurons) is used up: stop asking, it resets at 00:00 UTC."""


def _cf_image(prompt, w, h):
    """One call to Workers AI flux-2-klein-4b. This model takes multipart/form-data, even for a plain prompt."""
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/ai/run/{CF_MODEL}"
    fields = {"prompt": (None, prompt), "width": (None, str(w)), "height": (None, str(h))}
    r = requests.post(url, headers={"Authorization": f"Bearer {CF_TOKEN}"}, files=fields, timeout=240)
    body = r.text[:400]
    low = body.lower()
    if r.status_code != 200:
        if "neurons" in low or "daily free allocation" in low or "4006" in low:
            raise CFQuotaExceeded(f"Workers AI daily free allocation used up: {body[:160]}")
        raise RuntimeError(f"HTTP {r.status_code} {body[:200]}")
    _NEURONS["image"] += 26.05 * math.ceil(w / 512) * math.ceil(h / 512)  # per output 512x512 tile, rounded up
    if r.headers.get("content-type", "").startswith("image/"):
        return r.content
    data = r.json()
    if isinstance(data, dict) and data.get("success") is False:
        raise RuntimeError(f"api error {str(data.get('errors'))[:200]}")
    res = data.get("result", data) if isinstance(data, dict) else data
    b64 = res.get("image") if isinstance(res, dict) else None
    if not b64:
        raise RuntimeError(f"no image in response: {body[:200]}")
    return base64.b64decode(b64)


def _fetch_candidate(post, variant, tag):
    last = None
    for i in range(3):
        model = IMAGE_MODELS[0]
        prompt = build_image_prompt(post, variant, model)
        gw, gh = _gen_size(model)
        try:
            raw = _cf_image(prompt, gw, gh)
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            log(f"image candidate ok (model {model}, {img.size[0]}x{img.size[1]}, try {i + 1})")
            fit = ImageOps.fit(img, (W, H), method=Image.LANCZOS, centering=(0.5, 0.4))
            return fit, os.environ.get("FORCE_VINTAGE", "0") == "1"
        except CFQuotaExceeded:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
        log(f"image call failed ({i + 1}/3, model {model}): {str(last)[:200]}")
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"image generation failed: {last}")


VINTAGE_GRADE = os.environ.get("VINTAGE_GRADE", "1") == "1"


def _vintage(img, mode):
    """Sepia (or faded colour) toning, vignette, grain and a few dust specks: reads as an old archive photo and hides softness."""
    w, h = img.size
    if mode == "mono":
        g = ImageOps.autocontrast(img.convert("L"), cutoff=1)
        g = ImageEnhance.Contrast(g).enhance(1.12)
        out = ImageOps.colorize(g, black=(18, 11, 6), white=(244, 230, 201), mid=(138, 108, 76))
    else:
        out = ImageEnhance.Color(img).enhance(0.78)
        out = Image.blend(out, ImageChops.multiply(out, Image.new("RGB", (w, h), (255, 232, 200))), 0.35)
    edge = Image.radial_gradient("L").resize((w, h)).point(lambda v: int(v * 0.55))  # dark corners
    out = Image.composite(ImageEnhance.Brightness(out).enhance(0.45), out, edge)
    out = Image.blend(out, Image.effect_noise((w, h), 34).convert("RGB"), 0.06)  # film grain
    d = ImageDraw.Draw(out)
    for _ in range(40):  # dust specks
        x, y, r = random.randint(0, w - 1), random.randint(0, h - 1), random.choice((1, 1, 2))
        d.ellipse([x, y, x + r, y + r], fill=random.choice(((236, 226, 204), (30, 22, 14))))
    return out


def _finish(img, upscaled, mode="mono"):
    img = ImageEnhance.Contrast(img).enhance(1.06)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.4 if upscaled else 1.2, percent=80 if upscaled else 65, threshold=3))
    if upscaled and VINTAGE_GRADE:
        return _vintage(img, mode)
    img = ImageEnhance.Color(img).enhance(1.05)
    if upscaled:
        img = Image.blend(img, Image.effect_noise((W, H), 30).convert("RGB"), 0.05)
    return img


_QA_JUDGE = []


def vision_check(img, story=""):
    """Ask free vision models whether the image is distorted. Retries once if they are all busy. None = unavailable."""
    if not QA_MODELS:
        return None
    try:
        small = img.copy()
        small.thumbnail((640, 800))
        buf = io.BytesIO()
        small.save(buf, "JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode()
        messages = [{"role": "user", "content": [
            {"type": "text", "text": QA_PROMPT + (
                f' The picture must illustrate this story: "{story}". Add "matches_story": true only if the main subject of the picture '
                'clearly belongs to that story (a story about a temple needs a temple; a story about a waterfall needs a waterfall; an unrelated portrait is false), otherwise false.'
                if story else "")},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}]
        for round_ in range(2):
            now = time.time()
            order = list(QA_MODELS)
            if _QA_JUDGE and _QA_JUDGE[0] in order:  # one judge for all candidates, so the scores are comparable
                order.remove(_QA_JUDGE[0])
                order.insert(0, _QA_JUDGE[0])
            if round_ == 0:  # models that were busy a moment ago go last, they usually still are
                order.sort(key=lambda m: _BUSY.get(m, 0) > now)
            for model in order:
                if round_ == 0 and _BUSY.get(model, 0) > now and any(_BUSY.get(x, 0) <= now for x in order):
                    continue
                try:
                    result = extract_json(or_call(messages, model, temperature=0, max_tokens=400))
                    _QA_JUDGE[:] = [model]
                    return result
                except Exception as e:  # noqa: BLE001
                    log(f"[image QA {model}] unavailable: {str(e)[:120]}")
            if round_ == 0:
                time.sleep(8)
    except Exception as e:  # noqa: BLE001
        log("image QA error:", e)
    return None


def generate_image(post):
    """A few candidates, alternating the writer's two ideas. First one that passes QA wins, else the best scored."""
    best, best_score, best_up = None, -1.0, False
    qa_failures = 0
    image_failures = 0
    mode = _era_mode(post)
    story = re.sub(r"[\[\]]", "", " ".join(post.get("headline_lines") or []))
    for n in range(max(1, int(os.environ.get("IMAGE_CANDIDATES", "3") or 3))):
        try:
            img, up = _fetch_candidate(post, n % 2, n)
        except CFQuotaExceeded as e:
            log(e)
            break
        except RuntimeError as e:
            log(e)
            image_failures += 1
            if image_failures >= 2 and best is None:
                break  # the image service is down: use the text card instead of waiting longer
            continue
        if not IMAGE_QA:
            return _finish(img, up, mode)
        verdict = None if qa_failures >= 2 else vision_check(img, story)
        if verdict is None:
            if qa_failures < 2:
                qa_failures += 1
                log(f"image QA unavailable for candidate #{n + 1}, kept as unrated")
            score = 5.0
        else:
            on_topic = verdict.get("matches_story") is not False
            ok = bool(verdict.get("ok")) and on_topic
            try:
                score = float(verdict.get("score", 8 if ok else 3))
            except (TypeError, ValueError):
                score = 8.0 if ok else 3.0
            if not on_topic:
                score = min(score, 4.0)
            log(f"image QA #{n + 1}: ok={ok} on_topic={on_topic} score={score} problems={verdict.get('problems')}")
            if ok and score >= 6:
                return _finish(img, up, mode)
        if score > best_score:
            best, best_score, best_up = img, score, up
    if best is not None:
        log(f"no image passed QA; using the best candidate (score {best_score})")
        return _finish(best, best_up, mode)
    raise RuntimeError("image generation failed (no candidate)")


def fallback_background():
    """If no picture can be made at all, the post still goes out on an old-paper style card."""
    base = Image.new("RGB", (W, H), (96, 74, 52))
    d = ImageDraw.Draw(base)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(120 - 50 * t), int(92 - 38 * t), int(64 - 26 * t)))
    return _vintage(base, "mono")


def load_font(size):
    path = FONT_PATH if FONT_PATH.exists() else FALLBACK_FONT
    return ImageFont.truetype(str(path), size)


def parse_segments(line):
    return [(t, RED if red else WHITE) for t, red in _runs(line) if t]


def _smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def _spaced(draw, cx, y, text, font, fill, spacing=3, shadow=True):
    total = sum(font.getlength(c) + spacing for c in text) - spacing
    x = cx - total / 2
    for c in text:
        if shadow:
            draw.text((x + 2, y + 3), c, font=font, fill=(0, 0, 0))
        draw.text((x, y), c, font=font, fill=fill)
        x += font.getlength(c) + spacing


def overlay_text(photo, headline_lines, subhook):
    """Full-bleed photo, dark gradient at the bottom, big headline with red keywords, small subline, watermarks."""
    img = photo.convert("RGB")
    if img.size != (W, H):
        img = ImageOps.fit(img, (W, H), method=Image.LANCZOS)

    grad = Image.new("L", (1, H))
    px = grad.load()
    for y in range(H):
        f = y / H
        if f < 0.50:
            a = 0.0
        elif f < 0.72:
            a = 0.90 * _smooth((f - 0.50) / 0.22)
        else:
            a = 0.90 + 0.08 * ((f - 0.72) / 0.28)
        px[0, y] = int(255 * a)
    img.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), grad.resize((W, H)))

    lines = [parse_segments(l.upper()) for l in headline_lines if l.strip()]
    max_w, max_h = W - 120, int(H * 0.29)
    size = 108
    while size > 40:
        font = load_font(size)
        lh = int(size * 1.08)
        widths = [sum(font.getlength(t) for t, _ in segs) for segs in lines]
        total = lh * len(lines) + (int(size * 0.62) if subhook.strip() else 0)
        if max(widths) <= max_w and total <= max_h:
            break
        size -= 4

    draw = ImageDraw.Draw(img)
    y = H - 104 - total
    for segs, width in zip(lines, widths):
        x = (W - width) / 2
        for text, color in segs:
            draw.text((x + 3, y + 4), text, font=font, fill=(0, 0, 0))
            draw.text((x, y), text, font=font, fill=color)
            x += font.getlength(text)
        y += lh
    sub = subhook.upper().strip()
    if sub:
        _spaced(draw, W / 2, y + int(size * 0.10), sub, load_font(max(26, int(size * 0.34))), WHITE, spacing=3)

    # watermarks: page name (bottom-left) and the AI label (bottom-right)
    if PAGE_NAME:
        wf = load_font(24)
        draw.ellipse([30, H - 46, 46, H - 30], fill=GOLD)
        x = 58
        for c in PAGE_NAME.upper():
            draw.text((x + 1, H - 46), c, font=wf, fill=(0, 0, 0))
            draw.text((x, H - 47), c, font=wf, fill=(238, 238, 238))
            x += wf.getlength(c) + 2
    if WATERMARK:
        af = load_font(20)
        label = WATERMARK.upper()
        aw = sum(af.getlength(c) + 1 for c in label)
        draw.text((W - aw - 30, H - 44), label, font=af, fill=(205, 205, 205))
    return img


# ----------------------------------------------------------------- telegram
def tg(method, **kwargs):
    r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/{method}", timeout=120, **kwargs)
    if not r.ok:
        raise RuntimeError(f"telegram {method} failed: {r.status_code} {r.text[:300]}")
    return r.json()


def send_to_telegram(img, post):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=94, subsampling=0)
    buf.seek(0)
    # 1) image alone, 2) full caption as a normal message (no 1024-char caption limit)
    tg("sendPhoto", data={"chat_id": TG_CHAT}, files={"photo": ("post.jpg", buf)})
    tg("sendMessage", data={"chat_id": TG_CHAT, "text": post["caption"].strip()[:4000]})
    if SEND_NOTES:
        notes = (
            f"Fact check notes\n"
            f"Confidence: {post.get('confidence')}\n"
            f"Fact: {post.get('fact')}\n"
            f"Sources to verify: {', '.join(post.get('sources') or [])}\n"
        )
        if post.get("issues"):
            notes += "Checker remarks: " + "; ".join(map(str, post["issues"])) + "\n"
        notes += "Image rights: AI-generated recreation, no real people or logos."
        tg("sendMessage", data={"chat_id": TG_CHAT, "text": notes[:4000]})


# ----------------------------------------------------------------- facebook
def send_to_facebook(img, post):
    """Publish image + caption together as ONE photo post on the Page."""
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=94, subsampling=0)
    buf.seek(0)
    caption = post["caption"].strip()
    if FB_DISCLOSURE:
        caption += "\n\n" + FB_DISCLOSURE
    r = requests.post(
        f"https://graph.facebook.com/{FB_VERSION}/{FB_PAGE_ID}/photos",
        data={"caption": caption, "published": "true", "access_token": FB_PAGE_TOKEN},
        files={"source": ("post.jpg", buf, "image/jpeg")},
        timeout=180,
    )
    if not r.ok:
        raise RuntimeError(f"facebook failed: HTTP {r.status_code} {r.text[:400]}")
    data = r.json()
    log("facebook post id:", data.get("post_id") or data.get("id"))


def facebook_due():
    if not (FB_PAGE_ID and FB_PAGE_TOKEN):
        return False
    return datetime.now(timezone.utc).hour % FB_EVERY_HOURS == 0


# ----------------------------------------------------------------- main
def main():
    missing = [n for n, v in (("CLOUDFLARE_ACCOUNT_ID", CF_ACCOUNT), ("CLOUDFLARE_API_TOKEN", CF_TOKEN), ("TELEGRAM_BOT_TOKEN", TG_TOKEN), ("TELEGRAM_CHAT_ID", TG_CHAT),
                              ("OPENROUTER_API_KEYS", ",".join(OR_KEYS) or ("-" if CF_TEXT else ""))) if not v]
    if missing:
        sys.exit(f"missing env vars: {', '.join(missing)}")

    load_keystate()
    setup_models()
    try:
        run()
    finally:
        log(f"OpenRouter keys usable now: {len(keys_usable())}/{len(OR_KEYS)}")
        tot = _NEURONS["text"] + _NEURONS["image"]
        log(f"WORKERS AI USAGE THIS RUN (estimate): text {_NEURONS['text']:.0f} + images {_NEURONS['image']:.0f} = {tot:.0f} neurons "
            f"({_NEURONS['calls']} text calls). Daily free limit 10000. Check the real number in the Cloudflare dashboard.")
        save_keystate()


def run():
    history = load_history()
    recent = [h["topic"] for h in history[-40:]]
    used = {h.get("source") for h in history if h.get("source")}
    RECENT_CLUSTERS[:] = [h.get("cluster") for h in history[-12:] if h.get("cluster")]
    try:
        post = build_post(recent, used)  # always returns something unless Wikipedia itself is unreachable
    except RuntimeError as e:
        log("NO POST THIS RUN (Wikipedia could not be reached):", e)
        return
    log("topic:", post["topic"])

    try:
        photo = generate_image(post)
    except Exception as e:  # noqa: BLE001
        log("no picture could be made, using the text card:", e)
        photo = fallback_background()
    img = overlay_text(photo, post["headline_lines"], post.get("subhook", "") if SHOW_SUBHOOK else "")

    sent = False
    try:
        send_to_telegram(img, post)
        sent = True
    except Exception as e:  # noqa: BLE001
        log("TELEGRAM ERROR:", e)
    if facebook_due():
        try:
            send_to_facebook(img, post)
            sent = True
        except Exception as e:  # noqa: BLE001
            log("FACEBOOK ERROR:", e)
    else:
        log("facebook skipped this hour (not configured or not due)")

    if sent:
        history.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                "topic": post["topic"],
                "source": post.get("source_title"),
                "year": post.get("year"),
                "cluster": post.get("cluster"),
                "country": post.get("country"),
            }
        )
        save_history(history)
        log("done")
    else:
        log("NOTHING WAS DELIVERED: check the Telegram and Facebook errors above")


if __name__ == "__main__":
    main()
