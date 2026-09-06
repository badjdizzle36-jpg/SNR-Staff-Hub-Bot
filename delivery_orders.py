"""Durable multi-item website delivery orders linked to SNR sales."""
import json

from snr_core import DEALS, normalize_name, utc_now

ACTIVE_STATUSES = ("pending", "accepted", "on_way", "processing")


class DeliveryStore:
    def __init__(self, db):
        self.db = db
        with db.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS web_delivery_settings (
                    id INTEGER PRIMARY KEY CHECK(id=1), channel_id TEXT NOT NULL, guild_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS web_delivery_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_key TEXT NOT NULL REFERENCES customers(customer_key), customer_name TEXT NOT NULL,
                    deal_key TEXT NOT NULL, deal_name TEXT NOT NULL, price INTEGER NOT NULL,
                    postal TEXT NOT NULL, request_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
                    resolved_at TEXT, resolved_by TEXT, channel_id TEXT NOT NULL, guild_id TEXT NOT NULL,
                    message_id TEXT, sale_transaction_id TEXT, items_json TEXT,
                    notes TEXT NOT NULL DEFAULT '', assigned_driver_id TEXT, assigned_driver_name TEXT,
                    accepted_at TEXT, on_way_at TEXT, status_updated_at TEXT);
                CREATE TABLE IF NOT EXISTS delivery_fees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL UNIQUE REFERENCES web_delivery_orders(id),
                    customer_key TEXT NOT NULL REFERENCES customers(customer_key),
                    customer_name TEXT NOT NULL,
                    amount INTEGER NOT NULL DEFAULT 500,
                    status TEXT NOT NULL DEFAULT 'owed',
                    reason TEXT NOT NULL DEFAULT 'Wasted delivery journey',
                    created_at TEXT NOT NULL, created_by TEXT NOT NULL, created_by_name TEXT NOT NULL,
                    resolved_at TEXT, resolved_by TEXT, resolved_by_name TEXT);
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(web_delivery_orders)")}
            for name, definition in (
                ("items_json", "TEXT"), ("notes", "TEXT NOT NULL DEFAULT ''"),
                ("assigned_driver_id", "TEXT"), ("assigned_driver_name", "TEXT"),
                ("accepted_at", "TEXT"), ("on_way_at", "TEXT"), ("status_updated_at", "TEXT"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE web_delivery_orders ADD COLUMN {name} {definition}")
            conn.execute("UPDATE web_delivery_orders SET status_updated_at=created_at WHERE status_updated_at IS NULL")
            conn.execute("DROP INDEX IF EXISTS one_pending_web_delivery")
            conn.execute("""CREATE UNIQUE INDEX one_pending_web_delivery ON web_delivery_orders(customer_key)
                WHERE status IN ('pending','accepted','on_way','processing')""")

    @staticmethod
    def audit(conn, action, details, staff_id=None, staff_name=None):
        conn.execute("INSERT INTO audit_log(action,details,staff_id,staff_name,created_at) VALUES(?,?,?,?,?)",
                     (action, details, staff_id, staff_name, utc_now()))

    @staticmethod
    def items(row):
        if row.get("items_json"):
            try:
                return json.loads(row["items_json"])
            except (ValueError, TypeError):
                pass
        deal = DEALS.get(row.get("deal_key"))
        return ([{"key": deal.key, "name": deal.name, "quantity": 1,
                  "unit_price": deal.price, "line_total": deal.price}] if deal else [])

    def configure(self, channel_id, guild_id, staff_id, staff_name):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR REPLACE INTO web_delivery_settings VALUES(1,?,?)",
                         (str(channel_id), str(guild_id)))
            conn.execute("""UPDATE web_delivery_orders SET channel_id=?,message_id=NULL
                WHERE status IN ('pending','accepted','on_way','processing') AND guild_id=? AND channel_id!=?""",
                (str(channel_id), str(guild_id), str(channel_id)))
            self.audit(conn, "web_delivery_channel", str(channel_id), str(staff_id), staff_name)

    def configured(self):
        with self.db.connect() as conn:
            return conn.execute("SELECT 1 FROM web_delivery_settings WHERE id=1").fetchone() is not None

    def create_cart_authenticated(self, customer_key, quantities, postal, request_key, notes=""):
        key = normalize_name(customer_key)
        postal = " ".join(str(postal).strip().split())
        notes = " ".join(str(notes).strip().split())
        if not 2 <= len(postal) <= 80:
            raise ValueError("Enter a postal or clear delivery location between 2 and 80 characters.")
        if not 10 <= len(request_key) <= 160:
            raise ValueError("Please reopen your account and try again.")
        if len(notes) > 200:
            raise ValueError("Order notes can be up to 200 characters.")
        items, units = [], 0
        for deal_key, raw_quantity in quantities.items():
            if deal_key not in DEALS:
                raise ValueError("Choose only available SNR deals.")
            try:
                quantity = int(raw_quantity)
            except (TypeError, ValueError):
                raise ValueError("Each deal amount must be a whole number.")
            if not 0 <= quantity <= 10:
                raise ValueError("Choose between 0 and 10 of each deal.")
            if quantity:
                deal = DEALS[deal_key]
                items.append({"key": deal.key, "name": deal.name, "quantity": quantity,
                              "unit_price": deal.price, "line_total": deal.price * quantity})
                units += quantity
        if not items:
            raise ValueError("Choose at least one SNR deal.")
        if units > 20:
            raise ValueError("A delivery can contain up to 20 deals in total.")
        subtotal = sum(item["line_total"] for item in items)
        description = ", ".join(f'{item["name"]} ×{item["quantity"]}' for item in items)
        items_json = json.dumps(items, separators=(",", ":"))
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM web_delivery_orders WHERE request_key=?", (request_key,)).fetchone()
            if existing:
                if existing["customer_key"] != key:
                    raise ValueError("Please reopen your account and try again.")
                return dict(existing)
            fee = conn.execute("SELECT amount FROM delivery_fees WHERE customer_key=? AND status='owed'", (key,)).fetchone()
            if fee:
                raise ValueError(
                    f"A £{int(fee['amount']):,} Wasted Journey fee is outstanding on your account. "
                    "Please pay SNR staff before placing another delivery."
                )
            pending = conn.execute("""SELECT * FROM web_delivery_orders WHERE customer_key=?
                AND status IN ('pending','accepted','on_way','processing')""", (key,)).fetchone()
            if pending:
                return dict(pending)
            config = conn.execute("SELECT * FROM web_delivery_settings WHERE id=1").fetchone()
            if not config:
                raise ValueError("Online delivery is being set up. Please contact SNR Buns.")
            customer = conn.execute("SELECT display_name FROM customers WHERE customer_key=?", (key,)).fetchone()
            if not customer:
                raise ValueError("Customer account not found.")
            cursor = conn.execute("""INSERT INTO web_delivery_orders
                (customer_key,customer_name,deal_key,deal_name,price,postal,request_key,created_at,
                 channel_id,guild_id,items_json,notes,status_updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (key, customer["display_name"], "cart", description, subtotal, postal, request_key, utc_now(),
                 config["channel_id"], config["guild_id"], items_json, notes, utc_now()))
            self.audit(conn, "web_delivery_requested",
                       f"order={cursor.lastrowid};customer={key};items={description};subtotal={subtotal};postal={postal};notes={notes}")
            return dict(conn.execute("SELECT * FROM web_delivery_orders WHERE id=?", (cursor.lastrowid,)).fetchone())

    def create_authenticated(self, customer_key, deal_key, postal, request_key):
        if deal_key not in DEALS:
            raise ValueError("Choose one of the available SNR deals.")
        return self.create_cart_authenticated(customer_key, {deal_key: 1}, postal, request_key)

    def summary(self, customer_key, limit=5):
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM web_delivery_orders WHERE customer_key=? ORDER BY id DESC LIMIT ?",
                                (normalize_name(customer_key), int(limit))).fetchall()
        return [dict(row) for row in rows]

    def pending(self, unsent=False):
        where = "WHERE status IN ('pending','accepted','on_way','processing')" + (" AND message_id IS NULL" if unsent else "")
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute(f"SELECT * FROM web_delivery_orders {where} ORDER BY id")]

    def get(self, order_id):
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM web_delivery_orders WHERE id=?", (int(order_id),)).fetchone()
        return dict(row) if row else None

    def notified(self, order_id, message_id):
        with self.db.connect() as conn:
            conn.execute("UPDATE web_delivery_orders SET message_id=? WHERE id=?", (str(message_id), int(order_id)))

    def status_counts(self, guild_id):
        with self.db.connect() as conn:
            rows = conn.execute("""SELECT status,COUNT(*) AS total FROM web_delivery_orders
                WHERE guild_id=? AND status IN ('pending','accepted','on_way','processing') GROUP BY status""",
                (str(guild_id),)).fetchall()
        counts = {name: 0 for name in ACTIVE_STATUSES}
        counts.update({row["status"]: int(row["total"]) for row in rows})
        return counts

    def outstanding_fee(self, customer_key):
        with self.db.connect() as conn:
            row = conn.execute("""SELECT * FROM delivery_fees
                WHERE customer_key=? AND status='owed' ORDER BY id DESC LIMIT 1""",
                (normalize_name(customer_key),)).fetchone()
        return dict(row) if row else None

    def outstanding_fees(self, guild_id=None, limit=100):
        where, values = "f.status='owed'", []
        if guild_id is not None:
            where += " AND o.guild_id=?"
            values.append(str(guild_id))
        values.append(int(limit))
        with self.db.connect() as conn:
            rows = conn.execute(f"""SELECT f.*,o.guild_id,o.channel_id FROM delivery_fees f
                JOIN web_delivery_orders o ON o.id=f.order_id
                WHERE {where} ORDER BY f.id LIMIT ?""", values).fetchall()
        return [dict(row) for row in rows]

    def fee_get(self, fee_id):
        with self.db.connect() as conn:
            row = conn.execute("""SELECT f.*,o.guild_id,o.channel_id FROM delivery_fees f
                JOIN web_delivery_orders o ON o.id=f.order_id WHERE f.id=?""", (int(fee_id),)).fetchone()
        return dict(row) if row else None

    def outstanding_debt_map(self):
        with self.db.connect() as conn:
            rows = conn.execute("""SELECT customer_key,SUM(amount) AS amount FROM delivery_fees
                WHERE status='owed' GROUP BY customer_key""").fetchall()
        return {row["customer_key"]: int(row["amount"]) for row in rows}

    def charge_wasted_journey(self, order_id, staff_id, staff_name):
        order_id = int(order_id)
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM web_delivery_orders WHERE id=?", (order_id,)).fetchone()
            if not row:
                raise ValueError("Order not found.")
            existing = conn.execute("SELECT * FROM delivery_fees WHERE order_id=?", (order_id,)).fetchone()
            if existing:
                return dict(row), dict(existing)
            if row["status"] != "on_way":
                raise ValueError("A Wasted Journey fee can only be added after the driver is marked On The Way.")
            now = utc_now()
            cursor = conn.execute("""INSERT INTO delivery_fees
                (order_id,customer_key,customer_name,amount,status,created_at,created_by,created_by_name)
                VALUES(?,?,?,?,?,?,?,?)""",
                (order_id, row["customer_key"], row["customer_name"], 500, "owed", now,
                 str(staff_id), staff_name))
            conn.execute("""UPDATE web_delivery_orders SET status='wasted_journey',resolved_at=?,
                resolved_by=?,status_updated_at=? WHERE id=?""", (now, str(staff_id), now, order_id))
            self.audit(conn, "web_delivery_wasted_journey",
                       f"order={order_id};fee={cursor.lastrowid};amount=500", str(staff_id), staff_name)
            fee = conn.execute("SELECT * FROM delivery_fees WHERE id=?", (cursor.lastrowid,)).fetchone()
            updated = conn.execute("SELECT * FROM web_delivery_orders WHERE id=?", (order_id,)).fetchone()
        return dict(updated), dict(fee)

    def resolve_fee(self, fee_id, status, staff_id, staff_name):
        if status not in ("paid", "waived"):
            raise ValueError("Invalid fee action.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM delivery_fees WHERE id=?", (int(fee_id),)).fetchone()
            if not row:
                raise ValueError("Delivery fee not found.")
            if row["status"] == status:
                return dict(row)
            if row["status"] != "owed":
                raise ValueError("This delivery fee has already been resolved.")
            now = utc_now()
            conn.execute("""UPDATE delivery_fees SET status=?,resolved_at=?,resolved_by=?,resolved_by_name=?
                WHERE id=?""", (status, now, str(staff_id), staff_name, int(fee_id)))
            self.audit(conn, "web_delivery_fee_" + status,
                       f"fee={int(fee_id)};order={row['order_id']};amount={row['amount']}",
                       str(staff_id), staff_name)
            updated = conn.execute("SELECT * FROM delivery_fees WHERE id=?", (int(fee_id),)).fetchone()
        return dict(updated)

    def advance(self, order_id, target, staff_id, staff_name):
        transitions = {"accepted": "pending", "on_way": "accepted"}
        if target not in transitions:
            raise ValueError("Invalid delivery update.")
        order_id = int(order_id)
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM web_delivery_orders WHERE id=?", (order_id,)).fetchone()
            if not row:
                raise ValueError("Order not found.")
            if row["status"] == target:
                return dict(row)
            if row["status"] != transitions[target]:
                raise ValueError("That delivery step has already been completed or is not ready yet.")
            now = utc_now()
            if target == "accepted":
                conn.execute("""UPDATE web_delivery_orders SET status='accepted',assigned_driver_id=?,
                    assigned_driver_name=?,accepted_at=?,status_updated_at=? WHERE id=?""",
                    (str(staff_id), staff_name, now, now, order_id))
            else:
                conn.execute("UPDATE web_delivery_orders SET status='on_way',on_way_at=?,status_updated_at=? WHERE id=?",
                             (now, now, order_id))
            self.audit(conn, "web_delivery_" + target, f"order={order_id}", str(staff_id), staff_name)
        return self.get(order_id)

    def resolve(self, order_id, status, staff_id, staff_name):
        if status not in ("paid", "cancelled"):
            raise ValueError("Invalid order action.")
        order_id = int(order_id)
        if status == "cancelled":
            with self.db.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM web_delivery_orders WHERE id=?", (order_id,)).fetchone()
                if not row:
                    raise ValueError("Order not found.")
                if row["status"] not in ("pending", "accepted", "on_way"):
                    raise ValueError("This order has already been processed.")
                now = utc_now()
                conn.execute("""UPDATE web_delivery_orders SET status='cancelled',resolved_at=?,resolved_by=?,
                    status_updated_at=? WHERE id=?""", (now, str(staff_id), now, order_id))
                self.audit(conn, "web_delivery_cancelled", f"order={order_id}", str(staff_id), staff_name)
            return self.get(order_id), []
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM web_delivery_orders WHERE id=?", (order_id,)).fetchone()
            if not row:
                raise ValueError("Order not found.")
            if row["status"] == "cancelled":
                raise ValueError("This order has already been processed.")
            already_paid = row["status"] == "paid"
            if not already_paid and row["status"] not in ("on_way", "processing"):
                raise ValueError("Accept the delivery and mark the driver on the way before confirming payment.")
            if not already_paid:
                conn.execute("UPDATE web_delivery_orders SET status='processing',status_updated_at=? WHERE id=?",
                             (utc_now(), order_id))
            order = dict(row)
        results = []
        try:
            for line_index, item in enumerate(self.items(order)):
                for unit_index in range(int(item["quantity"])):
                    source_ref = (f"delivery:{order_id}" if not order.get("items_json")
                                  else f"delivery:{order_id}:{line_index}:{unit_index}")
                    results.append(self.db.record_sale(
                        order["customer_name"], item["key"], str(staff_id), staff_name,
                        source_ref=source_ref))
        except Exception:
            with self.db.connect() as conn:
                conn.execute("UPDATE web_delivery_orders SET status='on_way',status_updated_at=? WHERE id=? AND status='processing'",
                             (utc_now(), order_id))
            raise
        if already_paid:
            return self.get(order_id), results
        transaction_ids = ",".join(result["transaction_id"] for result in results)
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = utc_now()
            conn.execute("""UPDATE web_delivery_orders SET status='paid',resolved_at=?,resolved_by=?,
                sale_transaction_id=?,status_updated_at=? WHERE id=? AND status='processing'""",
                (now, str(staff_id), transaction_ids, now, order_id))
            self.audit(conn, "web_delivery_paid", f"order={order_id};transactions={transaction_ids}",
                       str(staff_id), staff_name)
        return self.get(order_id), results
