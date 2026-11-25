"""The Google Sheet is a redrawn view of the database, and can rebuild a wiped database."""
from khata import render
from khata.ledger import Ledger
from khata.sheets import SheetMirror


class FakeSheet:
    def __init__(self):
        self.tabs: dict[str, list[list]] = {}
        self.fail_next = 0
        self.writes = 0

    def read_tab(self, tab):
        if self.fail_next:
            self.fail_next -= 1
            raise ConnectionError("down")
        return [list(r) for r in self.tabs.get(tab, [])]

    def write_tab(self, tab, rows):
        if self.fail_next:
            self.fail_next -= 1
            raise ConnectionError("down")
        self.writes += 1
        self.tabs[tab] = [list(r) for r in rows]

    def delete_tab(self, tab):
        self.tabs.pop(tab, None)

    # helpers for assertions
    def _rows(self, tab):
        t = self.tabs.get(tab, [])
        return [dict(zip(t[0], r)) for r in t[1:]] if t else []

    def khata(self):
        """Udhaar and Entries rows together, by date (what the old Transactions tab showed)."""
        return sorted(self._rows("Udhaar") + self._rows("Entries"), key=lambda r: (r["Date"], r["Recorded on"]))

    def bills(self):
        return self._rows("Bills")

    def dues(self):
        return {r[0]: dict(zip(render.DUES_COLUMNS, r)) for r in self.tabs.get("Dues", [])[1:] if r}


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def _cell(sh, ref, col):
    return next(r[col] for r in sh.khata() if r["Ref"] == ref)


def test_transactions_table_has_only_the_agreed_columns_sorted_by_date():
    lg, sh = Ledger(), FakeSheet()
    m = SheetMirror(lg, sh, "o", Clock())
    lg.add_entry("o", "Ramesh", "credit", 150000, entry_date="2026-11-20")
    lg.add_expense("o", "purchase", 449000, vendor="GUJARAT FREIGHT TOOLS", note="bill photo",
                   entry_date="2025-07-23", source="bill")
    lg.add_entry("o", "Ramesh", "payment", 50000, entry_date="2026-11-22", source="voice")
    lg.add_expense("o", "electricity", 200000, entry_date="2026-11-22", source="voice")
    assert m.sync()["synced"] == 4
    assert sh.tabs["Udhaar"][0] == ["Ref", "Date", "Type", "Name", "Amount (₹)", "Due", "Via", "Note",
                                    "Recorded on"]
    assert sh.tabs["Entries"][0] == ["Ref", "Date", "Type", "Name", "Amount (₹)", "Via", "Note", "Recorded on"]
    assert {r["Ref"][0] for r in sh._rows("Udhaar")} == {"K"} and {r["Ref"][0] for r in sh._rows("Entries")} == {"E"}
    for gone in ("Category", "Out (₹)", "In (₹)", "Status"):
        assert gone not in sh.tabs["Udhaar"][0]
    rows = sh.khata()
    assert [r["Date"] for r in rows] == sorted(r["Date"] for r in rows)
    assert {k: rows[0][k] for k in ("Ref", "Type", "Name", "Amount (₹)", "Via", "Note")} == {
        "Ref": "E-1", "Type": "Expense (Purchase)", "Name": "GUJARAT FREIGHT TOOLS", "Amount (₹)": 4490,
        "Via": "Bill", "Note": ""}
    assert _cell(sh, "K-2", "Type") == "Payment received" and _cell(sh, "K-2", "Amount (₹)") == 500
    assert _cell(sh, "E-2", "Type") == "Expense (Electricity)"

def test_undone_rows_leave_the_transactions_table():
    lg, sh = Ledger(), FakeSheet()
    m = SheetMirror(lg, sh, "o", Clock())
    lg.add_entry("o", "Ramesh", "credit", 50000)
    lg.add_entry("o", "Ramesh", "credit", 70000)
    lg.undo_last("o")
    m.sync()
    assert [r["Ref"] for r in sh.khata()] == ["K-1"]
    assert sh.dues()["Ramesh"] == {"Name": "Ramesh", "Udhaar given (₹)": 500, "Amount paid back (₹)": 0}


