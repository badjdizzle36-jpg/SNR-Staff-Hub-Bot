from __future__ import annotations

import hmac
import html
import json
import os
import time
from collections import defaultdict, deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from delivery_orders import DeliveryStore
from phone_pairing import PhonePairings
from reward_claims import ClaimStore
from snr_core import DEALS, SNRDatabase

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
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{html.escape(title)}</title><style>{CSS}</style></head><body><main class="wrap"><div class="brand"><div class="logo-frame"><img src="/snr-logo.png" alt="Official Snr. Buns logo" width="1983" height="793"></div><div class="tag">LOYALTY ACCOUNT</div></div>{content}<footer>SNR Buns • Securely linked to your in-game character</footer></main></body></html>'''


def lb_phone_landing(message: str = "") -> str:
    notice = f'<div class="notice">{html.escape(message)}</div>' if message else ""
    return page(
        "SNR Buns LB Phone App",
        f'''<section class="card"><div class="label">Secure customer access</div><h1>Open SNR Buns on your LB Phone</h1>{notice}<p>Your loyalty card, rewards and delivery ordering are now inside the official SNR Buns in-game phone app.</p><div class="notice"><strong>No password is required.</strong><br>Your account is linked to your verified in-game character and equipped phone after an in-person staff approval.</div><p class="muted">Visit the SNR Buns counter if your phone has not been paired yet.</p></section>''',
    )


def _sale_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone(LONDON).strftime("%d %b %Y")
    except (TypeError, ValueError):
        return "Previous visit"


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
    limiter, claims, orders = Limiter(), ClaimStore(db), DeliveryStore(db)
    pairings = PhonePairings(db)
    api_secret = os.getenv("LB_PHONE_API_SECRET", "")
    class Handler(BaseHTTPRequestHandler):
        def address(self) -> str:
            forwarded = self.headers.get("X-Forwarded-For")
            return forwarded.split(",")[0].strip() if forwarded else self.client_address[0]

        def api_authorized(self) -> bool:
            supplied = self.headers.get("Authorization", "")
            expected = "Bearer " + api_secret
            return bool(api_secret) and hmac.compare_digest(supplied, expected)

        def read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192 or self.headers.get("Transfer-Encoding"):
                raise ValueError("Invalid request size.")
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("JSON is required.")
            self.connection.settimeout(10)
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("Invalid request.")
            return value

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

        def send_json(self, status: int, value: dict) -> None:
            data = json.dumps(value, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
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

        @staticmethod
        def public_customer(customer: dict) -> dict:
            return {
                "name": customer["display_name"],
                "loyalty_points": int(customer["loyalty_points"]),
                "golden_tickets": int(customer["golden_tickets"]),
                "visits": int(customer["lifetime_sales"]),
                "items_served": int(customer["food_sold"]) + int(customer["drinks_sold"]),
                "jackpot_wins": int(customer["jackpot_wins"]),
                "recent_sales": [
                    {"deal": row["deal_name"], "loyalty": int(row["loyalty_points"]), "date": _sale_date(row["created_at"])}
                    for row in customer.get("recent_sales", [])
                ],
            }

        def api_owner(self, data: dict) -> str:
            owner = pairings.owner(data.get("character_id"), data.get("phone_number"))
            if not owner:
                raise PermissionError("This character and phone are not paired to an SNR loyalty account.")
            return owner

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
            elif path in ("/", "/account", "/card"):
                self.send_html(200, lb_phone_landing())
            else:
                self.send_html(404, lb_phone_landing("Page not found."))

        def do_POST(self) -> None:  # noqa: N802
            path, data = urlparse(self.path).path, {}
            if not path.startswith("/api/lb/"):
                self.send_html(404, lb_phone_landing("Use the SNR Buns app on your LB Phone."))
                return
            if not self.api_authorized():
                self.send_json(503 if not api_secret else 401, {"ok": False, "error": "LB Phone connection is not configured."})
                return
            try:
                data = self.read_json()
                if path == "/api/lb/state":
                    owner = pairings.owner(data.get("character_id"), data.get("phone_number"))
                    if not owner:
                        self.send_json(200, {"ok": True, **pairings.status_for_identity(data.get("character_id", ""))})
                        return
                    customer = db.get_customer(owner)
                    self.send_json(200, {
                        "ok": True,
                        "status": "approved",
                        "customer": self.public_customer(customer),
                        "claims": claims.summary(owner),
                        "orders": orders.summary(owner),
                        "deals": [
                            {"key": deal.key, "name": deal.name, "price": deal.price, "contents": deal.item_summary,
                             "loyalty": deal.loyalty_points, "tickets": deal.golden_tickets}
                            for deal in DEALS.values()
                        ],
                    })
                elif path == "/api/lb/pair":
                    result = pairings.request(
                        data.get("customer_name"), data.get("character_id"), data.get("phone_number"),
                        data.get("rp_name"), data.get("request_key"),
                    )
                    self.send_json(200, {"ok": True, "status": result["status"], "customer_name": result["customer_name"]})
                elif path == "/api/lb/claim":
                    owner = self.api_owner(data)
                    result = claims.request_authenticated(owner, data.get("request_key", ""))
                    self.send_json(200, {"ok": True, "id": result["id"], "status": result["status"]})
                elif path == "/api/lb/order":
                    owner = self.api_owner(data)
                    result = orders.create_authenticated(
                        owner, data.get("deal", ""), data.get("postal", ""), data.get("request_key", "")
                    )
                    self.send_json(200, {"ok": True, "id": result["id"], "status": result["status"],
                                         "deal_name": result["deal_name"], "price": result["price"]})
                else:
                    self.send_json(404, {"ok": False, "error": "Unknown LB Phone action."})
            except PermissionError as exc:
                self.send_json(403, {"ok": False, "error": str(exc)})
            except (ValueError, UnicodeError, KeyError, json.JSONDecodeError) as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    Thread(target=server.serve_forever, name="snr-loyalty-web", daemon=True).start()
    return server
