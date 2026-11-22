from khata.ledger import Ledger
from khata.sheets import HEADER, SheetMirror


class FakeSheet:
    def __init__(self):
        self.rows: list[list] = [list(HEADER)]       # header in row 1
        self.fail_next = 0
        self.lose_response = False

    def append(self, row):
        if self.fail_next:
            self.fail_next -= 1
            raise ConnectionError("down")
        self.rows.append(list(row))
        if self.lose_response:                       # write landed, but the caller never hears back
            self.lose_response = False
            raise TimeoutError("lost response")
        return len(self.rows)

    def find(self, entry_id):
        for i, r in enumerate(self.rows, start=1):
            if str(r[0]) == str(entry_id):
                return i
        return None

    def set_status(self, row, status):
        self.rows[row - 1][7] = status

    def rewrite(self, rows):
        self.rows = [list(r) for r in rows]

    def read_all(self):
        if self.fail_next:
            self.fail_next -= 1
            raise ConnectionError("down")
        return [list(r) for r in self.rows]


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def test_append_then_undo_marks_cancelled():
    lg, sh = Ledger(), FakeSheet()
    m = SheetMirror(lg, sh, "o", Clock())
    lg.add_entry("o", "Ramesh", "credit", 50000)
    assert m.sync()["synced"] == 1
    assert sh.rows[1][2:6] == ["credit", "Ramesh", "", 500.0] and sh.rows[1][7] == "active"
    lg.undo_last("o")
    m.sync()
    assert len(sh.rows) == 2 and sh.rows[1][7] == "cancelled"
    assert m.sync() == {"synced": 0, "failed": 0, "pending": 0}       # idempotent


def test_outage_is_retried_with_backoff():
    lg, sh, clk = Ledger(), FakeSheet(), Clock()
    m = SheetMirror(lg, sh, "o", clk)
    lg.add_entry("o", "Sita", "payment", 100)
    sh.fail_next = 1
    assert m.sync() == {"synced": 0, "failed": 1, "pending": 1}
    assert m.sync()["failed"] == 0 and len(sh.rows) == 1             # still backing off
    clk.t += 10
    assert m.sync()["synced"] == 1 and len(sh.rows) == 2


def test_lost_response_does_not_duplicate():
    lg, sh, clk = Ledger(), FakeSheet(), Clock()
    m = SheetMirror(lg, sh, "o", clk)
    lg.add_entry("o", "Sita", "payment", 100)
    sh.lose_response = True
    m.sync()
    clk.t += 10
    m.sync()
    assert [r[0] for r in sh.rows[1:]] == [1]                        # exactly one row for entry 1


def test_only_mirrors_its_owner():
    lg, sh = Ledger(), FakeSheet()
    m = SheetMirror(lg, sh, "o", Clock())
    lg.add_entry("someone-else", "X", "credit", 100)
    lg.add_entry("o", "Y", "credit", 100)
    m.sync()
    assert [r[3] for r in sh.rows[1:]] == ["Y"]


def test_google_client_request_shapes():
    from khata.sheets import GoogleSheetClient

    class Resp:
        def __init__(self, j): self.j = j
        def json(self): return self.j
        def raise_for_status(self): pass

    class Sess:
        calls = []
        def post(self, url, **kw):
            self.calls.append(("post", url, kw)); return Resp({"updates": {"updatedRange": "Khata!A12:I12"}})
        def get(self, url, **kw):
            self.calls.append(("get", url, kw)); return Resp({"values": [["entry_id"], ["7"], ["9"]]})
        def put(self, url, **kw):
            self.calls.append(("put", url, kw)); return Resp({})

    c = GoogleSheetClient.__new__(GoogleSheetClient)
    c.s, c.id, c.tab = Sess(), "SHEET", "Khata"
    assert c.append([1, "x"]) == 12
    assert c.find(9) == 3 and c.find(8) is None
    c.set_status(3, "cancelled")
    post, put = c.s.calls[0], c.s.calls[-1]
    assert post[1].endswith("/SHEET/values/Khata%21A%3AI:append")
    assert post[2]["params"]["valueInputOption"] == "RAW"
    assert put[1].endswith("Khata%21H3") and put[2]["json"] == {"values": [["cancelled"]]}


def test_restore_after_disk_wipe_round_trip():
    sh = FakeSheet()
    lg = Ledger()
    m = SheetMirror(lg, sh, "shop", Clock())
    lg.add_entry("shop", "Ramesh", "credit", 50000, entry_date="2026-11-01", due_date="2026-11-10")
    lg.add_entry("shop", "Sita", "payment", 2550, entry_date="2026-11-02")
    lg.add_entry("shop", "Sita", "credit", 900, entry_date="2026-11-03")
    lg.undo_last("shop")
    m.sync()
    assert len(sh.rows) == 4

    fresh = Ledger()                                   # the host wiped the disk
    m2 = SheetMirror(fresh, sh, "shop", Clock())
    assert m2.boot() == {"restored": 3, "skipped": 0}
    assert fresh.balances("shop") == {"Ramesh": 50000, "Sita": -2550}
    assert [e.status for e in fresh.entries("shop", include_cancelled=True)] == ["active", "active", "cancelled"]
    assert fresh.entries("shop")[0].due_date == "2026-11-10"
    assert m2.sync() == {"synced": 0, "failed": 0, "pending": 0}        # nothing re-uploaded

    fresh.undo_last("shop")                            # undo after restore updates the same row
    new_id = fresh.add_entry("shop", "Mohan", "credit", 100)
    m2.sync()
    assert new_id == 4 and sh.rows[2][7] == "cancelled" and [r[0] for r in sh.rows[1:]] == [1, 2, 3, 4]


