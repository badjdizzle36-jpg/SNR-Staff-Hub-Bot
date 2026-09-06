from __future__ import annotations

import html
import hashlib
import hmac
import secrets
from pathlib import Path
import difflib
import json
import time
from collections import defaultdict, deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from snr_core import SNRDatabase, normalize_name
from reward_claims import ClaimStore


LONDON = ZoneInfo("Europe/London")
MAX_LOOKUPS_PER_MINUTE = 15
LOGO_IMAGE = Path(__file__).with_name("snr-logo.png").read_bytes()


BASE_CSS = """
:root{--gold:#ffbd27;--orange:#ff6a00;--ink:#100b08;--panel:#1b120d;--cream:#fff4dc;--muted:#cdbfae}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(circle at top,#4b1a08 0,#160d08 42%,#090706 100%);color:var(--cream);font-family:Inter,Arial,sans-serif}
.wrap{width:min(680px,92vw);margin:auto;padding:42px 0 54px}.brand{text-align:center;margin-bottom:24px}.logo{font-size:clamp(42px,11vw,72px);line-height:.9;font-weight:1000;font-style:italic;letter-spacing:-3px;color:var(--orange);text-shadow:0 3px 0 #411000,0 0 26px #ff6a0066}.tag{color:var(--gold);font-weight:800;letter-spacing:2px;margin-top:10px}
.card{background:linear-gradient(145deg,#21150f,#130d09);border:1px solid #ffbd2755;border-radius:24px;padding:clamp(22px,5vw,34px);box-shadow:0 20px 60px #0009,0 0 40px #ff7b0014}.label{color:var(--gold);text-transform:uppercase;font-size:12px;font-weight:900;letter-spacing:1.5px}.name{font-size:clamp(28px,7vw,44px);font-weight:950;margin:6px 0 20px}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.stat{background:#ffffff09;border:1px solid #ffffff12;border-radius:16px;padding:17px}.num{font-size:29px;font-weight:950;margin-top:5px}.wide{grid-column:1/-1}.history{margin-top:22px}.sale{display:flex;justify-content:space-between;gap:14px;padding:14px 0;border-bottom:1px solid #ffffff12}.sale:last-child{border:0}.sale small,.muted{color:var(--muted)}
form{display:flex;gap:10px;margin-top:22px}input{min-width:0;flex:1;border:1px solid #ffbd2766;background:#0c0907;color:white;border-radius:14px;padding:16px;font-size:16px;outline:none}input:focus{border-color:var(--gold);box-shadow:0 0 0 3px #ffbd2722}button{border:0;border-radius:14px;background:linear-gradient(135deg,var(--orange),var(--gold));color:#170a02;padding:16px 20px;font-weight:950;font-size:15px;cursor:pointer}.notice{text-align:center;padding:18px;border-radius:14px;background:#ffbd2710;color:var(--muted);margin-top:18px}.back{display:block;text-align:center;color:var(--gold);margin-top:20px;text-decoration:none}.jackpot{border-color:#ffbd2790;background:linear-gradient(135deg,#3a2108,#17100a)}footer{text-align:center;color:#88796c;font-size:12px;margin-top:24px}@media(max-width:430px){.grid{grid-template-columns:1fr}.wide{grid-column:auto}form{flex-direction:column}}
"""


