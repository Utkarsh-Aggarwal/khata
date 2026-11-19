"""Conversation flow: extract -> ask for gaps -> confirm -> save. Nothing is saved unasked."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .extract import Extraction, extract, parse_amount
from .ledger import Ledger, format_rupees

YES = {"haan", "han", "ha", "yes", "y", "ok", "okay", "confirm", "sahi", "theek", "thik", "हाँ", "हां",
       "हा", "सही", "ठीक", "ਹਾਂ", "ਠੀਕ", "✅", "1", "yes.", "save"}
NO = {"nahi", "nahin", "no", "n", "cancel", "nope", "नहीं", "नही", "रद्द", "ਨਹੀਂ", "ਨਹੀ", "❌", "0", "galat", "गलत"}
CREDIT_W = {"credit", "udhaar", "udhar", "उधार", "ਉਧਾਰ", "gave", "diya"}
PAY_W = {"payment", "paid", "received", "jama", "जमा", "wapas", "वापस", "ਜਮ੍ਹਾ"}

M = {
    "confirm_credit": {"en": "Save: {name} owes you {amt}{due}? Reply yes / no, or send a new amount to edit.",
                       "hi": "सेव करूँ: {name} पर {amt} उधार{due}? हाँ / नहीं, या नई रकम भेजें।",
                       "pa": "ਸੇਵ ਕਰਾਂ: {name} ਤੇ {amt} ਉਧਾਰ{due}? ਹਾਂ / ਨਹੀਂ, ਜਾਂ ਨਵੀਂ ਰਕਮ ਭੇਜੋ।"},
    "confirm_payment": {"en": "Save: {name} paid you {amt}? Reply yes / no, or send a new amount to edit.",
                        "hi": "सेव करूँ: {name} ने {amt} दिए? हाँ / नहीं, या नई रकम भेजें।",
                        "pa": "ਸੇਵ ਕਰਾਂ: {name} ਨੇ {amt} ਦਿੱਤੇ? ਹਾਂ / ਨਹੀਂ, ਜਾਂ ਨਵੀਂ ਰਕਮ ਭੇਜੋ।"},
    "saved": {"en": "Saved. {name} now owes {bal}.", "hi": "सेव हो गया। {name} पर अब {bal} बाकी।",
              "pa": "ਸੇਵ ਹੋ ਗਿਆ। {name} ਤੇ ਹੁਣ {bal} ਬਾਕੀ।"},
    "cancelled": {"en": "Cancelled. Nothing saved.", "hi": "रद्द किया। कुछ सेव नहीं हुआ।",
                  "pa": "ਰੱਦ ਕੀਤਾ। ਕੁਝ ਸੇਵ ਨਹੀਂ ਹੋਇਆ।"},
    "ask_customer": {"en": "Who is the customer?", "hi": "ग्राहक कौन है?", "pa": "ਗਾਹਕ ਕੌਣ ਹੈ?"},
    "ask_amount": {"en": "How much (₹)?", "hi": "कितने रुपये?", "pa": "ਕਿੰਨੇ ਰੁਪਏ?"},
    "ask_kind": {"en": "Is this credit (udhaar given) or a payment received? Reply: credit / payment.",
                 "hi": "यह उधार है या पेमेंट मिला? लिखें: उधार / जमा।",
                 "pa": "ਇਹ ਉਧਾਰ ਹੈ ਜਾਂ ਪੇਮੈਂਟ ਮਿਲੀ? ਲਿਖੋ: ਉਧਾਰ / ਜਮ੍ਹਾ।"},
    "balance": {"en": "{name} owes {bal}.", "hi": "{name} पर {bal} बाकी है।", "pa": "{name} ਤੇ {bal} ਬਾਕੀ ਹੈ।"},
    "nobody": {"en": "No entries yet.", "hi": "अभी कोई एंट्री नहीं।", "pa": "ਹਾਲੇ ਕੋਈ ਐਂਟਰੀ ਨਹੀਂ।"},
    "undone": {"en": "Undone: {what}.", "hi": "वापस किया: {what}।", "pa": "ਵਾਪਸ ਕੀਤਾ: {what}।"},
    "nothing_undo": {"en": "Nothing to undo.", "hi": "वापस करने को कुछ नहीं।", "pa": "ਵਾਪਸ ਕਰਨ ਲਈ ਕੁਝ ਨਹੀਂ।"},
    "unknown": {"en": "I didn't get that. Try: 'Ramesh ko 500 udhaar' or 'Sita ne 200 diye'. Type help.",
                "hi": "समझ नहीं आया। लिखें: 'रमेश को 500 उधार' या 'सीता ने 200 दिए'। help लिखें।",
                "pa": "ਸਮਝ ਨਹੀਂ ਆਇਆ। ਲਿਖੋ: 'ਰਮੇਸ਼ ਨੂੰ 500 ਉਧਾਰ'। help ਲਿਖੋ।"},
    "help": {"en": "Khata: say or type 'Ramesh ko 500 udhaar' (credit), 'Ramesh ne 200 diye' (payment), "
                   "'Ramesh ka kitna baaki', 'list', 'undo', 'export'. Every entry asks for confirmation.",
             "hi": "खाता: 'रमेश को 500 उधार', 'रमेश ने 200 दिए', 'रमेश का कितना बाकी', 'list', 'undo'। हर एंट्री पर पुष्टि होगी।",
             "pa": "ਖਾਤਾ: 'ਰਮੇਸ਼ ਨੂੰ 500 ਉਧਾਰ', 'ਰਮੇਸ਼ ਨੇ 200 ਦਿੱਤੇ', 'list', 'undo'। ਹਰ ਐਂਟਰੀ ਤੇ ਪੁਸ਼ਟੀ ਹੋਵੇਗੀ।"},
}


def L(lang: str) -> str:
    return "hi" if lang in ("hi", "hinglish") else ("pa" if lang == "pa" else "en")


@dataclass
class Pending:
    ex: Extraction
    source: str = "text"
    awaiting: str | None = None   # customer | amount | kind | confirm


@dataclass
class Bot:
    ledger: Ledger
    pending: dict[str, Pending] = field(default_factory=dict)

    def say(self, key: str, lang: str, **kw) -> str:
        return M[key][L(lang)].format(**kw)

    def handle(self, owner: str, text: str, *, source: str = "text", today: date | None = None) -> dict:
        text = (text or "").strip()
        if not text:
            return {"reply": self.say("help", "en"), "buttons": []}
        p = self.pending.get(owner)
        if p:
            r = self._continue(owner, p, text)
            if r is not None:
                return r
        return self._fresh(owner, text, source, today)

    # ------------------------------------------------------------
    def _fresh(self, owner, text, source, today):
        low = text.lower().strip()
        if low in ("export", "/export", "एक्सपोर्ट"):
            return {"reply": "Download: /api/export.xlsx", "buttons": [], "export": True}
        ex = extract(text, today)
        lang = ex.lang
        if ex.intent == "help":
            return self._r(self.say("help", lang))
        if ex.intent == "undo":
            e = self.ledger.undo_last(owner)
            if not e:
                return self._r(self.say("nothing_undo", lang))
            what = f"{e.customer} {format_rupees(e.paise)} ({e.kind})"
            return self._r(self.say("undone", lang, what=what), changed=True)
        if ex.intent == "list":
            b = {k: v for k, v in self.ledger.balances(owner).items() if v}
            if not b:
                return self._r(self.say("nobody", lang))
            return self._r("\n".join(f"{k}: {format_rupees(v)}" for k, v in b.items()))
        if ex.intent == "balance":
            if ex.customer:
                real = self.ledger.find_customer(owner, ex.customer)
                if real:
                    return self._r(self.say("balance", lang, name=real,
                                            bal=format_rupees(self.ledger.balance(owner, real))))
            b = {k: v for k, v in self.ledger.balances(owner).items() if v}
            if not b:
                return self._r(self.say("nobody", lang))
            return self._r("\n".join(f"{k}: {format_rupees(v)}" for k, v in b.items()))
        if ex.intent in ("add_credit", "add_payment", "add_unknown"):
            if ex.customer:
                real = self.ledger.find_customer(owner, ex.customer)
                if real:
                    ex.customer = real
            p = Pending(ex, source)
            self.pending[owner] = p
            return self._advance(owner, p)
        return self._r(self.say("unknown", lang))

    def start_from_extraction(self, owner: str, ex: Extraction, source: str) -> dict:
        """Used by voice/bill adapters: always routes through the same confirm step."""
        if ex.customer:
            real = self.ledger.find_customer(owner, ex.customer)
            ex.customer = real or ex.customer
        p = Pending(ex, source)
        self.pending[owner] = p
        return self._advance(owner, p)

    def _advance(self, owner, p: Pending):
        ex, lang = p.ex, p.ex.lang
        if not ex.customer:
            p.awaiting = "customer"; return self._r(self.say("ask_customer", lang))
        if ex.paise is None:
            p.awaiting = "amount"; return self._r(self.say("ask_amount", lang))
        if ex.intent == "add_unknown":
            p.awaiting = "kind"
            return self._r(self.say("ask_kind", lang), buttons=["credit", "payment"])
        p.awaiting = "confirm"
        key = "confirm_credit" if ex.intent == "add_credit" else "confirm_payment"
        due = f" (due {ex.due_date})" if ex.due_date else ""
        return self._r(self.say(key, lang, name=ex.customer, amt=format_rupees(ex.paise), due=due),
                       buttons=["yes", "no"], pending=True)

    def _continue(self, owner, p: Pending, text: str):
        low = text.lower().strip().rstrip(".!")
        ex, lang = p.ex, p.ex.lang
        if low in NO:
            del self.pending[owner]
            return self._r(self.say("cancelled", lang))
        if p.awaiting == "customer":
            if extract(text).intent in ("undo",):
                return None
            ex.customer = text.strip().title()
            real = self.ledger.find_customer(owner, ex.customer)
            ex.customer = real or ex.customer
            return self._advance(owner, p)
        if p.awaiting == "amount":
            amt, _ = parse_amount(text)
            if amt is None:
                return self._r(self.say("ask_amount", lang))
            ex.paise = amt
            return self._advance(owner, p)
        if p.awaiting == "kind":
            w = set(low.split())
            if w & CREDIT_W:
                ex.intent = "add_credit"
            elif w & PAY_W:
                ex.intent = "add_payment"
            else:
                return self._r(self.say("ask_kind", lang), buttons=["credit", "payment"])
            return self._advance(owner, p)
        # confirm stage
        if low in YES:
            e_id = self.ledger.add_entry(
                owner, ex.customer, "credit" if ex.intent == "add_credit" else "payment",
                ex.paise, note=ex.note, entry_date=ex.entry_date, due_date=ex.due_date,
                source=p.source)
            del self.pending[owner]
            bal = self.ledger.balance(owner, ex.customer)
            return self._r(self.say("saved", lang, name=ex.customer, bal=format_rupees(bal)),
                           changed=True, entry_id=e_id)
        amt, _ = parse_amount(text)
        if amt is not None and len(low.split()) <= 4:    # edit amount
            ex.paise = amt
            return self._advance(owner, p)
        if low in ("credit", "udhaar", "उधार") or low in PAY_W:
            ex.intent = "add_credit" if low in ("credit", "udhaar", "उधार") else "add_payment"
            return self._advance(owner, p)
        return None  # treat as a brand-new message (old pending dropped by _fresh)

    @staticmethod
    def _r(reply, buttons=None, **extra):
        return {"reply": reply, "buttons": buttons or [], **extra}
