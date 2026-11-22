"""Rule-based extractor (the always-on 'demo mode' brain). Hindi, Punjabi, Hinglish, English.

Returns an Extraction; never touches the ledger. The conversation layer confirms before saving.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from fractions import Fraction

# ---------- numbers ----------
_HI = ("ek do teen char paanch chhah saat aath nau das gyarah barah terah chaudah pandrah "
       "solah satrah atharah unnis bees ikkis baaees teis chaubis pachchis chhabbis sattais "
       "atthais untis tees ikattis battis taintis chauntis paintis chhattis saintis artis "
       "untalis chalis iktalis bayalis taintalis chauvalis paintalis chhiyalis saintalis "
       "artalis unchas pachas ikyavan bavan tirpan chauvan pachpan chhappan sattavan atthavan "
       "unsath saath iksath baasath tirsath chausath painsath chhiyasath sadsath adsath "
       "unhattar sattar ikhattar bahattar tihattar chauhattar pachhattar chhihattar satattar "
       "athattar unasi assi ikyasi bayasi tirasi chaurasi pachasi chhiyasi sattasi atthasi "
       "nauasi nabbe ikyanve baanve tiranve chauranve pachanve chhiyanve sattanve atthanve "
       "ninyanve").split()
WORDS: dict[str, Fraction] = {w: Fraction(i + 1) for i, w in enumerate(_HI)}
WORDS.update({"panch": Fraction(5), "chhe": Fraction(6), "che": Fraction(6), "sath": Fraction(60),
              "dus": Fraction(10), "bis": Fraction(20), "tis": Fraction(30), "chaalis": Fraction(40),
              "pachaas": Fraction(50), "dedh": Fraction(3, 2), "dhai": Fraction(5, 2),
              "dhaai": Fraction(5, 2), "adha": Fraction(1, 2), "aadha": Fraction(1, 2)})
# Modifiers act on the next number: sawa do = 2.25, saadhe teen = 3.5, paune do = 1.75.
# Directly before a multiplier they mean 1 +/- x: sawa sau = 125, paune sau = 75.
MODS = {"sawa": Fraction(1, 4), "savva": Fraction(1, 4), "सवा": Fraction(1, 4), "ਸਵਾ": Fraction(1, 4),
        "saadhe": Fraction(1, 2), "sadhe": Fraction(1, 2), "sade": Fraction(1, 2),
        "साढ़े": Fraction(1, 2), "साढे": Fraction(1, 2), "ਸਾਢੇ": Fraction(1, 2),
        "paune": Fraction(-1, 4), "pone": Fraction(-1, 4), "पौने": Fraction(-1, 4), "ਪੌਣੇ": Fraction(-1, 4)}
_EN = ("one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
       "fifteen sixteen seventeen eighteen nineteen").split()
WORDS.update({w: Fraction(i + 1) for i, w in enumerate(_EN)})
WORDS.update({"twenty": Fraction(20), "thirty": Fraction(30), "forty": Fraction(40),
              "fifty": Fraction(50), "sixty": Fraction(60), "seventy": Fraction(70),
              "eighty": Fraction(80), "ninety": Fraction(90)})
MULT = {"sau": 100, "so": 100, "hundred": 100, "hazaar": 1000, "hazar": 1000, "hajar": 1000,
        "thousand": 1000, "lakh": 100000, "lac": 100000, "lakhs": 100000, "crore": 10**7}
# Devanagari / Gurmukhi number words
WORDS.update({"एक": Fraction(1), "दो": Fraction(2), "तीन": Fraction(3), "चार": Fraction(4),
              "पांच": Fraction(5), "पाँच": Fraction(5), "छह": Fraction(6), "सात": Fraction(7),
              "आठ": Fraction(8), "नौ": Fraction(9), "दस": Fraction(10), "बीस": Fraction(20),
              "तीस": Fraction(30), "चालीस": Fraction(40), "पचास": Fraction(50),
              "साठ": Fraction(60), "सत्तर": Fraction(70), "अस्सी": Fraction(80),
              "नब्बे": Fraction(90), "पंद्रह": Fraction(15), "पच्चीस": Fraction(25),
              "डेढ़": Fraction(3, 2), "ढाई": Fraction(5, 2),
              "ਇੱਕ": Fraction(1), "ਦੋ": Fraction(2), "ਤਿੰਨ": Fraction(3), "ਚਾਰ": Fraction(4),
              "ਪੰਜ": Fraction(5), "ਛੇ": Fraction(6), "ਸੱਤ": Fraction(7), "ਅੱਠ": Fraction(8),
              "ਨੌ": Fraction(9), "ਦਸ": Fraction(10), "ਵੀਹ": Fraction(20), "ਤੀਹ": Fraction(30),
              "ਪੰਜਾਹ": Fraction(50), "ਡੇਢ": Fraction(3, 2), "ਢਾਈ": Fraction(5, 2)})
MULT.update({"सौ": 100, "हज़ार": 1000, "हजार": 1000, "लाख": 100000,
             "ਸੌ": 100, "ਹਜ਼ਾਰ": 1000, "ਹਜ਼ਾਰ": 1000, "ਲੱਖ": 100000})

_DIGITS = {c: str(i) for i, c in enumerate("०१२३४५६७८९")}
_DIGITS.update({c: str(i) for i, c in enumerate("੦੧੨੩੪੫੬੭੮੯")})

PUNCT = ".,;:!?()[]{}\"'-।॥"     # includes the Devanagari danda that Whisper adds to Hindi
NUM_RE = re.compile(r"\d+(?:,\d{2,3})*(?:\.\d+)?")


def _norm(text: str) -> str:
    return "".join(_DIGITS.get(c, c) for c in text)


def parse_amount(text: str) -> tuple[int | None, set[int]]:
    """Return (paise, token indexes consumed)."""
    toks = _norm(text).replace("₹", " ₹ ").split()
    low = [t.lower().strip(PUNCT) for t in toks]
    used: set[int] = set()

    def is_num(t: str) -> bool:
        return bool(NUM_RE.fullmatch(t.replace(",", "") if re.fullmatch(r"[\d,]+(\.\d+)?", t) else t))

    best: tuple[int, set[int]] | None = None
    i = 0
    while i < len(low):
        t = low[i]
        if re.fullmatch(r"[\d,]+(\.\d+)?", t) and re.search(r"\d", t):
            val = Fraction(t.replace(",", ""))
            idx = {i}
            j = i + 1
            if j < len(low) and low[j] in MULT:
                val *= MULT[low[j]]; idx.add(j)
            elif j < len(low) and re.fullmatch(r"k", low[j]):
                val *= 1000; idx.add(j)
            cand = (int(val * 100), idx)
        elif re.fullmatch(r"\d+(\.\d+)?k", t):
            cand = (int(Fraction(t[:-1]) * 1000 * 100), {i})
        elif t in WORDS or t in MULT or t in MODS:
            total, cur, idx, j, seen = Fraction(0), Fraction(0), set(), i, False
            mod: Fraction | None = None
            while j < len(low) and (low[j] in WORDS or low[j] in MULT or low[j] in MODS):
                w = low[j]
                if w in MODS:
                    mod = MODS[w]
                elif w in WORDS:
                    cur += WORDS[w] + (mod or 0); mod = None
                else:
                    m = MULT[w]
                    base = cur or (1 + mod if mod is not None else 1)
                    mod = None
                    if m >= 1000:
                        total += base * m; cur = Fraction(0)
                    else:
                        cur = base * m
                idx.add(j); j += 1; seen = True
            val = total + cur
            if not seen or val == 0:
                i += 1; continue
            cand = (int(val * 100), idx)
        else:
            i += 1; continue
        # prefer candidate adjacent to a currency marker, else the largest
        near = any(k in low[max(0, min(cand[1]) - 1):max(cand[1]) + 2]
                   for k in ("rs", "rs.", "₹", "rupay", "rupaye", "rupees", "rupee", "रुपये",
                             "रुपए", "रूपये", "रुपया", "ਰੁਪਏ", "inr", "रु"))
        if best is None or near or (not best[0] == -1 and cand[0] > best[0]):
            if best is None or near or cand[0] > best[0]:
                best = (cand[0], cand[1])
        if near:
            break
        i = max(cand[1]) + 1
    if best:
        return best[0], best[1]
    return None, used


# ---------- intent ----------
CREDIT = {"udhaar", "udhar", "उधार", "उधारी", "ਉਧਾਰ", "credit", "baaki", "baki", "बाकी",
          "borrowed", "owes", "lia", "liya", "लिया", "ले", "ਲਿਆ", "ਉਧਾਰੀ", "udhari"}
PAYMENT = {"paid", "payment", "received", "jama", "जमा", "वापस", "wapas", "vapas", "returned",
           "diye", "diya", "दिए", "दिये", "दिया", "ਦਿੱਤੇ", "ਦਿੱਤਾ", "ਮੋੜੇ", "चुकाए", "chukaye",
           "settled", "gave", "दे", "de", "ਦੇ", "mile", "मिले", "ਮਿਲੇ", "aaye", "आए"}
BAL = {"balance", "kitna", "कितना", "ਕਿੰਨਾ", "hisaab", "hisab", "हिसाब", "ਹਿਸਾਬ", "owe", "dena",
       "देना", "baqaya", "check", "statement"}
LIST = {"list", "all", "sab", "सब", "ਸਾਰੇ", "everyone", "dues", "summary", "report", "history"}
UNDO = {"undo", "wapas", "galti", "गलती", "ਗਲਤੀ", "delete", "remove", "hatao", "हटाओ", "ਹਟਾਓ"}
HELP = {"help", "madad", "मदद", "ਮਦਦ", "start", "hi", "hello", "namaste", "नमस्ते", "ਸਤ"}

NE = {"ne", "ने", "ਨੇ"}
KO = {"ko", "को", "ਨੂੰ", "se", "से", "ਤੋਂ"}
PAST = {"diya", "diye", "liya", "liye", "tha", "thi", "gaya", "gayi", "paid", "gave", "received",
        "दिया", "दिये", "दिए", "लिया", "था", "थे", "गया", "ਦਿੱਤਾ", "ਦਿੱਤੇ", "ਲਿਆ", "ਸੀ",
        "mile", "aaye", "मिले", "आए", "ਮਿਲੇ", "borrowed", "returned", "settled", "jama"}
STOP = (CREDIT | PAYMENT | BAL | LIST | UNDO | HELP | NE | KO | PAST | {
    "kal", "कल", "ਕੱਲ", "ਕਲ", "parso", "परसों", "aaj", "आज", "ਅੱਜ", "ka", "ki", "ke", "ko", "ne", "ka",
    "का", "की", "के", "ਦਾ", "ਦੀ", "ਦੇ", "rs", "rupay", "rupaye", "rupees", "rupee", "रुपये",
    "रुपए", "ਰੁਪਏ", "₹", "inr", "ka", "hai", "hain", "है", "ਹੈ", "mein", "me", "में", "the",
    "to", "for", "from", "of", "a", "and", "aur", "और", "ਅਤੇ", "dunga", "dega", "dungi",
    "देगा", "देंगे", "दूँगा", "दूंगा", "ਦੇਵਾਂਗਾ", "ਦੇਵੇਗਾ", "will", "pay", "back", "on", "due",
    "by", "kiye", "kiya", "kie", "kar", "karo", "किए", "किये", "किया", "कर", "ਕੀਤੇ", "ਕੀਤਾ",
    "degi", "denge", "dedega", "dedegi", "देगी", "ਦੇਵੇਗੀ", "hua", "hui", "हुआ", "wapis", "kuchh", "kuch", "रु", "please", "bhai", "ji", "जी", "ਜੀ", "भाई", "ਭਾਈ", "din", "दिन", "give", "credit", "upi", "cash", "gpay", "paytm", "phonepe", "online",
    "batao", "bata", "बताओ", "ਦੱਸੋ", "likh", "likho", "लिखो", "लिख", "ਲਿਖੋ", "do", "dena", "entry",
    "pichhli", "pichli", "last", "पिछली", "ਪਿਛਲੀ", "gaye", "gaya", "गए", "poore", "pure", "पूरे",
    # the shopkeeper speaking: "मैंने रमेश को..." / "Maine Ramesh ko..." is never part of a name
    "maine", "mainne", "mainey", "main", "mai", "hamne", "humne", "ham", "hum", "i", "we", "maina",
    "मैंने", "मैने", "मैं", "मै", "हमने", "हम", "ਮੈਂ", "ਮੈਂਨੇ", "ਅਸੀਂ"}) \
    | set(WORDS) | set(MULT) | set(MODS)
RECEIVED = {"mil", "mila", "mile", "मिल", "मिला", "मिले", "ਮਿਲ", "ਮਿਲੇ", "bheje", "bheja", "भेजे",
            "भेजा", "ਭੇਜੇ", "aaye", "आए", "received", "got"}
STOP |= RECEIVED


@dataclass
class Extraction:
    intent: str                      # add_credit | add_payment | add_unknown | balance | list | undo | help | unknown
    customer: str | None = None
    paise: int | None = None
    entry_date: str | None = None
    due_date: str | None = None
    note: str = ""
    lang: str = "en"
    missing: list[str] = field(default_factory=list)


def detect_lang(text: str) -> str:
    if re.search(r"[\u0A00-\u0A7F]", text):
        return "pa"
    if re.search(r"[\u0900-\u097F]", text):
        return "hi"
    low = set(re.findall(r"[a-z]+", text.lower()))
    if low & {"ko", "ne", "udhaar", "udhar", "kal", "diye", "diya", "kitna", "hai", "baaki",
              "rupay", "rupaye", "hisaab", "liya", "wapas", "paanch", "sau", "hazaar", "ka"}:
        return "hinglish"
    return "en"


def _tok(text: str) -> list[str]:
    return [t for t in re.split(r"[\s,.;:!?।॥]+", _norm(text)) if t]


# A name sits directly before a postposition/honorific (Ramesh ko, Gupta ji, ਰਮੇਸ਼ ਨੂੰ),
# or directly after an English preposition (from Mohan, by Sita, to Arjun).
POSTPOS = {"ko", "ne", "ka", "ki", "ke", "se", "ji", "bhai", "sahab", "ko.", "को", "ने", "का", "की",
           "के", "से", "जी", "भाई", "ਨੂੰ", "ਨੇ", "ਦਾ", "ਦੀ", "ਦੇ", "ਤੋਂ", "ਜੀ", "ਭਾਈ"}
PREPOS = {"from", "by", "to", "give", "for"}


def _is_word(t: str, i: int, used: set[int]) -> bool:
    return bool(t) and i not in used and t.lower() not in STOP and not re.search(r"\d", t) \
        and not t.startswith("/")


def _name_tokens(clean: list[str], used: set[int]) -> list[str]:
    for i, t in enumerate(clean):
        if t.lower() in POSTPOS:
            j, run = i - 1, []
            while j >= 0 and _is_word(clean[j], j, used) and len(run) < 3:
                run.insert(0, clean[j]); j -= 1
            if run:
                return run
    for i, t in enumerate(clean):
        if t.lower() in PREPOS:
            j, run = i + 1, []
            while j < len(clean) and _is_word(clean[j], j, used) and len(run) < 3:
                run.append(clean[j]); j += 1
            if run:
                return run
    run: list[str] = []
    for i, t in enumerate(clean):            # fallback: first contiguous run of non-keywords
        if _is_word(t, i, used):
            run.append(t)
            if len(run) == 3:
                break
        elif run:
            break
    return run


def _wants_undo(s: set[str]) -> bool:
    return bool(s & {"delete", "remove", "hatao", "hata", "cancel", "हटाओ", "ਹਟਾਓ"}
                and s & {"last", "pichhli", "pichli", "entry", "पिछली", "एंट्री", "ਪਿਛਲੀ", "ਐਂਟਰੀ"})


def extract(text: str, today: date | None = None) -> Extraction:
    today = today or date.today()
    lang = detect_lang(text)
    toks = _tok(text)
    low = [t.lower() for t in toks]
    s = set(low)

    if s & {"undo", "galti", "गलती", "ਗਲਤੀ"} or _wants_undo(s) or (low[:1] and low[0] in {"delete", "remove", "hatao", "हटाओ", "ਹਟਾਓ"} and "last" in s):
        return Extraction("undo", lang=lang)
    if low and low[0] in {"help", "madad", "मदद", "ਮਦਦ", "/help", "/start", "hi", "hello", "namaste", "नमस्ते"}:
        return Extraction("help", lang=lang)

    paise, used = parse_amount(text)
    ntok = [t for t in _norm(text).replace("₹", " ₹ ").split()]
    clean = [t.strip(PUNCT) for t in ntok]
    name_toks = _name_tokens(clean, used)
    customer = " ".join(name_toks).title() if name_toks else None
    # latin names title-cased; native scripts untouched by title()

    # direction
    if s & BAL and paise is None and "udhaar" not in s:
        return Extraction("balance", customer=customer, lang=lang)
    if s & BAL and not (s & (CREDIT | PAYMENT)) and paise is None:
        return Extraction("balance", customer=customer, lang=lang)
    if (s & LIST) and paise is None:
        return Extraction("list", lang=lang)
    if paise is None and not (s & (CREDIT | PAYMENT)):
        if customer and len(name_toks) == 1 and ("?" in text or s & {"kitna", "कितना", "ਕਿੰਨਾ"}):
            return Extraction("balance", customer=customer, lang=lang)
        return Extraction("unknown", lang=lang)

    ne = bool(s & NE)
    ko = bool(s & KO)
    SETTLE = {"paid", "received", "jama", "जमा", "payment", "returned", "wapas", "vapas", "वापस",
              "settled", "ਮੋੜੇ", "चुकाए", "chukaye"} | RECEIVED
    if s & SETTLE:
        kind = "add_payment"
    elif s & CREDIT:
        kind = "add_credit"
    elif ne and (s & (PAYMENT | PAST)):
        kind = "add_payment"
    elif ko and (s & PAYMENT):
        kind = "add_credit"            # "Ramesh ko 500 diye" -> gave goods/cash to Ramesh
    else:
        kind = "add_unknown"

    ex = Extraction(kind, customer=customer, paise=paise, lang=lang)

    # dates: 'kal' is yesterday in past tense, tomorrow in future tense
    past = bool(s & PAST)
    rel = None
    if s & {"kal", "कल", "ਕੱਲ", "ਕਲ"}:
        rel = -1 if past else 1
    elif s & {"parso", "परसों", "ਪਰਸੋਂ"}:
        rel = -2 if past else 2
    elif s & {"aaj", "आज", "ਅੱਜ", "today"}:
        rel = 0
    elif "tomorrow" in s:
        rel = 1
    elif "yesterday" in s:
        rel = -1
    m = re.search(r"(\d+)\s*(?:din|days?|दिन)", _norm(text).lower())
    if m and not past:
        rel = int(m.group(1))
    if rel is not None:
        d = (today + timedelta(days=rel)).isoformat()
        if kind == "add_credit" and rel > 0:
            ex.due_date = d
        elif rel <= 0:
            ex.entry_date = d
        else:
            ex.due_date = d
    if not ex.customer:
        ex.missing.append("customer")
    if paise is None:
        ex.missing.append("amount")
    if kind == "add_unknown":
        ex.missing.append("kind")
    return ex
