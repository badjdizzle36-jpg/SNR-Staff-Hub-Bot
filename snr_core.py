from __future__ import annotations

import difflib
import json
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


JACKPOT_POOL_SIZE = 1000

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
    "quick_fix": Deal("quick_fix", "SNR Quick Fix", 150, 1, 1, 0, 1),
    "happy_meal": Deal("happy_meal", "SNR Happy Meal", 300, 2, 2, 0, 1),
    "sweet_treat": Deal("sweet_treat", "SNR Sweet Treat Deal", 400, 5, 0, 0, 1, "5 desserts"),
    "mega_deal": Deal("mega_deal", "SNR Mega Deal", 500, 4, 4, 1, 1),
    "blue_light": Deal("blue_light", "SNR Blue Light Deal", 600, 8, 8, 0, 1),
    "share_box": Deal("share_box", "SNR Share Box", 1200, 10, 10, 2, 4),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_name(name: str) -> str:
    return " ".join(name.strip().casefold().split())


def display_name(name: str) -> str:
    return " ".join(part.capitalize() for part in name.strip().split())


class SNRDatabase:
    def __init__(self, path: str, jackpot_pool_size: int = JACKPOT_POOL_SIZE):
        self.path = path
        self.jackpot_pool_size = max(10, jackpot_pool_size)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

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
            row = conn.execute("SELECT * FROM jackpot WHERE id = 1").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO jackpot VALUES (1, 1, ?, ?, 0, NULL, NULL)",
                    (self.jackpot_pool_size, random.SystemRandom().randint(1, self.jackpot_pool_size)),
                )
            elif int(row["pool_size"]) != self.jackpot_pool_size:
                # Apply a changed jackpot rarity safely, even to an existing database.
                issued = int(row["tickets_issued"])
                cycle = int(row["cycle"])
                if issued >= self.jackpot_pool_size:
                    cycle += 1
                    issued = 0
                winning_position = random.SystemRandom().randint(issued + 1, self.jackpot_pool_size)
                conn.execute(
                    """UPDATE jackpot SET cycle = ?, pool_size = ?, winning_position = ?,
                       tickets_issued = ? WHERE id = 1""",
                    (cycle, self.jackpot_pool_size, winning_position, issued),
                )

            # Trading-card giveaways have been retired. Historical rows remain
            # for audit purposes, but no unclaimed card reward can be redeemed.
            conn.execute(
                """UPDATE rewards SET status = 'cancelled', cancelled_at = ?,
                   cancelled_by = 'system: trading-card rewards retired'
                   WHERE reward_type = 'card_pack' AND status = 'unclaimed'""",
                (utc_now(),),
            )

    def customer_names(self) -> list[str]:
        with self.connect() as conn:
            return [r["display_name"] for r in conn.execute("SELECT display_name FROM customers")]

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

    def record_sale(self, customer_name: str, deal_key: str, staff_id: str, staff_name: str) -> dict[str, Any]:
        if deal_key not in DEALS:
            raise ValueError("Unknown deal selected.")
        deal = DEALS[deal_key]
        now = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            customer = self._ensure_customer(conn, customer_name)
            key = customer["customer_key"]
            shown = customer["display_name"]

            new_points_total = int(customer["loyalty_points"]) + deal.loyalty_points

            cursor = conn.execute(
                """INSERT INTO sales
                   (customer_key, customer_name, deal_key, deal_name, price, food, drinks, production_cost,
                    loyalty_points, golden_tickets, card_rewards_created, staff_id, staff_name, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    key, shown, deal.key, deal.name, deal.price, deal.food, deal.drinks,
                    deal.production_cost, deal.loyalty_points, deal.golden_tickets, 0,
                    str(staff_id), staff_name, now,
                ),
            )
            sale_id = int(cursor.lastrowid)
            transaction_id = self._transaction_id(sale_id)
            conn.execute("UPDATE sales SET transaction_id = ? WHERE id = ?", (transaction_id, sale_id))

            jackpot = conn.execute("SELECT * FROM jackpot WHERE id = 1").fetchone()
            winning_ticket = None
            ticket_positions: list[str] = []
            for _ in range(deal.golden_tickets):
                position = int(jackpot["tickets_issued"]) + 1
                ticket_positions.append(f"{jackpot['cycle']}-{position:04d}")
                if position == int(jackpot["winning_position"]):
                    winning_ticket = ticket_positions[-1]
                    break
                conn.execute("UPDATE jackpot SET tickets_issued = ? WHERE id = 1", (position,))
                jackpot = conn.execute("SELECT * FROM jackpot WHERE id = 1").fetchone()

            jackpot_code = None
            if winning_ticket:
                jackpot_code = self._create_reward(
                    conn, key, shown, "golden_jackpot",
                    "SNR Golden Mystery Ticket: £5,000 cash jackpot", sale_id,
                )
                old_cycle = int(jackpot["cycle"])
                next_cycle = old_cycle + 1
                next_winner = random.SystemRandom().randint(1, self.jackpot_pool_size)
                conn.execute(
                    """UPDATE jackpot SET cycle = ?, pool_size = ?, winning_position = ?,
                       tickets_issued = 0, last_winner = ?, last_won_at = ? WHERE id = 1""",
                    (next_cycle, self.jackpot_pool_size, next_winner, shown, now),
                )
                conn.execute("UPDATE sales SET jackpot_won = 1 WHERE id = ?", (sale_id,))

            conn.execute(
                """UPDATE customers SET loyalty_points = ?, lifetime_sales = lifetime_sales + 1,
                   card_packs_earned = card_packs_earned + ?, golden_tickets = golden_tickets + ?,
                   jackpot_wins = jackpot_wins + ?, revenue = revenue + ?,
                   food_sold = food_sold + ?, drinks_sold = drinks_sold + ?, updated_at = ?
                   WHERE customer_key = ?""",
                (
                    new_points_total, 0, deal.golden_tickets, 1 if winning_ticket else 0,
                    deal.price, deal.food, deal.drinks, now, key,
                ),
            )
            conn.execute(
                "INSERT INTO audit_log (action, staff_id, staff_name, details, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    "sale_recorded", str(staff_id), staff_name,
                    json.dumps({"transaction_id": transaction_id, "customer": shown, "deal": deal.key}), now,
                ),
            )
            updated = conn.execute("SELECT * FROM customers WHERE customer_key = ?", (key,)).fetchone()
            current_jackpot = conn.execute("SELECT * FROM jackpot WHERE id = 1").fetchone()

        return {
            "transaction_id": transaction_id,
            "customer": dict(updated),
            "deal": deal,
            "card_reward_codes": [],
            "jackpot_won": bool(winning_ticket),
            "jackpot_reward_code": jackpot_code,
            "winning_ticket": winning_ticket,
            "ticket_positions": ticket_positions,
            "jackpot_cycle": int(current_jackpot["cycle"]),
            "tickets_issued_in_cycle": int(current_jackpot["tickets_issued"]),
        }

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

    def jackpot_status(self) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jackpot WHERE id = 1").fetchone()
            winners = conn.execute(
                "SELECT COUNT(*) AS count FROM rewards WHERE reward_type = 'golden_jackpot'"
            ).fetchone()["count"]
            result = dict(row)
            result["total_winners"] = int(winners)
            return result

    def latest_jackpot_winner(self) -> str | None:
        return self.jackpot_status().get("last_winner")

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
                    COALESCE(SUM(loyalty_points),0) AS loyalty,
                    COALESCE(SUM(golden_tickets),0) AS tickets,
                    COALESCE(SUM(jackpot_won),0) AS jackpots FROM sales {where}""",
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
            point_word = "point" if d.loyalty_points == 1 else "points"
            reward_lines.append(f"⭐ {d.loyalty_points} loyalty {point_word}")
        ticket_word = "ticket" if d.golden_tickets == 1 else "tickets"
        reward_lines.append(f"🎟️ {d.golden_tickets} Golden {ticket_word}")
        return (
            "🍔 SNR BUNS IS OPEN! 🍔\n\n"
            f"🔥 {d.name.upper()} 🔥\n"
            f"{d.item_summary.upper()}\n"
            f"ONLY £{d.price:,}\n\n"
            f"{'\n'.join(reward_lines)}\n\n"
            "The extremely rare £5,000 cash jackpot is still waiting to be found!\n"
            "Head down to SNR Buns or call us to order."
        )
    if kind == "open":
        return (
            "🍔🔥 SNR BUNS IS OPEN! 🔥🍔\n\n"
            "Fresh food, cold drinks and proper deals are ready now.\n\n"
            "Every qualifying deal gives you a chance to uncover the SNR Golden Ticket Jackpot! 🎟️🏆\n\n"
            "Hungry? Head down or call SNR Buns to order!"
        )
    if kind == "jackpot":
        return (
            "🎟️🏆 THE SNR GOLDEN MYSTERY TICKET IS OUT THERE! 🏆🎟️\n\n"
            "Purchase a qualifying SNR Buns deal for a chance to uncover our rarest ticket!\n\n"
            "💰 WIN £5,000 CASH 💰\n\n"
            "Only one Golden Mystery Ticket is hidden among 1,000 tickets.\n"
            "Could the rare jackpot be inside your next order?"
        )
    if kind == "winner":
        if not winner:
            raise ValueError("There is no recorded Golden Ticket winner yet.")
        return (
            "🏆🎟️ THE GOLDEN MYSTERY TICKET HAS BEEN FOUND! 🎟️🏆\n\n"
            f"Congratulations to {winner}, winner of the £5,000 CASH JACKPOT! 💰\n\n"
            "A brand-new extremely rare Golden Mystery Ticket is now hidden.\n"
            "Will you be our next winner? 🍔🔥"
        )
    if kind == "loyalty":
        return (
            "⭐🍔 SNR BUNS LOYALTY REWARDS 🍔⭐\n\n"
            "Purchase our Mega Deal or Share Box to collect loyalty points.\n\n"
            "Your points are saved under your character name and continue building every time you visit.\n\n"
            "Head down to SNR Buns and keep growing your loyalty balance!"
        )
    if kind == "delivery":
        return (
            "🚗🍔 SNR BUNS DELIVERIES ARE AVAILABLE! 🍔🚗\n\n"
            "Hungry but can’t get to the restaurant? Call SNR Buns and let our team bring the food to you.\n\n"
            "Fresh food • Cold drinks • Fast service"
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
