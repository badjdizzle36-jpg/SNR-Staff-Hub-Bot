from __future__ import annotations

import hashlib
import hmac
import html
import json
import secrets
import time
from collections import defaultdict, deque
from datetime import date, datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from customer_accounts import Accounts, SECURITY_QUESTIONS
from delivery_orders import DeliveryStore
from staff_shifts import StaffShifts
from reward_claims import ClaimStore
from snr_core import DEALS, SNRDatabase, normalize_name

LONDON = ZoneInfo("Europe/London")
MAX_REQUESTS_PER_MINUTE = 15
ACTIVE_ORDER_STATUSES = ("accepted", "on_way", "arrived", "ready_for_pickup", "processing")
LOGO_IMAGE = Path(__file__).with_name("snr-logo.png").read_bytes()

CSS = """
:root{--gold:#ffe53b;--cream:#fff9e8;--muted:#f4d8ce}*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:radial-gradient(ellipse at 10% 0%,#f02417 0,transparent 55%),radial-gradient(ellipse at 100% 45%,#9d120c 0,transparent 60%),#350708;background-attachment:fixed;color:var(--cream);font:16px/1.5 Inter,Arial,sans-serif}
.wrap{width:min(760px,94vw);margin:auto;padding:18px 0 38px}.brand{text-align:center;margin-bottom:14px}.logo-frame{overflow:hidden;width:min(220px,52vw);margin:0 auto 10px;border:2px solid var(--gold);border-radius:18px;box-shadow:0 10px 28px #1a000077}.logo-frame img{display:block;width:100%;height:auto}.tag{display:inline-block;padding:6px 18px;background:var(--gold);color:#7b1009;border-radius:99px;font-size:12px;font-weight:900;letter-spacing:1.5px}
.card{background:linear-gradient(145deg,#73130f,#32090a);border:2px solid #ffd334;border-top:7px solid var(--gold);border-radius:24px;padding:clamp(22px,5vw,34px);box-shadow:0 18px 60px #21000088}.label{color:var(--gold);text-transform:uppercase;font-size:14px;font-weight:900;letter-spacing:1px}.name{font-size:clamp(28px,7vw,44px);font-weight:950;margin:6px 0 20px;overflow-wrap:anywhere}h1{font-size:clamp(27px,6vw,36px);line-height:1.15;margin:10px 0 18px}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.stat{background:linear-gradient(135deg,#b52319,#70120e);border:1px solid #ffb93480;border-radius:16px;padding:17px}.num{font-size:29px;font-weight:950;margin-top:5px;color:#fff8dd}.wide{grid-column:1/-1}.jackpot{background:linear-gradient(135deg,#ffe33b,#ffa818);color:#51100a;border:2px solid #fff39c}.jackpot .label,.jackpot .muted,.jackpot strong{color:#51100a!important}
form{display:flex;gap:10px;margin-top:18px}input,select,textarea{min-width:0;flex:1;width:100%;background:#290808;color:white;border:1px solid #ffda4e;border-radius:14px;padding:16px;font:inherit}textarea{resize:vertical;min-height:82px}input:focus,select:focus,textarea:focus{outline:3px solid var(--gold);outline-offset:3px}button{border:0;border-radius:14px;background:linear-gradient(135deg,#fff05b,#ffbf18);color:#60100b;padding:16px 20px;font-weight:950;font-size:15px;box-shadow:0 4px 0 #a3540b;cursor:pointer}button:hover{filter:brightness(1.1)}button:focus-visible,a:focus-visible{outline:3px solid white;outline-offset:4px}.secondary{background:#5f100d;color:#fff4db;border:1px solid #ffda4e;box-shadow:none}.panel{padding-top:22px;margin-top:22px;border-top:1px solid #ffda3544}.panel form{flex-direction:column}.notice{padding:18px;border-radius:14px;background:#ffda3514;border:1px solid #ffda3544;color:#fff1d3;margin-top:18px}.muted{color:var(--muted)}.history{margin-top:22px;padding-top:16px;border-top:1px solid #ffda3533}.sale{display:flex;justify-content:space-between;gap:14px;padding:14px 0;border-bottom:1px solid #ffffff12}.sale:last-child{border:0}.sale small{font-size:14px;color:var(--muted)}.back{display:block;text-align:center;color:var(--gold);font-weight:700;margin-top:20px;text-decoration:none}footer{text-align:center;color:#f2c9b7;font-size:13px;margin-top:24px}
.delivery{margin-top:24px;padding:22px;background:#210708aa;border:2px solid #ff8c22;border-radius:20px}.delivery h2{margin:5px 0 8px}.delivery-form{display:block}.deal-list{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:16px 0}.deal-box{display:block;height:100%;padding:15px;border:2px solid #7e2b1f;border-radius:15px;background:#4a0d0c}.deal-box strong,.deal-box span{display:block}.deal-box .price{font-size:23px;color:var(--gold);font-weight:950;margin-top:7px}.quantity{display:flex;align-items:center;gap:10px;margin-top:12px}.quantity input{width:85px;flex:none;padding:10px;text-align:center}.subtotal{font-size:24px;font-weight:950;color:var(--gold);text-align:right;margin:14px 0}.location-row{display:flex;gap:10px}.status-paid{color:#88f29b}.status-pending{color:#ffe45f}.status-cancelled,.status-wasted_journey{color:#ff9f91}.debt-warning{padding:20px;margin:16px 0;border:3px solid #ff5b4c;border-radius:16px;background:linear-gradient(135deg,#a31313,#4a0707);box-shadow:0 0 24px #ff2b1f55}.debt-warning strong{font-size:22px;color:#fff06a}
.order-progress{display:grid;grid-template-columns:repeat(5,1fr);gap:5px;margin:18px 0 8px}.order-step{text-align:center;color:#d8aaa1;font-size:11px;font-weight:850}.order-step:before{content:'✓';display:grid;place-items:center;width:30px;height:30px;margin:0 auto 5px;border-radius:50%;background:#3a1111;border:2px solid #7e3e35;color:#d8aaa1}.order-step.done{color:#fff2c9}.order-step.done:before{background:#159447;border-color:#7effa4;color:#fff}.order-step.current{color:var(--gold)}.order-step.current:before{content:'•';background:#ffb91e;border-color:#fff07b;color:#5b0d08;box-shadow:0 0 14px #ffc40088}.reorder-row{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:13px 0;border-bottom:1px solid #ffffff12}.reorder-row:last-child{border-bottom:0}.reorder-row button{padding:9px 12px;white-space:nowrap;box-shadow:none}.reorder-message{display:none;margin:12px 0}.reorder-message.show{display:block}
.account-choice{display:grid;gap:12px;margin-top:20px}.account-choice details{background:#3d0a0a;border:1px solid #ffda4e66;border-radius:16px;overflow:hidden}.account-choice details[open]{border-color:var(--gold);background:#4d0d0c}.account-choice summary{cursor:pointer;padding:17px 18px;color:var(--gold);font-size:18px;font-weight:950;list-style:none}.account-choice summary::-webkit-details-marker{display:none}.account-choice summary:after{content:'+';float:right}.account-choice details[open] summary:after{content:'−'}.choice-body{padding:0 18px 18px}.choice-body p{margin:0 0 10px}.choice-body form{flex-direction:column;margin-top:10px}
.loyalty-progress{margin:15px 0}.progress-track{height:20px;border-radius:99px;background:#250606;border:1px solid #ffda4e;overflow:hidden}.progress-fill{height:100%;background:linear-gradient(90deg,#ff9e19,#fff05b);border-radius:99px;transition:width .4s}.progress-text{display:flex;justify-content:space-between;margin-top:7px;font-weight:800}.vip-card{grid-column:1/-1;background:linear-gradient(135deg,#151515,#7b150c);border:2px solid var(--gold);box-shadow:inset 0 0 22px #ffbc2340}.vip-card .num{color:var(--gold)}.vip-benefits{margin:8px 0 0;color:#fff5d4}.ownership{margin-top:9px;color:#fff3b0;font-weight:900;letter-spacing:.5px}.order-status{border:2px solid var(--gold);background:linear-gradient(135deg,#7e180f,#4b0b0a)}#status-toast{position:fixed;left:50%;bottom:24px;transform:translate(-50%,130%);width:min(560px,90vw);padding:18px;background:#ffe33b;color:#4d0b08;border-radius:16px;font-weight:950;text-align:center;box-shadow:0 12px 40px #000a;z-index:10;transition:transform .25s}#status-toast.show{transform:translate(-50%,0)}
.app-tabs{position:sticky;top:8px;z-index:8;display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin:0 0 16px;padding:7px;background:#260607e8;border:1px solid #ffda4e80;border-radius:16px;box-shadow:0 8px 28px #16000099;backdrop-filter:blur(10px)}.app-tab{min-width:0;padding:11px 7px;border-radius:11px;background:#5f100d;color:#fff4db;border:1px solid #ffda4e55;box-shadow:none;font-size:13px}.app-tab[aria-selected="true"]{background:linear-gradient(135deg,#fff05b,#ffbf18);color:#60100b;border-color:#fff08b}.app-tab .tab-icon{display:block;font-size:21px;line-height:1.1}.app-view{display:block}.app-ready .app-view{display:none}.app-ready .app-view.active{display:block;animation:page-in .16s ease-out}@keyframes page-in{from{opacity:.4;transform:translateY(5px)}to{opacity:1;transform:none}}.app-page{border:2px solid #ffb52b;border-radius:18px;background:#290808;overflow:hidden}.app-page.delivery{margin-top:0;padding:0}.app-page-title{margin:0;padding:16px 18px;color:var(--gold);font-size:22px;font-weight:950;border-bottom:1px solid #ffda4e33}.section-drawer{margin-top:18px;border:2px solid #ffb52b;border-radius:18px;background:#290808;overflow:hidden}.section-drawer>summary{padding:18px;cursor:pointer;list-style:none;color:var(--gold);font-size:20px;font-weight:950}.section-drawer>summary::-webkit-details-marker{display:none}.section-drawer>summary:after{content:'+';float:right;font-size:25px}.section-drawer[open]>summary:after{content:'−'}.drawer-body{padding:18px 20px 22px}.section-drawer.delivery{padding:0}.section-drawer .history{margin-top:18px}.compact-info{margin-top:16px}.compact-info summary{cursor:pointer;color:var(--gold);font-weight:900}.account-head{display:flex;align-items:end;justify-content:space-between;gap:12px;margin-bottom:12px}.account-head .name{margin:2px 0}.page-hint{margin:0;color:var(--muted)}.review-box{margin-top:10px;padding:13px;border:1px solid #ffda4e66;border-radius:14px;background:#3c0a0a}.stars{color:var(--gold);font-size:20px;letter-spacing:2px}.reviewed{color:#9dffab;font-weight:850}.rating-overlay{position:fixed;inset:0;z-index:100;display:grid;place-items:center;padding:16px;background:#130000e8;backdrop-filter:blur(8px)}.rating-popup{width:min(560px,100%);max-height:94vh;overflow:auto;padding:24px;background:linear-gradient(145deg,#8b1710,#310708);border:3px solid var(--gold);border-radius:24px;box-shadow:0 0 45px #ffbd2490;text-align:center}.rating-popup h2{font-size:30px;line-height:1.1;margin:7px 0}.rating-popup form{display:block}.rating-options{display:grid;grid-template-columns:repeat(5,1fr);gap:7px;margin:18px 0}.star-choice{position:relative;cursor:pointer}.star-choice input{position:absolute;opacity:0;pointer-events:none}.star-choice span{display:block;padding:12px 3px;border:2px solid #ffda4e70;border-radius:12px;background:#4b0a08;color:#ffe53b;font-size:18px;font-weight:950}.star-choice input:checked+span{background:#ffe53b;color:#5b0b07;border-color:#fff;transform:scale(1.06);box-shadow:0 0 16px #ffe53b99}.rating-popup input[type="text"]{margin-bottom:12px}.rating-popup .later{display:block;width:100%;margin-top:12px;padding:10px;background:transparent;color:#ffeab3;border:0;box-shadow:none;text-decoration:underline}
@media(max-width:560px){form,.location-row{flex-direction:column}button{width:100%}.wrap{width:min(100% - 18px,760px);padding-top:10px}.brand{margin-bottom:9px}.logo-frame{width:125px;border-radius:13px;margin-bottom:7px}.tag{padding:4px 12px;font-size:10px}.card{padding:14px;border-radius:19px}.account-head{margin-bottom:8px}.account-head .label{font-size:11px}.account-head .name{font-size:25px}.grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.stat{padding:12px}.stat .label{font-size:11px;letter-spacing:.3px}.num{font-size:22px}.wide,.vip-card{grid-column:1/-1}.vip-benefits{font-size:14px}.deal-list{grid-template-columns:1fr}.app-tabs{top:5px;margin-bottom:12px}.app-tab{padding:9px 3px;font-size:11px}.app-tab .tab-icon{font-size:19px}.app-page-title{font-size:19px;padding:13px 14px}.drawer-body{padding:13px 14px 17px}.delivery{margin-top:0}.deal-box{padding:12px}.deal-box .price{font-size:20px}input,select,textarea{padding:13px}.subtotal{font-size:20px}.notice{padding:14px}.ownership{display:none}.order-progress{gap:2px}.order-step{font-size:9px}.order-step:before{width:25px;height:25px}.reorder-row{align-items:flex-start;flex-direction:column}.reorder-row button{width:auto}}
@media(max-width:560px){.rating-popup{padding:19px 14px}.rating-popup h2{font-size:25px}.star-choice span{font-size:15px;padding:11px 1px}}
"""