def test_dues_table_shows_only_udhaar_given_and_paid_back():
    lg, sh = Ledger(), FakeSheet()
    m = SheetMirror(lg, sh, "o", Clock())
    lg.add_entry("o", "Ramesh", "credit", 150000)
    lg.add_entry("o", "Ramesh", "payment", 50000)
    lg.add_entry("o", "Sita", "credit", 30000)
    lg.add_expense("o", "rent", 800000)                   # expenses are not dues
    m.sync()
    assert sh.tabs["Dues"][0] == ["Name", "Udhaar given (₹)", "Amount paid back (₹)"]
    d = sh.dues()
    assert d["Ramesh"] == {"Name": "Ramesh", "Udhaar given (₹)": 1500, "Amount paid back (₹)": 500}
    assert d["Sita"] == {"Name": "Sita", "Udhaar given (₹)": 300, "Amount paid back (₹)": 0}
    assert d["Total"] == {"Name": "Total", "Udhaar given (₹)": 1800, "Amount paid back (₹)": 500}
    assert all(len(r) <= 3 for r in sh.tabs["Dues"])
    assert "rent" not in str(sh.tabs["Dues"]).lower()


def test_sheet_updates_right_after_a_change():
    import time as _t
    lg, sh = Ledger(), FakeSheet()
    m = SheetMirror(lg, sh, "o", Clock())
    m.start(every=60)                                     # fallback timer far away
    lg.add_entry("o", "Ramesh", "credit", 50000)
    for _ in range(40):                                   # up to 4 s
        if sh.khata():
            break
        _t.sleep(0.1)
    assert [r["Name"] for r in sh.khata()] == ["Ramesh"]
    lg.undo_last("o")
    for _ in range(40):
        if not sh.khata():
            break
        _t.sleep(0.1)
    assert sh.khata() == []
def test_old_tabs_are_removed_after_the_new_ones_are_written():
    lg, sh = Ledger(), FakeSheet()
    sh.tabs["Khata"], sh.tabs["Summary"], sh.tabs["Transactions"] = [["old"]], [["old"]], [["old"]]
    lg.add_entry("o", "A", "credit", 100)
    SheetMirror(lg, sh, "o", Clock()).sync()
    assert set(sh.tabs) == {"Udhaar", "Bills", "Entries", "Dues", "Rates", "Customers", "Settings"}

def test_redraw_only_when_something_changed():
    lg, sh = Ledger(), FakeSheet()
    m = SheetMirror(lg, sh, "o", Clock())
    lg.add_entry("o", "A", "credit", 100)
    m.sync()
    writes = sh.writes
    assert m.sync() == {"synced": 0, "failed": 0, "pending": 0} and sh.writes == writes
    lg.add_entry("o", "A", "credit", 100)
    assert m.pending() == 1 and m.sync()["synced"] == 2


def test_junk_typed_in_the_sheet_is_kept_with_a_warning():
    lg, sh = Ledger(), FakeSheet()
    m = SheetMirror(lg, sh, "o", Clock())
    lg.add_entry("o", "A", "credit", 100)
    m.sync()
    sh.tabs["Udhaar"].append(["", "typed by hand"])
    m.sync()
    junk = [r for r in sh.khata() if r["Date"] == "typed by hand"]
    assert len(junk) == 1 and junk[0]["Note"].startswith("⚠️ Not saved:")
    assert len(lg.entries("o")) == 1
def test_outage_is_retried_with_backoff():
    lg, sh, clk = Ledger(), FakeSheet(), Clock()
    m = SheetMirror(lg, sh, "o", clk)
    lg.add_entry("o", "Sita", "payment", 100)
    sh.fail_next = 1
    assert m.sync() == {"synced": 0, "failed": 1, "pending": 1}
    assert m.sync() == {"synced": 0, "failed": 0, "pending": 1}      # still backing off
    clk.t += 10
    assert m.sync()["synced"] == 1


def test_only_mirrors_its_owner():
    lg, sh = Ledger(), FakeSheet()
    m = SheetMirror(lg, sh, "o", Clock())
    lg.add_entry("someone-else", "X", "credit", 100)
    lg.add_entry("o", "Y", "credit", 100)
    m.sync()
    assert [r["Name"] for r in sh.khata()] == ["Y"]


def test_restore_round_trip_in_current_layout():
    lg, sh = Ledger(), FakeSheet()
    m = SheetMirror(lg, sh, "shop", Clock())
    lg.add_entry("shop", "Ramesh", "credit", 50000, entry_date="2026-11-01", due_date="2026-11-10")
    lg.add_entry("shop", "Sita", "payment", 2550, entry_date="2026-11-02", note="advance")
    lg.add_expense("shop", "electricity", 90000, entry_date="2026-11-04")
    lg.add_expense("shop", "purchase", 3802600, vendor="SUNRISE ENTERPRISE", entry_date="2025-01-10", source="bill")
    lg.add_entry("shop", "Sita", "credit", 900, entry_date="2026-11-03")
    lg.undo_last("shop")                               # undone rows are not in the sheet
    m.sync()
    before = {t: [list(r) for r in sh.tabs[t]] for t in ("Udhaar", "Entries", "Dues")}

    fresh = Ledger()                                   # the host wiped the disk
    m2 = SheetMirror(fresh, sh, "shop", Clock())
    assert m2.boot() == {"restored": 4, "skipped": 0}
    assert fresh.balances("shop") == {"Ramesh": 50000, "Sita": -2550}
    assert fresh.expense_totals("shop") == {"purchase": 3802600, "electricity": 90000}
    assert {x.vendor for x in fresh.expenses("shop")} == {"SUNRISE ENTERPRISE", ""}
    assert fresh.entries("shop")[0].due_date == "2026-11-10"
    m2.sync()
    assert {t: sh.tabs[t] for t in ("Udhaar", "Entries", "Dues")} == before   # identical redraw
    restored_ids = [e.id for e in fresh.entries("shop")]
    assert fresh.add_entry("shop", "Mohan", "credit", 100) > max(restored_ids)   # ids continue


