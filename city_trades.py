"""Atomic, season-scoped duplicate exchange. No currency or minted stickers."""
import html
import json
import secrets
from datetime import datetime, timedelta
from city_run import BUSINESSES, queue_completions, queue_event
from snr_core import normalize_name, utc_now

NAMES = {b['key']: b['name'] for b in BUSINESSES}

# Served as /city-marketplace.js. Keeping marketplace behaviour in an external
# same-origin asset allows the portal's strict Content-Security-Policy to remain
# enabled while every interactive control still works.
from compact_market import MARKETPLACE_JS, render_market, feed_html, normal_options, PAGE_SIZE

class TradeStore:
    def __init__(self, city):
        self.city, self.db = city, city.db
        with self.db.connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS city_run_trades (
                id INTEGER PRIMARY KEY, campaign_id INTEGER NOT NULL REFERENCES city_run_campaigns(id),
                maker TEXT NOT NULL REFERENCES customers(customer_key),
                offered TEXT NOT NULL REFERENCES city_run_businesses(business_key),
                wanted TEXT NOT NULL REFERENCES city_run_businesses(business_key),
                status TEXT NOT NULL DEFAULT 'open', taker TEXT REFERENCES customers(customer_key),
                request_key TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                resolved_at TEXT, UNIQUE(maker,request_key))''')
            c.execute('CREATE INDEX IF NOT EXISTS city_trade_open ON city_run_trades(campaign_id,status,maker,offered)')
            c.execute('''CREATE TABLE IF NOT EXISTS city_run_trade_settings (
                id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 1,
                max_offers INTEGER NOT NULL DEFAULT 5, expiry_hours INTEGER NOT NULL DEFAULT 24)''')
            c.execute('INSERT OR IGNORE INTO city_run_trade_settings(id) VALUES(1)')
            c.execute('''CREATE TABLE IF NOT EXISTS city_run_trade_blocks (
                customer_key TEXT PRIMARY KEY REFERENCES customers(customer_key),
                reason TEXT NOT NULL, staff_id TEXT NOT NULL, updated_at TEXT NOT NULL)''')

    def settings(self, connection=None):
        if connection is not None:
            return dict(connection.execute('SELECT * FROM city_run_trade_settings WHERE id=1').fetchone())
        with self.db.connect() as c:
            return self.settings(c)

    def _check_access(self, c, owner):
        if not self.settings(c)['enabled']:
            raise ValueError('The marketplace is paused by an owner. You can still cancel your offers.')
        if c.execute('SELECT 1 FROM city_run_trade_blocks WHERE customer_key=?', (owner,)).fetchone():
            raise ValueError('Trading access is restricted for this account. Contact SNR staff.')

    def _admin_audit(self, c, action, staff_id, staff_name, details):
        if not str(staff_id).strip():
            raise ValueError('An owner identity is required.')
        now = utc_now()
        ident = c.execute('INSERT INTO audit_log(action,staff_id,staff_name,details,created_at) VALUES(?,?,?,?,?)',
                          ('marketplace_'+action, str(staff_id), str(staff_name), json.dumps(details), now)).lastrowid
        queue_event(c, f'marketplace-admin:{ident}',
                    f'Marketplace admin: {staff_name} — {action.replace("_", " ")}. {json.dumps(details)[:1200]}', now)

    @staticmethod
    def _reason(reason):
        reason = str(reason).strip()
        if not 3 <= len(reason) <= 250:
            raise ValueError('Enter a reason between 3 and 250 characters.')
        return reason

    def admin_configure(self, enabled, max_offers, expiry_hours, staff_id, staff_name, reason):
        reason = self._reason(reason)
        max_offers, expiry_hours = int(max_offers), int(expiry_hours)
        if not isinstance(enabled, bool) or not 1 <= max_offers <= 20 or not 1 <= expiry_hours <= 168:
            raise ValueError('Use 1–20 offers per player and 1–168 hours per new offer.')
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old = self.settings(c)
            c.execute('UPDATE city_run_trade_settings SET enabled=?,max_offers=?,expiry_hours=? WHERE id=1',
                      (int(enabled), max_offers, expiry_hours))
            self._admin_audit(c, 'settings', staff_id, staff_name,
                              {'before': old, 'after': self.settings(c), 'reason': reason})

    def admin_cancel(self, trade_id, staff_id, staff_name, reason):
        reason = self._reason(reason)
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            self._expire(c, utc_now())
            row = c.execute('SELECT * FROM city_run_trades WHERE id=?', (int(trade_id),)).fetchone()
            if not row or row['status'] != 'open':
                raise ValueError('Only an open offer can be cancelled. Completed swaps are kept in the history.')
            c.execute("UPDATE city_run_trades SET status='cancelled',resolved_at=? WHERE id=?", (utc_now(), trade_id))
            self._admin_audit(c, 'cancel_offer', staff_id, staff_name, {'trade_id': trade_id, 'reason': reason})

    def admin_cancel_all(self, staff_id, staff_name, reason):
        reason = self._reason(reason)
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            self._expire(c, utc_now())
            ids = [r[0] for r in c.execute("SELECT id FROM city_run_trades WHERE status='open'")]
            c.execute("UPDATE city_run_trades SET status='cancelled',resolved_at=? WHERE status='open'", (utc_now(),))
            self._admin_audit(c, 'cancel_all', staff_id, staff_name, {'trade_ids': ids, 'count': len(ids), 'reason': reason})
            return len(ids)

    def admin_access(self, customer, blocked, staff_id, staff_name, reason):
        reason = self._reason(reason)
        key = normalize_name(customer)
        if not isinstance(blocked, bool):
            raise ValueError('Choose restrict or restore.')
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT display_name FROM customers WHERE customer_key=?', (key,)).fetchone()
            if not row:
                raise ValueError('Account not found. Enter the exact character name.')
            if blocked:
                c.execute('INSERT OR REPLACE INTO city_run_trade_blocks VALUES(?,?,?,?)', (key, reason, str(staff_id), utc_now()))
                c.execute("UPDATE city_run_trades SET status='cancelled',resolved_at=? WHERE maker=? AND status='open'", (utc_now(), key))
            else:
                c.execute('DELETE FROM city_run_trade_blocks WHERE customer_key=?', (key,))
            self._admin_audit(c, 'restrict' if blocked else 'restore', staff_id, staff_name, {'customer': key, 'reason': reason})
            return row['display_name']

    def admin_list(self, kind='open', page=0, customer=''):
        page = max(0, int(page))
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            self._expire(c, utc_now())
            if kind == 'restricted':
                sql, args = 'SELECT * FROM city_run_trade_blocks ORDER BY updated_at DESC', []
            elif kind == 'audit':
                sql, args = "SELECT * FROM audit_log WHERE action LIKE 'marketplace_%' ORDER BY id DESC", []
            else:
                filters, args = [], []
                if kind == 'open':
                    filters.append("status='open'")
                elif kind != 'history':
                    raise ValueError('Unknown marketplace list.')
                if customer:
                    filters.append('(maker=? OR taker=?)')
                    args.extend([normalize_name(customer)]*2)
                sql = 'SELECT * FROM city_run_trades' + (' WHERE '+' AND '.join(filters) if filters else '') + ' ORDER BY id DESC'
            rows = [dict(r) for r in c.execute(sql+' LIMIT 6 OFFSET ?', (*args,page*5))]
            counts = {r[0]:r[1] for r in c.execute('SELECT status,COUNT(*) FROM city_run_trades GROUP BY status')}
            return {'rows': rows[:5], 'more': len(rows)>5, 'page':page, 'counts':counts, 'settings':self.settings(c)}

    def _expire(self, c, now):
        c.execute("UPDATE city_run_trades SET status='expired',resolved_at=? WHERE status='open' AND (expires_at<=? OR campaign_id IN (SELECT id FROM city_run_campaigns WHERE status='ended'))", (now, now))

    def _campaign(self, c):
        r = c.execute("SELECT * FROM city_run_campaigns ORDER BY id DESC LIMIT 1").fetchone()
        if not r or r['status'] != 'active' or (r['ends_at'] and c.execute('SELECT julianday(?)<=julianday(?)', (r['ends_at'], utc_now())).fetchone()[0]):
            raise ValueError('Trading is available only while the City Run season is active.')
        return dict(r)

    def _spares(self, c, campaign, owner, key):
        row = c.execute('SELECT copies_owned FROM city_run_customer_cards WHERE campaign_id=? AND customer_key=? AND business_key=?', (campaign, owner, key)).fetchone()
        reserved = c.execute("SELECT COUNT(*) FROM city_run_trades WHERE campaign_id=? AND maker=? AND offered=? AND status='open'", (campaign, owner, key)).fetchone()[0]
        return max(0, (row[0] if row else 0) - 1 - reserved)

    def _rarity(self, c, campaign, key):
        r = c.execute('SELECT rarity FROM city_run_inventory WHERE campaign_id=? AND business_key=?', (campaign, key)).fetchone()
        if not r or r[0] == 'unassigned':
            raise ValueError('Choose a valid business sticker.')
        return r[0]

    def create(self, owner, offered, wanted, request_key):
        owner = normalize_name(owner)
        if not isinstance(request_key, str) or not 16 <= len(request_key) <= 100:
            raise ValueError('Refresh the exchange and try again.')
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            now = utc_now()
            self._expire(c, now)
            previous = c.execute('SELECT * FROM city_run_trades WHERE maker=? AND request_key=?', (owner, request_key)).fetchone()
            if previous:
                return dict(previous)
            campaign = self._campaign(c)['id']
            self._check_access(c, owner)
            if offered == wanted or offered not in NAMES or wanted not in NAMES:
                raise ValueError('Choose two different business stickers.')
            if self._rarity(c, campaign, offered) != self._rarity(c, campaign, wanted):
                raise ValueError('Both stickers must have the same rarity.')
            if self._spares(c, campaign, owner, offered) < 1:
                raise ValueError('You need an unreserved duplicate. Your first copy always stays on your board.')
            settings = self.settings(c)
            if c.execute("SELECT COUNT(*) FROM city_run_trades WHERE maker=? AND status='open'", (owner,)).fetchone()[0] >= settings['max_offers']:
                raise ValueError(f"You can have {settings['max_offers']} open offers. Cancel an offer before listing another.")
            expires = (datetime.fromisoformat(now) + timedelta(hours=settings['expiry_hours'])).isoformat()
            ident = c.execute('INSERT INTO city_run_trades(campaign_id,maker,offered,wanted,request_key,created_at,expires_at) VALUES(?,?,?,?,?,?,?)', (campaign, owner, offered, wanted, request_key, now, expires)).lastrowid
            return dict(c.execute('SELECT * FROM city_run_trades WHERE id=?', (ident,)).fetchone())

    def resolve(self, owner, trade_id, action):
        owner = normalize_name(owner)
        if action not in ('accept', 'cancel'):
            raise ValueError('Unknown trade action.')
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            now = utc_now()
            self._expire(c, now)
            row = c.execute('SELECT * FROM city_run_trades WHERE id=?', (trade_id,)).fetchone()
            if not row:
                raise ValueError('Offer not found.')
            r = dict(row)
            if action == 'cancel':
                if r['maker'] != owner:
                    raise ValueError('You can only cancel your own offers.')
                if r['status'] == 'open':
                    c.execute("UPDATE city_run_trades SET status='cancelled',resolved_at=? WHERE id=?", (now, trade_id))
                return 'Offer closed. Any reserved duplicate is available again.'
            if r['status'] == 'accepted' and r['taker'] == owner:
                return 'Swap already completed. Your board is up to date.'
            campaign = self._campaign(c)
            self._check_access(c, owner)
            self._check_access(c, r['maker'])
            if r['status'] != 'open' or r['campaign_id'] != campaign['id']:
                raise ValueError('This offer is no longer available.')
            if r['maker'] == owner:
                raise ValueError('You cannot accept your own offer.')
            if self._rarity(c, campaign['id'], r['offered']) != self._rarity(c, campaign['id'], r['wanted']):
                raise ValueError('Rarities have changed. The owner must cancel and create a new offer.')
            if self._spares(c, campaign['id'], owner, r['wanted']) < 1:
                raise ValueError('You need an unreserved duplicate of the requested sticker.')
            # Release this reservation inside the same transaction, then recheck
            # the maker against all their other live reservations.
            c.execute("UPDATE city_run_trades SET status='accepted',taker=?,resolved_at=? WHERE id=?", (owner, now, trade_id))
            if self._spares(c, campaign['id'], r['maker'], r['offered']) < 1:
                raise ValueError('The offered duplicate is no longer available.')
            for sender, receiver, key in ((r['maker'], owner, r['offered']), (owner, r['maker'], r['wanted'])):
                changed = c.execute('UPDATE city_run_customer_cards SET copies_owned=copies_owned-1,updated_at=? WHERE campaign_id=? AND customer_key=? AND business_key=? AND copies_owned>1', (now, campaign['id'], sender, key)).rowcount
                if changed != 1:
                    raise ValueError('The collection changed. Refresh the exchange.')
                c.execute('''INSERT INTO city_run_customer_cards VALUES(?,?,?,1,?,?)
                    ON CONFLICT(campaign_id,customer_key,business_key) DO UPDATE SET copies_owned=copies_owned+1,updated_at=excluded.updated_at''', (campaign['id'], receiver, key, now, now))
            for player in (r['maker'], owner):
                queue_completions(c, campaign['id'], player, now)
                name = c.execute('SELECT display_name FROM customers WHERE customer_key=?', (player,)).fetchone()[0]
                count = c.execute('SELECT COUNT(*) FROM city_run_customer_cards WHERE campaign_id=? AND customer_key=? AND copies_owned>0', (campaign['id'], player)).fetchone()[0]
                self.city._sync_corner_awards(campaign, player, name, count, connection=c)
            description = f"Sticker swap #{trade_id}: {r['maker']} exchanged {NAMES[r['offered']]} with {owner} for {NAMES[r['wanted']]}. Both boards updated."
            queue_event(c, f'trade:{trade_id}', description, now)
            c.execute('INSERT INTO audit_log(action,staff_id,staff_name,details,created_at) VALUES(?,?,?,?,?)', ('city_run_trade', owner, owner, json.dumps({'trade_id': trade_id, 'maker': r['maker'], 'taker': owner, 'offered': r['offered'], 'wanted': r['wanted']}), now))
            return 'Swap complete! Your sticker is on your board. Any new route rewards are ready to claim.'

    def page_snapshot(self, owner, options=None):
        data = self.snapshot(owner)
        tab, page, rarity = normal_options(options)
        owner = normalize_name(owner)
        with self.db.connect() as c:
            campaign = c.execute('SELECT id FROM city_run_campaigns ORDER BY id DESC LIMIT 1').fetchone()
            if not campaign:
                data.update(feed={'rows': [], 'page': 1, 'pages': 1, 'total': 0}, live_total=0)
                return data
            cid = campaign['id']
            data['live_total'] = c.execute("SELECT COUNT(*) FROM city_run_trades WHERE campaign_id=? AND status='open' AND maker!=?", (cid,owner)).fetchone()[0]
            condition = "t.campaign_id=?"
            params = [cid]
            if tab == 'activity':
                condition += " AND (t.maker=? OR t.taker=?)"
                params += [owner,owner]
            else:
                condition += " AND t.status='open' AND t.maker!=?"
                params += [owner]
                if rarity != 'all':
                    condition += " AND i.rarity=?"
                    params.append(rarity)
            tables = "city_run_trades t JOIN city_run_inventory i ON i.campaign_id=t.campaign_id AND i.business_key=t.offered JOIN customers u ON u.customer_key=t.maker"
            total = c.execute('SELECT COUNT(*) FROM '+tables+' WHERE '+condition,params).fetchone()[0]
            pages = max(1,(total+PAGE_SIZE-1)//PAGE_SIZE)
            page = min(page,pages)
            rows = [dict(r) for r in c.execute('SELECT t.*,u.display_name FROM '+tables+' WHERE '+condition+' ORDER BY t.id DESC LIMIT ? OFFSET ?',params+[PAGE_SIZE,(page-1)*PAGE_SIZE])]
        data['feed'] = {'rows':rows,'page':page,'pages':pages,'total':total}
        return data

    def snapshot(self, owner):
        self.city.current()  # Close any expired season before showing trade actions.
        owner = normalize_name(owner)
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            self._expire(c, utc_now())
            campaign = c.execute('SELECT * FROM city_run_campaigns ORDER BY id DESC LIMIT 1').fetchone()
            settings = self.settings(c)
            blocked = bool(c.execute('SELECT 1 FROM city_run_trade_blocks WHERE customer_key=?', (owner,)).fetchone())
            if not campaign:
                return {'active': False, 'pieces': [], 'offers': [], 'history': [], 'owner': owner, 'settings':settings, 'blocked':blocked}
            cid = campaign['id']
            pieces = []
            for b in BUSINESSES:
                row = c.execute('SELECT rarity FROM city_run_inventory WHERE campaign_id=? AND business_key=?', (cid, b['key'])).fetchone()
                pieces.append({**b, 'rarity': row[0] if row else 'unassigned', 'spares': self._spares(c, cid, owner, b['key'])})
            offers = [dict(r) for r in c.execute("SELECT t.*,c.display_name FROM city_run_trades t JOIN customers c ON c.customer_key=t.maker WHERE campaign_id=? AND status='open' ORDER BY (t.maker=?) DESC,t.id DESC LIMIT 100", (cid, owner))]
            history = [dict(r) for r in c.execute("SELECT * FROM city_run_trades WHERE campaign_id=? AND (maker=? OR taker=?) ORDER BY id DESC LIMIT 30", (cid, owner, owner))]
            return {'active': campaign['status']=='active' and bool(settings['enabled']) and not blocked, 'pieces': pieces, 'offers': offers, 'history': history, 'owner': owner, 'settings':settings, 'blocked':blocked}


def exchange_html(store, owner, token, options=None):
    data = store.page_snapshot(owner, options)
    return render_market(data, token, options)
