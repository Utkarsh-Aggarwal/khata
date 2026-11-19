"""Cross-script name matching: Devanagari/Gurmukhi/Latin collapse to one phonetic key."""
import re
import unicodedata

_DEV = {
    "अ": "a", "आ": "a", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u", "ए": "e", "ऐ": "e",
    "ओ": "o", "औ": "o", "ऋ": "ri",
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d",
    "ध": "dh", "न": "n", "प": "p", "फ": "f", "ब": "b", "भ": "bh", "म": "m", "य": "y",
    "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h", "ळ": "l",
    "ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "े": "e", "ै": "e", "ो": "o",
    "ौ": "o", "ं": "n", "ँ": "n", "ः": "", "्": "", "़": "", "ृ": "ri",
}
_GUR = {
    "ਅ": "a", "ਆ": "a", "ਇ": "i", "ਈ": "i", "ਉ": "u", "ਊ": "u", "ਏ": "e", "ਐ": "e",
    "ਓ": "o", "ਔ": "o",
    "ਕ": "k", "ਖ": "kh", "ਗ": "g", "ਘ": "gh", "ਚ": "ch", "ਛ": "chh", "ਜ": "j", "ਝ": "jh",
    "ਟ": "t", "ਠ": "th", "ਡ": "d", "ਢ": "dh", "ਣ": "n", "ਤ": "t", "ਥ": "th", "ਦ": "d",
    "ਧ": "dh", "ਨ": "n", "ਪ": "p", "ਫ": "f", "ਬ": "b", "ਭ": "bh", "ਮ": "m", "ਯ": "y",
    "ਰ": "r", "ਲ": "l", "ਵ": "v", "ਸ਼": "sh", "ਸ": "s", "ਹ": "h",
    "ਾ": "a", "ਿ": "i", "ੀ": "i", "ੁ": "u", "ੂ": "u", "ੇ": "e", "ੈ": "e", "ੋ": "o",
    "ੌ": "o", "ਂ": "n", "ੰ": "n", "ਁ": "n", "੍": "", "਼": "",
}


def translit(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    return "".join(_DEV.get(c) if c in _DEV else _GUR.get(c, c) for c in s)


def name_key(name: str) -> str:
    """Phonetic skeleton: 'Ramesh' == 'रमेश' == 'ਰਮੇਸ਼' == 'Rameshh'."""
    t = translit(name).lower()
    t = re.sub(r"[^a-z]", "", t)
    t = t.replace("w", "v").replace("z", "j").replace("ph", "f").replace("c", "k")
    t = re.sub(r"[aeiouy]", "", t)       # drop vowels (schwa deletion makes them unreliable)
    t = t.replace("h", "")               # drop aspiration
    t = re.sub(r"(.)\1+", r"\1", t)
    if not t:  # all-vowel names like "Asha"->"sh" are fine; "A"/"Ee" fall back to the vowels
        t = re.sub(r"(.)\1+", r"\1", re.sub(r"[^a-z]", "", translit(name).lower()))
    return t
