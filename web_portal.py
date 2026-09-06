from __future__ import annotations

import hashlib
import hmac
import html
import json
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from customer_accounts import Accounts
from delivery_orders import DeliveryStore
from reward_claims import ClaimStore
from snr_core import DEALS, SNRDatabase, normalize_name

LONDON = ZoneInfo("Europe/London")
MAX_REQUESTS_PER_MINUTE = 15
LOGO_IMAGE = Path(__file__).with_name("snr-logo.png").read_bytes()

CSS = """
:root{--gold:#ffe53b;--cream:#fff9e8;--muted:#f4d8ce}*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:radial-gradient(ellipse at 10% 0%,#f02417 0,transparent 55%),radial-gradient(ellipse at 100% 45%,#9d120c 0,transparent 60%),#350708;background-attachment:fixed;color:var(--cream);font:16px/1.5 Inter,Arial,sans-serif}
.wrap{width:min(740px,92vw);margin:auto;padding:28px 0 54px}.brand{text-align:center;margin-bottom:24px}.logo-frame{overflow:hidden;width:min(100%,620px);margin:0 auto 18px;border:2px solid var(--gold);border-radius:16px;box-shadow:0 12px 36px #1a000077}.logo-frame img{display:block;width:100%;height:auto}.tag{display:inline-block;padding:7px 22px;background:var(--gold);color:#7b1009;border-radius:99px;font-size:14px;font-weight:900;letter-spacing:2px}
.card{background:linear-gradient(145deg,#73130f,#32090a);border:2px solid #ffd334;border-top:7px solid var(--gold);border-radius:24px;padding:clamp(22px,5vw,34px);box-shadow:0 18px 60px #21000088}.label{color:var(--gold);text-transform:uppercase;font-size:14px;font-weight:900;letter-spacing:1px}.name{font-size:clamp(28px,7vw,44px);font-weight:950;margin:6px 0 20px;overflow-wrap:anywhere}h1{font-size:clamp(27px,6vw,36px);line-height:1.15;margin:10px 0 18px}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.stat{background:linear-gradient(135deg,#b52319,#70120e);border:1px solid #ffb93480;border-radius:16px;padding:17px}.num{font-size:29px;font-weight:950;margin-top:5px;color:#fff8dd}.wide{grid-column:1/-1}.jackpot{background:linear-gradient(135deg,#ffe33b,#ffa818);color:#51100a;border:2px solid #fff39c}.jackpot .label,.jackpot .muted,.jackpot strong{color:#51100a!important}
form{display:flex;gap:10px;margin-top:18px}input,select{min-width:0;flex:1;width:100%;background:#290808;color:white;border:1px solid #ffda4e;border-radius:14px;padding:16px;font-size:16px}input:focus,select:focus{outline:3px solid var(--gold);outline-offset:3px}button{border:0;border-radius:14px;background:linear-gradient(135deg,#fff05b,#ffbf18);color:#60100b;padding:16px 20px;font-weight:950;font-size:15px;box-shadow:0 4px 0 #a3540b;cursor:pointer}button:hover{filter:brightness(1.1)}button:focus-visible,a:focus-visible{outline:3px solid white;outline-offset:4px}.secondary{background:#5f100d;color:#fff4db;border:1px solid #ffda4e;box-shadow:none}.panel{padding-top:22px;margin-top:22px;border-top:1px solid #ffda3544}.notice{padding:18px;border-radius:14px;background:#ffda3514;border:1px solid #ffda3544;color:#fff1d3;margin-top:18px}.muted{color:var(--muted)}.history{margin-top:22px;padding-top:16px;border-top:1px solid #ffda3533}.sale{display:flex;justify-content:space-between;gap:14px;padding:14px 0;border-bottom:1px solid #ffffff12}.sale:last-child{border:0}.sale small{font-size:14px;color:var(--muted)}.back{display:block;text-align:center;color:var(--gold);font-weight:700;margin-top:20px;text-decoration:none}footer{text-align:center;color:#f2c9b7;font-size:13px;margin-top:24px}
.delivery{margin-top:24px;padding:22px;background:#210708aa;border:2px solid #ff8c22;border-radius:20px}.delivery h2{margin:5px 0 8px}.delivery-form{display:block}.deal-list{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:16px 0}.deal-choice{display:block;position:relative}.deal-choice input{position:absolute;opacity:0;pointer-events:none}.deal-box{display:block;height:100%;padding:15px;border:2px solid #7e2b1f;border-radius:15px;background:#4a0d0c;cursor:pointer}.deal-box strong,.deal-box span{display:block}.deal-box .price{font-size:23px;color:var(--gold);font-weight:950;margin-top:7px}.deal-choice input:checked+.deal-box{border-color:var(--gold);background:#8d1b12;box-shadow:0 0 0 3px #ffda3544}.deal-choice input:focus-visible+.deal-box{outline:3px solid white}.location-row{display:flex;gap:10px}.status-paid{color:#88f29b}.status-pending{color:#ffe45f}.status-cancelled{color:#ff9f91}
@media(max-width:560px){form,.location-row{flex-direction:column}button{width:100%}.card{padding:22px}.grid,.deal-list{grid-template-columns:1fr}.wide{grid-column:auto}.logo-frame{border-radius:14px}.wrap{padding-top:20px}}
"""


