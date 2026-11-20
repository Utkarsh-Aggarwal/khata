from datetime import date, timedelta
from urllib.parse import unquote

from fastapi.testclient import TestClient

from khata.app import create_app
from khata.flow import Bot
from khata.guard import PaidGuard
from khata.ledger import Ledger
from khata.reminders import ghosts, normalize_phone

TODAY = date(2026, 11, 19)


def test_normalize_phone():
    assert normalize_phone("98765 43210") == "919876543210"
    assert normalize_phone("+91-98765-43210") == "919876543210"
    assert normalize_phone("098765 43210") == "919876543210"
    assert normalize_phone("+44 7700 900123") == "447700900123"
    for bad in ("12345", "5876543210", "1234567890123456789"):
        assert normalize_phone(bad) is None


def test_ghosts_order_language_and_links():
    lg = Ledger()
    lg.add_entry("o", "Ramesh", "credit", 50000)
    lg.add_entry("o", "Pappu", "credit", 10000, due_date=(TODAY - timedelta(days=5)).isoformat())
    lg.add_entry("o", "Sita", "credit", 100)
    lg.add_entry("o", "Sita", "payment", 100)                     # settled: no ghost
    lg.set_phone("o", "Ramesh", "919876543210")
    gs = ghosts(lg, "o", "hi", TODAY)
    assert [g.name for g in gs] == ["Pappu", "Ramesh"]             # overdue first
    assert gs[0].days_overdue == 5 and "5 दिन से बकाया" in gs[0].message
    assert gs[1].wa.startswith("https://wa.me/919876543210?text=")
    assert gs[0].wa.startswith("https://wa.me/?text=")             # no number: contact picker
    assert "₹500" in unquote(gs[1].wa)
    assert "ਸਤ ਸ੍ਰੀ ਅਕਾਲ" in ghosts(lg, "o", "pa", TODAY)[0].message
    assert ghosts(lg, "o", "klingon", TODAY)[0].message.startswith("Namaste")


def test_phone_command_does_not_touch_money_or_pending_entry():
    lg = Ledger()
    b = Bot(lg)
    b.handle("o", "Ramesh ko 500 udhaar")
    r = b.handle("o", "Ramesh ka number 98765 43210")
    assert "+919876543210" in r["reply"]
    assert lg.phones("o") == {"Ramesh": "919876543210"}
    assert lg.entries("o") == []
    b.handle("o", "yes")                                           # pending entry survived
    assert lg.balance("o", "Ramesh") == 50000


def test_phone_cross_script_and_bad_number():
    lg = Ledger()
    b = Bot(lg)
    lg.add_entry("o", "Ramesh", "credit", 100)
    b.handle("o", "रमेश का नंबर 9876543210")
    assert lg.phones("o") == {"Ramesh": "919876543210"}           # matched the existing Ramesh
    r = b.handle("o", "Sita ka number 5876543210")
    assert "नंबर" in r["reply"] and "Sita" not in lg.phones("o")


def test_bare_phone_number_is_not_saved_as_money():
    lg = Ledger()
    b = Bot(lg)
    r = b.handle("o", "Ramesh 9876543210 udhaar")
    assert "1 करोड़" in r["reply"] and r["buttons"] == []
    b.handle("o", "yes")
    assert lg.entries("o") == []


def test_remind_command():
    lg = Ledger()
    b = Bot(lg)
    assert "ghosts rest" in b.handle("o", "remind")["reply"]
    lg.add_entry("o", "Ramesh", "credit", 50000)
    r = b.handle("o", "remind")["reply"]
    assert "👻 Ramesh ₹500: https://wa.me/?text=" in r


def test_seeded_judge_sees_restless_ghost_and_lab():
    c = TestClient(create_app(":memory:", PaidGuard(key="", budget_calls=0, killed=False)))
    c.post("/api/demo-login")
    d = c.get("/api/ledger?lang=pa").json()
    assert d["overdue_count"] == 1 and d["customers"][0]["name"] == "Pappu"
    assert d["customers"][0]["days_overdue"] == 12 and "ਸਤ ਸ੍ਰੀ ਅਕਾਲ" in d["customers"][0]["message"]
    assert not any(cu["has_phone"] for cu in d["customers"] if "has_phone" in cu)
    lab = c.get("/api/lab").json()
    assert lab["mode"] == "demo" and {p["part"] for p in lab["parts"]} >= {"Ledger", "Ears", "Eyes"}
    assert [p["alive"] for p in lab["parts"] if p["part"] == "Ears"] == [False]


def test_phone_column_migrates_old_database(tmp_path):
    import sqlite3
    db = str(tmp_path / "old.db")
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE customers(id INTEGER PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,"
                      " key TEXT NOT NULL, UNIQUE(owner, key));")
    con.execute("INSERT INTO customers(owner,name,key) VALUES('o','Ramesh','rmes')")
    con.commit()
    con.close()
    lg = Ledger(db)
    lg.set_phone("o", "Ramesh", "919876543210")
    assert lg.phones("o") == {"Ramesh": "919876543210"}
