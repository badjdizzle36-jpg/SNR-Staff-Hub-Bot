"""Shared staff clock used by Discord and the public delivery page."""
import time

from snr_core import utc_now


class StaffShifts:
    def __init__(self, db):
        self.db = db
        with db.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS staff_shifts (
                    staff_id TEXT PRIMARY KEY,
                    staff_name TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    clocked_in_at TEXT NOT NULL,
                    expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS staff_shift_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    staff_id TEXT NOT NULL,
                    staff_name TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(staff_shifts)")}
            if "expires" not in columns:
                conn.execute("ALTER TABLE staff_shifts ADD COLUMN expires REAL NOT NULL DEFAULT 0")
                conn.execute("UPDATE staff_shifts SET expires=? WHERE expires=0", (time.time() + 8 * 3600,))

    def clock_in(self, staff_id, staff_name, guild_id):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM staff_shifts WHERE staff_id=?", (str(staff_id),)).fetchone():
                conn.execute("UPDATE staff_shifts SET expires=? WHERE staff_id=?",
                             (time.time() + 8 * 3600, str(staff_id)))
                return False
            now = utc_now()
            conn.execute("INSERT INTO staff_shifts(staff_id,staff_name,guild_id,clocked_in_at,expires) VALUES(?,?,?,?,?)",
                         (str(staff_id), str(staff_name), str(guild_id), now, time.time() + 8 * 3600))
            conn.execute("INSERT INTO staff_shift_log(staff_id,staff_name,guild_id,action,created_at) VALUES(?,?,?,?,?)",
                         (str(staff_id), str(staff_name), str(guild_id), "clock_in", now))
        return True

    def clock_out(self, staff_id):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM staff_shifts WHERE staff_id=?", (str(staff_id),)).fetchone()
            if not row:
                return False
            conn.execute("DELETE FROM staff_shifts WHERE staff_id=?", (str(staff_id),))
            conn.execute("INSERT INTO staff_shift_log(staff_id,staff_name,guild_id,action,created_at) VALUES(?,?,?,?,?)",
                         (row["staff_id"], row["staff_name"], row["guild_id"], "clock_out", utc_now()))
        return True

    def active(self, guild_id=None):
        with self.db.connect() as conn:
            conn.execute("DELETE FROM staff_shifts WHERE expires<=?", (time.time(),))
            if guild_id is None:
                rows = conn.execute("SELECT * FROM staff_shifts ORDER BY clocked_in_at").fetchall()
            else:
                rows = conn.execute("SELECT * FROM staff_shifts WHERE guild_id=? ORDER BY clocked_in_at",
                                    (str(guild_id),)).fetchall()
        return [dict(row) for row in rows]

    def drivers_available(self):
        with self.db.connect() as conn:
            conn.execute("DELETE FROM staff_shifts WHERE expires<=?", (time.time(),))
            return conn.execute("SELECT 1 FROM staff_shifts LIMIT 1").fetchone() is not None
