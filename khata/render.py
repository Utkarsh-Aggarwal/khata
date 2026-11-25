"""One rendering of the khata, used by the Google Sheet mirror and the Excel export.

Two tables, both redrawn from the database on every change, so the bot, the sheet and the
Excel file never disagree:
  Transactions: one row per active entry or expense, sorted by date.
  Dues:         one row per person: credit given, paid back, borrowed, and who owes whom.
"""
from __future__ import annotations

from datetime import date

from .ledger import Ledger

TX_TAB, DUES_TAB = "Transactions", "Dues"
COLUMNS = ["Ref", "Date", "Type", "Name", "Amount (₹)", "Due", "Via", "Note", "Recorded on"]
COL = {c: i for i, c in enumerate(COLUMNS)}
DUES_COLUMNS = ["Name", "Udhaar given (₹)", "Amount paid back (₹)"]

TYPE_LABEL = {"credit": "Udhaar given", "payment": "Payment received"}
CATEGORY_LABEL = {
    "electricity": "Electricity", "rent": "Rent", "salary": "Salary", "transport": "Transport",
    "stock": "Stock", "phone": "Phone/internet", "water": "Water", "gas": "Gas", "tea": "Tea/snacks",
    "repair": "Repair", "purchase": "Purchase", "sale": "Sale", "other": "Other",
}
CATEGORY_OF_LABEL = {v.lower(): k for k, v in CATEGORY_LABEL.items()}
VIA_LABEL = {"voice": "Voice note", "telegram": "Telegram", "bill": "Bill photo", "text": "Web chat",
             "whatsapp": "WhatsApp", "sheet": "Restored", "seed": "Sample"}
VIA_OF_LABEL = {v.lower(): k for k, v in VIA_LABEL.items()}


def _rupees(paise: int) -> float | int:
    return paise // 100 if paise % 100 == 0 else paise / 100


def expense_label(category: str, direction: str = "out") -> str:
    word = "Money in" if direction == "in" else "Expense"
    return f"{word} ({CATEGORY_LABEL.get(category, category)})"


def transaction_rows(ledger: Ledger, owner: str) -> list[list]:
    """Active entries and expenses of `owner`, oldest first. Undone records are left out."""
    recs = []
    with ledger.lock:
        for r in ledger.db.execute(
                "SELECT e.*, c.name AS cname FROM entries e JOIN customers c ON c.id=e.customer_id "
                "WHERE e.owner=? AND e.status='active'", (owner,)):
            recs.append(("K", r))
        for r in ledger.db.execute("SELECT * FROM expenses WHERE owner=? AND status='active'", (owner,)):
            recs.append(("E", r))
    recs.sort(key=lambda t: (t[1]["entry_date"], t[1]["created_at"], t[0], t[1]["id"]))
    rows = []
    for tag, r in recs:
        if tag == "K":
            kind, name, due = TYPE_LABEL[r["kind"]], r["cname"], r["due_date"] or ""
        else:
            kind, name, due = expense_label(r["category"], r["direction"] or "out"), r["vendor"] or "", ""
        note = r["note"] or ""
        if note.lower() == "bill photo":           # the Via column already says so
            note = ""
        rows.append([f"{tag}-{r['id']}", r["entry_date"], kind, name, _rupees(r["paise"]), due,
                     VIA_LABEL.get(r["source"] or "", r["source"] or ""), note, (r["created_at"] or "")[:10]])
    return rows


def dues_rows(ledger: Ledger, owner: str) -> list[list]:
    """Per person: total udhaar given and total paid back (active entries only), plus a Total row."""
    people: dict[str, list[int]] = {}
    with ledger.lock:
        for r in ledger.db.execute(
                "SELECT c.name, e.kind, e.paise FROM entries e JOIN customers c ON c.id=e.customer_id "
                "WHERE e.owner=? AND e.status='active'", (owner,)):
            people.setdefault(r["name"], [0, 0])[0 if r["kind"] == "credit" else 1] += r["paise"]
    rows = [[name, _rupees(g), _rupees(p)]
            for name, (g, p) in sorted(people.items(), key=lambda kv: (-(kv[1][0] - kv[1][1]), kv[0]))]
    if rows:
        rows += [[], ["Total", _rupees(sum(g for g, _ in people.values())),
                      _rupees(sum(p for _, p in people.values()))]]
    return [DUES_COLUMNS] + rows


def parse_row(row: list) -> dict:
    """Read one Transactions row back (for restore). Raises ValueError on anything malformed."""
    row = list(row) + [""] * (len(COLUMNS) - len(row))
    ref = str(row[COL["Ref"]]).strip().upper()
    if len(ref) < 3 or ref[:2] not in ("K-", "E-") or not ref[2:].isdigit():
        raise ValueError(ref)
    label = str(row[COL["Type"]]).strip()
    category, direction = "", "out"
    if ref[0] == "E":
        prefix = next((w for w in ("Expense (", "Money in (") if label.startswith(w)), None)
        if not prefix or not label.endswith(")"):
            raise ValueError(ref)
        kind, direction = "expense", ("in" if prefix == "Money in (" else "out")
        cat = label[len(prefix):-1]
        category = CATEGORY_OF_LABEL.get(cat.lower(), cat.lower())
    else:
        kind = {v.lower(): k for k, v in TYPE_LABEL.items()}.get(label.lower())
        if kind is None:
            raise ValueError(ref)
    paise = round(float(str(row[COL["Amount (₹)"]]).replace(",", "").replace("₹", "")) * 100)
    return {"id": int(ref[2:]), "kind": kind, "date": str(row[COL["Date"]])[:10],
            "name": str(row[COL["Name"]]).strip(), "category": category, "paise": paise,
            "due": str(row[COL["Due"]])[:10] or None, "status": "active", "note": str(row[COL["Note"]]),
            "direction": direction,
            "via": VIA_OF_LABEL.get(str(row[COL["Via"]]).strip().lower(), "sheet"),
            "recorded": str(row[COL["Recorded on"]])[:10]}


def today_iso() -> str:
    return date.today().isoformat()
