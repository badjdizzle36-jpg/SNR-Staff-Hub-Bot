"""Two-way customer rewards, vouchers and staff action requests."""
from __future__ import annotations

import secrets

from snr_core import normalize_name, utc_now


ACTION_LABELS = {
    "staff_help": "Call SNR staff",
    "change_order": "Change my order",
    "cancel_order": "Cancel my order",
    "driver_help": "Driver cannot find me",
}


class CustomerServices:
    def __init__(self, db):
        self.db = db
        with db.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS custom_reward_catalog (
                    code TEXT PRIMARY KEY, name TEXT NOT NULL, points_cost INTEGER NOT NULL,
                    reward_kind TEXT NOT NULL, reward_amount INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1, display_order INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS custom_reward_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_key TEXT NOT NULL REFERENCES customers(customer_key),
                    customer_name TEXT NOT NULL, reward_code TEXT NOT NULL,
                    reward_name TEXT NOT NULL, points_cost INTEGER NOT NULL,
                    request_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL, resolved_at TEXT, resolved_by TEXT,
                    resolved_by_name TEXT, channel_id TEXT NOT NULL, guild_id TEXT NOT NULL,
                    message_id TEXT, voucher_id INTEGER);
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_custom_reward
                    ON custom_reward_requests(customer_key) WHERE status='pending';
                CREATE TABLE IF NOT EXISTS customer_vouchers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, voucher_code TEXT NOT NULL UNIQUE,
                    customer_key TEXT NOT NULL REFERENCES customers(customer_key),
                    customer_name TEXT NOT NULL, title TEXT NOT NULL, voucher_kind TEXT NOT NULL,
                    amount INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',
                    source TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT,
                    used_at TEXT, used_by TEXT, used_by_name TEXT, order_id INTEGER);
                CREATE TABLE IF NOT EXISTS customer_live_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_key TEXT NOT NULL REFERENCES customers(customer_key),
                    customer_name TEXT NOT NULL, action_type TEXT NOT NULL,
                    order_id INTEGER, details TEXT NOT NULL DEFAULT '',
                    request_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'pending',
                    staff_response TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                    resolved_at TEXT, resolved_by TEXT, resolved_by_name TEXT,
                    channel_id TEXT NOT NULL, guild_id TEXT NOT NULL, message_id TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_live_action
                    ON customer_live_actions(customer_key,action_type) WHERE status='pending';
            """)
            seeds = (
                ("FREE_DRINK", "Free drink", 4, "free_item", 0, 10),
                ("FREE_DESSERT", "Free dessert", 4, "free_item", 0, 20),
                ("FREE_BURGER", "Free burger", 6, "free_item", 0, 30),
                ("FREE_DELIVERY", "Free delivery", 5, "free_delivery", 0, 40),
                ("RAFFLE_ENTRY", "Bonus raffle entry", 8, "raffle_entry", 1, 50),
            )
            conn.executemany("""INSERT OR IGNORE INTO custom_reward_catalog
                (code,name,points_cost,reward_kind,reward_amount,display_order)
                VALUES(?,?,?,?,?,?)""", seeds)

    @staticmethod
    def _route(conn):
        row = conn.execute("SELECT channel_id,guild_id FROM web_delivery_settings WHERE id=1").fetchone()
        has_claims = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='web_claim_settings'"
        ).fetchone()
        if not row and has_claims:
            row = conn.execute("SELECT channel_id,guild_id FROM web_claim_settings WHERE id=1").fetchone()
        return row

    @staticmethod
    def _audit(conn, action, details, staff_id=None, staff_name=None):
        conn.execute("INSERT INTO audit_log(action,details,staff_id,staff_name,created_at) VALUES(?,?,?,?,?)",
                     (action, details, staff_id, staff_name, utc_now()))

    def catalog(self, enabled_only=True):
        where = "WHERE enabled=1" if enabled_only else ""
        with self.db.connect() as conn:
            rows = conn.execute(f"SELECT * FROM custom_reward_catalog {where} ORDER BY display_order,code").fetchall()
        return [dict(row) for row in rows]

    def set_reward_enabled(self, code, enabled, staff_id, staff_name):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM custom_reward_catalog WHERE code=?", (str(code),)).fetchone()
            if not row:
                raise ValueError("Reward not found.")
            conn.execute("UPDATE custom_reward_catalog SET enabled=? WHERE code=?", (1 if enabled else 0, str(code)))
            self._audit(conn, "custom_reward_toggled", f"reward={code};enabled={bool(enabled)}", str(staff_id), staff_name)
        return dict(row) | {"enabled": 1 if enabled else 0}

    def request_reward(self, customer_key, reward_code, request_key):
        key = normalize_name(customer_key)
        reward_code = str(reward_code or "").strip().upper()
        if not 10 <= len(str(request_key)) <= 160:
            raise ValueError("Refresh your account and try again.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM custom_reward_requests WHERE request_key=?", (request_key,)).fetchone()
            if existing:
                if existing["customer_key"] != key:
                    raise ValueError("Refresh your account and try again.")
                return dict(existing)
            pending = conn.execute("SELECT * FROM custom_reward_requests WHERE customer_key=? AND status='pending'", (key,)).fetchone()
            if pending:
                return dict(pending)
            reward = conn.execute("SELECT * FROM custom_reward_catalog WHERE code=? AND enabled=1", (reward_code,)).fetchone()
            customer = conn.execute("SELECT * FROM customers WHERE customer_key=?", (key,)).fetchone()
            route = self._route(conn)
            if not reward or not customer:
                raise ValueError("That reward is not currently available.")
            if not route:
                raise ValueError("Reward requests are being set up. Please ask staff.")
            if int(customer["loyalty_points"]) < int(reward["points_cost"]):
                raise ValueError(f"You need {int(reward['points_cost'])} points for {reward['name']}.")
            cursor = conn.execute("""INSERT INTO custom_reward_requests
                (customer_key,customer_name,reward_code,reward_name,points_cost,request_key,
                 created_at,channel_id,guild_id) VALUES(?,?,?,?,?,?,?,?,?)""",
                (key, customer["display_name"], reward_code, reward["name"], reward["points_cost"],
                 request_key, utc_now(), route["channel_id"], route["guild_id"]))
            self._audit(conn, "custom_reward_requested", f"request={cursor.lastrowid};customer={key};reward={reward_code}")
            return dict(conn.execute("SELECT * FROM custom_reward_requests WHERE id=?", (cursor.lastrowid,)).fetchone())

    def pending_rewards(self, unsent=False, limit=100):
        extra = " AND message_id IS NULL" if unsent else ""
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM custom_reward_requests WHERE status='pending'" + extra + " ORDER BY id LIMIT ?", (int(limit),)).fetchall()
        return [dict(row) for row in rows]

    def reward_requests(self, customer_key, limit=5):
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM custom_reward_requests WHERE customer_key=? ORDER BY id DESC LIMIT ?",
                                (normalize_name(customer_key), int(limit))).fetchall()
        return [dict(row) for row in rows]

    def reward_notified(self, request_id, message_id):
        with self.db.connect() as conn:
            conn.execute("UPDATE custom_reward_requests SET message_id=? WHERE id=?", (str(message_id), int(request_id)))

    def reward_request(self, request_id):
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM custom_reward_requests WHERE id=?", (int(request_id),)).fetchone()
        return dict(row) if row else None

    def resolve_reward(self, request_id, decision, staff_id, staff_name):
        if decision not in ("approved", "declined"):
            raise ValueError("Choose approve or decline.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM custom_reward_requests WHERE id=?", (int(request_id),)).fetchone()
            if not row or row["status"] != "pending":
                raise ValueError("This reward request has already been handled.")
            voucher_id = None
            if decision == "approved":
                customer = conn.execute("SELECT loyalty_points FROM customers WHERE customer_key=?", (row["customer_key"],)).fetchone()
                if not customer or int(customer["loyalty_points"]) < int(row["points_cost"]):
                    raise ValueError("The customer no longer has enough points.")
                reward = conn.execute("SELECT * FROM custom_reward_catalog WHERE code=?", (row["reward_code"],)).fetchone()
                code = "SNR-" + secrets.token_hex(4).upper()
                cursor = conn.execute("""INSERT INTO customer_vouchers
                    (voucher_code,customer_key,customer_name,title,voucher_kind,amount,source,created_at)
                    VALUES(?,?,?,?,?,?,?,?)""", (code, row["customer_key"], row["customer_name"], row["reward_name"],
                    reward["reward_kind"], reward["reward_amount"], f"points:{request_id}", utc_now()))
                voucher_id = cursor.lastrowid
                conn.execute("UPDATE customers SET loyalty_points=loyalty_points-?,updated_at=? WHERE customer_key=?",
                             (int(row["points_cost"]), utc_now(), row["customer_key"]))
            conn.execute("""UPDATE custom_reward_requests SET status=?,resolved_at=?,resolved_by=?,
                resolved_by_name=?,voucher_id=? WHERE id=?""", (decision, utc_now(), str(staff_id), staff_name,
                voucher_id, int(request_id)))
            self._audit(conn, "custom_reward_" + decision,
                        f"request={int(request_id)};customer={row['customer_key']};voucher={voucher_id}", str(staff_id), staff_name)
        return self.reward_request(request_id)

    def vouchers(self, customer_key, active_only=False):
        where = " AND status='active'" if active_only else ""
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM customer_vouchers WHERE customer_key=?" + where + " ORDER BY id DESC",
                                (normalize_name(customer_key),)).fetchall()
        return [dict(row) for row in rows]

    def redeem_voucher(self, voucher_code, staff_id, staff_name):
        code = str(voucher_code or "").strip().upper()
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM customer_vouchers WHERE voucher_code=?", (code,)).fetchone()
            if not row:
                raise ValueError("Voucher code not found.")
            if row["status"] != "active":
                raise ValueError("That voucher has already been used or cancelled.")
            conn.execute("UPDATE customer_vouchers SET status='used',used_at=?,used_by=?,used_by_name=? WHERE id=?",
                         (utc_now(), str(staff_id), staff_name, row["id"]))
            self._audit(conn, "customer_voucher_used", f"voucher={code};customer={row['customer_key']}", str(staff_id), staff_name)
        return dict(row) | {"status": "used"}

    def create_action(self, customer_key, action_type, order_id, details, request_key):
        key = normalize_name(customer_key)
        action_type = str(action_type or "").strip().lower()
        details = " ".join(str(details or "").split())
        if action_type not in ACTION_LABELS:
            raise ValueError("Choose a valid request.")
        if len(details) > 250:
            raise ValueError("Your message can be up to 250 characters.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM customer_live_actions WHERE request_key=?", (request_key,)).fetchone()
            if existing:
                return dict(existing)
            customer = conn.execute("SELECT display_name FROM customers WHERE customer_key=?", (key,)).fetchone()
            route = self._route(conn)
            if not customer or not route:
                raise ValueError("Customer requests are being set up. Please ask staff.")
            if order_id:
                order = conn.execute("SELECT * FROM web_delivery_orders WHERE id=? AND customer_key=?", (int(order_id), key)).fetchone()
                if not order:
                    raise ValueError("That order does not belong to your account.")
                if action_type in ("change_order", "cancel_order") and order["status"] != "pending":
                    raise ValueError("That order has already been accepted. Please call SNR staff for help.")
            cursor = conn.execute("""INSERT INTO customer_live_actions
                (customer_key,customer_name,action_type,order_id,details,request_key,created_at,channel_id,guild_id)
                VALUES(?,?,?,?,?,?,?,?,?)""", (key, customer["display_name"], action_type,
                int(order_id) if order_id else None, details, request_key, utc_now(), route["channel_id"], route["guild_id"]))
            self._audit(conn, "customer_live_action_created", f"action={cursor.lastrowid};customer={key};type={action_type};order={order_id}")
            return dict(conn.execute("SELECT * FROM customer_live_actions WHERE id=?", (cursor.lastrowid,)).fetchone())

    def pending_actions(self, unsent=False, limit=100):
        extra = " AND message_id IS NULL" if unsent else ""
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM customer_live_actions WHERE status='pending'" + extra + " ORDER BY id LIMIT ?", (int(limit),)).fetchall()
        return [dict(row) for row in rows]

    def action_notified(self, action_id, message_id):
        with self.db.connect() as conn:
            conn.execute("UPDATE customer_live_actions SET message_id=? WHERE id=?", (str(message_id), int(action_id)))

    def action(self, action_id):
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM customer_live_actions WHERE id=?", (int(action_id),)).fetchone()
        return dict(row) if row else None

    def resolve_action(self, action_id, decision, staff_id, staff_name, response=""):
        if decision not in ("resolved", "declined"):
            raise ValueError("Choose resolve or decline.")
        response = " ".join(str(response or "").split())[:250]
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM customer_live_actions WHERE id=?", (int(action_id),)).fetchone()
            if not row or row["status"] != "pending":
                raise ValueError("This request has already been handled.")
            conn.execute("""UPDATE customer_live_actions SET status=?,staff_response=?,resolved_at=?,
                resolved_by=?,resolved_by_name=? WHERE id=?""", (decision, response, utc_now(), str(staff_id), staff_name, int(action_id)))
            self._audit(conn, "customer_live_action_" + decision,
                        f"action={int(action_id)};customer={row['customer_key']};type={row['action_type']}", str(staff_id), staff_name)
        return self.action(action_id)

    def customer_actions(self, customer_key, limit=5):
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM customer_live_actions WHERE customer_key=? ORDER BY id DESC LIMIT ?",
                                (normalize_name(customer_key), int(limit))).fetchall()
        return [dict(row) for row in rows]

    def recent_audit(self, limit=20):
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(row) for row in rows]
