"""Password accounts attached to staff-owned customer records, never Discord identities."""
import hashlib
import hmac
import secrets
import time

from snr_core import normalize_name, utc_now

ROUNDS = 600_000
SESSION_SECONDS = 8 * 3600


class Accounts:
    def __init__(self, db):
        self.db = db
        with db.connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS customer_accounts (
                    customer_key TEXT PRIMARY KEY REFERENCES customers(customer_key),
                    salt TEXT, password_hash TEXT, rounds INTEGER NOT NULL DEFAULT 600000,
                    activation_hash TEXT, activation_expires REAL,
                    failures INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS customer_sessions (
                    token_hash TEXT PRIMARY KEY, customer_key TEXT NOT NULL REFERENCES customers(customer_key),
                    expires REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS customer_session_owner ON customer_sessions(customer_key);
                CREATE TABLE IF NOT EXISTS customer_account_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_key TEXT NOT NULL REFERENCES customers(customer_key),
                    customer_name TEXT NOT NULL,
                    request_type TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    rounds INTEGER NOT NULL DEFAULT 600000,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    resolved_by TEXT,
                    channel_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    message_id TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_account_request
                    ON customer_account_requests(customer_key) WHERE status='pending';
            ''')

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def code_hash(code):
        return Accounts.token_hash(code.upper().replace('-', '').replace(' ', '').strip())

    @staticmethod
    def password_hash(password, salt, rounds=ROUNDS):
        return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), rounds).hex()

    def issue_setup(self, name, staff_id, staff_name, reset=False):
        if not 2 <= len(name.strip()) <= 60:
            raise ValueError('Use a character name between 2 and 60 characters.')
        code = secrets.token_hex(10).upper()
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            customer = self.db._ensure_customer(conn, name)
            key = customer['customer_key']
            row = conn.execute('SELECT * FROM customer_accounts WHERE customer_key=?', (key,)).fetchone()
            if row and row['password_hash'] and not reset:
                raise ValueError('This account already has a password. Use Reset Website Password after verifying the customer.')
            conn.execute('''INSERT INTO customer_accounts(customer_key,activation_hash,activation_expires,updated_at)
                VALUES(?,?,?,?) ON CONFLICT(customer_key) DO UPDATE SET activation_hash=excluded.activation_hash,
                activation_expires=excluded.activation_expires,failures=0,locked_until=0,updated_at=excluded.updated_at''',
                (key,self.code_hash(code),time.time()+86400,utc_now()))
            if reset:
                # Staff-initiated recovery locks out existing password and sessions immediately.
                conn.execute('UPDATE customer_accounts SET salt=NULL,password_hash=NULL WHERE customer_key=?',(key,))
                conn.execute('DELETE FROM customer_sessions WHERE customer_key=?',(key,))
            conn.execute('INSERT INTO audit_log(action,staff_id,staff_name,details,created_at) VALUES(?,?,?,?,?)',
                         ('website_password_reset' if reset else 'website_account_setup', str(staff_id),staff_name,key,utc_now()))
        return '-'.join(code[i:i+4] for i in range(0,len(code),4))

    def _session(self, conn, key):
        token = secrets.token_urlsafe(32)
        conn.execute('DELETE FROM customer_sessions WHERE expires<=?',(time.time(),))
        conn.execute('INSERT INTO customer_sessions VALUES(?,?,?)',(self.token_hash(token),key,time.time()+SESSION_SECONDS))
        return token

    def set_password(self, name, code, password):
        if not 10 <= len(password) <= 128:
            raise ValueError('Choose a password between 10 and 128 characters.')
        if len(code)>100:
            raise ValueError('Setup code not accepted.')
        key = normalize_name(name)
        failed = False
        token = None
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM customer_accounts WHERE customer_key=?',(key,)).fetchone()
            now = time.time()
            if not row or row['locked_until']>now:
                failed = True
            elif (not row['activation_hash'] or (row['activation_expires'] or 0)<now or
                  not hmac.compare_digest(row['activation_hash'],self.code_hash(code))):
                self._failed(conn,row,now)
                failed = True
            else:
                salt = secrets.token_hex(16)
                digest = self.password_hash(password,salt)
                conn.execute('''UPDATE customer_accounts SET salt=?,password_hash=?,rounds=?,activation_hash=NULL,
                    activation_expires=NULL,failures=0,locked_until=0,updated_at=? WHERE customer_key=?''',
                    (salt,digest,ROUNDS,utc_now(),key))
                conn.execute('DELETE FROM customer_sessions WHERE customer_key=?',(key,))
                token = self._session(conn,key)
                conn.execute('INSERT INTO audit_log(action,details,created_at) VALUES(?,?,?)',
                             ('website_password_set',key,utc_now()))
        if failed:
            raise ValueError('Setup code not accepted or expired. After five attempts, wait 15 minutes or ask staff for a new code.')
        return token

    @staticmethod
    def _failed(conn,row,now):
        attempts = row['failures']+1
        conn.execute('UPDATE customer_accounts SET failures=?,locked_until=? WHERE customer_key=?',
                     (0 if attempts>=5 else attempts,now+900 if attempts>=5 else 0,row['customer_key']))

    def login(self,name,password):
        if not 1 <= len(password) <= 128:
            raise ValueError('Name or password not accepted.')
        key=normalize_name(name)
        token=None
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT * FROM customer_accounts WHERE customer_key=?',(key,)).fetchone()
            now=time.time()
            if row and row['locked_until']>now:
                pass
            else:
                digest=self.password_hash(password,row['salt'] if row and row['salt'] else '00'*16,
                                          row['rounds'] if row else ROUNDS)
                if row and row['password_hash'] and hmac.compare_digest(digest,row['password_hash']):
                    conn.execute('UPDATE customer_accounts SET failures=0,locked_until=0 WHERE customer_key=?',(key,))
                    token=self._session(conn,key)
                elif row:
                    self._failed(conn,row,now)
        if not token:
            raise ValueError('Name or password not accepted. After five attempts, wait 15 minutes or ask staff for a reset.')
        return token

    def owner(self,token,conn=None):
        if not token or len(token)>100:
            return None
        def lookup(connection):
            row=connection.execute('SELECT customer_key FROM customer_sessions WHERE token_hash=? AND expires>?',
                                   (self.token_hash(token),time.time())).fetchone()
            return row['customer_key'] if row else None
        if conn is not None:
            return lookup(conn)
        with self.db.connect() as connection:
            return lookup(connection)

    def logout(self,token):
        with self.db.connect() as conn:
            conn.execute('DELETE FROM customer_sessions WHERE token_hash=?',(self.token_hash(token),))

    def status(self,name):
        with self.db.connect() as conn:
            row=conn.execute('SELECT password_hash,activation_expires FROM customer_accounts WHERE customer_key=?',
                             (normalize_name(name),)).fetchone()
            pending=conn.execute("SELECT request_type FROM customer_account_requests WHERE customer_key=? AND status='pending'",
                                 (normalize_name(name),)).fetchone()
        if row and row['password_hash']:
            return 'Password set • reset awaiting approval' if pending else 'Password set'
        if pending:
            return 'Awaiting staff approval'
        return 'Not set up'

    def _alert_channel(self, conn):
        # Account approvals use the already configured private delivery channel.
        # Reward-claim channel is a safe fallback for older installations.
        row = conn.execute('SELECT channel_id,guild_id FROM web_delivery_settings WHERE id=1').fetchone()
        if not row:
            row = conn.execute('SELECT channel_id,guild_id FROM web_claim_settings WHERE id=1').fetchone()
        return row

    def request_access(self, name, password):
        """Let a customer choose a password; staff approve ownership in Discord."""
        if not 10 <= len(password) <= 128:
            raise ValueError('Choose a password between 10 and 128 characters.')
        key = normalize_name(name)
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            customer = conn.execute('SELECT * FROM customers WHERE customer_key=?', (key,)).fetchone()
            if not customer:
                raise ValueError('That customer name is not on the SNR loyalty system yet.')
            existing = conn.execute(
                "SELECT * FROM customer_account_requests WHERE customer_key=? AND status='pending'", (key,)
            ).fetchone()
            if existing:
                result = dict(existing)
                result["already_pending"] = True
                return result
            route = self._alert_channel(conn)
            if not route:
                raise ValueError('Website account approvals are being set up. Please ask SNR staff.')
            account = conn.execute('SELECT password_hash FROM customer_accounts WHERE customer_key=?', (key,)).fetchone()
            request_type = 'reset' if account and account['password_hash'] else 'create'
            salt = secrets.token_hex(16)
            digest = self.password_hash(password, salt)
            cursor = conn.execute(
                '''INSERT INTO customer_account_requests
                   (customer_key,customer_name,request_type,salt,password_hash,rounds,created_at,channel_id,guild_id)
                   VALUES(?,?,?,?,?,?,?,?,?)''',
                (key, customer['display_name'], request_type, salt, digest, ROUNDS, utc_now(),
                 route['channel_id'], route['guild_id']),
            )
            conn.execute(
                'INSERT INTO audit_log(action,details,created_at) VALUES(?,?,?)',
                ('website_account_requested', f'request={cursor.lastrowid};customer={key};type={request_type}', utc_now()),
            )
            return dict(conn.execute(
                'SELECT * FROM customer_account_requests WHERE id=?', (cursor.lastrowid,)
            ).fetchone())

    def pending(self, unsent=False):
        where = "WHERE status='pending'" + (' AND message_id IS NULL' if unsent else '')
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute(
                f'SELECT * FROM customer_account_requests {where} ORDER BY id'
            )]

    def get_request(self, request_id):
        with self.db.connect() as conn:
            row = conn.execute('SELECT * FROM customer_account_requests WHERE id=?', (int(request_id),)).fetchone()
        return dict(row) if row else None

    def request_notified(self, request_id, message_id):
        with self.db.connect() as conn:
            conn.execute('UPDATE customer_account_requests SET message_id=? WHERE id=?',
                         (str(message_id), int(request_id)))

    def resolve_request(self, request_id, decision, staff_id, staff_name):
        if decision not in ('approved', 'rejected'):
            raise ValueError('Invalid account action.')
        request_id = int(request_id)
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM customer_account_requests WHERE id=?', (request_id,)).fetchone()
            if not row:
                raise ValueError('Account request not found.')
            if row['status'] != 'pending':
                raise ValueError('This account request has already been processed.')
            if decision == 'approved':
                conn.execute(
                    '''INSERT INTO customer_accounts(customer_key,salt,password_hash,rounds,updated_at)
                       VALUES(?,?,?,?,?) ON CONFLICT(customer_key) DO UPDATE SET
                       salt=excluded.salt,password_hash=excluded.password_hash,rounds=excluded.rounds,
                       activation_hash=NULL,activation_expires=NULL,failures=0,locked_until=0,
                       updated_at=excluded.updated_at''',
                    (row['customer_key'], row['salt'], row['password_hash'], row['rounds'], utc_now()),
                )
                conn.execute('DELETE FROM customer_sessions WHERE customer_key=?', (row['customer_key'],))
            conn.execute(
                '''UPDATE customer_account_requests SET status=?,resolved_at=?,resolved_by=?,
                   salt='',password_hash='' WHERE id=?''',
                (decision, utc_now(), str(staff_id), request_id),
            )
            conn.execute(
                'INSERT INTO audit_log(action,staff_id,staff_name,details,created_at) VALUES(?,?,?,?,?)',
                ('website_account_' + decision, str(staff_id), staff_name,
                 f'request={request_id};customer={row["customer_key"]};type={row["request_type"]}', utc_now()),
            )
        return self.get_request(request_id)
