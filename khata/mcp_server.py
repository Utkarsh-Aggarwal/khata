"""khata-mcp: a read-only MCP server so an AI agent (e.g. Kiro) can query a khata.

Read-only by design: ledger writes only happen through the confirm step in khata/flow.py.
Tools return balances, history, overdue dues, drafted reminders, and a dry-run parse.

Run:  KHATA_DB=khata.db KHATA_MCP_OWNER=<owner id> python -m khata.mcp_server
The owner can also be derived from a WhatsApp number: KHATA_SHEET_OWNER_NUMBER + WA_APP_SECRET.
"""
from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

from .extract import extract
from .ledger import Ledger, format_rupees
from .reminders import ghosts


def resolve_owner() -> str:
    owner = os.environ.get("KHATA_MCP_OWNER")
    if owner:
        return owner
    tg = os.environ.get("KHATA_SHEET_OWNER_TELEGRAM_ID")
    if tg:
        from .telegram import owner_for_chat
        return owner_for_chat(tg, os.environ.get("KHATA_OWNER_SALT", ""))
    num, secret = os.environ.get("KHATA_SHEET_OWNER_NUMBER"), os.environ.get("WA_APP_SECRET")
    if num and secret:
        from .whatsapp import owner_for
        return owner_for(num, secret)
    raise SystemExit("Set KHATA_MCP_OWNER, or KHATA_SHEET_OWNER_NUMBER and WA_APP_SECRET.")


def build(ledger: Ledger, owner: str) -> MCPServer:
    srv = MCPServer("khata", instructions=(
        "Read-only access to one shopkeeper's khata (credit ledger). Amounts are rupees. "
        "Positive balance = the customer owes the shop. You cannot add or change entries; "
        "tell the user to send the entry to the Khata bot, which asks for confirmation."))

    @srv.tool(description="All customers with a non-zero balance, largest first, plus the total due.")
    def balances() -> dict:
        b = {n: v for n, v in ledger.balances(owner).items() if v}
        return {"total_due": format_rupees(ledger.total_due(owner)),
                "customers": [{"name": n, "balance": format_rupees(v), "owes_shop": v > 0} for n, v in b.items()]}

    @srv.tool(description="One customer's balance and entries. Name matching works across scripts "
                          "(Ramesh = रमेश = ਰਮੇਸ਼).")
    def customer(name: str, include_cancelled: bool = False) -> dict:
        real = ledger.find_customer(owner, name)
        if not real:
            return {"found": False, "name": name}
        return {"found": True, "name": real, "balance": format_rupees(ledger.balance(owner, real)),
                "entries": [{"id": e.id, "date": e.entry_date, "kind": e.kind, "amount": format_rupees(e.paise),
                             "due": e.due_date, "status": e.status}
                            for e in ledger.entries(owner, real, include_cancelled=include_cancelled)]}

    @srv.tool(description="Customers who owe money, most overdue first, each with a drafted WhatsApp "
                          "reminder and wa.me link. lang: hinglish, hi, pa or en. Nothing is sent.")
    def draft_reminders(lang: str = "hinglish") -> dict:
        return {"reminders": [{"name": g.name, "balance": format_rupees(g.paise), "days_overdue": g.days_overdue,
                               "message": g.message, "link": g.wa} for g in ghosts(ledger, owner, lang)]}

    @srv.tool(description="Dry run: how the Khata bot would read a message such as 'Ramesh ko 500 udhaar'. "
                          "Does not save anything.")
    def parse_entry(text: str) -> dict:
        e = extract(text[:1000])
        return {"intent": e.intent, "customer": e.customer,
                "amount": format_rupees(e.paise) if e.paise else None,
                "entry_date": e.entry_date, "due_date": e.due_date, "language": e.lang, "missing": e.missing}

    return srv


def main() -> None:
    build(Ledger(os.environ.get("KHATA_DB", "khata.db")), resolve_owner()).run("stdio")


if __name__ == "__main__":
    main()
