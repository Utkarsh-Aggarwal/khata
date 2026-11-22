"""Regression tests for bugs found in code review."""
import threading

from khata.bills import SAMPLE_BILL, to_extraction
from khata.flow import M, Bot
from khata.ledger import Ledger
from khata.names import name_key

DISCARDED_HI = M["discarded"]["hi"]      # Hinglish entries get Hindi replies


def test_different_names_are_different_customers():
    for a, b in [("Mohan", "Mohini"), ("Ramesh", "Rimsha"), ("Raju", "Rajni"), ("Sunil", "Sonal")]:
        assert name_key(a) != name_key(b), (a, b)
    lg = Ledger()
    lg.add_entry("o", "Mohan", "credit", 100)
    lg.add_entry("o", "Mohini", "credit", 200)
    assert lg.balances("o") == {"Mohini": 200, "Mohan": 100}


def test_same_person_across_scripts_and_spellings_still_matches():
    groups = [("Ramesh", "रमेश", "ਰਮੇਸ਼", "Rameshh"), ("Sita", "Seeta", "सीता", "ਸੀਤਾ"),
              ("Mohan", "मोहन", "ਮੋਹਨ"), ("Pooja", "Puja", "पूजा"), ("Gupta", "गुप्ता"),
              ("Priya", "प्रिया"), ("Gurpreet", "ਗੁਰਪ੍ਰੀਤ"), ("Kavita", "कविता")]
    for g in groups:
        assert len({name_key(n) for n in g}) == 1, g


def test_rekey_on_open_keeps_old_customers_findable(tmp_path):
    db = str(tmp_path / "k.db")
    lg = Ledger(db)
    lg.add_entry("o", "Mohan", "credit", 100)
    lg.db.execute("UPDATE customers SET key='mn'")        # key written by the old algorithm
    lg.db.commit()
    assert Ledger(db).balance("o", "मोहन") == 100


def test_unrelated_message_discards_unsaved_entry():
    lg = Ledger()
    b = Bot(lg)
    b.handle("o", "Ramesh ko 500 udhaar")
    r = b.handle("o", "list")
    assert r["reply"].startswith(DISCARDED_HI)
    b.handle("o", "yes")
    assert lg.entries("o") == []


def test_new_entry_for_other_customer_does_not_edit_pending_one():
    lg = Ledger()
    b = Bot(lg)
    b.handle("o", "Ramesh ko 500 udhaar")
    r = b.handle("o", "Sita ko 200")
    assert r["reply"].startswith(DISCARDED_HI) and "Ramesh" not in r["reply"]
    b.handle("o", "credit")
    b.handle("o", "yes")
    assert lg.balances("o") == {"Sita": 20000}


def test_amount_edit_still_works():
    lg = Ledger()
    b = Bot(lg)
    b.handle("o", "Ramesh ko 500 udhaar")
    assert "₹650" in b.handle("o", "650")["reply"]
    assert "₹700" in b.handle("o", "Ramesh ko 700")["reply"]     # same customer: an edit
    b.handle("o", "yes")
    assert lg.balance("o", "Ramesh") == 70000


def test_commands_at_customer_prompt_are_not_names():
    lg = Ledger()
    b = Bot(lg)
    b.start_from_extraction("o", to_extraction(SAMPLE_BILL), "bill")
    r = b.handle("o", "help")
    assert "Khata:" in r["reply"]
    b.start_from_extraction("o", to_extraction(SAMPLE_BILL), "bill")
    assert b.handle("o", "!!!")["reply"].startswith("Whose bill was this")
    assert lg.balances("o") == {}


def test_concurrent_access_is_safe(tmp_path):
    lg = Ledger(str(tmp_path / "c.db"))
    errs = []

    def worker(i):
        try:
            for _ in range(100):
                lg.add_entry("c", f"Cust{i}", "credit", 100)
                lg.entries("c", f"Cust{i}")
                lg.get_entry("c", 1)
        except Exception as e:      # noqa: BLE001
            errs.append(repr(e))

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errs == [] and len(lg.entries("c")) == 800


def test_export_reply():
    b = Bot(Ledger(), base_url="https://khata.example")
    assert "https://khata.example/api/export.xlsx" in b.handle("o", "export")["reply"]
    assert "web app" in b.handle("o", "export", source="whatsapp")["reply"]
