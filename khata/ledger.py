"""Core ledger. Money is integer paise. Every query is scoped by owner."""
from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .names import name_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers(
  id INTEGER PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, key TEXT NOT NULL,
  UNIQUE(owner, key));
CREATE TABLE IF NOT EXISTS aliases(
  owner TEXT NOT NULL, alias_key TEXT NOT NULL, customer_id INTEGER NOT NULL,
  PRIMARY KEY(owner, alias_key));
CREATE TABLE IF NOT EXISTS entries(
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, customer_id INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('credit','payment')),
  paise INTEGER NOT NULL CHECK(paise > 0),
  note TEXT DEFAULT '', entry_date TEXT NOT NULL, due_date TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
  source TEXT DEFAULT 'text', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_entries_owner ON entries(owner, customer_id);
CREATE TABLE IF NOT EXISTS expenses(
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, category TEXT NOT NULL,
  paise INTEGER NOT NULL CHECK(paise > 0), note TEXT DEFAULT '', entry_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
  source TEXT DEFAULT 'text', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_expenses_owner ON expenses(owner, entry_date);
CREATE TABLE IF NOT EXISTS prices(
  owner TEXT NOT NULL, key TEXT NOT NULL, name TEXT NOT NULL, rate INTEGER NOT NULL CHECK(rate > 0),
  unit TEXT DEFAULT '', updated_at TEXT NOT NULL, PRIMARY KEY(owner, key));
CREATE TABLE IF NOT EXISTS bills(
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, customer TEXT, items TEXT NOT NULL,
  total INTEGER NOT NULL, payment TEXT NOT NULL CHECK(payment IN ('cash','udhaar')),
  bill_date TEXT NOT NULL, created_at TEXT NOT NULL);
"""


class LedgerError(ValueError):
    pass


@dataclass
class Entry:
    id: int
    customer: str
    kind: str
    paise: int
    note: str
    entry_date: str
    due_date: str | None
    status: str


@dataclass
class Expense:
    """The shop's own cost (electricity, rent, salary...). Never changes a customer balance."""
    id: int
    category: str
    paise: int
    note: str
    entry_date: str
    status: str
    vendor: str = ""
    direction: str = "out"           # "out" = shop expense, "in" = money received (cash sale...)
    kind: str = "expense"


def format_rupees(paise: int) -> str:
    sign = "-" if paise < 0 else ""
    p = abs(paise)
    rupees, rem = divmod(p, 100)
    s = str(rupees)
    if len(s) > 3:  # Indian grouping
        head, tail = s[:-3], s[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        s = head + "," + tail
    return f"{sign}₹{s}" + (f".{rem:02d}" if rem else "")


class Ledger:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.listeners: list = []          # called after every committed change (sheet mirror)
        with self.lock:
            self.db.executescript(SCHEMA)
            cols = {r["name"] for r in self.db.execute("PRAGMA table_info(customers)")}
            if "phone" not in cols:                    # migration for databases made before phones
                self.db.execute("ALTER TABLE customers ADD COLUMN phone TEXT")
            xcols = {r["name"] for r in self.db.execute("PRAGMA table_info(expenses)")}
            if "vendor" not in xcols:                  # who was paid: shop name on a bill, staff name
                self.db.execute("ALTER TABLE expenses ADD COLUMN vendor TEXT DEFAULT ''")
            if "direction" not in xcols:               # 'out' = expense, 'in' = money received (sale...)
                self.db.execute("ALTER TABLE expenses ADD COLUMN direction TEXT NOT NULL DEFAULT 'out'")
            # Expenses saved before the vendor column kept the shop name in the note as "bill: <shop>".
            self.db.execute("UPDATE expenses SET vendor=TRIM(SUBSTR(note, 7)), note='bill photo' "
                            "WHERE (vendor IS NULL OR vendor='') AND note LIKE 'bill: %'")
            self._commit()
            self._rekey()

    def _commit(self) -> None:
        self.db.commit()
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:
                pass

    def _rekey(self) -> None:
        """Recompute stored name keys so a change to name_key() can't orphan old customers.

        Keys that would now collide (only possible if two rows already shared a key) are left as is.
        """
        for r in self.db.execute("SELECT id, name, key FROM customers").fetchall():
            k = name_key(r["name"])
            if k and k != r["key"]:
                try:
                    self.db.execute("UPDATE customers SET key=? WHERE id=?", (k, r["id"]))
                except sqlite3.IntegrityError:
                    pass
        self._commit()

    # ---- customers -------------------------------------------------
    def _find(self, owner: str, name: str) -> sqlite3.Row | None:
        k = name_key(name)
        if not k:
            return None
        with self.lock:      # one shared connection: every statement runs under the lock
            r = self.db.execute(
                "SELECT c.* FROM customers c WHERE c.owner=? AND c.key=?", (owner, k)
            ).fetchone()
            if r:
                return r
            return self.db.execute(
                "SELECT c.* FROM aliases a JOIN customers c ON c.id=a.customer_id "
                "WHERE a.owner=? AND a.alias_key=? AND c.owner=?", (owner, k, owner)
            ).fetchone()

    def find_customer(self, owner: str, name: str) -> str | None:
        with self.lock:
            r = self._find(owner, name)
            return r["name"] if r else None

    def get_or_create_customer(self, owner: str, name: str) -> int:
        name = name.strip()
        k = name_key(name)
        if not k:
            raise LedgerError("customer name required")
        with self.lock:
            r = self._find(owner, name)
            if r:
                return r["id"]
            cur = self.db.execute(
                "INSERT INTO customers(owner,name,key) VALUES(?,?,?)", (owner, name, k))
            self._commit()
            return cur.lastrowid

    def set_phone(self, owner: str, name: str, phone: str | None) -> str:
        """Store a customer's WhatsApp number (digits with country code). Returns the stored name."""
        if phone is not None and not re.fullmatch(r"\d{11,15}", phone):
            raise LedgerError("phone must be digits with country code")
        with self.lock:
            cid = self.get_or_create_customer(owner, name)
            self.db.execute("UPDATE customers SET phone=? WHERE id=? AND owner=?", (phone, cid, owner))
            self._commit()
            return self.db.execute("SELECT name FROM customers WHERE id=?", (cid,)).fetchone()["name"]

    def phones(self, owner: str) -> dict[str, str]:
        with self.lock:
            rows = self.db.execute("SELECT name, phone FROM customers WHERE owner=? AND phone IS NOT NULL",
                                   (owner,)).fetchall()
        return {r["name"]: r["phone"] for r in rows}

    def add_alias(self, owner: str, name: str, alias: str) -> None:
        with self.lock:
            r = self._find(owner, name)
            if not r:
                raise LedgerError("unknown customer")
            self.db.execute(
                "INSERT OR REPLACE INTO aliases(owner,alias_key,customer_id) VALUES(?,?,?)",
                (owner, name_key(alias), r["id"]))
            self._commit()

    # ---- entries ---------------------------------------------------
    def add_entry(self, owner: str, customer: str, kind: str, paise: int, *,
                  note: str = "", entry_date: str | None = None,
                  due_date: str | None = None, source: str = "text") -> int:
        if not isinstance(paise, int) or isinstance(paise, bool) or paise <= 0:
            raise LedgerError("amount must be positive integer paise")
        if kind not in ("credit", "payment"):
            raise LedgerError("kind must be credit or payment")
        with self.lock:
            cid = self.get_or_create_customer(owner, customer)
            cur = self.db.execute(
                "INSERT INTO entries(owner,customer_id,kind,paise,note,entry_date,due_date,"
                "source,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (owner, cid, kind, paise, note, entry_date or date.today().isoformat(),
                 due_date, source, datetime.now(timezone.utc).isoformat()))
            self._commit()
            return cur.lastrowid

    def undo_last(self, owner: str) -> Entry | Expense | None:
        """Cancel the newest active record, customer entry or shop expense."""
        with self.lock:
            e = self.db.execute(
                "SELECT id, created_at FROM entries WHERE owner=? AND status='active' "
                "ORDER BY created_at DESC, id DESC LIMIT 1", (owner,)).fetchone()
            x = self.db.execute(
                "SELECT id, created_at FROM expenses WHERE owner=? AND status='active' "
                "ORDER BY created_at DESC, id DESC LIMIT 1", (owner,)).fetchone()
            if not e and not x:
                return None
            if x and (not e or x["created_at"] > e["created_at"]):
                self.db.execute("UPDATE expenses SET status='cancelled' WHERE id=? AND owner=?", (x["id"], owner))
                self._commit()
                return self.get_expense(owner, x["id"])
            self.db.execute("UPDATE entries SET status='cancelled' WHERE id=? AND owner=?",
                            (e["id"], owner))
            self._commit()
            return self.get_entry(owner, e["id"])

    # ---- shop expenses ----------------------------------------------
    def add_expense(self, owner: str, category: str, paise: int, *, note: str = "", vendor: str = "",
                    entry_date: str | None = None, source: str = "text", direction: str = "out") -> int:
        """A normal ledger entry: money out (expense) or in (sale...). No person's balance changes."""
        if direction not in ("in", "out"):
            raise LedgerError("direction must be in or out")
        if not isinstance(paise, int) or isinstance(paise, bool) or paise <= 0:
            raise LedgerError("amount must be positive integer paise")
        category = (category or "other").strip()[:40] or "other"
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO expenses(owner,category,paise,note,vendor,entry_date,source,created_at,direction) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (owner, category, paise, note, (vendor or "").strip()[:60], entry_date or date.today().isoformat(),
                 source, datetime.now(timezone.utc).isoformat(), direction))
            self._commit()
            return cur.lastrowid

    @staticmethod
    def _expense(r) -> Expense:
        return Expense(r["id"], r["category"], r["paise"], r["note"], r["entry_date"], r["status"],
                       r["vendor"] or "", r["direction"] or "out")

    def get_expense(self, owner: str, expense_id: int) -> Expense | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM expenses WHERE owner=? AND id=?", (owner, expense_id)).fetchone()
        return self._expense(r) if r else None

    def expenses(self, owner: str, *, since: str | None = None, until: str | None = None,
                 include_cancelled: bool = False, direction: str | None = None) -> list[Expense]:
        q, args = "SELECT * FROM expenses WHERE owner=?", [owner]
        if direction:
            q += " AND direction=?"
            args.append(direction)
        if since:
            q += " AND entry_date>=?"
            args.append(since)
        if until:
            q += " AND entry_date<=?"
            args.append(until)
        if not include_cancelled:
            q += " AND status='active'"
        with self.lock:
            return [self._expense(r) for r in self.db.execute(q + " ORDER BY id", args)]

    def expense_totals(self, owner: str, since: str | None = None) -> dict[str, int]:
        """Active expenses per category since a date, largest first."""
        out: dict[str, int] = {}
        for x in self.expenses(owner, since=since, direction="out"):
            out[x.category] = out.get(x.category, 0) + x.paise
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def get_entry(self, owner: str, entry_id: int) -> Entry | None:
        with self.lock:
            r = self.db.execute(
                "SELECT e.*, c.name AS cname FROM entries e JOIN customers c ON c.id=e.customer_id "
                "WHERE e.owner=? AND e.id=?", (owner, entry_id)).fetchone()
        return self._entry(r) if r else None

    @staticmethod
    def _entry(r) -> Entry:
        return Entry(r["id"], r["cname"], r["kind"], r["paise"], r["note"],
                     r["entry_date"], r["due_date"], r["status"])

    def entries(self, owner: str, customer: str | None = None,
                include_cancelled: bool = False) -> list[Entry]:
        q = ("SELECT e.*, c.name AS cname FROM entries e JOIN customers c ON c.id=e.customer_id "
             "WHERE e.owner=?")
        args: list = [owner]
        if customer:
            r = self._find(owner, customer)
            if not r:
                return []
            q += " AND e.customer_id=?"
            args.append(r["id"])
        if not include_cancelled:
            q += " AND e.status='active'"
        q += " ORDER BY e.id"
        with self.lock:
            return [self._entry(r) for r in self.db.execute(q, args)]

    # ---- balances --------------------------------------------------
    def balance(self, owner: str, customer: str) -> int:
        """Positive means the customer owes the shopkeeper."""
        with self.lock:
            r = self._find(owner, customer)
            if not r:
                return 0
            row = self.db.execute(
                "SELECT COALESCE(SUM(CASE kind WHEN 'credit' THEN paise ELSE -paise END),0) s "
                "FROM entries WHERE owner=? AND customer_id=? AND status='active'",
                (owner, r["id"])).fetchone()
            return row["s"]

    def balances(self, owner: str) -> dict[str, int]:
        with self.lock:
            rows = self.db.execute(
                "SELECT c.name, COALESCE(SUM(CASE e.kind WHEN 'credit' THEN e.paise "
                "ELSE -e.paise END),0) s FROM customers c LEFT JOIN entries e "
                "ON e.customer_id=c.id AND e.status='active' AND e.owner=c.owner "
                "WHERE c.owner=? GROUP BY c.id ORDER BY s DESC", (owner,)).fetchall()
        return {r["name"]: r["s"] for r in rows}

    def total_due(self, owner: str) -> int:
        return sum(v for v in self.balances(owner).values() if v > 0)

    def overdue(self, owner: str, today: str | None = None) -> list[Entry]:
        today = today or date.today().isoformat()
        return [e for e in self.entries(owner)
                if e.kind == "credit" and e.due_date and e.due_date < today
                and self.balance(owner, e.customer) > 0]

    def purge_demo_owners(self, older_than_days: int = 7) -> int:
        """Delete demo accounts whose newest entry is older than the cutoff. Never touches wa- owners."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        with self.lock:
            old = [r["owner"] for r in self.db.execute(
                "SELECT owner FROM (SELECT owner, created_at FROM entries UNION ALL "
                "SELECT owner, created_at FROM expenses) WHERE owner LIKE 'demo-%' GROUP BY owner "
                "HAVING MAX(created_at) < ?", (cutoff,))]
            for o in old:
                self.delete_owner_data(o)
            return len(old)

    # ---- price list and bills ---------------------------------------
    def set_price(self, owner: str, name: str, rate: int, unit: str = "") -> None:
        k = name_key(name)
        if not k or not isinstance(rate, int) or rate <= 0:
            return
        with self.lock:
            self.db.execute("INSERT INTO prices(owner,key,name,rate,unit,updated_at) VALUES(?,?,?,?,?,?) "
                            "ON CONFLICT(owner,key) DO UPDATE SET rate=excluded.rate, unit=excluded.unit, "
                            "name=excluded.name, updated_at=excluded.updated_at",
                            (owner, k, name.strip()[:30], rate, unit, datetime.now(timezone.utc).isoformat()))
            self.db.commit()                       # not money: the sheet need not redraw

    def price(self, owner: str, name: str) -> int | None:
        with self.lock:
            r = self.db.execute("SELECT rate FROM prices WHERE owner=? AND key=?", (owner, name_key(name))).fetchone()
        return r["rate"] if r else None

    def prices(self, owner: str) -> list[tuple[str, int, str]]:
        with self.lock:
            rows = self.db.execute("SELECT name, rate, unit FROM prices WHERE owner=? ORDER BY name", (owner,))
            return [(r["name"], r["rate"], r["unit"]) for r in rows]

    def add_bill(self, owner: str, customer: str | None, items: list[dict], total: int, payment: str) -> int:
        import json
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO bills(owner,customer,items,total,payment,bill_date,created_at) VALUES(?,?,?,?,?,?,?)",
                (owner, customer, json.dumps(items, ensure_ascii=False), total, payment,
                 date.today().isoformat(), datetime.now(timezone.utc).isoformat()))
            self.db.commit()
            return cur.lastrowid

    # ---- privacy ---------------------------------------------------
    def delete_owner_data(self, owner: str) -> None:
        with self.lock:
            for t in ("entries", "expenses", "aliases", "customers", "prices", "bills"):
                self.db.execute(f"DELETE FROM {t} WHERE owner=?", (owner,))
            self._commit()