BRAND_CSS = """
:root{--gold:#ffe53b;--orange:#ffb800;--cream:#fff9e8;--muted:#f4d8ce}
body{background:radial-gradient(ellipse at 10% 0%,#f02417 0,transparent 55%),radial-gradient(ellipse at 100% 45%,#9d120c 0,transparent 60%),#350708;background-attachment:fixed;line-height:1.5}
.wrap{width:min(740px,92vw);padding-top:28px}.brand{margin-bottom:24px}
.logo-frame{overflow:hidden;width:min(100%,620px);margin:0 auto 18px;border:2px solid #ffe53b;border-radius:16px;box-shadow:0 12px 36px #1a000077}
.logo-frame img{display:block;width:100%;height:auto}
.tag{display:inline-block;margin:0;padding:7px 22px;background:#ffe53b;color:#7b1009;border-radius:99px;font-size:14px;letter-spacing:2px}
.card{background:linear-gradient(145deg,#73130f,#32090a);border:2px solid #ffd334;border-top:7px solid #ffe53b;box-shadow:0 18px 60px #21000088}
h1{font-size:clamp(27px,6vw,36px);line-height:1.15;margin:10px 0 18px}.label{font-size:14px;letter-spacing:1px}.name{overflow-wrap:anywhere}
.stat{background:linear-gradient(135deg,#b52319,#70120e);border:1px solid #ffb93480}.num{color:#fff8dd}.jackpot{background:linear-gradient(135deg,#ffe33b,#ffa818);color:#51100a;border:2px solid #fff39c}.jackpot .label,.jackpot .muted,.jackpot strong{color:#51100a!important}
input,select{background:#290808!important;border-color:#ffda4e!important}input:focus,select:focus{outline:3px solid #ffe53b;outline-offset:3px}button{background:linear-gradient(135deg,#fff05b,#ffbf18);color:#60100b;box-shadow:0 4px 0 #a3540b;line-height:1.3}button:hover{filter:brightness(1.1)}button:focus-visible,a:focus-visible{outline:3px solid white;outline-offset:4px}
.notice{background:#ffda3514;border:1px solid #ffda3544;color:#fff1d3}.history{padding-top:16px;border-top:1px solid #ffda3533}.sale small{font-size:14px}footer{color:#f2c9b7;font-size:13px}.back{font-weight:700}
@media(max-width:560px){form{flex-direction:column}button{width:100%}.card{padding:22px}.logo-frame{border-radius:14px}.wrap{padding-top:20px}}
"""


