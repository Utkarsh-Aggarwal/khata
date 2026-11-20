"""Séance: drafted WhatsApp reminders for customers who owe money.

The app never sends reminders itself. It builds wa.me links that open WhatsApp with the
message typed out, and the shopkeeper decides whether to send. Without a stored number the
link opens WhatsApp's contact picker.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from .ledger import Ledger, format_rupees

LANGS = ("hinglish", "hi", "pa", "en")
MSG = {
    "hinglish": "Namaste {name} ji, aapke khate mein {amt} baaki hai{late}. Kripya jaldi chuka dein. 🙏",
    "hi": "नमस्ते {name} जी, आपके खाते में {amt} बाकी है{late}। कृपया जल्दी चुका दें। 🙏",
    "pa": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ {name} ਜੀ, ਤੁਹਾਡੇ ਖਾਤੇ ਵਿੱਚ {amt} ਬਾਕੀ ਹਨ{late}। ਕਿਰਪਾ ਕਰਕੇ ਜਲਦੀ ਚੁਕਾ ਦਿਓ। 🙏",
    "en": "Namaste {name} ji, {amt} is due on your khata{late}. Please clear it soon. 🙏",
}
LATE = {
    "hinglish": " ({d} din se overdue)", "hi": " ({d} दिन से बकाया)", "pa": " ({d} ਦਿਨਾਂ ਤੋਂ ਬਕਾਇਆ)",
    "en": " ({d} days overdue)",
}


def normalize_phone(raw: str) -> str | None:
    """Indian mobile numbers to 91XXXXXXXXXX; other numbers must already carry a country code."""
    d = re.sub(r"\D", "", raw or "")
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(d) == 10 and d[0] in "6789":
        return "91" + d
    if len(d) == 12 and d.startswith("91") and d[2] in "6789":
        return d
    if raw.strip().startswith("+") and 11 <= len(d) <= 15:
        return d
    return None


@dataclass
class Ghost:
    name: str
    paise: int
    days_overdue: int
    phone: str | None
    message: str
    wa: str

    def as_dict(self) -> dict:
        return {"name": self.name, "paise": self.paise, "balance": format_rupees(self.paise),
                "days_overdue": self.days_overdue, "overdue": self.days_overdue > 0,
                "has_phone": bool(self.phone), "message": self.message, "wa": self.wa}


def ghosts(ledger: Ledger, owner: str, lang: str = "hinglish", today: date | None = None) -> list[Ghost]:
    """Everyone who owes money, most overdue first, then largest balance."""
    lang = lang if lang in LANGS else "hinglish"
    today = today or date.today()
    phones = ledger.phones(owner)
    first_due: dict[str, str] = {}
    for e in ledger.overdue(owner, today.isoformat()):
        first_due[e.customer] = min(first_due.get(e.customer, e.due_date), e.due_date)
    out = []
    for name, paise in ledger.balances(owner).items():
        if paise <= 0:
            continue
        days = (today - date.fromisoformat(first_due[name])).days if name in first_due else 0
        late = LATE[lang].format(d=days) if days > 0 else ""
        msg = MSG[lang].format(name=name, amt=format_rupees(paise), late=late)
        phone = phones.get(name)
        out.append(Ghost(name, paise, days, phone, msg, f"https://wa.me/{phone or ''}?text={quote(msg)}"))
    out.sort(key=lambda g: (-g.days_overdue, -g.paise))
    return out
