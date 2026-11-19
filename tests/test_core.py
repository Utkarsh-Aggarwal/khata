from fastapi.testclient import TestClient
from hypothesis import given, strategies as st

from khata.app import create_app
from khata.extract import extract
from khata.flow import Bot
from khata.ledger import Ledger, format_rupees
from khata.names import name_key

amounts = st.integers(min_value=1, max_value=10**9)


@given(st.lists(st.tuples(st.sampled_from(["credit", "payment"]), amounts), max_size=30))
def test_balance_equals_signed_sum(ops):
    lg = Ledger()
    for k, p in ops:
        lg.add_entry("o", "Ramesh", k, p)
    exp = sum(p if k == "credit" else -p for k, p in ops)
    assert lg.balance("o", "Ramesh") == exp


@given(st.lists(st.tuples(st.sampled_from(["credit", "payment"]), amounts), min_size=1, max_size=20))
def test_undo_restores_balance(ops):
    lg = Ledger()
    for k, p in ops[:-1]:
        lg.add_entry("o", "A", k, p)
    before = lg.balance("o", "A")
    lg.add_entry("o", "A", *ops[-1][:1], ops[-1][1])
    lg.undo_last("o")
    assert lg.balance("o", "A") == before


def test_isolation_and_delete():
    lg = Ledger()
    lg.add_entry("a", "Ramesh", "credit", 100)
    lg.add_entry("b", "Ramesh", "credit", 999)
    assert lg.balance("a", "Ramesh") == 100
    assert lg.undo_last("b").paise == 999
    assert lg.balance("a", "Ramesh") == 100
    lg.delete_owner_data("b")
    assert lg.balances("b") == {} and lg.balance("a", "Ramesh") == 100


def test_rejects_bad_money():
    lg = Ledger()
    for bad in (0, -5, 1.5, True):
        try:
            lg.add_entry("o", "A", "credit", bad)
            assert False
        except ValueError:
            pass


def test_cross_script_names():
    assert name_key("Ramesh") == name_key("रमेश") == name_key("ਰਮੇਸ਼")
    lg = Ledger()
    lg.add_entry("o", "Ramesh", "credit", 500)
    lg.add_entry("o", "रमेश", "credit", 500)
    assert lg.balance("o", "Ramesh") == 1000 and len(lg.balances("o")) == 1


def test_alias():
    lg = Ledger()
    lg.add_entry("o", "Ramesh Kumar", "credit", 500)
    lg.add_alias("o", "Ramesh Kumar", "Lala")
    lg.add_entry("o", "Lala", "payment", 200)
    assert lg.balance("o", "Ramesh Kumar") == 300


def test_format():
    assert format_rupees(123456789) == "₹12,34,567.89"
    assert format_rupees(50000) == "₹500"


GOLDEN = [
    ("Ramesh ko 500 udhaar", "add_credit", "Ramesh", 50000),
    ("Sita ne 200 diye", "add_payment", "Sita", 20000),
    ("रमेश को पाँच सौ रुपये उधार", "add_credit", "रमेश", 50000),
    ("ਰਮੇਸ਼ ਨੂੰ 300 ਉਧਾਰ", "add_credit", "ਰਮੇਸ਼", 30000),
    ("Ramesh ne dedh sau diye", "add_payment", "Ramesh", 15000),
    ("Gupta ji ko dhai hazaar udhaar", "add_credit", "Gupta", 250000),
    ("paid 300 by Sita", "add_payment", "Sita", 30000),
    ("Mohan ko pachas udhaar", "add_credit", "Mohan", 5000),
    ("Sita ne 2,500 udhaar liya", "add_credit", "Sita", 250000),
]


def test_golden_extraction():
    for text, intent, cust, paise in GOLDEN:
        e = extract(text)
        assert (e.intent, e.customer, e.paise) == (intent, cust, paise), text


def test_kal_by_tense():
    from datetime import date
    t = date(2026, 11, 19)
    assert extract("Ramesh ko 500 udhaar kal dunga", t).due_date == "2026-11-20"
    assert extract("Ramesh ne kal 500 diye", t).entry_date == "2026-11-18"


def test_confirm_required_and_cancel():
    lg = Ledger()
    b = Bot(lg)
    r = b.handle("o", "Ramesh ko 500 udhaar")
    assert r["buttons"] == ["yes", "no"] and lg.entries("o") == []
    b.handle("o", "no")
    assert lg.entries("o") == []
    b.handle("o", "Ramesh ko 500 udhaar")
    b.handle("o", "600")                       # edit
    r = b.handle("o", "yes")
    assert lg.balance("o", "Ramesh") == 60000 and "₹600" in r["reply"]


def test_asks_for_missing_parts():
    lg = Ledger()
    b = Bot(lg)
    assert b.handle("o", "500 udhaar")["reply"] == "ग्राहक कौन है?"
    assert b.handle("o", "Sita")["buttons"] == ["yes", "no"]
    assert lg.entries("o") == []
    b.handle("o", "yes")
    assert lg.balance("o", "Sita") == 50000


def test_web_flow_and_isolation():
    c1 = TestClient(create_app(":memory:"))
    assert c1.post("/api/chat", json={"text": "hi"}).status_code == 401
    c1.post("/api/demo-login?seed=false")
    c1.post("/api/chat", json={"text": "Ramesh ko 500 udhaar"})
    c1.post("/api/chat", json={"text": "yes"})
    d = c1.get("/api/ledger").json()
    assert d["total_due"] == "₹500"
    assert c1.get("/api/export.xlsx").status_code == 200
    assert c1.get("/health").json()["ok"]
    c1.delete("/api/data")
    assert c1.get("/api/ledger").json()["customers"] == []