def page(title: str, content: str) -> str:
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{html.escape(title)}</title><style>{CSS}</style></head><body><main class="wrap"><div class="brand"><div class="logo-frame"><img src="/snr-logo.png" alt="Official Snr. Buns logo" width="1254" height="1254"></div><div class="tag">LOYALTY • DELIVERY • VIP</div></div>{content}<footer>SNR Buns • Your account is protected by your password</footer></main></body></html>'''


def name_options(names: list[str], selected: str = "") -> str:
    items = [f'<option value="" disabled{"" if selected else " selected"}>Choose your character name</option>']
    wanted = normalize_name(selected)
    for name in sorted(set(names), key=normalize_name):
        mark = " selected" if normalize_name(name) == wanted else ""
        items.append(f'<option value="{html.escape(name, quote=True)}"{mark}>{html.escape(name)}</option>')
    return "".join(items)


def question_options() -> str:
    return "".join(f'<option value="{key}">{html.escape(label)}</option>'
                   for key, label in SECURITY_QUESTIONS.items())


def birthday_options() -> tuple[str, str]:
    months = ''.join(f'<option value="{month}">{date(2000, month, 1).strftime("%B")}</option>'
                     for month in range(1, 13))
    days = ''.join(f'<option value="{day}">{day}</option>' for day in range(1, 32))
    return months, days


def login_page(names: list[str], message: str = "", selected: str = "") -> str:
    notice = f'<div class="notice">{html.escape(message)}</div>' if message else ""
    options = name_options(names, selected)
    questions = question_options()
    return page("SNR Buns Loyalty Card", f'''
    <section class="card">
      <div class="label">SNR Loyalty Card</div>
      <h1>Manage your loyalty card</h1>
      <p class="muted">Log in, make a new card or reset your password.</p>
      {notice}
      <div class="account-choice">
        <details open>
          <summary>Log in to my card</summary>
          <div class="choice-body">
            <p class="muted">Use your character name and password.</p>
            <form method="post" action="/login">
              <select name="name" required>{options}</select>
              <input type="password" name="password" minlength="10" maxlength="128" autocomplete="current-password" placeholder="Your password" required>
              <button type="submit">Log In</button>
            </form>
          </div>
        </details>
        <details>
          <summary>Create a new loyalty card</summary>
          <div class="choice-body">
            <p class="muted">Enter your exact in-game name. Your card works immediately.</p>
            <form method="post" action="/request-access">
              <input name="name" minlength="2" maxlength="60" autocomplete="username" placeholder="Exact character name" required>
              <input type="password" name="password" minlength="10" maxlength="128" autocomplete="new-password" placeholder="Create password (10+ characters)" required>
              <input type="password" name="confirm" minlength="10" maxlength="128" autocomplete="new-password" placeholder="Repeat password" required>
              <select name="security_question" required><option value="" disabled selected>Choose a memorable question</option>{questions}</select>
              <input type="password" name="security_answer" minlength="3" maxlength="80" autocomplete="off" placeholder="Your memorable answer" required>
              <button type="submit">Create My Card</button>
            </form>
          </div>
        </details>
        <details>
          <summary>Forgot my password</summary>
          <div class="choice-body">
            <p class="muted">Use the memorable answer you chose.</p>
            <form method="post" action="/reset-password">
              <select name="name" required>{options}</select>
              <select name="security_question" required><option value="" disabled selected>Choose your memorable question</option>{questions}</select>
              <input type="password" name="security_answer" minlength="3" maxlength="80" autocomplete="off" placeholder="Your memorable answer" required>
              <input type="password" name="password" minlength="10" maxlength="128" autocomplete="new-password" placeholder="Create new password" required>
              <input type="password" name="confirm" minlength="10" maxlength="128" autocomplete="new-password" placeholder="Repeat new password" required>
              <button type="submit">Reset Password</button>
            </form>
          </div>
        </details>
      </div>
      <details class="section-drawer compact-info">
        <summary>Membership levels</summary><div class="drawer-body">
        <div class="label">👑 SNR Membership Levels</div>
        <p><strong>Regular</strong> from your first purchase<br>
        <strong>🥉 Bronze</strong> at 10 purchases — +1 Golden Ticket every purchase<br>
        <strong>🥈 Silver</strong> at 25 purchases — +1 Golden Ticket every purchase<br>
        <strong>🥇 Gold</strong> at 50 purchases — +1 loyalty point and +1 Golden Ticket<br>
        <strong>💎 Platinum</strong> at 100 purchases — +1 loyalty point and +2 Golden Tickets<br>
        <strong>👑 SNR VIP</strong> at 200 purchases — +2 loyalty points and +3 Golden Tickets</p>
        <p class="muted">Membership upgrades automatically. Log in to see your current level and progress.</p>
        </div>
      </details>
      <details class="section-drawer compact-info">
        <summary>How Golden Tickets work</summary><div class="drawer-body">
        <div class="label">🎟️ £5,000 Golden Ticket Draw</div>
        <p><strong>Every SNR meal deal automatically issues its listed Golden Ticket(s).</strong></p>
        <p>The tickets enter the live draw automatically and are checked instantly against one secret winner hidden among 1,000 tickets. You never need to enter a number yourself. If you win, your account and the staff sale receipt display a clear £5,000 winner alert.</p>
        </div>
      </details>
    </section>''')


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
    progress = min(points, 4)
    progress_label = "Reward ready!" if points >= 4 else f"{progress} of 4 points"
    progress_bar = f'''<div class="loyalty-progress"><div class="progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="4" aria-valuenow="{progress}"><div class="progress-fill" style="width:{progress * 25}%"></div></div><div class="progress-text"><span>⭐ {progress_label}</span><span>🎴 Free pack</span></div></div>'''
    if pending:
        action = "<p>Your request is saved. Visit SNR Buns to collect your pack from staff.</p>"
    elif not claims.configured():
        action = "<p>Online claims are being set up. Please ask staff.</p>"
    elif points < 4:
        action = f"<p>Collect {4-points} more loyalty point(s) to request your next pack.</p>"
    else:
        action = f'<form method="post" action="/claim"><input type="hidden" name="claim_request_key" value="{html.escape(form_token, quote=True)}"><button type="submit">Claim Trading Card Pack</button></form>'
    badge = " — READY" if points >= 4 else f" — {points}/4 points"
    return f'<section class="app-page" id="rewards"><h2 class="app-page-title">🎁 Claim Reward{badge}</h2><div class="drawer-body"><p><strong>Reach 4 points to claim 1 pack containing 2 trading cards.</strong></p>{progress_bar}<p>{points} available points.</p><p class="muted">After staff hand over your pack, your loyalty points reset to 0.</p>{action}<div class="muted">{history}</div></div></section>'


