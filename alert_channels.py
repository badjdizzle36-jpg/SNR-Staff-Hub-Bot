"""Owner-configurable Discord destinations for SNR website and bot alerts."""
from __future__ import annotations

from snr_core import utc_now


CHANNEL_TYPES = {
    "new_accounts": "New Loyalty Accounts",
    "active_orders": "Active Deliveries",
    "completed_orders": "Completed Deliveries",
    "pack_requests": "Pack Requests",
    "raffle_requests": "Raffle Number Requests",
    "customer_help": "Customer Help",
    "reward_requests": "Reward Requests",
    "reviews_issues": "Reviews & Problems",
}


class AlertChannels:
    def __init__(self, db):
        self.db = db
        with db.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS discord_alert_channels (
                guild_id TEXT NOT NULL, alert_type TEXT NOT NULL,
                channel_id TEXT NOT NULL, updated_at TEXT NOT NULL,
                updated_by TEXT, updated_by_name TEXT,
                PRIMARY KEY(guild_id, alert_type))""")

    @staticmethod
    def _validate(alert_type):
        key = str(alert_type or "").strip().lower()
        if key not in CHANNEL_TYPES:
            raise ValueError("Choose a valid SNR alert type.")
        return key

    def configure(self, alert_type, channel_id, guild_id, staff_id, staff_name):
        key = self._validate(alert_type)
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""INSERT INTO discord_alert_channels
                (guild_id,alert_type,channel_id,updated_at,updated_by,updated_by_name)
                VALUES(?,?,?,?,?,?) ON CONFLICT(guild_id,alert_type) DO UPDATE SET
                channel_id=excluded.channel_id,updated_at=excluded.updated_at,
                updated_by=excluded.updated_by,updated_by_name=excluded.updated_by_name""",
                (str(guild_id), key, str(channel_id), utc_now(), str(staff_id), staff_name))
            conn.execute("""INSERT INTO audit_log(action,details,staff_id,staff_name,created_at)
                VALUES(?,?,?,?,?)""", ("discord_alert_channel_changed",
                f"type={key};channel={channel_id};guild={guild_id}", str(staff_id), staff_name, utc_now()))
        return self.get(key, guild_id)

    def get(self, alert_type, guild_id):
        key = self._validate(alert_type)
        with self.db.connect() as conn:
            row = conn.execute("""SELECT * FROM discord_alert_channels
                WHERE guild_id=? AND alert_type=?""", (str(guild_id), key)).fetchone()
        return dict(row) if row else None

    def channel_id(self, alert_type, guild_id, fallback_channel_id=None):
        row = self.get(alert_type, guild_id)
        return str(row["channel_id"]) if row else (str(fallback_channel_id) if fallback_channel_id else None)

    def status(self, guild_id):
        with self.db.connect() as conn:
            rows = conn.execute("""SELECT * FROM discord_alert_channels
                WHERE guild_id=? ORDER BY alert_type""", (str(guild_id),)).fetchall()
        found = {row["alert_type"]: dict(row) for row in rows}
        return [{"alert_type": key, "label": label, "channel_id": found.get(key, {}).get("channel_id")}
                for key, label in CHANNEL_TYPES.items()]
