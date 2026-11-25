"""Bill generator: the shopkeeper lists items, edits them in chat, and gets a PDF bill.

Lines are typed like "atta 2 kg 45", "sabun 2 x 35", "chai 250 g 70", "cheeni 3 kg".
  - "2 kg 45"  -> 2 kg at ₹45 per kg = ₹90
  - "2 x 35"   -> 2 at ₹35 = ₹70
  - "250 g 70" -> for g/ml the number is the price of that amount (₹70), as kacha bills write it
  - no rate    -> the remembered rate for that item (price list), else the bot asks
Edits: "atta 50" (new rate), "atta 4 kg" (new qty), "hatao tel" (remove). Money is integer paise.
The PDF is English: Hindi/Punjabi item names are romanised.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .ledger import format_rupees
from .names import _DEV, _GUR, name_key

FONTS = Path(__file__).parent / "fonts"
MAX_LINES = 60
UNIT_ALIASES = {
    "kg": "kg", "kgs": "kg", "kilo": "kg", "किलो": "kg", "ਕਿਲੋ": "kg",
    "g": "g", "gm": "g", "gms": "g", "gram": "g", "grams": "g", "ग्राम": "g", "ਗ੍ਰਾਮ": "g",
    "l": "L", "ltr": "L", "litre": "L", "liter": "L", "लीटर": "L", "ਲੀਟਰ": "L",
    "ml": "ml", "pc": "pc", "pcs": "pc", "piece": "pc", "pieces": "pc", "nos": "pc",
    "pkt": "pkt", "packet": "pkt", "packets": "pkt", "पैकेट": "pkt",
    "dozen": "dozen", "dz": "dozen", "darjan": "dozen", "दर्जन": "dozen",
}
AMOUNT_UNITS = {"g", "ml"}            # the number given is the price of that quantity
REMOVE_W = {"hatao", "hata", "hatado", "remove", "delete", "nikalo", "nikal", "हटाओ", "हटा", "निकालो", "ਹਟਾਓ"}
FILLER = {"rs", "rs.", "₹", "rate", "ka", "ki", "ke", "add", "aur", "and", "@", "per", "x", "×", "का", "की",
          "के", "और", "ਦਾ", "ਦੀ", "ਅਤੇ", "/-"}
DONE_W = {"done", "ho gaya", "hogaya", "ho gya", "bas", "finish", "ok done", "हो गया", "बस", "ਹੋ ਗਿਆ", "bill:done", "✅"}
CANCEL_W = {"cancel", "bill:cancel", "chhodo", "rehne do", "nahi chahiye", "रद्द", "ਰੱਦ"}

_NUM = r"\d+(?:\.\d+)?"
_UNIT_RE = "|".join(sorted((re.escape(u) for u in UNIT_ALIASES), key=len, reverse=True))
QTY_UNIT = re.compile(rf"({_NUM})\s*({_UNIT_RE})(?![a-z])", re.I)
QTY_X_RATE = re.compile(rf"({_NUM})\s*[x×*]\s*({_NUM})", re.I)
DIGITS = str.maketrans("०१२३४५६७८९੦੧੨੩੪੫੬੭੮੯", "01234567890123456789")


@dataclass
class Line:
    name: str
    qty: float = 1.0
    unit: str = ""
    rate: int | None = None          # paise per unit
    total: int | None = None         # paise, for g/ml lines priced as a whole

    @property
    def amount(self) -> int | None:
        if self.total is not None:
            return self.total
        return round(self.qty * self.rate) if self.rate is not None else None

    def qty_text(self) -> str:
        q = int(self.qty) if float(self.qty).is_integer() else self.qty
        return f"{q} {self.unit}".strip()


@dataclass
class Draft:
    lines: list[Line] = field(default_factory=list)
    stage: str = "editing"           # editing | payment | customer
    lang: str = "en"
    vendor_note: str = ""            # e.g. built from a bill photo

    @property
    def total(self) -> int:
        return sum(l.amount or 0 for l in self.lines)

    def missing_rates(self) -> list[Line]:
        return [l for l in self.lines if l.amount is None]


def _money(s: str) -> int:
    return round(float(s) * 100)


def parse_line(seg: str) -> Line | None:
    """One item from text. Returns None if there is no item name."""
    s = seg.translate(DIGITS).replace(",", " ").strip()
    qty, unit, rate, total = None, "", None, None
    m = QTY_X_RATE.search(s)
    if m:
        qty, rate = float(m.group(1)), _money(m.group(2))
        s = s[:m.start()] + " " + s[m.end():]
    m = QTY_UNIT.search(s)
    if m:
        qty, unit = float(m.group(1)), UNIT_ALIASES[m.group(2).lower()]
        s = s[:m.start()] + " " + s[m.end():]
    nums = re.findall(_NUM, s)
    s = re.sub(_NUM, " ", s)
    if nums:
        if len(nums) >= 2 and qty is None:
            qty, rate = float(nums[0]), _money(nums[1])
        elif unit in AMOUNT_UNITS:
            total = _money(nums[0])
        elif rate is None:
            rate = _money(nums[-1])
    words = [w.strip(".:;-") for w in s.split()]
    name = " ".join(w for w in words if w and w.lower() not in FILLER and w not in ("/",))
    if not name_key(name):
        return None
    return Line(name[:30].title() if name.isascii() else name[:30], qty if qty is not None else 1.0, unit,
                rate, total)


def segments(text: str) -> list[str]:
    return [p for p in re.split(r"[,\n;]+|\s+aur\s+|\s+and\s+|\s+और\s+", text) if p.strip()]


def _find(draft: Draft, name: str) -> Line | None:
    k = name_key(name)
    for l in draft.lines:
        lk = name_key(l.name)
        # exact match, or a prefix when both keys are long enough to be meaningful (atta != tel)
        if lk == k or (min(len(k), len(lk)) >= 3 and (lk.startswith(k) or k.startswith(lk))):
            return l
    return None


def apply(draft: Draft, text: str, price_of) -> list[str]:
    """Apply typed items/edits to the draft. `price_of(name) -> paise | None` is the price list.
    Returns short notes about what changed (for the reply)."""
    notes = []
    for seg in segments(text):
        words = set(seg.lower().split())
        if words & REMOVE_W:
            target = " ".join(w for w in seg.split() if w.lower() not in REMOVE_W)
            hit = _find(draft, target)
            if hit:
                draft.lines.remove(hit)
                notes.append(f"removed {hit.name}")
            continue
        new = parse_line(seg)
        if not new:
            continue
        old = _find(draft, new.name)
        has_qty = bool(QTY_UNIT.search(seg.translate(DIGITS)) or QTY_X_RATE.search(seg))
        if old:                                     # an edit of an existing line
            if has_qty or (new.qty != 1.0):
                old.qty, old.unit = new.qty, new.unit or old.unit
            if new.rate is not None:
                old.rate, old.total = new.rate, None
            if new.total is not None:
                old.total = new.total
            notes.append(f"updated {old.name}")
            continue
        if len(draft.lines) >= MAX_LINES:
            notes.append("bill is full")
            break
        if new.amount is None:
            remembered = price_of(new.name)
            if remembered:
                new.rate = remembered
        draft.lines.append(new)
    return notes


def render_text(draft: Draft, header: str = "🧾 Bill draft") -> str:
    if not draft.lines:
        return (header + " (empty)\nSend items like: atta 2 kg 45, sabun 2 x 35, chai 250 g 70.")
    rows = [header]
    for i, l in enumerate(draft.lines, 1):
        if l.total is not None:
            rows.append(f"{i}. {l.name} {l.qty_text()} = {format_rupees(l.total)}")
        elif l.rate is None:
            rows.append(f"{i}. {l.name} {l.qty_text()} × ₹? (send the rate, e.g. '{l.name} 45')")
        else:
            rows.append(f"{i}. {l.name} {l.qty_text()} × {format_rupees(l.rate)} = {format_rupees(l.amount)}")
    rows.append(f"Total: {format_rupees(draft.total)}")
    rows.append("Edit: 'atta 50' (rate), 'atta 4 kg' (qty), 'hatao atta', or add more items.")
    return "\n".join(rows)


# ---- PDF ------------------------------------------------------------------------------

_DEV_CONS = {chr(c) for c in range(0x0915, 0x093A)} | {chr(c) for c in range(0x0958, 0x0960)}
_GUR_CONS = {chr(c) for c in range(0x0A15, 0x0A3A)} | {chr(c) for c in range(0x0A59, 0x0A5F)}
_MARKS = {chr(c) for c in range(0x093E, 0x094E)} | {chr(c) for c in range(0x0A3E, 0x0A4E)} | {
    "\u0901", "\u0902", "\u0903", "\u093C", "\u0A01", "\u0A02", "\u0A03", "\u0A3C", "\u0A70", "\u0A71"}


def roman(text: str) -> str:
    """Readable Latin spelling for the English PDF: चावल -> Chaval, रमेश -> Ramesh."""
    out = []
    chars = list(text)
    for i, ch in enumerate(chars):
        m = _DEV.get(ch, _GUR.get(ch))
        if m is None:
            out.append(ch)
            continue
        out.append(m)
        if ch in _DEV_CONS or ch in _GUR_CONS:      # inherent 'a' unless a sign or word end follows
            nxt = chars[i + 1] if i + 1 < len(chars) else ""
            if nxt and nxt not in _MARKS and (nxt in _DEV_CONS or nxt in _GUR_CONS):
                out.append("a")
    s = "".join(out)
    s = "".join(c for c in s if ord(c) < 0x250 or c == "₹")     # only what the Latin font can draw
    return " ".join(w.capitalize() if not w.isupper() else w for w in s.split()) or "Item"


def build_pdf(draft: Draft, *, bill_no: int, customer: str | None, payment: str,
              bill_date: str | None = None) -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format="A5")
    pdf.set_auto_page_break(True, margin=12)
    pdf.add_font("Noto", "", str(FONTS / "NotoSans-Regular.ttf"))
    pdf.add_font("Noto", "B", str(FONTS / "NotoSans-Bold.ttf"))
    pdf.add_page()
    pdf.set_font("Noto", "B", 16)
    pdf.cell(0, 9, "BILL", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Noto", "", 9)
    pdf.cell(0, 5, f"Bill No: {bill_no}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"Date: {bill_date or date.today().isoformat()}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"Bill to: {roman(customer) if customer else 'Cash customer'}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    widths = [8, 52, 24, 20, 24]
    heads = ["#", "Item", "Qty", "Rate", "Amount"]
    pdf.set_font("Noto", "B", 9)
    pdf.set_fill_color(243, 231, 201)
    for w, h, a in zip(widths, heads, ["C", "L", "R", "R", "R"]):
        pdf.cell(w, 7, h, border=1, align=a, fill=True)
    pdf.ln()
    pdf.set_font("Noto", "", 9)
    for i, l in enumerate(draft.lines, 1):
        rate = "" if l.total is not None or l.rate is None else format_rupees(l.rate)
        cells = [str(i), roman(l.name)[:34], l.qty_text(), rate, format_rupees(l.amount or 0)]
        for w, c, a in zip(widths, cells, ["C", "L", "R", "R", "R"]):
            pdf.cell(w, 7, c, border=1, align=a)
        pdf.ln()
    pdf.set_font("Noto", "B", 10)
    pdf.cell(sum(widths[:-1]), 8, "Total", border=1, align="R")
    pdf.cell(widths[-1], 8, format_rupees(draft.total), border=1, align="R")
    pdf.ln(11)
    pdf.set_font("Noto", "", 9)
    pdf.cell(0, 5, f"Payment: {'Cash' if payment == 'cash' else 'Udhaar (credit)'}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_text_color(110, 110, 110)
    pdf.set_font("Noto", "", 7)
    pdf.cell(0, 4, "Generated by Khata", align="C")
    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()
