"""Passwordless LB Phone identity pairing for SNR loyalty accounts."""
from __future__ import annotations

import hmac

from snr_core import normalize_name, utc_now


class PhonePairings:
    def __init__(self, db):
        self.db = db
        with db.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS lb_phone_pairings (
                    customer_key TEXT PRIMARY KEY REFERENCES customers(customer_key),
                    customer_name TEXT NOT NULL,
                    character_id TEXT NOT NULL UNIQUE,
                    phone_number TEXT NOT NULL,
                    rp_name TEXT NOT NULL,
                    paired_at TEXT NOT NULL,
                    paired_by TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lb_phone_pair_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_key TEXT NOT NULL UNIQUE,
                    customer_key TEXT NOT NULL REFERENCES customers(customer_key),
                    customer_name TEXT NOT NULL,
                    character_id TEXT NOT NULL,
                    phone_number TEXT NOT NULL,
                    rp_name TEXT NOT NULL,
                    identity_match INTEGER NOT NULL DEFAULT 0,
                    request_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    resolved_by TEXT,
                    channel_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    message_id TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_phone_customer
                    ON lb_phone_pair_requests(customer_key) WHERE status='pending';
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_phone_character
                    ON lb_phone_pair_requests(character_id) WHERE status='pending';
                """
            )

    @staticmethod
    def _clean(value, field, minimum=1, maximum=100):
        value = " ".join(str(value or "").strip().split())
        if not minimum <= len(value) <= maximum:
            raise ValueError(f"Invalid {field}.")
        return value

    def _alert_channel(self, conn):
        row = conn.execute("SELECT channel_id,guild_id FROM web_delivery_settings WHERE id=1").fetchone()
        if not row:
            row = conn.execute("SELECT channel_id,guild_id FROM web_claim_settings WHERE id=1").fetchone()
        return row

    def request(self, customer_name, character_id, phone_number, rp_name, request_key):
        customer_name = self._clean(customer_name, "loyalty name", 2, 60)
        character_id = self._clean(character_id, "character identity", 3, 160)
        phone_number = self._clean(phone_number, "phone number", 2, 40)
        rp_name = self._clean(rp_name, "roleplay name", 2, 80)
        request_key = self._clean(request_key, "request", 10, 160)
        customer_key = normalize_name(customer_name)
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            customer = conn.execute("SELECT * FROM customers WHERE customer_key=?", (customer_key,)).fetchone()
            if not customer:
                raise ValueError("That loyalty name is not registered. Ask SNR staff to check it at the counter.")
            current = conn.execute(
                "SELECT * FROM lb_phone_pairings WHERE customer_key=? OR character_id=?",
                (customer_key, character_id),
            ).fetchall()
            if len(current) == 1 and current[0]["customer_key"] == customer_key:
                row = current[0]
                if hmac.compare_digest(row["character_id"], character_id) and hmac.compare_digest(
                    row["phone_number"], phone_number
                ):
                    return {"status": "approved", "already_paired": True, **dict(row)}
            existing = conn.execute(
                "SELECT * FROM lb_phone_pair_requests WHERE request_key=?", (request_key,)
            ).fetchone()
            if existing:
                if existing["character_id"] != character_id:
                    raise ValueError("That pairing request is not valid for this character.")
                return dict(existing)
            pending = conn.execute(
                """SELECT * FROM lb_phone_pair_requests
                   WHERE status='pending' AND (customer_key=? OR character_id=?)""",
                (customer_key, character_id),
            ).fetchone()
            if pending:
                if pending["customer_key"] != customer_key or pending["character_id"] != character_id:
                    raise ValueError("This loyalty account or character already has a pairing awaiting staff.")
                return dict(pending)
            route = self._alert_channel(conn)
            if not route:
                raise ValueError("Phone pairing is being set up. Please ask SNR staff.")
            request_type = "replace" if current else "new"
            identity_match = int(normalize_name(rp_name) == customer_key)
            cursor = conn.execute(
                """INSERT INTO lb_phone_pair_requests
                   (request_key,customer_key,customer_name,character_id,phone_number,rp_name,
                    identity_match,request_type,created_at,channel_id,guild_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    request_key,
                    customer_key,
                    customer["display_name"],
                    character_id,
                    phone_number,
                    rp_name,
                    identity_match,
                    request_type,
                    utc_now(),
                    route["channel_id"],
                    route["guild_id"],
                ),
            )
            conn.execute(
                "INSERT INTO audit_log(action,details,created_at) VALUES(?,?,?)",
                (
                    "lb_phone_pair_requested",
                    f"request={cursor.lastrowid};customer={customer_key};character={character_id};phone={phone_number}",
                    utc_now(),
                ),
            )
            return dict(conn.execute("SELECT * FROM lb_phone_pair_requests WHERE id=?", (cursor.lastrowid,)).fetchone())

    def owner(self, character_id, phone_number):
        character_id = str(character_id or "").strip()
        phone_number = str(phone_number or "").strip()
        if not character_id or not phone_number:
            return None
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT customer_key FROM lb_phone_pairings WHERE character_id=? AND phone_number=?",
                (character_id, phone_number),
            ).fetchone()
        return row["customer_key"] if row else None

    def status_for_identity(self, character_id):
        with self.db.connect() as conn:
            paired = conn.execute(
                "SELECT customer_name FROM lb_phone_pairings WHERE character_id=?", (str(character_id),)
            ).fetchone()
            pending = conn.execute(
                "SELECT customer_name FROM lb_phone_pair_requests WHERE character_id=? AND status='pending'",
                (str(character_id),),
            ).fetchone()
        if paired:
            return {"status": "approved", "customer_name": paired["customer_name"]}
        if pending:
            return {"status": "pending", "customer_name": pending["customer_name"]}
        return {"status": "unpaired"}

    def status(self, customer_name):
        key = normalize_name(customer_name)
        with self.db.connect() as conn:
            paired = conn.execute("SELECT 1 FROM lb_phone_pairings WHERE customer_key=?", (key,)).fetchone()
            pending = conn.execute(
                "SELECT 1 FROM lb_phone_pair_requests WHERE customer_key=? AND status='pending'", (key,)
            ).fetchone()
        return "LB Phone linked" if paired else "Phone pairing awaiting approval" if pending else "Not linked"

    def pending(self, unsent=False):
        where = "WHERE status='pending'" + (" AND message_id IS NULL" if unsent else "")
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute(f"SELECT * FROM lb_phone_pair_requests {where} ORDER BY id")]

    def get_request(self, request_id):
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM lb_phone_pair_requests WHERE id=?", (int(request_id),)).fetchone()
        return dict(row) if row else None

    def notified(self, request_id, message_id):
        with self.db.connect() as conn:
            conn.execute("UPDATE lb_phone_pair_requests SET message_id=? WHERE id=?", (str(message_id), int(request_id)))

    def resolve(self, request_id, decision, staff_id, staff_name):
        if decision not in ("approved", "rejected"):
            raise ValueError("Invalid phone-pairing decision.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM lb_phone_pair_requests WHERE id=?", (int(request_id),)).fetchone()
            if not row:
                raise ValueError("Phone-pairing request not found.")
            if row["status"] != "pending":
                raise ValueError("This phone-pairing request has already been processed.")
            if decision == "approved":
                conn.execute(
                    "DELETE FROM lb_phone_pairings WHERE customer_key=? OR character_id=?",
                    (row["customer_key"], row["character_id"]),
                )
                conn.execute(
                    """INSERT INTO lb_phone_pairings
                       (customer_key,customer_name,character_id,phone_number,rp_name,paired_at,paired_by)
                       VALUES(?,?,?,?,?,?,?)""",
                    (
                        row["customer_key"], row["customer_name"], row["character_id"],
                        row["phone_number"], row["rp_name"], utc_now(), str(staff_id),
                    ),
                )
                conn.execute(
                    """UPDATE lb_phone_pair_requests SET status='rejected',resolved_at=?,resolved_by=?
                       WHERE status='pending' AND id!=? AND (customer_key=? OR character_id=?)""",
                    (utc_now(), str(staff_id), int(request_id), row["customer_key"], row["character_id"]),
                )
            conn.execute(
                "UPDATE lb_phone_pair_requests SET status=?,resolved_at=?,resolved_by=? WHERE id=?",
                (decision, utc_now(), str(staff_id), int(request_id)),
            )
            conn.execute(
                "INSERT INTO audit_log(action,staff_id,staff_name,details,created_at) VALUES(?,?,?,?,?)",
                (
                    "lb_phone_pair_" + decision, str(staff_id), staff_name,
                    f"request={request_id};customer={row['customer_key']};character={row['character_id']}", utc_now(),
                ),
            )
        return self.get_request(request_id)

    def unlink(self, customer_name, staff_id, staff_name):
        key = normalize_name(customer_name)
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            customer = conn.execute("SELECT display_name FROM customers WHERE customer_key=?", (key,)).fetchone()
            if not customer:
                raise ValueError("Customer not found.")
            removed = conn.execute("DELETE FROM lb_phone_pairings WHERE customer_key=?", (key,)).rowcount
            conn.execute(
                "UPDATE lb_phone_pair_requests SET status='rejected',resolved_at=?,resolved_by=? WHERE customer_key=? AND status='pending'",
                (utc_now(), str(staff_id), key),
            )
            conn.execute(
                "INSERT INTO audit_log(action,staff_id,staff_name,details,created_at) VALUES(?,?,?,?,?)",
                ("lb_phone_unlinked", str(staff_id), staff_name, key, utc_now()),
            )
        if not removed:
            raise ValueError("That customer does not currently have a linked LB Phone.")
        return customer["display_name"]
