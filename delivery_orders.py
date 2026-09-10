"""Durable multi-item website delivery orders linked to SNR sales."""
import json
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from snr_core import DEALS, normalize_name, utc_now

ACTIVE_STATUSES = ("pending", "accepted", "on_way", "arrived", "ready_for_pickup", "processing")
SERVICE_MODES = {
    "open": "Open",
    "busy": "Busy",
    "pickup_only": "Pickup Only",
    "delivery_paused": "Deliveries Paused",
    "closed": "Closed",
}
ISSUE_CATEGORIES = {
    "food_quality": "Food quality",
    "delivery_time": "Delivery time",
    "driver_behaviour": "Driver behaviour",
    "missing_items": "Missing items",
    "other": "Other",
}

# Minutes spent in the current stage before the order changes from green to
# orange, then red. A stage change resets its alert state automatically.
LATE_THRESHOLDS = {
    "pending": (5, 7),
    "accepted": (7, 12),
    "on_way": (10, 15),
    "arrived": (5, 8),
    "ready_for_pickup": (10, 15),
    "processing": (3, 5),
}
ANNOUNCEMENT_STYLES = {"info", "promo", "urgent"}


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
                    membership_level TEXT, fulfillment_type TEXT NOT NULL DEFAULT 'delivery',
                    ready_at TEXT, birthday_discount INTEGER NOT NULL DEFAULT 0,
                    late_alert_level INTEGER NOT NULL DEFAULT 0,
                    birthday_reward_year INTEGER);
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
                CREATE TABLE IF NOT EXISTS customer_birthdays (
                    customer_key TEXT PRIMARY KEY REFERENCES customers(customer_key),
                    birthday_mmdd TEXT NOT NULL, set_at TEXT NOT NULL,
                    last_reward_year INTEGER);
                CREATE TABLE IF NOT EXISTS birthday_reward_settings (
                    id INTEGER PRIMARY KEY CHECK(id=1), reward_type TEXT NOT NULL,
                    amount INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL, updated_by TEXT, updated_by_name TEXT);
                CREATE TABLE IF NOT EXISTS delivery_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL UNIQUE REFERENCES web_delivery_orders(id),
                    customer_key TEXT NOT NULL REFERENCES customers(customer_key),
                    customer_name TEXT NOT NULL,
                    staff_id TEXT NOT NULL, staff_name TEXT NOT NULL,
                    fulfillment_type TEXT NOT NULL,
                    rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                    comment TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                    notification_message_id TEXT,
                    issue_category TEXT NOT NULL DEFAULT '',
                    resolution_status TEXT NOT NULL DEFAULT 'none',
                    resolved_at TEXT, resolved_by TEXT, resolved_by_name TEXT);
                CREATE TABLE IF NOT EXISTS delivery_support_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL UNIQUE REFERENCES web_delivery_orders(id),
                    customer_key TEXT NOT NULL REFERENCES customers(customer_key),
                    customer_name TEXT NOT NULL, issue_type TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL, resolved_at TEXT, resolved_by TEXT,
                    resolved_by_name TEXT, notification_message_id TEXT);
                CREATE TABLE IF NOT EXISTS web_service_mode (
                    id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL DEFAULT 'open',
                    updated_at TEXT NOT NULL, updated_by TEXT, updated_by_name TEXT);
                CREATE TABLE IF NOT EXISTS web_customer_announcement (
                    id INTEGER PRIMARY KEY CHECK(id=1), message TEXT NOT NULL DEFAULT '',
                    style TEXT NOT NULL DEFAULT 'info', active INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL, updated_by TEXT, updated_by_name TEXT);
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
                ("fulfillment_type", "TEXT NOT NULL DEFAULT 'delivery'"), ("ready_at", "TEXT"),
                ("birthday_discount", "INTEGER NOT NULL DEFAULT 0"),
                ("birthday_reward_year", "INTEGER"),
                ("late_alert_level", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE web_delivery_orders ADD COLUMN {name} {definition}")
            conn.execute("UPDATE web_delivery_orders SET status_updated_at=created_at WHERE status_updated_at IS NULL")
            conn.execute("UPDATE web_delivery_orders SET subtotal=price WHERE subtotal IS NULL")
            review_columns = {row["name"] for row in conn.execute("PRAGMA table_info(delivery_reviews)")}
            for name, definition in (
                ("issue_category", "TEXT NOT NULL DEFAULT ''"),
                ("resolution_status", "TEXT NOT NULL DEFAULT 'none'"),
                ("resolved_at", "TEXT"), ("resolved_by", "TEXT"), ("resolved_by_name", "TEXT"),
            ):
                if name not in review_columns:
                    conn.execute(f"ALTER TABLE delivery_reviews ADD COLUMN {name} {definition}")
            conn.execute("DROP INDEX IF EXISTS one_pending_web_delivery")
            conn.execute("""CREATE UNIQUE INDEX one_pending_web_delivery ON web_delivery_orders(customer_key)
                WHERE status IN ('pending','accepted','on_way','arrived','ready_for_pickup','processing')""")
            conn.execute("""INSERT OR IGNORE INTO birthday_reward_settings
                (id,reward_type,amount,active,updated_at) VALUES(1,'percent',20,1,?)""", (utc_now(),))
            conn.execute("""INSERT OR IGNORE INTO web_service_mode
                (id,mode,updated_at) VALUES(1,'open',?)""", (utc_now(),))
            conn.execute("""INSERT OR IGNORE INTO web_customer_announcement
                (id,message,style,active,updated_at) VALUES(1,'','info',0,?)""", (utc_now(),))

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
                WHERE status IN ('pending','accepted','on_way','arrived','ready_for_pickup','processing') AND guild_id=? AND channel_id!=?""",
                (str(channel_id), str(guild_id), str(channel_id)))
            self.audit(conn, "web_delivery_channel", str(channel_id), str(staff_id), staff_name)

    def configured(self):
        with self.db.connect() as conn:
            return conn.execute("SELECT 1 FROM web_delivery_settings WHERE id=1").fetchone() is not None

    def service_mode(self):
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM web_service_mode WHERE id=1").fetchone()
        result = dict(row) if row else {"mode": "open"}
        result["label"] = SERVICE_MODES.get(result["mode"], "Open")
        return result

    def set_service_mode(self, mode, staff_id, staff_name):
        mode = str(mode or "").strip().lower()
        if mode not in SERVICE_MODES:
            raise ValueError("Choose a valid service mode.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""INSERT OR REPLACE INTO web_service_mode
                (id,mode,updated_at,updated_by,updated_by_name) VALUES(1,?,?,?,?)""",
                (mode, utc_now(), str(staff_id), staff_name))
            self.audit(conn, "web_service_mode_changed", mode, str(staff_id), staff_name)
        return self.service_mode()

    def customer_announcement(self):
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM web_customer_announcement WHERE id=1").fetchone()
        result = dict(row) if row else {"message": "", "style": "info", "active": 0}
        result["active"] = bool(result.get("active"))
        return result

    def set_customer_announcement(self, message, style, staff_id, staff_name):
        message = " ".join(str(message or "").split())
        style = str(style or "").strip().lower()
        if not 3 <= len(message) <= 300:
            raise ValueError("Announcement must be between 3 and 300 characters.")
        if style not in ANNOUNCEMENT_STYLES:
            raise ValueError("Choose update, promotion or urgent notice.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""UPDATE web_customer_announcement
                SET message=?,style=?,active=1,updated_at=?,updated_by=?,updated_by_name=? WHERE id=1""",
                (message, style, utc_now(), str(staff_id), staff_name))
            self.audit(conn, "web_customer_announcement_set", f"style={style};message={message}",
                       str(staff_id), staff_name)
        return self.customer_announcement()

    def clear_customer_announcement(self, staff_id, staff_name):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""UPDATE web_customer_announcement SET active=0,updated_at=?,
                updated_by=?,updated_by_name=? WHERE id=1""",
                (utc_now(), str(staff_id), staff_name))
            self.audit(conn, "web_customer_announcement_cleared", "customer banner off",
                       str(staff_id), staff_name)
        return self.customer_announcement()

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
        discount_type = row["discount_type"] if "discount_type" in row.keys() else row["reward_type"]
        if discount_type == "percent":
            return min(subtotal, (subtotal * int(row["amount"]) + 50) // 100)
        return min(subtotal, int(row["amount"]))

    def birthday_config(self):
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM birthday_reward_settings WHERE id=1").fetchone()
        return dict(row)

    def configure_birthday_reward(self, reward_type, amount, staff_id, staff_name):
        reward_type = str(reward_type or "").strip().lower()
        if reward_type == "off":
            with self.db.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""UPDATE birthday_reward_settings SET active=0,updated_at=?,
                    updated_by=?,updated_by_name=? WHERE id=1""",
                    (utc_now(), str(staff_id), staff_name))
                self.audit(conn, "birthday_reward_disabled", "active=0", str(staff_id), staff_name)
            return self.birthday_config()
        if reward_type not in ("percent", "fixed"):
            raise ValueError("Type must be percent, fixed or off.")
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            raise ValueError("Birthday reward amount must be a whole number.")
        if amount <= 0 or (reward_type == "percent" and amount > 100):
            raise ValueError("Enter a positive reward amount (maximum 100%).")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""UPDATE birthday_reward_settings SET reward_type=?,amount=?,active=1,
                updated_at=?,updated_by=?,updated_by_name=? WHERE id=1""",
                (reward_type, amount, utc_now(), str(staff_id), staff_name))
            self.audit(conn, "birthday_reward_configured", f"type={reward_type};amount={amount}",
                       str(staff_id), staff_name)
        return self.birthday_config()

    def set_birthday_authenticated(self, customer_key, month, day):
        key = normalize_name(customer_key)
        try:
            month, day = int(month), int(day)
            date(2000, month, day)
        except (TypeError, ValueError):
            raise ValueError("Choose a valid birthday day and month.")
        mmdd = f"{month:02d}-{day:02d}"
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM customers WHERE customer_key=?", (key,)).fetchone():
                raise ValueError("Customer account not found.")
            existing = conn.execute("SELECT * FROM customer_birthdays WHERE customer_key=?", (key,)).fetchone()
            if existing:
                if existing["birthday_mmdd"] == mmdd:
                    return self.birthday_status(key)
                raise ValueError("Your birthday is already saved. Ask an SNR owner if it needs correcting.")
            conn.execute("INSERT INTO customer_birthdays(customer_key,birthday_mmdd,set_at) VALUES(?,?,?)",
                         (key, mmdd, utc_now()))
            self.audit(conn, "customer_birthday_set", f"customer={key};birthday={mmdd}")
        return self.birthday_status(key)

    def set_birthday_by_owner(self, customer_name, month, day, staff_id, staff_name):
        customer = self.db.get_customer(customer_name)
        if not customer:
            suggestion = self.db.suggest_name(customer_name)
            customer = self.db.get_customer(suggestion) if suggestion else None
        if not customer:
            raise ValueError("Customer not found. Create their loyalty account first.")
        try:
            month, day = int(month), int(day)
            date(2000, month, day)
        except (TypeError, ValueError):
            raise ValueError("Choose a valid birthday day and month.")
        key = customer["customer_key"]
        mmdd = f"{month:02d}-{day:02d}"
        verified_at = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(timespec="seconds")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""INSERT INTO customer_birthdays(customer_key,birthday_mmdd,set_at)
                VALUES(?,?,?) ON CONFLICT(customer_key) DO UPDATE SET
                birthday_mmdd=excluded.birthday_mmdd,set_at=excluded.set_at""",
                (key, mmdd, verified_at))
            self.audit(conn, "customer_birthday_owner_set",
                       f"customer={key};birthday={mmdd}", str(staff_id), staff_name)
        result = self.birthday_status(key)
        result["customer_name"] = customer["display_name"]
        return result

    @staticmethod
    def _birthday_eligibility_conn(conn, customer_key):
        now = datetime.now(ZoneInfo("Europe/London"))
        row = conn.execute("SELECT * FROM customer_birthdays WHERE customer_key=?", (customer_key,)).fetchone()
        config = conn.execute("SELECT * FROM birthday_reward_settings WHERE id=1").fetchone()
        if not row or not config or not config["active"]:
            return row, config, False, "not_available"
        if row["birthday_mmdd"] != now.strftime("%m-%d"):
            return row, config, False, "not_today"
        set_at = datetime.fromisoformat(row["set_at"])
        if set_at.tzinfo is None:
            set_at = set_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - set_at.astimezone(timezone.utc) < timedelta(days=7):
            return row, config, False, "security_wait"
        if row["last_reward_year"] == now.year:
            return row, config, False, "used"
        existing = conn.execute("""SELECT 1 FROM web_delivery_orders WHERE customer_key=?
            AND birthday_reward_year=? AND status NOT IN ('cancelled','wasted_journey') LIMIT 1""",
            (customer_key, now.year)).fetchone()
        if existing:
            return row, config, False, "reserved"
        return row, config, True, "ready"

    def birthday_status(self, customer_key):
        key = normalize_name(customer_key)
        with self.db.connect() as conn:
            row, config, eligible, reason = self._birthday_eligibility_conn(conn, key)
        display = ""
        if row:
            month, day = (int(part) for part in row["birthday_mmdd"].split("-"))
            display = date(2000, month, day).strftime("%d %B")
        reward = ""
        if config:
            reward = (f"{int(config['amount'])}% off one order" if config["reward_type"] == "percent"
                      else f"£{int(config['amount']):,} off one order")
        return {"saved": bool(row), "date": display, "eligible": eligible, "reason": reason,
                "reward": reward, "active": bool(config and config["active"])}

    def create_cart_authenticated(self, customer_key, quantities, postal, request_key, notes="",
                                  discount_code="", fulfillment_type="delivery"):
        key = normalize_name(customer_key)
        fulfillment_type = str(fulfillment_type or "delivery").strip().lower()
        if fulfillment_type not in ("delivery", "pickup", "instore"):
            raise ValueError("Choose In Store, Delivery or Pickup.")
        postal = " ".join(str(postal).strip().split())
        notes = " ".join(str(notes).strip().split())
        if fulfillment_type == "delivery" and not 2 <= len(postal) <= 80:
            raise ValueError("Enter a postal or clear delivery location between 2 and 80 characters.")
        if fulfillment_type == "pickup":
            postal = "SNR Buns — customer collection"
        elif fulfillment_type == "instore":
            postal = "SNR Buns — customer at counter"
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
                AND status IN ('pending','accepted','on_way','arrived','ready_for_pickup','processing')""", (key,)).fetchone()
            if pending:
                return dict(pending)
            config = conn.execute("SELECT * FROM web_delivery_settings WHERE id=1").fetchone()
            if not config:
                raise ValueError("Online delivery is being set up. Please contact SNR Buns.")
            service = conn.execute("SELECT mode FROM web_service_mode WHERE id=1").fetchone()
            service_mode = service["mode"] if service else "open"
            if service_mode == "closed":
                raise ValueError("SNR Buns is currently closed. Please try again when we reopen.")
            if fulfillment_type == "delivery" and service_mode in ("pickup_only", "delivery_paused"):
                raise ValueError("Deliveries are currently paused. Pickup ordering is still open.")
            customer = conn.execute("SELECT * FROM customers WHERE customer_key=?", (key,)).fetchone()
            if not customer:
                raise ValueError("Customer account not found.")
            membership = self.db.membership(customer)
            delivery_fee = int(membership["delivery_fee"]) if fulfillment_type == "delivery" else 0
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
            _, birthday_config, birthday_ready, _ = self._birthday_eligibility_conn(conn, key)
            birthday_discount = (self._discount_amount(birthday_config, subtotal - discount_amount)
                                 if birthday_ready else 0)
            birthday_year = datetime.now(ZoneInfo("Europe/London")).year if birthday_ready else None
            total = subtotal - discount_amount - birthday_discount + delivery_fee
            cursor = conn.execute("""INSERT INTO web_delivery_orders
                (customer_key,customer_name,deal_key,deal_name,price,postal,request_key,created_at,
                 channel_id,guild_id,items_json,notes,status_updated_at,subtotal,delivery_fee,
                 discount_amount,discount_code,membership_level,fulfillment_type,birthday_discount,
                 birthday_reward_year)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (key, customer["display_name"], "cart", description, total, postal, request_key, utc_now(),
                 config["channel_id"], config["guild_id"], items_json, notes, utc_now(), subtotal,
                 delivery_fee, discount_amount, entered_code or None, membership["name"], fulfillment_type,
                 birthday_discount, birthday_year))
            self.audit(conn, "web_delivery_requested",
                       f"order={cursor.lastrowid};customer={key};items={description};subtotal={subtotal};"
                       f"delivery_fee={delivery_fee};discount={discount_amount};birthday_discount={birthday_discount};total={total};"
                       f"code={entered_code};membership={membership['name']};type={fulfillment_type};"
                       f"postal={postal};notes={notes}")
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

    def review_for_order(self, order_id):
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM delivery_reviews WHERE order_id=?", (int(order_id),)
            ).fetchone()
        return dict(row) if row else None

    def create_review_authenticated(self, customer_key, order_id, rating, comment="", issue_category=""):
        """Rate the staff member already assigned to this customer's completed order."""
        key = normalize_name(customer_key)
        try:
            order_id, rating = int(order_id), int(rating)
        except (TypeError, ValueError):
            raise ValueError("Choose a star rating from 1 to 5.")
        if rating not in range(1, 6):
            raise ValueError("Choose a star rating from 1 to 5.")
        comment = " ".join(str(comment or "").strip().split())
        if len(comment) > 250:
            raise ValueError("Your review can be up to 250 characters.")
        issue_category = str(issue_category or "").strip().lower()
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            order = conn.execute(
                "SELECT * FROM web_delivery_orders WHERE id=?", (order_id,)
            ).fetchone()
            if not order or order["customer_key"] != key:
                raise ValueError("That order does not belong to your account.")
            if order["status"] != "paid":
                raise ValueError("You can leave a review after staff confirm the order is complete and paid.")
            if not order["assigned_driver_id"] or not order["assigned_driver_name"]:
                raise ValueError("This older order has no assigned staff member to review.")
            if conn.execute(
                "SELECT 1 FROM delivery_reviews WHERE order_id=?", (order_id,)
            ).fetchone():
                raise ValueError("You have already reviewed this order.")
            if rating <= 2 and issue_category not in ISSUE_CATEGORIES:
                raise ValueError("Please tell us what went wrong so management can help.")
            if rating > 2:
                issue_category = ""
            resolution_status = "open" if rating <= 2 else "none"
            cursor = conn.execute("""INSERT INTO delivery_reviews
                (order_id,customer_key,customer_name,staff_id,staff_name,fulfillment_type,
                 rating,comment,created_at,issue_category,resolution_status) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (order_id, key, order["customer_name"], order["assigned_driver_id"],
                 order["assigned_driver_name"], order["fulfillment_type"] or "delivery",
                 rating, comment, utc_now(), issue_category, resolution_status))
            self.audit(conn, "delivery_review_created",
                       f"review={cursor.lastrowid};order={order_id};staff={order['assigned_driver_id']};rating={rating}")
            row = conn.execute(
                "SELECT * FROM delivery_reviews WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
        return dict(row)

    def open_low_reviews(self, limit=100):
        with self.db.connect() as conn:
            rows = conn.execute("""SELECT r.*,o.guild_id,o.channel_id FROM delivery_reviews r
                JOIN web_delivery_orders o ON o.id=r.order_id
                WHERE r.rating<=2 AND r.resolution_status='open' ORDER BY r.id LIMIT ?""",
                (int(limit),)).fetchall()
        return [dict(row) for row in rows]

    def resolve_low_review(self, review_id, staff_id, staff_name):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM delivery_reviews WHERE id=?", (int(review_id),)).fetchone()
            if not row or int(row["rating"]) > 2:
                raise ValueError("Low-rating case not found.")
            if row["resolution_status"] == "resolved":
                return dict(row)
            conn.execute("""UPDATE delivery_reviews SET resolution_status='resolved',resolved_at=?,
                resolved_by=?,resolved_by_name=? WHERE id=?""",
                (utc_now(), str(staff_id), staff_name, int(review_id)))
            self.audit(conn, "low_rating_resolved", f"review={int(review_id)}", str(staff_id), staff_name)
            row = conn.execute("SELECT * FROM delivery_reviews WHERE id=?", (int(review_id),)).fetchone()
        return dict(row)

    def support_for_order(self, order_id):
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM delivery_support_requests WHERE order_id=?",
                               (int(order_id),)).fetchone()
        return dict(row) if row else None

    def create_support_authenticated(self, customer_key, order_id, issue_type, details=""):
        key = normalize_name(customer_key)
        issue_type = str(issue_type or "").strip().lower()
        if issue_type not in ISSUE_CATEGORIES:
            raise ValueError("Choose what went wrong with your order.")
        details = " ".join(str(details or "").strip().split())
        if len(details) > 250:
            raise ValueError("Your message can be up to 250 characters.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            order = conn.execute("SELECT * FROM web_delivery_orders WHERE id=?", (int(order_id),)).fetchone()
            if not order or order["customer_key"] != key:
                raise ValueError("That order does not belong to your account.")
            if order["status"] != "paid":
                raise ValueError("You can report a problem after the order is completed.")
            existing = conn.execute("SELECT * FROM delivery_support_requests WHERE order_id=?",
                                    (int(order_id),)).fetchone()
            if existing:
                return dict(existing)
            cursor = conn.execute("""INSERT INTO delivery_support_requests
                (order_id,customer_key,customer_name,issue_type,details,created_at)
                VALUES(?,?,?,?,?,?)""",
                (int(order_id), key, order["customer_name"], issue_type, details, utc_now()))
            self.audit(conn, "order_problem_created",
                       f"support={cursor.lastrowid};order={int(order_id)};type={issue_type}")
            row = conn.execute("SELECT * FROM delivery_support_requests WHERE id=?",
                               (cursor.lastrowid,)).fetchone()
        return dict(row)

    def pending_support(self, unsent=False, limit=100):
        extra = " AND s.notification_message_id IS NULL" if unsent else ""
        with self.db.connect() as conn:
            rows = conn.execute(f"""SELECT s.*,o.guild_id,o.channel_id FROM delivery_support_requests s
                JOIN web_delivery_orders o ON o.id=s.order_id
                WHERE s.status='pending'{extra} ORDER BY s.id LIMIT ?""", (int(limit),)).fetchall()
        return [dict(row) for row in rows]

    def support_notified(self, support_id, message_id):
        with self.db.connect() as conn:
            conn.execute("UPDATE delivery_support_requests SET notification_message_id=? WHERE id=?",
                         (str(message_id), int(support_id)))

    def resolve_support(self, support_id, staff_id, staff_name):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM delivery_support_requests WHERE id=?",
                               (int(support_id),)).fetchone()
            if not row:
                raise ValueError("Order problem not found.")
            if row["status"] == "resolved":
                return dict(row)
            conn.execute("""UPDATE delivery_support_requests SET status='resolved',resolved_at=?,
                resolved_by=?,resolved_by_name=? WHERE id=?""",
                (utc_now(), str(staff_id), staff_name, int(support_id)))
            self.audit(conn, "order_problem_resolved", f"support={int(support_id)}",
                       str(staff_id), staff_name)
            row = conn.execute("SELECT * FROM delivery_support_requests WHERE id=?",
                               (int(support_id),)).fetchone()
        return dict(row)

    def order_estimate(self, order_id):
        row = self.get(order_id)
        if not row:
            return {"queue_position": 0, "eta_text": "Order not found"}
        status = row["status"]
        pickup = (row.get("fulfillment_type") or "delivery") == "pickup"
        with self.db.connect() as conn:
            queue = conn.execute("""SELECT COUNT(*) AS total FROM web_delivery_orders
                WHERE guild_id=? AND fulfillment_type=? AND status='pending' AND id<=?""",
                (row["guild_id"], row.get("fulfillment_type") or "delivery", int(order_id))).fetchone()
            mode = conn.execute("SELECT mode FROM web_service_mode WHERE id=1").fetchone()
        position = int(queue["total"]) if status == "pending" else 0
        busy = bool(mode and mode["mode"] == "busy")
        if status == "pending":
            base = 10 if busy else 5
            low, high = base + max(position - 1, 0) * 5, base + 5 + max(position - 1, 0) * 7
            text = f"Estimated {low}–{high} minutes • queue position {position}"
        elif status == "accepted":
            text = "Estimated 10–20 minutes" if busy else "Estimated 5–10 minutes"
        elif status == "on_way":
            text = "Estimated arrival in 3–8 minutes"
        elif status == "arrived":
            text = "Your driver is outside now"
        elif status == "ready_for_pickup":
            text = "Ready to collect now"
        elif status == "processing":
            text = "Completing payment now"
        elif status == "paid":
            text = "Order complete"
        else:
            text = "Order closed"
        if pickup and status == "pending":
            text = f"Estimated {10 if busy else 5}–{20 if busy else 10} minutes • queue position {position}"
        if row.get("fulfillment_type") == "instore" and status == "pending":
            text = "Your order is with the counter. Please pay SNR staff."
        return {"queue_position": position, "eta_text": text}

    def unnotified_reviews(self, limit=20):
        with self.db.connect() as conn:
            rows = conn.execute("""SELECT r.*,o.guild_id,o.channel_id FROM delivery_reviews r
                JOIN web_delivery_orders o ON o.id=r.order_id
                WHERE r.notification_message_id IS NULL ORDER BY r.id LIMIT ?""",
                (int(limit),)).fetchall()
        return [dict(row) for row in rows]

    def review_notified(self, review_id, message_id):
        with self.db.connect() as conn:
            conn.execute("UPDATE delivery_reviews SET notification_message_id=? WHERE id=?",
                         (str(message_id), int(review_id)))

    def review_leaderboard(self, guild_id, days=7):
        days = int(days)
        if days not in (7, 30):
            raise ValueError("Review period must be 7 or 30 days.")
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        with self.db.connect() as conn:
            rows = conn.execute("""SELECT r.staff_id,r.staff_name,
                COUNT(*) AS reviews,ROUND(AVG(r.rating),2) AS average_rating,
                SUM(CASE WHEN r.rating=5 THEN 1 ELSE 0 END) AS five_star_reviews,
                SUM(CASE WHEN r.fulfillment_type='pickup' THEN 1 ELSE 0 END) AS pickups,
                SUM(CASE WHEN r.fulfillment_type='instore' THEN 1 ELSE 0 END) AS instore,
                SUM(CASE WHEN r.fulfillment_type='delivery' THEN 1 ELSE 0 END) AS deliveries
                FROM delivery_reviews r JOIN web_delivery_orders o ON o.id=r.order_id
                WHERE o.guild_id=? AND r.created_at>=?
                GROUP BY r.staff_id,r.staff_name
                ORDER BY average_rating DESC,reviews DESC,five_star_reviews DESC,r.staff_name COLLATE NOCASE""",
                (str(guild_id), since)).fetchall()
        return [dict(row) for row in rows]

    def pending(self, unsent=False):
        where = "WHERE status IN ('pending','accepted','on_way','arrived','ready_for_pickup','processing')" + (" AND message_id IS NULL" if unsent else "")
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute(f"SELECT * FROM web_delivery_orders {where} ORDER BY id")]

    @staticmethod
    def order_health(row, now=None):
        row = dict(row)
        status = row.get("status")
        thresholds = LATE_THRESHOLDS.get(status)
        if not thresholds:
            return {"level": 0, "colour": "green", "minutes": 0,
                    "label": "Complete"}
        raw_time = row.get("status_updated_at") or row.get("created_at")
        try:
            changed = datetime.fromisoformat(raw_time)
            if changed.tzinfo is None:
                changed = changed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            changed = datetime.now(timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        minutes = max(0, int((current - changed.astimezone(timezone.utc)).total_seconds() // 60))
        orange_at, red_at = thresholds
        if minutes >= red_at:
            level, colour, description = 2, "red", "Overdue"
        elif minutes >= orange_at:
            level, colour, description = 1, "orange", "Approaching limit"
        else:
            level, colour, description = 0, "green", "On time"
        return {"level": level, "colour": colour, "minutes": minutes,
                "label": f"{description} — {minutes} min in this stage"}

    def late_alerts(self, guild_id=None):
        rows = self.pending()
        alerts = []
        for row in rows:
            if guild_id is not None and row["guild_id"] != str(guild_id):
                continue
            health = self.order_health(row)
            if health["level"] > int(row.get("late_alert_level") or 0):
                alerts.append({**row, "late_level": health["level"],
                               "late_colour": health["colour"],
                               "late_minutes": health["minutes"],
                               "late_label": health["label"]})
        return alerts

    def mark_late_alert(self, order_id, level):
        level = max(0, min(2, int(level)))
        with self.db.connect() as conn:
            conn.execute("""UPDATE web_delivery_orders SET late_alert_level=?
                WHERE id=? AND late_alert_level<?""", (level, int(order_id), level))

    def health_counts(self, guild_id):
        counts = {"green": 0, "orange": 0, "red": 0}
        for row in self.pending():
            if row["guild_id"] == str(guild_id):
                counts[self.order_health(row)["colour"]] += 1
        return counts

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
                WHERE guild_id=? AND status IN ('pending','accepted','on_way','arrived','ready_for_pickup','processing') GROUP BY status""",
                (str(guild_id),)).fetchall()
        counts = {name: 0 for name in ACTIVE_STATUSES}
        counts.update({row["status"]: int(row["total"]) for row in rows})
        return counts

    def fulfillment_counts(self, guild_id):
        with self.db.connect() as conn:
            rows = conn.execute("""SELECT fulfillment_type,COUNT(*) AS total
                FROM web_delivery_orders WHERE guild_id=?
                AND status IN ('pending','accepted','on_way','arrived','ready_for_pickup','processing')
                GROUP BY fulfillment_type""", (str(guild_id),)).fetchall()
        counts = {"delivery": 0, "pickup": 0}
        counts.update({(row["fulfillment_type"] or "delivery"): int(row["total"]) for row in rows})
        return counts

    def daily_summary(self, guild_id):
        london_now = datetime.now(ZoneInfo("Europe/London"))
        london_start = london_now.replace(hour=0, minute=0, second=0, microsecond=0)
        utc_start = london_start.astimezone(timezone.utc).isoformat(timespec="seconds")
        utc_end = london_now.astimezone(timezone.utc).isoformat(timespec="seconds")
        with self.db.connect() as conn:
            paid = conn.execute("""SELECT COUNT(*) AS orders,
                COALESCE(SUM(CASE WHEN fulfillment_type='pickup' THEN 1 ELSE 0 END),0) AS pickups,
                COALESCE(SUM(CASE WHEN fulfillment_type='instore' THEN 1 ELSE 0 END),0) AS instore,
                COALESCE(SUM(CASE WHEN fulfillment_type='delivery' THEN 1 ELSE 0 END),0) AS deliveries,
                COALESCE(SUM(subtotal),0) AS food_subtotal,
                COALESCE(SUM(delivery_fee),0) AS delivery_fees,
                COALESCE(SUM(discount_amount),0) AS code_discounts,
                COALESCE(SUM(birthday_discount),0) AS birthday_discounts,
                COALESCE(SUM(price),0) AS collected
                FROM web_delivery_orders WHERE guild_id=? AND status='paid'
                AND resolved_at>=? AND resolved_at<=?""", (str(guild_id), utc_start, utc_end)).fetchone()
            wasted = conn.execute("""SELECT COUNT(*) AS total,COALESCE(SUM(f.amount),0) AS amount
                FROM delivery_fees f JOIN web_delivery_orders o ON o.id=f.order_id
                WHERE o.guild_id=? AND f.created_at>=? AND f.created_at<=?""",
                (str(guild_id), utc_start, utc_end)).fetchone()
            active = conn.execute("""SELECT COUNT(*) AS total FROM web_delivery_orders WHERE guild_id=?
                AND status IN ('pending','accepted','on_way','arrived','ready_for_pickup','processing')""",
                (str(guild_id),)).fetchone()
            birthday_due = conn.execute("""SELECT COUNT(*) AS total FROM customer_birthdays b
                JOIN birthday_reward_settings s ON s.id=1
                WHERE s.active=1 AND b.birthday_mmdd=? AND b.set_at<=?
                AND (b.last_reward_year IS NULL OR b.last_reward_year!=?)
                AND NOT EXISTS (SELECT 1 FROM web_delivery_orders o WHERE o.customer_key=b.customer_key
                    AND o.birthday_reward_year=? AND o.status NOT IN ('cancelled','wasted_journey'))""",
                (london_now.strftime("%m-%d"),
                 (datetime.now(timezone.utc)-timedelta(days=7)).isoformat(timespec="seconds"),
                 london_now.year, london_now.year)).fetchone()
        result = dict(paid)
        result.update({"wasted_journeys": int(wasted["total"]), "wasted_fees": int(wasted["amount"]),
                       "active_orders": int(active["total"]), "birthday_rewards_due": int(birthday_due["total"])})
        return {key: int(value or 0) for key, value in result.items()}

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
            if row["fulfillment_type"] != "delivery":
                raise ValueError("Pickup and in-store orders cannot receive a Wasted Journey fee.")
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
        transitions = {
            "accepted": "pending", "on_way": "accepted", "arrived": "on_way",
            "ready_for_pickup": "accepted",
        }
        if target not in transitions:
            raise ValueError("Invalid delivery update.")
        order_id = int(order_id)
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM web_delivery_orders WHERE id=?", (order_id,)).fetchone()
            if not row:
                raise ValueError("Order not found.")
            fulfillment = row["fulfillment_type"] or "delivery"
            if fulfillment == "instore":
                raise ValueError("In-store orders only need Confirm Payment or Cancel Order.")
            if fulfillment == "pickup" and target in ("on_way", "arrived"):
                raise ValueError("Pickup orders must be marked Ready for Collection.")
            if fulfillment == "delivery" and target == "ready_for_pickup":
                raise ValueError("Delivery orders must use Driver On The Way.")
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
            if target in ("on_way", "arrived", "ready_for_pickup"):
                self._require_assigned_driver(row, staff_id, allow_override)
            now = utc_now()
            if target == "accepted":
                conn.execute("""UPDATE web_delivery_orders SET status='accepted',assigned_driver_id=?,
                    assigned_driver_name=?,accepted_at=?,status_updated_at=?,late_alert_level=0 WHERE id=?""",
                    (str(staff_id), staff_name, now, now, order_id))
            elif target == "on_way":
                conn.execute("UPDATE web_delivery_orders SET status='on_way',on_way_at=?,status_updated_at=?,late_alert_level=0 WHERE id=?",
                             (now, now, order_id))
            elif target == "arrived":
                conn.execute("UPDATE web_delivery_orders SET status='arrived',arrived_at=?,status_updated_at=?,late_alert_level=0 WHERE id=?",
                             (now, now, order_id))
            else:
                conn.execute("""UPDATE web_delivery_orders SET status='ready_for_pickup',
                    ready_at=?,status_updated_at=?,late_alert_level=0 WHERE id=?""", (now, now, order_id))
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
                if row["status"] not in ("pending", "accepted", "on_way", "arrived", "ready_for_pickup"):
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
            fulfillment = row["fulfillment_type"] or "delivery"
            if fulfillment == "instore" and row["status"] == "pending" and not row["assigned_driver_id"]:
                conn.execute("UPDATE web_delivery_orders SET assigned_driver_id=?,assigned_driver_name=? WHERE id=?",
                             (str(staff_id), staff_name, order_id))
                row = conn.execute("SELECT * FROM web_delivery_orders WHERE id=?", (order_id,)).fetchone()
            self._require_assigned_driver(row, staff_id, allow_override)
            already_paid = row["status"] == "paid"
            required_status = "pending" if fulfillment == "instore" else "ready_for_pickup" if fulfillment == "pickup" else "arrived"
            if not already_paid and row["status"] not in (required_status, "processing"):
                message = ("Mark the order Ready for Collection before confirming payment."
                           if fulfillment == "pickup" else
                           "Mark the driver as arrived before confirming payment.")
                raise ValueError(message)
            if not already_paid:
                conn.execute("UPDATE web_delivery_orders SET status='processing',status_updated_at=?,late_alert_level=0 WHERE id=?",
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
                conn.execute("UPDATE web_delivery_orders SET status=?,status_updated_at=? WHERE id=? AND status='processing'",
                             (required_status, utc_now(), order_id))
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
            if order.get("birthday_reward_year"):
                conn.execute("""UPDATE customer_birthdays SET last_reward_year=?
                    WHERE customer_key=?""", (int(order["birthday_reward_year"]), order["customer_key"]))
            self.audit(conn, "web_delivery_paid", f"order={order_id};transactions={transaction_ids}",
                       str(staff_id), staff_name)
        return self.get(order_id), results
