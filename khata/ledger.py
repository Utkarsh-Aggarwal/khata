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
  id INTEGER PRIMARY KEY, owner TEXT NOT NULL, customer_id INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('credit','payment')),
  paise INTEGER NOT NULL CHECK(paise > 0),
  note TEXT DEFAULT '', entry_date TEXT NOT NULL, due_date TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
  source TEXT DEFAULT 'text', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_entries_owner ON entries(owner, customer_id);
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
        with self.lock:
            self.db.executescript(SCHEMA)
            self._rekey()

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
        self.db.commit()

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
            self.db.commit()
            return cur.lastrowid

    def add_alias(self, owner: str, name: str, alias: str) -> None:
        with self.lock:
            r = self._find(owner, name)
            if not r:
                raise LedgerError("unknown customer")
            self.db.execute(
                "INSERT OR REPLACE INTO aliases(owner,alias_key,customer_id) VALUES(?,?,?)",
                (owner, name_key(alias), r["id"]))
            self.db.commit()

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
            self.db.commit()
            return cur.lastrowid

    def undo_last(self, owner: str) -> Entry | None:
        with self.lock:
            r = self.db.execute(
                "SELECT id FROM entries WHERE owner=? AND status='active' "
                "ORDER BY id DESC LIMIT 1", (owner,)).fetchone()
            if not r:
                return None
            self.db.execute("UPDATE entries SET status='cancelled' WHERE id=? AND owner=?",
                            (r["id"], owner))
            self.db.commit()
            return self.get_entry(owner, r["id"])

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
                "SELECT owner FROM entries WHERE owner LIKE 'demo-%' GROUP BY owner "
                "HAVING MAX(created_at) < ?", (cutoff,))]
            for o in old:
                self.delete_owner_data(o)
            return len(old)

    # ---- privacy ---------------------------------------------------
    def delete_owner_data(self, owner: str) -> None:
        with self.lock:
            for t in ("entries", "aliases", "customers"):
                self.db.execute(f"DELETE FROM {t} WHERE owner=?", (owner,))
            self.db.commit()