def order_tracker(status: str, pickup: bool) -> str:
    steps = ([('pending', 'Placed'), ('accepted', 'Accepted'), ('ready_for_pickup', 'Ready'),
              ('processing', 'Payment'), ('paid', 'Collected')]
             if pickup else
             [('pending', 'Placed'), ('accepted', 'Accepted'), ('on_way', 'On Way'),
              ('arrived', 'Arrived'), ('paid', 'Complete')])
    progress_status = status
    if status == 'processing':
        progress_status = 'processing' if pickup else 'arrived'
    current_index = next((index for index, (key, _) in enumerate(steps) if key == progress_status), 0)
    parts = []
    for index, (key, label) in enumerate(steps):
        state = ' current' if index == current_index else (' done' if index < current_index else '')
        parts.append(f'<div class="order-step{state}" data-track-status="{key}">{label}</div>')
    return '<div class="order-progress" aria-label="Live order progress">' + ''.join(parts) + '</div>'


def rating_popup(customer: dict, orders: DeliveryStore, form_token: str) -> str:
    rows = orders.summary(customer["customer_key"], 1)
    latest = rows[0] if rows else None
    if (not latest or latest["status"] != "paid" or not latest.get("assigned_driver_id")
            or orders.review_for_order(latest["id"])):
        return ""
    pickup = (latest.get("fulfillment_type") or "delivery") == "pickup"
    subject = "pickup experience" if pickup else "driver"
    staff_name = html.escape(latest["assigned_driver_name"])
    star_buttons = ''.join(
        f'<label class="star-choice"><input type="radio" name="rating" value="{score}" required><span>{score} ★</span></label>'
        for score in range(1, 6))
    return f'''<div class="rating-overlay" id="rating-popup" role="dialog" aria-modal="true" aria-labelledby="rating-title"><div class="rating-popup"><div class="label">✅ ORDER #{int(latest["id"])} COMPLETE</div><h2 id="rating-title">How was your order?</h2><p>Rate your {subject} with <strong>{staff_name}</strong>.</p><form method="post" action="/review"><input type="hidden" name="review_request_key" value="{html.escape(form_token, quote=True)}"><input type="hidden" name="order_id" value="{int(latest["id"])}"><div class="rating-options" aria-label="Choose a rating from 1 to 5 stars">{star_buttons}</div><input type="text" name="comment" maxlength="250" placeholder="Optional short review" aria-label="Optional review"><button type="submit">Send My Rating</button><button class="later" id="rating-later" type="button">Maybe later</button></form></div></div>'''


