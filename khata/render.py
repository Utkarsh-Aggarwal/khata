"""One rendering of the khata, used by the Google Sheet mirror and the Excel export.

Four tables, all redrawn from the database on every change, so the bot, the sheet and the
Excel file never disagree:
  Udhaar:  credit given and money returned, one row per entry, oldest first.
  Bills:   one row per bill made with the bill generator.
  Entries: normal ledger entries, money in (sales...) or out (expenses...), no balances.
  Dues:    per person, total udhaar given and amount paid back.
Undone records are left out.
"""
from __future__ import annotations

import json
from datetime import date

from .ledger import Ledger

UDHAAR_TAB, BILLS_TAB, ENTRIES_TAB, DUES_TAB = "Udhaar", "Bills", "Entries", "Dues"
TABS = (UDHAAR_TAB, BILLS_TAB, ENTRIES_TAB, DUES_TAB)
UDHAAR_COLUMNS = ["Ref", "Date", "Type", "Name", "Amount (₹)", "Due", "Via", "Note", "Recorded on"]
ENTRY_COLUMNS = ["Ref", "Date", "Type", "Name", "Amount (₹)", "Via", "Note", "Recorded on"]
BILL_COLUMNS = ["Bill No", "Date", "Customer", "Items", "Total (₹)", "Payment"]
DUES_COLUMNS = ["Name", "Udhaar given (₹)", "Amount paid back (₹)"]
COLUMNS = UDHAAR_COLUMNS            # the combined "Transactions" layout used before; restore still reads it

TYPE_LABEL = {"credit": "Udhaar given", "payment": "Payment received"}
CATEGORY_LABEL = {
    "electricity": "Electricity", "rent": "Rent", "salary": "Salary", "transport": "Transport",
    "stock": "Stock", "phone": "Phone/internet", "water": "Water", "gas": "Gas", "tea": "Tea/snacks",
    "repair": "Repair", "purchase": "Purchase", "sale": "Sale", "other": "Other",
}
CATEGORY_OF_LABEL = {v.lower(): k for k, v in CATEGORY_LABEL.items()}
VIA_LABEL = {"voice": "Voice note", "telegram": "Telegram", "bill": "Bill", "text": "Web chat",
             "whatsapp": "WhatsApp", "sheet": "Restored", "seed": "Sample"}
VIA_OF_LABEL = {v.lower(): k for k, v in VIA_LABEL.items()} | {"bill photo": "bill"}


def _rupees(paise: int) -> float | int:
    return paise // 100 if paise % 100 == 0 else paise / 100


def _note(n: str | None) -> str:
    n = n or ""
    return "" if n.lower() == "bill photo" else n


def expense_label(category: str, direction: str = "out") -> str:
    word = "Money in" if direction == "in" else "Expense"
    return f"{word} ({CATEGORY_LABEL.get(category, category)})"


def udhaar_rows(ledger: Ledger, owner: str) -> list[list]:
    with ledger.lock:
        rs = ledger.db.execute(
            "SELECT e.*, c.name AS cname FROM entries e JOIN customers c ON c.id=e.customer_id "
            "WHERE e.owner=? AND e.status='active' ORDER BY e.entry_date, e.created_at, e.id", (owner,)).fetchall()
    return [[f"K-{r['id']}", r["entry_date"], TYPE_LABEL[r["kind"]], r["cname"], _rupees(r["paise"]),
             r["due_date"] or "", VIA_LABEL.get(r["source"] or "", r["source"] or ""), _note(r["note"]),
             (r["created_at"] or "")[:10]] for r in rs]


def entry_rows(ledger: Ledger, owner: str) -> list[list]:
    with ledger.lock:
        rs = ledger.db.execute("SELECT * FROM expenses WHERE owner=? AND status='active' "
                               "ORDER BY entry_date, created_at, id", (owner,)).fetchall()
    return [[f"E-{r['id']}", r["entry_date"], expense_label(r["category"], r["direction"] or "out"),
             r["vendor"] or "", _rupees(r["paise"]), VIA_LABEL.get(r["source"] or "", r["source"] or ""),
             _note(r["note"]), (r["created_at"] or "")[:10]] for r in rs]


def bill_items_text(items: list[dict]) -> str:
    out = []
    for it in items:
        if "text" in it:                     # restored from the sheet
            out.append(it["text"])
            continue
        amt = f"₹{_rupees(it['amount'])}" if it.get("amount") else ""
        rate = f" × ₹{_rupees(it['rate'])}" if it.get("rate") else ""
        out.append(" ".join(x for x in (it.get("name", ""), (it.get("qty") or "") + rate, "=", amt) if x).strip())
    return "; ".join(out)


def bill_rows(ledger: Ledger, owner: str) -> list[list]:
    with ledger.lock:
        rs = ledger.db.execute("SELECT * FROM bills WHERE owner=? ORDER BY id", (owner,)).fetchall()
    return [[r["id"], r["bill_date"], r["customer"] or "Cash customer", bill_items_text(json.loads(r["items"])),
             _rupees(r["total"]), "Cash" if r["payment"] == "cash" else "Udhaar"] for r in rs]


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


def all_tabs(ledger: Ledger, owner: str) -> dict[str, list[list]]:
    return {UDHAAR_TAB: [UDHAAR_COLUMNS] + udhaar_rows(ledger, owner),
            BILLS_TAB: [BILL_COLUMNS] + bill_rows(ledger, owner),
            ENTRIES_TAB: [ENTRY_COLUMNS] + entry_rows(ledger, owner),
            DUES_TAB: dues_rows(ledger, owner)}


# ---- reading back (restore) ------------------------------------------------------------
def _money(v) -> int:
    return round(float(str(v).replace(",", "").replace("₹", "")) * 100)


def parse_row(header: list, row: list) -> dict:
    """One Udhaar/Entries/Transactions row, matched by column name. Raises ValueError if malformed."""
    d = {str(h).strip(): (row[i] if i < len(row) else "") for i, h in enumerate(header)}
    ref = str(d.get("Ref", "")).strip().upper()
    if len(ref) < 3 or ref[:2] not in ("K-", "E-") or not ref[2:].isdigit():
        raise ValueError(ref)
    label = str(d.get("Type", "")).strip()
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
    return {"id": int(ref[2:]), "kind": kind, "date": str(d.get("Date", ""))[:10],
            "name": str(d.get("Name", "")).strip(), "category": category, "paise": _money(d.get("Amount (₹)", "")),
            "due": str(d.get("Due", ""))[:10] or None, "status": "active", "note": str(d.get("Note", "")),
            "direction": direction, "via": VIA_OF_LABEL.get(str(d.get("Via", "")).strip().lower(), "sheet"),
            "recorded": str(d.get("Recorded on", ""))[:10]}


def parse_bill(row: list) -> dict:
    row = list(row) + [""] * 6
    no = int(float(row[0]))
    payment = {"cash": "cash", "udhaar": "udhaar"}[str(row[5]).strip().lower()]
    customer = str(row[2]).strip()
    return {"id": no, "date": str(row[1])[:10], "customer": None if customer in ("", "Cash customer") else customer,
            "items": [{"text": t.strip()} for t in str(row[3]).split(";") if t.strip()],
            "total": _money(row[4]), "payment": payment}


def today_iso() -> str:
    return date.today().isoformat()