def test_restore_v1_layout_from_the_real_sheet():
    sh = FakeSheet()
    sh.tabs["Khata"] = [["entry_id", "date", "customer", "kind", "rupees", "due", "status", "source"],
                        [31, "2026-11-22", "Ramesh", "credit", 500, "", "cancelled"],
                        [32, "2026-11-22", "Ramesh", "credit", 1500, "", "active"],
                        ["E2", "2025-07-23", "purchase", "expense", 4490, "", "active", "bill: GUJARAT FREIGHT TOOLS"],
                        [56, "2026-11-22", "Ramesh", "payment", 500, "", "active"]]
    lg = Ledger()
    assert SheetMirror(lg, sh, "shop", Clock()).boot() == {"restored": 4, "skipped": 0}
    assert lg.balance("shop", "Ramesh") == 100000
    assert lg.expenses("shop")[0].vendor == "GUJARAT FREIGHT TOOLS"


def test_restore_v3_layout_from_the_real_sheet():
    sh = FakeSheet()
    sh.tabs["Khata"] = [["Ref", "Date", "Type", "Name", "Category", "Out (₹)", "In (₹)",
                         "Balance after (₹, + they owe you)", "Due", "Status", "Via", "Note", "Recorded on"],
                        ["E-3", "2025-01-10", "Expense", "SUNRISE ENTERPRISE", "Purchase", 38026, "", "", "", "active",
                         "Bill photo", "", "2026-11-22"],
                        ["K-56", "2026-11-22", "Payment received", "Ramesh", "", "", 500, 1000, "", "active",
                         "Voice note", "", "2026-11-22"],
                        ["K-32", "2026-11-22", "Udhaar given", "Ramesh", "", 1500, "", 1500, "", "active",
                         "Voice note", "", "2026-11-22"]]
    lg = Ledger()
    assert SheetMirror(lg, sh, "shop", Clock()).boot() == {"restored": 3, "skipped": 0}
    assert lg.balance("shop", "Ramesh") == 100000
    assert lg.expenses("shop")[0].category == "purchase"


def test_restore_v2_layout_from_the_real_sheet():
    sh = FakeSheet()
    sh.tabs["Khata"] = [["id", "date", "type", "name", "category", "rupees", "due", "status", "note"],
                        [33, "2025-07-23", "payment", "Me", "", 4490, "", "cancelled", "bill: GUJARAT FREIGHT TOOLS"],
                        ["E2", "2025-07-23", "expense", "GUJARAT FREIGHT TOOLS", "purchase", 4490, "", "active",
                         "bill photo"],
                        [32, "2026-11-22", "credit", "Ramesh", "", 1500, "", "active"],
                        ["E4", "2026-11-22", "expense", "", "electricity", 2000, "", "active"]]
    lg = Ledger()
    assert SheetMirror(lg, sh, "shop", Clock()).boot() == {"restored": 4, "skipped": 0}
    assert lg.balances("shop") == {"Ramesh": 150000, "Me": 0}
    assert lg.expense_totals("shop") == {"purchase": 449000, "electricity": 200000}


def test_restore_skips_bad_rows_and_unknown_layouts():
    sh = FakeSheet()
    sh.tabs["Transactions"] = [render.COLUMNS,
                               ["K-1", "2026-11-01", "Udhaar given", "Ramesh", 500, "", "", "", ""],
                               ["K-2", "2026-11-01", "Udhaar given", "Evil", -5, "", "", "", ""],
                               ["K-3", "not-a-date", "Udhaar given", "X", 5, "", "", "", ""],
                               ["K-4", "2026-11-01", "Steal", "Y", 5, "", "", "", ""],
                               ["E-5", "2026-11-01", "Udhaar given", "Y", 5, "", "", "", ""]]
    lg = Ledger()
    assert SheetMirror(lg, sh, "shop", Clock()).boot() == {"restored": 1, "skipped": 4}
    sh2 = FakeSheet()
    sh2.tabs["Transactions"] = [["who", "what"], ["x", "y"]]
    assert SheetMirror(Ledger(), sh2, "shop", Clock()).boot()["reason"] == "unknown sheet layout"


