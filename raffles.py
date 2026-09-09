"""Paid-number raffles shared by the SNR website and Discord staff hub."""
from __future__ import annotations

import json
import secrets
import sqlite3

from snr_core import display_name, normalize_name, utc_now


class RaffleStore:
    NUMBER_LIMIT = 100
    CUSTOMER_LIMIT = 10

    def __init__(self, db):
        self.db = db
        with db.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS snr_raffles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    prize TEXT NOT NULL,
                    entry_price INTEGER NOT NULL,
                    number_limit INTEGER NOT NULL DEFAULT 100,
                    customer_limit INTEGER NOT NULL DEFAULT 10,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_by_name TEXT NOT NULL,
                    closed_at TEXT,
                    drawn_at TEXT,
                    cancelled_at TEXT,
                    winner_entry_id INTEGER,
                    winner_name TEXT,
                    winning_number INTEGER
                );
                CREATE TABLE IF NOT EXISTS snr_raffle_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    raffle_id INTEGER NOT NULL REFERENCES snr_raffles(id),
                    customer_key TEXT NOT NULL REFERENCES customers(customer_key),
                    customer_name TEXT NOT NULL,
                    numbers_json TEXT NOT NULL,
                    total_price INTEGER NOT NULL,
                    request_key TEXT NOT NULL UNIQUE,
                    source TEXT NOT NULL DEFAULT 'website',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    resolved_by TEXT,
                    resolved_by_name TEXT,
                    channel_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    message_id TEXT
                );
                CREATE TABLE IF NOT EXISTS snr_raffle_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    raffle_id INTEGER NOT NULL REFERENCES snr_raffles(id),
                    request_id INTEGER NOT NULL REFERENCES snr_raffle_requests(id),
                    customer_key TEXT NOT NULL REFERENCES customers(customer_key),
                    customer_name TEXT NOT NULL,
                    number INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    confirmed_at TEXT,
                    UNIQUE(raffle_id, number)
                );
                CREATE INDEX IF NOT EXISTS raffle_request_status
                    ON snr_raffle_requests(status, message_id);
                CREATE INDEX IF NOT EXISTS raffle_entry_customer
                    ON snr_raffle_entries(raffle_id, customer_key, status);
            """)

    @staticmethod
    def _audit(conn, action, details, staff_id=None, staff_name=None):
        conn.execute(
            "INSERT INTO audit_log(action,details,staff_id,staff_name,created_at) VALUES(?,?,?,?,?)",
            (action, details, str(staff_id) if staff_id is not None else None,
             staff_name, utc_now()),
        )

    @staticmethod
    def _row(row):
        return dict(row) if row else None

    def _route(self, conn):
        row = conn.execute("SELECT channel_id,guild_id FROM web_delivery_settings WHERE id=1").fetchone()
        if not row:
            raise ValueError("Website alerts are not configured. Run /snrhub_orders_setup in a private staff channel first.")
        return dict(row)

    def configured(self) -> bool:
        with self.db.connect() as conn:
            return conn.execute("SELECT 1 FROM web_delivery_settings WHERE id=1").fetchone() is not None

    def _active(self, conn):
        return conn.execute(
            "SELECT * FROM snr_raffles WHERE status IN ('open','closed') ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def current(self, include_latest=True):
        with self.db.connect() as conn:
            row = self._active(conn)
            if row is None and include_latest:
                row = conn.execute("SELECT * FROM snr_raffles ORDER BY id DESC LIMIT 1").fetchone()
            return self._enrich(conn, row) if row else None

    def _enrich(self, conn, row):
        result = dict(row)
        counts = conn.execute(
            """SELECT status,COUNT(*) AS total FROM snr_raffle_entries
               WHERE raffle_id=? GROUP BY status""", (row["id"],)
        ).fetchall()
        totals = {item["status"]: int(item["total"]) for item in counts}
        result["pending_numbers"] = totals.get("pending", 0)
        result["confirmed_numbers"] = totals.get("confirmed", 0)
        result["available_numbers"] = int(row["number_limit"]) - sum(totals.values())
        result["revenue"] = result["confirmed_numbers"] * int(row["entry_price"])
        return result

    def create(self, title, prize, entry_price, staff_id, staff_name):
        title, prize = " ".join(str(title).split()), " ".join(str(prize).split())
        if not 3 <= len(title) <= 80:
            raise ValueError("Raffle title must be 3–80 characters.")
        if not 2 <= len(prize) <= 120:
            raise ValueError("Prize must be 2–120 characters.")
        try:
            price = int(str(entry_price).replace("£", "").replace(",", "").strip())
        except (TypeError, ValueError):
            raise ValueError("Entry price must be a whole number, for example 100.") from None
        if not 1 <= price <= 1_000_000:
            raise ValueError("Entry price must be between £1 and £1,000,000.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if self._active(conn):
                raise ValueError("Finish or cancel the current raffle before creating another one.")
            now = utc_now()
            cursor = conn.execute(
                """INSERT INTO snr_raffles
                   (title,prize,entry_price,number_limit,customer_limit,status,created_at,created_by,created_by_name)
                   VALUES(?,?,?,?,?,'open',?,?,?)""",
                (title, prize, price, self.NUMBER_LIMIT, self.CUSTOMER_LIMIT, now,
                 str(staff_id), str(staff_name)),
            )
            self._audit(conn, "raffle_created", json.dumps({"raffle_id": cursor.lastrowid,
                        "title": title, "prize": prize, "entry_price": price}), staff_id, str(staff_name))
            row = conn.execute("SELECT * FROM snr_raffles WHERE id=?", (cursor.lastrowid,)).fetchone()
            return self._enrich(conn, row)

    def update(self, title, prize, entry_price, staff_id, staff_name):
        title, prize = " ".join(str(title).split()), " ".join(str(prize).split())
        if not 3 <= len(title) <= 80 or not 2 <= len(prize) <= 120:
            raise ValueError("Use a 3–80 character title and a 2–120 character prize.")
        try:
            price = int(str(entry_price).replace("£", "").replace(",", "").strip())
        except (TypeError, ValueError):
            raise ValueError("Entry price must be a whole number.") from None
        if not 1 <= price <= 1_000_000:
            raise ValueError("Entry price must be between £1 and £1,000,000.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._active(conn)
            if not row:
                raise ValueError("There is no active raffle to edit.")
            entries = conn.execute("SELECT COUNT(*) FROM snr_raffle_entries WHERE raffle_id=?",
                                   (row["id"],)).fetchone()[0]
            if entries and price != int(row["entry_price"]):
                raise ValueError("The price cannot change after a number has been reserved. The title and prize can still be edited.")
            conn.execute("UPDATE snr_raffles SET title=?,prize=?,entry_price=? WHERE id=?",
                         (title, prize, price, row["id"]))
            self._audit(conn, "raffle_edited", json.dumps({"raffle_id": row["id"], "title": title,
                        "prize": prize, "entry_price": price}), staff_id, str(staff_name))
            return self._enrich(conn, conn.execute("SELECT * FROM snr_raffles WHERE id=?", (row["id"],)).fetchone())

    @staticmethod
    def parse_numbers(value):
        if isinstance(value, (list, tuple, set)):
            raw = list(value)
        else:
            raw = str(value).replace(";", ",").replace(" ", ",").split(",")
        try:
            numbers = sorted({int(item) for item in raw if str(item).strip()})
        except (TypeError, ValueError):
            raise ValueError("Use numbers separated by commas, for example 4, 17, 82.") from None
        if not numbers:
            raise ValueError("Choose at least one raffle number.")
        if len(numbers) > RaffleStore.CUSTOMER_LIMIT:
            raise ValueError("You can choose up to 10 raffle numbers.")
        if any(number < 1 or number > RaffleStore.NUMBER_LIMIT for number in numbers):
            raise ValueError("Raffle numbers must be between 1 and 100.")
        return numbers

    def request(self, customer_key, numbers, request_key, *, source="website",
                confirmed=False, staff_id=None, staff_name=None):
        numbers = self.parse_numbers(numbers)
        request_key = str(request_key).strip()
        if not request_key:
            raise ValueError("This request has expired. Refresh and try again.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM snr_raffle_requests WHERE request_key=?", (request_key,)
            ).fetchone()
            if existing:
                return self._request_row(conn, existing)
            raffle = self._active(conn)
            if not raffle or raffle["status"] != "open":
                raise ValueError("There is no raffle accepting entries right now.")
            customer = conn.execute(
                "SELECT customer_key,display_name FROM customers WHERE customer_key=?",
                (normalize_name(str(customer_key)),),
            ).fetchone()
            if not customer:
                raise ValueError("That customer account could not be found.")
            owned = conn.execute(
                """SELECT COUNT(*) FROM snr_raffle_entries
                   WHERE raffle_id=? AND customer_key=? AND status IN ('pending','confirmed')""",
                (raffle["id"], customer["customer_key"]),
            ).fetchone()[0]
            if int(owned) + len(numbers) > int(raffle["customer_limit"]):
                raise ValueError(f"This customer can choose only {int(raffle['customer_limit'])} numbers in this raffle.")
            route = self._route(conn)
            status = "confirmed" if confirmed else "pending"
            now = utc_now()
            try:
                cursor = conn.execute(
                    """INSERT INTO snr_raffle_requests
                       (raffle_id,customer_key,customer_name,numbers_json,total_price,request_key,source,status,
                        created_at,resolved_at,resolved_by,resolved_by_name,channel_id,guild_id)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (raffle["id"], customer["customer_key"], customer["display_name"],
                     json.dumps(numbers), len(numbers) * int(raffle["entry_price"]), request_key,
                     source, status, now, now if confirmed else None,
                     str(staff_id) if confirmed else None, str(staff_name) if confirmed else None,
                     route["channel_id"], route["guild_id"]),
                )
                for number in numbers:
                    conn.execute(
                        """INSERT INTO snr_raffle_entries
                           (raffle_id,request_id,customer_key,customer_name,number,status,created_at,confirmed_at)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (raffle["id"], cursor.lastrowid, customer["customer_key"],
                         customer["display_name"], number, status, now, now if confirmed else None),
                    )
            except sqlite3.IntegrityError as exc:
                raise ValueError("One or more chosen numbers were just taken. Refresh the raffle and choose again.") from exc
            self._audit(conn, "raffle_entry_confirmed" if confirmed else "raffle_entry_requested",
                        json.dumps({"raffle_id": raffle["id"], "request_id": cursor.lastrowid,
                                    "customer": customer["display_name"], "numbers": numbers}),
                        staff_id, str(staff_name) if staff_name else None)
            row = conn.execute("SELECT * FROM snr_raffle_requests WHERE id=?", (cursor.lastrowid,)).fetchone()
            return self._request_row(conn, row)

    def _request_row(self, conn, row):
        result = dict(row)
        result["numbers"] = json.loads(result["numbers_json"])
        raffle = conn.execute("SELECT title,prize,entry_price,status AS raffle_status FROM snr_raffles WHERE id=?",
                              (result["raffle_id"],)).fetchone()
        result.update(dict(raffle))
        return result

    def request_by_id(self, request_id):
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM snr_raffle_requests WHERE id=?", (int(request_id),)).fetchone()
            return self._request_row(conn, row) if row else None

    def pending(self, unsent=False, limit=50):
        query = "SELECT * FROM snr_raffle_requests WHERE status='pending'"
        if unsent:
            query += " AND message_id IS NULL"
        query += " ORDER BY id LIMIT ?"
        with self.db.connect() as conn:
            return [self._request_row(conn, row) for row in conn.execute(query, (int(limit),)).fetchall()]

    def notified(self, request_id, message_id):
        with self.db.connect() as conn:
            conn.execute("UPDATE snr_raffle_requests SET message_id=? WHERE id=? AND message_id IS NULL",
                         (str(message_id), int(request_id)))

    def resolve(self, request_id, approve, staff_id, staff_name):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM snr_raffle_requests WHERE id=?", (int(request_id),)).fetchone()
            if not row:
                raise ValueError("Raffle request not found.")
            if row["status"] != "pending":
                raise ValueError(f"This request is already {row['status']}.")
            status = "confirmed" if approve else "rejected"
            now = utc_now()
            conn.execute(
                """UPDATE snr_raffle_requests SET status=?,resolved_at=?,resolved_by=?,resolved_by_name=?
                   WHERE id=? AND status='pending'""",
                (status, now, str(staff_id), str(staff_name), row["id"]),
            )
            if approve:
                conn.execute("UPDATE snr_raffle_entries SET status='confirmed',confirmed_at=? WHERE request_id=?",
                             (now, row["id"]))
            else:
                conn.execute("DELETE FROM snr_raffle_entries WHERE request_id=? AND status='pending'", (row["id"],))
            self._audit(conn, f"raffle_request_{status}",
                        json.dumps({"request_id": row["id"], "customer": row["customer_name"],
                                    "numbers": json.loads(row["numbers_json"])}), staff_id, str(staff_name))
            refreshed = conn.execute("SELECT * FROM snr_raffle_requests WHERE id=?", (row["id"],)).fetchone()
            return self._request_row(conn, refreshed)

    def entries(self, raffle_id=None):
        with self.db.connect() as conn:
            if raffle_id is None:
                raffle = self._active(conn) or conn.execute("SELECT * FROM snr_raffles ORDER BY id DESC LIMIT 1").fetchone()
                if not raffle:
                    return []
                raffle_id = raffle["id"]
            rows = conn.execute(
                """SELECT * FROM snr_raffle_entries WHERE raffle_id=?
                   ORDER BY number""", (int(raffle_id),)
            ).fetchall()
            return [dict(row) for row in rows]

    def customer_entries(self, customer_key, raffle_id=None):
        rows = self.entries(raffle_id)
        key = normalize_name(str(customer_key))
        return [row for row in rows if row["customer_key"] == key]

    def close(self, staff_id, staff_name):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._active(conn)
            if not row or row["status"] != "open":
                raise ValueError("There is no open raffle to close.")
            pending = conn.execute("SELECT COUNT(*) FROM snr_raffle_requests WHERE raffle_id=? AND status='pending'",
                                   (row["id"],)).fetchone()[0]
            if pending:
                raise ValueError(f"Resolve the {pending} pending payment request(s) before closing entries.")
            conn.execute("UPDATE snr_raffles SET status='closed',closed_at=? WHERE id=?", (utc_now(), row["id"]))
            self._audit(conn, "raffle_closed", json.dumps({"raffle_id": row["id"]}), staff_id, str(staff_name))
            return self._enrich(conn, conn.execute("SELECT * FROM snr_raffles WHERE id=?", (row["id"],)).fetchone())

    def reopen(self, staff_id, staff_name):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._active(conn)
            if not row or row["status"] != "closed":
                raise ValueError("There is no closed raffle to reopen.")
            conn.execute("UPDATE snr_raffles SET status='open',closed_at=NULL WHERE id=?", (row["id"],))
            self._audit(conn, "raffle_reopened", json.dumps({"raffle_id": row["id"]}), staff_id, str(staff_name))
            return self._enrich(conn, conn.execute("SELECT * FROM snr_raffles WHERE id=?", (row["id"],)).fetchone())

    def draw(self, staff_id, staff_name):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            raffle = self._active(conn)
            if not raffle or raffle["status"] != "closed":
                raise ValueError("Close entries before drawing the winner.")
            entries = conn.execute(
                "SELECT * FROM snr_raffle_entries WHERE raffle_id=? AND status='confirmed' ORDER BY id",
                (raffle["id"],),
            ).fetchall()
            if not entries:
                raise ValueError("This raffle has no confirmed paid entries.")
            winner = secrets.choice(entries)
            now = utc_now()
            conn.execute(
                """UPDATE snr_raffles SET status='drawn',drawn_at=?,winner_entry_id=?,winner_name=?,winning_number=?
                   WHERE id=?""", (now, winner["id"], winner["customer_name"], winner["number"], raffle["id"]),
            )
            self._audit(conn, "raffle_drawn", json.dumps({"raffle_id": raffle["id"],
                        "winner": winner["customer_name"], "number": winner["number"]}), staff_id, str(staff_name))
            return self._enrich(conn, conn.execute("SELECT * FROM snr_raffles WHERE id=?", (raffle["id"],)).fetchone())

    def cancel(self, staff_id, staff_name):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._active(conn)
            if not row:
                raise ValueError("There is no active raffle to cancel.")
            now = utc_now()
            conn.execute("UPDATE snr_raffles SET status='cancelled',cancelled_at=? WHERE id=?", (now, row["id"]))
            conn.execute("UPDATE snr_raffle_requests SET status='cancelled',resolved_at=? WHERE raffle_id=? AND status='pending'",
                         (now, row["id"]))
            self._audit(conn, "raffle_cancelled", json.dumps({"raffle_id": row["id"]}), staff_id, str(staff_name))
            return self._enrich(conn, conn.execute("SELECT * FROM snr_raffles WHERE id=?", (row["id"],)).fetchone())

    def history(self, limit=10):
        with self.db.connect() as conn:
            return [self._enrich(conn, row) for row in conn.execute(
                "SELECT * FROM snr_raffles ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()]
