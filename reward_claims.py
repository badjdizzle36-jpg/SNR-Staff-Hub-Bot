"""Transactional website pack claims; all state lives in the existing SQLite file."""
import hashlib
import hmac
import secrets
import time

from snr_core import normalize_name, utc_now


class ClaimStore:
    def __init__(self, db):
        self.db = db
        with db.connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS web_claim_settings (
                    id INTEGER PRIMARY KEY CHECK(id=1), channel_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS web_claim_codes (
                    customer_key TEXT PRIMARY KEY REFERENCES customers(customer_key),
                    code_hash TEXT NOT NULL, failures INTEGER NOT NULL DEFAULT 0,
                    locked_until REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS web_pack_claims (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_key TEXT NOT NULL REFERENCES customers(customer_key),
                    customer_name TEXT NOT NULL,
                    request_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    points INTEGER NOT NULL DEFAULT 4,
                    created_at TEXT NOT NULL, resolved_at TEXT, resolved_by TEXT,
                    channel_id TEXT NOT NULL, guild_id TEXT NOT NULL,
                    message_id TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_web_pack
                    ON web_pack_claims(customer_key) WHERE status='pending';
            ''')

    @staticmethod
    def digest(code):
        return hashlib.sha256(code.strip().upper().replace('-', '').replace(' ', '').encode()).hexdigest()

    @staticmethod
    def audit(conn, action, details, staff_id=None, staff_name=None):
        conn.execute('INSERT INTO audit_log(action,details,staff_id,staff_name,created_at) VALUES(?,?,?,?,?)',
                     (action, details, staff_id, staff_name, utc_now()))

    def configure(self, channel_id, guild_id, staff_id, staff_name):
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('INSERT OR REPLACE INTO web_claim_settings VALUES(1,?,?)', (str(channel_id), str(guild_id)))
            conn.execute("UPDATE web_pack_claims SET channel_id=?,message_id=NULL WHERE status='pending' AND guild_id=? AND channel_id!=?",
                         (str(channel_id), str(guild_id), str(channel_id)))
            self.audit(conn, 'web_claim_channel', str(channel_id), str(staff_id), staff_name)

    def configured(self):
        with self.db.connect() as conn:
            return conn.execute('SELECT 1 FROM web_claim_settings WHERE id=1').fetchone() is not None

    def issue_code(self, name, staff_id, staff_name):
        key = normalize_name(name)
        code = secrets.token_hex(10).upper()
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM customers WHERE customer_key=?', (key,)).fetchone():
                raise ValueError('Customer not found.')
            conn.execute('INSERT OR REPLACE INTO web_claim_codes(customer_key,code_hash) VALUES(?,?)',
                         (key, self.digest(code)))
            self.audit(conn, 'web_claim_code_issued', key, str(staff_id), staff_name)
        return '-'.join(code[i:i+4] for i in range(0,len(code),4))

    def summary(self, name):
        with self.db.connect() as conn:
            rows = conn.execute('SELECT id,status FROM web_pack_claims WHERE customer_key=? ORDER BY id DESC LIMIT 5',
                                (normalize_name(name),)).fetchall()
        return [dict(row) for row in rows]

    def request(self, name, code, request_key):
        key = normalize_name(name)
        if not 10 <= len(request_key) <= 160 or len(code) > 100:
            raise ValueError('Please reopen your card and try again.')
        # Failed attempts must commit before the generic error is raised.
        failure = False
        result = None
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            credential = conn.execute('SELECT * FROM web_claim_codes WHERE customer_key=?', (key,)).fetchone()
            now = time.time()
            if not credential or credential['locked_until'] > now:
                failure = True
            elif not hmac.compare_digest(credential['code_hash'], self.digest(code)):
                failures = credential['failures'] + 1
                conn.execute('UPDATE web_claim_codes SET failures=?,locked_until=? WHERE customer_key=?',
                             (0 if failures >= 5 else failures, now+900 if failures >= 5 else 0, key))
                failure = True
            else:
                conn.execute('UPDATE web_claim_codes SET failures=0,locked_until=0 WHERE customer_key=?', (key,))
                existing = conn.execute('SELECT * FROM web_pack_claims WHERE request_key=?', (request_key,)).fetchone()
                if existing:
                    if existing['customer_key'] != key:
                        raise ValueError('Please reopen your card and try again.')
                    return dict(existing)
                pending = conn.execute("SELECT * FROM web_pack_claims WHERE customer_key=? AND status='pending'", (key,)).fetchone()
                if pending:
                    return dict(pending)
                config = conn.execute('SELECT * FROM web_claim_settings WHERE id=1').fetchone()
                if not config:
                    raise ValueError('Online claims are not set up yet. Please ask staff.')
                changed = conn.execute('UPDATE customers SET loyalty_points=loyalty_points-4,updated_at=? WHERE customer_key=? AND loyalty_points>=4',
                                       (utc_now(), key))
                if changed.rowcount != 1:
                    raise ValueError('You need four available loyalty points for a pack.')
                customer = conn.execute('SELECT display_name FROM customers WHERE customer_key=?', (key,)).fetchone()
                cursor = conn.execute('''INSERT INTO web_pack_claims
                    (customer_key,customer_name,request_key,created_at,channel_id,guild_id) VALUES(?,?,?,?,?,?)''',
                    (key,customer['display_name'],request_key,utc_now(),config['channel_id'],config['guild_id']))
                self.audit(conn, 'web_pack_requested', f'claim={cursor.lastrowid};customer={key};points=4')
                result = dict(conn.execute('SELECT * FROM web_pack_claims WHERE id=?', (cursor.lastrowid,)).fetchone())
        if failure:
            raise ValueError('Name or private claim code not accepted. After five incorrect codes, wait 15 minutes or ask staff for a new code.')
        return result

    def request_authenticated(self, customer_key, request_key):
        """Reserve a pack for the already authenticated website account owner."""
        key = normalize_name(customer_key)
        if not 10 <= len(request_key) <= 160:
            raise ValueError('Please reopen your account and try again.')
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            existing = conn.execute(
                'SELECT * FROM web_pack_claims WHERE request_key=?', (request_key,)
            ).fetchone()
            if existing:
                if existing['customer_key'] != key:
                    raise ValueError('Please reopen your account and try again.')
                return dict(existing)
            pending = conn.execute(
                "SELECT * FROM web_pack_claims WHERE customer_key=? AND status='pending'", (key,)
            ).fetchone()
            if pending:
                return dict(pending)
            config = conn.execute('SELECT * FROM web_claim_settings WHERE id=1').fetchone()
            if not config:
                raise ValueError('Online claims are not set up yet. Please ask staff.')
            changed = conn.execute(
                'UPDATE customers SET loyalty_points=loyalty_points-4,updated_at=? '
                'WHERE customer_key=? AND loyalty_points>=4', (utc_now(), key)
            )
            if changed.rowcount != 1:
                raise ValueError('You need four available loyalty points for a pack.')
            customer = conn.execute(
                'SELECT display_name FROM customers WHERE customer_key=?', (key,)
            ).fetchone()
            if not customer:
                raise ValueError('Customer account not found.')
            cursor = conn.execute(
                '''INSERT INTO web_pack_claims
                   (customer_key,customer_name,request_key,created_at,channel_id,guild_id)
                   VALUES(?,?,?,?,?,?)''',
                (key, customer['display_name'], request_key, utc_now(),
                 config['channel_id'], config['guild_id'])
            )
            self.audit(conn, 'web_pack_requested',
                       f'claim={cursor.lastrowid};customer={key};points=4;auth=password')
            return dict(conn.execute(
                'SELECT * FROM web_pack_claims WHERE id=?', (cursor.lastrowid,)
            ).fetchone())

    def pending(self, unsent=False):
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM web_pack_claims WHERE status='pending'" +
                    (' AND message_id IS NULL' if unsent else '') + ' ORDER BY id')]

    def get(self, claim_id):
        with self.db.connect() as conn:
            row = conn.execute('SELECT * FROM web_pack_claims WHERE id=?', (claim_id,)).fetchone()
            return dict(row) if row else None

    def notified(self, claim_id, message_id):
        with self.db.connect() as conn:
            conn.execute('UPDATE web_pack_claims SET message_id=? WHERE id=?', (str(message_id), claim_id))

    def resolve(self, claim_id, status, staff_id, staff_name):
        if status not in ('fulfilled', 'cancelled'):
            raise ValueError('Invalid claim action.')
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM web_pack_claims WHERE id=?', (claim_id,)).fetchone()
            if not row:
                raise ValueError('Claim not found.')
            if row['status'] != 'pending':
                raise ValueError('This claim has already been resolved.')
            conn.execute('UPDATE web_pack_claims SET status=?,resolved_at=?,resolved_by=? WHERE id=?',
                         (status, utc_now(), str(staff_id), claim_id))
            if status == 'cancelled':
                conn.execute('UPDATE customers SET loyalty_points=loyalty_points+4,updated_at=? WHERE customer_key=?',
                             (utc_now(), row['customer_key']))
            else:
                conn.execute('''UPDATE customers SET card_packs_earned=card_packs_earned+1,
                    card_packs_claimed=card_packs_claimed+1,updated_at=? WHERE customer_key=?''',
                    (utc_now(), row['customer_key']))
            self.audit(conn, 'web_pack_'+status, f'claim={claim_id};customer={row["customer_key"]}', str(staff_id), staff_name)
        return self.get(claim_id)
