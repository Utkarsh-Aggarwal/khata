"""Excel export of one owner's ledger, shared by `/api/export.xlsx` and Telegram `/export`."""
from __future__ import annotations

import io

from .ledger import Ledger

HEADER = ["id", "customer", "kind", "rupees", "date", "due", "status"]
EXPENSE_HEADER = ["id", "category", "rupees", "date", "note", "status"]


def build_xlsx(ledger: Ledger, owner: str) -> bytes:
    """One row per entry of `owner`, cancelled included, in ledger order. rupees = paise / 100.

    The rupees column is a display value only; the ledger itself stays integer paise.
    `ledger.entries` filters by owner and holds `ledger.lock` while reading.
    """
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Khata"
    ws.append(HEADER)
    for e in ledger.entries(owner, include_cancelled=True):
        ws.append([e.id, e.customer, e.kind, e.paise / 100, e.entry_date, e.due_date, e.status])
    xs = wb.create_sheet("Expenses")             # the shop's own costs, kept apart from customers
    xs.append(EXPENSE_HEADER)
    for x in ledger.expenses(owner, include_cancelled=True):
        xs.append([x.id, x.category, x.paise / 100, x.entry_date, x.note, x.status])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