def delivery_section(customer: dict, orders: DeliveryStore, shifts: StaffShifts, form_token: str) -> str:
    rows = orders.summary(customer["display_name"])
    fee = orders.outstanding_fee(customer["customer_key"])
    active = next((row for row in rows if row["status"] in ("pending", "accepted", "on_way", "arrived", "ready_for_pickup", "processing")), None)
    labels = {
        "pending": "Waiting for a driver to accept",
        "accepted": "Accepted — your order is being prepared",
        "on_way": "Your driver is on the way",
        "arrived": "Your driver has arrived and is waiting outside",
        "ready_for_pickup": "Your order is ready for collection at SNR Buns",
        "processing": "Payment is being confirmed",
        "paid": "Delivered, paid and added to loyalty",
        "cancelled": "Cancelled",
        "wasted_journey": "Wasted journey — £500 delivery fee owed",
    }
    can_reorder = not active and not fee and orders.configured()
    recent_parts = []
    for row in rows:
        pickup_row = (row.get("fulfillment_type") or "delivery") == "pickup"
        description = (f'{"🛍️ Pickup" if pickup_row else "🚗 Delivery"} #{row["id"]}: '
                       f'<strong class="status-{"pending" if row["status"] in ACTIVE_ORDER_STATUSES else row["status"]}">'
                       f'{labels.get(row["status"], row["status"])}</strong><br>'
                       f'{html.escape(row["deal_name"])} • £{int(row["price"]):,}')
        reorder = ''
        if can_reorder and row["status"] == "paid":
            payload = html.escape(json.dumps({item["key"]: int(item["quantity"]) for item in orders.items(row)}, separators=(",", ":")), quote=True)
            mode = html.escape(row.get("fulfillment_type") or "delivery", quote=True)
            reorder = f'<button class="secondary reorder-button" type="button" data-reorder="{payload}" data-mode="{mode}">Order Again</button>'
        review_html = ''
        if row["status"] == "paid" and row.get("assigned_driver_id"):
            review = orders.review_for_order(row["id"])
            subject = "pickup experience" if pickup_row else "driver"
            if review:
                stars = "★" * int(review["rating"]) + "☆" * (5 - int(review["rating"]))
                comment = f'<br><small>“{html.escape(review["comment"]) }”</small>' if review.get("comment") else ''
                review_html = f'<div class="review-box reviewed">Your {subject} rating: <span class="stars">{stars}</span>{comment}</div>'
        recent_parts.append(f'<div class="reorder-row"><div>{description}{review_html}</div>{reorder}</div>')
    recent = ''.join(recent_parts)
    if active:
        pickup = (active.get("fulfillment_type") or "delivery") == "pickup"
        handler_label = "Handling staff" if pickup else "Driver"
        driver = (f'<br>{handler_label}: <strong>{html.escape(active["assigned_driver_name"])}</strong>'
                  if active.get("assigned_driver_name") else "")
        note = f'<br>Your note: {html.escape(active["notes"])}' if active.get("notes") else ""
        subtotal = int(active.get("subtotal") or active["price"])
        delivery_fee = int(active.get("delivery_fee") or 0)
        discount = int(active.get("discount_amount") or 0)
        birthday_discount = int(active.get("birthday_discount") or 0)
        charge_label = "Pickup charge" if pickup else "Delivery"
        discount_line = (f'<br>Discount ({html.escape(active.get("discount_code") or "code")}): '
                         f'<strong>−£{discount:,}</strong>' if discount else "")
        birthday_line = (f'<br>🎂 Birthday reward: <strong>−£{birthday_discount:,}</strong>'
                         if birthday_discount else "")
        order_form = (
            f'<div class="notice order-status" data-order-id="{active["id"]}" data-order-status="{active["status"]}">'
            f'<strong>Order #{active["id"]}: {labels[active["status"]]}</strong><br>'
            f'{order_tracker(active["status"], pickup)}'
            f'{html.escape(active["deal_name"])}<br>Food subtotal: £{subtotal:,}'
            f'{discount_line}{birthday_line}<br>{charge_label}: {"FREE" if delivery_fee == 0 else f"£{delivery_fee:,}"}'
            f'<br><strong>Total owed: £{int(active["price"]):,}</strong><br>'
            f'{"Collection: SNR Buns" if pickup else "Delivery location: " + html.escape(active["postal"])}'
            f'{driver}{note}</div><div id="status-toast" role="status"></div><script src="/delivery.js" defer></script>'
        )
    elif fee:
        order_form = f'''<div class="debt-warning"><strong>⚠️ £{int(fee["amount"]):,} OWED</strong><br>Wasted Journey fee from delivery order #{int(fee["order_id"])}.<br><br>New deliveries are unavailable until SNR staff mark this fee as paid or waived.</div>'''
    else:
        choices = "".join(
            f'''<div class="deal-box"><strong>{html.escape(deal.name)}</strong><span>{html.escape(deal.item_summary)}</span><span>{deal.loyalty_points} loyalty point(s) • {deal.golden_tickets} Golden ticket(s)</span><span class="price">£{deal.price:,} each</span><label class="quantity">Amount <input class="deal-qty" type="number" name="qty_{deal.key}" value="0" min="0" max="10" step="1" data-price="{deal.price}" aria-label="Amount of {html.escape(deal.name, quote=True)}"></label></div>'''
            for deal in DEALS.values())
        if orders.configured():
            delivery_fee = int(customer["membership"]["delivery_fee"])
            fee_text = "FREE — SNR VIP benefit" if delivery_fee == 0 else f"£{delivery_fee:,}"
            drivers = shifts.drivers_available()
            mode_options = ('<option value="delivery" selected>🚗 Delivery</option><option value="pickup">🛍️ Pickup from SNR Buns</option>'
                            if drivers else
                            '<option value="pickup" selected>🛍️ Pickup from SNR Buns</option><option value="delivery" disabled>🚗 Delivery — no drivers available</option>')
            availability = ("" if drivers else '<div class="notice"><strong>No delivery drivers are clocked in.</strong><br>Pickup ordering is still available.</div>')
            birthday = orders.birthday_status(customer["customer_key"])
            birthday_notice = (f'<div class="notice"><strong>🎂 Happy Birthday — {html.escape(birthday["reward"])}!</strong><br>Your birthday reward will be applied automatically to this order.</div>'
                               if birthday["eligible"] else "")
            order_form = f'''{availability}{birthday_notice}<div id="reorder-message" class="notice reorder-message" role="status"></div><form class="delivery-form" method="post" action="/order" data-delivery-fee="{delivery_fee}"><input type="hidden" name="order_request_key" value="{html.escape(form_token, quote=True)}"><label><strong>How would you like your order?</strong><select id="fulfillment-type" name="fulfillment_type" required>{mode_options}</select></label><div class="deal-list">{choices}</div><div class="subtotal" aria-live="polite">Food subtotal: <span id="delivery-subtotal">£0</span><br><small id="order-fee-label">Membership delivery: {fee_text}</small><br>Total before discount: <span id="delivery-total">£{delivery_fee if drivers else 0:,}</span></div><input name="discount_code" maxlength="20" autocomplete="off" placeholder="Discount code (optional)" aria-label="Discount code"><textarea name="notes" maxlength="200" placeholder="Optional order notes — meeting point, no ice, call when nearby…" aria-label="Optional order notes"></textarea><div class="location-row"><input id="delivery-location" name="postal" minlength="2" maxlength="80" autocomplete="street-address" placeholder="Required postal or delivery location" aria-label="Postal or delivery location" {"required" if drivers else "hidden"}><button type="submit">Place Order</button></div><p class="muted">Pickup is always free. Delivery uses your membership price. Choose up to 10 of each deal (20 deals total). Rewards are added only after staff confirm payment.</p></form><script src="/delivery.js" defer></script>'''
        else:
            order_form = f'<div class="deal-list">{choices}</div><div class="notice">Online delivery is being set up. Please contact SNR Buns for now.</div>'
    return f'''<section class="app-page delivery" id="delivery"><h2 class="app-page-title">🍔 Order Food — Delivery or Pickup</h2><div class="drawer-body"><p class="muted">Choose your deals, then select delivery or collection.</p>{order_form}<details class="compact-info"><summary>Previous orders</summary><div class="history">{recent or '<p class="muted">No orders yet.</p>'}</div></details></div></section>'''