def page(title: str, content: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{html.escape(title)}</title><style>{BASE_CSS}{BRAND_CSS}</style></head><body><main class="wrap"><div class="brand"><div class="logo-frame"><img src="/snr-logo.png" alt="Official Snr. Buns logo" width="1983" height="793"></div><div class="tag">LOYALTY CARD</div></div>{content}<footer>SNR Buns • Thanks for supporting us</footer></main></body></html>"""


def suggested_names(db: SNRDatabase, entered: str) -> list[str]:
    key = normalize_name(entered)
    if len(key) < 3:
        return []
    names = {normalize_name(name): name for name in db.customer_names()}
    matches = difflib.get_close_matches(
        key, sorted(names), n=3, cutoff=0.75 if len(key) < 6 else 0.82
    )
    return [names[match] for match in matches]


def search_page(not_found: bool = False, entered: str = "", suggestions: list[str] | None = None, names: list[str] | None = None) -> str:
    message = '<div class="notice">We couldn\'t find that exact character name. Check the spelling or ask SNR Buns staff.</div>' if not_found else ""
    if suggestions:
        choices = "".join(
            f'<form action="/card" method="get"><button type="submit" name="name" value="{html.escape(name, quote=True)}">{html.escape(name)}</button></form>'
            for name in suggestions
        )
        message = f'<div class="notice"><strong>Did you mean…?</strong><p>Select your name to open your card, or correct the name above.</p>{choices}</div>'
    options = "".join(
        f'<option value="{html.escape(name, quote=True)}">{html.escape(name)}</option>'
        for name in sorted(set(names or []), key=normalize_name)
    )
    browse = (
        f'<div class="history"><label class="label" for="saved-name">Or find your name in the list</label>'
        f'<p class="muted">Browse all saved customer names, A–Z.</p>'
        f'<form action="/card" method="get"><select id="saved-name" name="name" required '
        f'style="min-width:0;flex:1;width:100%;border:1px solid #ffbd2766;background:#0c0907;color:white;border-radius:14px;padding:16px;font-size:16px">'
        f'<option value="" selected disabled>Choose your character name</option>{options}</select>'
        f'<button type="submit">Open Selected Card</button></form></div>'
        if options else '<div class="notice">No customer names saved yet. Ask staff to record your first purchase.</div>'
    )
    return page("SNR Buns Loyalty Card", f"""<section class="card"><div class="label">Check your rewards</div><h1>Find your loyalty card</h1><p class="muted">Enter the character name you gave our staff. Capital letters and extra spaces are fine. We can suggest close spelling matches.</p><form action="/card" method="get"><input name="name" maxlength="60" minlength="2" autocomplete="name" placeholder="Your character name" value="{html.escape(entered, quote=True)}" required><button type="submit">View My Card</button></form>{message}{browse}</section>""")


def _sale_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone(LONDON).strftime("%d %b %Y")
    except (TypeError, ValueError):
        return "Previous visit"


def customer_page(customer: dict[str, Any]) -> str:
    recent = "".join(
        f'<div class="sale"><div><strong>{html.escape(str(s["deal_name"]))}</strong><br><small>{_sale_date(s["created_at"])}</small></div><span>+{int(s["loyalty_points"])} ⭐</span></div>'
        for s in customer.get("recent_sales", [])
    ) or '<div class="notice">No recent visits to show.</div>'
    jackpot_message = '<strong style="color:var(--gold)">🏆 Jackpot winner!</strong>' if int(customer["jackpot_wins"]) else "Your Golden Tickets are automatically entered into the £5,000 jackpot."
    content = f"""<section class="card"><div class="label">Official customer</div><div class="name">{html.escape(str(customer["display_name"]))}</div><div class="grid"><div class="stat"><div class="label">Loyalty points</div><div class="num">⭐ {int(customer["loyalty_points"])}</div></div><div class="stat"><div class="label">Golden tickets earned</div><div class="num">🎟️ {int(customer["golden_tickets"])}</div></div><div class="stat"><div class="label">Visits</div><div class="num">🍔 {int(customer["lifetime_sales"])}</div></div><div class="stat"><div class="label">Items served</div><div class="num">🥤 {int(customer["food_sold"]) + int(customer["drinks_sold"])}</div></div><div class="stat wide jackpot"><div class="label">£5,000 Golden Ticket Jackpot</div><p>{jackpot_message}</p><small class="muted">The winning ticket and its position remain hidden.</small></div></div><div class="history"><div class="label">Recent visits</div>{recent}</div></section><a class="back" href="/">← Check another card</a>"""
    content = content.replace('<div class="history">', customer.get('_claim_section', '') + '<div class="history">', 1)
    return page(f"{customer['display_name']} • SNR Loyalty", content)


def claim_section(customer, claims, form_token):
    rows = claims.summary(customer['display_name'])
    pending = next((r for r in rows if r['status'] == 'pending'), None)
    labels = {'pending': 'Awaiting staff handover — 4 points reserved',
              'fulfilled': 'Pack handed over', 'cancelled': 'Cancelled — 4 points returned'}
    history = ''.join(f'<p>Request #{r["id"]}: {labels[r["status"]]}</p>' for r in rows)
    name = html.escape(customer['display_name'], quote=True)
    points = int(customer['loyalty_points'])
    if pending:
        action = '<p>Your request is saved. Visit SNR Buns to collect your pack from staff.</p>'
    elif not claims.configured():
        action = '<p>Online claims are being set up. Please ask staff about your reward.</p>'
    elif points < 4:
        action = f'<p>Collect {4-points} more loyalty point(s) to request your next pack.</p>'
    else:
        action = f'''<p>Ask staff for your private claim code. Keep it secret; it protects your points.</p>
            <form method="post" action="/claim">
            <input type="hidden" name="name" value="{name}">
            <input type="hidden" name="request_key" value="{html.escape(form_token, quote=True)}">
            <input type="password" name="code" aria-label="Private claim code" placeholder="Private claim code"
                maxlength="100" autocomplete="off" required>
            <button type="submit">Request Pack — 4 Points</button></form>'''
    return f'''<section class="history"><div class="label">Free trading card packs</div>
        <p><strong>4 points = 1 pack containing 2 trading cards.</strong></p>
        <p>{points} available points. Requesting reserves four points; staff confirm when you collect.</p>
        {action}<div class="muted">{history}</div></section>'''


class LookupLimiter:
    def __init__(self) -> None:
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allowed(self, address: str) -> bool:
        now = time.monotonic()
        bucket = self.hits[address]
        while bucket and bucket[0] < now - 60:
            bucket.popleft()
        if len(bucket) >= MAX_LOOKUPS_PER_MINUTE:
            return False
        bucket.append(now)
        return True


def start_web_server(db: SNRDatabase, port: int) -> ThreadingHTTPServer:
    limiter = LookupLimiter()
    claims = ClaimStore(db)
    form_secret = secrets.token_bytes(32)

    def signature(name, token):
        return hmac.new(form_secret, (normalize_name(name)+'|'+token).encode(), hashlib.sha256).hexdigest()

    def card_html(customer):
        token = f'{int(time.time())}.{secrets.token_hex(16)}'
        customer['_claim_section'] = claim_section(customer, claims, token+'.'+signature(customer['display_name'], token))
        return customer_page(customer)

    class LoyaltyHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            if urlparse(self.path).path != '/claim':
                self._send(404, 'Not found', 'text/plain; charset=utf-8')
                return
            if not limiter.allowed(self.client_address[0]):
                self._send(429, 'Please wait one minute and try again.', 'text/plain; charset=utf-8')
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096 or self.headers.get('Transfer-Encoding'):
                    raise ValueError('Invalid request size.')
                if self.headers.get('Content-Type','').split(';')[0] != 'application/x-www-form-urlencoded':
                    raise ValueError('Please use the claim button on your card.')
                self.connection.settimeout(10)
                data = parse_qs(self.rfile.read(length).decode('utf-8'), max_num_fields=5)
                name = data.get('name',[''])[0]
                key = data.get('request_key',[''])[0]
                code = data.get('code',[''])[0]
                if not 2 <= len(name) <= 60:
                    raise ValueError('Please reopen your card.')
                parts = key.split('.')
                if len(parts) != 3 or not hmac.compare_digest(signature(name,'.'.join(parts[:2])),parts[2]):
                    raise ValueError('Please reopen your card and try again.')
                age = time.time()-int(parts[0])
                if not 0 <= age <= 1800:
                    raise ValueError('This form has expired. Refresh your card and try again.')
                result = claims.request(name, code, key)
                status = {'pending': 'Your request is saved and queued for staff. Four points are reserved. Please visit SNR Buns to collect.',
                          'fulfilled': 'Staff have already marked this pack as handed over.',
                          'cancelled': 'This request was cancelled and its points returned. Reopen your card to make a new request.'}[result['status']]
                from urllib.parse import urlencode
                link = '/card?'+urlencode({'name': result['customer_name']})
                self._send(200, page('SNR reward request', f'<section class="card"><h1>Request #{result["id"]}</h1><p>{status}</p><a class="back" href="{html.escape(link, quote=True)}">Back to my card</a></section>'))
            except (ValueError, UnicodeError) as exc:
                self._send(400, page('Check your claim', f'<section class="card"><h1>Unable to request pack</h1><p>{html.escape(str(exc))}</p><a class="back" href="/">Find my card again</a></section>'))

        def _send(self, status: int, body: str, content_type: str = "text/html; charset=utf-8") -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Robots-Tag", "noindex, nofollow")
            # Public cards must embed in the phone; claim mutations require a code and signed form.
            # Do not send X-Frame-Options or a restrictive frame-ancestors policy.
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            parsed = urlparse(self.path)
            if parsed.path in ("/snr-logo.png", "/snr-logo.jpg"):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(LOGO_IMAGE)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(LOGO_IMAGE)
                return
            if parsed.path == "/health":
                self._send(200, json.dumps({"status": "ok"}), "application/json; charset=utf-8")
                return
            if parsed.path == "/":
                self._send(200, search_page(names=db.customer_names()))
                return
            if parsed.path != "/card":
                self._send(404, search_page(names=db.customer_names()))
                return
            forwarded = self.headers.get("X-Forwarded-For")
            address = forwarded.split(",")[0].strip() if forwarded else self.client_address[0]
            if not limiter.allowed(address):
                self._send(429, "Too many searches. Please wait one minute and try again.", "text/plain; charset=utf-8")
                return
            name = parse_qs(parsed.query).get("name", [""])[0].strip()
            if len(name) < 2 or len(name) > 60:
                self._send(404 if name else 200, search_page(bool(name), names=db.customer_names()))
                return
            customer = db.get_customer(name)
            if customer:
                self._send(200, card_html(customer))
                return
            suggestions = suggested_names(db, name)
            self._send(200 if suggestions else 404, search_page(True, name, suggestions, db.customer_names()))

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("0.0.0.0", port), LoyaltyHandler)
    server.daemon_threads = True
    Thread(target=server.serve_forever, name="snr-loyalty-web", daemon=True).start()
    return server
