"""SNR City Run collect-to-win campaign shared by sales, web and Discord."""
from __future__ import annotations

import json
import secrets
from collections import OrderedDict
from typing import Any


COLLECTIONS: "OrderedDict[str, dict[str, Any]]" = OrderedDict([
    ("food", {"name": "Food & Cafes", "colour": "#ff3b32", "businesses": [
        "SNR Buns Restaurant", "Kebab King Restaurant", "Casey's Diner", "Pizza This", "UwU Cafe",
    ]}),
    ("nightlife", {"name": "Nightlife", "colour": "#d95cff", "businesses": [
        "Bahamamamas", "Three Hawkers Pub", "Tropical Heights Nightclub", "Club 77 Nightclub",
        "Tequi-la-la Bar",
    ]}),
    ("mechanics", {"name": "Mechanics & Recovery", "colour": "#34a8ff", "businesses": [
        "Benny's Customs Mechanics", "Titan Recovery & Parts", "Cruzin Mechanics",
        "Tuning Bar Mechanics", "Route 68 Mechanics", "Bobs Big Tow",
    ]}),
    ("motors", {"name": "Motors & Transport", "colour": "#ffd23f", "businesses": [
        "Perlah Motorcycle Dealer", "Route 68 Car Dealer", "Unmatched Taxis", "GoPostal",
    ]}),
    ("shops", {"name": "Shops", "colour": "#45d97b", "businesses": [
        "Pawn Shop", "Route 68 Pawn", "UM Comic Store", "Pharmacy", "Jeweled Dragon",
    ]}),
    ("luxury", {"name": "Luxury & Leisure", "colour": "#f4c969", "businesses": [
        "Vankov Jewellery", "Diamond Casino", "Pearls Resort",
    ]}),
    ("finance", {"name": "Finance & Property", "colour": "#20d6d0", "businesses": [
        "Dynasty8 Real Estate", "MBU Finance Group", "Unmatched Vehicle Finance", "Vercotti Group",
    ]}),
    ("services", {"name": "Public Services", "colour": "#d8e2f0", "businesses": [
        "Unmatched Police Department", "Unmatched Health Service", "Unmatched Fire Brigade",
        "Unmatched Search & Rescue", "Unmatched Court Services", "Unmatched City Hall",
    ]}),
])

BUSINESSES = tuple(
    {"key": f"{collection_key}-{position}", "name": business_name,
     "collection_key": collection_key, "collection_name": details["name"],
     "colour": details["colour"], "position": position}
    for collection_key, details in COLLECTIONS.items()
    for position, business_name in enumerate(details["businesses"], 1)
)

RARITY_WEIGHTS = {"common": 1000, "rare": 45, "ultra_rare": 1}
# Once a customer owns a business, it remains in the draw at a higher weight.
# This keeps duplicates meaningful and makes a full 38-business collection a
# genuine long-term chase rather than something completed in a few reveals.
DUPLICATE_WEIGHT_MULTIPLIER = 5
REWARD_KEYS = tuple(COLLECTIONS) + ("grand",)
APPROVED_REWARDS = {
    "food": ("Free Quick Fix", "Complete Food & Cafes and claim one free SNR Quick Fix.", "food", None, 20),
    "nightlife": ("Free Mega Deal", "Complete Nightlife and claim one free SNR Mega Deal.", "food", None, 15),
    "mechanics": ("Free Share Box", "Complete Mechanics & Recovery and claim one free SNR Share Box.", "food", None, 10),
    "motors": ("GBP 1,000 RP Cash", "Complete Motors & Transport and claim GBP 1,000 RP cash.", "cash", 1000, 8),
    "shops": ("GBP 2,500 RP Cash", "Complete Shops and claim GBP 2,500 RP cash.", "cash", 2500, 5),
    "luxury": ("GBP 5,000 RP Cash", "Complete Luxury & Leisure and claim GBP 5,000 RP cash.", "cash", 5000, 3),
    "finance": ("One Month SNR VIP", "Complete Finance & Property and receive one month of SNR VIP.", "vip", 1, 3),
    "services": ("GBP 10,000 RP Cash", "Complete Public Services and claim GBP 10,000 RP cash.", "cash", 10000, 1),
    "grand": ("Grand-Prize Vehicle", "Collect all 38 businesses and claim the season grand-prize vehicle.", "vehicle", 1, 1),
}

RECOMMENDED_RARE_PIECES = {
    "food-1": ("ultra_rare", 20),           # SNR Buns Restaurant; stock unchanged
    "nightlife-1": ("rare", 15),           # Bahamamamas
    "mechanics-1": ("rare", 10),           # Benny's Customs
    "motors-2": ("rare", 8),               # Route 68 Car Dealer
    "shops-5": ("rare", 5),                # Jeweled Dragon
    "luxury-2": ("rare", 3),               # Diamond Casino
    "finance-2": ("rare", 3),              # MBU Finance Group
    "services-6": ("ultra_rare", 1),        # Unmatched City Hall
}


