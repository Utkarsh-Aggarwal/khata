"""Conversation flow: extract -> ask for gaps -> confirm -> save. Nothing is saved unasked."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .extract import Extraction, extract, parse_amount
from .ledger import Ledger, format_rupees
from .names import name_key
from .reminders import ghosts, normalize_phone

MAX_ENTRY_PAISE = 10**7 * 100     # ₹1 crore: larger single entries are almost surely misreads
PHONE_W = {"number", "nambar", "numbar", "phone", "mobile", "whatsapp", "नंबर", "नम्बर", "फोन", "ਨੰਬਰ", "ਫੋਨ"}
PHONE_FILLER = {"ka", "ki", "ke", "is", "hai", "my", "of", "का", "की", "है", "ਦਾ", "ਦੀ", "ਹੈ", "no", "no."}
REMIND_W = {"remind", "reminder", "reminders", "yaad", "seance", "séance", "याद", "ਯਾਦ", "tagada", "तगादा"}

YES = {"haan", "han", "ha", "yes", "y", "ok", "okay", "confirm", "sahi", "theek", "thik", "हाँ", "हां",
       "हा", "सही", "ठीक", "ਹਾਂ", "ਠੀਕ", "✅", "1", "yes.", "save"}
NO = {"nahi", "nahin", "no", "n", "cancel", "nope", "नहीं", "नही", "रद्द", "ਨਹੀਂ", "ਨਹੀ", "❌", "0", "galat", "गलत"}
CREDIT_W = {"credit", "udhaar", "udhar", "उधार", "ਉਧਾਰ", "gave", "diya"}
# Answers to "which way did the money go?"
KIND_PAY = {"payment", "paid", "received", "jama", "जमा", "ਜਮ੍ਹਾ", "📥", "they", "he", "she", "unhone", "usne",
            "उन्होंने", "उसने", "mila", "mile", "मिले", "aaya", "aaye", "आए", "ਉਸਨੇ", "ਮਿਲੇ"}
KIND_CREDIT = {"credit", "udhaar", "udhar", "उधार", "ਉਧਾਰ", "📤", "i", "maine", "मैंने", "मैने", "ਮੈਂ", "gave",
               "diya", "diye", "दिए", "lent", "returned", "lautaye", "लौटाए", "bhara", "भरा"}
DOUBT_EXACT = {"?", "??", "kya", "kya?", "matlab", "matlab?", "samjhao", "explain", "what", "what?", "help me",
               "क्या", "क्या?", "मतलब", "मतलब?", "समझाओ", "ਕੀ", "ਕੀ?", "ਮਤਲਬ", "confused", "not sure", "pata nahi"}
DOUBT_PHRASES = ("kya matlab", "matlab kya", "samajh nahi", "samjh nahi", "what does", "what is credit",
                 "what is payment", "which one", "kaunsa", "konsa", "क्या मतलब", "समझ नहीं", "ਸਮਝ ਨਹੀਂ")
PAY_W = {"payment", "paid", "received", "jama", "जमा", "wapas", "वापस", "ਜਮ੍ਹਾ"}

M = {
    "confirm_credit": {"en": "Save: 📤 you gave {name} {amt}{due}. After this, {after}. Reply yes / no, or send a new amount.",
                       "hi": "सेव करूँ: 📤 आपने {name} को {amt} दिए (उधार){due}। इसके बाद {after}। हाँ / नहीं, या नई रकम भेजें।",
                       "pa": "ਸੇਵ ਕਰਾਂ: 📤 ਤੁਸੀਂ {name} ਨੂੰ {amt} ਦਿੱਤੇ (ਉਧਾਰ){due}। ਇਸ ਤੋਂ ਬਾਅਦ {after}। ਹਾਂ / ਨਹੀਂ, ਜਾਂ ਨਵੀਂ ਰਕਮ ਭੇਜੋ।"},
    "confirm_payment": {"en": "Save: 📥 {name} gave you {amt}. After this, {after}. Reply yes / no, or send a new amount.",
                        "hi": "सेव करूँ: 📥 {name} ने आपको {amt} दिए। इसके बाद {after}। हाँ / नहीं, या नई रकम भेजें।",
                        "pa": "ਸੇਵ ਕਰਾਂ: 📥 {name} ਨੇ ਤੁਹਾਨੂੰ {amt} ਦਿੱਤੇ। ਇਸ ਤੋਂ ਬਾਅਦ {after}। ਹਾਂ / ਨਹੀਂ, ਜਾਂ ਨਵੀਂ ਰਕਮ ਭੇਜੋ।"},
    "saved": {"en": "Saved. Now {after}.", "hi": "सेव हो गया। अब {after}।", "pa": "ਸੇਵ ਹੋ ਗਿਆ। ਹੁਣ {after}।"},
    "now_label": {"en": "Right now: ", "hi": "अभी: ", "pa": "ਹੁਣ: "},
    "owes_you": {"en": "{name} owes you {amt}", "hi": "{name} पर {amt} बाकी", "pa": "{name} ਤੇ {amt} ਬਾਕੀ"},
    "you_owe": {"en": "you owe {name} {amt}", "hi": "आपको {name} को {amt} देने हैं",
                "pa": "ਤੁਸੀਂ {name} ਨੂੰ {amt} ਦੇਣੇ ਹਨ"},
    "settled": {"en": "{name}'s account is settled (₹0)", "hi": "{name} का हिसाब बराबर (₹0)",
                "pa": "{name} ਦਾ ਹਿਸਾਬ ਬਰਾਬਰ (₹0)"},
    "explain": {"en": "📤 credit = money or goods went from you to {name}: you gave udhaar, gave cash, paid their bill "
                      "for them, or returned their money. 📥 payment = money came from {name} to you: they paid you, "
                      "paid their bill, or returned your money. {now}",
                "hi": "📤 उधार = पैसा या सामान आपसे {name} को गया: उधार दिया, नकद दिया, उनका बिल भरा, या उनके पैसे लौटाए। "
                      "📥 जमा = पैसा {name} से आपको आया: उन्होंने पैसे दिए, बिल चुकाया, या आपके पैसे लौटाए। {now}",
                "pa": "📤 ਉਧਾਰ = ਪੈਸਾ ਜਾਂ ਸਮਾਨ ਤੁਹਾਡੇ ਤੋਂ {name} ਨੂੰ ਗਿਆ। 📥 ਜਮ੍ਹਾ = ਪੈਸਾ {name} ਤੋਂ ਤੁਹਾਨੂੰ ਆਇਆ। {now}"},
    "cancelled": {"en": "Cancelled. Nothing saved.", "hi": "रद्द किया। कुछ सेव नहीं हुआ।",
                  "pa": "ਰੱਦ ਕੀਤਾ। ਕੁਝ ਸੇਵ ਨਹੀਂ ਹੋਇਆ।"},
    "discarded": {"en": "(The unsaved entry was discarded.)", "hi": "(बिना सेव की एंट्री हटा दी गई।)",
                  "pa": "(ਬਿਨਾਂ ਸੇਵ ਕੀਤੀ ਐਂਟਰੀ ਹਟਾ ਦਿੱਤੀ ਗਈ।)"},
    "export": {"en": "Download your khata as Excel: {url}", "hi": "अपना खाता Excel में डाउनलोड करें: {url}",
               "pa": "ਆਪਣਾ ਖਾਤਾ Excel ਵਿੱਚ ਡਾਊਨਲੋਡ ਕਰੋ: {url}"},
    "export_doc": {"en": "Here is your khata as an Excel file.", "hi": "आपका खाता Excel फ़ाइल में।",
                   "pa": "ਤੁਹਾਡਾ ਖਾਤਾ Excel ਫ਼ਾਈਲ ਵਿੱਚ।"},
    "export_wa": {"en": "Excel export is available in the web app.", "hi": "Excel एक्सपोर्ट वेब ऐप में है।",
                  "pa": "Excel ਐਕਸਪੋਰਟ ਵੈੱਬ ਐਪ ਵਿੱਚ ਹੈ।"},
    "phone_saved": {"en": "Saved {name}'s WhatsApp number: {phone}.", "hi": "{name} का WhatsApp नंबर सेव किया: {phone}।",
                    "pa": "{name} ਦਾ WhatsApp ਨੰਬਰ ਸੇਵ ਕੀਤਾ: {phone}।"},
    "bad_phone": {"en": "That doesn't look like a mobile number. Send 10 digits, e.g. Ramesh ka number 98765 43210.",
                  "hi": "यह मोबाइल नंबर नहीं लगता। 10 अंक भेजें, जैसे: रमेश का नंबर 98765 43210",
                  "pa": "ਇਹ ਮੋਬਾਈਲ ਨੰਬਰ ਨਹੀਂ ਲੱਗਦਾ। 10 ਅੰਕ ਭੇਜੋ, ਜਿਵੇਂ: ਰਮੇਸ਼ ਦਾ ਨੰਬਰ 98765 43210"},
    "seance": {"en": "🕯️ Séance: tap a link to send that reminder from WhatsApp.",
               "hi": "🕯️ तगादा: लिंक दबाएँ, WhatsApp में संदेश तैयार मिलेगा।",
               "pa": "🕯️ ਤਗਾਦਾ: ਲਿੰਕ ਦਬਾਓ, WhatsApp ਵਿੱਚ ਸੁਨੇਹਾ ਤਿਆਰ ਮਿਲੇਗਾ।"},
    "no_dues": {"en": "Nobody owes you anything. The ghosts rest.", "hi": "किसी पर कुछ बाकी नहीं। भूत आराम कर रहे हैं।",
                "pa": "ਕਿਸੇ ਤੇ ਕੁਝ ਬਾਕੀ ਨਹੀਂ। ਭੂਤ ਆਰਾਮ ਕਰ ਰਹੇ ਹਨ।"},
    "too_big": {"en": "That amount looks too large (over ₹1 crore).", "hi": "यह रकम बहुत बड़ी लग रही है (₹1 करोड़ से ज़्यादा)।",
                "pa": "ਇਹ ਰਕਮ ਬਹੁਤ ਵੱਡੀ ਲੱਗਦੀ ਹੈ (₹1 ਕਰੋੜ ਤੋਂ ਵੱਧ)।"},
    "ask_customer": {"en": "Who is the customer?", "hi": "ग्राहक कौन है?", "pa": "ਗਾਹਕ ਕੌਣ ਹੈ?"},
    "ask_amount": {"en": "How much (₹)?", "hi": "कितने रुपये?", "pa": "ਕਿੰਨੇ ਰੁਪਏ?"},
    "ask_kind": {"en": "Which way did the {amt} go? 📤 credit: you gave it to {name} (udhaar, cash, paid their bill, "
                       "or returned their money). 📥 payment: {name} gave it to you. Not sure? Ask '?'",
                 "hi": "{amt} किस तरफ गए? 📤 उधार: आपने {name} को दिए (उधार, नकद, उनका बिल भरा, या पैसे लौटाए)। "
                       "📥 जमा: {name} ने आपको दिए। समझ न आए तो '?' भेजें।",
                 "pa": "{amt} ਕਿਸ ਪਾਸੇ ਗਏ? 📤 ਉਧਾਰ: ਤੁਸੀਂ {name} ਨੂੰ ਦਿੱਤੇ। 📥 ਜਮ੍ਹਾ: {name} ਨੇ ਤੁਹਾਨੂੰ ਦਿੱਤੇ। "
                       "ਸਮਝ ਨਾ ਆਵੇ ਤਾਂ '?' ਭੇਜੋ।"},
    "balance": {"en": "{after}.", "hi": "{after}।", "pa": "{after}।"},
    "nobody": {"en": "No entries yet.", "hi": "अभी कोई एंट्री नहीं।", "pa": "ਹਾਲੇ ਕੋਈ ਐਂਟਰੀ ਨਹੀਂ।"},
    "undone": {"en": "Undone: {what}.", "hi": "वापस किया: {what}।", "pa": "ਵਾਪਸ ਕੀਤਾ: {what}।"},
    "nothing_undo": {"en": "Nothing to undo.", "hi": "वापस करने को कुछ नहीं।", "pa": "ਵਾਪਸ ਕਰਨ ਲਈ ਕੁਝ ਨਹੀਂ।"},
    "unknown": {"en": "I didn't get that. Try: 'Ramesh ko 500 udhaar' or 'Sita ne 200 diye'. Type help.",
                "hi": "समझ नहीं आया। लिखें: 'रमेश को 500 उधार' या 'सीता ने 200 दिए'। help लिखें।",
                "pa": "ਸਮਝ ਨਹੀਂ ਆਇਆ। ਲਿਖੋ: 'ਰਮੇਸ਼ ਨੂੰ 500 ਉਧਾਰ'। help ਲਿਖੋ।"},
    "help": {"en": "Khata: say or type 'Ramesh ko 500 udhaar' (credit), 'Ramesh ne 200 diye' (payment), "
                   "'Ramesh ka kitna baaki', 'list', 'undo', 'export', 'Ramesh ka number 98765 43210', "
                   "'remind'. You can also send a voice note or a bill photo. Every entry asks for confirmation.",
             "hi": "खाता: 'रमेश को 500 उधार', 'रमेश ने 200 दिए', 'रमेश का कितना बाकी', 'list', 'undo', "
                   "'रमेश का नंबर 98765 43210', 'याद'। वॉइस नोट या बिल की फोटो भी भेज सकते हैं। हर एंट्री पर पुष्टि होगी।",
             "pa": "ਖਾਤਾ: 'ਰਮੇਸ਼ ਨੂੰ 500 ਉਧਾਰ', 'ਰਮੇਸ਼ ਨੇ 200 ਦਿੱਤੇ', 'list', 'undo', 'ਰਮੇਸ਼ ਦਾ ਨੰਬਰ 98765 43210', "
                   "'ਯਾਦ'। ਵੌਇਸ ਨੋਟ ਜਾਂ ਬਿੱਲ ਦੀ ਫੋਟੋ ਵੀ ਭੇਜ ਸਕਦੇ ਹੋ। ਹਰ ਐਂਟਰੀ ਤੇ ਪੁਸ਼ਟੀ ਹੋਵੇਗੀ।"},
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
    base_url: str = ""

    def say(self, key: str, lang: str, **kw) -> str:
        return M[key][L(lang)].format(**kw)

    def handle(self, owner: str, text: str, *, source: str = "text", today: date | None = None) -> dict:
        text = (text or "").strip()
        if not text:
            return {"reply": self.say("help", "en"), "buttons": []}
        with self.ledger.lock:    # web threads and WhatsApp background tasks share `pending`
            # Phone numbers first: otherwise a 10-digit number could be read as an amount edit.
            # A pending entry is left untouched so the shopkeeper can still confirm it.
            phone_cmd = self._phone_command(owner, text)
            if phone_cmd:
                return phone_cmd
            p = self.pending.get(owner)
            if p and self._is_doubt(text):
                return self._explain(owner, p)        # answer the question, keep the entry waiting
            if p:
                r = self._continue(owner, p, text)
                if r is not None:
                    return r
                # Unrelated message: drop the unsaved entry so a later "yes" can't save it by surprise.
                self.pending.pop(owner, None)
                r = self._fresh(owner, text, source, today)
                r["reply"] = self.say("discarded", p.ex.lang) + "\n" + r["reply"]
                return r
            return self._fresh(owner, text, source, today)

    # ------------------------------------------------------------
    def _fresh(self, owner, text, source, today):
        low = text.lower().strip()
        if set(low.replace("/", " ").split()) & REMIND_W:
            return self._remind(owner, extract(text).lang)
        if low in ("export", "/export", "एक्सपोर्ट", "ਐਕਸਪੋਰਟ"):
            lang = extract(text).lang
            if source == "telegram":
                return self._r(self.say("export_doc", lang), export=True)
            if source == "whatsapp":
                return self._r(self.say("export_wa", lang))
            return self._r(self.say("export", lang, url=self.base_url + "/api/export.xlsx"), export=True)
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
                    return self._r(self.say("balance", lang,
                                            after=self._after(lang, real, self.ledger.balance(owner, real))))
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

    def _phone_command(self, owner: str, text: str) -> dict | None:
        """'Ramesh ka number 98765 43210' stores a WhatsApp number. Not money, so no confirm step."""
        low = text.lower()
        if not any(w in low for w in PHONE_W):
            return None
        m = re.search(r"\+?\d[\d\s-]{8,16}\d", text)
        if not m:
            return None
        lang = extract(text).lang
        phone = normalize_phone(m.group(0))
        if not phone:
            return self._r(self.say("bad_phone", lang))
        rest = (text[:m.start()] + " " + text[m.end():]).split()
        name = " ".join(t for t in (w.strip(".,:;!?") for w in rest)
                        if t and t.lower() not in PHONE_W and t.lower() not in PHONE_FILLER)[:40]
        if not name_key(name):
            return self._r(self.say("ask_customer", lang))
        real = self.ledger.find_customer(owner, name) or name.title()
        stored = self.ledger.set_phone(owner, real, phone)
        return self._r(self.say("phone_saved", lang, name=stored, phone="+" + phone), changed=True)

    def _remind(self, owner: str, lang: str) -> dict:
        gs = ghosts(self.ledger, owner, lang if lang in ("hi", "pa", "en") else "hinglish")
        if not gs:
            return self._r(self.say("no_dues", lang))
        lines = [self.say("seance", lang)]
        for g in gs[:10]:
            late = f" ⚠️ {g.days_overdue}d" if g.days_overdue else ""
            lines.append(f"👻 {g.name} {format_rupees(g.paise)}{late}: {g.wa}")
        return self._r("\n".join(lines))

    def start_from_extraction(self, owner: str, ex: Extraction, source: str) -> dict:
        """Used by voice/bill adapters: always routes through the same confirm step."""
        if ex.customer:
            real = self.ledger.find_customer(owner, ex.customer)
            ex.customer = real or ex.customer
        p = Pending(ex, source)
        self.pending[owner] = p
        return self._advance(owner, p)

    # ---- plain-language balances and doubts ------------------------------------------------
    def _after(self, lang: str, name: str, bal: int) -> str:
        if bal > 0:
            return self.say("owes_you", lang, name=name, amt=format_rupees(bal))
        if bal < 0:
            return self.say("you_owe", lang, name=name, amt=format_rupees(-bal))
        return self.say("settled", lang, name=name)

    @staticmethod
    def _is_doubt(text: str) -> bool:
        low = text.lower().strip()
        return low in DOUBT_EXACT or any(p in low for p in DOUBT_PHRASES)

    def _ask_kind(self, lang: str, ex: Extraction) -> dict:
        return self._r(self.say("ask_kind", lang, name=ex.customer, amt=format_rupees(ex.paise)),
                       buttons=["credit", "payment", "?"])

    def _explain(self, owner: str, p: Pending) -> dict:
        """'?' while an entry waits: explain what credit/payment mean here, then ask again."""
        ex, lang = p.ex, p.ex.lang
        name = ex.customer or {"en": "the customer", "hi": "ग्राहक", "pa": "ਗਾਹਕ"}[L(lang)]
        now = self._after(lang, ex.customer, self.ledger.balance(owner, ex.customer)) if ex.customer else ""
        now = (self.say("now_label", lang) + now) if now else ""
        nxt = self._advance(owner, p)
        return {**nxt, "reply": self.say("explain", lang, name=name, now=now) + "\n\n" + nxt["reply"]}

    def _advance(self, owner, p: Pending):
        ex, lang = p.ex, p.ex.lang
        if not ex.customer:
            p.awaiting = "customer"; return self._r(self.say("ask_customer", lang))
        if ex.paise is not None and ex.paise > MAX_ENTRY_PAISE:
            ex.paise = None
            p.awaiting = "amount"
            return self._r(self.say("too_big", lang) + " " + self.say("ask_amount", lang))
        if ex.paise is None:
            p.awaiting = "amount"; return self._r(self.say("ask_amount", lang))
        if ex.intent == "add_unknown":
            p.awaiting = "kind"
            return self._ask_kind(lang, ex)
        p.awaiting = "confirm"
        key = "confirm_credit" if ex.intent == "add_credit" else "confirm_payment"
        due = f" (due {ex.due_date})" if ex.due_date else ""
        delta = ex.paise if ex.intent == "add_credit" else -ex.paise
        after = self._after(lang, ex.customer, self.ledger.balance(owner, ex.customer) + delta)
        return self._r(self.say(key, lang, name=ex.customer, amt=format_rupees(ex.paise), due=due, after=after),
                       buttons=["yes", "no"], pending=True)

    def _continue(self, owner, p: Pending, text: str):
        low = text.lower().strip().rstrip(".!।॥")
        ex, lang = p.ex, p.ex.lang
        if low in NO:
            del self.pending[owner]
            return self._r(self.say("cancelled", lang))
        if p.awaiting == "customer":
            if extract(text).intent in ("undo", "help", "list", "balance") or low in ("export", "/export") \
                    or set(low.split()) & REMIND_W:
                return None
            name = " ".join(text.split())[:40]
            if not name_key(name):
                return self._r(self.say("ask_customer", lang))
            ex.customer = name.title()
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
            w = set(low.replace("📤", " 📤 ").replace("📥", " 📥 ").split())
            if w & KIND_PAY:                  # checked first: "they gave" also contains "gave"
                ex.intent = "add_payment"
            elif w & KIND_CREDIT:
                ex.intent = "add_credit"
            else:
                return self._ask_kind(lang, ex)
            return self._advance(owner, p)
        # confirm stage
        if low in YES:
            e_id = self.ledger.add_entry(
                owner, ex.customer, "credit" if ex.intent == "add_credit" else "payment",
                ex.paise, note=ex.note, entry_date=ex.entry_date, due_date=ex.due_date,
                source=p.source)
            del self.pending[owner]
            bal = self.ledger.balance(owner, ex.customer)
            return self._r(self.say("saved", lang, after=self._after(lang, ex.customer, bal)),
                           changed=True, entry_id=e_id)
        amt, _ = parse_amount(text)
        if amt is not None and len(low.split()) <= 4:    # edit amount, unless it names someone else
            other = extract(text).customer
            if other is None or self.ledger.find_customer(owner, other) == ex.customer \
                    or name_key(other) == name_key(ex.customer):
                ex.paise = amt
                return self._advance(owner, p)
            return None
        if low in ("credit", "udhaar", "उधार") or low in PAY_W:
            ex.intent = "add_credit" if low in ("credit", "udhaar", "उधार") else "add_payment"
            return self._advance(owner, p)
        return None  # treat as a brand-new message (old pending dropped by _fresh)

    @staticmethod
    def _r(reply, buttons=None, **extra):
        return {"reply": reply, "buttons": buttons or [], **extra}
