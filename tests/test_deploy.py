from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import khata.app as appmod
from khata.app import create_app
from khata.guard import PaidGuard
from khata.ledger import Ledger


def test_demo_login_rate_limited(monkeypatch):
    monkeypatch.setattr(appmod, "LOGINS_PER_HOUR", 2)
    c = TestClient(create_app(":memory:", PaidGuard(key="", budget_calls=0, killed=False)))
    assert c.post("/api/demo-login?seed=false").status_code == 200
    assert c.post("/api/demo-login?seed=false").status_code == 200
    assert c.post("/api/demo-login?seed=false").status_code == 429


def test_purge_only_old_demo_owners():
    lg = Ledger()
    lg.add_entry("demo-old", "A", "credit", 100)
    lg.add_entry("demo-new", "A", "credit", 100)
    lg.add_entry("wa-shop", "A", "credit", 100)
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    lg.db.execute("UPDATE entries SET created_at=? WHERE owner IN ('demo-old','wa-shop')", (old,))
    assert lg.purge_demo_owners(7) == 1
    assert lg.balances("demo-old") == {} and lg.balance("demo-new", "A") == 100
    assert lg.balance("wa-shop", "A") == 100          # real shopkeepers are never purged


def test_full_judge_flow_with_paid_apis_off():
    """What a judge does in a fresh browser when the budget is spent."""
    c = TestClient(create_app(":memory:", PaidGuard(key="k", budget_calls=0, killed=False)))
    assert c.get("/health").json()["mode"] == "demo"
    assert c.get("/").status_code == 200
    c.post("/api/demo-login")
    assert c.get("/api/ledger").json()["total_due"] != "₹0"          # seeded sample data
    r = c.post("/api/voice", files={"file": ("n.webm", b"x" * 10, "audio/webm")}).json()
    assert "demo mode" in r["reply"]
    r = c.post("/api/bill/sample").json()
    assert "Who is the customer?" in r["reply"]
    for t in ("Kamla", "credit", "yes"):
        r = c.post("/api/chat", json={"text": t}).json()
    assert "Saved" in r["reply"]
    for t in ("Ramesh ne 200 diye", "yes", "undo"):
        r = c.post("/api/chat", json={"text": t}).json()
    assert "Undone" in r["reply"]
    assert c.get("/api/export.xlsx").status_code == 200
