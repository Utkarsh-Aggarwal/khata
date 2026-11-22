"""khata-mcp: tool behaviour in-process, plus a real stdio round trip like Kiro would make."""
import asyncio
import json
import os
import sys

from khata.ledger import Ledger
from khata.mcp_server import build


def _text(result) -> object:
    """Tool results carry JSON in their first text content block."""
    return json.loads(result.content[0].text)


def _seed(lg: Ledger):
    lg.add_entry("shop", "Ramesh", "credit", 50000, due_date="2026-01-01")
    lg.add_entry("shop", "Sita", "credit", 20000)
    lg.add_entry("shop", "Sita", "payment", 5000)
    lg.add_entry("someone-else", "Secret", "credit", 99999)


def test_tools_in_process():
    lg = Ledger()
    _seed(lg)
    srv = build(lg, "shop")
    names = {t.name for t in asyncio.run(srv.list_tools())}
    assert names == {"balances", "customer", "draft_reminders", "parse_entry"}

    b = _text(asyncio.run(srv.call_tool("balances", {})))
    assert b["total_due"] == "₹650" and "Secret" not in json.dumps(b)          # per-owner isolation
    c = _text(asyncio.run(srv.call_tool("customer", {"name": "रमेश"})))
    assert c["found"] and c["name"] == "Ramesh" and c["balance"] == "₹500"
    r = _text(asyncio.run(srv.call_tool("draft_reminders", {"lang": "hi"})))["reminders"]
    assert r[0]["name"] == "Ramesh" and r[0]["days_overdue"] > 0 and "बकाया" in r[0]["message"]
    p = _text(asyncio.run(srv.call_tool("parse_entry", {"text": "Mohan ko dhai sau udhaar"})))
    assert (p["intent"], p["customer"], p["amount"]) == ("add_credit", "Mohan", "₹250")
    assert lg.find_customer("shop", "Mohan") is None                          # dry run saved nothing


def test_stdio_round_trip(tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    db = str(tmp_path / "k.db")
    _seed(Ledger(db))
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "khata.mcp_server"], cwd=os.getcwd(),
        env={**os.environ, "KHATA_DB": db, "KHATA_MCP_OWNER": "shop"})

    async def go():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                tools = {t.name for t in (await s.list_tools()).tools}
                bal = _text(await s.call_tool("balances", {}))
                return tools, bal

    tools, bal = asyncio.run(asyncio.wait_for(go(), timeout=30))
    assert "parse_entry" in tools and bal["total_due"] == "₹650"
