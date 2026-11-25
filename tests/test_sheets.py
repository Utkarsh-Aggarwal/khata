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

    # helpers for assertions
    def khata(self):
        return [dict(zip(render.COLUMNS, r)) for r in self.tabs.get("Khata", [])[1:]]


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def _cell(sh, ref, col):
    return next(r[col] for r in sh.khata() if r["Ref"] == ref)


def test_sheet_has_fixed_columns_sorted_by_date_with_running_balance():
    lg, sh = Ledger(), FakeSheet()
    m = SheetMirror(lg, sh, "o", Clock())
    lg.add_entry("o", "Ramesh", "credit", 150000, entry_date="2026-11-20")
    lg.add_expense("o", "purchase", 449000, vendor="GUJARAT FREIGHT TOOLS", note="bill photo",
                   entry_date="2025-07-23", source="bill")
    lg.add_entry("o", "Ramesh", "payment", 50000, entry_date="2026-11-22", source="voice")
    lg.add_expense("o", "electricity", 200000, entry_date="2026-11-22", source="voice")
    assert m.sync()["synced"] == 4
    assert sh.tabs["Khata"][0] == render.COLUMNS
    rows = sh.khata()
    assert [r["Date"] for r in rows] == sorted(r["Date"] for r in rows)
    assert rows[0] == {**rows[0], "Ref": "E-1", "Type": "Expense", "Name": "GUJARAT FREIGHT TOOLS",
                       "Category": "Purchase", "Out (₹)": 4490, "In (₹)": "", "Via": "Bill photo"}
    assert _cell(sh, "K-1", "Out (₹)") == 1500 and _cell(sh, "K-1", "Balance after (₹, + they owe you)") == 1500
    assert _cell(sh, "K-2", "In (₹)") == 500 and _cell(sh, "K-2", "Balance after (₹, + they owe you)") == 1000
    assert _cell(sh, "E-2", "Category") == "Electricity" and _cell(sh, "E-2", "Name") == ""
    summary = sh.tabs["Summary"]
    assert ["Udhaar you gave (they owe you)", 1000] in summary and ["  Ramesh", 1000] in summary


def test_undo_greys_row_and_balance_skips_it():
    lg, sh = Ledger(), FakeSheet()
    m = SheetMirror(lg, sh, "o", Clock())
    lg.add_entry("o", "Ramesh", "credit", 50000)
    lg.add_entry("o", "Ramesh", "credit", 70000)
    lg.undo_last("o")
    m.sync()
    assert _cell(sh, "K-2", "Status") == "cancelled" and _cell(sh, "K-2", "Balance after (₹, + they owe you)") == ""
    assert ["  Ramesh", 500] in sh.tabs["Summary"]


def test_redraw_only_when_something_changed():
    lg, sh = Ledger(), FakeSheet()
    m = SheetMirror(lg, sh, "o", Clock())
    lg.add_entry("o", "A", "credit", 100)
    m.sync()
    writes = sh.writes
    assert m.sync() == {"synced": 0, "failed": 0, "pending": 0} and sh.writes == writes
    lg.add_entry("o", "A", "credit", 100)
    assert m.pending() == 1 and m.sync()["synced"] == 2


def test_hand_edits_are_overwritten_by_the_database():
    lg, sh = Ledger(), FakeSheet()
    m = SheetMirror(lg, sh, "o", Clock())
    lg.add_entry("o", "A", "credit", 100)
    m.sync()
    sh.tabs["Khata"].append(["junk", "typed by hand"])
    lg.add_entry("o", "A", "credit", 100)
    m.sync()
    assert all(r["Ref"] != "junk" for r in sh.khata())


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
    lg.add_expense("shop", "purchase", 3802600, vendor="SUNRISE ENTERPRISE", entry_date="2025-01-10", source="bill")
    lg.add_entry("shop", "Sita", "credit", 900, entry_date="2026-11-03")
    lg.undo_last("shop")
    m.sync()
    before = [list(r) for r in sh.tabs["Khata"]]

    fresh = Ledger()                                   # the host wiped the disk
    m2 = SheetMirror(fresh, sh, "shop", Clock())
    assert m2.boot() == {"restored": 4, "skipped": 0}
    assert fresh.balances("shop") == {"Ramesh": 50000, "Sita": -2550}
    assert fresh.expenses("shop")[0].vendor == "SUNRISE ENTERPRISE"
    assert fresh.entries("shop")[0].due_date == "2026-11-10"
    m2.sync()
    assert sh.tabs["Khata"] == before                  # identical redraw after restore
    assert fresh.add_entry("shop", "Mohan", "credit", 100) == 4     # ids continue, no collisions


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
    sh.tabs["Khata"] = [render.COLUMNS,
                        ["K-1", "2026-11-01", "Udhaar given", "Ramesh", "", 500, "", 500, "", "active", "", "", ""],
                        ["K-2", "2026-11-01", "Udhaar given", "Evil", "", -5, "", "", "", "active", "", "", ""],
                        ["K-3", "not-a-date", "Udhaar given", "X", "", 5, "", "", "", "active", "", "", ""],
                        ["K-4", "2026-11-01", "Steal", "Y", "", 5, "", "", "", "active", "", "", ""],
                        ["E-5", "2026-11-01", "Udhaar given", "Y", "", 5, "", "", "", "active", "", "", ""]]
    lg = Ledger()
    assert SheetMirror(lg, sh, "shop", Clock()).boot() == {"restored": 1, "skipped": 4}
    sh2 = FakeSheet()
    sh2.tabs["Khata"] = [["who", "what"], ["x", "y"]]
    assert SheetMirror(Ledger(), sh2, "shop", Clock()).boot()["reason"] == "unknown sheet layout"


def test_failed_restore_pauses_so_an_empty_db_never_overwrites_the_sheet():
    sh = FakeSheet()
    sh.tabs["Khata"] = [render.COLUMNS, ["K-1", "2026-11-01", "Udhaar given", "Ramesh", "", 500, "", 500, "",
                                         "active", "", "", ""]]
    sh.fail_next = 2
    lg = Ledger()
    m = SheetMirror(lg, sh, "shop", Clock())
    assert m.boot(attempts=2, wait=0) == {"error": "ConnectionError"} and m.paused
    assert m.sync()["paused"] is True and len(sh.tabs["Khata"]) == 2


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
