"""Durable multi-item website delivery orders linked to SNR sales."""
import json
import re
from datetime import date

from snr_core import DEALS, normalize_name, utc_now

ACTIVE_STATUSES = ("pending", "accepted", "on_way", "arrived", "processing")


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
                    accepted_at TEXT, on_way_at TEXT, arrived_at TEXT, status_updated_at TEXT,
                    subtotal INTEGER, delivery_fee INTEGER NOT NULL DEFAULT 0,
                    discount_amount INTEGER NOT NULL DEFAULT 0, discount_code TEXT,
                    membership_level TEXT);
                CREATE TABLE IF NOT EXISTS delivery_discount_codes (
                    code TEXT PRIMARY KEY, discount_type TEXT NOT NULL, amount INTEGER NOT NULL,
                    max_uses INTEGER, uses INTEGER NOT NULL DEFAULT 0, expires_on TEXT,
                    active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
                    created_by TEXT NOT NULL, created_by_name TEXT NOT NULL);
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
                ("accepted_at", "TEXT"), ("on_way_at", "TEXT"), ("arrived_at", "TEXT"),
                ("status_updated_at", "TEXT"),
                ("subtotal", "INTEGER"), ("delivery_fee", "INTEGER NOT NULL DEFAULT 0"),
                ("discount_amount", "INTEGER NOT NULL DEFAULT 0"), ("discount_code", "TEXT"),
                ("membership_level", "TEXT"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE web_delivery_orders ADD COLUMN {name} {definition}")
            conn.execute("UPDATE web_delivery_orders SET status_updated_at=created_at WHERE status_updated_at IS NULL")
            conn.execute("UPDATE web_delivery_orders SET subtotal=price WHERE subtotal IS NULL")
            conn.execute("DROP INDEX IF EXISTS one_pending_web_delivery")
            conn.execute("""CREATE UNIQUE INDEX one_pending_web_delivery ON web_delivery_orders(customer_key)
                WHERE status IN ('pending','accepted','on_way','arrived','processing')""")

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
                WHERE status IN ('pending','accepted','on_way','arrived','processing') AND guild_id=? AND channel_id!=?""",
                (str(channel_id), str(guild_id), str(channel_id)))
            self.audit(conn, "web_delivery_channel", str(channel_id), str(staff_id), staff_name)

    def configured(self):
        with self.db.connect() as conn:
            return conn.execute("SELECT 1 FROM web_delivery_settings WHERE id=1").fetchone() is not None

    @staticmethod
    def normalize_discount_code(code):
        return str(code or "").strip().upper()

    def create_discount_code(self, code, discount_type, amount, max_uses, expires_on,
                             staff_id, staff_name):
        code = self.normalize_discount_code(code)
        if not re.fullmatch(r"[A-Z0-9-]{3,20}", code):
            raise ValueError("Code must be 3–20 letters, numbers or hyphens.")
        discount_type = str(discount_type).strip().lower()
        if discount_type not in ("percent", "fixed"):
            raise ValueError("Discount type must be percent or fixed.")
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            raise ValueError("Discount amount must be a whole number.")
        if amount <= 0 or (discount_type == "percent" and amount > 100):
            raise ValueError("Enter a valid positive discount amount (maximum 100%).")
        try:
            max_uses = int(max_uses) if str(max_uses or "").strip() else None
        except (TypeError, ValueError):
            raise ValueError("Usage limit must be a whole number or left blank.")
        if max_uses is not None and not 1 <= max_uses <= 10000:
            raise ValueError("Usage limit must be between 1 and 10,000.")
        expires_on = str(expires_on or "").strip() or None
        if expires_on:
            try:
                expiry = date.fromisoformat(expires_on)
            except ValueError:
                raise ValueError("Expiry must use YYYY-MM-DD, for example 2026-12-31.")
            if expiry < date.today():
                raise ValueError("The expiry date cannot be in the past.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM delivery_discount_codes WHERE code=?", (code,)).fetchone():
                raise ValueError("That discount code already exists.")
            conn.execute("""INSERT INTO delivery_discount_codes
                (code,discount_type,amount,max_uses,expires_on,created_at,created_by,created_by_name)
                VALUES(?,?,?,?,?,?,?,?)""",
                (code, discount_type, amount, max_uses, expires_on, utc_now(), str(staff_id), staff_name))
            self.audit(conn, "delivery_discount_created",
                       f"code={code};type={discount_type};amount={amount};max_uses={max_uses};expires={expires_on}",
                       str(staff_id), staff_name)
        return self.discount_code(code)

    def discount_code(self, code):
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM delivery_discount_codes WHERE code=?",
                               (self.normalize_discount_code(code),)).fetchone()
        return dict(row) if row else None

    def discount_codes(self, active_only=False):
        where = "WHERE active=1" if active_only else ""
        with self.db.connect() as conn:
            rows = conn.execute(f"SELECT * FROM delivery_discount_codes {where} ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def disable_discount_code(self, code, staff_id, staff_name):
        code = self.normalize_discount_code(code)
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM delivery_discount_codes WHERE code=?", (code,)).fetchone()
            if not row:
                raise ValueError("Discount code not found.")
            if not row["active"]:
                return dict(row)
            conn.execute("UPDATE delivery_discount_codes SET active=0 WHERE code=?", (code,))
            self.audit(conn, "delivery_discount_disabled", f"code={code}", str(staff_id), staff_name)
        return self.discount_code(code)

    @staticmethod
    def _discount_amount(row, subtotal):
        if row["discount_type"] == "percent":
            return min(subtotal, (subtotal * int(row["amount"]) + 50) // 100)
        return min(subtotal, int(row["amount"]))

    def create_cart_authenticated(self, customer_key, quantities, postal, request_key, notes="",
                                  discount_code=""):
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
                AND status IN ('pending','accepted','on_way','arrived','processing')""", (key,)).fetchone()
            if pending:
                return dict(pending)
            config = conn.execute("SELECT * FROM web_delivery_settings WHERE id=1").fetchone()
            if not config:
                raise ValueError("Online delivery is being set up. Please contact SNR Buns.")
            customer = conn.execute("SELECT * FROM customers WHERE customer_key=?", (key,)).fetchone()
            if not customer:
                raise ValueError("Customer account not found.")
            membership = self.db.membership(customer)
            delivery_fee = int(membership["delivery_fee"])
            entered_code = self.normalize_discount_code(discount_code)
            discount_amount = 0
            if entered_code:
                discount = conn.execute(
                    "SELECT * FROM delivery_discount_codes WHERE code=?", (entered_code,)).fetchone()
                if not discount or not discount["active"]:
                    raise ValueError("That discount code is not valid.")
                if discount["expires_on"] and discount["expires_on"] < date.today().isoformat():
                    raise ValueError("That discount code has expired.")
                if discount["max_uses"] is not None and int(discount["uses"]) >= int(discount["max_uses"]):
                    raise ValueError("That discount code has reached its usage limit.")
                discount_amount = self._discount_amount(discount, subtotal)
                conn.execute("UPDATE delivery_discount_codes SET uses=uses+1 WHERE code=?", (entered_code,))
            total = subtotal - discount_amount + delivery_fee
            cursor = conn.execute("""INSERT INTO web_delivery_orders
                (customer_key,customer_name,deal_key,deal_name,price,postal,request_key,created_at,
                 channel_id,guild_id,items_json,notes,status_updated_at,subtotal,delivery_fee,
                 discount_amount,discount_code,membership_level) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (key, customer["display_name"], "cart", description, total, postal, request_key, utc_now(),
                 config["channel_id"], config["guild_id"], items_json, notes, utc_now(), subtotal,
                 delivery_fee, discount_amount, entered_code or None, membership["name"]))
            self.audit(conn, "web_delivery_requested",
                       f"order={cursor.lastrowid};customer={key};items={description};subtotal={subtotal};"
                       f"delivery_fee={delivery_fee};discount={discount_amount};total={total};"
                       f"code={entered_code};membership={membership['name']};postal={postal};notes={notes}")
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
        where = "WHERE status IN ('pending','accepted','on_way','arrived','processing')" + (" AND message_id IS NULL" if unsent else "")
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute(f"SELECT * FROM web_delivery_orders {where} ORDER BY id")]

    def get(self, order_id):
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM web_delivery_orders WHERE id=?", (int(order_id),)).fetchone()
        return dict(row) if row else None

    def ticket_result(self, order_id):
        prefix = f"delivery:{int(order_id)}"
        with self.db.connect() as conn:
            row = conn.execute("""SELECT COALESCE(SUM(golden_tickets),0) AS tickets,
                COALESCE(MAX(jackpot_won),0) AS jackpot_won FROM sales
                WHERE voided=0 AND (source_ref=? OR source_ref LIKE ?)""",
                (prefix, prefix + ":%"),).fetchone()
        return {"tickets": int(row["tickets"]), "jackpot_won": bool(row["jackpot_won"])}

    def notified(self, order_id, message_id):
        with self.db.connect() as conn:
            conn.execute("UPDATE web_delivery_orders SET message_id=? WHERE id=?", (str(message_id), int(order_id)))

    def status_counts(self, guild_id):
        with self.db.connect() as conn:
            rows = conn.execute("""SELECT status,COUNT(*) AS total FROM web_delivery_orders
                WHERE guild_id=? AND status IN ('pending','accepted','on_way','arrived','processing') GROUP BY status""",
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

    @staticmethod
    def _require_assigned_driver(row, staff_id, allow_override=False):
        """Keep an accepted order locked to its driver for every later action."""
        assigned_id = row["assigned_driver_id"]
        if assigned_id and assigned_id != str(staff_id) and not allow_override:
            driver = row["assigned_driver_name"] or "another driver"
            raise ValueError(
                f"This order has already been accepted by {driver}. "
                "It is locked to that driver."
            )

    def charge_wasted_journey(self, order_id, staff_id, staff_name, allow_override=False):
        order_id = int(order_id)
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM web_delivery_orders WHERE id=?", (order_id,)).fetchone()
            if not row:
                raise ValueError("Order not found.")
            self._require_assigned_driver(row, staff_id, allow_override)
            existing = conn.execute("SELECT * FROM delivery_fees WHERE order_id=?", (order_id,)).fetchone()
            if existing:
                return dict(row), dict(existing)
            if row["status"] != "arrived":
                raise ValueError("A Wasted Journey fee can only be added after the driver is marked Arrived.")
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

    def advance(self, order_id, target, staff_id, staff_name, allow_override=False):
        transitions = {"accepted": "pending", "on_way": "accepted", "arrived": "on_way"}
        if target not in transitions:
            raise ValueError("Invalid delivery update.")
        order_id = int(order_id)
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM web_delivery_orders WHERE id=?", (order_id,)).fetchone()
            if not row:
                raise ValueError("Order not found.")
            if target == "accepted" and row["status"] != "pending":
                # An old Discord button can still be clicked while another
                # interaction is refreshing. Never let that stale click claim
                # (or appear to claim) an order already owned by a driver.
                self._require_assigned_driver(row, staff_id, False)
                if row["status"] == "accepted":
                    return dict(row)
                raise ValueError("This delivery has already moved past the acceptance step.")
            if row["status"] == target:
                self._require_assigned_driver(row, staff_id, allow_override)
                return dict(row)
            if row["status"] != transitions[target]:
                raise ValueError("That delivery step has already been completed or is not ready yet.")
            if target in ("on_way", "arrived"):
                self._require_assigned_driver(row, staff_id, allow_override)
            now = utc_now()
            if target == "accepted":
                conn.execute("""UPDATE web_delivery_orders SET status='accepted',assigned_driver_id=?,
                    assigned_driver_name=?,accepted_at=?,status_updated_at=? WHERE id=?""",
                    (str(staff_id), staff_name, now, now, order_id))
            elif target == "on_way":
                conn.execute("UPDATE web_delivery_orders SET status='on_way',on_way_at=?,status_updated_at=? WHERE id=?",
                             (now, now, order_id))
            else:
                conn.execute("UPDATE web_delivery_orders SET status='arrived',arrived_at=?,status_updated_at=? WHERE id=?",
                             (now, now, order_id))
            self.audit(conn, "web_delivery_" + target, f"order={order_id}", str(staff_id), staff_name)
        return self.get(order_id)

    def resolve(self, order_id, status, staff_id, staff_name, allow_override=False):
        if status not in ("paid", "cancelled"):
            raise ValueError("Invalid order action.")
        order_id = int(order_id)
        if status == "cancelled":
            with self.db.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM web_delivery_orders WHERE id=?", (order_id,)).fetchone()
                if not row:
                    raise ValueError("Order not found.")
                if row["status"] not in ("pending", "accepted", "on_way", "arrived"):
                    raise ValueError("This order has already been processed.")
                self._require_assigned_driver(row, staff_id, allow_override)
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
            self._require_assigned_driver(row, staff_id, allow_override)
            already_paid = row["status"] == "paid"
            if not already_paid and row["status"] not in ("arrived", "processing"):
                raise ValueError("Mark the driver as arrived before confirming payment.")
            if not already_paid:
                conn.execute("UPDATE web_delivery_orders SET status='processing',status_updated_at=? WHERE id=?",
                             (utc_now(), order_id))
            order = dict(row)
        results = []
        try:
            units = []
            for line_index, item in enumerate(self.items(order)):
                for unit_index in range(int(item["quantity"])):
                    units.append((line_index, unit_index, item))
            base_total = sum(int(item["unit_price"]) for _, _, item in units)
            charged_total = int(order["price"])
            allocations = ([charged_total] if len(units) == 1 else
                           [int(item["unit_price"]) * charged_total // base_total
                            for _, _, item in units])
            if allocations:
                allocations[-1] += charged_total - sum(allocations)
            for allocation, (line_index, unit_index, item) in zip(allocations, units):
                source_ref = (f"delivery:{order_id}" if not order.get("items_json")
                              else f"delivery:{order_id}:{line_index}:{unit_index}")
                results.append(self.db.record_sale(
                    order["customer_name"], item["key"], str(staff_id), staff_name,
                    source_ref=source_ref, price_override=allocation))
        except Exception:
            with self.db.connect() as conn:
                conn.execute("UPDATE web_delivery_orders SET status='arrived',status_updated_at=? WHERE id=? AND status='processing'",
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