def ensure_city_run_schema(conn, now: str) -> None:
    """Create and safely seed the campaign without making it live."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS city_run_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT UNIQUE NOT NULL,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL,
            message_id TEXT
        );
        CREATE TABLE IF NOT EXISTS city_run_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            stickers_per_pack INTEGER NOT NULL DEFAULT 2,
            packs_per_sale INTEGER NOT NULL DEFAULT 1,
            starts_at TEXT,
            ends_at TEXT,
            created_at TEXT NOT NULL,
            created_by TEXT,
            activated_at TEXT,
            ended_at TEXT
        );
        CREATE TABLE IF NOT EXISTS city_run_businesses (
            business_key TEXT PRIMARY KEY,
            business_name TEXT NOT NULL UNIQUE,
            collection_key TEXT NOT NULL,
            collection_name TEXT NOT NULL,
            colour TEXT NOT NULL,
            position INTEGER NOT NULL,
            image_key TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS city_run_inventory (
            campaign_id INTEGER NOT NULL REFERENCES city_run_campaigns(id),
            business_key TEXT NOT NULL REFERENCES city_run_businesses(business_key),
            rarity TEXT NOT NULL DEFAULT 'unassigned',
            total_available INTEGER,
            issued_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(campaign_id,business_key)
        );
        CREATE TABLE IF NOT EXISTS city_run_rewards (
            campaign_id INTEGER NOT NULL REFERENCES city_run_campaigns(id),
            reward_key TEXT NOT NULL,
            reward_name TEXT NOT NULL,
            reward_description TEXT NOT NULL,
            reward_type TEXT NOT NULL,
            reward_amount INTEGER,
            stock_limit INTEGER,
            claims_fulfilled INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(campaign_id,reward_key)
        );
        CREATE TABLE IF NOT EXISTS city_run_packs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL REFERENCES city_run_campaigns(id),
            customer_key TEXT NOT NULL REFERENCES customers(customer_key),
            customer_name TEXT NOT NULL,
            sale_id INTEGER NOT NULL UNIQUE REFERENCES sales(id),
            status TEXT NOT NULL DEFAULT 'unopened',
            issued_at TEXT NOT NULL,
            opened_at TEXT,
            voided_at TEXT,
            voided_by TEXT
        );
        CREATE TABLE IF NOT EXISTS city_run_pack_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pack_id INTEGER NOT NULL REFERENCES city_run_packs(id),
            business_key TEXT NOT NULL REFERENCES city_run_businesses(business_key),
            slot INTEGER NOT NULL,
            UNIQUE(pack_id,slot)
        );
        CREATE TABLE IF NOT EXISTS city_run_customer_cards (
            campaign_id INTEGER NOT NULL REFERENCES city_run_campaigns(id),
            customer_key TEXT NOT NULL REFERENCES customers(customer_key),
            business_key TEXT NOT NULL REFERENCES city_run_businesses(business_key),
            copies_owned INTEGER NOT NULL DEFAULT 0,
            first_collected_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(campaign_id,customer_key,business_key)
        );
        CREATE TABLE IF NOT EXISTS city_run_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL REFERENCES city_run_campaigns(id),
            customer_key TEXT NOT NULL REFERENCES customers(customer_key),
            customer_name TEXT NOT NULL,
            reward_key TEXT NOT NULL,
            reward_name TEXT NOT NULL,
            reward_description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            requested_at TEXT NOT NULL,
            resolved_at TEXT,
            resolved_by TEXT,
            resolved_by_name TEXT,
            channel_id TEXT,
            guild_id TEXT,
            message_id TEXT,
            UNIQUE(campaign_id,customer_key,reward_key)
        );
        CREATE TABLE IF NOT EXISTS city_run_tokens (
            campaign_id INTEGER NOT NULL REFERENCES city_run_campaigns(id),
            customer_key TEXT NOT NULL REFERENCES customers(customer_key),
            customer_name TEXT NOT NULL,
            available INTEGER NOT NULL DEFAULT 0,
            lifetime_earned INTEGER NOT NULL DEFAULT 0,
            lifetime_used INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(campaign_id,customer_key)
        );
        CREATE TABLE IF NOT EXISTS city_run_token_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL REFERENCES city_run_campaigns(id),
            customer_key TEXT NOT NULL REFERENCES customers(customer_key),
            customer_name TEXT NOT NULL,
            source_key TEXT NOT NULL UNIQUE,
            amount INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS city_run_reveals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL REFERENCES city_run_campaigns(id),
            customer_key TEXT NOT NULL REFERENCES customers(customer_key),
            business_key TEXT NOT NULL REFERENCES city_run_businesses(business_key),
            duplicate INTEGER NOT NULL DEFAULT 0,
            request_key TEXT,
            revealed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS city_run_corner_awards (
            campaign_id INTEGER NOT NULL REFERENCES city_run_campaigns(id),
            customer_key TEXT NOT NULL,
            corner_key TEXT NOT NULL,
            bonus_reveals INTEGER NOT NULL DEFAULT 0,
            awarded_at TEXT NOT NULL,
            PRIMARY KEY(campaign_id, customer_key, corner_key)
        );
        CREATE INDEX IF NOT EXISTS city_run_pack_owner
            ON city_run_packs(campaign_id,customer_key,status);
        CREATE INDEX IF NOT EXISTS city_run_claim_status
            ON city_run_claims(status,guild_id,message_id);
    """)
    reveal_columns = {row["name"] for row in conn.execute("PRAGMA table_info(city_run_reveals)")}
    if "request_key" not in reveal_columns:
        conn.execute("ALTER TABLE city_run_reveals ADD COLUMN request_key TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS city_run_reveal_request ON city_run_reveals(request_key)"
    )
    campaign = conn.execute(
        "SELECT * FROM city_run_campaigns WHERE status IN ('draft','active','paused') ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if campaign is None:
        cursor = conn.execute(
            "INSERT INTO city_run_campaigns(title,status,created_at) VALUES('SNR City Run','draft',?)", (now,)
        )
        campaign_id = int(cursor.lastrowid)
    else:
        campaign_id = int(campaign["id"])
    for business in BUSINESSES:
        conn.execute(
            """INSERT INTO city_run_businesses
               (business_key,business_name,collection_key,collection_name,colour,position,image_key)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(business_key) DO UPDATE SET business_name=excluded.business_name,
               collection_key=excluded.collection_key,collection_name=excluded.collection_name,
               colour=excluded.colour,position=excluded.position,image_key=excluded.image_key""",
            (business["key"], business["name"], business["collection_key"],
             business["collection_name"], business["colour"], business["position"], business["key"]),
        )
        conn.execute(
            "INSERT OR IGNORE INTO city_run_inventory(campaign_id,business_key) VALUES(?,?)",
            (campaign_id, business["key"]),
        )
    for reward_key, values in APPROVED_REWARDS.items():
        conn.execute(
            """INSERT OR IGNORE INTO city_run_rewards
               (campaign_id,reward_key,reward_name,reward_description,reward_type,reward_amount,stock_limit)
               VALUES(?,?,?,?,?,?,?)""",
            (campaign_id, reward_key, *values),
        )
    migrate_snr_rarity(conn, now)


def migrate_snr_rarity(conn, now):
    """One-time approved change; never reset ownership, issued stock or rewards."""
    conn.execute("""CREATE TABLE IF NOT EXISTS city_run_migrations
                    (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)""")
    marker = "snr_ultra_uwu_common_v1"
    if conn.execute("SELECT 1 FROM city_run_migrations WHERE name=?", (marker,)).fetchone():
        return
    rows = conn.execute("""SELECT i.* FROM city_run_inventory i
        JOIN city_run_campaigns c ON c.id=i.campaign_id
        WHERE c.status IN ('draft','active','paused')
        AND i.business_key IN ('food-1','food-5') AND i.rarity != 'unassigned'""").fetchall()
    changes = []
    for row in rows:
        rarity = 'ultra_rare' if row['business_key'] == 'food-1' else 'common'
        # Common pieces have no stock cap. Retain SNR's existing cap, including
        # an exhausted cap; do not mint new stock by lowering issued_count.
        cap = row['total_available'] if row['business_key'] == 'food-1' else None
        if row['rarity'] == rarity and row['total_available'] == cap:
            continue
        conn.execute("""UPDATE city_run_inventory SET rarity=?,total_available=?
            WHERE campaign_id=? AND business_key=?""",
            (rarity, cap, row['campaign_id'], row['business_key']))
        changes.append({'before': dict(row), 'rarity': rarity, 'total_available': cap})
    conn.execute("INSERT INTO city_run_migrations VALUES(?,?)", (marker, now))
    conn.execute("INSERT INTO audit_log(action,details,created_at) VALUES(?,?,?)",
                 (marker, json.dumps(changes), now))


def _weighted_choice(rows, owned_keys: set[str] | None = None) -> Any:
    owned_keys = owned_keys or set()
    total = sum(
        RARITY_WEIGHTS.get(row["rarity"], 0)
        * (DUPLICATE_WEIGHT_MULTIPLIER if row["business_key"] in owned_keys else 1)
        for row in rows
    )
    if total <= 0:
        raise RuntimeError("City Run cannot issue packs until its collectible rarities are configured.")
    pick = secrets.randbelow(total)
    for row in rows:
        weight = RARITY_WEIGHTS.get(row["rarity"], 0)
        if row["business_key"] in owned_keys:
            weight *= DUPLICATE_WEIGHT_MULTIPLIER
        if pick < weight:
            return row
        pick -= weight
    return rows[-1]


def queue_event(conn, event_key, description, now):
    conn.execute('INSERT OR IGNORE INTO city_run_events(event_key,description,created_at) VALUES(?,?,?)',
                 (event_key, description, now))


def queue_completions(conn, campaign_id, customer_key, now):
    """Called inside the award transaction, not during a sale or network send."""
    owned = {r['business_key'] for r in conn.execute(
        'SELECT business_key FROM city_run_customer_cards WHERE campaign_id=? AND customer_key=? AND copies_owned>0',
        (campaign_id, customer_key))}
    customer = conn.execute('SELECT display_name FROM customers WHERE customer_key=?', (customer_key,)).fetchone()
    name = customer['display_name'] if customer else customer_key
    for route, data in COLLECTIONS.items():
        keys = {b['key'] for b in BUSINESSES if b['collection_key'] == route}
        if keys <= owned:
            reward = conn.execute('SELECT reward_name FROM city_run_rewards WHERE campaign_id=? AND reward_key=?', (campaign_id, route)).fetchone()
            queue_event(conn, f'set:{campaign_id}:{customer_key}:{route}',
                        f"{name} completed {data['name']}. Unlocked: {reward['reward_name'] if reward else 'route complete'}. Staff handover is still required.", now)
    if {b['key'] for b in BUSINESSES} <= owned:
        queue_event(conn, f'grand:{campaign_id}:{customer_key}', f'{name} collected all 38 businesses. Grand-prize claim unlocked; not yet handed over.', now)


def issue_pack_for_sale(conn, sale_id: int, customer_key: str, customer_name: str, now: str) -> int | None:
    """Atomically pre-assign one sealed pack for an eligible paid sale."""
    campaign = conn.execute(
        "SELECT * FROM city_run_campaigns WHERE status='active' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not campaign:
        return None
    existing = conn.execute("SELECT id FROM city_run_packs WHERE sale_id=?", (int(sale_id),)).fetchone()
    if existing:
        return int(existing["id"])
    unassigned = conn.execute(
        "SELECT COUNT(*) AS count FROM city_run_inventory WHERE campaign_id=? AND rarity='unassigned'",
        (campaign["id"],),
    ).fetchone()["count"]
    if int(unassigned):
        raise RuntimeError("City Run is active but its rarity setup is incomplete.")
    cursor = conn.execute(
        """INSERT INTO city_run_packs
           (campaign_id,customer_key,customer_name,sale_id,status,issued_at)
           VALUES(?,?,?,?,'unopened',?)""",
        (campaign["id"], customer_key, customer_name, int(sale_id), now),
    )
    pack_id = int(cursor.lastrowid)
    selected: set[str] = set()
    for slot in range(1, int(campaign["stickers_per_pack"]) + 1):
        rows = conn.execute(
            """SELECT i.business_key,i.rarity,i.total_available,i.issued_count
               FROM city_run_inventory i JOIN city_run_businesses b USING(business_key)
               WHERE i.campaign_id=? AND b.active=1 AND i.rarity!='unassigned'
               AND (i.total_available IS NULL OR i.issued_count<i.total_available)
               ORDER BY i.business_key""",
            (campaign["id"],),
        ).fetchall()
        unique_rows = [row for row in rows if row["business_key"] not in selected]
        chosen = _weighted_choice(unique_rows or rows)
        selected.add(chosen["business_key"])
        conn.execute(
            "INSERT INTO city_run_pack_items(pack_id,business_key,slot) VALUES(?,?,?)",
            (pack_id, chosen["business_key"], slot),
        )
        conn.execute(
            """UPDATE city_run_inventory SET issued_count=issued_count+1
               WHERE campaign_id=? AND business_key=?""",
            (campaign["id"], chosen["business_key"]),
        )
    conn.execute(
        "INSERT INTO audit_log(action,details,created_at) VALUES('city_run_pack_issued',?,?)",
        (json.dumps({"pack_id": pack_id, "sale_id": int(sale_id), "customer": customer_name}), now),
    )
    return pack_id


def award_tokens_for_sale(conn, sale_id: int, customer_key: str, customer_name: str,
                          points: int, now: str) -> int:
    """Turn earned loyalty points into idempotent digital City Run reveals."""
    amount = max(0, int(points))
    if amount == 0:
        return 0
    campaign = conn.execute(
        "SELECT * FROM city_run_campaigns WHERE status='active' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not campaign:
        return 0
    source_key = f"sale:{int(sale_id)}"
    previous = conn.execute(
        "SELECT amount FROM city_run_token_ledger WHERE source_key=?", (source_key,)
    ).fetchone()
    if previous:
        return int(previous["amount"])
    conn.execute(
        """INSERT INTO city_run_token_ledger
           (campaign_id,customer_key,customer_name,source_key,amount,created_at)
           VALUES(?,?,?,?,?,?)""",
        (campaign["id"], customer_key, customer_name, source_key, amount, now),
    )
    conn.execute(
        """INSERT INTO city_run_tokens
           (campaign_id,customer_key,customer_name,available,lifetime_earned,lifetime_used,updated_at)
           VALUES(?,?,?,?,?,0,?)
           ON CONFLICT(campaign_id,customer_key) DO UPDATE SET
           available=available+excluded.available,
           lifetime_earned=lifetime_earned+excluded.lifetime_earned,
           customer_name=excluded.customer_name,updated_at=excluded.updated_at""",
        (campaign["id"], customer_key, customer_name, amount, amount, now),
    )
    conn.execute(
        "INSERT INTO audit_log(action,details,created_at) VALUES('city_run_points_awarded',?,?)",
        (json.dumps({"sale_id": int(sale_id), "customer": customer_name, "reveals": amount}), now),
    )
    return amount


CORNER_RULES = (
    {"key": "start", "name": "Start Your Run", "threshold": 1, "bonus": 0,
     "description": "Your first qualifying meal starts your City Run board."},
    {"key": "route_pack", "name": "Open a Route Pack", "threshold": 8, "bonus": 2,
     "description": "Collect 8 different businesses to unlock two bonus reveals."},
    {"key": "pit_stop", "name": "Pit Stop Bonus", "threshold": 18, "bonus": 3,
     "description": "Collect 18 different businesses to unlock three bonus reveals."},
    {"key": "garage", "name": "Grand Prize Garage", "threshold": 38, "bonus": 0,
     "description": "Collect all 38 businesses to unlock the grand-prize vehicle claim."},
)


def reverse_tokens_for_sales(conn, sale_ids: list[int], now: str, staff_id: str) -> int:
    """Reverse reveal credits when an owner safely reverses the source sale."""
    if not sale_ids:
        return 0
    source_keys = [f"sale:{int(sale_id)}" for sale_id in sale_ids]
    marks = ",".join("?" for _ in source_keys)
    rows = conn.execute(
        f"SELECT * FROM city_run_token_ledger WHERE source_key IN ({marks})", source_keys
    ).fetchall()
    if not rows:
        return 0
    grouped: dict[tuple[int, str], int] = {}
    for row in rows:
        key = (int(row["campaign_id"]), row["customer_key"])
        grouped[key] = grouped.get(key, 0) + int(row["amount"])
    for (campaign_id, customer_key), amount in grouped.items():
        balance = conn.execute(
            "SELECT available FROM city_run_tokens WHERE campaign_id=? AND customer_key=?",
            (campaign_id, customer_key),
        ).fetchone()
        if not balance or int(balance["available"]) < amount:
            raise ValueError(
                "This sale cannot be undone because some of its City Run reveals have already been used."
            )
    for (campaign_id, customer_key), amount in grouped.items():
        conn.execute(
            """UPDATE city_run_tokens SET available=available-?,lifetime_earned=MAX(0,lifetime_earned-?),
               updated_at=? WHERE campaign_id=? AND customer_key=?""",
            (amount, amount, now, campaign_id, customer_key),
        )
    conn.execute(f"DELETE FROM city_run_token_ledger WHERE source_key IN ({marks})", source_keys)
    conn.execute(
        "INSERT INTO audit_log(action,staff_id,details,created_at) VALUES('city_run_points_reversed',?,?,?)",
        (str(staff_id), json.dumps({"sale_ids": sale_ids, "reveals_removed": sum(grouped.values())}), now),
    )
    return sum(grouped.values())


def void_packs_for_sales(conn, sale_ids: list[int], now: str, staff_id: str) -> int:
    if not sale_ids:
        return 0
    marks = ",".join("?" for _ in sale_ids)
    packs = conn.execute(
        f"SELECT * FROM city_run_packs WHERE sale_id IN ({marks}) AND status!='voided'", sale_ids
    ).fetchall()
    if any(row["status"] == "opened" for row in packs):
        raise ValueError("This sale cannot be undone because its City Run pack has already been opened.")
    for pack in packs:
        items = conn.execute(
            "SELECT business_key FROM city_run_pack_items WHERE pack_id=?", (pack["id"],)
        ).fetchall()
        for item in items:
            conn.execute(
                """UPDATE city_run_inventory SET issued_count=MAX(0,issued_count-1)
                   WHERE campaign_id=? AND business_key=?""",
                (pack["campaign_id"], item["business_key"]),
            )
        conn.execute(
            "UPDATE city_run_packs SET status='voided',voided_at=?,voided_by=? WHERE id=?",
            (now, str(staff_id), pack["id"]),
        )
    return len(packs)


class CityRunStore:
    def __init__(self, db):
        self.db = db
        with db.connect() as conn:
            from snr_core import utc_now
            ensure_city_run_schema(conn, utc_now())

    def current(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM city_run_campaigns WHERE status IN ('draft','active','paused') ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not row:
                row = conn.execute("SELECT * FROM city_run_campaigns ORDER BY id DESC LIMIT 1").fetchone()
            if not row:
                return {}
            result = dict(row)
            result["business_count"] = int(conn.execute(
                "SELECT COUNT(*) FROM city_run_inventory WHERE campaign_id=?", (row["id"],)
            ).fetchone()[0])
            result["configured_pieces"] = int(conn.execute(
                "SELECT COUNT(*) FROM city_run_inventory WHERE campaign_id=? AND rarity!='unassigned'", (row["id"],)
            ).fetchone()[0])
            result["configured_rewards"] = int(conn.execute(
                "SELECT COUNT(*) FROM city_run_rewards WHERE campaign_id=? AND active=1", (row["id"],)
            ).fetchone()[0])
            result["packs_issued"] = int(conn.execute(
                "SELECT COUNT(*) FROM city_run_packs WHERE campaign_id=? AND status!='voided'", (row["id"],)
            ).fetchone()[0])
            result["packs_unopened"] = int(conn.execute(
                "SELECT COUNT(*) FROM city_run_packs WHERE campaign_id=? AND status='unopened'", (row["id"],)
            ).fetchone()[0])
        return result

    def _sync_corner_awards(self, campaign: dict[str, Any], customer_key: str,
                            customer_name: str, unique_collected: int) -> None:
        """Award one-time corner bonuses when a customer reaches the milestone."""
        from snr_core import utc_now
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for rule in CORNER_RULES:
                if unique_collected < int(rule["threshold"]):
                    continue
                source = conn.execute(
                    "SELECT 1 FROM city_run_corner_awards WHERE campaign_id=? AND customer_key=? AND corner_key=?",
                    (campaign["id"], customer_key, rule["key"]),
                ).fetchone()
                if source:
                    continue
                now = utc_now()
                bonus = int(rule["bonus"])
                conn.execute(
                    "INSERT INTO city_run_corner_awards(campaign_id,customer_key,corner_key,bonus_reveals,awarded_at) VALUES(?,?,?,?,?)",
                    (campaign["id"], customer_key, rule["key"], bonus, now),
                )
                queue_event(conn, f"corner:{campaign['id']}:{customer_key}:{rule['key']}",
                            f"{customer_name} unlocked {rule['name']}. Bonus reveals: {bonus}.", now)
                if bonus:
                    ledger_key = f"corner:{campaign['id']}:{customer_key}:{rule['key']}"
                    conn.execute(
                        "INSERT OR IGNORE INTO city_run_token_ledger(campaign_id,customer_key,customer_name,source_key,amount,created_at) VALUES(?,?,?,?,?,?)",
                        (campaign["id"], customer_key, customer_name, ledger_key, bonus, now),
                    )
                    if conn.execute("SELECT changes()").fetchone()[0]:
                        conn.execute(
                            """INSERT INTO city_run_tokens(campaign_id,customer_key,customer_name,available,lifetime_earned,lifetime_used,updated_at)
                               VALUES(?,?,?,?,?,0,?) ON CONFLICT(campaign_id,customer_key) DO UPDATE SET
                               available=available+excluded.available,lifetime_earned=lifetime_earned+excluded.lifetime_earned,
                               customer_name=excluded.customer_name,updated_at=excluded.updated_at""",
                            (campaign["id"], customer_key, customer_name, bonus, bonus, now),
                        )

    def customer_board(self, customer_key: str) -> dict[str, Any]:
        from snr_core import normalize_name
        customer_key = normalize_name(customer_key)
        campaign = self.current()
        if not campaign:
            return {"campaign": {}, "collections": [], "available_reveals": 0,
                    "unique_collected": 0, "duplicates": 0, "total_businesses": len(BUSINESSES)}
        with self.db.connect() as conn:
            cards = {row["business_key"]: dict(row) for row in conn.execute(
                """SELECT c.*,i.rarity FROM city_run_customer_cards c
                   JOIN city_run_inventory i ON i.campaign_id=c.campaign_id AND i.business_key=c.business_key
                   WHERE c.campaign_id=? AND c.customer_key=?""",
                (campaign["id"], customer_key),
            ).fetchall()}
            customer_name = conn.execute(
                "SELECT display_name FROM customers WHERE customer_key=?", (customer_key,)
            ).fetchone()
            unique_collected = len(cards)
            if customer_name:
                # Corner milestones are idempotent and are awarded once per season.
                pass
            token_row = conn.execute(
                """SELECT available,lifetime_earned,lifetime_used FROM city_run_tokens
                   WHERE campaign_id=? AND customer_key=?""",
                (campaign["id"], customer_key),
            ).fetchone()
            claims = {row["reward_key"]: dict(row) for row in conn.execute(
                """SELECT * FROM city_run_claims WHERE campaign_id=? AND customer_key=?""",
                (campaign["id"], customer_key),
            ).fetchall()}
            rewards = {row["reward_key"]: dict(row) for row in conn.execute(
                """SELECT * FROM city_run_rewards WHERE campaign_id=? AND active=1""",
                (campaign["id"],),
            ).fetchall()}
        if campaign.get("status") == "active" and customer_name:
            self._sync_corner_awards(campaign, customer_key, customer_name["display_name"], len(cards))
            with self.db.connect() as conn:
                token_row = conn.execute(
                    "SELECT available,lifetime_earned,lifetime_used FROM city_run_tokens WHERE campaign_id=? AND customer_key=?",
                    (campaign["id"], customer_key),
                ).fetchone()
        groups = []
        for key, details in COLLECTIONS.items():
            items = []
            for business in [row for row in BUSINESSES if row["collection_key"] == key]:
                owned = cards.get(business["key"])
                items.append({**business, "owned": bool(owned),
                              "copies": int(owned["copies_owned"]) if owned else 0,
                              "rarity": owned["rarity"] if owned else "hidden"})
            groups.append({"key": key, "name": details["name"], "colour": details["colour"],
                           "items": items, "collected": sum(1 for item in items if item["owned"]),
                           "total": len(items), "complete": all(item["owned"] for item in items),
                           "reward": rewards.get(key), "claim": claims.get(key)})
        awarded_corners = set()
        with self.db.connect() as conn:
            awarded_corners = {row["corner_key"] for row in conn.execute(
                "SELECT corner_key FROM city_run_corner_awards WHERE campaign_id=? AND customer_key=?",
                (campaign["id"], customer_key),
            ).fetchall()}
        corners = [{**rule, "unlocked": rule["key"] in awarded_corners or len(cards) >= int(rule["threshold"])} for rule in CORNER_RULES]
        return {"campaign": campaign, "collections": groups,
                "available_reveals": int(token_row["available"]) if token_row else 0,
                "lifetime_reveals": int(token_row["lifetime_earned"]) if token_row else 0,
                "reveals_used": int(token_row["lifetime_used"]) if token_row else 0,
                "unique_collected": len(cards),
                "duplicates": sum(max(0, int(row["copies_owned"]) - 1) for row in cards.values()),
                "total_businesses": len(BUSINESSES), "grand_reward": rewards.get("grand"),
                "corners": corners,
                "grand_claim": claims.get("grand"), "grand_complete": len(cards) == len(BUSINESSES)}

    def reveal_one(self, customer_key: str, request_key: str | None = None) -> dict[str, Any]:
        """Spend one reveal credit and atomically award one digital business sticker."""
        from snr_core import normalize_name, utc_now
        key = normalize_name(customer_key)
        request_key = str(request_key or secrets.token_urlsafe(24))
        if not 16 <= len(request_key) <= 200:
            raise ValueError("This City Run reveal has expired. Refresh and try again.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """SELECT r.*,b.business_name,b.collection_key,b.collection_name,b.colour,i.rarity,
                   c.copies_owned,t.available AS reveals_left
                   FROM city_run_reveals r JOIN city_run_businesses b USING(business_key)
                   JOIN city_run_inventory i ON i.campaign_id=r.campaign_id AND i.business_key=r.business_key
                   JOIN city_run_customer_cards c ON c.campaign_id=r.campaign_id
                       AND c.customer_key=r.customer_key AND c.business_key=r.business_key
                   JOIN city_run_tokens t ON t.campaign_id=r.campaign_id AND t.customer_key=r.customer_key
                   WHERE r.request_key=?""",
                (request_key,),
            ).fetchone()
            if existing:
                if existing["customer_key"] != key:
                    raise ValueError("This City Run reveal does not belong to your account.")
                return dict(existing)
            campaign = conn.execute(
                "SELECT * FROM city_run_campaigns WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not campaign:
                raise ValueError("SNR City Run is not accepting reveals right now.")
            token = conn.execute(
                "SELECT * FROM city_run_tokens WHERE campaign_id=? AND customer_key=?",
                (campaign["id"], key),
            ).fetchone()
            if not token or int(token["available"]) < 1:
                raise ValueError("You need a City Run point before revealing another business.")
            rows = conn.execute(
                """SELECT i.business_key,i.rarity,i.total_available,i.issued_count
                   FROM city_run_inventory i JOIN city_run_businesses b USING(business_key)
                   WHERE i.campaign_id=? AND b.active=1 AND i.rarity!='unassigned'
                   AND (i.total_available IS NULL OR i.issued_count<i.total_available)
                   ORDER BY i.business_key""",
                (campaign["id"],),
            ).fetchall()
            owned_keys = {
                row["business_key"] for row in conn.execute(
                    "SELECT business_key FROM city_run_customer_cards WHERE campaign_id=? AND customer_key=?",
                    (campaign["id"], key),
                ).fetchall()
            }
            chosen = _weighted_choice(rows, owned_keys)
            previous = conn.execute(
                """SELECT copies_owned FROM city_run_customer_cards
                   WHERE campaign_id=? AND customer_key=? AND business_key=?""",
                (campaign["id"], key, chosen["business_key"]),
            ).fetchone()
            now = utc_now()
            conn.execute(
                """UPDATE city_run_tokens SET available=available-1,lifetime_used=lifetime_used+1,
                   updated_at=? WHERE campaign_id=? AND customer_key=? AND available>=1""",
                (now, campaign["id"], key),
            )
            conn.execute(
                """UPDATE city_run_inventory SET issued_count=issued_count+1
                   WHERE campaign_id=? AND business_key=?""",
                (campaign["id"], chosen["business_key"]),
            )
            conn.execute(
                """INSERT INTO city_run_customer_cards
                   (campaign_id,customer_key,business_key,copies_owned,first_collected_at,updated_at)
                   VALUES(?,?,?,1,?,?)
                   ON CONFLICT(campaign_id,customer_key,business_key) DO UPDATE SET
                   copies_owned=copies_owned+1,updated_at=excluded.updated_at""",
                (campaign["id"], key, chosen["business_key"], now, now),
            )
            cursor = conn.execute(
                """INSERT INTO city_run_reveals
                   (campaign_id,customer_key,business_key,duplicate,request_key,revealed_at)
                   VALUES(?,?,?,?,?,?)""",
                (campaign["id"], key, chosen["business_key"], 1 if previous else 0,
                 request_key, now),
            )
            result = conn.execute(
                """SELECT r.*,b.business_name,b.collection_key,b.collection_name,b.colour,i.rarity,
                   c.copies_owned,t.available AS reveals_left
                   FROM city_run_reveals r JOIN city_run_businesses b USING(business_key)
                   JOIN city_run_inventory i ON i.campaign_id=r.campaign_id AND i.business_key=r.business_key
                   JOIN city_run_customer_cards c ON c.campaign_id=r.campaign_id
                       AND c.customer_key=r.customer_key AND c.business_key=r.business_key
                   JOIN city_run_tokens t ON t.campaign_id=r.campaign_id AND t.customer_key=r.customer_key
                   WHERE r.id=?""",
                (cursor.lastrowid,),
            ).fetchone()
            conn.execute(
                "INSERT INTO audit_log(action,details,created_at) VALUES('city_run_business_revealed',?,?)",
                (json.dumps({"customer": key, "business": chosen["business_key"],
                             "duplicate": bool(previous)}), now),
            )
            queue_completions(conn, campaign['id'], key, now)
        return dict(result)

    def unopened_packs(self, customer_key: str) -> list[dict[str, Any]]:
        campaign = self.current()
        if not campaign:
            return []
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute(
                """SELECT * FROM city_run_packs WHERE campaign_id=? AND customer_key=?
                   AND status='unopened' ORDER BY id""", (campaign["id"], customer_key)
            ).fetchall()]

    def open_pack(self, customer_key: str, pack_id: int) -> dict[str, Any]:
        from snr_core import normalize_name, utc_now
        key = normalize_name(customer_key)
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            pack = conn.execute("SELECT * FROM city_run_packs WHERE id=?", (int(pack_id),)).fetchone()
            if not pack or pack["customer_key"] != key:
                raise ValueError("That City Run pack does not belong to your account.")
            if pack["status"] == "voided":
                raise ValueError("That City Run pack was cancelled with its original sale.")
            items = conn.execute(
                """SELECT p.*,b.business_name,b.collection_key,b.collection_name,b.colour,i.rarity
                   FROM city_run_pack_items p JOIN city_run_businesses b USING(business_key)
                   JOIN city_run_inventory i ON i.campaign_id=? AND i.business_key=p.business_key
                   WHERE p.pack_id=? ORDER BY p.slot""", (pack["campaign_id"], pack["id"])
            ).fetchall()
            if pack["status"] == "opened":
                return {"pack": dict(pack), "items": [dict(row) for row in items], "already_opened": True}
            if pack["status"] != "unopened":
                raise ValueError("That City Run pack cannot be opened.")
            now = utc_now()
            revealed = []
            for item in items:
                previous = conn.execute(
                    """SELECT copies_owned FROM city_run_customer_cards
                       WHERE campaign_id=? AND customer_key=? AND business_key=?""",
                    (pack["campaign_id"], key, item["business_key"]),
                ).fetchone()
                conn.execute(
                    """INSERT INTO city_run_customer_cards
                       (campaign_id,customer_key,business_key,copies_owned,first_collected_at,updated_at)
                       VALUES(?,?,?,1,?,?)
                       ON CONFLICT(campaign_id,customer_key,business_key) DO UPDATE SET
                       copies_owned=copies_owned+1,updated_at=excluded.updated_at""",
                    (pack["campaign_id"], key, item["business_key"], now, now),
                )
                revealed.append({**dict(item), "duplicate": previous is not None,
                                 "copy_number": int(previous["copies_owned"]) + 1 if previous else 1})
            conn.execute("UPDATE city_run_packs SET status='opened',opened_at=? WHERE id=?", (now, pack["id"]))
            queue_completions(conn, pack['campaign_id'], key, now)
            conn.execute(
                "INSERT INTO audit_log(action,details,created_at) VALUES('city_run_pack_opened',?,?)",
                (json.dumps({"pack_id": pack["id"], "customer": key,
                             "businesses": [item["business_key"] for item in revealed]}), now),
            )
            updated = dict(conn.execute("SELECT * FROM city_run_packs WHERE id=?", (pack["id"],)).fetchone())
        return {"pack": updated, "items": revealed, "already_opened": False}

    def inventory(self) -> list[dict[str, Any]]:
        campaign = self.current()
        if not campaign:
            return []
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute(
                """SELECT i.*,b.business_name,b.collection_key,b.collection_name,b.colour,b.position
                   FROM city_run_inventory i JOIN city_run_businesses b USING(business_key)
                   WHERE i.campaign_id=? ORDER BY b.collection_key,b.position""", (campaign["id"],)
            ).fetchall()]

    def configure_piece(self, business_key: str, rarity: str, total_available: int | None,
                        staff_id: str, staff_name: str) -> dict[str, Any]:
        from snr_core import utc_now
        rarity = str(rarity or "").strip().lower()
        if rarity not in RARITY_WEIGHTS:
            raise ValueError("Rarity must be common, rare or ultra rare.")
        if rarity == "common":
            total = None
        else:
            try:
                total = int(total_available)
            except (TypeError, ValueError):
                raise ValueError("Rare collectibles need a fixed available quantity.")
            if not 1 <= total <= 10000:
                raise ValueError("Available quantity must be between 1 and 10,000.")
        campaign = self.current()
        if not campaign or campaign["status"] != "draft":
            raise ValueError("Collectible rarity can only be changed while the campaign is in draft.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT i.*,b.business_name FROM city_run_inventory i
                   JOIN city_run_businesses b USING(business_key)
                   WHERE i.campaign_id=? AND i.business_key=?""",
                (campaign["id"], business_key),
            ).fetchone()
            if not row:
                raise ValueError("City Run business not found.")
            conn.execute(
                """UPDATE city_run_inventory SET rarity=?,total_available=?
                   WHERE campaign_id=? AND business_key=?""",
                (rarity, total, campaign["id"], business_key),
            )
            conn.execute(
                """INSERT INTO audit_log(action,staff_id,staff_name,details,created_at)
                   VALUES('city_run_piece_configured',?,?,?,?)""",
                (str(staff_id), staff_name,
                 json.dumps({"business": row["business_name"], "rarity": rarity,
                             "total_available": total}), utc_now()),
            )
            result = conn.execute(
                """SELECT i.*,b.business_name FROM city_run_inventory i
                   JOIN city_run_businesses b USING(business_key)
                   WHERE i.campaign_id=? AND i.business_key=?""",
                (campaign["id"], business_key),
            ).fetchone()
        return dict(result)

    def configure_reward(self, reward_key: str, reward_name: str, reward_description: str,
                         reward_type: str, reward_amount: int | None, stock_limit: int | None,
                         staff_id: str, staff_name: str) -> dict[str, Any]:
        """Save an owner-selected reward; no defaults are silently invented."""
        from snr_core import utc_now
        reward_key = str(reward_key or "").strip().lower()
        if reward_key not in REWARD_KEYS:
            raise ValueError("Choose a valid City Run collection.")
        reward_name = " ".join(str(reward_name or "").split())
        reward_description = " ".join(str(reward_description or "").split())
        reward_type = str(reward_type or "").strip().lower()
        if not 2 <= len(reward_name) <= 80 or not 3 <= len(reward_description) <= 240:
            raise ValueError("Enter a clear reward name and description.")
        if reward_type not in ("food", "cash", "vip", "vehicle", "custom"):
            raise ValueError("Reward type must be food, cash, VIP, vehicle or custom.")
        try:
            amount = int(reward_amount) if reward_amount not in (None, "") else None
            stock = int(stock_limit) if stock_limit not in (None, "") else None
        except (TypeError, ValueError):
            raise ValueError("Reward amount and stock must be whole numbers.")
        if amount is not None and amount < 0:
            raise ValueError("Reward amount cannot be negative.")
        if stock is not None and not 1 <= stock <= 10000:
            raise ValueError("Reward stock must be between 1 and 10,000.")
        campaign = self.current()
        if not campaign or campaign["status"] != "draft":
            raise ValueError("Rewards can only be changed while the campaign is in draft.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO city_run_rewards
                   (campaign_id,reward_key,reward_name,reward_description,reward_type,reward_amount,stock_limit)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,reward_key) DO UPDATE SET
                   reward_name=excluded.reward_name,reward_description=excluded.reward_description,
                   reward_type=excluded.reward_type,reward_amount=excluded.reward_amount,
                   stock_limit=excluded.stock_limit,active=1""",
                (campaign["id"], reward_key, reward_name, reward_description,
                 reward_type, amount, stock),
            )
            conn.execute(
                """INSERT INTO audit_log(action,staff_id,staff_name,details,created_at)
                   VALUES('city_run_reward_configured',?,?,?,?)""",
                (str(staff_id), staff_name,
                 json.dumps({"reward_key": reward_key, "reward_name": reward_name,
                             "amount": amount, "stock": stock}), utc_now()),
            )
            row = conn.execute(
                "SELECT * FROM city_run_rewards WHERE campaign_id=? AND reward_key=?",
                (campaign["id"], reward_key),
            ).fetchone()
        return dict(row)

    def apply_recommended_rarities(self, staff_id: str, staff_name: str) -> dict[str, Any]:
        """Apply the owner-approved scarcity plan while the campaign is still a draft."""
        from snr_core import utc_now
        campaign = self.current()
        if not campaign or campaign["status"] != "draft":
            raise ValueError("Recommended rarities can only be applied while City Run is in draft.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for business in BUSINESSES:
                rarity, total = RECOMMENDED_RARE_PIECES.get(business["key"], ("common", None))
                conn.execute(
                    """UPDATE city_run_inventory SET rarity=?,total_available=?
                       WHERE campaign_id=? AND business_key=?""",
                    (rarity, total, campaign["id"], business["key"]),
                )
            conn.execute(
                """INSERT INTO audit_log(action,staff_id,staff_name,details,created_at)
                   VALUES('city_run_recommended_rarities_applied',?,?,?,?)""",
                (str(staff_id), staff_name,
                 json.dumps({"campaign_id": campaign["id"], "limited_pieces": RECOMMENDED_RARE_PIECES}),
                 utc_now()),
            )
        return self.current()

    def activate(self, staff_id: str, staff_name: str) -> dict[str, Any]:
        from snr_core import utc_now
        campaign = self.current()
        if not campaign or campaign["status"] != "draft":
            raise ValueError("There is no draft City Run campaign to activate.")
        if int(campaign["configured_pieces"]) != len(BUSINESSES):
            raise ValueError("Configure all 38 collectible rarities before starting City Run.")
        if int(campaign["configured_rewards"]) != len(REWARD_KEYS):
            raise ValueError("Choose all eight collection rewards and the grand prize before starting City Run.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            channel_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='discord_alert_channels'"
            ).fetchone()
            route = (conn.execute(
                """SELECT 1 FROM discord_alert_channels
                   WHERE alert_type='city_run_claims' LIMIT 1"""
            ).fetchone() if channel_table else None)
            if not route:
                raise ValueError(
                    "Set the private City Run Reward Claims channel before starting the season."
                )
            now = utc_now()
            # Move every current loyalty balance into City Run stickers, then
            # clear the old account points so customers have one progress system.
            customers = conn.execute(
                "SELECT customer_key,display_name,loyalty_points FROM customers WHERE loyalty_points>0"
            ).fetchall()
            for customer in customers:
                amount = int(customer["loyalty_points"])
                source_key = f"opening:{campaign['id']}:{customer['customer_key']}"
                conn.execute(
                    """INSERT OR IGNORE INTO city_run_token_ledger
                       (campaign_id,customer_key,customer_name,source_key,amount,created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (campaign["id"], customer["customer_key"], customer["display_name"],
                     source_key, amount, now),
                )
                if conn.execute("SELECT changes()").fetchone()[0]:
                    conn.execute(
                        """INSERT INTO city_run_tokens
                           (campaign_id,customer_key,customer_name,available,lifetime_earned,lifetime_used,updated_at)
                           VALUES(?,?,?,?,?,0,?)
                           ON CONFLICT(campaign_id,customer_key) DO UPDATE SET
                           available=available+excluded.available,
                           lifetime_earned=lifetime_earned+excluded.lifetime_earned,
                           customer_name=excluded.customer_name,updated_at=excluded.updated_at""",
                        (campaign["id"], customer["customer_key"], customer["display_name"],
                         amount, amount, now),
                    )
                conn.execute(
                    "UPDATE customers SET loyalty_points=0,updated_at=? WHERE customer_key=?",
                    (now, customer["customer_key"]),
                )
            conn.execute(
                "UPDATE city_run_campaigns SET status='active',activated_at=?,starts_at=? WHERE id=?",
                (now, now, campaign["id"]),
            )
            conn.execute(
                """INSERT INTO audit_log(action,staff_id,staff_name,details,created_at)
                   VALUES('city_run_activated',?,?,?,?)""",
                (str(staff_id), staff_name, json.dumps({"campaign_id": campaign["id"]}), now),
            )
        return self.current()

    def set_status(self, status: str, staff_id: str, staff_name: str) -> dict[str, Any]:
        from snr_core import utc_now
        status = str(status or "").strip().lower()
        campaign = self.current()
        if not campaign:
            raise ValueError("City Run campaign not found.")
        allowed = {("active", "paused"), ("paused", "active"),
                   ("active", "ended"), ("paused", "ended")}
        if (campaign["status"], status) not in allowed:
            raise ValueError("That City Run status change is not allowed.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = utc_now()
            ended_at = now if status == "ended" else None
            conn.execute(
                "UPDATE city_run_campaigns SET status=?,ended_at=COALESCE(?,ended_at) WHERE id=?",
                (status, ended_at, campaign["id"]),
            )
            conn.execute(
                """INSERT INTO audit_log(action,staff_id,staff_name,details,created_at)
                   VALUES(?,?,?,?,?)""",
                (f"city_run_{status}", str(staff_id), staff_name,
                 json.dumps({"campaign_id": campaign["id"]}), now),
            )
        return self.current()

    def request_reward(self, customer_key: str, reward_key: str) -> dict[str, Any]:
        """Create one verified collection claim for the authenticated customer."""
        from snr_core import normalize_name, utc_now
        key = normalize_name(customer_key)
        reward_key = str(reward_key or "").strip().lower()
        if reward_key not in REWARD_KEYS:
            raise ValueError("Choose a valid City Run reward.")
        board = self.customer_board(key)
        campaign = board.get("campaign") or {}
        if campaign.get("status") not in ("active", "paused"):
            raise ValueError("SNR City Run reward claims are not open yet.")
        if reward_key == "grand":
            complete = bool(board.get("grand_complete"))
        else:
            route = next((row for row in board["collections"] if row["key"] == reward_key), None)
            complete = bool(route and route["complete"])
        if not complete:
            raise ValueError("Complete that City Run collection before requesting its reward.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """SELECT * FROM city_run_claims
                   WHERE campaign_id=? AND customer_key=? AND reward_key=?""",
                (campaign["id"], key, reward_key),
            ).fetchone()
            if existing and existing["status"] != "cancelled":
                return dict(existing)
            reward = conn.execute(
                """SELECT * FROM city_run_rewards
                   WHERE campaign_id=? AND reward_key=? AND active=1""",
                (campaign["id"], reward_key),
            ).fetchone()
            if not reward:
                raise ValueError("That City Run reward is not available.")
            reserved = int(conn.execute(
                """SELECT COUNT(*) FROM city_run_claims
                   WHERE campaign_id=? AND reward_key=? AND status IN ('pending','fulfilled')""",
                (campaign["id"], reward_key),
            ).fetchone()[0])
            if reward["stock_limit"] is not None and reserved >= int(reward["stock_limit"]):
                raise ValueError("That limited City Run reward has already been fully claimed.")
            route = conn.execute(
                """SELECT guild_id,channel_id FROM discord_alert_channels
                   WHERE alert_type='city_run_claims' ORDER BY updated_at DESC LIMIT 1"""
            ).fetchone()
            if not route:
                raise ValueError("City Run claims are not connected to the SNR staff channel yet.")
            customer = conn.execute(
                "SELECT display_name FROM customers WHERE customer_key=?", (key,)
            ).fetchone()
            if not customer:
                raise ValueError("Customer account not found.")
            now = utc_now()
            if existing:
                conn.execute(
                    """UPDATE city_run_claims SET status='pending',requested_at=?,resolved_at=NULL,
                       resolved_by=NULL,resolved_by_name=NULL,channel_id=?,guild_id=?,message_id=NULL
                       WHERE id=?""",
                    (now, route["channel_id"], route["guild_id"], existing["id"]),
                )
                claim_id = int(existing["id"])
            else:
                cursor = conn.execute(
                    """INSERT INTO city_run_claims
                       (campaign_id,customer_key,customer_name,reward_key,reward_name,
                        reward_description,status,requested_at,channel_id,guild_id)
                       VALUES(?,?,?,?,?,?,'pending',?,?,?)""",
                    (campaign["id"], key, customer["display_name"], reward_key,
                     reward["reward_name"], reward["reward_description"], now,
                     route["channel_id"], route["guild_id"]),
                )
                claim_id = int(cursor.lastrowid)
            conn.execute(
                """INSERT INTO audit_log(action,details,created_at)
                   VALUES('city_run_reward_requested',?,?)""",
                (json.dumps({"claim_id": claim_id, "customer": key,
                             "reward_key": reward_key}), now),
            )
            row = conn.execute("SELECT * FROM city_run_claims WHERE id=?", (claim_id,)).fetchone()
        return dict(row)

    def get_claim(self, claim_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM city_run_claims WHERE id=?", (int(claim_id),)).fetchone()
        return dict(row) if row else None

    def mark_claim_notified(self, claim_id: int, message_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE city_run_claims SET message_id=? WHERE id=? AND status='pending'",
                (str(message_id), int(claim_id)),
            )

    def resolve_claim(self, claim_id: int, status: str, staff_id: str,
                      staff_name: str) -> dict[str, Any]:
        from snr_core import utc_now
        if status not in ("fulfilled", "cancelled"):
            raise ValueError("Choose fulfilled or cancelled.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM city_run_claims WHERE id=?", (int(claim_id),)
            ).fetchone()
            if not row:
                raise ValueError("City Run claim not found.")
            if row["status"] != "pending":
                raise ValueError("That City Run claim has already been handled.")
            now = utc_now()
            if status == "fulfilled":
                reward = conn.execute(
                    """SELECT * FROM city_run_rewards
                       WHERE campaign_id=? AND reward_key=?""",
                    (row["campaign_id"], row["reward_key"]),
                ).fetchone()
                if not reward:
                    raise ValueError("City Run reward configuration is missing.")
                if (reward["stock_limit"] is not None
                        and int(reward["claims_fulfilled"]) >= int(reward["stock_limit"])):
                    raise ValueError("That City Run reward has no stock remaining.")
                conn.execute(
                    """UPDATE city_run_rewards SET claims_fulfilled=claims_fulfilled+1
                       WHERE campaign_id=? AND reward_key=?""",
                    (row["campaign_id"], row["reward_key"]),
                )
            conn.execute(
                """UPDATE city_run_claims SET status=?,resolved_at=?,resolved_by=?,resolved_by_name=?
                   WHERE id=?""",
                (status, now, str(staff_id), staff_name, int(claim_id)),
            )
            queue_event(conn, f"claim:{claim_id}:{status}:{row['requested_at']}",
                        f"{row['customer_name']} — {row['reward_name']}: {status} by {staff_name}. Claim #{claim_id}.", now)
            conn.execute(
                """INSERT INTO audit_log(action,staff_id,staff_name,details,created_at)
                   VALUES(?,?,?,?,?)""",
                (f"city_run_reward_{status}", str(staff_id), staff_name,
                 json.dumps({"claim_id": int(claim_id), "customer": row["customer_key"],
                             "reward_key": row["reward_key"]}), now),
            )
        return self.get_claim(claim_id)

    def pending_claims(self, unsent: bool = False) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM city_run_claims WHERE status='pending'" +
                (" AND message_id IS NULL" if unsent else "") + " ORDER BY id"
            ).fetchall()]

    def pending_events(self):
        with self.db.connect() as conn:
            route = conn.execute("SELECT guild_id,channel_id FROM discord_alert_channels WHERE alert_type='city_run_claims' ORDER BY updated_at DESC LIMIT 1").fetchone()
            if not route:
                return []
            return [{**dict(r), **dict(route)} for r in conn.execute('SELECT * FROM city_run_events WHERE message_id IS NULL ORDER BY id LIMIT 20')]

    def mark_event_sent(self, event_id, message_id):
        with self.db.connect() as conn:
            conn.execute('UPDATE city_run_events SET message_id=? WHERE id=? AND message_id IS NULL', (str(message_id), event_id))