def test_failed_restore_pauses_so_an_empty_db_never_overwrites_the_sheet():
    sh = FakeSheet()
    sh.tabs["Transactions"] = [render.COLUMNS, ["K-1", "2026-11-01", "Udhaar given", "Ramesh", 500, "", "", "", ""]]
    sh.fail_next = 2
    lg = Ledger()
    m = SheetMirror(lg, sh, "shop", Clock())
    assert m.boot(attempts=2, wait=0) == {"error": "ConnectionError"} and m.paused
    assert m.sync()["paused"] is True and len(sh.tabs["Transactions"]) == 2 and "Udhaar" not in sh.tabs


def test_nonempty_db_is_not_restored_over():
    sh = FakeSheet()
    lg = Ledger()
    lg.add_entry("demo-x", "A", "credit", 1)
    assert SheetMirror(lg, sh, "shop", Clock()).boot()["reason"] == "database not empty"


def test_entry_ids_never_reused_after_delete():
    lg = Ledger()
    lg.add_entry("shop", "A", "credit", 1)
    last = lg.add_entry("demo-x", "B", "credit", 1)
    lg.delete_owner_data("demo-x")
    assert lg.add_entry("shop", "A", "credit", 1) == last + 1


def test_google_client_request_shapes():
    from khata.sheets import GoogleSheetClient

    class Resp:
        def __init__(self, j, code=200): self.j, self.status_code = j, code
        def json(self): return self.j
        def raise_for_status(self): pass

    class Sess:
        def __init__(self): self.calls = []
        def post(self, url, **kw): self.calls.append(("post", url, kw)); return Resp({})
        def get(self, url, **kw):
            self.calls.append(("get", url, kw))
            if "values" in url:
                return Resp({"values": [["Ref"], ["K-1"]]})
            return Resp({"sheets": [{"properties": {"title": "Khata"}}]})
        def put(self, url, **kw): self.calls.append(("put", url, kw)); return Resp({})

    c = GoogleSheetClient.__new__(GoogleSheetClient)
    c.s, c.id, c.tab = Sess(), "SHEET", "Khata"
    assert c.read_tab("Khata") == [["Ref"], ["K-1"]]
    c.write_tab("Summary", [["a", 1]])
    kinds = [(k, u.split("/SHEET")[1]) for k, u, _ in c.s.calls[1:]]
    assert ("post", ":batchUpdate") in kinds                                    # Summary tab created
    assert ("post", "/values/Summary%21A%3AZ:clear") in kinds
    put = [kw for k, _, kw in c.s.calls if k == "put"][0]
    assert put["params"]["valueInputOption"] == "RAW" and put["json"] == {"values": [["a", 1]]}


def test_bills_tab_and_restore_keeps_bill_numbers():
    from khata.flow import Bot
    lg, sh = Ledger(), FakeSheet()
    b = Bot(lg, mode_prefixes=("tg-",))
    o = "tg-shop"
    for items, pay in (("atta 2 kg 45, sabun 2 x 35", "pay:cash"), ("tel 1 L 180", "pay:udhaar")):
        b.handle(o, items); b.handle(o, "mode:bill"); b.handle(o, "bill:done"); b.handle(o, pay)
    b.handle(o, "Ramesh")
    SheetMirror(lg, sh, o, Clock()).sync()
    assert sh.tabs["Bills"][0] == ["Bill No", "Date", "Customer", "Items", "Total (₹)", "Payment"]
    bills = sh.bills()
    assert [(x["Bill No"], x["Customer"], x["Total (₹)"], x["Payment"]) for x in bills] == [
        (1, "Cash customer", 160, "Cash"), (2, "Ramesh", 180, "Udhaar")]
    assert bills[0]["Items"] == "Atta 2 kg × ₹45 = ₹90; Sabun 2 × ₹35 = ₹70"
    assert sorted(r["Type"] for r in sh.khata()) == ["Money in (Sale)", "Udhaar given"]
    fresh = Ledger()
    r = SheetMirror(fresh, sh, o, Clock()).boot()
    assert r == {"restored": 4, "skipped": 0}
    assert fresh.add_bill(o, None, [], 100, "cash") == 3                  # numbering continues
    sh2 = FakeSheet()
    SheetMirror(fresh, sh2, o, Clock()).sync()
    assert sh2.bills()[0]["Items"] == bills[0]["Items"]