def test_restore_skips_bad_rows_and_nonempty_db():
    sh = FakeSheet()
    sh.rows = [["entry_id"]]          # old-format sheet
    sh.rows += [[1, "2026-11-01", "Ramesh", "credit", 500, "", "active", ""],
                [2, "2026-11-01", "Evil", "credit", -5, "", "active", ""],
                [3, "not-a-date", "X", "credit", 5, "", "active", ""],
                [4, "2026-11-01", "Y", "steal", 5, "", "active", ""],
                ["", "", "", "", "", "", "", ""]]
    lg = Ledger()
    r = SheetMirror(lg, sh, "shop", Clock()).boot()
    assert (r["restored"], r["skipped"]) == (1, 3)
    assert lg.balances("shop") == {"Ramesh": 50000}
    lg2 = Ledger()
    lg2.add_entry("demo-x", "A", "credit", 1)
    assert SheetMirror(lg2, sh, "shop", Clock()).restore()["reason"] == "database not empty"


def test_failed_restore_pauses_sync_instead_of_reusing_ids():
    sh = FakeSheet()
    sh.rows = [["entry_id"]]
    sh.rows.append([1, "2026-11-01", "Ramesh", "credit", 500, "", "active", ""])
    sh.fail_next = 2
    lg = Ledger()
    m = SheetMirror(lg, sh, "shop", Clock())
    assert m.boot(attempts=2, wait=0) == {"error": "ConnectionError"} and m.paused
    lg.add_entry("shop", "Sita", "credit", 100)        # would get id 1, which row 2 already uses
    assert m.sync()["paused"] is True and len(sh.rows) == 2


def test_new_sheet_gets_header():
    sh = FakeSheet()
    sh.rows = []
    assert SheetMirror(Ledger(), sh, "shop", Clock()).boot()["reason"] == "new sheet"
    assert sh.rows[0] == HEADER


def test_entry_ids_never_reused_after_delete():
    lg = Ledger()
    lg.add_entry("shop", "A", "credit", 1)
    last = lg.add_entry("demo-x", "B", "credit", 1)
    lg.delete_owner_data("demo-x")
    assert lg.add_entry("shop", "A", "credit", 1) == last + 1


def test_old_sheet_from_screenshot_restores_and_migrates():
    """The real sheet before the column change: vendor was hidden in the note column."""
    sh = FakeSheet()
    sh.rows = [["entry_id", "date", "customer", "kind", "rupees", "due", "status", "source"],
               [31, "2026-11-22", "Ramesh", "credit", 500, "", "cancelled"],
               [32, "2026-11-22", "Ramesh", "credit", 1500, "", "active"],
               [33, "2025-07-23", "Me", "payment", 4490, "", "cancelled"],
               ["E2", "2025-07-23", "purchase", "expense", 4490, "", "active", "bill: GUJARAT FREIGHT TOOLS"],
               [56, "2026-11-22", "Ramesh", "payment", 500, "", "active"]]
    lg = Ledger()
    m = SheetMirror(lg, sh, "shop", Clock())
    r = m.boot()
    assert (r["restored"], r["skipped"], r["migrated_rows"]) == (5, 0, 5)
    assert lg.balance("shop", "Ramesh") == 100000
    x = lg.expenses("shop")[0]
    assert (x.vendor, x.category, x.paise) == ("GUJARAT FREIGHT TOOLS", "purchase", 449000)
    assert sh.rows[0] == HEADER
    by_id = {str(row[0]): row for row in sh.rows[1:]}
    assert [str(row[1]) for row in sh.rows[1:]] == sorted(str(row[1]) for row in sh.rows[1:])
    assert by_id["E2"] == ["E2", "2025-07-23", "expense", "GUJARAT FREIGHT TOOLS", "purchase", 4490.0, "",
                          "active", "bill photo"]
    assert m.sync() == {"synced": 0, "failed": 0, "pending": 0}       # nothing duplicated
    lg.undo_last("shop")                                               # newest active = entry 56
    m.sync()
    assert {str(row[0]): row for row in sh.rows[1:]}["56"][7] == "cancelled"
    fresh = Ledger()                                                   # and the new format restores too
    assert SheetMirror(fresh, sh, "shop", Clock()).boot() == {"restored": 5, "skipped": 0}
    assert fresh.expenses("shop")[0].vendor == "GUJARAT FREIGHT TOOLS"
