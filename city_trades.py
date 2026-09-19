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
            if offered == wanted or offered not in NAMES or wanted not in NAMES:
                raise ValueError('Choose two different business stickers.')
            if self._rarity(c, campaign, offered) != self._rarity(c, campaign, wanted):
                raise ValueError('Both stickers must have the same rarity.')
            if self._spares(c, campaign, owner, offered) < 1:
                raise ValueError('You need an unreserved duplicate. Your first copy always stays on your board.')
            if c.execute("SELECT COUNT(*) FROM city_run_trades WHERE maker=? AND status='open'", (owner,)).fetchone()[0] >= 5:
                raise ValueError('You can have five open offers. Cancel an offer before listing another.')
            expires = (datetime.fromisoformat(now) + timedelta(hours=24)).isoformat()
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
            if not campaign:
                return {'active': False, 'pieces': [], 'offers': [], 'history': [], 'owner': owner}
            cid = campaign['id']
            pieces = []
            for b in BUSINESSES:
                row = c.execute('SELECT rarity FROM city_run_inventory WHERE campaign_id=? AND business_key=?', (cid, b['key'])).fetchone()
                pieces.append({**b, 'rarity': row[0] if row else 'unassigned', 'spares': self._spares(c, cid, owner, b['key'])})
            offers = [dict(r) for r in c.execute("SELECT t.*,c.display_name FROM city_run_trades t JOIN customers c ON c.customer_key=t.maker WHERE campaign_id=? AND status='open' ORDER BY (t.maker=?) DESC,t.id DESC LIMIT 100", (cid, owner))]
            history = [dict(r) for r in c.execute("SELECT * FROM city_run_trades WHERE campaign_id=? AND (maker=? OR taker=?) ORDER BY id DESC LIMIT 30", (cid, owner, owner))]
            return {'active': campaign['status']=='active', 'pieces': pieces, 'offers': offers, 'history': history, 'owner': owner}


