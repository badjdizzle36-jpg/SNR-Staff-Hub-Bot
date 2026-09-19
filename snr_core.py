from __future__ import annotations

import difflib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from city_run import award_tokens_for_sale, ensure_city_run_schema, reverse_tokens_for_sales


# Production costs supplied by SNR Buns management. Bulk prices are converted
# to a single-item cost before averages are calculated.
FOOD_COSTS = {
    "Burger": 7.70,
    "Chicken Wrap": 5.60,
}
DRINK_COSTS = {
    "Cherry Slush": 1.75,
    "Lemon Slush": 0.70,
    "Pineapple Slush": 6.25,
}
DESSERT_COSTS = {
    "Chocolate Muffin": 7.00,
    "Doughnut": 7.00,
    "Chocolate Ice Cream": 3.50,
    "Mint Ice Cream": 4.90,
    "Strawberry Ice Cream": 8.20,
    "Vanilla Ice Cream": 2.80,
}

AVERAGE_FOOD_COST = sum(FOOD_COSTS.values()) / len(FOOD_COSTS)
AVERAGE_DRINK_COST = sum(DRINK_COSTS.values()) / len(DRINK_COSTS)
AVERAGE_DESSERT_COST = sum(DESSERT_COSTS.values()) / len(DESSERT_COSTS)


@dataclass(frozen=True)
class Deal:
    key: str
    name: str
    price: int
    food: int
    drinks: int
    loyalty_points: int
    golden_tickets: int
    contents: str = ""

    @property
    def item_summary(self) -> str:
        return self.contents or f"{self.food} food + {self.drinks} drinks"

    @property
    def production_cost(self) -> float:
        if self.key == "sweet_treat":
            return round(self.food * AVERAGE_DESSERT_COST, 2)
        return round(
            (self.food * AVERAGE_FOOD_COST) + (self.drinks * AVERAGE_DRINK_COST),
            2,
        )

    @property
    def gross_profit(self) -> float:
        return round(self.price - self.production_cost, 2)

    @property
    def profit_margin(self) -> float:
        return round((self.gross_profit / self.price) * 100, 1) if self.price else 0.0


DEALS: dict[str, Deal] = {
    "quick_fix": Deal("quick_fix", "SNR Quick Fix", 150, 1, 1, 0, 0),
    "happy_meal": Deal("happy_meal", "SNR Happy Meal", 300, 2, 2, 0, 0),
    "sweet_treat": Deal("sweet_treat", "SNR Sweet Treat Deal", 400, 5, 0, 0, 0, "5 desserts"),
    "mega_deal": Deal("mega_deal", "SNR Mega Deal", 500, 4, 4, 1, 0),
    "blue_light": Deal("blue_light", "SNR Blue Light Deal", 600, 8, 8, 0, 0),
    "share_box": Deal("share_box", "SNR Share Box", 1200, 10, 10, 2, 0),
}

VIP_LEVELS = {
    "Regular": {"minimum_sales": 0, "bonus_points": 0, "bonus_tickets": 0, "delivery_fee": 100, "emoji": "🍔"},
    "Bronze": {"minimum_sales": 10, "bonus_points": 0, "bonus_tickets": 0, "delivery_fee": 90, "emoji": "🥉"},
    "Silver": {"minimum_sales": 25, "bonus_points": 0, "bonus_tickets": 0, "delivery_fee": 75, "emoji": "🥈"},
    "Gold": {"minimum_sales": 50, "bonus_points": 1, "bonus_tickets": 0, "delivery_fee": 50, "emoji": "🥇"},
    "Platinum": {"minimum_sales": 100, "bonus_points": 1, "bonus_tickets": 0, "delivery_fee": 25, "emoji": "💎"},
    "SNR VIP": {"minimum_sales": 200, "bonus_points": 2, "bonus_tickets": 0, "delivery_fee": 0, "emoji": "👑"},
}


def vip_level_for_sales(lifetime_sales: int, override: str | None = None) -> dict[str, Any]:
    if override in VIP_LEVELS:
        name = override
    else:
        name = "Regular"
        for candidate, details in VIP_LEVELS.items():
            if int(lifetime_sales) >= details["minimum_sales"]:
                name = candidate
    details = dict(VIP_LEVELS[name])
    details["name"] = name
    details["manual"] = override in VIP_LEVELS
    names = list(VIP_LEVELS)
    index = names.index(name)
    if index + 1 < len(names) and not details["manual"]:
        next_name = names[index + 1]
        details["next_level"] = next_name
        details["next_at"] = VIP_LEVELS[next_name]["minimum_sales"]
        details["remaining"] = max(0, details["next_at"] - int(lifetime_sales))
    else:
        details.update({"next_level": None, "next_at": None, "remaining": 0})
    return details


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_name(name: str) -> str:
    return " ".join(name.strip().casefold().split())


def display_name(name: str) -> str:
    return " ".join(part.capitalize() for part in name.strip().split())


