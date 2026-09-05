from __future__ import annotations

import html
import json
import time
from collections import defaultdict, deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from snr_core import SNRDatabase


LONDON = ZoneInfo("Europe/London")
MAX_LOOKUPS_PER_MINUTE = 15


BASE_CSS = """
:root{--gold:#ffbd27;--orange:#ff6a00;--ink:#100b08;--panel:#1b120d;--cream:#fff4dc;--muted:#cdbfae}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(circle at top,#4b1a08 0,#160d08 42%,#090706 100%);color:var(--cream);font-family:Inter,Arial,sans-serif}
.wrap{width:min(680px,92vw);margin:auto;padding:42px 0 54px}.brand{text-align:center;margin-bottom:24px}.logo{font-size:clamp(42px,11vw,72px);line-height:.9;font-weight:1000;font-style:italic;letter-spacing:-3px;color:var(--orange);text-shadow:0 3px 0 #411000,0 0 26px #ff6a0066}.tag{color:var(--gold);font-weight:800;letter-spacing:2px;margin-top:10px}
.card{background:linear-gradient(145deg,#21150f,#130d09);border:1px solid #ffbd2755;border-radius:24px;padding:clamp(22px,5vw,34px);box-shadow:0 20px 60px #0009,0 0 40px #ff7b0014}.label{color:var(--gold);text-transform:uppercase;font-size:12px;font-weight:900;letter-spacing:1.5px}.name{font-size:clamp(28px,7vw,44px);font-weight:950;margin:6px 0 20px}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.stat{background:#ffffff09;border:1px solid #ffffff12;border-radius:16px;padding:17px}.num{font-size:29px;font-weight:950;margin-top:5px}.wide{grid-column:1/-1}.history{margin-top:22px}.sale{display:flex;justify-content:space-between;gap:14px;padding:14px 0;border-bottom:1px solid #ffffff12}.sale:last-child{border:0}.sale small,.muted{color:var(--muted)}
form{display:flex;gap:10px;margin-top:22px}input{min-width:0;flex:1;border:1px solid #ffbd2766;background:#0c0907;color:white;border-radius:14px;padding:16px;font-size:16px;outline:none}input:focus{border-color:var(--gold);box-shadow:0 0 0 3px #ffbd2722}button{border:0;border-radius:14px;background:linear-gradient(135deg,var(--orange),var(--gold));color:#170a02;padding:16px 20px;font-weight:950;font-size:15px;cursor:pointer}.notice{text-align:center;padding:18px;border-radius:14px;background:#ffbd2710;color:var(--muted);margin-top:18px}.back{display:block;text-align:center;color:var(--gold);margin-top:20px;text-decoration:none}.jackpot{border-color:#ffbd2790;background:linear-gradient(135deg,#3a2108,#17100a)}footer{text-align:center;color:#88796c;font-size:12px;margin-top:24px}@media(max-width:430px){.grid{grid-template-columns:1fr}.wide{grid-column:auto}form{flex-direction:column}}
"""


def page(title: str, content: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{html.escape(title)}</title><style>{BASE_CSS}</style></head><body><main class="wrap"><div class="brand"><div class="logo">SNR BUNS</div><div class="tag">LOYALTY CARD</div></div>{content}<footer>Official SNR Buns loyalty portal • Private business figures are never shown</footer></main></body></html>"""


def search_page(not_found: bool = False) -> str:
    message = '<div class="notice">We couldn\'t find that exact character name. Check the spelling or ask SNR Buns staff.</div>' if not_found else ""
    return page("SNR Buns Loyalty Card", f"""<section class="card"><div class="label">Check your rewards</div><h1>Find your loyalty card</h1><p class="muted">Enter the same character name you gave our staff when ordering.</p><form action="/card" method="get"><input name="name" maxlength="60" minlength="2" autocomplete="name" placeholder="Your character name" required><button type="submit">View My Card</button></form>{message}</section>""")


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
    return page(f"{customer['display_name']} • SNR Loyalty", content)


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

    class LoyaltyHandler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: str, content_type: str = "text/html; charset=utf-8") -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Robots-Tag", "noindex, nofollow")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._send(200, json.dumps({"status": "ok"}), "application/json; charset=utf-8")
                return
            if parsed.path == "/":
                self._send(200, search_page())
                return
            if parsed.path != "/card":
                self._send(404, search_page())
                return
            forwarded = self.headers.get("X-Forwarded-For")
            address = forwarded.split(",")[0].strip() if forwarded else self.client_address[0]
            if not limiter.allowed(address):
                self._send(429, "Too many searches. Please wait one minute and try again.", "text/plain; charset=utf-8")
                return
            name = parse_qs(parsed.query).get("name", [""])[0].strip()
            if len(name) < 2 or len(name) > 60:
                self._send(404 if name else 200, search_page(bool(name)))
                return
            customer = db.get_customer(name)
            self._send(200 if customer else 404, customer_page(customer) if customer else search_page(True))

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("0.0.0.0", port), LoyaltyHandler)
    server.daemon_threads = True
    Thread(target=server.serve_forever, name="snr-loyalty-web", daemon=True).start()
    return server
