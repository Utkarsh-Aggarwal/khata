"""One rendering of the khata, used by the Google Sheet mirror and the Excel export.

The sheet is a view of the database: it is redrawn from scratch on every change, sorted by
date, with fixed columns, so the bot, the sheet and the Excel file can never disagree.
"""
from __future__ import annotations

from datetime import date

from .ledger import Ledger, format_rupees

COLUMNS = ["Ref", "Date", "Type", "Name", "Category", "Out (₹)", "In (₹)",
           "Balance after (₹, + they owe you)", "Due", "Status", "Via", "Note", "Recorded on"]
COL = {c: i for i, c in enumerate(COLUMNS)}

TYPE_LABEL = {"credit": "Udhaar given", "payment": "Payment received", "expense": "Expense"}
TYPE_OF_LABEL = {v.lower(): k for k, v in TYPE_LABEL.items()}

CATEGORY_LABEL = {
    "electricity": "Electricity", "rent": "Rent", "salary": "Salary", "transport": "Transport",
    "stock": "Stock", "phone": "Phone/internet", "water": "Water", "gas": "Gas", "tea": "Tea/snacks",
    "repair": "Repair", "purchase": "Purchase", "other": "Other",
}
CATEGORY_OF_LABEL = {v.lower(): k for k, v in CATEGORY_LABEL.items()}

VIA_LABEL = {"voice": "Voice note", "telegram": "Telegram", "bill": "Bill photo", "text": "Web chat",
             "whatsapp": "WhatsApp", "sheet": "Restored", "seed": "Sample"}


def _rupees(paise: int) -> float | int:
    return paise // 100 if paise % 100 == 0 else paise / 100


def ledger_rows(ledger: Ledger, owner: str) -> list[list]:
    """Every entry and expense of `owner`, oldest first, with each customer's running balance."""
    recs = []
    with ledger.lock:
        for r in ledger.db.execute(
                "SELECT e.*, c.name AS cname FROM entries e JOIN customers c ON c.id=e.customer_id "
                "WHERE e.owner=?", (owner,)):
            recs.append(("K", r))
        for r in ledger.db.execute("SELECT * FROM expenses WHERE owner=?", (owner,)):
            recs.append(("E", r))
    recs.sort(key=lambda t: (t[1]["entry_date"], t[1]["created_at"], t[0], t[1]["id"]))

    running: dict[str, int] = {}
    rows = []
    for tag, r in recs:
        active = r["status"] == "active"
        out = inn = bal = ""
        if tag == "K":
            kind, name, category = r["kind"], r["cname"], ""
            if kind == "credit":
                out = _rupees(r["paise"])
            else:
                inn = _rupees(r["paise"])
            if active:
                running[name] = running.get(name, 0) + (r["paise"] if kind == "credit" else -r["paise"])
                bal = _rupees(running[name]) if running[name] >= 0 else -_rupees(-running[name])
            due = r["due_date"] or ""
        else:
            kind, name, due = "expense", r["vendor"] or "", ""
            category = CATEGORY_LABEL.get(r["category"], r["category"])
            out = _rupees(r["paise"])
        note = r["note"] or ""
        if note.lower() == "bill photo":         # already said by the Via column
            note = ""
        rows.append([f"{tag}-{r['id']}", r["entry_date"], TYPE_LABEL[kind], name, category, out, inn, bal, due,
                     "active" if active else "cancelled", VIA_LABEL.get(r["source"] or "", r["source"] or ""),
                     note, (r["created_at"] or "")[:10]])
    return rows


def summary_rows(ledger: Ledger, owner: str, today: date | None = None) -> list[list]:
    """The same numbers the bot shows for /dues and /expenses, as a small table."""
    today = today or date.today()
    b = {k: v for k, v in ledger.balances(owner).items() if v}
    give = [(k, v) for k, v in b.items() if v > 0]
    take = sorted(((k, -v) for k, v in b.items() if v < 0), key=lambda kv: -kv[1])
    rows: list[list] = [["Khata summary", f"as of {today.isoformat()}"], []]
    rows.append(["Udhaar you gave (they owe you)", _rupees(sum(v for _, v in give))])
    rows += [["  " + k, _rupees(v)] for k, v in give]
    rows.append(["Udhaar you took (you owe them)", _rupees(sum(v for _, v in take))])
    rows += [["  " + k, _rupees(v)] for k, v in take]
    net = sum(b.values())
    rows.append(["Net: you will receive" if net >= 0 else "Net: you have to pay", _rupees(abs(net))])
    rows.append([])

    months: dict[str, dict[str, int]] = {}
    for x in ledger.expenses(owner):
        months.setdefault(x.entry_date[:7], {}).setdefault(x.category, 0)
        months[x.entry_date[:7]][x.category] += x.paise
    rows.append(["Shop expenses by month", "Category", "Spent (₹)"])
    if not months:
        rows.append(["(none yet)"])
    for m in sorted(months, reverse=True):
        cats = months[m]
        rows.append([m, "Total", _rupees(sum(cats.values()))])
        rows += [["", CATEGORY_LABEL.get(c, c), _rupees(v)] for c, v in sorted(cats.items(), key=lambda kv: -kv[1])]
    return rows


def parse_row(row: list) -> dict:
    """Read one rendered row back (for restore). Raises ValueError on anything malformed."""
    row = list(row) + [""] * (len(COLUMNS) - len(row))
    ref = str(row[COL["Ref"]]).strip().upper()
    if len(ref) < 3 or ref[:2] not in ("K-", "E-") or not ref[2:].isdigit():
        raise ValueError(ref)
    kind = TYPE_OF_LABEL.get(str(row[COL["Type"]]).strip().lower())
    if kind is None or (kind == "expense") != (ref[0] == "E"):
        raise ValueError(ref)
    amount = row[COL["Out (₹)"]] if kind in ("credit", "expense") else row[COL["In (₹)"]]
    paise = round(float(str(amount).replace(",", "").replace("₹", "")) * 100)
    cat = str(row[COL["Category"]]).strip()
    via = {v.lower(): k for k, v in VIA_LABEL.items()}.get(str(row[COL["Via"]]).strip().lower(), "sheet")
    return {"id": int(ref[2:]), "kind": kind, "date": str(row[COL["Date"]])[:10],
            "name": str(row[COL["Name"]]).strip(), "category": CATEGORY_OF_LABEL.get(cat.lower(), cat.lower()),
            "paise": paise, "due": str(row[COL["Due"]])[:10] or None,
            "status": str(row[COL["Status"]]).strip() or "active", "note": str(row[COL["Note"]]),
            "via": via, "recorded": str(row[COL["Recorded on"]])[:10]}


def fmt(paise: int) -> str:
    return format_rupees(paise)