class SNRDatabase:
    def __init__(self, path: str, jackpot_pool_size: int = 1000):
        self.path = path
        # Retained only so older deployment configuration can still construct
        # the database while the retired Golden Ticket columns are migrated.
        self.jackpot_pool_size = max(10, jackpot_pool_size)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _city_run_active(self) -> bool:
        with self.connect() as conn:
            return bool(conn.execute(
                "SELECT 1 FROM city_run_campaigns WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone())

    def _initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS customers (
                    customer_key TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    loyalty_points INTEGER NOT NULL DEFAULT 0,
                    lifetime_sales INTEGER NOT NULL DEFAULT 0,
                    card_packs_earned INTEGER NOT NULL DEFAULT 0,
                    card_packs_claimed INTEGER NOT NULL DEFAULT 0,
                    golden_tickets INTEGER NOT NULL DEFAULT 0,
                    jackpot_wins INTEGER NOT NULL DEFAULT 0,
                    revenue INTEGER NOT NULL DEFAULT 0,
                    food_sold INTEGER NOT NULL DEFAULT 0,
                    drinks_sold INTEGER NOT NULL DEFAULT 0,
                    leaderboard_excluded INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sales (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transaction_id TEXT UNIQUE,
                    customer_key TEXT NOT NULL,
                    customer_name TEXT NOT NULL,
                    deal_key TEXT NOT NULL,
                    deal_name TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    food INTEGER NOT NULL,
                    drinks INTEGER NOT NULL,
                    production_cost REAL NOT NULL DEFAULT 0,
                    loyalty_points INTEGER NOT NULL,
                    golden_tickets INTEGER NOT NULL,
                    card_rewards_created INTEGER NOT NULL DEFAULT 0,
                    jackpot_won INTEGER NOT NULL DEFAULT 0,
                    staff_id TEXT NOT NULL,
                    staff_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    source_ref TEXT,
                    voided INTEGER NOT NULL DEFAULT 0,
                    void_reason TEXT,
                    voided_at TEXT,
                    voided_by TEXT,
                    FOREIGN KEY(customer_key) REFERENCES customers(customer_key)
                );

                CREATE TABLE IF NOT EXISTS rewards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reward_code TEXT UNIQUE NOT NULL,
                    customer_key TEXT NOT NULL,
                    customer_name TEXT NOT NULL,
                    reward_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'unclaimed',
                    earned_sale_id INTEGER,
                    earned_at TEXT NOT NULL,
                    claimed_at TEXT,
                    claimed_by TEXT,
                    cancelled_at TEXT,
                    cancelled_by TEXT,
                    FOREIGN KEY(customer_key) REFERENCES customers(customer_key),
                    FOREIGN KEY(earned_sale_id) REFERENCES sales(id)
                );

                CREATE TABLE IF NOT EXISTS jackpot (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    cycle INTEGER NOT NULL,
                    pool_size INTEGER NOT NULL,
                    winning_position INTEGER NOT NULL,
                    tickets_issued INTEGER NOT NULL,
                    last_winner TEXT,
                    last_won_at TEXT
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    staff_id TEXT,
                    staff_name TEXT,
                    details TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

            # Add finance support without replacing an existing Railway database.
            sales_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sales)")}
            if "production_cost" not in sales_columns:
                conn.execute("ALTER TABLE sales ADD COLUMN production_cost REAL NOT NULL DEFAULT 0")
            if "source_ref" not in sales_columns:
                conn.execute("ALTER TABLE sales ADD COLUMN source_ref TEXT")
            customer_columns = {row["name"] for row in conn.execute("PRAGMA table_info(customers)")}
            if "vip_override" not in customer_columns:
                conn.execute("ALTER TABLE customers ADD COLUMN vip_override TEXT")
            if "leaderboard_excluded" not in customer_columns:
                conn.execute("ALTER TABLE customers ADD COLUMN leaderboard_excluded INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS unique_sale_source_ref ON sales(source_ref) WHERE source_ref IS NOT NULL"
            )

            # Backfill any earlier sales using the same category-average model.
            for deal in DEALS.values():
                conn.execute(
                    """UPDATE sales SET production_cost = ?
                       WHERE deal_key = ? AND production_cost = 0""",
                    (deal.production_cost, deal.key),
                )
            conn.execute(
                """UPDATE sales SET production_cost = ROUND(
                       (food * ?) + (drinks * ?), 2
                   ) WHERE production_cost = 0""",
                (AVERAGE_FOOD_COST, AVERAGE_DRINK_COST),
            )
            # Trading-card giveaways have been retired. Historical rows remain
            # for audit purposes, but no unclaimed card reward can be redeemed.
            conn.execute(
                """UPDATE rewards SET status = 'cancelled', cancelled_at = ?,
                   cancelled_by = 'system: trading-card rewards retired'
                   WHERE reward_type = 'card_pack' AND status = 'unclaimed'""",
                (utc_now(),),
            )
            # Golden Tickets are retired. Keep the legacy columns/tables so an
            # existing Railway SQLite database is upgraded in place, but clear
            # balances and retire any prize that was never handed over.
            conn.execute("UPDATE customers SET golden_tickets=0")
            conn.execute(
                """UPDATE rewards SET status='cancelled', cancelled_at=?,
                   cancelled_by='system: Golden Tickets retired'
                   WHERE reward_type='golden_jackpot' AND status='unclaimed'""",
                (utc_now(),),
            )
            # City Run is seeded in draft mode. It cannot issue packs until an
            # owner has configured every rarity and reward, then activates it.
            ensure_city_run_schema(conn, utc_now())

    def customer_names(self) -> list[str]:
        with self.connect() as conn:
            return [r["display_name"] for r in conn.execute("SELECT display_name FROM customers")]

    def leaderboard_customer_names(self, excluded: bool = False) -> list[str]:
        """Return customers available for leaderboard removal or restoration."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT display_name FROM customers WHERE leaderboard_excluded=? ORDER BY display_name COLLATE NOCASE",
                (1 if excluded else 0,),
            ).fetchall()
        return [row["display_name"] for row in rows]

    def set_leaderboard_excluded(
        self, name: str, excluded: bool, staff_id: str, staff_name: str,
    ) -> dict[str, Any]:
        """Reversibly remove a customer from the monthly customer chase."""
        key = normalize_name(name)
        now = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            customer = conn.execute(
                "SELECT * FROM customers WHERE customer_key=?", (key,)
            ).fetchone()
            if not customer:
                raise ValueError("Customer not found.")
            conn.execute(
                "UPDATE customers SET leaderboard_excluded=?,updated_at=? WHERE customer_key=?",
                (1 if excluded else 0, now, key),
            )
            conn.execute(
                "INSERT INTO audit_log(action,staff_id,staff_name,details,created_at) VALUES(?,?,?,?,?)",
                ("leaderboard_customer_excluded" if excluded else "leaderboard_customer_restored",
                 str(staff_id), staff_name,
                 json.dumps({"customer": customer["display_name"]}), now),
            )
        result = self.get_customer(name)
        if result is None:
            raise ValueError("Customer not found.")
        return result

    def recent_customer_names(self, limit: int = 10) -> list[str]:
        """Return recently served customers once each, newest first."""
        with self.connect() as conn:
            rows = conn.execute("""SELECT customer_name,MAX(id) AS latest FROM sales
                WHERE voided=0 GROUP BY customer_key,customer_name
                ORDER BY latest DESC LIMIT ?""", (max(1, min(int(limit), 25)),)).fetchall()
        return [row["customer_name"] for row in rows]

    @staticmethod
    def membership(customer: dict[str, Any] | sqlite3.Row) -> dict[str, Any]:
        return vip_level_for_sales(int(customer["lifetime_sales"]), customer["vip_override"])

    def set_vip_override(self, name: str, level: str | None, staff_id: str, staff_name: str) -> dict[str, Any]:
        if level in ("", "Automatic"):
            level = None
        if level is not None and level not in VIP_LEVELS:
            raise ValueError("Unknown membership level.")
        key = normalize_name(name)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            customer = conn.execute("SELECT * FROM customers WHERE customer_key=?", (key,)).fetchone()
            if not customer:
                raise ValueError("Customer not found.")
            conn.execute("UPDATE customers SET vip_override=?,updated_at=? WHERE customer_key=?",
                         (level, utc_now(), key))
            conn.execute(
                "INSERT INTO audit_log(action,staff_id,staff_name,details,created_at) VALUES(?,?,?,?,?)",
                ("vip_membership_changed", str(staff_id), staff_name,
                 json.dumps({"customer": customer["display_name"], "level": level or "Automatic"}), utc_now()),
            )
        return self.get_customer(name)

    def vip_counts(self) -> dict[str, int]:
        counts = {name: 0 for name in VIP_LEVELS}
        with self.connect() as conn:
            for row in conn.execute("SELECT lifetime_sales,vip_override FROM customers"):
                counts[self.membership(row)["name"]] += 1
        return counts

    def vip_membership_map(self) -> dict[str, dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT customer_key,lifetime_sales,vip_override FROM customers").fetchall()
        return {row["customer_key"]: self.membership(row) for row in rows}

    def suggest_name(self, name: str, cutoff: float = 0.82) -> str | None:
        wanted = normalize_name(name)
        names = self.customer_names()
        lookup = {normalize_name(n): n for n in names}
        if wanted in lookup:
            return lookup[wanted]
        matches = difflib.get_close_matches(wanted, list(lookup), n=1, cutoff=cutoff)
        return lookup[matches[0]] if matches else None

    def _ensure_customer(self, conn: sqlite3.Connection, name: str) -> sqlite3.Row:
        key = normalize_name(name)
        if not key:
            raise ValueError("Customer name cannot be empty.")
        row = conn.execute("SELECT * FROM customers WHERE customer_key = ?", (key,)).fetchone()
        if row:
            return row
        shown = display_name(name)
        now = utc_now()
        conn.execute(
            "INSERT INTO customers (customer_key, display_name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (key, shown, now, now),
        )
        return conn.execute("SELECT * FROM customers WHERE customer_key = ?", (key,)).fetchone()

    @staticmethod
    def _transaction_id(sale_id: int) -> str:
        return f"SNR-{sale_id:06d}"

    @staticmethod
    def _reward_code(reward_id: int) -> str:
        return f"REWARD-{reward_id:05d}"

    def _create_reward(
        self,
        conn: sqlite3.Connection,
        customer_key: str,
        customer_name: str,
        reward_type: str,
        description: str,
        sale_id: int,
    ) -> str:
        cursor = conn.execute(
            """INSERT INTO rewards
               (reward_code, customer_key, customer_name, reward_type, description, earned_sale_id, earned_at)
               VALUES ('PENDING', ?, ?, ?, ?, ?, ?)""",
            (customer_key, customer_name, reward_type, description, sale_id, utc_now()),
        )
        code = self._reward_code(cursor.lastrowid)
        conn.execute("UPDATE rewards SET reward_code = ? WHERE id = ?", (code, cursor.lastrowid))
        return code

    def _existing_sale_result(self, conn: sqlite3.Connection, sale: sqlite3.Row) -> dict[str, Any]:
        """Rebuild the standard result for an idempotent externally sourced sale."""
        customer = conn.execute(
            "SELECT * FROM customers WHERE customer_key=?", (sale["customer_key"],)
        ).fetchone()
        city_award = conn.execute(
            "SELECT amount FROM city_run_token_ledger WHERE source_key=?", (f"sale:{sale['id']}",)
        ).fetchone()
        return {
            "transaction_id": sale["transaction_id"],
            "customer": {**dict(customer), "membership": self.membership(customer)},
            "deal": DEALS[sale["deal_key"]],
            "card_reward_codes": [],
            "jackpot_won": False,
            "jackpot_reward_code": None,
            "winning_ticket": None,
            "ticket_positions": [],
            "jackpot_cycle": 0,
            "tickets_issued_in_cycle": 0,
            "loyalty_awarded": int(sale["loyalty_points"]),
            "tickets_awarded": 0,
            "city_run_reveals_awarded": int(city_award["amount"]) if city_award else 0,
        }

    def record_sale(
        self, customer_name: str, deal_key: str, staff_id: str, staff_name: str,
        source_ref: str | None = None, price_override: int | None = None,
    ) -> dict[str, Any]:
        if deal_key not in DEALS:
            raise ValueError("Unknown deal selected.")
        if source_ref is not None and not 3 <= len(source_ref) <= 100:
            raise ValueError("Invalid sale source reference.")
        deal = DEALS[deal_key]
        charged_price = deal.price if price_override is None else int(price_override)
        if charged_price < 0:
            raise ValueError("Sale price cannot be negative.")
        now = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if source_ref:
                previous = conn.execute(
                    "SELECT * FROM sales WHERE source_ref=?", (source_ref,)
                ).fetchone()
                if previous:
                    return self._existing_sale_result(conn, previous)
            customer = self._ensure_customer(conn, customer_name)
            key = customer["customer_key"]
            shown = customer["display_name"]

            vip = vip_level_for_sales(int(customer["lifetime_sales"]) + 1, customer["vip_override"])
            city_run_active = bool(conn.execute(
                "SELECT 1 FROM city_run_campaigns WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone())
            earned_stickers = deal.loyalty_points + int(vip["bonus_points"])
            # Once City Run is live, qualifying meals award City Run stickers
            # only; legacy account points no longer increase.
            sale_points = 0 if city_run_active else earned_stickers
            sale_tickets = 0
            previous_points_total = int(customer["loyalty_points"])
            new_points_total = previous_points_total + sale_points

            cursor = conn.execute(
                """INSERT INTO sales
                   (customer_key, customer_name, deal_key, deal_name, price, food, drinks, production_cost,
                    loyalty_points, golden_tickets, card_rewards_created, staff_id, staff_name, created_at,
                    source_ref)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    key, shown, deal.key, deal.name, charged_price, deal.food, deal.drinks,
                    deal.production_cost, sale_points, sale_tickets, 0,
                    str(staff_id), staff_name, now, source_ref,
                ),
            )
            sale_id = int(cursor.lastrowid)
            transaction_id = self._transaction_id(sale_id)
            conn.execute("UPDATE sales SET transaction_id = ? WHERE id = ?", (transaction_id, sale_id))

            conn.execute(
                """UPDATE customers SET loyalty_points = loyalty_points + ?, lifetime_sales = lifetime_sales + 1,
                   card_packs_earned = card_packs_earned + ?, golden_tickets = golden_tickets + ?,
                   jackpot_wins = jackpot_wins + ?, revenue = revenue + ?,
                   food_sold = food_sold + ?, drinks_sold = drinks_sold + ?, updated_at = ?
                   WHERE customer_key = ?""",
                (
                    sale_points, 0, 0, 0,
                    charged_price, deal.food, deal.drinks, now, key,
                ),
            )
            conn.execute(
                "INSERT INTO audit_log (action, staff_id, staff_name, details, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    "sale_recorded", str(staff_id), staff_name,
                    json.dumps({"transaction_id": transaction_id, "customer": shown, "deal": deal.key,
                                "loyalty_before": previous_points_total,
                                "loyalty_added": sale_points,
                                "loyalty_after": new_points_total}), now,
                ),
            )
            updated = conn.execute("SELECT * FROM customers WHERE customer_key = ?", (key,)).fetchone()
            if int(updated["loyalty_points"]) != new_points_total:
                raise RuntimeError("The sale was stopped because its City Run stickers did not save.")
            city_run_reveals_awarded = award_tokens_for_sale(
                conn, sale_id, key, shown, earned_stickers if city_run_active else 0, now
            )

        return {
            "transaction_id": transaction_id,
            "customer": {**dict(updated), "membership": self.membership(updated)},
            "deal": deal,
            "card_reward_codes": [],
            "jackpot_won": False,
            "jackpot_reward_code": None,
            "winning_ticket": None,
            "ticket_positions": [],
            "jackpot_cycle": 0,
            "tickets_issued_in_cycle": 0,
            "loyalty_awarded": sale_points,
            "city_run_stickers_awarded": city_run_reveals_awarded,
            "loyalty_before": previous_points_total,
            "loyalty_after": new_points_total,
            "tickets_awarded": 0,
            "city_run_reveals_awarded": city_run_reveals_awarded,
        }

    def record_sale_quantity(
        self, customer_name: str, deal_key: str, quantity: int, staff_id: str, staff_name: str,
    ) -> dict[str, Any]:
        """Record every purchased deal as a sale and return one combined staff receipt."""
        amount = int(quantity)
        if not 1 <= amount <= 10:
            raise ValueError("Sale amount must be between 1 and 10.")
        if deal_key not in DEALS:
            raise ValueError("Unknown deal selected.")
        batch_ref = secrets.token_hex(8)
        results = [self.record_sale(
            customer_name, deal_key, staff_id, staff_name,
            source_ref=f"staff:{batch_ref}:{index + 1}")
            for index in range(amount)]
        total_points = sum(int(result["loyalty_awarded"]) for result in results)
        base_points = DEALS[deal_key].loyalty_points * amount
        if not self._city_run_active() and total_points < base_points:
            raise RuntimeError("The full quantity City Run sticker reward was not recorded.")
        return {
            **results[-1],
            "quantity": amount,
            "transaction_ids": [result["transaction_id"] for result in results],
            "loyalty_awarded": total_points,
            "loyalty_before": int(results[0]["loyalty_before"]),
            "loyalty_after": int(results[-1]["loyalty_after"]),
            "base_loyalty_awarded": base_points,
            "membership_loyalty_awarded": total_points - base_points,
            # Aggregate City Run reveal credits across every item in the batch;
            # the last row alone would under-report 2×/3× Share Box purchases.
            "city_run_stickers_awarded": sum(int(result.get("city_run_stickers_awarded", 0)) for result in results),
            "city_run_reveals_awarded": sum(int(result.get("city_run_reveals_awarded", 0)) for result in results),
            "tickets_awarded": 0,
            "jackpot_won": False,
            "jackpot_reward_codes": [],
            "winning_tickets": [],
            "batch_ref": batch_ref,
        }

    def latest_counter_sale_batch(self) -> dict[str, Any] | None:
        """Find the most recent Discord counter-sale action, grouping its quantity rows."""
        with self.connect() as conn:
            latest = conn.execute("""SELECT * FROM sales WHERE voided=0
                AND (source_ref IS NULL OR source_ref LIKE 'staff:%') ORDER BY id DESC LIMIT 1""").fetchone()
            if not latest:
                return None
            source_ref = latest["source_ref"] or ""
            batch_ref = source_ref.split(":")[1] if source_ref.startswith("staff:") else ""
            if batch_ref:
                rows = conn.execute("""SELECT * FROM sales WHERE voided=0
                    AND source_ref LIKE ? ORDER BY id""", (f"staff:{batch_ref}:%",)).fetchall()
            else:
                rows = [latest]
        return {
            "batch_ref": batch_ref,
            "sale_ids": [int(row["id"]) for row in rows],
            "transaction_ids": [row["transaction_id"] for row in rows],
            "customer_key": latest["customer_key"],
            "customer_name": latest["customer_name"],
            "deal_key": latest["deal_key"],
            "deal_name": latest["deal_name"],
            "quantity": len(rows),
            "revenue": sum(int(row["price"]) for row in rows),
            "loyalty_points": sum(int(row["loyalty_points"]) for row in rows),
            "golden_tickets": 0,
            "has_jackpot_winner": False,
        }

    def undo_counter_sale_batch(self, batch_ref: str, sale_ids: list[int],
                                staff_id: str, staff_name: str) -> dict[str, Any]:
        """Owner-only caller reverses one counter-sale action without deleting its audit trail."""
        wanted_ids = sorted({int(value) for value in sale_ids})
        if not wanted_ids:
            raise ValueError("There is no sale to undo.")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            marks = ",".join("?" for _ in wanted_ids)
            rows = conn.execute(
                f"SELECT * FROM sales WHERE id IN ({marks}) AND voided=0 ORDER BY id", wanted_ids
            ).fetchall()
            if len(rows) != len(wanted_ids):
                raise ValueError("That sale has already been undone or changed.")
            expected_ref = str(batch_ref or "")
            for row in rows:
                actual_ref = row["source_ref"] or ""
                actual_batch = actual_ref.split(":")[1] if actual_ref.startswith("staff:") else ""
                if actual_batch != expected_ref or actual_ref.startswith("delivery:"):
                    raise ValueError("That sale selection is no longer valid.")
            customer_key = rows[0]["customer_key"]
            if any(row["customer_key"] != customer_key for row in rows):
                raise ValueError("The sale group is not valid.")
            quantity = len(rows)
            revenue = sum(int(row["price"]) for row in rows)
            points = sum(int(row["loyalty_points"]) for row in rows)
            tickets = sum(int(row["golden_tickets"]) for row in rows)
            food = sum(int(row["food"]) for row in rows)
            drinks = sum(int(row["drinks"]) for row in rows)
            now = utc_now()
            city_run_reveals_removed = reverse_tokens_for_sales(
                conn, wanted_ids, now, str(staff_id)
            )
            conn.execute(f"""UPDATE sales SET voided=1,void_reason='Owner undid mistaken counter sale',
                voided_at=?,voided_by=? WHERE id IN ({marks})""", [now, str(staff_id), *wanted_ids])
            conn.execute("""UPDATE customers SET
                loyalty_points=MAX(0,loyalty_points-?),
                lifetime_sales=MAX(0,lifetime_sales-?),
                golden_tickets=MAX(0,golden_tickets-?),
                revenue=MAX(0,revenue-?),food_sold=MAX(0,food_sold-?),
                drinks_sold=MAX(0,drinks_sold-?),updated_at=? WHERE customer_key=?""",
                (points, quantity, tickets, revenue, food, drinks, now, customer_key))
            remaining_points = int(conn.execute(
                "SELECT loyalty_points FROM customers WHERE customer_key=?", (customer_key,)
            ).fetchone()["loyalty_points"])
            cancelled_claims = 0
            claims_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='web_pack_claims'"
            ).fetchone()
            if remaining_points < 4 and claims_table:
                cursor = conn.execute("""UPDATE web_pack_claims SET status='cancelled',resolved_at=?,
                    resolved_by=? WHERE customer_key=? AND status='pending'""",
                    (now, str(staff_id), customer_key))
                cancelled_claims = int(cursor.rowcount)
            rewound = False
            conn.execute("""INSERT INTO audit_log(action,staff_id,staff_name,details,created_at)
                VALUES(?,?,?,?,?)""", ("counter_sale_undone", str(staff_id), staff_name,
                json.dumps({"transactions": [row["transaction_id"] for row in rows],
                            "customer": rows[0]["customer_name"], "quantity": quantity,
                            "points_removed": points, "tickets_removed": tickets,
                            "jackpot_positions_rewound": rewound,
                            "invalid_pack_claims_cancelled": cancelled_claims,
                            "city_run_reveals_removed": city_run_reveals_removed}), now))
        customer = self.get_customer(rows[0]["customer_name"])
        return {"customer": customer, "deal_name": rows[0]["deal_name"], "quantity": quantity,
                "revenue": revenue, "points_removed": points, "tickets_removed": tickets,
                "transaction_ids": [row["transaction_id"] for row in rows],
                "jackpot_positions_rewound": rewound,
                "invalid_pack_claims_cancelled": cancelled_claims,
                "city_run_reveals_removed": city_run_reveals_removed}

    def get_customer(self, name: str) -> dict[str, Any] | None:
        key = normalize_name(name)
        with self.connect() as conn:
            customer = conn.execute("SELECT * FROM customers WHERE customer_key = ?", (key,)).fetchone()
            if not customer:
                return None
            rewards = conn.execute(
                "SELECT * FROM rewards WHERE customer_key = ? AND status = 'unclaimed' ORDER BY id",
                (key,),
            ).fetchall()
            recent = conn.execute(
                "SELECT * FROM sales WHERE customer_key = ? AND voided = 0 ORDER BY id DESC LIMIT 5",
                (key,),
            ).fetchall()
            result = dict(customer)
            result["unclaimed_rewards"] = [dict(r) for r in rewards]
            result["recent_sales"] = [dict(r) for r in recent]
            finance = conn.execute(
                """SELECT COALESCE(SUM(production_cost), 0) AS production_cost,
                   COALESCE(SUM(price - production_cost), 0) AS gross_profit
                   FROM sales WHERE customer_key = ? AND voided = 0""",
                (key,),
            ).fetchone()
            result["production_cost"] = round(float(finance["production_cost"]), 2)
            result["gross_profit"] = round(float(finance["gross_profit"]), 2)
            result["profit_margin"] = round(
                (result["gross_profit"] / result["revenue"] * 100), 1
            ) if result["revenue"] else 0.0
            result["membership"] = self.membership(customer)
            return result

    def unclaimed_rewards(self, name: str) -> list[dict[str, Any]]:
        customer = self.get_customer(name)
        return customer["unclaimed_rewards"] if customer else []

    def claim_reward(self, reward_code: str, staff_id: str, staff_name: str) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            reward = conn.execute("SELECT * FROM rewards WHERE reward_code = ?", (reward_code,)).fetchone()
            if not reward:
                raise ValueError("Reward not found.")
            if reward["status"] != "unclaimed":
                raise ValueError(f"Reward is already {reward['status']}.")
            now = utc_now()
            conn.execute(
                "UPDATE rewards SET status = 'claimed', claimed_at = ?, claimed_by = ? WHERE reward_code = ?",
                (now, staff_name, reward_code),
            )
            conn.execute(
                "INSERT INTO audit_log (action, staff_id, staff_name, details, created_at) VALUES (?, ?, ?, ?, ?)",
                ("reward_claimed", str(staff_id), staff_name, json.dumps({"code": reward_code}), now),
            )
            return dict(conn.execute("SELECT * FROM rewards WHERE reward_code = ?", (reward_code,)).fetchone())

    def monthly_leaderboard(
        self, customer_name: str | None = None, limit: int = 10,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Rank real, non-voided sales for the current London calendar month."""
        london = ZoneInfo("Europe/London")
        current = now or datetime.now(london)
        if current.tzinfo is None:
            current = current.replace(tzinfo=london)
        else:
            current = current.astimezone(london)
        month_start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if month_start.month == 12:
            next_month = month_start.replace(year=month_start.year + 1, month=1)
        else:
            next_month = month_start.replace(month=month_start.month + 1)
        start_utc = month_start.astimezone(timezone.utc).isoformat(timespec="seconds")
        end_utc = next_month.astimezone(timezone.utc).isoformat(timespec="seconds")
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT s.customer_key, c.display_name, COUNT(*) AS purchases,
                          COALESCE(SUM(s.price),0) AS spend
                   FROM sales s JOIN customers c ON c.customer_key=s.customer_key
                   WHERE s.voided=0 AND c.leaderboard_excluded=0
                         AND s.created_at>=? AND s.created_at<?
                   GROUP BY s.customer_key, c.display_name
                   ORDER BY spend DESC, purchases DESC, c.display_name COLLATE NOCASE""",
                (start_utc, end_utc),
            ).fetchall()
        ranked = []
        for position, row in enumerate(rows, 1):
            item = dict(row)
            item["rank"] = position
            item["spend"] = int(item["spend"])
            item["purchases"] = int(item["purchases"])
            ranked.append(item)
        own = None
        if customer_name:
            wanted = normalize_name(customer_name)
            with self.connect() as conn:
                customer_row = conn.execute(
                    "SELECT display_name,leaderboard_excluded FROM customers WHERE customer_key=?",
                    (wanted,),
                ).fetchone()
            if customer_row and bool(customer_row["leaderboard_excluded"]):
                own = {"customer_key": wanted, "display_name": customer_row["display_name"],
                       "rank": 0, "spend": 0, "purchases": 0, "gap_to_next": 0,
                       "excluded": True}
            else:
                own = next((dict(row) for row in ranked if row["customer_key"] == wanted), None)
            if own is None:
                own = {"customer_key": wanted, "display_name": customer_name, "rank": len(ranked) + 1,
                       "spend": 0, "purchases": 0}
            if own.get("excluded"):
                pass
            elif own["rank"] == 1:
                own["gap_to_next"] = 0
            elif ranked:
                target = ranked[int(own["rank"]) - 2] if int(own["rank"]) <= len(ranked) else ranked[-1]
                own["gap_to_next"] = max(0, int(target["spend"]) - int(own["spend"]) + 1)
            else:
                own["gap_to_next"] = 0
        with self.connect() as conn:
            excluded_customers = int(conn.execute(
                "SELECT COUNT(*) AS count FROM customers WHERE leaderboard_excluded=1"
            ).fetchone()["count"])
        return {
            "period": current.strftime("%B %Y"),
            "days_left": max(0, (next_month.date() - current.date()).days),
            "rows": ranked[:max(1, min(int(limit), 25))],
            "own": own,
            "total_customers": len(ranked),
            "leader": ranked[0] if ranked else None,
            "excluded_customers": excluded_customers,
        }

    def report(self, days: int | None = None, today: bool = False) -> dict[str, Any]:
        where = "WHERE voided = 0"
        params: tuple[Any, ...] = ()
        if today:
            london_now = datetime.now(ZoneInfo("Europe/London"))
            london_start = london_now.replace(hour=0, minute=0, second=0, microsecond=0)
            utc_start = london_start.astimezone(timezone.utc).isoformat(timespec="seconds")
            utc_end = london_now.astimezone(timezone.utc).isoformat(timespec="seconds")
            where += " AND created_at >= ? AND created_at <= ?"
            params = (utc_start, utc_end)
        elif days:
            where += " AND datetime(created_at) >= datetime('now', ?)"
            params = (f"-{int(days)} days",)
        with self.connect() as conn:
            total = conn.execute(
                f"""SELECT COUNT(*) AS sales, COALESCE(SUM(price),0) AS revenue,
                    COALESCE(SUM(production_cost),0) AS production_cost,
                    COALESCE(SUM(price - production_cost),0) AS gross_profit,
                    COALESCE(SUM(food),0) AS food, COALESCE(SUM(drinks),0) AS drinks,
                    COALESCE(SUM(loyalty_points),0) AS loyalty FROM sales {where}""",
                params,
            ).fetchone()
            deals = conn.execute(
                f"""SELECT deal_name, COUNT(*) AS quantity, SUM(price) AS revenue,
                    SUM(production_cost) AS production_cost,
                    SUM(price - production_cost) AS gross_profit
                    FROM sales {where} GROUP BY deal_key, deal_name ORDER BY quantity DESC, revenue DESC""",
                params,
            ).fetchall()
            result = dict(total)
            result["production_cost"] = round(float(result["production_cost"]), 2)
            result["gross_profit"] = round(float(result["gross_profit"]), 2)
            result["profit_margin"] = round(
                (result["gross_profit"] / result["revenue"] * 100), 1
            ) if result["revenue"] else 0.0
            deal_rows = []
            for row in deals:
                deal_row = dict(row)
                deal_row["production_cost"] = round(float(deal_row["production_cost"]), 2)
                deal_row["gross_profit"] = round(float(deal_row["gross_profit"]), 2)
                deal_row["profit_margin"] = round(
                    (deal_row["gross_profit"] / deal_row["revenue"] * 100), 1
                ) if deal_row["revenue"] else 0.0
                deal_rows.append(deal_row)
            result["deals"] = deal_rows
            return result

    def import_legacy_json(self, legacy_path: str) -> dict[str, int]:
        path = Path(legacy_path)
        if not path.exists():
            return {"imported": 0, "skipped": 0}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"imported": 0, "skipped": 0}
        imported = 0
        skipped = 0
        with self.connect() as conn:
            existing = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
            previous = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action = 'legacy_import'"
            ).fetchone()[0]
            if existing or previous:
                return {"imported": 0, "skipped": len(data.get("customers", {}))}
            now = utc_now()
            for old_key, old in data.get("customers", {}).items():
                name = str(old.get("name") or old_key.replace("name:", "")).strip()
                key = normalize_name(name)
                if not key:
                    skipped += 1
                    continue
                conn.execute(
                    """INSERT OR IGNORE INTO customers
                       (customer_key, display_name, loyalty_points, lifetime_sales,
                        card_packs_earned, card_packs_claimed, revenue, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        key, display_name(name), int(old.get("points", 0)),
                        int(old.get("lifetime_purchases", 0)),
                        int(old.get("packs_given", old.get("rewards_redeemed", 0))),
                        int(old.get("packs_given", old.get("rewards_redeemed", 0))),
                        int(float(old.get("revenue", 0))), now, now,
                    ),
                )
                imported += 1
            conn.execute(
                "INSERT INTO audit_log (action, details, created_at) VALUES ('legacy_import', ?, ?)",
                (json.dumps({"source": str(path), "imported": imported, "skipped": skipped}), now),
            )
        return {"imported": imported, "skipped": skipped}


def birdy_post(kind: str, deal_key: str | None = None, winner: str | None = None) -> str:
    if kind == "deal":
        if not deal_key or deal_key not in DEALS:
            raise ValueError("A valid deal is required.")
        d = DEALS[deal_key]
        reward_lines = []
        if d.loyalty_points:
            sticker_word = "sticker" if d.loyalty_points == 1 else "stickers"
            reward_lines.append(f"🏁 {d.loyalty_points} City Run {sticker_word}")
        return (
            "🍔 SNR BUNS IS OPEN! 🍔\n\n"
            f"🔥 {d.name.upper()} 🔥\n"
            f"{d.item_summary.upper()}\n"
            f"ONLY £{d.price:,}\n\n"
            f"{'\n'.join(reward_lines)}\n\n"
            "Head down to SNR Buns or call us to order."
        )
    if kind == "open":
        return (
            "🍔🔥 SNR BUNS IS OPEN! 🔥🍔\n\n"
            "Fresh food, cold drinks and proper deals are ready now.\n\n"
            "Qualifying meals earn City Run stickers for your collection. 🏁\n\n"
            "Hungry? Head down or call SNR Buns to order!"
        )
    if kind == "loyalty":
        return (
            "🏁🍔 SNR CITY RUN IS COMING 🍔🏁\n\n"
            "Purchase SNR meal deals to collect City Run stickers. Every sticker gives you one secure "
            "digital business reveal on the SNR City Run board.\n\n"
            "Complete business routes for RP food, cash and VIP rewards—and collect all 38 businesses "
            "for a chance to claim the grand-prize vehicle.\n\n"
            "Log in to your SNR account to follow your collection."
        )
    if kind == "delivery":
        return (
            "🚗🍔 SNR BUNS DELIVERIES ARE AVAILABLE! 🍔🚗\n\n"
            "Hungry but can’t get to the restaurant? Log in through the SNR Buns customer webpage, choose your deal and enter your postal.\n\n"
            "Fresh food • Cold drinks • Fast service\n\n"
            "Pay on delivery — your City Run stickers update after staff confirm payment."
        )
    if kind == "catering":
        return (
            "🎉🍔 NEED FOOD FOR YOUR EVENT? 🍔🎉\n\n"
            "SNR Buns offers catering for parties, car meets, business events and celebrations around the city.\n\n"
            "Contact us through Birdy or call SNR Buns to discuss your event!"
        )
    if kind == "hiring":
        return (
            "🍔📋 SNR BUNS IS HIRING! 📋🍔\n\n"
            "We’re looking for reliable, active and professional people to join the team.\n\n"
            "Experience is preferred. Contact SNR Buns to apply!"
        )
    raise ValueError("Unknown Birdy post type.")