def exchange_html(store, owner, token):
    data = store.snapshot(owner)
    esc = lambda value: html.escape(str(value), quote=True)
    hidden = f'<input type="hidden" name="city_run_request_key" value="{esc(token)}">'
    pieces = {p['key']: p for p in data['pieces']}
    def art(key):
        return f'<figure><img src="/city-art/{esc(key)}.svg?v=individual-art-2" alt="{esc(NAMES[key])}"><figcaption>{esc(NAMES[key])}</figcaption></figure>'
    cards = []
    for offer in data['offers']:
        mine = offer['maker'] == data['owner']
        eligible = data['active'] and pieces[offer['wanted']]['spares'] > 0 and pieces[offer['offered']]['rarity'] == pieces[offer['wanted']]['rarity']
        form = f'<form method="post" action="/city-run-trade">{hidden}<input type="hidden" name="trade_id" value="{offer["id"]}"><button name="action" value="cancel">Cancel my offer</button></form>' if mine else (f'<details><summary>Review swap</summary><p>You give one {esc(NAMES[offer["wanted"]])} and receive one {esc(NAMES[offer["offered"]])}. Your first copies stay on your board.</p><form method="post" action="/city-run-trade">{hidden}<input type="hidden" name="trade_id" value="{offer["id"]}"><button name="action" value="accept">Confirm this swap</button></form></details>' if eligible else '<p class="muted">Requested duplicate needed · or trading paused</p>')
        cards.append(f'<article class="swap-card"><header><b>{"YOUR OFFER" if mine else esc(offer["display_name"])}</b><span>{esc(pieces[offer["offered"]]["rarity"].replace("_"," "))}</span></header><div class="swap-pair"><div><small>YOU {"GIVE" if mine else "RECEIVE"}</small>{art(offer["offered"])}</div><b aria-hidden="true">⇄</b><div><small>YOU {"RECEIVE" if mine else "GIVE"}</small>{art(offer["wanted"])}</div></div><small>Expires {esc(offer["expires_at"][:16].replace("T"," "))} UTC · #{offer["id"]}</small>{form}</article>')
    spare_options = ''.join(f'<option value="{esc(p["key"])}" data-rarity="{esc(p["rarity"])}">{esc(p["name"])} · {p["spares"]} spare · {esc(p["rarity"].replace("_"," "))}</option>' for p in data['pieces'] if p['spares']>0 and any(q['key']!=p['key'] and q['rarity']==p['rarity'] for q in data['pieces']))
    wanted_options = ''.join(f'<option value="{esc(p["key"])}" data-rarity="{esc(p["rarity"])}">{esc(p["name"])} · {esc(p["rarity"].replace("_"," "))}</option>' for p in data['pieces'])
    listing = f'''<form method="post" action="/city-run-trade" id="swap-create">{hidden}<input type="hidden" name="request_key" value="{secrets.token_urlsafe(24)}"><label>I give one duplicate<select name="offered" id="swap-give" required>{spare_options}</select></label><label>I want in return<select name="wanted" id="swap-want" required>{wanted_options}</select></label><div class="swap-pair" id="swap-preview"></div><p>Your offer is public to signed-in players. One duplicate is reserved until it is accepted, cancelled or expires.</p><button name="action" value="create">Publish 24-hour offer</button></form>''' if spare_options and data['active'] else '<p>Reveal stickers to find duplicates. Your spare copies will appear here when trading is active and another business has the same rarity. Stickers with no same-rarity match cannot be listed.</p>'
    history = ''.join(f'<li>#{r["id"]} · {esc(NAMES[r["offered"]])} ⇄ {esc(NAMES[r["wanted"]])} <b>{esc(r["status"])}</b></li>' for r in data['history']) or '<li>Your swap activity will appear here.</li>'
    return '''<style>.exchange{max-width:1000px;margin:auto}.exchange-hero{padding:24px;border:1px solid #e9c45b;border-radius:22px;background:radial-gradient(ellipse at top right,#81621d88,transparent 65%),#101319}.exchange h1{font-size:clamp(28px,6vw,44px);margin:8px 0}.exchange .kicker{color:#ffd65b;letter-spacing:2px;font-weight:900}.swap-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,310px),1fr));gap:14px}.swap-card,.swap-create{padding:16px;margin:15px 0;border:1px solid #685632;border-radius:18px;background:#12151b}.swap-card header{display:flex;justify-content:space-between;gap:10px}.swap-card header span{color:#ffd65b;text-transform:uppercase;font-size:11px}.swap-pair{display:grid;grid-template-columns:minmax(0,1fr) 24px minmax(0,1fr);align-items:center;gap:8px;text-align:center;margin:14px 0}.swap-pair figure{margin:0}.swap-pair img{width:100%;height:145px;object-fit:contain;border-radius:12px;background:#080a0e}.swap-pair figcaption{font-size:13px;font-weight:800;margin-top:8px;min-height:36px}.swap-pair small{color:#e6c977;font-size:10px;letter-spacing:1px}.exchange select{width:100%;margin:8px 0 16px}.exchange summary{cursor:pointer;font-weight:800;color:#ffd65b;padding:12px 0}.exchange button{width:100%}.exchange .muted,.exchange p{color:#c6c2b9}.exchange li{margin-bottom:10px}.exchange a{color:#ffd65b}.swap-create p{font-size:13px}</style>''' + f'''<main class="exchange"><a href="/account#city-run">← Back to my board</a><section class="exchange-hero"><span class="kicker">SNR CITY RUN · STICKER EXCHANGE</span><h1>Your spare. Their missing piece.</h1><p>Swap duplicates with other players. One sticker for one sticker, matching rarity. Your first copy always stays safe.</p><strong>{sum(p['spares'] for p in data['pieces'])} available duplicates · {len(data['offers'])} recent open offers</strong>{'' if data['active'] else '<p>Trading is paused until the season is active.</p>'}</section><details class="swap-create"><summary>＋ Make a swap offer</summary>{listing}</details><h2>Open offers</h2><div class="swap-grid">{''.join(cards) or '<article class="swap-card"><h3>Start the exchange</h3><p>No open offers yet. List a spare sticker and choose the business you need.</p></article>'}</div><details class="swap-create"><summary>My offers &amp; swap history</summary><ul>{history}</ul></details><a href="/city-run-trades">Refresh offers</a></main>''' + '''<script>(()=>{const give=document.getElementById('swap-give'),want=document.getElementById('swap-want'),preview=document.getElementById('swap-preview');if(!give)return;function draw(){preview.replaceChildren();[give,want].forEach((select,i)=>{if(i){const arrow=document.createElement('b');arrow.textContent='⇄';preview.append(arrow)}const fig=document.createElement('figure'),img=document.createElement('img'),cap=document.createElement('figcaption');img.src='/city-art/'+encodeURIComponent(select.value)+'.svg?v=individual-art-2';cap.textContent=select.selectedOptions[0]?.textContent.split(' · ')[0]||'Choose a sticker';img.alt=cap.textContent;fig.append(img,cap);preview.append(fig)})}function filter(){const rarity=give.selectedOptions[0].dataset.rarity;for(const o of want.options){o.disabled=o.dataset.rarity!==rarity||o.value===give.value;o.hidden=o.disabled}if(!want.selectedOptions.length||want.selectedOptions[0].disabled)want.value=[...want.options].find(o=>!o.disabled)?.value||'';draw()}give.addEventListener('change',filter);want.addEventListener('change',draw);filter()})();</script>'''