def page(title: str, content: str) -> str:
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{html.escape(title)}</title><style>{CSS}</style></head><body><main class="wrap"><div class="brand"><div class="logo-frame"><img src="/snr-logo.png" alt="Official Snr. Buns logo" width="1983" height="793"></div><div class="tag">LOYALTY ACCOUNT</div></div>{content}<footer>SNR Buns • Your account is protected by your password</footer></main></body></html>'''


def name_options(names: list[str], selected: str = "") -> str:
    items = [f'<option value="" disabled{"" if selected else " selected"}>Choose your name</option>']
    wanted = normalize_name(selected)
    for name in sorted(set(names), key=normalize_name):
        mark = " selected" if normalize_name(name) == wanted else ""
        items.append(f'<option value="{html.escape(name, quote=True)}"{mark}>{html.escape(name)}</option>')
    return "".join(items)


def login_page(names: list[str], message: str = "", selected: str = "") -> str:
    notice = f'<div class="notice">{html.escape(message)}</div>' if message else ""
    options = name_options(names, selected)
    return page("SNR Buns account login", f'''<section class="card"><div class="label">Customer login</div><h1>Open your loyalty account</h1><p class="muted">Choose your character name and enter your password.</p>{notice}<form method="post" action="/login"><select name="name" required>{options}</select><input type="password" name="password" minlength="10" maxlength="128" autocomplete="current-password" placeholder="Your password" required><button type="submit">Log In</button></form><div class="panel"><div class="label">Create account or forgot password</div><h2>Choose your password here</h2><p class="muted">No setup code is needed. Choose your name and password below. SNR staff will receive one approval button in Discord to protect your rewards.</p><form method="post" action="/request-access"><select name="name" required>{options}</select><input type="password" name="password" minlength="10" maxlength="128" autocomplete="new-password" placeholder="Choose password (10+ characters)" required><input type="password" name="confirm" minlength="10" maxlength="128" autocomplete="new-password" placeholder="Repeat password" required><button type="submit">Request My Account</button></form><p class="muted">If you already have an account, this safely requests a password reset. Your old password stays active until staff approve the request.</p></div></section>''')


def _sale_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone(LONDON).strftime("%d %b %Y")
    except (TypeError, ValueError):
        return "Previous visit"


def claim_section(customer: dict, claims: ClaimStore, form_token: str) -> str:
    rows = claims.summary(customer["display_name"])
    pending = next((row for row in rows if row["status"] == "pending"), None)
    labels = {"pending": "Claim sent — awaiting staff handover", "fulfilled": "Pack handed over — points reset to 0", "cancelled": "Claim cancelled — points unchanged"}
    history = "".join(f'<p>Request #{row["id"]}: {labels[row["status"]]}</p>' for row in rows)
    points = int(customer["loyalty_points"])
    if pending:
        action = "<p>Your request is saved. Visit SNR Buns to collect your pack from staff.</p>"
    elif not claims.configured():
        action = "<p>Online claims are being set up. Please ask staff.</p>"
    elif points < 4:
        action = f"<p>Collect {4-points} more loyalty point(s) to request your next pack.</p>"
    else:
        action = f'<form method="post" action="/claim"><input type="hidden" name="claim_request_key" value="{html.escape(form_token, quote=True)}"><button type="submit">Claim Trading Card Pack</button></form>'
    return f'<section class="history"><div class="label">Free trading-card packs</div><p><strong>Reach 4 points to claim 1 pack containing 2 trading cards.</strong></p><p>{points} available points. Your login proves this is your account.</p><p class="muted">After staff hand over your pack, your loyalty points reset to 0.</p>{action}<div class="muted">{history}</div></section>'


def delivery_section(customer: dict, orders: DeliveryStore, form_token: str) -> str:
    rows = orders.summary(customer["display_name"])
    active = next((row for row in rows if row["status"] in ("pending", "processing")), None)
    labels = {
        "pending": "Waiting for SNR staff",
        "processing": "Payment is being confirmed",
        "paid": "Paid and added to loyalty",
        "cancelled": "Cancelled",
    }
    recent = "".join(
        f'<p>Order #{row["id"]}: <strong class="status-{"pending" if row["status"] == "processing" else row["status"]}">{labels[row["status"]]}</strong> — {html.escape(row["deal_name"])} • £{int(row["price"]):,}</p>'
        for row in rows
    )
    if active:
        order_form = (
            f'<div class="notice"><strong>Order #{active["id"]} is waiting.</strong><br>'
            f'{html.escape(active["deal_name"])} • <strong>£{int(active["price"]):,} owed</strong><br>'
            f'Delivery location: {html.escape(active["postal"])}</div>'
        )
    else:
        choices = "".join(
            f'''<label class="deal-choice"><input type="radio" name="deal" value="{deal.key}" required><span class="deal-box"><strong>{html.escape(deal.name)}</strong><span>{html.escape(deal.item_summary)}</span><span>{deal.loyalty_points} loyalty point(s) • {deal.golden_tickets} Golden ticket(s)</span><span class="price">£{deal.price:,}</span></span></label>'''
            for deal in DEALS.values()
        )
        if orders.configured():
            order_form = f'''<form class="delivery-form" method="post" action="/order"><input type="hidden" name="order_request_key" value="{html.escape(form_token, quote=True)}"><div class="deal-list">{choices}</div><div class="location-row"><input name="postal" minlength="2" maxlength="80" autocomplete="street-address" placeholder="Required postal or delivery location" aria-label="Postal or delivery location" required><button type="submit">Send Delivery Order</button></div><p class="muted">You pay SNR staff on delivery. Loyalty points and Golden Tickets are added only after staff confirm payment.</p></form>'''
        else:
            order_form = f'<div class="deal-list">{choices}</div><div class="notice">Online delivery is being set up. Please contact SNR Buns for now.</div>'
    return f'''<section class="delivery"><div class="label">🚗 SNR delivery</div><h2>Order from our current deals</h2><p class="muted">Choose one deal and tell us exactly where to deliver it.</p>{order_form}<div class="history"><div class="label">My delivery orders</div>{recent or '<p class="muted">No delivery orders yet.</p>'}</div></section>'''


def customer_page(customer: dict, claims: ClaimStore, orders: DeliveryStore, claim_token: str, order_token: str) -> str:
    recent = "".join(f'<div class="sale"><div><strong>{html.escape(str(s["deal_name"]))}</strong><br><small>{_sale_date(s["created_at"])}</small></div><span>+{int(s["loyalty_points"])} ⭐</span></div>' for s in customer.get("recent_sales", [])) or '<div class="notice">No recent visits to show.</div>'
    jackpot = '<strong>🏆 Jackpot winner!</strong>' if int(customer["jackpot_wins"]) else "Your Golden Tickets are automatically entered into the £5,000 jackpot."
    return page(f'{customer["display_name"]} • SNR Loyalty', f'''<section class="card"><div class="label">Logged-in customer</div><div class="name">{html.escape(customer["display_name"])}</div><div class="grid"><div class="stat"><div class="label">Available points</div><div class="num">⭐ {int(customer["loyalty_points"])}</div></div><div class="stat"><div class="label">Golden tickets</div><div class="num">🎟️ {int(customer["golden_tickets"])}</div></div><div class="stat"><div class="label">Visits</div><div class="num">🍔 {int(customer["lifetime_sales"])}</div></div><div class="stat"><div class="label">Items served</div><div class="num">🥤 {int(customer["food_sold"])+int(customer["drinks_sold"])}</div></div><div class="stat wide jackpot"><div class="label">£5,000 Golden Ticket Jackpot</div><p>{jackpot}</p><small>The winning position remains hidden.</small></div></div>{delivery_section(customer, orders, order_token)}{claim_section(customer, claims, claim_token)}<div class="history"><div class="label">Recent visits</div>{recent}</div><form method="post" action="/logout"><input type="hidden" name="logout" value="1"><button class="secondary" type="submit">Log Out</button></form></section>''')


class Limiter:
    def __init__(self) -> None:
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allowed(self, address: str) -> bool:
        now = time.monotonic()
        bucket = self.hits[address]
        while bucket and bucket[0] < now - 60:
            bucket.popleft()
        if len(bucket) >= MAX_REQUESTS_PER_MINUTE:
            return False
        bucket.append(now)
        return True


def start_web_server(db: SNRDatabase, port: int) -> ThreadingHTTPServer:
    limiter, claims, orders, accounts = Limiter(), ClaimStore(db), DeliveryStore(db), Accounts(db)
    form_secret = secrets.token_bytes(32)

    def signature(owner: str, token: str) -> str:
        return hmac.new(form_secret, (owner + "|" + token).encode(), hashlib.sha256).hexdigest()

    def make_form_token(owner: str) -> str:
        token = f"{int(time.time())}.{secrets.token_hex(16)}"
        return token + "." + signature(owner, token)

    def valid_form_token(owner: str, key: str) -> bool:
        parts = key.split(".")
        if len(parts) != 3 or not hmac.compare_digest(signature(owner, ".".join(parts[:2])), parts[2]):
            return False
        try:
            age = time.time() - int(parts[0])
        except ValueError:
            return False
        return 0 <= age <= 1800

    class Handler(BaseHTTPRequestHandler):
        def address(self) -> str:
            forwarded = self.headers.get("X-Forwarded-For")
            return forwarded.split(",")[0].strip() if forwarded else self.client_address[0]

        def session_token(self) -> str:
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
                return cookie["snr_session"].value if "snr_session" in cookie else ""
            except Exception:
                return ""

        def owner(self) -> str | None:
            return accounts.owner(self.session_token())

        def read_form(self) -> dict[str, str]:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096 or self.headers.get("Transfer-Encoding"):
                raise ValueError("Invalid request size.")
            if self.headers.get("Content-Type", "").split(";")[0] != "application/x-www-form-urlencoded":
                raise ValueError("Please use the form on this page.")
            self.connection.settimeout(10)
            parsed = parse_qs(self.rfile.read(length).decode("utf-8"), max_num_fields=8)
            return {key: values[0] for key, values in parsed.items()}

        def send_html(self, status: int, body: str, cookie: str | None = None) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Robots-Tag", "noindex, nofollow")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; object-src 'none'")
            self.send_header("Cache-Control", "no-store")
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(data)

        def redirect(self, location: str, cookie: str | None = None) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Cache-Control", "no-store")
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()

        @staticmethod
        def session_cookie(token: str) -> str:
            return f"snr_session={token}; Path=/; Max-Age=28800; HttpOnly; Secure; SameSite=None; Partitioned"

        def show_account(self) -> None:
            owner = self.owner()
            customer = db.get_customer(owner) if owner else None
            if not customer:
                self.send_html(401, login_page(db.customer_names(), "Please log in to open a loyalty account."))
                return
            self.send_html(200, customer_page(
                customer, claims, orders, make_form_token(owner), make_form_token(owner)
            ))

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in ("/snr-logo.png", "/snr-logo.jpg"):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(LOGO_IMAGE)))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(LOGO_IMAGE)
            elif path == "/health":
                data = json.dumps({"status": "ok"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif path in ("/account", "/card"):
                self.show_account()
            elif path == "/":
                self.redirect("/account") if self.owner() else self.send_html(200, login_page(db.customer_names()))
            else:
                self.send_html(404, login_page(db.customer_names(), "Page not found."))

        def do_POST(self) -> None:  # noqa: N802
            if not limiter.allowed(self.address()):
                self.send_html(429, page("Please wait", '<section class="card"><h1>Please wait one minute and try again.</h1></section>'))
                return
            path, data = urlparse(self.path).path, {}
            try:
                data = self.read_form()
                if path == "/login":
                    token = accounts.login(data.get("name", ""), data.get("password", ""))
                    self.redirect("/account", self.session_cookie(token))
                elif path == "/request-access":
                    if data.get("password", "") != data.get("confirm", ""):
                        raise ValueError("The two passwords do not match.")
                    result = accounts.request_access(data.get("name", ""), data.get("password", ""))
                    action = "password reset" if result["request_type"] == "reset" else "new account"
                    if result.get("already_pending"):
                        notice = "An approval is already waiting for this name. The original password chosen for that request has not been changed. If it needs replacing, ask staff to reject the waiting request and submit again."
                    else:
                        notice = "You do not need a code. After staff verify and approve you in Discord, return here and log in using the password you just chose."
                    self.send_html(200, page("Account request sent", f'''<section class="card"><div class="label">Account request sent</div><h1>One quick staff check</h1><p>Your {action} request for <strong>{html.escape(result["customer_name"])}</strong> has been sent to SNR staff.</p><div class="notice">{html.escape(notice)}</div><a class="back" href="/">Back to login</a></section>'''))
                elif path == "/logout":
                    accounts.logout(self.session_token())
                    self.redirect("/", "snr_session=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=None; Partitioned")
                elif path == "/claim":
                    owner = self.owner()
                    if not owner:
                        raise ValueError("Your login has expired. Please log in again.")
                    key = data.get("claim_request_key", "")
                    if not valid_form_token(owner, key):
                        raise ValueError("This form has expired. Refresh your account and try again.")
                    result = claims.request_authenticated(owner, key)
                    message = {"pending": "Your claim has been sent to SNR staff. Visit SNR Buns to collect your pack. Your points reset to 0 only after staff mark it handed over.", "fulfilled": "Staff marked this pack as handed over and your points have reset to 0.", "cancelled": "This claim was cancelled and your points were not changed."}[result["status"]]
                    self.send_html(200, page("Reward request", f'<section class="card"><h1>Request #{result["id"]}</h1><p>{message}</p><a class="back" href="/account">Back to my account</a></section>'))
                elif path == "/order":
                    owner = self.owner()
                    if not owner:
                        raise ValueError("Your login has expired. Please log in again.")
                    key = data.get("order_request_key", "")
                    if not valid_form_token(owner, key):
                        raise ValueError("This order form has expired. Refresh your account and try again.")
                    result = orders.create_authenticated(
                        owner, data.get("deal", ""), data.get("postal", ""), key
                    )
                    self.send_html(200, page("Delivery order received", f'''<section class="card"><div class="label">🚗 Delivery order sent</div><h1>Order #{result["id"]}</h1><p><strong>{html.escape(result["deal_name"])}</strong></p><p>Amount to pay on delivery: <strong>£{int(result["price"]):,}</strong></p><p>Delivery location: <strong>{html.escape(result["postal"])}</strong></p><div class="notice">SNR staff have been notified. Your loyalty points and Golden Tickets will update after staff confirm you have paid.</div><a class="back" href="/account">Back to my account</a></section>'''))
                else:
                    self.send_html(404, login_page(db.customer_names(), "Page not found."))
            except (ValueError, UnicodeError, KeyError) as exc:
                if self.owner():
                    self.send_html(400, page("Could not complete request", f'<section class="card"><h1>Could not complete that request</h1><div class="notice">{html.escape(str(exc))}</div><a class="back" href="/account">Back to my account</a></section>'))
                else:
                    self.send_html(400, login_page(db.customer_names(), str(exc), data.get("name", "")))

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    Thread(target=server.serve_forever, name="snr-loyalty-web", daemon=True).start()
    return server
