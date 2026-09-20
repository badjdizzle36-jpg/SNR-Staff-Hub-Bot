"""Atomic, season-scoped duplicate exchange. No currency or minted stickers."""
import html
import json
import secrets
from datetime import datetime, timedelta
from city_run import BUSINESSES, queue_completions, queue_event
from snr_core import normalize_name, utc_now

NAMES = {b['key']: b['name'] for b in BUSINESSES}

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
        if not r or r['status'] != 'active':
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

    def snapshot(self, owner):
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


def exchange_html(store, owner, token):
    data = store.snapshot(owner)
    esc = lambda value: html.escape(str(value), quote=True)
    hidden = f'<input type="hidden" name="city_run_request_key" value="{esc(token)}">'
    pieces = {p['key']: p for p in data['pieces']}

    rarity_score = {'common': '82', 'rare': '89', 'ultra_rare': '97'}
    def art_url(key):
        # This route uses the current 941x1672 individual artwork manifest, so
        # marketplace cards cannot drift from the reveal and board identity.
        return f'/city-card/{esc(key)}.png?v=market-art-1'

    def market_card(key, copies=0, label='AVAILABLE'):
        piece = pieces[key]
        rarity = piece['rarity']
        rarity_class = 'ultra' if rarity == 'ultra_rare' else rarity
        rarity_name = rarity.replace('_', ' ').upper()
        return (f'<article class="fut-card {esc(rarity_class)}" aria-label="{esc(NAMES[key])}, {esc(rarity_name)}">'
                '<div class="fut-inner">'
                f'<header><span class="rating">{rarity_score.get(rarity, "80")}<small>{esc(rarity_name)}</small></span>'
                f'<span class="copy-count">{esc(label)}{f" · ×{copies}" if copies else ""}</span></header>'
                f'<img src="{art_url(key)}" alt="{esc(NAMES[key])} business sticker artwork">'
                f'<strong>{esc(NAMES[key])}</strong><span>{esc(piece["collection_name"])}</span>'
                f'<footer><b>1<small>BOARD</small></b><b>{copies}<small>SPARE</small></b><b>{esc(rarity_name)}<small>RARITY</small></b></footer>'
                '</div></article>')

    cards = []
    for offer in data['offers']:
        mine = offer['maker'] == data['owner']
        eligible = (data['active'] and pieces[offer['wanted']]['spares'] > 0 and
                    pieces[offer['offered']]['rarity'] == pieces[offer['wanted']]['rarity'])
        if mine:
            action = (f'<form method="post" action="/city-run-trade">{hidden}'
                      f'<input type="hidden" name="trade_id" value="{offer["id"]}">'
                      '<button class="market-btn danger" name="action" value="cancel">Cancel my offer</button></form>')
        elif eligible:
            action = (f'<form method="post" action="/city-run-trade">{hidden}'
                      f'<input type="hidden" name="trade_id" value="{offer["id"]}">'
                      '<button class="market-btn primary" name="action" value="accept">Confirm this swap</button></form>')
        else:
            action = '<button class="market-btn" type="button" disabled>Requested duplicate needed</button>'
        rarity = pieces[offer['offered']]['rarity']
        cards.append(
            f'<article class="offer-slide" data-rarity="{esc(rarity)}">'
            f'<div class="offer-owner"><span>{"YOUR OFFER" if mine else "LISTED BY " + esc(offer["display_name"])}</span>'
            f'<b>#{offer["id"]}</b></div>{market_card(offer["offered"], 1, "ON OFFER")}'
            '<section class="swap-review">'
            f'<div><small>{"YOU OFFER" if mine else "YOU RECEIVE"}</small><img src="{art_url(offer["offered"])}" alt="{esc(NAMES[offer["offered"]])}"><b>{esc(NAMES[offer["offered"]])}</b></div>'
            '<strong aria-hidden="true">⇄</strong>'
            f'<div><small>{"YOU WANT" if mine else "YOU GIVE"}</small><img src="{art_url(offer["wanted"])}" alt="{esc(NAMES[offer["wanted"]])}"><b>{esc(NAMES[offer["wanted"]])}</b></div>'
            f'</section><p class="expiry">Expires {esc(offer["expires_at"][:16].replace("T", " "))} UTC</p>{action}</article>')

    spare_options = ''.join(f'<option value="{esc(p["key"])}" data-rarity="{esc(p["rarity"])}">{esc(p["name"])} · {p["spares"]} spare · {esc(p["rarity"].replace("_"," "))}</option>' for p in data['pieces'] if p['spares']>0 and any(q['key']!=p['key'] and q['rarity']==p['rarity'] for q in data['pieces']))
    wanted_options = ''.join(f'<option value="{esc(p["key"])}" data-rarity="{esc(p["rarity"])}">{esc(p["name"])} · {esc(p["rarity"].replace("_"," "))}</option>' for p in data['pieces'])
    if spare_options and data['active']:
        listing = (f'<form method="post" action="/city-run-trade" id="swap-create">{hidden}'
                   f'<input type="hidden" name="request_key" value="{secrets.token_urlsafe(24)}">'
                   f'<label>I give one duplicate<select name="offered" id="swap-give" required>{spare_options}</select></label>'
                   f'<label>I want in return<select name="wanted" id="swap-want" required>{wanted_options}</select></label>'
                   '<div class="builder-preview" id="swap-preview"></div>'
                   '<p>Only matching rarities appear. Your first copy stays on your board and the duplicate is reserved safely.</p>'
                   f'<button class="market-btn primary" name="action" value="create">Publish {data["settings"]["expiry_hours"]}-hour offer</button></form>')
    else:
        listing = ('<div class="empty-state"><b>No tradeable duplicate is ready.</b><p>Reveal more stickers, or wait until the season and marketplace are active. '
                   'A rarity needs at least two different businesses before it can be traded.</p></div>')

    history_cards = []
    for row in data['history']:
        history_cards.append(
            f'<article class="history-card"><span class="status {esc(row["status"])}">{esc(row["status"].upper())}</span>'
            f'<div><img src="{art_url(row["offered"])}" alt="{esc(NAMES[row["offered"]])}"><b>{esc(NAMES[row["offered"]])}</b></div>'
            '<strong aria-hidden="true">⇄</strong>'
            f'<div><img src="{art_url(row["wanted"])}" alt="{esc(NAMES[row["wanted"]])}"><b>{esc(NAMES[row["wanted"]])}</b></div>'
            f'<small>Offer #{row["id"]}</small></article>')

    disabled_notice = ('' if data['active'] else
        '<div class="market-notice">Trading is currently unavailable. The season or Marketplace may be paused, or your account may be restricted. You can still review your activity.</div>')
    empty_offers = '<div class="empty-state"><b>No live offers yet.</b><p>Be the first player to place a duplicate on the Marketplace.</p></div>'
    styles = '''<style>
.market{--gold:#f5c95e;--red:#ff3a52;--panel:#12151b;--line:#353945;max-width:980px;margin:auto;color:#f7f4ec}.market *{box-sizing:border-box}.market a{color:var(--gold)}
.market-hero{padding:22px;border-radius:22px;background:radial-gradient(circle at 86% 0,#77471988,transparent 45%),linear-gradient(145deg,#211510,#0a0c11 68%);border:1px solid #735d2d}.market-hero .kicker{color:var(--gold);letter-spacing:2px;font-weight:900}.market-hero h1{font-size:clamp(28px,7vw,46px);line-height:1;margin:12px 0 7px}.market-hero p{margin:0;color:#bcb8af}.market-stats{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.market-stats span{padding:7px 10px;border-radius:99px;background:#ffffff10;font-size:12px;font-weight:800}
.market-tabs{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin:14px 0}.market-tab{min-height:46px;border:0;border-radius:12px;background:#191b22;color:#a5a5aa;font-weight:900}.market-tab[aria-selected="true"]{color:#ffe38c;background:#30231b;box-shadow:inset 0 0 0 1px #c53847}.market-panel[hidden]{display:none}
.rarity-filters{display:flex;gap:7px;overflow:auto;padding:2px 0 12px;scrollbar-width:none}.rarity-filter{flex:none;min-height:39px;padding:0 14px;border:1px solid #353741;border-radius:99px;background:#111319;color:#aaaab0;font-weight:900}.rarity-filter[aria-pressed="true"]{border-color:#d7a936;background:#5a4218;color:#fff0b0}
.offer-carousel{position:relative;overflow:hidden;min-height:650px}.offer-deck{display:flex;align-items:flex-start;transition:transform .38s cubic-bezier(.2,.8,.2,1);touch-action:pan-y}.offer-slide{flex:0 0 min(78%,370px);padding:8px;opacity:.42;transform:scale(.9);transition:.38s}.offer-slide.active{opacity:1;transform:scale(1)}.offer-owner{display:flex;justify-content:space-between;gap:10px;margin:0 8px 8px;color:#e5c66e;font-size:11px;font-weight:900;letter-spacing:1px}
.fut-card{position:relative;padding:10px;clip-path:polygon(8% 0,92% 0,100% 8%,96% 89%,82% 100%,18% 100%,4% 89%,0 8%);background:linear-gradient(150deg,#fff0a0,#9b6b18 20%,#201707 52%,#d7a52d 87%,#fff2b1);filter:drop-shadow(0 16px 18px #0009)}.fut-card.rare{background:linear-gradient(150deg,#c9fbff,#1387a5 20%,#071926 52%,#37bed7 87%,#d7fdff)}.fut-card.ultra{background:linear-gradient(150deg,#ff9cad,#d71d3c 18%,#17070c 50%,#edb13b 86%,#fff0a8)}
.fut-inner{padding:15px 14px 24px;clip-path:inherit;background:radial-gradient(circle at 50% 26%,#ffffff20,transparent 36%),linear-gradient(#191a1e,#08090d);text-align:center}.fut-inner header{display:flex;align-items:flex-start;justify-content:space-between}.rating{font-size:28px;font-weight:900;color:#ffe086;line-height:1}.rating small{display:block;margin-top:4px;font-size:9px;letter-spacing:1px}.copy-count{padding:6px 8px;border-radius:9px;background:#000b;font-size:10px;font-weight:900}.fut-inner>img{display:block;width:100%;height:270px;object-fit:contain;margin:7px 0;border-radius:13px;background:#08090d}.fut-inner>strong,.fut-inner>span{display:block}.fut-inner>strong{font-size:18px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.fut-inner>span{margin-top:4px;color:#bbb7ae;font-size:11px;letter-spacing:1px;text-transform:uppercase}.fut-inner footer{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;margin-top:13px;padding-top:10px;border-top:1px solid #ffffff22}.fut-inner footer b{font-size:11px;overflow:hidden;text-overflow:ellipsis}.fut-inner footer small{display:block;color:#8f8c85;font-size:8px;letter-spacing:1px}
.carousel-arrow{position:absolute;z-index:4;top:300px;width:44px;height:44px;border:0;border-radius:50%;background:var(--gold);color:#211603;font-size:25px;font-weight:900;box-shadow:0 8px 24px #0009}.carousel-arrow.prev{left:4px}.carousel-arrow.next{right:4px}.carousel-dots{display:flex;justify-content:center;gap:7px;margin:6px 0 13px}.carousel-dot{width:8px;height:8px;border:0;border-radius:10px;background:#3a3c44;padding:0}.carousel-dot.active{width:25px;background:var(--gold)}
.swap-review,.history-card,.builder-preview{display:grid;grid-template-columns:minmax(0,1fr) 30px minmax(0,1fr);align-items:center;gap:8px;text-align:center;margin:13px 0;padding:12px;border-radius:16px;background:#12151b;border:1px solid var(--line)}.swap-review div,.history-card div,.builder-preview figure{min-width:0;margin:0}.swap-review img,.history-card img,.builder-preview img{display:block;width:100%;height:115px;object-fit:contain;border-radius:10px;background:#08090d}.swap-review small,.builder-preview small{display:block;color:#e7c96e;font-size:9px;letter-spacing:1px}.swap-review b,.history-card b,.builder-preview figcaption{display:block;margin-top:6px;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.swap-review>strong,.history-card>strong,.builder-preview>b{font-size:24px;color:var(--gold)}.expiry{text-align:center;color:#999;font-size:11px}.market-btn{width:100%;min-height:48px;border:1px solid #41444f;border-radius:12px;background:#20232b;color:#f5f2e9;font-weight:900}.market-btn.primary{border:0;background:linear-gradient(135deg,#f5d16a,#d39421);color:#201505}.market-btn.danger{border-color:#9a3140;background:#35141b;color:#ffb1bc}.market-btn:disabled{opacity:.48}
.offer-builder,.activity-wrap{padding:17px;border:1px solid #56492e;border-radius:18px;background:#111319}.offer-builder label{display:block;margin-bottom:13px;color:#e5c66e;font-size:12px;font-weight:900}.offer-builder select{width:100%;margin-top:7px}.offer-builder p{color:#aaa69f;font-size:12px}.history-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,285px),1fr));gap:10px}.history-card{position:relative;margin:0}.history-card .status{position:absolute;top:8px;right:8px;padding:5px 7px;border-radius:99px;background:#333642;color:#ddd;font-size:9px;font-weight:900}.history-card .status.accepted{background:#174d36;color:#adf0d1}.history-card small{grid-column:1/-1;color:#85858b}.empty-state,.market-notice{padding:22px;border:1px solid #46413a;border-radius:16px;background:#121419;text-align:center}.empty-state p,.market-notice{color:#b7b3aa}.market-notice{margin:12px 0;border-color:#8e6826;background:#2b2110}.market-bottom{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-top:14px;font-size:12px}
@media(max-width:520px){.market{margin:-10px}.market-hero{border-radius:0;padding:19px 15px}.market-tabs{padding:0 12px}.market-panel{padding:0 12px}.offer-carousel{min-height:620px;margin:0 -12px}.offer-slide{flex-basis:82%;padding:6px}.fut-inner>img{height:235px}.carousel-arrow{top:265px}.swap-review img,.history-card img,.builder-preview img{height:95px}.market-bottom{padding:0 12px 10px}.history-grid{display:block}.history-card{margin-bottom:10px}}
</style>'''
    body = f'''<main class="market"><a href="/account#city-run">← Back to my City Run board</a>
<section class="market-hero"><span class="kicker">SNR CITY RUN</span><h1>The Marketplace</h1><p>Swipe the live business cards. Trade one duplicate for one missing business of the same rarity.</p><div class="market-stats"><span>{sum(p['spares'] for p in data['pieces'])} YOUR SPARES</span><span>{len(data['offers'])} LIVE OFFERS</span><span>FIRST COPIES PROTECTED</span></div></section>{disabled_notice}
<nav class="market-tabs" aria-label="Marketplace pages"><button class="market-tab" type="button" data-market-tab="browse" aria-selected="true">Browse</button><button class="market-tab" type="button" data-market-tab="make" aria-selected="false">Make offer</button><button class="market-tab" type="button" data-market-tab="activity" aria-selected="false">My activity</button></nav>
<section class="market-panel" data-market-panel="browse"><div class="rarity-filters"><button class="rarity-filter" type="button" data-rarity="all" aria-pressed="true">All cards</button><button class="rarity-filter" type="button" data-rarity="common" aria-pressed="false">Common</button><button class="rarity-filter" type="button" data-rarity="rare" aria-pressed="false">Rare</button><button class="rarity-filter" type="button" data-rarity="ultra_rare" aria-pressed="false">Ultra rare</button></div><div class="offer-carousel"><button class="carousel-arrow prev" type="button" aria-label="Previous offer">‹</button><div class="offer-deck">{''.join(cards)}</div><button class="carousel-arrow next" type="button" aria-label="Next offer">›</button></div><div class="carousel-dots" aria-label="Offer position"></div><div class="offer-empty" {'hidden' if cards else ''}>{empty_offers}</div></section>
<section class="market-panel" data-market-panel="make" hidden><div class="offer-builder"><h2>Build your trade</h2>{listing}</div></section>
<section class="market-panel" data-market-panel="activity" hidden><div class="activity-wrap"><h2>My offers &amp; swap history</h2><div class="history-grid">{''.join(history_cards) or '<div class="empty-state"><b>No swap activity yet.</b><p>Your offers and completed swaps will appear here with their card artwork.</p></div>'}</div></div></section>
<div class="market-bottom"><a href="/account#city-run">My board</a><a href="/city-run-trades">Refresh Marketplace</a></div></main>'''
    script = '''<script>(()=>{const root=document.querySelector('.market');if(!root)return;
const panels=[...root.querySelectorAll('[data-market-panel]')],tabs=[...root.querySelectorAll('[data-market-tab]')];tabs.forEach(tab=>tab.addEventListener('click',()=>{tabs.forEach(t=>t.setAttribute('aria-selected',String(t===tab)));panels.forEach(p=>p.hidden=p.dataset.marketPanel!==tab.dataset.marketTab)}));
const deck=root.querySelector('.offer-deck'),all=[...root.querySelectorAll('.offer-slide')],dots=root.querySelector('.carousel-dots'),empty=root.querySelector('.offer-empty');let visible=[...all],index=0,startX=0;
function render(){all.forEach(s=>s.classList.remove('active'));if(!visible.length){deck.style.transform='';dots.replaceChildren();empty.hidden=false;return}empty.hidden=true;index=Math.max(0,Math.min(index,visible.length-1));const active=visible[index];active.classList.add('active');const width=deck.parentElement.clientWidth,card=active.offsetWidth;deck.style.transform=`translateX(${width/2-(index+.5)*card}px)`;dots.replaceChildren(...visible.map((_,i)=>{const b=document.createElement('button');b.type='button';b.className='carousel-dot'+(i===index?' active':'');b.setAttribute('aria-label',`Show offer ${i+1}`);b.addEventListener('click',()=>{index=i;render()});return b}))}
function move(step){if(!visible.length)return;index=(index+step+visible.length)%visible.length;render()}root.querySelector('.carousel-arrow.prev').addEventListener('click',()=>move(-1));root.querySelector('.carousel-arrow.next').addEventListener('click',()=>move(1));deck.addEventListener('pointerdown',e=>startX=e.clientX);deck.addEventListener('pointerup',e=>{if(Math.abs(e.clientX-startX)>35)move(e.clientX<startX?1:-1)});
root.querySelectorAll('.rarity-filter').forEach(btn=>btn.addEventListener('click',()=>{root.querySelectorAll('.rarity-filter').forEach(b=>b.setAttribute('aria-pressed','false'));btn.setAttribute('aria-pressed','true');visible=all.filter(s=>btn.dataset.rarity==='all'||s.dataset.rarity===btn.dataset.rarity);all.forEach(s=>s.style.display=visible.includes(s)?'block':'none');index=0;render()}));
const give=root.querySelector('#swap-give'),want=root.querySelector('#swap-want'),preview=root.querySelector('#swap-preview');function draw(){if(!give)return;preview.replaceChildren();[give,want].forEach((select,i)=>{if(i){const arrow=document.createElement('b');arrow.textContent='⇄';preview.append(arrow)}const fig=document.createElement('figure'),small=document.createElement('small'),img=document.createElement('img'),cap=document.createElement('figcaption');small.textContent=i?'YOU RECEIVE':'YOU GIVE';img.src='/city-card/'+encodeURIComponent(select.value)+'.png?v=market-art-1';cap.textContent=select.selectedOptions[0]?.textContent.split(' · ')[0]||'Choose a sticker';img.alt=cap.textContent;fig.append(small,img,cap);preview.append(fig)})}function filterWanted(){const rarity=give.selectedOptions[0].dataset.rarity;for(const option of want.options){option.disabled=option.dataset.rarity!==rarity||option.value===give.value;option.hidden=option.disabled}if(!want.selectedOptions.length||want.selectedOptions[0].disabled)want.value=[...want.options].find(o=>!o.disabled)?.value||'';draw()}if(give){give.addEventListener('change',filterWanted);want.addEventListener('change',draw);filterWanted()}addEventListener('resize',render);render()})();</script>'''
    return styles + body + script