def customer_page(customer: dict, claims: ClaimStore, orders: DeliveryStore, shifts: StaffShifts,
                  accounts: Accounts, claim_token: str, order_token: str, security_token: str) -> str:
    recent = "".join(f'<div class="sale"><div><strong>{html.escape(str(s["deal_name"]))}</strong><br><small>{_sale_date(s["created_at"])}</small></div><span>+{int(s["loyalty_points"])} ⭐</span></div>' for s in customer.get("recent_sales", [])) or '<div class="notice">No recent visits to show.</div>'
    jackpot = ('''<strong>🏆 YOU HAVE A WINNING GOLDEN TICKET!</strong><br>Your account has won the £5,000 jackpot. Speak to SNR staff to verify and collect the prize.'''
               if int(customer["jackpot_wins"]) else
               f'''<details class="compact-info"><summary>My {int(customer["golden_tickets"])} automatic Golden Ticket(s)</summary><p>Every meal deal issues its listed ticket(s). They are entered automatically, then each ticket is checked instantly against one secret winner hidden among 1,000 tickets. You do not need to enter anything.</p></details>''')
    recovery = '' if accounts.has_security(customer['customer_key']) else f'''<section class="notice"><strong>Protect your password recovery</strong><p>This older account needs a memorable question. Set it now so you can reset your own password later.</p><form method="post" action="/set-security"><input type="hidden" name="security_request_key" value="{html.escape(security_token, quote=True)}"><select name="security_question" required><option value="" disabled selected>Choose a memorable question</option>{question_options()}</select><input type="password" name="security_answer" minlength="3" maxlength="80" autocomplete="off" placeholder="Your memorable answer" required><button type="submit">Save Memorable Answer</button></form></section>'''
    fee = orders.outstanding_fee(customer["customer_key"])
    debt = (f'''<div class="debt-warning"><strong>⚠️ DELIVERY ACCOUNT: £{int(fee["amount"]):,} OWED</strong><br>Wasted Journey fee. Please speak to SNR staff. New delivery orders are blocked until it is paid or waived.</div>'''
            if fee else '')
    membership = customer["membership"]
    next_text = (f'''<p class="muted">Complete {membership["remaining"]} more purchase(s) to unlock {html.escape(membership["next_level"])}.</p>'''
                 if membership["next_level"] else '<p class="muted">You have reached your current highest membership level.</p>')
    delivery_benefit = ("FREE delivery" if int(membership["delivery_fee"]) == 0
                        else f'£{int(membership["delivery_fee"]):,} delivery')
    vip = f'''<div class="stat vip-card"><div class="label">SNR Customer Membership</div><div class="num">{membership["emoji"]} {html.escape(membership["name"])}</div><p class="vip-benefits">Every purchase earns the normal rewards <strong>plus {membership["bonus_points"]} loyalty point(s) and {membership["bonus_tickets"]} Golden Ticket(s)</strong>.<br>Delivery benefit: <strong>{delivery_benefit}</strong>.</p>{next_text}<small>Regular 0+ • Bronze 10+ • Silver 25+ • Gold 50+ • Platinum 100+ • SNR VIP 200+</small></div>'''
    recovery = debt + recovery
    birthday = orders.birthday_status(customer["customer_key"])
    if birthday["saved"]:
        birthday_box = ('<div class="notice"><strong>🎂 Birthday saved</strong><br>Birthday rewards are currently paused by SNR Buns.</div>'
                        if not birthday["active"] else
                        f'<div class="notice"><strong>🎂 Happy Birthday!</strong><br>Your {html.escape(birthday["reward"])} reward is ready in the Order page.</div>'
                        if birthday["eligible"] else
                        f'<div class="notice"><strong>🎂 Birthday saved: {html.escape(birthday["date"])}</strong><br>Your annual reward: {html.escape(birthday["reward"] or "Birthday offer")}.</div>')
    else:
        months, days = birthday_options()
        birthday_box = f'''<details class="section-drawer compact-info"><summary>🎂 Add my birthday reward</summary><div class="drawer-body"><p>Save your birthday day and month once. For security, rewards activate seven days after it is saved.</p><form method="post" action="/birthday"><input type="hidden" name="birthday_request_key" value="{html.escape(security_token, quote=True)}"><select name="birthday_day" required><option value="" disabled selected>Day</option>{days}</select><select name="birthday_month" required><option value="" disabled selected>Month</option>{months}</select><button type="submit">Save Birthday</button></form></div></details>'''
    review_popup = rating_popup(customer, orders, order_token)
    return page(f'{customer["display_name"]} • SNR Loyalty', f'''<section class="card" id="customer-app">{review_popup}
      <div class="account-head"><div><div class="label">Logged-in customer</div><div class="name">{html.escape(customer["display_name"])}</div></div><p class="page-hint">My SNR app</p></div>
      <nav class="app-tabs" aria-label="Customer account pages" role="tablist">
        <button class="app-tab" type="button" role="tab" aria-selected="true" data-tab-target="home"><span class="tab-icon">🏠</span>Home</button>
        <button class="app-tab" type="button" role="tab" aria-selected="false" data-tab-target="order"><span class="tab-icon">🍔</span>Order</button>
        <button class="app-tab" type="button" role="tab" aria-selected="false" data-tab-target="rewards"><span class="tab-icon">🎁</span>Rewards</button>
        <button class="app-tab" type="button" role="tab" aria-selected="false" data-tab-target="visits"><span class="tab-icon">📋</span>Visits</button>
      </nav>
      <div class="app-view active" data-app-view="home" role="tabpanel">{recovery}{birthday_box}<div class="grid">{vip}
        <div class="stat"><div class="label">Available points</div><div class="num">⭐ {int(customer["loyalty_points"])}</div></div>
        <div class="stat"><div class="label">Golden tickets</div><div class="num">🎟️ {int(customer["golden_tickets"])}</div></div>
        <div class="stat"><div class="label">Visits</div><div class="num">🍔 {int(customer["lifetime_sales"])}</div></div>
        <div class="stat"><div class="label">Items served</div><div class="num">🥤 {int(customer["food_sold"])+int(customer["drinks_sold"])}</div></div>
        <div class="stat wide jackpot"><div class="label">£5,000 Golden Ticket Jackpot</div><div>{jackpot}</div><small>The winning position remains hidden.</small></div>
      </div><form method="post" action="/logout"><input type="hidden" name="logout" value="1"><button class="secondary" type="submit">Log Out</button></form></div>
      <div class="app-view" data-app-view="order" role="tabpanel">{delivery_section(customer, orders, shifts, order_token)}</div>
      <div class="app-view" data-app-view="rewards" role="tabpanel">{claim_section(customer, claims, claim_token)}</div>
      <div class="app-view" data-app-view="visits" role="tabpanel"><section class="app-page" id="history"><h2 class="app-page-title">📋 My Recent Visits</h2><div class="drawer-body">{recent}</div></section></div>
      <script src="/portal.js" defer></script>
    </section>''')


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
    limiter, claims, orders, accounts, shifts = Limiter(), ClaimStore(db), DeliveryStore(db), Accounts(db), StaffShifts(db)
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
            parsed = parse_qs(self.rfile.read(length).decode("utf-8"), max_num_fields=16, keep_blank_values=True)
            return {key: values[0] for key, values in parsed.items()}

        def send_html(self, status: int, body: str, cookie: str | None = None) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Robots-Tag", "noindex, nofollow")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'self'; form-action 'self'; base-uri 'none'; object-src 'none'")
            self.send_header("Cache-Control", "no-store")
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, status: int, value: dict) -> None:
            data = json.dumps(value).encode("utf-8")
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

        def show_account(self) -> None:
            owner = self.owner()
            customer = db.get_customer(owner) if owner else None
            if not customer:
                self.send_html(401, login_page(db.customer_names(), "Please log in to open a loyalty account."))
                return
            self.send_html(200, customer_page(
                customer, claims, orders, shifts, accounts, make_form_token(owner),
                make_form_token(owner), make_form_token(owner)
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
            elif path == "/delivery.js":
                data = b'''document.addEventListener("DOMContentLoaded",()=>{
const q=[...document.querySelectorAll(".deal-qty")],o=document.getElementById("delivery-subtotal"),z=document.getElementById("delivery-total"),f=document.querySelector(".delivery-form"),mode=document.getElementById("fulfillment-type"),loc=document.getElementById("delivery-location"),feeLabel=document.getElementById("order-fee-label"),fee=parseInt(f?.dataset.deliveryFee||"0",10);
const currentFee=()=>mode?.value==="pickup"?0:fee;const total=()=>{let t=0;q.forEach(x=>t+=(parseInt(x.value||"0",10)||0)*parseInt(x.dataset.price,10));if(o)o.textContent="\\u00a3"+t.toLocaleString("en-GB");if(z)z.textContent="\\u00a3"+(t+currentFee()).toLocaleString("en-GB")};const syncMode=()=>{const pickup=mode?.value==="pickup";if(loc){loc.hidden=pickup;loc.required=!pickup}if(feeLabel)feeLabel.textContent=pickup?"Pickup charge: FREE":"Membership delivery: \\u00a3"+fee.toLocaleString("en-GB");total()};q.forEach(x=>x.addEventListener("input",total));mode?.addEventListener("change",syncMode);syncMode();
document.querySelectorAll(".reorder-button").forEach(b=>b.addEventListener("click",()=>{try{const wanted=JSON.parse(b.dataset.reorder||"{}");q.forEach(x=>x.value=String(wanted[x.name.replace("qty_","")]||0));if(mode){const option=[...mode.options].find(x=>x.value===b.dataset.mode&&!x.disabled);mode.value=option?option.value:"pickup"}syncMode();const m=document.getElementById("reorder-message");if(m){m.textContent="Your previous basket is ready below. Check the amounts and press Place Order when ready.";m.classList.add("show")}f?.scrollIntoView({behavior:"smooth",block:"start"})}catch(e){}}));
const tracker=document.querySelector("[data-order-id]");if(!tracker)return;let current=tracker.dataset.orderStatus;
const messages={accepted:"Your order has been accepted!",on_way:"Your driver is on the way!",arrived:"Your SNR Buns driver has arrived and is waiting outside!",ready_for_pickup:"Your order is ready for collection at SNR Buns!",paid:"Your order is complete. Your Golden Tickets were issued and entered automatically!",cancelled:"Your order was cancelled.",wasted_journey:"A \\u00a3500 Wasted Journey fee has been added to your account. Please contact SNR staff."};
setInterval(async()=>{try{const r=await fetch("/order-status",{cache:"no-store"});if(!r.ok)return;const d=await r.json();if(d.id==tracker.dataset.orderId&&d.status!==current){current=d.status;const toast=document.getElementById("status-toast");let message=(d.status==="paid"&&d.jackpot_won)?"WINNER! One of your automatic Golden Tickets won the \\u00a35,000 jackpot! Speak to SNR staff now.":(messages[d.status]||"Your order status has changed.");if(d.driver&&!["cancelled","wasted_journey"].includes(d.status))message+=(d.fulfillment_type==="pickup"?" Handling staff: ":" Driver: ")+d.driver;if(toast){toast.textContent=message;toast.classList.add("show")}document.title="SNR UPDATE: "+message;if(navigator.vibrate)navigator.vibrate([200,100,200]);if(d.status==="paid"){location.href="/account#order"}else{setTimeout(()=>location.reload(),1800)}}}catch(e){}},2000);
});'''
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            elif path == "/portal.js":
                data = b'''document.addEventListener("DOMContentLoaded",()=>{
const app=document.getElementById("customer-app"),tabs=[...document.querySelectorAll("[data-tab-target]")],views=[...document.querySelectorAll("[data-app-view]")];if(!app||!tabs.length||!views.length)return;app.classList.add("app-ready");
const names=new Set(views.map(v=>v.dataset.appView));const show=name=>{if(!names.has(name))name="home";tabs.forEach(t=>t.setAttribute("aria-selected",String(t.dataset.tabTarget===name)));views.forEach(v=>v.classList.toggle("active",v.dataset.appView===name));history.replaceState(null,"","#"+name);document.getElementById("customer-app")?.scrollIntoView({behavior:"smooth",block:"start"})};
tabs.forEach(t=>t.addEventListener("click",()=>show(t.dataset.tabTarget)));const wanted=location.hash.slice(1);if(names.has(wanted))show(wanted);
document.getElementById("rating-later")?.addEventListener("click",()=>document.getElementById("rating-popup")?.remove());
});'''
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            elif path == "/order-status":
                owner = self.owner()
                if not owner:
                    self.send_json(401, {"error": "login_required"})
                    return
                rows = orders.summary(owner, 1)
                row = rows[0] if rows else None
                outcome = orders.ticket_result(row["id"]) if row and row["status"] == "paid" else {"tickets": 0, "jackpot_won": False}
                self.send_json(200, ({"id": str(row["id"]), "status": row["status"],
                                      "driver": row.get("assigned_driver_name") or "",
                                      "fulfillment_type": row.get("fulfillment_type") or "delivery",
                                      "tickets": outcome["tickets"], "jackpot_won": outcome["jackpot_won"]}
                                     if row else {"id": None, "status": "none", "driver": ""}))
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
                    result = accounts.request_access(
                        data.get("name", ""), data.get("password", ""),
                        data.get("security_question", ""), data.get("security_answer", ""),
                    )
                    self.send_html(200, page("Account created", f'''<section class="card"><div class="label">Account created</div><h1>You’re ready to go!</h1><p>The loyalty account for <strong>{html.escape(result["customer_name"])}</strong> is active now.</p><div class="notice">You can log in immediately using the password you just chose. SNR staff have been notified, but no approval is required.</div><a class="back" href="/">Log in to my account</a></section>'''))
                elif path == "/reset-password":
                    if data.get("password", "") != data.get("confirm", ""):
                        raise ValueError("The two passwords do not match.")
                    accounts.reset_with_answer(
                        data.get("name", ""), data.get("security_question", ""),
                        data.get("security_answer", ""), data.get("password", ""),
                    )
                    self.send_html(200, page("Password reset", '''<section class="card"><div class="label">Password updated</div><h1>Your new password is ready</h1><p>All older website sessions were signed out for your protection.</p><a class="back" href="/">Log in</a></section>'''))
                elif path == "/logout":
                    accounts.logout(self.session_token())
                    self.redirect("/", "snr_session=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=None; Partitioned")
                elif path == "/set-security":
                    owner = self.owner()
                    if not owner:
                        raise ValueError("Your login has expired. Please log in again.")
                    key = data.get("security_request_key", "")
                    if not valid_form_token(owner, key):
                        raise ValueError("This form has expired. Refresh your account and try again.")
                    accounts.set_security_authenticated(
                        owner, data.get("security_question", ""), data.get("security_answer", ""))
                    self.send_html(200, page("Recovery protected", '''<section class="card"><h1>Memorable answer saved</h1><p>You can now use it from the login screen if you forget your password.</p><a class="back" href="/account">Back to my account</a></section>'''))
                elif path == "/birthday":
                    owner = self.owner()
                    if not owner:
                        raise ValueError("Your login has expired. Please log in again.")
                    if not valid_form_token(owner, data.get("birthday_request_key", "")):
                        raise ValueError("This form has expired. Refresh your account and try again.")
                    result = orders.set_birthday_authenticated(
                        owner, data.get("birthday_month", ""), data.get("birthday_day", ""))
                    self.send_html(200, page("Birthday saved", f'''<section class="card"><div class="label">Birthday Reward</div><h1>🎂 {html.escape(result["date"])} saved</h1><p>Your annual <strong>{html.escape(result["reward"] or "birthday reward")}</strong> will appear automatically when it is available.</p><div class="notice">For account security, a newly saved birthday must be on the account for seven days before it can produce a reward.</div><a class="back" href="/account#home">Back to my account</a></section>'''))
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
                    fulfillment_type = data.get("fulfillment_type", "delivery").strip().lower()
                    if fulfillment_type == "delivery" and not shifts.drivers_available():
                        raise ValueError("No drivers are currently available. Please try again when SNR staff have clocked in.")
                    quantities = {deal_key: data.get("qty_" + deal_key, "0") for deal_key in DEALS}
                    result = orders.create_cart_authenticated(
                        owner, quantities, data.get("postal", ""), key, data.get("notes", ""),
                        data.get("discount_code", ""), fulfillment_type)
                    lines = "".join(f'<li>{item["quantity"]} × {html.escape(item["name"])} — £{item["line_total"]:,}</li>' for item in orders.items(result))
                    note = f'<p>Order note: <strong>{html.escape(result["notes"])}</strong></p>' if result.get("notes") else ""
                    discount_line = (f'<p>Discount ({html.escape(result["discount_code"])}): <strong>−£{int(result["discount_amount"]):,}</strong></p>'
                                     if int(result.get("discount_amount") or 0) else "")
                    birthday_line = (f'<p>🎂 Birthday reward: <strong>−£{int(result["birthday_discount"]):,}</strong></p>'
                                     if int(result.get("birthday_discount") or 0) else "")
                    pickup = (result.get("fulfillment_type") or "delivery") == "pickup"
                    fee_text = "FREE" if int(result.get("delivery_fee") or 0) == 0 else f'£{int(result["delivery_fee"]):,}'
                    fee_label = "Pickup charge" if pickup else f'{html.escape(result.get("membership_level") or "Regular")} delivery'
                    place = ("Collection: <strong>SNR Buns</strong>" if pickup else
                             f'Delivery location: <strong>{html.escape(result["postal"])}</strong>')
                    updates = ("Accepted, Ready for Collection and completion" if pickup else
                               "Accepted, Driver On The Way, Driver Arrived and completion")
                    icon_title = "🛍️ Pickup order sent" if pickup else "🚗 Delivery order sent"
                    self.send_html(200, page("Order received", f'''<section class="card"><div class="label">{icon_title}</div><h1>Order #{result["id"]}</h1><ul>{lines}</ul><p>Food subtotal: <strong>£{int(result["subtotal"]):,}</strong></p>{discount_line}{birthday_line}<p>{fee_label}: <strong>{fee_text}</strong></p><p>Total to pay: <strong>£{int(result["price"]):,}</strong></p><p>{place}</p>{note}<div class="notice"><strong>Please allow 5–7 minutes for your order to be confirmed.</strong><br>Keep this page open for {updates} updates.<br><br>After staff confirm payment, your Golden Tickets are issued, checked and entered into the £5,000 draw automatically. This page will immediately alert you if one wins.</div><a class="back" href="/account#order">Track my order</a></section>'''))
                elif path == "/review":
                    owner = self.owner()
                    if not owner:
                        raise ValueError("Your login has expired. Please log in again.")
                    if not valid_form_token(owner, data.get("review_request_key", "")):
                        raise ValueError("This rating form has expired. Refresh your account and try again.")
                    result = orders.create_review_authenticated(
                        owner, data.get("order_id", ""), data.get("rating", ""),
                        data.get("comment", ""))
                    stars = "★" * int(result["rating"]) + "☆" * (5 - int(result["rating"]))
                    subject = "pickup experience" if result["fulfillment_type"] == "pickup" else "driver"
                    self.send_html(200, page("Thanks for your rating", f'''<section class="card"><div class="label">Review received</div><h1>Thank you! <span class="stars">{stars}</span></h1><p>Your {subject} rating for <strong>{html.escape(result["staff_name"])}</strong> has been recorded.</p><div class="notice">SNR staff have been notified. Each completed order can only be rated once.</div><a class="back" href="/account#order">Back to my orders</a></section>'''))
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
