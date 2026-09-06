"""Durable website delivery orders linked to the existing SNR sales system."""
from __future__ import annotations

from snr_core import DEALS, normalize_name, utc_now


class DeliveryStore:
    def __init__(self, db):
        self.db = db
        with db.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS web_delivery_settings (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    channel_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS web_delivery_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_key TEXT NOT NULL REFERENCES customers(customer_key),
                    customer_name TEXT NOT NULL,
                    deal_key TEXT NOT NULL,
                    deal_name TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    postal TEXT NOT NULL,
                    request_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    resolved_by TEXT,
                    channel_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    message_id TEXT,
                    sale_transaction_id TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_web_delivery
                    ON web_delivery_orders(customer_key)
                    WHERE status IN ('pending','processing');
                """
            )

    @staticmethod
    def audit(conn, action, details, staff_id=None, staff_name=None):
        conn.execute(
            "INSERT INTO audit_log(action,details,staff_id,staff_name,created_at) VALUES(?,?,?,?,?)",
            (action, details, staff_id, staff_name, utc_now()),
        )

    def configure(self, channel_id, guild_id, staff_id, staff_name):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR REPLACE INTO web_delivery_settings VALUES(1,?,?)",
                (str(channel_id), str(guild_id)),
            )
            conn.execute(
                """UPDATE web_delivery_orders SET channel_id=?,message_id=NULL
                   WHERE status IN ('pending','processing') AND guild_id=? AND channel_id!=?""",
                (str(channel_id), str(guild_id), str(channel_id)),
            )
            self.audit(conn, "web_delivery_channel", str(channel_id), str(staff_id), staff_name)

    def configured(self):
        with self.db.connect() as conn:
            return conn.execute("SELECT 1 FROM web_delivery_settings WHERE id=1").fetchone() is not None

    def create_authenticated(self, customer_key, deal_key, postal, request_key):
        key = normalize_name(customer_key)
        postal = " ".join(str(postal).strip().split())
        if deal_key not in DEALS:
            raise ValueError("Choose one of the available SNR deals.")
        if not 2 <= len(postal) <= 80:
            raise ValueError("Enter a postal or clear delivery location between 2 and 80 characters.")
        if not 10 <= len(request_key) <= 160:
            raise ValueError("Please reopen your account and try again.")
        deal = DEALS[deal_key]
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM web_delivery_orders WHERE request_key=?", (request_key,)
            ).fetchone()
            if existing:
                if existing["customer_key"] != key:
                    raise ValueError("Please reopen your account and try again.")
                return dict(existing)
            pending = conn.execute(
                """SELECT * FROM web_delivery_orders WHERE customer_key=?
                   AND status IN ('pending','processing')""",
                (key,),
            ).fetchone()
            if pending:
                return dict(pending)
            config = conn.execute("SELECT * FROM web_delivery_settings WHERE id=1").fetchone()
            if not config:
                raise ValueError("Online delivery is being set up. Please contact SNR Buns.")
            customer = conn.execute(
                "SELECT display_name FROM customers WHERE customer_key=?", (key,)
            ).fetchone()
            if not customer:
                raise ValueError("Customer account not found.")
            cursor = conn.execute(
                """INSERT INTO web_delivery_orders
                   (customer_key,customer_name,deal_key,deal_name,price,postal,request_key,
                    created_at,channel_id,guild_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    key, customer["display_name"], deal.key, deal.name, deal.price, postal,
                    request_key, utc_now(), config["channel_id"], config["guild_id"],
                ),
            )
            self.audit(
                conn, "web_delivery_requested",
                f"order={cursor.lastrowid};customer={key};deal={deal.key};price={deal.price};postal={postal}",
            )
            return dict(conn.execute(
                "SELECT * FROM web_delivery_orders WHERE id=?", (cursor.lastrowid,)
            ).fetchone())

    def summary(self, customer_key, limit=5):
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM web_delivery_orders WHERE customer_key=?
                   ORDER BY id DESC LIMIT ?""",
                (normalize_name(customer_key), int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def pending(self, unsent=False):
        where = "WHERE status IN ('pending','processing')"
        if unsent:
            where += " AND message_id IS NULL"
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute(
                f"SELECT * FROM web_delivery_orders {where} ORDER BY id"
            )]

    def get(self, order_id):
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM web_delivery_orders WHERE id=?", (int(order_id),)
            ).fetchone()
        return dict(row) if row else None

    def notified(self, order_id, message_id):
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE web_delivery_orders SET message_id=? WHERE id=?",
                (str(message_id), int(order_id)),
            )

    def resolve(self, order_id, status, staff_id, staff_name):
        if status not in ("paid", "cancelled"):
            raise ValueError("Invalid order action.")
        order_id = int(order_id)
        if status == "cancelled":
            with self.db.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM web_delivery_orders WHERE id=?", (order_id,)
                ).fetchone()
                if not row:
                    raise ValueError("Order not found.")
                if row["status"] != "pending":
                    raise ValueError("This order has already been processed.")
                conn.execute(
                    """UPDATE web_delivery_orders SET status='cancelled',resolved_at=?,resolved_by=?
                       WHERE id=?""",
                    (utc_now(), str(staff_id), order_id),
                )
                self.audit(conn, "web_delivery_cancelled", f"order={order_id}", str(staff_id), staff_name)
            return self.get(order_id), None

        # Mark processing first. The deterministic source reference makes retries safe
        # if Railway restarts between recording the sale and completing the order.
        already_paid = False
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM web_delivery_orders WHERE id=?", (order_id,)
            ).fetchone()
            if not row:
                raise ValueError("Order not found.")
            if row["status"] == "cancelled":
                raise ValueError("This order has already been processed.")
            already_paid = row["status"] == "paid"
            if not already_paid:
                conn.execute(
                    "UPDATE web_delivery_orders SET status='processing' WHERE id=?", (order_id,)
                )
            order = dict(row)
        try:
            sale = self.db.record_sale(
                order["customer_name"], order["deal_key"], str(staff_id), staff_name,
                source_ref=f"delivery:{order_id}",
            )
        except Exception:
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE web_delivery_orders SET status='pending' WHERE id=? AND status='processing'",
                    (order_id,),
                )
            raise
        if already_paid:
            return self.get(order_id), sale
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """UPDATE web_delivery_orders SET status='paid',resolved_at=?,resolved_by=?,
                   sale_transaction_id=? WHERE id=? AND status='processing'""",
                (utc_now(), str(staff_id), sale["transaction_id"], order_id),
            )
            self.audit(
                conn, "web_delivery_paid",
                f"order={order_id};transaction={sale['transaction_id']}", str(staff_id), staff_name,
            )
        return self.get(order_id), sale
