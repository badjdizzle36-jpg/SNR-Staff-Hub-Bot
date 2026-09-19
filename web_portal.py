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
from customer_services import ACTION_LABELS, CustomerServices
from delivery_orders import DeliveryStore
from staff_shifts import StaffShifts
from reward_claims import ClaimStore
from raffles import RaffleStore
from city_run import BUSINESSES, CityRunStore
from city_artwork import poster_markup, sticker_svg
from snr_core import DEALS, VIP_LEVELS, SNRDatabase, normalize_name

LONDON = ZoneInfo("Europe/London")
MAX_REQUESTS_PER_MINUTE = 15
ACTIVE_ORDER_STATUSES = ("accepted", "on_way", "arrived", "ready_for_pickup", "processing")
LOGO_IMAGE = Path(__file__).with_name("snr-logo.png").read_bytes()
CITY_RUN_BOARD_IMAGE = Path(__file__).with_name("city-run-board-v2.png").read_bytes()
# This is the exact perimeter order in the supplied poster (top, right, left,
# bottom). It is deliberately separate from the database collection order so
# a reveal can never show a neighbouring business photo.
CITY_BOARD_PHOTO_ORDER = (
    [f"food-{i}" for i in range(1, 6)]
    + [f"nightlife-{i}" for i in range(1, 6)]
    + [f"shops-{i}" for i in range(1, 6)]
    + [f"luxury-{i}" for i in range(1, 4)]
    + [f"mechanics-{i}" for i in range(1, 7)]
    + [f"motors-{i}" for i in range(1, 5)]
    + [f"finance-{i}" for i in range(1, 5)]
    + [f"services-{i}" for i in range(1, 7)]
)
CITY_STICKER_IMAGES = {
    key: Path(__file__).with_name(f"city-sticker-{index + 1:02d}.jpg")
    for index, key in enumerate(CITY_BOARD_PHOTO_ORDER)
}


def city_business_svg(business_key: str) -> bytes | None:
    """Small exact-name vector artwork; fast enough for embedded game phones."""
    business = next((row for row in BUSINESSES if row["key"] == business_key), None)
    if not business:
        return None
    icons = {
        "food": '<path d="M42 65c2-17 14-27 34-27s32 10 34 27H42zm-3 8h74v9H39zm8 17h58l-6 12H53z"/><circle cx="61" cy="54" r="2"/><circle cx="77" cy="49" r="2"/><circle cx="92" cy="55" r="2"/>',
        "nightlife": '<path d="M47 35h58L82 66v25h15v10H55V91h15V66L47 35zm15 10 14 15 14-15H62z"/><path d="M115 39l4 9 10 1-8 7 3 10-9-5-9 5 3-10-8-7 10-1z"/>',
        "mechanics": '<path d="M51 41a22 22 0 0 0 28 27l27 27-12 12-27-27a22 22 0 0 1-27-28l13 13 12-4 4-12-18-8z"/><circle cx="101" cy="101" r="4"/>',
        "motors": '<path d="M38 79l9-24h58l12 24 8 3v17h-12v-8H48v8H36V82l2-3zm17-16-6 16h56l-8-16H55z"/><circle cx="57" cy="88" r="8"/><circle cx="105" cy="88" r="8"/>',
        "shops": '<path d="M49 53h64l-6 55H55l-6-55zm15 0c0-14 7-23 17-23s17 9 17 23h-8c0-9-3-14-9-14s-9 5-9 14h-8z"/><path d="M63 72h36" fill="none" stroke="currentColor" stroke-width="6"/>',
        "luxury": '<path d="M45 54l16-20h40l16 20-36 54-36-54zm18 0 18 39 18-39-10-12H73L63 54z"/><path d="M45 54h72M81 93V42" fill="none" stroke="currentColor" stroke-width="5"/>',
        "finance": '<path d="M38 52l43-24 43 24v9H38v-9zm8 18h12v34H46V70zm23 0h12v34H69V70zm23 0h12v34H92V70zm-52 42h82v10H40z"/>',
        "services": '<path d="M81 27l38 14v29c0 26-16 42-38 55-22-13-38-29-38-55V41l38-14zm0 19-8 17-19 3 14 13-3 19 16-9 16 9-3-19 14-13-19-3-8-17z"/>',
    }
    initials = "".join(word[0] for word in business["name"].replace("&", " ").split()[:3]).upper()
    colour = business["colour"]
    name = html.escape(business["name"])
    short_name = html.escape(business["name"][:25])
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 112" role="img" aria-label="{name}"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="{colour}" stop-opacity=".78"/><stop offset=".6" stop-color="#15171d"/><stop offset="1" stop-color="#050608"/></linearGradient><radialGradient id="l"><stop stop-color="#fff" stop-opacity=".3"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient></defs><rect width="160" height="112" rx="14" fill="url(#g)"/><circle cx="130" cy="15" r="45" fill="url(#l)"/><g fill="#fff4d0" color="#fff4d0" transform="translate(5 0) scale(.75)">{icons[business['collection_key']]}</g><rect x="9" y="84" width="142" height="20" rx="7" fill="#050608" fill-opacity=".78"/><text x="15" y="98" fill="#fff" font-family="Arial,sans-serif" font-size="9" font-weight="700">{short_name}</text><text x="146" y="22" fill="#fff7cf" text-anchor="end" font-family="Arial,sans-serif" font-size="10" font-weight="900">{initials}</text></svg>'''
    return svg.encode("utf-8")

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
.reward-summary{display:grid;gap:14px}.reward-summary-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.reward-summary-head strong{font-size:21px;color:#fff7df}.reward-count{flex:none;padding:7px 11px;border:1px solid #c6a754;border-radius:99px;background:#c6a75418;color:#f2d583;font-weight:950}.reward-summary .loyalty-progress{margin:0}.reward-summary .progress-track{height:14px}.reward-next{margin:0;color:#fff7df;font-weight:850}.reward-rule{margin:-4px 0 0;color:var(--muted);font-size:14px}.reward-action{margin:0}.reward-action form{margin:0}.reward-action button{width:100%}.reward-ready{padding:12px 14px;border:1px solid #d6b559;border-radius:13px;background:#d6b55914;color:#fff2c8}.reward-history{margin-top:16px;padding-top:13px;border-top:1px solid #d6b55930}.reward-history>summary{cursor:pointer;color:#e6cc88;font-weight:850;list-style:none}.reward-history>summary:after{content:'+';float:right}.reward-history[open]>summary:after{content:'−'}.reward-history-list{margin-top:10px}.reward-history-row{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:9px 0;border-bottom:1px solid #ffffff0d;color:#c9c0ac;font-size:13px}.reward-history-row:last-child{border-bottom:0}.reward-history-row strong{color:#e7d095;font-size:12px;text-align:right}
.app-tabs{position:sticky;top:8px;z-index:8;display:grid;grid-template-columns:repeat(5,1fr);gap:7px;margin:0 0 16px;padding:7px;background:#260607e8;border:1px solid #ffda4e80;border-radius:16px;box-shadow:0 8px 28px #16000099;backdrop-filter:blur(10px)}.app-tab{min-width:0;padding:11px 5px;border-radius:11px;background:#5f100d;color:#fff4db;border:1px solid #ffda4e55;box-shadow:none;font-size:12px}.app-tab[aria-selected="true"]{background:linear-gradient(135deg,#fff05b,#ffbf18);color:#60100b;border-color:#fff08b}.app-tab .tab-icon{display:block;font-size:21px;line-height:1.1}.app-view{display:block}.app-ready .app-view{display:none}.app-ready .app-view.active{display:block;animation:page-in .16s ease-out}@keyframes page-in{from{opacity:.4;transform:translateY(5px)}to{opacity:1;transform:none}}.app-page{border:2px solid #ffb52b;border-radius:18px;background:#290808;overflow:hidden}.app-page.delivery{margin-top:0;padding:0}.app-page-title{margin:0;padding:16px 18px;color:var(--gold);font-size:22px;font-weight:950;border-bottom:1px solid #ffda4e33}.section-drawer{margin-top:18px;border:2px solid #ffb52b;border-radius:18px;background:#290808;overflow:hidden}.section-drawer>summary{padding:18px;cursor:pointer;list-style:none;color:var(--gold);font-size:20px;font-weight:950}.section-drawer>summary::-webkit-details-marker{display:none}.section-drawer>summary:after{content:'+';float:right;font-size:25px}.section-drawer[open]>summary:after{content:'−'}.drawer-body{padding:18px 20px 22px}.section-drawer.delivery{padding:0}.section-drawer .history{margin-top:18px}.compact-info{margin-top:16px}.compact-info summary{cursor:pointer;color:var(--gold);font-weight:900}.account-head{display:flex;align-items:end;justify-content:space-between;gap:12px;margin-bottom:12px}.account-head .name{margin:2px 0}.page-hint{margin:0;color:var(--muted)}.review-box{margin-top:10px;padding:13px;border:1px solid #ffda4e66;border-radius:14px;background:#3c0a0a}.stars{color:var(--gold);font-size:20px;letter-spacing:2px}.reviewed{color:#9dffab;font-weight:850}.rating-overlay{position:fixed;inset:0;z-index:100;display:grid;place-items:center;padding:16px;background:#130000e8;backdrop-filter:blur(8px)}.rating-popup{width:min(560px,100%);max-height:94vh;overflow:auto;padding:24px;background:linear-gradient(145deg,#8b1710,#310708);border:3px solid var(--gold);border-radius:24px;box-shadow:0 0 45px #ffbd2490;text-align:center}.rating-popup h2{font-size:30px;line-height:1.1;margin:7px 0}.rating-popup form{display:block}.rating-options{display:grid;grid-template-columns:repeat(5,1fr);gap:7px;margin:18px 0}.star-choice{position:relative;cursor:pointer}.star-choice input{position:absolute;opacity:0;pointer-events:none}.star-choice span{display:block;padding:12px 3px;border:2px solid #ffda4e70;border-radius:12px;background:#4b0a08;color:#ffe53b;font-size:18px;font-weight:950}.star-choice input:checked+span{background:#ffe53b;color:#5b0b07;border-color:#fff;transform:scale(1.06);box-shadow:0 0 16px #ffe53b99}.rating-popup input[type="text"]{margin-bottom:12px}.rating-popup .later{display:block;width:100%;margin-top:12px;padding:10px;background:transparent;color:#ffeab3;border:0;box-shadow:none;text-decoration:underline}
.raffle-hero{padding:17px;border-radius:16px;background:linear-gradient(135deg,#ffdf36,#ff8a13);color:#4f0906;box-shadow:0 8px 26px #ff9b2550}.raffle-hero .label{color:#5a0905}.raffle-hero h3{font-size:28px;margin:4px 0}.raffle-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:13px 0}.raffle-stats div{text-align:center;padding:10px 5px;border-radius:12px;background:#3b0909;border:1px solid #ffda4e55}.raffle-numbers{display:grid;grid-template-columns:repeat(10,1fr);gap:6px;margin:16px 0}.raffle-number{position:relative}.raffle-number input{position:absolute;opacity:0;pointer-events:none}.raffle-number span{display:grid;place-items:center;aspect-ratio:1;border-radius:8px;background:#68120e;border:1px solid #ffda4e77;font-size:13px;font-weight:900}.raffle-number input:checked+span{background:var(--gold);color:#5b0b07;border-color:#fff;box-shadow:0 0 12px #ffe53b99}.raffle-number.taken span{background:#250707;color:#8f6560;border-color:#52231f;text-decoration:line-through}.raffle-number.mine-pending span{background:#915512;color:white;text-decoration:none}.raffle-number.mine-confirmed span{background:#178447;color:white;text-decoration:none}.raffle-legend{display:flex;flex-wrap:wrap;gap:8px;font-size:12px}.raffle-legend span{padding:5px 8px;border:1px solid #ffda4e55;border-radius:99px}.raffle-winner{text-align:center;padding:22px;border:3px solid var(--gold);border-radius:18px;background:linear-gradient(135deg,#8a170f,#3a0808);box-shadow:0 0 30px #ffcf3160}.raffle-winner strong{display:block;font-size:28px;color:var(--gold)}
.customer-banner{padding:15px 17px;margin:0 0 14px;border:2px solid #67b8ff;border-radius:16px;background:linear-gradient(135deg,#153b6b,#0b1d39);color:#fff;box-shadow:0 6px 20px #0007}.customer-banner strong{display:block;margin-bottom:4px;color:#a9dcff;text-transform:uppercase;letter-spacing:.7px}.customer-banner.banner-promo{border-color:#ffe04b;background:linear-gradient(135deg,#a94708,#671207)}.customer-banner.banner-promo strong{color:#fff36f}.customer-banner.banner-urgent{border-color:#ff7b70;background:linear-gradient(135deg,#aa1414,#4a0505);box-shadow:0 0 24px #ff312f55}.customer-banner.banner-urgent strong{color:#fff36f}.customer-banner[hidden]{display:none}.service-banner{padding:14px 16px;margin:0 0 14px;border-radius:14px;background:#4b0a08;border:2px solid var(--gold);font-weight:900}.service-busy{background:#7b3e08}.service-closed{background:#6b0b0b;border-color:#ff7468}.eta-card{margin:12px 0;padding:14px;border-radius:14px;background:#160505;border:1px solid #ffe53b;color:#ffe53b;font-size:18px;font-weight:950}.problem-form{display:flex;flex-direction:column;gap:8px;margin-top:10px}.problem-form input,.problem-form select{padding:11px}.low-rating-reason{display:none;margin-bottom:12px}.low-rating-reason.show{display:block}
@media(max-width:560px){form,.location-row{flex-direction:column}button{width:100%}.wrap{width:min(100% - 18px,760px);padding-top:10px}.brand{margin-bottom:9px}.logo-frame{width:125px;border-radius:13px;margin-bottom:7px}.tag{padding:4px 12px;font-size:10px}.card{padding:14px;border-radius:19px}.account-head{margin-bottom:8px}.account-head .label{font-size:11px}.account-head .name{font-size:25px}.grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.stat{padding:12px}.stat .label{font-size:11px;letter-spacing:.3px}.num{font-size:22px}.wide,.vip-card{grid-column:1/-1}.vip-benefits{font-size:14px}.deal-list{grid-template-columns:1fr}.app-tabs{top:5px;margin-bottom:12px;gap:4px;padding:5px}.app-tab{padding:8px 1px;font-size:9px}.app-tab .tab-icon{font-size:17px}.app-page-title{font-size:19px;padding:13px 14px}.drawer-body{padding:13px 14px 17px}.delivery{margin-top:0}.deal-box{padding:12px}.deal-box .price{font-size:20px}input,select,textarea{padding:13px}.subtotal{font-size:20px}.notice{padding:14px}.ownership{display:none}.order-progress{gap:2px}.order-step{font-size:9px}.order-step:before{width:25px;height:25px}.reorder-row{align-items:flex-start;flex-direction:column}.reorder-row button{width:auto}.raffle-numbers{grid-template-columns:repeat(10,1fr);gap:3px}.raffle-number span{font-size:10px;border-radius:5px}.raffle-stats{font-size:12px}}
@media(max-width:560px){.rating-popup{padding:19px 14px}.rating-popup h2{font-size:25px}.star-choice span{font-size:15px;padding:11px 1px}}
.store-card{grid-column:1/-1;position:relative;isolation:isolate;overflow:hidden;width:100%;max-width:540px;justify-self:center;aspect-ratio:1.586;min-height:224px;display:flex;flex-direction:column;justify-content:space-between;gap:10px;padding:clamp(17px,4vw,28px);border:1px solid #ffe9a3;border-radius:22px;text-align:left;color:#fff7dd;background:radial-gradient(ellipse at 95% 0%,#ffce4266,transparent 58%),linear-gradient(125deg,#260909 0%,#9f101b 35%,#eb2630 60%,#61091a 100%);box-shadow:0 12px 30px #0c000088,inset 0 1px 0 #fff6,inset 0 -2px 0 #490510;transition:transform .2s,box-shadow .2s}.store-card:before{content:'';position:absolute;z-index:-1;inset:-60%;background:repeating-radial-gradient(ellipse at 85% 65%,transparent 0 22px,#ffd98a25 23px 24px,transparent 25px 43px);transform:rotate(-24deg);pointer-events:none}.store-card:after{content:'';position:absolute;z-index:-1;inset:0;background:linear-gradient(115deg,transparent 25%,#fff4 26%,#fff1 35%,transparent 52%);pointer-events:none}.store-card:hover{transform:translateY(-2px);box-shadow:0 16px 35px #090000aa,0 0 22px #ffba332a;filter:none}.store-card strong{font-size:clamp(20px,5vw,29px);line-height:1.1;text-shadow:0 2px 2px #35030a}.store-card>span{font-size:12px}.store-card .store-card-top{display:flex;justify-content:space-between;align-items:center;gap:10px;font-size:12px;letter-spacing:1.5px;color:#ffe6a4}.store-card-top b{font-size:10px;background:#24060980;padding:5px 9px;border:1px solid #ffd27566;border-radius:99px;letter-spacing:.6px}.store-card .card-hardware{display:flex;align-items:center;gap:13px}.card-chip{display:block;width:43px;height:33px;border:1px solid #b58523;border-radius:7px;background:linear-gradient(90deg,transparent 32%,#8a671c 33% 35%,transparent 36% 65%,#8a671c 66% 68%,transparent 69%),linear-gradient(0deg,transparent 31%,#8a671c 32% 35%,transparent 36% 65%,#8a671c 66% 69%,transparent 70%),linear-gradient(135deg,#f9eab1,#c9973c,#fff1b7);box-shadow:inset 0 1px 2px #fff9,0 1px 3px #240504}.card-contactless{font-size:25px;color:#ffe4a7;transform:rotate(90deg);display:block}.store-card .card-title{display:flex;flex-direction:column;gap:5px}.store-card small{font-size:10px;color:#ffe9cf}.store-card .store-card-bottom{display:flex;justify-content:space-between;align-items:end;gap:10px;font-size:12px}.card-holder{min-width:0;text-transform:uppercase;letter-spacing:1px;text-shadow:0 1px 1px #320107}.card-holder small{display:block;font-size:8px;letter-spacing:1.8px;margin-bottom:3px}.card-holder span{display:block;overflow-wrap:anywhere}.store-card-bottom b{flex-shrink:0;font-size:9px;color:#ffe0a0;letter-spacing:.5px}.store-card:focus-visible{outline:3px solid #fff;outline-offset:4px}@media(prefers-reduced-motion:reduce){.store-card{transition:none}.store-card:hover{transform:none}}.customer-shell{padding-bottom:82px}.customer-shell .brand{display:none}.customer-shell .wrap{width:min(760px,100%);padding-top:10px}.quick-hub{padding:0;overflow:hidden;border-top-width:2px;background:#260707}.quick-app-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:13px 16px;background:#1d0808;border-bottom:1px solid #ffda4e33}.quick-brand{display:flex;align-items:center;gap:10px}.quick-brand img{width:46px;height:46px;object-fit:cover;border-radius:13px;border:1px solid #ffda4e88}.quick-brand span,.quick-brand strong,.quick-brand small{display:block}.quick-brand strong{font-size:18px}.quick-brand small{color:#e2b9ab;font-size:10px;letter-spacing:1px}.quick-service{flex:none;padding:6px 10px;border-radius:99px;font-size:11px;font-weight:900}.quick-open{background:#17492b;color:#9affb2}.quick-pickup,.quick-busy{background:#70430d;color:#ffe595}.quick-closed{background:#6e1511;color:#ffb2a8}.quick-welcome{display:flex;align-items:end;justify-content:space-between;gap:12px;padding:14px 18px 8px}.quick-welcome .name{margin:1px 0;font-size:30px}.quick-welcome p{flex:none;margin:0 0 4px;padding:5px 9px;border-radius:99px;background:#4a1210;color:#ffe7a0;font-size:12px;font-weight:900}.quick-content{padding:8px 16px 24px}.quick-loyalty{padding:17px;border-radius:20px;background:linear-gradient(135deg,#ffe35a,#f29320);color:#4f0d08;box-shadow:0 9px 25px #ff9e2044}.quick-loyalty-top{display:flex;justify-content:space-between;gap:10px;font-size:11px;font-weight:900}.quick-loyalty h2{margin:7px 0 3px;font-size:24px}.quick-loyalty p{margin:0;font-size:13px}.quick-progress{height:9px;margin-top:13px;border-radius:99px;background:#7c481c55;overflow:hidden}.quick-progress span{display:block;height:100%;border-radius:99px;background:#55110b}.quick-actions{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin:12px 0}.quick-action{min-height:82px;padding:12px;border:1px solid #ffda4e33;border-radius:16px;background:#49110f;color:#fff;text-align:left;box-shadow:none}.quick-action-main{grid-column:1/-1;background:linear-gradient(135deg,#a62117,#74130e)}.quick-action span,.quick-action strong,.quick-action small{display:block}.quick-action span{font-size:21px}.quick-action strong{margin-top:2px;font-size:15px}.quick-action small{color:#e6beb0;font-size:11px}.quick-section-title{display:flex;justify-content:space-between;align-items:center;margin:17px 2px 8px}.quick-section-title span{color:var(--gold);font-size:10px;font-weight:900;letter-spacing:.7px}.quick-order{display:grid;width:100%;grid-template-columns:48px 1fr auto;align-items:center;gap:11px;padding:12px;border:1px solid #ffda4e33;border-radius:16px;background:#3d0d0d;color:#fff;text-align:left;box-shadow:none}.quick-order-icon{display:grid;place-items:center;width:48px;height:48px;border-radius:13px;background:#681812;font-size:24px}.quick-order span small,.quick-order span strong{display:block}.quick-order span small{color:#dfb4a8;font-size:10px}.quick-order span strong{margin:2px 0;font-size:14px}.quick-order b{color:var(--gold);font-size:25px}.quick-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin:11px 0}.quick-metrics div{min-width:0;padding:10px 6px;border-radius:14px;background:#40100e;text-align:center}.quick-metrics small,.quick-metrics strong{display:block}.quick-metrics small{color:#d3a296;font-size:8px}.quick-metrics strong{margin-top:3px;font-size:14px;overflow-wrap:anywhere}.quick-jackpot{padding:13px;border-radius:15px;background:#ffcf35;color:#4c0c07;font-size:12px}.quick-jackpot>strong{display:block;margin-bottom:4px}.quick-jackpot details summary{color:#4c0c07}.quick-more-page{margin-top:14px}.quick-hub>.app-tabs{position:fixed;left:50%;bottom:8px;top:auto;transform:translateX(-50%);width:min(720px,calc(100vw - 18px));margin:0;z-index:50;background:#1d0808f5;border-color:#ffda4e55;box-shadow:0 10px 35px #000c}.quick-hub .app-tab{background:transparent;border:0;box-shadow:none;color:#d5aaa0}.quick-hub .app-tab[aria-selected="true"]{background:#ffcf35;color:#53100a}.quick-hub .app-view{padding-bottom:4px}
@media(max-width:560px){.customer-shell .wrap{width:100%;padding:0}.quick-hub{min-height:100vh;border:0;border-radius:0}.quick-app-head{padding:10px 12px}.quick-brand img{width:41px;height:41px}.quick-welcome{padding:12px 13px 6px}.quick-welcome .name{font-size:25px}.quick-content{padding:7px 10px 24px}.quick-loyalty{padding:14px}.quick-loyalty h2{font-size:21px}.quick-actions{gap:7px}.quick-action{min-height:75px;padding:10px}.quick-order{grid-template-columns:43px 1fr auto;padding:10px}.quick-order-icon{width:43px;height:43px}.quick-metrics{gap:5px}.quick-metrics div{padding:8px 3px}.quick-metrics strong{font-size:12px}.quick-hub>.app-tabs{bottom:5px;width:calc(100vw - 10px);border-radius:15px}.quick-hub .app-tab{width:auto}.customer-shell footer{display:none}}
.unified-card{max-width:600px;margin:8px auto 15px;aspect-ratio:auto;min-height:275px;gap:15px}.unified-card:hover{transform:none}.member-identity{display:flex;align-items:center;gap:14px}.member-identity .card-holder{font-size:14px;font-weight:700}.card-reward{display:flex;justify-content:space-between;align-items:center;gap:10px;width:100%;padding:0;background:transparent;color:#fff6db;text-align:left;box-shadow:none}.card-reward strong{display:block;font-size:clamp(19px,4.8vw,26px)}.card-reward small{display:block;margin-top:5px}.reward-arrow{font-size:25px;color:#ffe4a3}.member-progress{height:5px;overflow:hidden;border-radius:8px;background:#ffffff26;margin-top:-5px}.member-progress>span{display:block;height:100%;background:linear-gradient(90deg,#b78b35,#fff1b5);border-radius:8px}.member-actions{display:grid;grid-template-columns:1fr 1fr;gap:9px;border-top:1px solid #f8dc9444;padding-top:13px}.member-actions button{width:100%;min-width:0;padding:12px 7px;border-radius:11px;background:linear-gradient(120deg,#d9b65f,#fff1b8,#c99c45);color:#221909;box-shadow:none;font-size:13px}.member-actions button+button{background:#180b1180;border:1px solid #ddc78a88;color:#fff1c9}.member-actions button:disabled{opacity:.55;cursor:not-allowed;filter:none}.member-actions span,.member-actions small{display:block}.member-actions small{font-size:9px;color:inherit;opacity:.85;margin-top:4px}.vip-black{background:radial-gradient(ellipse at 90% 0%,#a28b4b33,transparent 65%),linear-gradient(125deg,#08090c,#292a2f 45%,#101114 72%,#050507);border-color:#baa06a;box-shadow:0 14px 35px #0009,inset 0 1px 0 #e8d4a766,inset 0 -1px 0 #000}.vip-black:before{opacity:.35}.vip-black:after{opacity:.45}.vip-black .store-card-top{color:#e9d7aa}.vip-black .store-card-top b{background:#d1b16b1a;border-color:#cbb47888}.vip-black .card-holder,.vip-black strong{text-shadow:0 1px 2px #000}.vip-black .card-holder small{color:#bdb9ad}.vip-black .member-actions button+button{background:#121316}.vip-black:hover{box-shadow:0 14px 35px #0009,inset 0 1px 0 #e8d4a766}@media(max-width:360px){.unified-card{padding:15px;gap:12px}.member-actions button{font-size:11px}.member-identity .card-holder{font-size:12px}}
.home-pack-form{margin:0;display:block}.home-pack-form button{width:100%;padding:11px;font-size:13px}.home-pack-form button:disabled{opacity:.6;cursor:not-allowed}.membership-link{padding:4px;background:none;border:0;color:#ffe5ad;text-align:left;font-size:12px;box-shadow:none}.membership-gallery{margin:18px 0}.membership-gallery h2{font-size:21px}.membership-gallery>p{font-size:13px}.tier-cards{display:flex;gap:13px;overflow-x:auto;scroll-snap-type:x mandatory;padding:4px 3px 16px}.tier-tile{flex:0 0 min(285px,88%);scroll-snap-align:start;background:#231516;border:1px solid #806643;border-radius:17px;padding:12px}.tier-current{border:2px solid #ffdf74}.tier-tile svg{display:block;width:100%;height:auto}.tier-tile h3{font-size:17px;color:#ffe3a3;margin:12px 0 5px}.tier-tile p,.tier-tile li{font-size:12px}.tier-tile ul{padding-left:17px;margin:9px 0}.tier-tile li{margin:6px 0}
.city-run-hero{padding:18px;border-radius:18px;background:radial-gradient(circle at 85% 15%,#ffd95d66,transparent 35%),linear-gradient(135deg,#24150e,#11151d 58%,#08252b);border:1px solid #d6b459;box-shadow:0 12px 35px #0008}.city-run-hero small,.city-run-hero strong,.city-run-hero span{display:block}.city-run-hero small{color:#f1cf74;letter-spacing:1.4px;font-weight:900}.city-run-hero strong{font-size:28px;line-height:1.05;margin:7px 0}.city-run-hero span{color:#d4cab5}.city-run-score{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin:12px 0}.city-run-score div{padding:10px 5px;border-radius:13px;background:#111216;border:1px solid #6e5b30;text-align:center}.city-run-score b,.city-run-score small{display:block}.city-run-score b{font-size:20px;color:#f0ce73}.city-run-score small{font-size:9px;color:#bcb5a6}.city-reveal-form{display:block;margin:12px 0}.city-reveal-form button{width:100%;background:linear-gradient(135deg,#f5d770,#d89418);color:#191006}.city-reveal-form button:disabled{opacity:.5}.city-route{margin-top:10px;border:1px solid #5e5238;border-radius:15px;background:#101113;overflow:hidden}.city-route>summary{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:13px;cursor:pointer;list-style:none}.city-route>summary::-webkit-details-marker{display:none}.city-route>summary strong{color:#f4dfaa}.city-route>summary span{flex:none;padding:4px 8px;border-radius:99px;background:#ffffff0e;font-size:11px}.city-business-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;padding:0 10px 12px}.city-business{min-width:0;overflow:hidden;border-radius:12px;background:#191a1d;border:1px solid #3c3d42}.city-business-art{display:grid;place-items:center;min-height:72px;background:#090a0c}.city-business-art img{display:block;width:100%;height:auto;aspect-ratio:10/7;object-fit:cover}.city-business-copy{padding:8px}.city-business-copy strong,.city-business-copy small{display:block}.city-business-copy strong{font-size:11px;line-height:1.2;overflow-wrap:anywhere}.city-business-copy small{margin-top:4px;color:#a9a497;font-size:9px}.city-business.locked{filter:saturate(.15);opacity:.55}.city-business.owned{border-color:var(--set-colour);box-shadow:inset 0 0 14px color-mix(in srgb,var(--set-colour) 20%,transparent)}.city-reveal-result{text-align:center}.city-reveal-result .city-business{max-width:300px;margin:15px auto}.city-draft{padding:22px;text-align:center;border:1px solid #6d5a31;border-radius:16px;background:#111216}
.city-route-reward{padding:0 10px 12px}.city-route-reward form,.city-grand form{display:block;margin:8px 0}.city-route-reward button,.city-grand button{width:100%}.city-grand{margin-top:13px;padding:16px;border:1px solid #d3ae51;border-radius:15px;background:linear-gradient(135deg,#251b0c,#111216)}.city-grand>strong{display:block;color:#f3d477;font-size:19px}.city-grand .muted{margin-bottom:0}
/* Full GTA-style City Run board. The inner board stays wide and scrolls safely on LB/iOS. */
.city-board-viewport{width:100vw;position:relative;left:50%;transform:translateX(-50%);overflow-x:auto;padding:5px 12px 14px;scrollbar-width:thin}.city-board{width:min(1120px,calc(100vw - 24px));min-width:860px;margin:0 auto;padding:8px;border:3px solid #b8903e;border-radius:14px;background:linear-gradient(145deg,#090b10,#17120c);box-shadow:0 12px 35px #000b,inset 0 0 0 1px #f5d77655}.city-board-row{display:grid;gap:6px}.city-board-top,.city-board-bottom{grid-template-columns:repeat(10,minmax(0,1fr))}.city-board-middle{display:grid;grid-template-columns:1fr 3.15fr 1fr;gap:6px;margin:6px 0}.city-board-column{display:grid;grid-template-rows:repeat(9,minmax(0,1fr));gap:6px}.city-board-centre{min-width:0;display:grid;grid-template-columns:1.15fr .85fr;grid-template-rows:auto 1fr;gap:9px;padding:14px;border:1px solid #c09b4b;border-radius:8px;background:radial-gradient(circle at 75% 18%,#294d6755,transparent 45%),linear-gradient(145deg,#0b1927,#132535 55%,#101012);box-shadow:inset 0 0 35px #c08c2930}.city-board-tile{min-width:0;display:flex;flex-direction:column;justify-content:flex-end;overflow:hidden;min-height:100px;border:2px solid var(--set-colour);border-radius:7px;background:#111217;box-shadow:0 3px 9px #0009}.city-board-tile img{display:block;width:100%;height:68px;object-fit:cover;background:#08090b}.city-board-tile>div{min-height:32px;padding:4px 5px;background:#f5ecd3;color:#12100d}.city-board-tile strong,.city-board-tile small{display:block}.city-board-tile strong{font-size:10px;line-height:1.08;overflow-wrap:anywhere}.city-board-tile small{margin-top:2px;font-size:8px;font-weight:900;color:#665b4a;text-transform:uppercase}.city-board-tile.locked{filter:saturate(.35);opacity:.72}.city-board-tile.owned{box-shadow:0 0 10px color-mix(in srgb,var(--set-colour) 65%,transparent)}.city-board-tile.owned>div{background:linear-gradient(90deg,#fff1c4,#fff8e8)}.city-board-title{grid-row:1/-1;display:flex;flex-direction:column;justify-content:center;text-align:center}.city-board-title small{color:#f8d777;letter-spacing:2px;font-weight:900}.city-board-title strong{display:block;margin:8px 0 0;font-size:clamp(28px,5vw,64px);line-height:.8;letter-spacing:-2px;text-shadow:0 3px 0 #000,0 0 22px #f7c42b66}.city-board-title strong span{display:block;color:#f2c64f}.city-board-title strong em{display:block;color:#f2f0e9;font-style:normal;font-size:.58em;letter-spacing:1px}.city-board-title p{margin:12px auto 0;padding:5px 8px;border:1px solid #e9c867;border-radius:4px;color:#f7d77f;font-size:10px;font-weight:900;letter-spacing:.5px}.city-board-prize{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:9px;text-align:center;border:1px solid #d5ad52;border-radius:8px;background:linear-gradient(145deg,#33200f,#0f1117)}.city-board-prize span{font-size:clamp(34px,5vw,62px);filter:drop-shadow(0 4px 8px #000)}.city-board-prize b{font-size:14px;line-height:1.05;color:#f5d67b}.city-board-prize small{margin-top:6px;font-size:9px;color:#d8ccb1}.city-board-rewards{grid-column:1/-1;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px;padding-top:7px;border-top:1px solid #d5ad5244}.city-board-reward{min-width:0;padding:7px 6px;border-left:3px solid var(--set-colour);background:#080a0f99}.city-board-reward b,.city-board-reward small,.city-board-reward span{display:block}.city-board-reward b{font-size:9px;color:#f5e8c3}.city-board-reward small{font-size:9px;color:#c3b89e}.city-board-reward span{margin-top:2px;color:var(--set-colour);font-size:9px;font-weight:900;overflow-wrap:anywhere}.city-board-details{margin-top:12px}.city-board-details>summary{cursor:pointer;color:#efcf79;font-weight:900}.city-board-route-list{margin-top:8px}.city-board-route-list .city-route{margin-top:8px}.city-board-art-wrap{margin:14px auto 6px;max-width:980px;padding:8px;border:2px solid #c9a354;border-radius:16px;background:#0a0c10;box-shadow:0 12px 34px #000b}.city-board-art{display:block;width:100%;height:auto;border-radius:10px}.city-board-art-hint{margin:8px 5px 2px;color:#bdb5a6;font-size:12px;text-align:center}.city-board-page .city-board-viewport{display:none}
.city-corners{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:13px;padding:13px;border:1px solid #6d5a31;border-radius:15px;background:#111216}.city-corners h3{grid-column:1/-1;margin:0;color:#f3d477;font-size:16px}.city-corner{min-width:0;padding:9px;border-left:3px solid #5a554c;background:#0b0c0f}.city-corner.unlocked{border-left-color:#6be69a;box-shadow:inset 0 0 12px #6be69a18}.city-corner b,.city-corner small{display:block}.city-corner b{font-size:11px;color:#eee5d0}.city-corner small{margin-top:4px;color:#aaa294;font-size:10px;line-height:1.25}
@media(max-width:700px){.city-board{min-width:760px}.city-board-tile{min-height:88px}.city-board-tile img{height:56px}.city-board-centre{padding:10px}.city-board-title strong{font-size:38px}.city-board-reward b{font-size:8px}}
/* Collected stickers stay visible on the live board, but are greyed out and stamped. */
.city-board-page .city-board-viewport{display:none}.city-board-page .city-board-art-wrap{display:block}.city-board-tile.owned{position:relative;filter:grayscale(1) saturate(.15);opacity:.72}.city-board-tile.owned:after{content:'✓ COLLECTED';position:absolute;inset:0;display:grid;place-items:center;background:#08090bb8;color:#fff3b0;font-size:11px;font-weight:950;letter-spacing:.5px;text-shadow:0 1px 4px #000}.city-board-tile.owned>div{background:linear-gradient(90deg,#b9b19f,#e6dfca);color:#39352c}.city-board-tile.owned small{color:#514b40}.city-reveal-result .city-business{width:min(330px,100%);margin:16px auto;overflow:hidden;border:2px solid #d9b85d;border-radius:16px;background:#0b0c10;box-shadow:0 0 28px #d9b85d45;animation:sticker-pop .55s cubic-bezier(.2,.8,.2,1)}.city-reveal-art{display:block;width:100%;height:auto;aspect-ratio:160/112;object-fit:contain;object-position:center;background:#090a0c;image-rendering:auto}.city-reveal-badge{display:inline-block;margin:10px 0;padding:6px 12px;border:1px solid #f4cf6a;border-radius:99px;color:#ffe28b;background:#ffe28b18;font-weight:900;text-transform:uppercase;letter-spacing:1px}@keyframes sticker-pop{0%{opacity:0;transform:scale(.72) rotate(-4deg)}70%{transform:scale(1.04) rotate(1deg)}100%{opacity:1;transform:scale(1) rotate(0)}}
.city-run-hero{padding:12px}.city-run-hero strong{font-size:22px}.city-corners{padding:9px;gap:5px}.city-board-art-wrap small{display:block;text-align:center;font-size:11px;color:#bbb}.city-reveal-art{aspect-ratio:auto;max-height:280px;object-fit:contain}.city-reveal-result .city-business{max-width:230px}@media(prefers-reduced-motion:reduce){.city-reveal-result .city-business{animation:none}}
/* VIP After Dark website theme. Membership-card artwork above remains unchanged. */
body{background:radial-gradient(circle at 50% -10%,#5c441f 0,transparent 30%),radial-gradient(circle at 105% 35%,#2b1d08 0,transparent 38%),linear-gradient(145deg,#030303 0%,#0b0b0c 52%,#171109 100%);color:#fff9e8}
.card:not(.quick-hub){background:linear-gradient(145deg,#171719,#080809);border-color:#8d7439;border-top-color:#e4c36f;box-shadow:0 22px 70px #000b,0 0 30px #d9ad3b14}
.label,.app-page-title,.section-drawer>summary,.account-choice summary{color:#f0d184}
.muted,.page-hint,.sale small{color:#c8bfa9}
input,select,textarea{background:#0b0b0c;color:#fff9e8;border-color:#806a38}
input:focus,select:focus,textarea:focus{outline-color:#e8c86f}
.account-choice details,.account-choice details[open]{background:#101012;border-color:#66552f}
.account-choice details[open]{border-color:#d2ad54}
.notice{background:#d2ad5412;border-color:#806a38;color:#f3ead3}
.delivery,.app-page,.section-drawer{background:linear-gradient(150deg,#171719,#09090a);border-color:#8a7038;box-shadow:0 15px 38px #0007}
.deal-box,.review-box{background:#111113;border-color:#5e4d2e}
.order-status{background:linear-gradient(135deg,#201b12,#0d0d0e);border-color:#b3944c}
.reorder-row,.sale{border-bottom-color:#d9bc7130}
.raffle-hero{background:linear-gradient(135deg,#d6b45d,#82651f);color:#0d0d0d;box-shadow:0 10px 28px #b8933645}
.raffle-hero .label{color:#18140b}
.raffle-stats div{background:#101012;border-color:#756033}
.raffle-number span{background:#171719;border-color:#756033}
.raffle-number.taken span{background:#09090a;color:#6f6b62;border-color:#28251e}
.raffle-winner{background:linear-gradient(145deg,#211a0e,#09090a);border-color:#d9b85d}
.customer-banner,.customer-banner.banner-promo,.customer-banner.banner-urgent{background:linear-gradient(135deg,#1d1a13,#0a0a0b);border-color:#9e8243;box-shadow:0 8px 24px #0008}
.customer-banner strong,.customer-banner.banner-promo strong,.customer-banner.banner-urgent strong{color:#eed180}
.service-banner{background:#15130e;border-color:#a88b49}
.rating-overlay{background:#020202e8}.rating-popup{background:linear-gradient(145deg,#211a0e,#080809);border-color:#d5b45d;box-shadow:0 0 48px #d6aa3b55}
.customer-shell .quick-hub{background:linear-gradient(180deg,#101011,#050506);border-color:#8f753b;box-shadow:0 22px 75px #000d,0 0 34px #d6ac4018}
.customer-shell .quick-app-head{background:linear-gradient(90deg,#050506,#15130e);border-bottom-color:#a3894a55}
.customer-shell .quick-brand img{border-color:#d5b45d;box-shadow:0 0 16px #d5b45d33}
.customer-shell .quick-brand small{color:#cdbd91;letter-spacing:1.6px}
.customer-shell .quick-welcome p{background:#19160f;color:#efd282;border:1px solid #6c592f}
.customer-shell .quick-content{background:linear-gradient(180deg,#0a0a0b,#050506)}
.customer-shell .quick-action{background:linear-gradient(145deg,#1a1a1c,#0d0d0e);border-color:#725e32;color:#fff8e4}
.customer-shell .quick-action-main{background:linear-gradient(135deg,#2a2415,#0e0e0f);border-color:#c4a352;box-shadow:inset 0 1px 0 #f2d98922,0 10px 24px #0008}
.customer-shell .quick-action small{color:#c4baa2}
.customer-shell .quick-order{background:linear-gradient(145deg,#18181a,#0b0b0c);border-color:#66542f;color:#fff9e9}
.customer-shell .quick-order-icon{background:#252015;border:1px solid #68572f}
.customer-shell .quick-order span small{color:#bfb49d}
.customer-shell .quick-metrics div{background:linear-gradient(145deg,#171719,#0b0b0c);border:1px solid #4e432b}
.customer-shell .quick-metrics small{color:#aa9d80}
.customer-shell .quick-jackpot{background:linear-gradient(135deg,#d8b75f,#8d6d25);color:#0c0c0d;box-shadow:0 8px 24px #b8923440}
.customer-shell .quick-jackpot details summary{color:#0c0c0d}
.customer-shell .quick-hub>.app-tabs{background:#070708f5;border-color:#8c743d;box-shadow:0 10px 38px #000e,0 0 18px #d0a63b18}
.customer-shell .quick-hub .app-tab{color:#bcb39f}
.customer-shell .quick-hub .app-tab[aria-selected="true"]{background:linear-gradient(135deg,#f3d780,#b8892d);color:#111;box-shadow:0 3px 12px #d8b05040}
.customer-shell .quick-more-page{background:linear-gradient(145deg,#161618,#080809)}
.customer-shell .vip-card{background:linear-gradient(135deg,#171719,#0a0a0b);border-color:#9e8244;box-shadow:inset 0 0 22px #d0aa4d18}
.customer-shell .tier-tile{background:#111113;border-color:#665638}
.customer-shell .tier-current{border-color:#e6c66d;box-shadow:0 0 20px #d9b65222}
.customer-shell footer{color:#9f947b}
@media(max-width:560px){body{background:#050506}.customer-shell .quick-hub{box-shadow:none}.customer-shell .quick-content{padding-bottom:30px}}
.action-centre{margin:13px 0;padding:14px;border:1px solid #6f5b31;border-radius:16px;background:linear-gradient(145deg,#171719,#0b0b0c)}.action-centre h3{margin:0 0 5px;color:#efd180}.action-centre form{display:grid;gap:11px;margin-top:10px}.request-kind{padding:12px 14px;border:1px solid #8d7439;border-radius:13px;background:#0d0d0f;color:#fff8e5;font-weight:850}.request-choices{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:0;padding:0;border:0}.request-choices label{display:block}.request-choices input{position:absolute;opacity:0;pointer-events:none}.request-choices span{display:grid;min-height:52px;place-items:center;padding:10px;border:1px solid #715d32;border-radius:13px;background:#111113;color:#e5dcc7;text-align:center;font-weight:800}.request-choices input:checked+span{border-color:#f3d66f;background:linear-gradient(135deg,#e8c65f,#a97b23);color:#111;box-shadow:0 0 16px #d5b45d33}.action-centre .request-message{width:100%;min-height:58px}.action-centre button[disabled]{opacity:.72;cursor:wait}.action-status{display:flex;justify-content:space-between;gap:10px;padding:9px 0;border-top:1px solid #63522d55;font-size:13px}.action-status strong{color:#efd180}.reward-shop{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin:14px 0}.reward-choice{padding:13px;border:1px solid #62522f;border-radius:14px;background:#111113}.reward-choice strong,.reward-choice small{display:block}.reward-choice small{color:#c8bfa9}.reward-choice form{display:block;margin-top:9px}.reward-choice button{width:100%;padding:10px}.voucher{padding:12px;margin:8px 0;border:1px dashed #d5b45d;border-radius:13px;background:#d5b45d12}.voucher code{color:#f6d77d;font-weight:900}.voucher-used{opacity:.58}.inbox-count{display:inline-grid;place-items:center;min-width:24px;height:24px;padding:0 7px;border-radius:99px;background:#e5c365;color:#111;font-size:12px;font-weight:950}@media(max-width:460px){.reward-shop,.request-choices{grid-template-columns:1fr}.action-centre form{flex-direction:column}}
#service-toast{position:fixed;left:50%;top:18px;transform:translate(-50%,-160%);z-index:110;width:min(560px,92vw);padding:17px;border:2px solid #e2c269;border-radius:16px;background:#0b0b0c;color:#fff8df;text-align:center;font-weight:900;box-shadow:0 15px 45px #000;transition:transform .25s}#service-toast.show{transform:translate(-50%,0)}

/* Neon Street Food — complete customer website redesign. */
:root{--gold:#ffc52f;--cream:#fff8e7;--muted:#b7b8c5;--neon-red:#ff2638;--neon-orange:#ff8a19;--neon-cyan:#35ddff;--panel:#111318;--panel-2:#181b22;--line:#353946}
html{background:#050608;scroll-behavior:smooth}
body{background:radial-gradient(circle at 12% -5%,#451018 0,transparent 28%),radial-gradient(circle at 100% 22%,#062b39 0,transparent 26%),linear-gradient(180deg,#06070a 0%,#0b0c11 48%,#050608 100%);color:#f8f8fb;font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Arial,sans-serif;letter-spacing:.01em}
body:before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.22;background-image:linear-gradient(#ffffff05 1px,transparent 1px),linear-gradient(90deg,#ffffff04 1px,transparent 1px);background-size:32px 32px;mask-image:linear-gradient(to bottom,#000,transparent 70%)}
.wrap{position:relative;width:min(800px,94vw);padding:22px 0 42px}
.brand{margin:0 auto 18px}.logo-frame{width:min(210px,48vw);border:1px solid #ff344d;border-radius:24px;background:#090a0d;box-shadow:0 0 0 5px #ff263812,0 0 38px #ff26384d,0 20px 45px #000b}.tag{margin-top:5px;border:1px solid #ff8a1977;background:#0d0e12;color:#ffc45b;box-shadow:0 0 16px #ff8a192f}
h1,h2,h3,.name{letter-spacing:-.025em}.label{color:#ffbd37;letter-spacing:.14em}.muted,.page-hint,.sale small{color:var(--muted)}
.card:not(.quick-hub){border:1px solid #ff344d;border-top:4px solid var(--neon-red);background:linear-gradient(145deg,#171920e8,#090a0edb);box-shadow:0 0 0 1px #ff263818,0 0 36px #ff263829,0 24px 70px #000c;backdrop-filter:blur(16px)}
form{gap:12px}input,select,textarea{border:1px solid #484d5a;background:#0b0d12;color:#fff;border-radius:13px;box-shadow:inset 0 1px 0 #ffffff08}input:hover,select:hover,textarea:hover{border-color:#737a8b}input:focus,select:focus,textarea:focus{border-color:var(--neon-cyan);outline:2px solid #35ddff38;box-shadow:0 0 18px #35ddff28}input::placeholder,textarea::placeholder{color:#777d8c}
button{border:1px solid #ffb02e;background:linear-gradient(135deg,#ff2638,#ff8419);color:#fff;border-radius:13px;box-shadow:0 0 17px #ff26383b,0 4px 0 #7f151b;text-transform:none;transition:transform .16s ease,filter .16s ease,box-shadow .16s ease}button:hover{transform:translateY(-1px);filter:brightness(1.12);box-shadow:0 0 24px #ff26385c,0 5px 0 #7f151b}button:active{transform:translateY(2px);box-shadow:0 1px 0 #7f151b}.secondary{border-color:#3cdaff;background:#11151b;color:#dffaff;box-shadow:0 0 14px #35ddff22}
.account-choice{gap:10px}.account-choice details,.account-choice details[open]{border:1px solid #343844;background:#0f1116}.account-choice details[open]{border-color:#ff354d;box-shadow:0 0 18px #ff263824}.account-choice summary{color:#fff;padding:16px 18px}.account-choice details[open] summary{color:#ffbe43}.choice-body{border-top:1px solid #2a2e38;padding-top:16px}
.notice{border:1px solid #6b5a2c;background:linear-gradient(135deg,#221b0e,#111217);color:#fff3cd;box-shadow:inset 3px 0 #ffb820}.debt-warning{border:1px solid #ff4b58;background:linear-gradient(135deg,#401016,#170b0e);box-shadow:0 0 22px #ff263832}.delivery,.app-page,.section-drawer{border:1px solid #333844;background:linear-gradient(150deg,#171920,#0c0e13);box-shadow:0 16px 40px #0008}.app-page-title,.section-drawer>summary{color:#fff;border-color:#2d3039}.app-page-title{position:relative;padding-left:21px}.app-page-title:before{content:"";position:absolute;left:0;top:18%;width:4px;height:64%;border-radius:5px;background:linear-gradient(var(--neon-red),var(--neon-orange));box-shadow:0 0 12px var(--neon-red)}
.deal-list{gap:12px}.deal-box,.review-box{border:1px solid #353a46;background:linear-gradient(145deg,#181b22,#101218)}.deal-box:hover{border-color:#ff344d;box-shadow:0 0 18px #ff263823}.deal-box .price,.subtotal{color:#ffbc35}.quantity input{border-color:#ff7032;background:#080a0d}.history{border-color:#343844}.sale,.reorder-row{border-color:#2c303a}.order-status{border-color:#35dfff;background:linear-gradient(135deg,#10212a,#0d1015);box-shadow:0 0 22px #35ddff24}.status-paid{color:#59f69a}.status-pending{color:#ffd052}.status-cancelled,.status-wasted_journey{color:#ff7480}
.customer-banner,.customer-banner.banner-promo,.customer-banner.banner-urgent{border:1px solid #35ddff;background:linear-gradient(135deg,#102733,#0d1117);box-shadow:0 0 22px #35ddff24}.customer-banner strong{color:#65e9ff}.customer-banner.banner-promo{border-color:#ffb52d;background:linear-gradient(135deg,#35230d,#121215)}.customer-banner.banner-promo strong{color:#ffd267}.customer-banner.banner-urgent{border-color:#ff3a4d;background:linear-gradient(135deg,#401017,#140d11)}.customer-banner.banner-urgent strong{color:#ff8a94}
.service-banner{border:1px solid #ff8a19;background:#21170d}.eta-card{border-color:#35ddff;background:#0b1116;color:#7beaff;box-shadow:0 0 15px #35ddff20}
.reward-summary-head strong{color:#fff}.reward-count{border-color:#ff7830;background:#ff263817;color:#ffc34e}.progress-track{border-color:#ff583f;background:#08090d}.progress-fill{background:linear-gradient(90deg,#ff2439,#ff8b18,#ffe04a);box-shadow:0 0 14px #ff453f}.reward-rule{color:#b7b8c5}.reward-ready{border-color:#38daf7;background:#35ddff12;color:#dffaff}.reward-choice{border-color:#343945;background:#11141a}.reward-choice:hover{border-color:#ff3f50}.reward-choice small{color:#b9bbc4}.voucher{border-color:#ffb72e;background:#ff9e0f0d}.voucher code{color:#ffd15a}
.raffle-hero{border:1px solid #ffbd32;background:radial-gradient(circle at 90% 0%,#ffdd5260,transparent 36%),linear-gradient(135deg,#5b1118,#1b0d12);color:#fff;box-shadow:0 0 26px #ff263833}.raffle-hero .label{color:#ffd04e}.raffle-stats div{border-color:#34434b;background:#10151a}.raffle-number span{border-color:#3b4651;background:#12161c}.raffle-number input:checked+span{border-color:#ffdc52;background:linear-gradient(135deg,#ff2638,#ff8a19);color:#fff;box-shadow:0 0 15px #ff26386b}.raffle-number.mine-confirmed span{border-color:#4cf096;background:#126139;box-shadow:0 0 12px #32e5844a}.raffle-number.mine-pending span{border-color:#ffc13c;background:#755214}.raffle-number.taken span{border-color:#252a31;background:#090b0e;color:#5d626d}.raffle-winner{border-color:#ffd14d;background:radial-gradient(circle,#65111a,#100b0f);box-shadow:0 0 36px #ff26384a}.raffle-winner strong{color:#ffd451}
.rating-overlay{background:#030407e8}.rating-popup{border:1px solid #ff354d;background:linear-gradient(145deg,#1a1c23,#090b0f);box-shadow:0 0 60px #ff26384d}.star-choice span{border-color:#3d424d;background:#101218;color:#ffd040}.star-choice input:checked+span{border-color:#ff8a19;background:linear-gradient(135deg,#ff2638,#ff8a19);color:#fff;box-shadow:0 0 18px #ff26386b}
.action-centre{border-color:#343945;background:linear-gradient(145deg,#171a20,#0d0f14)}.action-centre h3,.action-status strong{color:#ffca4e}.request-kind,.request-choices span{border-color:#353a45;background:#0e1116}.request-choices input:checked+span{border-color:#35ddff;background:#102b34;color:#dffaff;box-shadow:0 0 17px #35ddff2e}

/* Signed-in customer app. */
.customer-shell{padding-bottom:90px;background:radial-gradient(circle at 50% -5%,#371017 0,transparent 25%),radial-gradient(circle at 100% 28%,#092936 0,transparent 27%),#050608}.customer-shell .quick-hub{border:1px solid #282c35;background:#080a0e;box-shadow:0 24px 80px #000d;overflow:hidden}.customer-shell .quick-app-head{position:relative;z-index:4;border-bottom:1px solid #262a33;background:#090b0ff2;backdrop-filter:blur(18px)}.customer-shell .quick-brand img{border:1px solid #ff3a4d;box-shadow:0 0 18px #ff26384c}.customer-shell .quick-brand strong{font-weight:950;letter-spacing:.02em}.customer-shell .quick-brand small{color:#ff9c54;letter-spacing:.15em}.quick-service{border:1px solid currentColor;background:#0d1015}.quick-open{color:#5cf49b}.quick-pickup,.quick-busy{color:#ffc84b}.quick-closed{color:#ff6c78}
.customer-shell .quick-welcome{position:relative;isolation:isolate;min-height:168px;padding:74px 22px 20px;overflow:hidden;border-bottom:1px solid #37272b;background:linear-gradient(90deg,#090a0ed9 0%,#090a0e99 45%,#090a0e20),url('/snr-logo.png') center 62%/cover no-repeat}.customer-shell .quick-welcome:before{content:"";position:absolute;z-index:-1;inset:0;background:linear-gradient(180deg,#0001,#080a0ecc),linear-gradient(90deg,#ff26382e,transparent 55%)}.customer-shell .quick-welcome .label{color:#ffb537;text-shadow:0 1px 6px #000}.customer-shell .quick-welcome .name{margin:0;color:#fff;font-size:clamp(27px,6vw,42px);text-shadow:0 3px 12px #000}.customer-shell .quick-welcome p{border:1px solid #ffb62f;background:#101216d9;color:#ffd766;box-shadow:0 0 17px #ff8a1930}
.customer-shell .quick-content{padding:16px 18px 28px;background:linear-gradient(180deg,#090a0f,#050608)}
.customer-shell .store-card{border:1px solid #ff7040;background:radial-gradient(circle at 87% 15%,#ffb42d46 0,transparent 32%),radial-gradient(circle at 18% 90%,#ff26384a 0,transparent 42%),linear-gradient(128deg,#1b090d 0%,#5d1119 48%,#130b10 100%);box-shadow:0 0 0 1px #ff263823,0 0 28px #ff26383c,0 18px 42px #000b,inset 0 1px #ffffff35}.customer-shell .store-card:before{background:repeating-linear-gradient(125deg,transparent 0 27px,#ffbd3212 28px 29px,transparent 30px 54px);opacity:1}.customer-shell .store-card:after{background:linear-gradient(110deg,transparent 24%,#ffffff24 26%,#ffffff08 36%,transparent 50%)}
.customer-shell .unified-card{max-width:600px;min-height:0;aspect-ratio:1.586;margin:0 auto 14px;padding:clamp(17px,4vw,27px);gap:8px}.customer-shell .vip-black{border-color:#d0ae5d;background:radial-gradient(circle at 88% 10%,#cf9f3545,transparent 32%),radial-gradient(circle at 15% 90%,#ff26382e,transparent 43%),linear-gradient(128deg,#050609,#1b1d22 48%,#08090c);box-shadow:0 0 0 1px #d4b45b25,0 0 30px #d5a83c35,0 18px 42px #000c,inset 0 1px #ffffff25}.customer-shell .store-card-top{color:#ffd35d}.customer-shell .store-card-top b{border-color:#ff7d3f;background:#ff263818;color:#fff0c3}.customer-shell .card-chip{width:46px;height:35px;box-shadow:0 0 10px #ffc65042,inset 0 1px 2px #fff9}.card-balance{display:flex;align-items:end;gap:10px;margin:auto 0 2px}.card-balance strong{font-size:clamp(43px,10vw,68px);line-height:.85;color:#fff}.card-balance span{padding-bottom:3px;color:#ffc854;font-size:12px;font-weight:900;letter-spacing:.13em}.customer-shell .store-card-bottom{align-items:end}.customer-shell .card-holder span{font-weight:850}.customer-shell .card-reward{width:auto;max-width:58%;padding:7px 10px;border:1px solid #ffffff28;border-radius:9px;background:#06080b66;box-shadow:none}.customer-shell .card-reward:hover{transform:none;border-color:#ffbd3d;box-shadow:0 0 12px #ff8a192e}.customer-shell .card-reward strong{font-size:11px;letter-spacing:.02em}.customer-shell .card-reward small{display:none}.customer-shell .reward-arrow{font-size:16px}.customer-shell .member-progress{height:4px;margin:0;background:#ffffff20}.customer-shell .member-progress>span{background:linear-gradient(90deg,#ff2638,#ff8a19,#ffe04a);box-shadow:0 0 10px #ff4b38}
.card-tools{display:grid;grid-template-columns:1fr auto;gap:9px;margin-bottom:12px}.card-tools .home-pack-form{margin:0}.card-tools .home-pack-form button{height:100%;border-color:#ffb830}.card-tools .membership-link{min-width:150px;padding:10px 12px;border:1px solid #36ddff;background:#0e161c;color:#c9f8ff;text-align:center;box-shadow:0 0 15px #35ddff20}.customer-shell .member-actions{margin:0 0 13px;padding:0;border:0;gap:10px}.customer-shell .member-actions button{min-height:68px;border:1px solid #ff354d;background:linear-gradient(145deg,#281017,#121318);color:#fff;box-shadow:0 0 18px #ff263823;text-align:left;padding:13px 15px}.customer-shell .member-actions button+button{border-color:#35ddff;background:linear-gradient(145deg,#0d2028,#111318);color:#e1faff;box-shadow:0 0 18px #35ddff20}.customer-shell .member-actions button span{font-size:15px;font-weight:950}.customer-shell .member-actions button small{font-size:10px;color:#b8bbc5}
.customer-shell .quick-action{border:1px solid #ff9f28;background:radial-gradient(circle at 90% 10%,#ffbd3130,transparent 35%),linear-gradient(135deg,#3b1016,#151319);color:#fff;box-shadow:0 0 20px #ff263825}.customer-shell .quick-action-main{background:linear-gradient(135deg,#5c1019,#191318)}.customer-shell .quick-action span{filter:drop-shadow(0 0 6px #ff8a19)}.customer-shell .quick-action small{color:#c5c7cf}
.customer-shell .quick-section-title strong{font-size:16px}.customer-shell .quick-section-title span{color:#59e5ff}.customer-shell .quick-order{border:1px solid #35404a;background:linear-gradient(145deg,#15191f,#0c0f14);color:#fff;box-shadow:0 10px 24px #0007}.customer-shell .quick-order:hover{border-color:#35ddff;transform:none}.customer-shell .quick-order-icon{border:1px solid #ff384b;background:#351018;box-shadow:0 0 13px #ff263830}.customer-shell .quick-order span small{color:#aeb2be}.customer-shell .quick-order b{color:#4be4ff}.customer-shell .quick-metrics div{border:1px solid #303540;background:linear-gradient(145deg,#15181e,#0d0f14)}.customer-shell .quick-metrics div:nth-child(2){border-color:#67491f}.customer-shell .quick-metrics small{color:#9298a6}.customer-shell .quick-jackpot{border:1px solid #ffb931;background:linear-gradient(135deg,#3c2710,#161216);color:#fff1c4;box-shadow:0 0 20px #ff8a1925}.customer-shell .quick-jackpot details summary{color:#ffd15d}
.customer-shell .app-page,.customer-shell .section-drawer,.customer-shell .quick-more-page{border-color:#343945;background:linear-gradient(150deg,#15181e,#0b0d11)}.customer-shell .app-page-title{background:#0e1116}.customer-shell .drawer-body{background:linear-gradient(180deg,#11141a,#0a0c10)}
.customer-shell .quick-hub>.app-tabs{border:1px solid #323640;background:#090b0ff2;box-shadow:0 -8px 35px #000d,0 0 20px #ff263816;backdrop-filter:blur(20px)}.customer-shell .quick-hub .app-tab{color:#9095a2}.customer-shell .quick-hub .app-tab:hover{transform:none;box-shadow:none}.customer-shell .quick-hub .app-tab[aria-selected="true"]{border:1px solid #ff344b;background:linear-gradient(135deg,#501019,#231016);color:#fff;box-shadow:0 0 16px #ff263842}.customer-shell .quick-hub .app-tab[aria-selected="true"] .tab-icon{filter:drop-shadow(0 0 6px #ff364d)}
.customer-shell .tier-tile{border-color:#373b45;background:#101319}.customer-shell .tier-current{border-color:#ffbf38;box-shadow:0 0 21px #ff8a1930}.customer-shell .membership-gallery h2{color:#fff}.customer-shell footer{color:#6f7480}
#service-toast{border-color:#35ddff;background:#0a1015;color:#dffaff;box-shadow:0 0 30px #35ddff45,0 15px 45px #000}
@media(max-width:560px){body{background:#050608}.wrap{width:min(100% - 14px,800px)}.customer-shell .wrap{width:100%}.customer-shell .quick-hub{border:0}.customer-shell .quick-app-head{position:sticky;top:0;padding:9px 11px}.customer-shell .quick-welcome{min-height:142px;padding:57px 13px 15px;background-position:center 64%}.customer-shell .quick-welcome .name{font-size:25px}.customer-shell .quick-content{padding:11px 10px 25px}.customer-shell .unified-card{border-radius:19px;padding:15px;aspect-ratio:1.586}.card-balance strong{font-size:43px}.card-balance span{font-size:9px}.customer-shell .card-reward{max-width:54%;padding:6px 8px}.customer-shell .card-reward strong{font-size:9px}.card-tools{grid-template-columns:1fr}.card-tools .membership-link{width:100%}.customer-shell .member-actions{gap:7px}.customer-shell .member-actions button{min-height:61px;padding:10px}.customer-shell .quick-hub>.app-tabs{bottom:4px;width:calc(100vw - 8px)}.customer-shell .quick-hub .app-tab{padding:7px 1px;font-size:9px}.deal-list{grid-template-columns:1fr}.rating-popup{border-radius:20px}}
@media(max-width:360px){.customer-shell .unified-card{padding:13px}.customer-shell .store-card-top{font-size:10px}.customer-shell .store-card-top b{font-size:8px}.customer-shell .card-chip{width:39px;height:29px}.card-balance strong{font-size:36px}.customer-shell .card-holder{font-size:10px}.customer-shell .card-reward{max-width:50%}.customer-shell .member-actions button span{font-size:13px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}button{transition:none}button:hover{transform:none}}
/* Preview 2: original artwork, composition and functional live content. */
.customer-shell .quick-hub>.app-tabs{grid-template-columns:repeat(4,1fr)}
.brand .logo-frame{position:relative}.brand .logo-frame:after{content:'ORDER · EARN · ENJOY';position:absolute;left:9%;top:65%;width:28%;height:17%;display:grid;place-items:center;background:#050b08;border:1px solid #35ffc0;border-radius:6px;color:#35ffc0;font-size:clamp(7px,1.7vw,14px);font-weight:900;box-shadow:0 0 10px #28ffaa88}
body{background:#030305;color:#f6f3e9}.customer-shell .wrap{width:min(941px,100%);padding-top:0}.customer-shell .quick-hub{border:0;border-radius:0;background:#030305;box-shadow:none}.customer-shell .quick-app-head{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
.neon-hero{position:relative;aspect-ratio:941/370;background:url('/neon-design.png') center top/100% auto no-repeat;overflow:hidden}.hero-service{position:absolute;left:10%;top:66%;width:26%;height:15%;display:flex;align-items:center;justify-content:center;text-align:center;background:#00100a;color:#39ffbb;border:2px solid currentColor;border-radius:7px;box-shadow:0 0 8px currentColor,inset 0 0 6px #13ff9444;font-size:clamp(9px,2.1vw,20px);font-weight:950;letter-spacing:.12em;text-transform:uppercase}.hero-service.quick-closed{color:#ff624f;background:#180405}.hero-service.quick-busy,.hero-service.quick-pickup{color:#ffd353;background:#160f02}
.customer-shell .quick-welcome{min-height:0;padding:12px 4%;background:#030305;border:0;align-items:center;gap:8px}.customer-shell .quick-welcome:before{display:none}.customer-shell .quick-welcome .name{font-family:Impact,'Arial Narrow',sans-serif;font-size:clamp(19px,4.1vw,39px);line-height:1.1;text-shadow:none}.quick-welcome em{font-style:normal;color:#ff3434}.customer-shell .quick-welcome .membership-link{padding:0;flex:none;background:transparent;color:#ffc62f;border:0;box-shadow:none;font-size:clamp(10px,2vw,20px)}
.customer-shell .quick-content{padding:2px 4% 24px}.customer-shell .unified-card{max-width:600px;margin:8px auto 20px;border:1px solid #d4b060;border-radius:22px;box-shadow:0 0 1px 1px #ffe6a3,0 0 15px #ff1b3255,0 14px 35px #000;aspect-ratio:1.586}.customer-shell .card-balance strong{font-size:clamp(42px,9vw,68px)}
.customer-shell .member-actions{display:grid;grid-template-columns:1fr 1fr;gap:2.4%;margin:12px 0 18px}.customer-shell .member-actions button{position:relative;aspect-ratio:422/240;min-height:0;padding:0;border:1px solid #ff675e;border-radius:15px;background-image:url('/neon-design.png');background-size:223% auto;background-position:7.32% 49.02%;box-shadow:0 0 3px #fff,0 0 13px #ff263866;overflow:hidden}.customer-shell .member-actions button:nth-child(2){background-position:92.5% 49.02%;border-color:#48dfff;box-shadow:0 0 3px #fff,0 0 13px #17cfff77}.customer-shell .member-actions button>span,.customer-shell .member-actions button>small{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}.customer-shell .member-actions button:disabled{opacity:.65;filter:saturate(.5)}.customer-shell .member-actions button:disabled:after{content:'DELIVERY UNAVAILABLE';position:absolute;bottom:0;inset-inline:0;padding:7px;background:#040709ed;color:white;font-size:10px;letter-spacing:.08em}
.neon-reward-banner{display:flex;align-items:center;gap:3%;width:100%;margin:0 0 10px;padding:16px 4%;border:2px solid #ffe475;border-radius:16px;background:radial-gradient(ellipse at 20% 30%,#b2691533,transparent 60%),linear-gradient(120deg,#211305,#0b0805 65%,#3a1d06);color:#ffe775;text-align:left;box-shadow:0 0 3px #fff,0 0 17px #ffad3077,inset 0 0 20px #ff9d1722}.neon-reward-banner>span:first-child{font-size:clamp(30px,6vw,62px);color:#ffd737;text-shadow:0 0 12px #ffa611}.neon-reward-banner>strong{font-size:clamp(32px,7vw,70px);line-height:1;color:#ffdb50}.neon-reward-banner>strong small{font:700 clamp(7px,1vw,11px) Arial;display:block;margin-top:6px}.neon-reward-banner>span:nth-of-type(2){font-family:Impact,'Arial Narrow',sans-serif;font-size:clamp(15px,3vw,29px);line-height:1.1;color:#fff9db}.neon-reward-banner small{display:block;font:400 clamp(9px,1.4vw,14px) Arial;margin-top:7px;color:#dbcdb5}.neon-reward-banner>b{margin-left:auto;font-size:32px}
.customer-shell .card-tools{display:block;margin:0 0 13px;padding:6px 0;border:0;background:transparent;color:#c6b78e;font-size:12px}.card-tools summary{cursor:pointer}.card-tools .home-pack-form{margin-top:12px}.customer-shell .quick-actions{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:12px 0}.customer-shell .quick-action{display:flex;align-items:center;justify-content:center;gap:10px;min-height:88px;padding:12px 7px;border:1px solid #776665;border-radius:14px;background:linear-gradient(135deg,#171314,#030405);color:#fff;box-shadow:inset 0 0 12px #ffffff04}.customer-shell .quick-action span{font-size:32px;color:#ff343c;text-shadow:0 0 12px currentColor}.customer-shell .quick-action:nth-child(2) span{color:#ffb129}.customer-shell .quick-action:nth-child(3) span{color:#46dfff}.customer-shell .quick-action strong{font:400 clamp(11px,2vw,22px) Impact,'Arial Narrow',sans-serif;text-transform:uppercase;margin:0}.customer-shell .quick-section-title{color:#16f5ab}.customer-shell .quick-section-title span{color:#aaa}.customer-shell .quick-order{min-height:110px;background:linear-gradient(135deg,#141414,#060708);border:1px solid #6c6262;border-radius:15px}.customer-shell .quick-order-icon{background:#160705;border:1px solid #f9473c;box-shadow:0 0 9px #ff343c33}.customer-shell .quick-order span strong{font-size:18px}.customer-shell .quick-order b{color:#ff4444}
.customer-shell .quick-hub>.app-tabs{border:0;border-top:1px solid #554342;border-radius:0;background:#030405f5;bottom:0;width:min(941px,100%);padding-bottom:max(7px,env(safe-area-inset-bottom));box-shadow:0 -3px 22px #000}.customer-shell .quick-hub .app-tab{border-radius:0;background:transparent;color:#bbb}.customer-shell .quick-hub .app-tab[aria-selected="true"]{color:#ff4541;background:transparent;border-top:2px solid #ff3939;box-shadow:0 -5px 10px -7px #ff3939}.customer-shell .app-page,.customer-shell .section-drawer,.customer-shell .action-centre{background:#09090c;border:1px solid #574447;border-radius:16px}.customer-shell .app-page-title,.customer-shell .section-drawer>summary{background:linear-gradient(90deg,#251011,#0a090c);color:#fff}.customer-shell .deal{background:linear-gradient(135deg,#1e1011,#08090c);border:1px solid #743334}.customer-shell .deal:focus-within{border-color:#ff473b;box-shadow:0 0 12px #ff473b33}.customer-shell .order-tracker{border-color:#28bace;background:#071115}
.brand .logo-frame{width:100%;aspect-ratio:941/370;border:0;border-radius:14px;background:url('/neon-design.png') center top/100% auto}.brand .logo-frame img{visibility:hidden}.brand .tag{margin-top:8px}.customer-shell .quick-metrics,.customer-shell .quick-jackpot{margin-top:18px}
@media(max-width:560px){.customer-shell .quick-welcome{padding:12px 4%}.customer-shell .quick-content{padding:0 4% 24px}.customer-shell .unified-card{padding:17px;margin-bottom:14px}.customer-shell .quick-action{min-height:63px;gap:5px;border-radius:10px}.customer-shell .quick-action span{font-size:24px}.customer-shell .quick-actions{gap:7px}.neon-reward-banner{padding:12px 3%;border-radius:12px}.customer-shell .member-actions button{border-radius:11px}.customer-shell .quick-order{min-height:90px}.customer-shell .quick-order span strong{font-size:14px}.customer-shell .quick-hub>.app-tabs{width:100%;bottom:0}.hero-service{border-width:1px}.customer-shell .card-tools{font-size:11px}}
/* Compact layout and explicit delivery artwork override. */
.customer-shell .member-actions button+button{background-image:url('/neon-design.png');background-size:223% auto;background-position:92.5% 49.02%;background-repeat:no-repeat}
.customer-shell .quick-hub{overflow:visible}.customer-shell .neon-hero{width:min(640px,100%);margin:auto}.customer-shell .quick-welcome{padding:10px 4%}.customer-shell .quick-welcome .name{font-size:clamp(18px,3vw,28px)}
.customer-shell .unified-card{max-width:440px;padding:18px;gap:6px;margin:8px auto 12px}.customer-shell .card-balance strong{font-size:44px}.customer-shell .card-chip{width:38px;height:29px}.customer-shell .card-contactless{font-size:22px}.customer-shell .card-balance span{font-size:10px}
.customer-shell .member-actions{max-width:660px;margin:10px auto 12px;gap:12px}.customer-shell .member-actions button{box-shadow:0 0 6px #ff263844}.customer-shell .member-actions button:nth-child(2){box-shadow:0 0 6px #17cfff55}.customer-shell .neon-reward-banner{padding:10px 16px;border-width:1px;box-shadow:0 0 8px #ffad3033}.neon-reward-banner>strong{font-size:36px}.neon-reward-banner>span:first-child{font-size:32px}.neon-reward-banner>span:nth-of-type(2){font-size:20px}.neon-reward-banner small{margin-top:4px;font-size:11px}
.customer-shell .quick-actions{margin:10px 0}.customer-shell .quick-action{min-height:58px;padding:8px}.customer-shell .quick-action span{font-size:24px}.customer-shell .quick-action strong{font-size:16px}.customer-shell .action-centre{margin-top:10px}.customer-shell .action-centre summary{padding:12px;font-size:14px}.customer-shell .card-tools{margin-bottom:6px}.customer-shell .site-banner{padding:12px;font-size:13px}
.top-order{position:sticky;top:0;z-index:60;padding:10px 4% 8px;background:#07100ff5;border-bottom:1px solid #258b72;backdrop-filter:blur(16px);box-shadow:0 4px 18px #0008}.top-order>button{display:flex;align-items:center;justify-content:space-between;width:100%;gap:12px;background:none;border:0;box-shadow:none;padding:0;color:#dffff3;text-align:left;font-size:13px}.top-order>button span{font-size:11px;color:#56e2c0}.top-order-track{height:4px;background:#263630;border-radius:4px;margin:8px 0 6px;overflow:hidden}.top-order-track>span{display:block;height:100%;background:linear-gradient(90deg,#1abf8a,#5effd8);transition:width .3s}.top-order-steps{display:flex;justify-content:space-between;gap:6px;font-size:10px;color:#97aaa4}.top-order-steps .done{color:#64f4c9;font-weight:800}
@media(max-width:560px){.customer-shell .unified-card{max-width:360px;padding:15px}.customer-shell .member-actions{gap:9px}.customer-shell .neon-reward-banner{padding:10px;gap:8px}.neon-reward-banner>span:nth-of-type(2){font-size:16px}.neon-reward-banner small{font-size:9px}.customer-shell .quick-action strong{font-size:12px}.customer-shell .quick-action{min-height:50px}.top-order{padding:9px 4%}.top-order-steps{font-size:9px}}
/* Premium bank-card finish plus iOS and embedded-phone compatibility. */
.customer-shell .quick-welcome{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;width:100%;min-width:0}.customer-shell .quick-welcome>div{min-width:0;overflow:hidden}.customer-shell .quick-welcome .name{display:block;max-width:100%;margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;overflow-wrap:normal;word-break:normal}.customer-shell .quick-welcome .membership-link{width:auto;min-width:0;max-width:180px;white-space:nowrap}
.customer-shell .unified-card{position:relative;isolation:isolate;overflow:hidden;min-height:252px;border:1px solid #d5bd78;border-radius:22px;background:radial-gradient(circle at 82% 12%,#edc76c30,transparent 25%),radial-gradient(circle at 12% 110%,#9c243b40,transparent 42%),linear-gradient(125deg,#050608 0%,#17191e 45%,#07080b 100%);box-shadow:0 1px 0 #fff6 inset,0 -1px 0 #000 inset,0 0 0 1px #7d632a,0 16px 38px #000b,0 0 22px #d5ae5430;color:#f8edc9;font-family:Arial,Helvetica,sans-serif;-webkit-transform:translateZ(0);transform:translateZ(0)}
.customer-shell .unified-card:before{content:"";position:absolute;z-index:-2;inset:0;background:repeating-linear-gradient(105deg,transparent 0 34px,#e5bd5c0d 35px 36px,transparent 37px 68px),linear-gradient(115deg,transparent 0 37%,#ffffff13 44%,transparent 52%);transform:none}.customer-shell .unified-card:after{content:"";position:absolute;z-index:-1;width:260px;height:260px;right:-105px;bottom:-150px;border:1px solid #d8b75d30;border-radius:50%;box-shadow:0 0 0 24px #d8b75d0b,0 0 0 48px #d8b75d08;background:none}
.customer-shell .store-card-top{font-size:11px;letter-spacing:.22em;color:#ead48f}.customer-shell .store-card-top>span{font-weight:800}.customer-shell .store-card-top b{border:1px solid #a98b48;background:#090a0d99;color:#f4df9c;padding:5px 10px;box-shadow:0 1px #ffffff12 inset}
.customer-shell .card-hardware{margin-top:2px}.customer-shell .card-chip{width:46px;height:34px;border-color:#80641f;background:linear-gradient(90deg,transparent 31%,#876718 32% 35%,transparent 36% 64%,#876718 65% 68%,transparent 69%),linear-gradient(0deg,transparent 31%,#876718 32% 35%,transparent 36% 64%,#876718 65% 68%,transparent 69%),linear-gradient(135deg,#fff1ad 0%,#b88a29 44%,#f2d772 75%,#8f6a1e 100%);box-shadow:0 1px 0 #fff8 inset,0 3px 7px #0007}.customer-shell .card-contactless{position:relative;width:28px;height:32px;color:transparent;font-size:0;transform:none}.customer-shell .card-contactless:before,.customer-shell .card-contactless:after{content:"";position:absolute;top:4px;bottom:4px;border:2px solid #ead287;border-left:0;border-radius:0 30px 30px 0}.customer-shell .card-contactless:before{left:4px;width:9px}.customer-shell .card-contactless:after{left:10px;width:14px}
.customer-shell .card-balance{margin:0;gap:9px}.customer-shell .card-balance strong{font-size:42px;line-height:.9;font-weight:500;letter-spacing:-.04em;color:#fff;text-shadow:0 1px 1px #000,0 0 14px #fff2}.customer-shell .card-balance span{color:#d6bc70;letter-spacing:.18em}.card-number{font:600 13px/1.2 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.13em;color:#f8edc9;text-shadow:0 1px #000;margin-top:0;font-variant-numeric:tabular-nums}
.customer-shell .store-card-bottom{display:grid;grid-template-columns:minmax(0,1fr) minmax(120px,1.25fr) auto;align-items:end;gap:10px}.customer-shell .card-holder small{color:#aeaa9e;font-size:7px;letter-spacing:.18em}.customer-shell .card-holder span{font-size:11px;font-weight:700;letter-spacing:.13em;color:#fff;text-shadow:0 1px #000}.customer-shell .card-reward{max-width:none;min-width:0;padding:7px 8px;border-color:#8c7844;background:#05060988}.customer-shell .card-reward strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:9px;color:#f6e5ad}.card-network{text-align:right;font-size:15px;font-weight:900;font-style:italic;line-height:.9;letter-spacing:-.04em;color:#fff;text-shadow:0 1px #000}.card-network small{display:block;margin-bottom:3px;color:#bfaa6e;font-size:6px;font-style:normal;letter-spacing:.18em}.card-network i{color:#d6b75d;font:inherit}.customer-shell .member-progress{height:3px;background:#ffffff16}.customer-shell .member-progress>span{background:linear-gradient(90deg,#a57625,#ffe695,#b88c35);box-shadow:0 0 8px #d6b75d66}
.tier-tile svg{display:block;width:100%;height:auto;aspect-ratio:1.633;border-radius:18px;filter:drop-shadow(0 13px 14px #0008)}
.top-order{-webkit-backdrop-filter:blur(16px);backdrop-filter:blur(16px)}.customer-shell .quick-hub>.app-tabs{-webkit-backdrop-filter:blur(16px);backdrop-filter:blur(16px)}
@supports not (aspect-ratio:1 / 1){.customer-shell .unified-card{height:277px}.customer-shell .member-actions button{height:210px}.tier-tile svg{height:auto}}
@media(max-width:560px){.customer-shell .quick-welcome{grid-template-columns:minmax(0,1fr) auto;gap:8px;min-height:48px}.customer-shell .quick-welcome .name{font-size:clamp(16px,5vw,23px)}.customer-shell .quick-welcome .membership-link{max-width:120px;padding:7px 8px;font-size:10px}.customer-shell .unified-card{width:100%;max-width:360px;min-height:218px;padding:15px;gap:5px}.customer-shell .card-chip{width:40px;height:30px}.customer-shell .card-balance strong{font-size:35px}.card-number{font-size:10px;letter-spacing:.08em}.customer-shell .store-card-bottom{grid-template-columns:minmax(0,1fr) minmax(95px,1fr) auto;gap:6px}.customer-shell .card-holder span{font-size:9px}.customer-shell .card-reward{padding:6px}.customer-shell .card-reward strong{font-size:8px}.card-network{font-size:12px}.card-network small{font-size:5px}.customer-shell .member-actions button{height:auto}.tier-tile svg{border-radius:14px}}
@media(max-width:360px){.customer-shell .quick-welcome .membership-link{max-width:100px}.customer-shell .unified-card{min-height:205px;padding:13px}.customer-shell .card-balance strong{font-size:31px}.card-number{font-size:9px}.customer-shell .card-reward{display:none}.customer-shell .store-card-bottom{grid-template-columns:minmax(0,1fr) auto}}
.monthly-leaderboard{margin:12px 0;padding:0;overflow:hidden;border:1px solid #74592a;border-radius:16px;background:radial-gradient(circle at 88% 0,#d99d2725,transparent 35%),linear-gradient(145deg,#15100a,#07080b 55%);box-shadow:0 10px 28px #0008}.monthly-leaderboard header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 15px;border-bottom:1px solid #6b542e;background:linear-gradient(90deg,#2a1b08,#0b0b0e)}.monthly-leaderboard header span,.monthly-leaderboard header small,.monthly-leaderboard header strong{display:block}.monthly-leaderboard header small{color:#d7b65e;font-size:8px;letter-spacing:.16em}.monthly-leaderboard header strong{margin-top:2px;color:#fff4d1;font-size:20px}.monthly-leaderboard header>b{flex:none;padding:5px 8px;border:1px solid #705c32;border-radius:99px;color:#eed17a;font-size:10px}.leader-prize{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 15px;background:#c9982118;color:#e8d9b0;font-size:11px}.leader-prize strong{flex:none;color:#ffd969}.monthly-leaderboard ol{list-style:none;margin:0;padding:4px 15px}.leader-row{display:grid;grid-template-columns:38px minmax(0,1fr) auto;align-items:center;gap:8px;padding:9px 0;border-bottom:1px solid #ffffff10}.leader-row:last-child{border-bottom:0}.leader-row>span:nth-child(2),.leader-row strong,.leader-row small{display:block;min-width:0}.leader-row strong{overflow:hidden;color:#f5efdf;text-overflow:ellipsis;white-space:nowrap;font-size:13px}.leader-row small{color:#928d83;font-size:9px}.leader-row>b{color:#e9ca70;font-size:13px;font-variant-numeric:tabular-nums}.leader-rank{text-align:center;color:#d8b75e;font-weight:900}.leader-first{margin:3px -7px;padding:9px 7px;border:1px solid #9b762f;border-radius:10px;background:linear-gradient(90deg,#cb92121a,transparent)}.leader-empty{padding:20px;text-align:center;color:#d7c8a4}.leader-own{margin:5px 12px 10px;padding:9px 11px;border:1px solid #2b8773;border-radius:10px;background:#0b2a23;color:#cffff0;font-size:11px}.leader-rules{display:block;padding:0 15px 12px;color:#746f66;font-size:8px;text-align:center}.leader-rules:before{content:'Leaderboard: '}
@media(max-width:560px){.monthly-leaderboard header{padding:12px}.monthly-leaderboard header strong{font-size:17px}.leader-prize{align-items:flex-start;padding:9px 12px;font-size:10px}.monthly-leaderboard ol{padding-inline:12px}.leader-row{padding:8px 0}.leader-own{margin-inline:9px}.leader-rules{padding-inline:10px}}
/* Compact premium loyalty card: keeps the bank-card proportions without dominating Home. */
.customer-shell .unified-card{width:min(100%,380px);max-width:380px;min-height:0;aspect-ratio:1.586;padding:14px;gap:4px;margin:6px auto 10px;border-radius:18px}.customer-shell .store-card-top{font-size:9px}.customer-shell .store-card-top b{padding:4px 7px;font-size:8px}.customer-shell .card-hardware{gap:8px;margin-top:0}.customer-shell .card-chip{width:35px;height:26px;border-radius:5px}.customer-shell .card-contactless{width:23px;height:26px}.customer-shell .card-contactless:before,.customer-shell .card-contactless:after{top:3px;bottom:3px}.customer-shell .card-contactless:before{left:3px;width:7px}.customer-shell .card-contactless:after{left:8px;width:12px}.customer-shell .card-balance{gap:6px}.customer-shell .card-balance strong{font-size:31px}.customer-shell .card-balance span{font-size:8px}.card-number{font-size:10px;letter-spacing:.1em}.customer-shell .store-card-bottom{grid-template-columns:minmax(0,1fr) minmax(86px,1fr) auto;gap:6px}.customer-shell .card-holder small{font-size:6px}.customer-shell .card-holder span{font-size:8px}.customer-shell .card-reward{padding:5px 6px}.customer-shell .card-reward strong{font-size:7px}.customer-shell .card-reward small{font-size:6px;margin-top:2px}.card-network{font-size:10px}.card-network small{font-size:5px}.customer-shell .member-progress{height:2px;margin-top:-2px}
@supports not (aspect-ratio:1 / 1){.customer-shell .unified-card{height:240px}}
@media(max-width:560px){.customer-shell .unified-card{width:min(310px,92%);max-width:310px;min-height:0;padding:12px;gap:3px;border-radius:16px}.customer-shell .card-balance strong{font-size:28px}.card-number{font-size:9px}.customer-shell .store-card-bottom{grid-template-columns:minmax(0,1fr) minmax(78px,.9fr) auto}.customer-shell .card-holder span{font-size:7.5px}.customer-shell .card-reward strong{font-size:6.5px}}
@supports not (aspect-ratio:1 / 1){@media(max-width:560px){.customer-shell .unified-card{height:196px}}}
@media(max-width:360px){.customer-shell .unified-card{width:min(286px,90%);min-height:0;padding:11px}.customer-shell .card-balance strong{font-size:26px}.card-number{font-size:8px}}
@supports not (aspect-ratio:1 / 1){@media(max-width:360px){.customer-shell .unified-card{height:180px}}}
"""


def order_progress(status: str, fulfillment: str) -> tuple[list[str], int]:
    if fulfillment == "pickup":
        steps = ["Sent", "Preparing", "Ready", "Complete"]
        stage = {"pending": 0, "accepted": 1, "ready_for_pickup": 2, "processing": 2, "paid": 3}
    elif fulfillment == "instore":
        steps = ["Sent", "Payment", "Complete"]
        stage = {"pending": 0, "accepted": 1, "processing": 1, "paid": 2}
    else:
        steps = ["Sent", "Preparing", "On the way", "Arrived", "Complete"]
        stage = {"pending": 0, "accepted": 1, "on_way": 2, "arrived": 3, "processing": 3, "paid": 4}
    return steps, stage.get(status, 0)


def page(title: str, content: str) -> str:
    body_class = ' class="customer-shell"' if 'id="customer-app"' in content else ""
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{html.escape(title)}</title><style>{CSS}</style></head><body{body_class}><main class="wrap"><div class="brand"><div class="logo-frame"><img src="/snr-logo.png" alt="Official Snr. Buns logo" width="1254" height="1254"></div><div class="tag">CITY RUN • DELIVERY • VIP</div></div>{content}<footer>SNR Buns • Your account is protected by your password</footer></main></body></html>'''


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
    return page("SNR Buns Members Card", f'''
    <section class="card">
      <div class="label">SNR Members Card</div>
      <h1>Manage your SNR card</h1>
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
          <summary>Create a new SNR card</summary>
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
        <strong>🥇 Gold</strong> at 50 purchases — +1 City Run sticker and +1 Golden Ticket<br>
        <strong>💎 Platinum</strong> at 100 purchases — +1 City Run sticker and +2 Golden Tickets<br>
        <strong>👑 SNR VIP</strong> at 200 purchases — +2 City Run stickers and +3 Golden Tickets</p>
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


def staff_reset_page(name: str, code: str, message: str = "") -> str:
    """One-use password form created from the private owner Discord control."""
    notice = f'<div class="notice">{html.escape(message)}</div>' if message else ""
    return page("Reset SNR Password", f'''
    <section class="card">
      <div class="label">Secure account recovery</div>
      <h1>Choose a new password</h1>
      <p>This private link is for <strong>{html.escape(name)}</strong>. It works once and expires after 24 hours.</p>
      {notice}
      <form method="post" action="/staff-reset" style="flex-direction:column">
        <input type="hidden" name="name" value="{html.escape(name, quote=True)}">
        <input type="hidden" name="code" value="{html.escape(code, quote=True)}">
        <input type="password" name="password" minlength="10" maxlength="128" autocomplete="new-password" placeholder="Create new password (10+ characters)" required>
        <input type="password" name="confirm" minlength="10" maxlength="128" autocomplete="new-password" placeholder="Repeat new password" required>
        <button type="submit">Save My New Password</button>
      </form>
      <div class="notice">SNR staff cannot see your new password.</div>
    </section>''')


def _sale_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone(LONDON).strftime("%d %b %Y")
    except (TypeError, ValueError):
        return "Previous visit"


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
    subject = "in-store experience" if latest.get("fulfillment_type") == "instore" else "pickup experience" if pickup else "driver"
    staff_name = html.escape(latest["assigned_driver_name"])
    star_buttons = ''.join(
        f'<label class="star-choice"><input type="radio" name="rating" value="{score}" required><span>{score} ★</span></label>'
        for score in range(1, 6))
    issues = ''.join(f'<option value="{key}">{label}</option>' for key, label in (
        ("food_quality", "Food quality"), ("delivery_time", "Delivery time"),
        ("driver_behaviour", "Driver behaviour"), ("missing_items", "Missing items"),
        ("other", "Other")))
    support = orders.support_for_order(latest["id"])
    help_box = (f'<div class="notice">Your order problem has been sent to SNR staff.</div>' if support else
                f'''<details class="compact-info"><summary>Something wrong with the order?</summary><form class="problem-form" method="post" action="/support"><input type="hidden" name="support_request_key" value="{html.escape(form_token, quote=True)}"><input type="hidden" name="order_id" value="{int(latest["id"])}"><select name="issue_type" required><option value="" disabled selected>Choose the problem</option>{issues}</select><input name="details" maxlength="250" placeholder="Tell us briefly what happened" required><button type="submit">Send Problem to SNR Staff</button></form></details>''')
    return f'''<div class="rating-overlay" id="rating-popup" role="dialog" aria-modal="true" aria-labelledby="rating-title"><div class="rating-popup"><div class="label">✅ ORDER #{int(latest["id"])} COMPLETE</div><h2 id="rating-title">How was your order?</h2><p>Rate your {subject} with <strong>{staff_name}</strong>.</p><form method="post" action="/review"><input type="hidden" name="review_request_key" value="{html.escape(form_token, quote=True)}"><input type="hidden" name="order_id" value="{int(latest["id"])}"><div class="rating-options" aria-label="Choose a rating from 1 to 5 stars">{star_buttons}</div><div class="low-rating-reason" id="low-rating-reason"><label><strong>What went wrong?</strong><select name="issue_category"><option value="" selected>Choose a reason</option>{issues}</select></label><p class="muted">A 1–2 star rating alerts SNR management so we can help.</p></div><input type="text" name="comment" maxlength="250" placeholder="Optional short review" aria-label="Optional review"><button type="submit">Send My Rating</button><button class="later" id="rating-later" type="button">Maybe later</button></form>{help_box}</div></div>'''


def delivery_section(customer: dict, orders: DeliveryStore, shifts: StaffShifts,
                     services: CustomerServices, form_token: str) -> str:
    rows = orders.summary(customer["display_name"])
    service = orders.service_mode()
    fee = orders.outstanding_fee(customer["customer_key"])
    active = next((row for row in rows if row["status"] in ("pending", "accepted", "on_way", "arrived", "ready_for_pickup", "processing")), None)
    labels = {
        "pending": "Waiting for a driver to accept",
        "accepted": "Accepted — your order is being prepared",
        "on_way": "Your driver is on the way",
        "arrived": "Your driver has arrived and is waiting outside",
        "ready_for_pickup": "Your order is ready for collection at SNR Buns",
        "processing": "Payment is being confirmed",
        "paid": "Delivered, paid and City Run stickers added",
        "cancelled": "Cancelled",
        "wasted_journey": "Wasted journey — £500 delivery fee owed",
    }
    can_reorder = not active and not fee and orders.configured()
    recent_parts = []
    for row in rows:
        pickup_row = (row.get("fulfillment_type") or "delivery") == "pickup"
        instore_row = row.get("fulfillment_type") == "instore"
        row_labels = dict(labels, paid="Served in store, paid and City Run stickers added", pending="Awaiting counter payment") if instore_row else labels
        description = (f'{"💳 In Store" if instore_row else "🛍️ Pickup" if pickup_row else "🚗 Delivery"} #{row["id"]}: '
                       f'<strong class="status-{"pending" if row["status"] in ACTIVE_ORDER_STATUSES else row["status"]}">'
                       f'{row_labels.get(row["status"], row["status"])}</strong><br>'
                       f'{html.escape(row["deal_name"])} • £{int(row["price"]):,}')
        reorder = ''
        if can_reorder and row["status"] == "paid":
            payload = html.escape(json.dumps({item["key"]: int(item["quantity"]) for item in orders.items(row)}, separators=(",", ":")), quote=True)
            mode = html.escape(row.get("fulfillment_type") or "delivery", quote=True)
            reorder = f'<button class="secondary reorder-button" type="button" data-reorder="{payload}" data-mode="{mode}">Order Again</button>'
        review_html = ''
        support_html = ''
        if row["status"] == "paid" and row.get("assigned_driver_id"):
            review = orders.review_for_order(row["id"])
            subject = "in-store experience" if instore_row else "pickup experience" if pickup_row else "driver"
            if review:
                stars = "★" * int(review["rating"]) + "☆" * (5 - int(review["rating"]))
                comment = f'<br><small>“{html.escape(review["comment"]) }”</small>' if review.get("comment") else ''
                review_html = f'<div class="review-box reviewed">Your {subject} rating: <span class="stars">{stars}</span>{comment}</div>'
            support = orders.support_for_order(row["id"])
            if support:
                state = "resolved" if support["status"] == "resolved" else "sent to SNR staff"
                support_html = f'<div class="review-box reviewed">Order problem: {state}</div>'
            else:
                support_html = f'''<details class="compact-info"><summary>Report a problem</summary><form class="problem-form" method="post" action="/support"><input type="hidden" name="support_request_key" value="{html.escape(form_token, quote=True)}"><input type="hidden" name="order_id" value="{int(row["id"])}"><select name="issue_type" required><option value="" disabled selected>Choose the problem</option><option value="food_quality">Food quality</option><option value="delivery_time">Delivery time</option><option value="driver_behaviour">Driver behaviour</option><option value="missing_items">Missing items</option><option value="other">Other</option></select><input name="details" maxlength="250" placeholder="Tell us briefly what happened" required><button type="submit">Send to SNR Staff</button></form></details>'''
        recent_parts.append(f'<div class="reorder-row"><div>{description}{review_html}{support_html}</div>{reorder}</div>')
    recent = ''.join(recent_parts)
    if active:
        estimate = orders.order_estimate(active["id"])
        pickup = (active.get("fulfillment_type") or "delivery") == "pickup"
        instore = active.get("fulfillment_type") == "instore"
        if instore:
            labels = dict(labels, pending="Sent to counter — awaiting payment", paid="Payment confirmed")
        handler_label = "Counter staff" if instore else "Handling staff" if pickup else "Driver"
        driver = (f'<br>{handler_label}: <strong>{html.escape(active["assigned_driver_name"])}</strong>'
                  if active.get("assigned_driver_name") else "")
        note = f'<br>Your note: {html.escape(active["notes"])}' if active.get("notes") else ""
        subtotal = int(active.get("subtotal") or active["price"])
        delivery_fee = int(active.get("delivery_fee") or 0)
        discount = int(active.get("discount_amount") or 0)
        birthday_discount = int(active.get("birthday_discount") or 0)
        voucher_discount = int(active.get("voucher_discount") or 0)
        charge_label = "In-store charge" if instore else "Pickup charge" if pickup else "Delivery"
        discount_line = (f'<br>Discount ({html.escape(active.get("discount_code") or "code")}): '
                         f'<strong>−£{discount:,}</strong>' if discount else "")
        birthday_line = (f'<br>🎂 Birthday reward: <strong>−£{birthday_discount:,}</strong>'
                         if birthday_discount else "")
        voucher_line = (f'<br>🎫 Voucher ({html.escape(active.get("voucher_code") or "reward")}): '
                        f'<strong>−£{voucher_discount:,}</strong>' if active.get("voucher_code") else "")
        order_form = (
            f'<div class="notice order-status" data-order-id="{active["id"]}" data-order-status="{active["status"]}">'
            f'<strong>Order #{active["id"]}: {labels[active["status"]]}</strong><br>'
            f'<div class="review-box" id="live-estimate"><strong>🕒 {html.escape(estimate["eta_text"])}</strong></div>'
            f'{"<p>Show your name and order number to staff, then pay at the counter.</p>" if instore else order_tracker(active["status"], pickup)}'
            f'{html.escape(active["deal_name"])}<br>Food subtotal: £{subtotal:,}'
            f'{discount_line}{birthday_line}{voucher_line}<br>{charge_label}: {"FREE" if delivery_fee == 0 else f"£{delivery_fee:,}"}'
            f'<br><strong>Total owed: £{int(active["price"]):,}</strong><br>'
            f'{"At the SNR Buns counter" if instore else "Collection: SNR Buns" if pickup else "Delivery location: " + html.escape(active["postal"])}'
            f'{driver}{note}</div><div id="status-toast" role="status"></div><script src="/delivery.js" defer></script>'
        )
    elif fee:
        order_form = f'''<div class="debt-warning"><strong>⚠️ £{int(fee["amount"]):,} OWED</strong><br>Wasted Journey fee from delivery order #{int(fee["order_id"])}.<br><br>New deliveries are unavailable until SNR staff mark this fee as paid or waived.</div>'''
    else:
        choices = "".join(
            f'''<div class="deal-box"><strong>{html.escape(deal.name)}</strong><span>{html.escape(deal.item_summary)}</span><span>{deal.loyalty_points} City Run sticker(s) • {deal.golden_tickets} Golden ticket(s)</span><span class="price">£{deal.price:,} each</span><label class="quantity">Amount <input class="deal-qty" type="number" name="qty_{deal.key}" value="0" min="0" max="10" step="1" data-price="{deal.price}" aria-label="Amount of {html.escape(deal.name, quote=True)}"></label></div>'''
            for deal in DEALS.values())
        if orders.configured():
            delivery_fee = int(customer["membership"]["delivery_fee"])
            fee_text = "FREE — SNR VIP benefit" if delivery_fee == 0 else f"£{delivery_fee:,}"
            drivers = shifts.drivers_available()
            delivery_open = service["mode"] not in ("pickup_only", "delivery_paused", "closed")
            mode_options = ('<option value="delivery" selected>🚗 Delivery</option><option value="pickup">🛍️ Click &amp; Collect from SNR Buns</option>'
                            if drivers and delivery_open else
                            '<option value="pickup" selected>🛍️ Click &amp; Collect from SNR Buns</option><option value="delivery" disabled>🚗 Delivery — unavailable</option>')
            if service["mode"] == "closed":
                availability = '<div class="debt-warning"><strong>🔴 SNR Buns is currently closed.</strong><br>Please check again when service reopens.</div>'
            elif service["mode"] in ("pickup_only", "delivery_paused"):
                availability = '<div class="notice"><strong>🛍️ Deliveries are paused.</strong><br>Pickup ordering is still open.</div>'
            elif service["mode"] == "busy":
                availability = '<div class="notice"><strong>🟠 We are busy.</strong><br>Orders are open, but estimates are a little longer.</div>'
            else:
                availability = ("" if drivers else '<div class="notice"><strong>No delivery drivers are clocked in.</strong><br>Pickup ordering is still available.</div>')
            birthday = orders.birthday_status(customer["customer_key"])
            birthday_notice = (f'<div class="notice"><strong>🎂 Happy Birthday — {html.escape(birthday["reward"])}!</strong><br>Your birthday reward will be applied automatically to this order.</div>'
                               if birthday["eligible"] else "")
            checkout_vouchers = [row for row in services.vouchers(customer["customer_key"], active_only=True)
                                 if row["voucher_kind"] in ("free_delivery", "percent", "fixed")]
            voucher_options = "".join(f'<option value="{html.escape(row["voucher_code"], quote=True)}">{html.escape(row["title"])} · {html.escape(row["voucher_code"])}</option>' for row in checkout_vouchers)
            voucher_select = (f'<label><strong>Use a voucher</strong><select name="voucher_code"><option value="">No voucher</option>{voucher_options}</select></label>'
                              if checkout_vouchers else "")
            order_form = (availability if service["mode"] == "closed" else f'''{availability}{birthday_notice}<div id="reorder-message" class="notice reorder-message" role="status"></div><form class="delivery-form" method="post" action="/order" data-delivery-fee="{delivery_fee}"><input type="hidden" name="order_request_key" value="{html.escape(form_token, quote=True)}"><label><strong>How would you like your order?</strong><select id="fulfillment-type" name="fulfillment_type" required>{mode_options}</select></label><div class="deal-list">{choices}</div><div class="subtotal" aria-live="polite">Food subtotal: <span id="delivery-subtotal">£0</span><br><small id="order-fee-label">Membership delivery: {fee_text}</small><br>Total before discounts: <span id="delivery-total">£{delivery_fee if drivers and delivery_open else 0:,}</span></div><input name="discount_code" maxlength="20" autocomplete="off" placeholder="Discount code (optional)" aria-label="Discount code">{voucher_select}<textarea name="notes" maxlength="200" placeholder="Optional order notes — meeting point, no ice, call when nearby…" aria-label="Optional order notes"></textarea><div class="location-row"><input id="delivery-location" name="postal" minlength="2" maxlength="80" autocomplete="street-address" placeholder="Required postal or delivery location" aria-label="Postal or delivery location" {"required" if drivers and delivery_open else "hidden"}><button type="submit">Place Order</button></div><p class="muted">Pickup is always free. Delivery uses your membership price. Choose up to 10 of each deal (20 deals total). Rewards are added only after staff confirm payment.</p></form><script src="/delivery.js" defer></script>''')
        else:
            order_form = f'<div class="deal-list">{choices}</div><div class="notice">Online delivery is being set up. Please contact SNR Buns for now.</div>'
    return f'''<section class="app-page delivery" id="delivery"><h2 class="app-page-title">🍔 Click &amp; Collect or Delivery</h2><div class="drawer-body"><p class="muted">Choose your meals and quantities, then collect from SNR Buns or have them delivered.</p>{order_form}<details class="compact-info"><summary>Previous orders</summary><div class="history">{recent or '<p class="muted">No orders yet.</p>'}</div></details></div></section>'''


def raffle_section(customer: dict, raffles: RaffleStore, form_token: str) -> str:
    raffle = raffles.current()
    heading = '<h2 class="app-page-title">🎟️ SNR Raffle</h2>'
    if not raffle:
        return f'<section class="app-page">{heading}<div class="drawer-body"><div class="notice">There is no raffle running right now. Check back soon.</div></div></section>'
    title, prize = html.escape(raffle["title"]), html.escape(raffle["prize"])
    if raffle["status"] == "drawn":
        winner = f'''<div class="raffle-winner"><div class="label">🏆 DRAW COMPLETE</div><h3>{title}</h3><strong>Number {int(raffle["winning_number"])}</strong><p>Winner: <b>{html.escape(raffle["winner_name"])}</b></p><p>Prize: {prize}</p></div>'''
        return f'<section class="app-page">{heading}<div class="drawer-body">{winner}</div></section>'
    if raffle["status"] == "cancelled":
        return f'<section class="app-page">{heading}<div class="drawer-body"><div class="notice"><strong>{title}</strong><br>This raffle was cancelled. No winner was drawn.</div></div></section>'
    entries = raffles.entries(raffle["id"])
    by_number = {int(row["number"]): row for row in entries}
    mine = [row for row in entries if row["customer_key"] == customer["customer_key"]]
    remaining = max(0, int(raffle["customer_limit"]) - len(mine))
    cells = []
    for number in range(1, int(raffle["number_limit"]) + 1):
        row = by_number.get(number)
        if not row and raffle["status"] == "open" and remaining:
            cells.append(f'<label class="raffle-number"><input type="checkbox" name="number_{number}" value="{number}"><span>{number}</span></label>')
        elif row and row["customer_key"] == customer["customer_key"]:
            state = "mine-confirmed" if row["status"] == "confirmed" else "mine-pending"
            cells.append(f'<span class="raffle-number {state}"><span>{number}</span></span>')
        else:
            cells.append(f'<span class="raffle-number taken"><span>{number}</span></span>')
    mine_text = ", ".join(
        f'{int(row["number"])} ({"paid" if row["status"] == "confirmed" else "awaiting payment"})'
        for row in mine) or "None yet"
    if raffle["status"] == "closed":
        action = '<div class="notice"><strong>Entries are closed.</strong><br>The winning number will be drawn soon.</div>'
    elif not raffles.configured():
        action = '<div class="notice">Raffle alerts are being set up. Please ask SNR staff.</div>'
    elif remaining == 0:
        action = '<div class="notice">You have reached the limit of 10 numbers in this raffle.</div>'
    else:
        action = f'''<form class="raffle-form" method="post" action="/raffle-request" data-max="{remaining}" data-price="{int(raffle["entry_price"])}"><input type="hidden" name="raffle_request_key" value="{html.escape(form_token, quote=True)}"><div class="raffle-numbers">{''.join(cells)}</div><p id="raffle-choice-total" class="subtotal">Choose up to {remaining} number(s)</p><button type="submit">Request My Numbers</button></form>'''
    if raffle["status"] != "open" or remaining == 0:
        action = f'<div class="raffle-numbers">{"".join(cells)}</div>' + action
    status_text = "OPEN — choose your numbers" if raffle["status"] == "open" else "CLOSED — draw coming soon"
    return f'''<section class="app-page">{heading}<div class="drawer-body" data-raffle-id="{int(raffle["id"])}" data-raffle-status="{raffle["status"]}" data-raffle-confirmed="{int(raffle["confirmed_numbers"])}"><div class="raffle-hero"><div class="label">{status_text}</div><h3>{title}</h3><p><strong>Prize: {prize}</strong><br>£{int(raffle["entry_price"]):,} per number • choose up to 10</p></div><div class="raffle-stats"><div><strong>{int(raffle["confirmed_numbers"])}</strong><br>Paid</div><div><strong>{int(raffle["pending_numbers"])}</strong><br>Reserved</div><div><strong>{int(raffle["available_numbers"])}</strong><br>Available</div></div><p><strong>My numbers:</strong> {mine_text}</p><div class="raffle-legend"><span>🟢 Your paid number</span><span>🟠 Awaiting payment</span><span>⬛ Unavailable</span></div>{action}<div class="notice"><strong>How it works</strong><br>Your chosen numbers are reserved immediately. Pay SNR staff in-store, then staff will confirm your payment. Only confirmed paid numbers enter the draw.</div><script src="/raffle.js" defer></script></div></section>'''


def membership_gallery(current: str) -> str:
    colours = [("#ed3037", "#6e0b14"), ("#d39a58", "#663616"), ("#dce2ec", "#596779"),
               ("#ffe69b", "#aa7620"), ("#b8dce6", "#485970"), ("#33343a", "#050507")]
    tiles = []
    for index, (name, level) in enumerate(VIP_LEVELS.items()):
        light, dark = colours[index]
        ink = "#f7e1a6" if name in ("Regular", "SNR VIP") else "#181818"
        active = name == current
        art = f'''<svg viewBox="0 0 320 196" role="img" aria-label="{html.escape(name)} premium membership card"><defs><linearGradient id="tier-{index}" x2="1" y2="1"><stop stop-color="{light}"/><stop offset=".48" stop-color="{dark}"/><stop offset="1" stop-color="#050507"/></linearGradient><linearGradient id="metal-{index}" x2="1" y2="1"><stop stop-color="#fff5bd"/><stop offset=".45" stop-color="#b98a2e"/><stop offset="1" stop-color="#f3da84"/></linearGradient></defs><rect x="1" y="1" width="318" height="194" rx="19" fill="url(#tier-{index})" stroke="#dbc38a"/><path d="M-20 165C70 95 190 58 345 62" fill="none" stroke="#ffffff" stroke-opacity=".08" stroke-width="46"/><path d="M105 0L320 165M180 0L320 100" stroke="#ffffff" stroke-opacity=".07" stroke-width="18"/><text x="22" y="30" fill="{ink}" font-family="Arial,sans-serif" font-size="14" font-weight="bold" letter-spacing="2">SNR BUNS</text><text x="298" y="29" fill="{ink}" text-anchor="end" font-family="Arial,sans-serif" font-size="9" font-weight="bold">{html.escape(name).upper()}</text><rect x="23" y="52" width="40" height="31" rx="6" fill="url(#metal-{index})" stroke="#8c6c27"/><path d="M36 52v31m14-31v31M23 67h40" stroke="#8c6c27"/><path d="M75 61q12 7 0 14m7-18q19 11 0 22" fill="none" stroke="{ink}" stroke-width="2" stroke-linecap="round"/><text x="22" y="114" fill="{ink}" font-family="monospace" font-size="14" letter-spacing="2.5">••••  ••••  ••••  SNR</text><text x="22" y="142" fill="{ink}" font-family="Arial,sans-serif" font-size="7" letter-spacing="1.5">MEMBERSHIP LEVEL</text><text x="22" y="160" fill="{ink}" font-family="Arial,sans-serif" font-size="17" font-weight="bold">{html.escape(name).upper()}</text><text x="298" y="172" fill="{ink}" text-anchor="end" font-family="Arial,sans-serif" font-size="14" font-style="italic" font-weight="bold">SNR ELITE</text><text x="22" y="180" fill="{ink}" font-family="Arial,sans-serif" font-size="7" letter-spacing="1.5">MEMBER CARD</text></svg>'''
        fee = "Free delivery" if not level["delivery_fee"] else f'£{level["delivery_fee"]} delivery'
        tiles.append(f'''<article class="tier-tile {'tier-current' if active else ''}">{art}<h3>{html.escape(name)} {'— Your level' if active else ''}</h3><p>{level["minimum_sales"]}+ purchases</p><ul><li>{level["bonus_points"]} extra City Run sticker(s) per meal deal</li><li>{level["bonus_tickets"]} extra Golden Ticket(s) per meal deal</li><li>{fee}</li></ul></article>''')
    return '<section class="membership-gallery"><h2>All membership cards</h2><p>Swipe to compare all six levels. Bonuses are added to each meal deal’s normal rewards. Click &amp; Collect is free at every level.</p><div class="tier-cards">' + ''.join(tiles) + '</div></section>'


def leaderboard_section(board: dict) -> str:
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    rows = "".join(
        f'''<li class="leader-row {'leader-first' if row['rank'] == 1 else ''}"><span class="leader-rank">{medals.get(row['rank'], '#' + str(row['rank']))}</span><span><strong>{html.escape(row['display_name'])}</strong><small>{int(row['purchases'])} purchase(s)</small></span><b>£{int(row['spend']):,}</b></li>'''
        for row in board["rows"][:5]
    ) or '<li class="leader-empty">Make the first purchase this month and take the crown!</li>'
    own = board.get("own")
    if own and own.get("excluded"):
        chase = "Your account is not taking part in this month’s customer chase. Your City Run stickers and membership still work normally."
    elif own and int(own["rank"]) == 1:
        chase = "👑 You are leading! Keep your crown until the month ends."
    elif own and int(own["gap_to_next"]) > 0:
        chase = (f'''You are <strong>#{int(own["rank"])}</strong> with <strong>£{int(own["spend"]):,}</strong>. '''
                 f'''Another <strong>£{int(own["gap_to_next"]):,}</strong> takes the next position.''')
    else:
        chase = "Your next completed purchase starts your climb."
    return f'''<section class="monthly-leaderboard"><header><span><small>MONTHLY CUSTOMER CHASE</small><strong>🏆 SNR Champions</strong></span><b>{int(board['days_left'])} days left</b></header><div class="leader-prize"><strong>🎁 Monthly Giveaway</strong><span>The #1 spender at month end becomes SNR Champion and wins the monthly giveaway.</span></div><ol>{rows}</ol><p class="leader-own">{chase}</p><small class="leader-rules">Confirmed purchases only • cancelled and voided sales do not count • resets every calendar month</small></section>'''


def city_run_section(customer: dict, city_run: CityRunStore, reveal_token: str) -> str:
    board = city_run.customer_board(customer["customer_key"])
    campaign = board.get("campaign") or {}
    if not campaign or campaign.get("status") == "draft":
        return '''<section class="app-page"><h2 class="app-page-title">🏁 SNR City Run</h2><div class="drawer-body"><div class="city-draft"><h2>City Run is being prepared</h2><p>Your existing sticker balance is protected. It will become digital business reveals when the season opens.</p></div></div></section>'''
    routes = []
    board_items = []
    for route in board["collections"]:
        cards = []
        for item in route["items"]:
            state = "owned" if item["owned"] else "locked"
            status = (f'{int(item["copies"])} collected' if item["owned"] else "Not collected")
            board_items.append((item, route))
            cards.append(f'''<article class="city-business {state}" style="--set-colour:{html.escape(route['colour'], quote=True)}"><div class="city-business-art"><img src="/city-art/{html.escape(item['key'], quote=True)}.svg?v=individual-art-2" loading="lazy" alt=""></div><div class="city-business-copy"><strong>{html.escape(item['name'])}</strong><small>{html.escape(status)}</small></div></article>''')
        reward = route.get("reward") or {}
        claim = route.get("claim") or {}
        if claim:
            reward_action = f'''<div class="notice"><strong>{html.escape(reward.get('reward_name') or route['name'])}</strong><br>Claim status: {html.escape(str(claim['status']).replace('_', ' ').title())}</div>'''
        elif route["complete"] and reward:
            reward_action = f'''<form method="post" action="/city-run-claim"><input type="hidden" name="city_run_request_key" value="{html.escape(reveal_token, quote=True)}"><input type="hidden" name="reward_key" value="{html.escape(route['key'], quote=True)}"><button type="submit">Claim {html.escape(reward['reward_name'])}</button></form>'''
        elif reward:
            reward_action = f'''<p class="muted">Complete this route to unlock: <strong>{html.escape(reward['reward_name'])}</strong></p>'''
        else:
            reward_action = ""
        routes.append(f'''<details class="city-route" {'open' if route['collected'] else ''}><summary><strong>{html.escape(route['name'])}</strong><span>{int(route['collected'])}/{int(route['total'])}{' ✓' if route['complete'] else ''}</span></summary><div class="city-business-grid">{''.join(cards)}</div><div class="city-route-reward">{reward_action}</div></details>''')
    available = int(board["available_reveals"])
    action = f'''<form class="city-reveal-form" method="post" action="/city-run-reveal"><input type="hidden" name="city_run_request_key" value="{html.escape(reveal_token, quote=True)}"><button type="submit" {'disabled' if available < 1 or campaign.get('status') != 'active' else ''}>{'Reveal a Business Sticker' if available else 'Earn a qualifying meal to receive a sticker'}</button></form>'''
    status = "PAUSED" if campaign.get("status") == "paused" else "SEASON LIVE"
    grand = board.get("grand_reward") or {}
    grand_claim = board.get("grand_claim") or {}
    if grand_claim:
        grand_action = f'''<div class="notice">Grand-prize claim: <strong>{html.escape(str(grand_claim['status']).title())}</strong></div>'''
    elif board.get("grand_complete") and grand:
        grand_action = f'''<form method="post" action="/city-run-claim"><input type="hidden" name="city_run_request_key" value="{html.escape(reveal_token, quote=True)}"><input type="hidden" name="reward_key" value="grand"><button type="submit">Claim the Grand-Prize Vehicle</button></form>'''
    else:
        grand_action = '<p class="muted">Collect all 38 businesses to unlock the grand-prize vehicle.</p>'
    board_art = poster_markup(item['key'] for item, _ in board_items if item['owned'])
    corners = ''.join(f'''<div class="city-corner {'unlocked' if corner['unlocked'] else ''}"><b>{'✓' if corner['unlocked'] else '○'} {html.escape(corner['name'])}</b><small>{html.escape(corner['description'])}</small></div>''' for corner in board.get('corners', []))
    return f'''<section class="app-page city-board-page"><h2 class="app-page-title">🏁 SNR City Run</h2><div class="drawer-body"><div class="city-run-hero"><small>{status}</small><strong>Collect the businesses.<br>Win the ride.</strong><span>Qualifying meals award City Run stickers. Duplicates can appear, so completing all 38 takes commitment.</span></div><div class="city-run-score"><div><b>{int(board['unique_collected'])}</b><small>OF 38 COLLECTED</small></div><div><b>{available}</b><small>STICKERS READY</small></div><div><b>{int(board['duplicates'])}</b><small>DUPLICATES</small></div></div>{action}{board_art}<section class="city-corners"><h3>🧭 Board corners</h3>{corners}</section><div class="city-grand"><strong>🏎️ Complete the City</strong>{grand_action}</div><details class="city-board-details"><summary>View route rewards and live collection details</summary><div class="city-board-route-list">{''.join(routes)}</div></details></div></section>'''


def customer_page(customer: dict, claims: ClaimStore, orders: DeliveryStore, shifts: StaffShifts,
                  accounts: Accounts, raffles: RaffleStore, services: CustomerServices,
                  city_run: CityRunStore,
                  claim_token: str, order_token: str, security_token: str, raffle_token: str,
                  service_token: str, city_run_token: str) -> str:
    recent = "".join(f'<div class="sale"><div><strong>{html.escape(str(s["deal_name"]))}</strong><br><small>{_sale_date(s["created_at"])}</small></div><span>+{int(s["loyalty_points"])} sticker(s)</span></div>' for s in customer.get("recent_sales", [])) or '<div class="notice">No recent visits to show.</div>'
    jackpot = ('''<strong>🏆 YOU HAVE A WINNING GOLDEN TICKET!</strong><br>Your account has won the £5,000 jackpot. Speak to SNR staff to verify and collect the prize.'''
               if int(customer["jackpot_wins"]) else
               f'''<details class="compact-info"><summary>My {int(customer["golden_tickets"])} automatic Golden Ticket(s)</summary><p>Every meal deal issues its listed ticket(s). They are entered automatically, then each ticket is checked instantly against one secret winner hidden among 1,000 tickets. You do not need to enter anything.</p></details>''')
    recovery = '' if accounts.has_security(customer['customer_key']) else f'''<section class="notice"><strong>Protect your password recovery</strong><p>This older account needs a memorable question. Set it now so you can reset your own password later.</p><form method="post" action="/set-security"><input type="hidden" name="security_request_key" value="{html.escape(security_token, quote=True)}"><select name="security_question" required><option value="" disabled selected>Choose a memorable question</option>{question_options()}</select><input type="password" name="security_answer" minlength="3" maxlength="80" autocomplete="off" placeholder="Your memorable answer" required><button type="submit">Save Memorable Answer</button></form></section>'''
    fee = orders.outstanding_fee(customer["customer_key"])
    debt = (f'''<div class="debt-warning"><strong>⚠️ DELIVERY ACCOUNT: £{int(fee["amount"]):,} OWED</strong><br>Wasted Journey fee. Please speak to SNR staff. New delivery orders are blocked until it is paid or waived.</div>'''
            if fee else '')
    membership = customer["membership"]
    card_suffix = f'{int(hashlib.sha256(customer["customer_key"].encode()).hexdigest()[-8:], 16) % 10000:04d}'
    leaderboard = leaderboard_section(orders.db.monthly_leaderboard(customer["display_name"], limit=5))
    next_text = (f'''<p class="muted">Complete {membership["remaining"]} more purchase(s) to unlock {html.escape(membership["next_level"])}.</p>'''
                 if membership["next_level"] else '<p class="muted">You have reached your current highest membership level.</p>')
    delivery_benefit = ("FREE delivery" if int(membership["delivery_fee"]) == 0
                        else f'£{int(membership["delivery_fee"]):,} delivery')
    vip = f'''<div class="stat vip-card"><div class="label">SNR Customer Membership</div><div class="num">{membership["emoji"]} {html.escape(membership["name"])}</div><p class="vip-benefits">Every purchase earns the normal rewards <strong>plus {membership["bonus_points"]} City Run sticker(s) and {membership["bonus_tickets"]} Golden Ticket(s)</strong>.<br>Delivery benefit: <strong>{delivery_benefit}</strong>.</p>{next_text}<small>Regular 0+ • Bronze 10+ • Silver 25+ • Gold 50+ • Platinum 100+ • SNR VIP 200+</small></div>'''
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
    announcement = orders.customer_announcement()
    banner_labels = {"info": "SNR Update", "promo": "Special Offer", "urgent": "Important Notice"}
    banner_style = announcement.get("style") if announcement.get("style") in banner_labels else "info"
    banner_hidden = "" if announcement.get("active") else " hidden"
    banner = (f'<aside id="customer-announcement" class="customer-banner banner-{banner_style}"{banner_hidden}>'
              f'<strong id="announcement-label">{banner_labels[banner_style]}</strong>'
              f'<span id="announcement-message">{html.escape(announcement.get("message") or "")}</span></aside>')
    service = orders.service_mode()
    drivers = shifts.drivers_available()
    if service["mode"] == "closed":
        service_text, service_class = "CLOSED", "quick-closed"
    elif service["mode"] in ("pickup_only", "delivery_paused") or not drivers:
        service_text, service_class = "PICKUP ONLY", "quick-pickup"
    elif service["mode"] == "busy":
        service_text, service_class = "BUSY • OPEN", "quick-busy"
    else:
        service_text, service_class = "DELIVERY OPEN", "quick-open"
    points = int(customer["loyalty_points"])
    city_board = city_run.customer_board(customer["customer_key"])
    city_reveals = int(city_board.get("available_reveals") or 0)
    city_unique = int(city_board.get("unique_collected") or 0)
    progress = min(city_unique, 38)
    city_run_live = (city_board.get("campaign") or {}).get("status") in ("active", "paused")
    if city_run_live:
        points = city_reveals
    reward_title = f"{city_reveals} sticker{'s' if city_reveals != 1 else ''} ready" if city_reveals else "Earn stickers from meals"
    latest_orders = orders.summary(customer["customer_key"], 1)
    active_order = next((row for row in latest_orders if row["status"] in ("pending", "accepted", "on_way", "arrived", "ready_for_pickup", "processing")), None)
    top_order = ""
    if active_order:
        steps, stage = order_progress(active_order["status"], active_order.get("fulfillment_type") or "delivery")
        step_labels = "".join(f'<span class="{"done" if index <= stage else ""}">{label}</span>' for index, label in enumerate(steps))
        top_order = f'''<aside class="top-order" aria-label="Current order progress"><button type="button" data-tab-target="order"><strong>Order #{int(active_order["id"])} · {steps[stage]}</strong><span>View order ›</span></button><div class="top-order-track" role="progressbar" aria-label="Order stage" aria-valuemin="0" aria-valuemax="{len(steps)-1}" aria-valuenow="{stage}"><span style="width:{max(4, round(stage / (len(steps)-1)*100))}%"></span></div><div class="top-order-steps">{step_labels}</div></aside>'''
        quick_labels = {"pending": "Waiting for acceptance", "accepted": "Being prepared", "on_way": "Driver on the way",
                        "arrived": "Driver has arrived", "ready_for_pickup": "Ready for collection",
                        "processing": "Payment being confirmed"}
        if active_order.get("fulfillment_type") == "instore":
            quick_labels["pending"] = "At counter — awaiting payment"
        order_icon = "💳" if active_order.get("fulfillment_type") == "instore" else "🛍️" if (active_order.get("fulfillment_type") or "delivery") == "pickup" else "🚗"
        quick_order = f'''<button class="quick-order" type="button" data-tab-target="order" aria-selected="false" data-quick-order-id="{int(active_order["id"])}" data-quick-order-status="{active_order["status"]}"><span class="quick-order-icon">{order_icon}</span><span><small>LIVE ORDER #{int(active_order["id"])}</small><strong>{html.escape(quick_labels.get(active_order["status"], active_order["status"]))}</strong><small>{html.escape(active_order["deal_name"])} • £{int(active_order["price"]):,}</small></span><b>›</b></button>'''
    else:
        quick_order = '''<button class="quick-order" type="button" data-tab-target="order" aria-selected="false"><span class="quick-order-icon">🍔</span><span><small>NO ACTIVE ORDER</small><strong>Ready when you are</strong><small>Start a delivery or pickup order</small></span><b>›</b></button>'''
    action_rows = services.customer_actions(customer["customer_key"], 4)
    action_history = "".join(
        f'<div class="action-status"><span>{html.escape(ACTION_LABELS.get(row["action_type"], row["action_type"]))}'
        f'{(" · Order #" + str(int(row["order_id"]))) if row.get("order_id") else ""}</span>'
        f'<strong>{html.escape(row["status"].title())}</strong></div>' for row in action_rows
    )
    active_id = str(int(active_order["id"])) if active_order else ""
    request_choices = [("staff_help", "💬 Call SNR staff")]
    if active_order:
        if active_order["status"] == "pending":
            request_choices.extend([("change_order", "✏️ Change my order"),
                                    ("cancel_order", "✖ Cancel my order")])
        if (active_order.get("fulfillment_type") or "delivery") == "delivery":
            request_choices.append(("driver_help", "📍 Driver cannot find me"))
    if len(request_choices) == 1:
        request_picker = '<input type="hidden" name="action_type" value="staff_help"><div class="request-kind">💬 Message SNR staff</div>'
    else:
        request_picker = '<fieldset class="request-choices"><legend class="sr-only">Choose what you need</legend>' + "".join(
            f'<label><input type="radio" name="action_type" value="{code}" {"checked" if index == 0 else ""}><span>{label}</span></label>'
            for index, (code, label) in enumerate(request_choices)
        ) + '</fieldset>'
    help_centre = f'''<details class="action-centre"><summary><strong>💬 Need help from SNR staff?</strong></summary><p class="muted">Send one clear request. It goes straight to the staff Live Actions inbox.</p><form class="live-action-form" method="post" action="/customer-action"><input type="hidden" name="service_request_key" value="{html.escape(service_token, quote=True)}"><input type="hidden" name="order_id" value="{active_id}">{request_picker}<input class="request-message" name="details" maxlength="250" placeholder="Optional: tell staff what you need"><button type="submit">Send to SNR Staff</button></form>{action_history}</details>'''
    vouchers = services.vouchers(customer["customer_key"])
    voucher_rows = "".join(
        f'''<div class="voucher {'voucher-used' if row['status'] != 'active' else ''}"><strong>{html.escape(row['title'])}</strong><br><code>{html.escape(row['voucher_code'])}</code> · {html.escape(row['status'].title())}</div>'''
        for row in vouchers[:8]) or '<p class="muted">No vouchers yet.</p>'
    voucher_wallet = f'''<section class="app-page quick-more-page"><h2 class="app-page-title">✨ My Existing Vouchers</h2><div class="drawer-body"><p>Previously issued vouchers stay valid. New City Run stickers now power your board.</p>{voucher_rows}</div></section>'''
    latest_action = action_rows[0] if action_rows else None
    latest_rewards = services.reward_requests(customer["customer_key"], 1)
    latest_reward = latest_rewards[0] if latest_rewards else None
    sync_data = (f' data-action-id="{int(latest_action["id"])}" data-action-status="{latest_action["status"]}"' if latest_action else '')
    sync_data += (f' data-reward-id="{int(latest_reward["id"])}" data-reward-status="{latest_reward["status"]}"' if latest_reward else '')
    return page(f'{customer["display_name"]} • SNR City Run', f'''<section class="card quick-hub" id="customer-app"{sync_data}>{top_order}{review_popup}<div id="service-toast" role="status"></div>
      <header class="quick-app-head"><div class="quick-brand"><img src="/snr-logo.png" alt="SNR Buns"><span><strong>SNR Buns</strong><small aria-label="CUSTOMER APP">SNR MEMBERS CLUB</small></span></div><span class="quick-service {service_class}">● {service_text}</span></header>
      <div class="neon-hero" role="img" aria-label="SNR Buns, burgers and neon city lights"><span class="hero-service {service_class}">{html.escape(service_text)}</span></div>
      <div class="quick-welcome"><div><div class="name">{html.escape(customer["display_name"])}’s <em>SNR Card</em></div></div><button type="button" class="membership-link" data-tab-target="visits">{membership["emoji"]} {html.escape(membership["name"])} ›</button></div>
      <div class="quick-content">{banner}
      <div class="app-view active" data-app-view="home" role="tabpanel">{recovery}
        <section class="store-card unified-card {'vip-black' if membership['name'] == 'SNR VIP' else ''}" aria-label="Your membership credit card">
          <div class="store-card-top"><span>SNR BUNS</span><b>{html.escape(membership["name"]).upper()}</b></div>
          <div class="card-hardware"><span class="card-chip" aria-hidden="true"></span><span class="card-contactless" aria-hidden="true">)))</span></div>
          <div class="card-balance"><strong>{points}</strong><span>CITY RUN STICKERS</span></div>
          <div class="card-number" aria-label="Member card ending {card_suffix}">••••&nbsp; ••••&nbsp; ••••&nbsp; {card_suffix}</div>
          <div class="store-card-bottom"><span class="card-holder"><small>CARDHOLDER</small><span>{html.escape(customer["display_name"])}</span></span><button class="card-reward" type="button" data-tab-target="city-run"><span><strong>{reward_title}</strong><small>Open City Run</small></span><span class="reward-arrow" aria-hidden="true">↗</span></button><span class="card-network" aria-label="SNR Elite membership card"><small>MEMBER</small>SNR <i>ELITE</i></span></div>
          <div class="member-progress" role="progressbar" aria-label="City Run collection progress" aria-valuemin="0" aria-valuemax="38" aria-valuenow="{progress}"><span style="width:{round(progress / 38 * 100)}%"></span></div>
        </section>
        <div class="member-actions"><button type="button" data-tab-target="order" data-order-mode="pickup"><span>🛍️ Click &amp; Collect</span><small>Order ahead and collect</small></button><button type="button" data-tab-target="order" data-order-mode="delivery" {'disabled aria-disabled="true"' if not drivers or service["mode"] in ("closed", "pickup_only", "delivery_paused") else ''}><span>🛵 Delivery</span><small>{'Currently unavailable' if not drivers or service["mode"] in ("closed", "pickup_only", "delivery_paused") else 'SNR Buns to your door'}</small></button></div>
        <button class="neon-reward-banner" type="button" data-tab-target="city-run"><span aria-hidden="true">🏁</span><strong>{city_unique}<small>OF 38 FOUND</small></strong><span>SNR CITY RUN<small>{str(city_reveals) + ' reveal(s) ready' if city_reveals else 'Every sticker unlocks a business reveal'}</small></span><b>›</b></button>
        <div class="quick-actions"><button type="button" class="quick-action" data-tab-target="raffle"><span>♧</span><strong>Raffle ›</strong></button><button type="button" class="quick-action" data-tab-target="order"><span>▤</span><strong>My orders ›</strong></button><button type="button" class="quick-action" data-tab-target="visits"><span>▥</span><strong>Visits ›</strong></button></div>{leaderboard}
        <div class="order-sync-anchor" hidden>{quick_order}</div>{help_centre}
      </div>
      <div class="app-view" data-app-view="order" role="tabpanel">{delivery_section(customer, orders, shifts, services, order_token)}</div>
      <div class="app-view" data-app-view="raffle" role="tabpanel">{raffle_section(customer, raffles, raffle_token)}</div>
      <div class="app-view" data-app-view="city-run" role="tabpanel">{city_run_section(customer, city_run, city_run_token)}</div>
      <div class="app-view" data-app-view="visits" role="tabpanel">
        <div class="quick-metrics"><div><small>MEMBERSHIP</small><strong>{membership["emoji"]} {html.escape(membership["name"])}</strong></div><div><small>Golden tickets</small><strong>🎟️ {int(customer["golden_tickets"])}</strong></div><div><small>VISITS</small><strong>🍔 {int(customer["lifetime_sales"])}</strong></div></div>
        <div class="quick-jackpot"><strong>🎟️ £5,000 Golden Ticket Jackpot</strong><div>{jackpot}</div></div>
      <div class="grid">{vip}</div>{membership_gallery(membership["name"])}{voucher_wallet}{birthday_box}<section class="app-page quick-more-page" id="history"><h2 class="app-page-title">📋 My Recent Visits</h2><div class="drawer-body">{recent}</div></section><form method="post" action="/logout"><input type="hidden" name="logout" value="1"><button class="secondary" type="submit">Log Out</button></form></div>
      </div>
      <nav class="app-tabs" aria-label="Customer account pages" role="tablist">
        <button class="app-tab" type="button" role="tab" aria-selected="true" data-tab-target="home"><span class="tab-icon">🏠</span>Home</button>
        <button class="app-tab" type="button" role="tab" aria-selected="false" data-tab-target="order"><span class="tab-icon">🍔</span>Order</button>
        <button class="app-tab" type="button" role="tab" aria-selected="false" data-tab-target="city-run"><span class="tab-icon">🏁</span>City Run</button>
        <button class="app-tab" type="button" role="tab" aria-selected="false" data-tab-target="visits"><span class="tab-icon">☰</span>More</button>
      </nav>
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
    limiter, claims, orders, accounts, shifts, raffles, services, city_run = (
        Limiter(), ClaimStore(db), DeliveryStore(db), Accounts(db), StaffShifts(db),
        RaffleStore(db), CustomerServices(db), CityRunStore(db)
    )
    # Physical card packs are retired. This is idempotent and refunds only
    # Legacy requests whose sticker credits had already been reserved.
    claims.retire_pending()
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
                self.send_html(401, login_page(db.customer_names(), "Please log in to open your SNR account."))
                return
            self.send_html(200, customer_page(
                customer, claims, orders, shifts, accounts, raffles, services, city_run,
                make_form_token(owner), make_form_token(owner), make_form_token(owner),
                make_form_token(owner), make_form_token(owner), make_form_token(owner)
            ))

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ("/snr-logo.png", "/snr-logo.jpg"):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(LOGO_IMAGE)))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(LOGO_IMAGE)
            elif path == "/neon-design.png":
                data = Path(__file__).with_name("neon-design.png").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(data)
            elif path == "/city-run-brand.png":
                data = Path(__file__).with_name("city-run-brand.png").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif path in ("/city-run-board.jpg", "/city-run-board-v2.png"):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(CITY_RUN_BOARD_IMAGE)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(CITY_RUN_BOARD_IMAGE)
            elif path.startswith("/city-sticker/") and path.endswith(".jpg"):
                business_key = path.removeprefix("/city-sticker/").removesuffix(".jpg")
                image_path = CITY_STICKER_IMAGES.get(business_key)
                if image_path is None or not image_path.exists():
                    self.send_html(404, page("Not found", '<section class="card"><h1>Sticker artwork not found.</h1></section>'))
                    return
                data = image_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)
            elif path.startswith("/city-art/") and path.endswith(".svg"):
                business_key = path.removeprefix("/city-art/").removesuffix(".svg")
                business = next((b for b in BUSINESSES if b['key'] == business_key), None)
                data = sticker_svg(business_key, business['name']) if business else None
                if data is None:
                    self.send_html(404, page("Not found", '<section class="card"><h1>Artwork not found.</h1></section>'))
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)
            elif path == "/delivery.js":
                data = b'''document.addEventListener("DOMContentLoaded",()=>{
const q=[...document.querySelectorAll(".deal-qty")],o=document.getElementById("delivery-subtotal"),z=document.getElementById("delivery-total"),f=document.querySelector(".delivery-form"),mode=document.getElementById("fulfillment-type"),loc=document.getElementById("delivery-location"),feeLabel=document.getElementById("order-fee-label"),fee=parseInt(f?.dataset.deliveryFee||"0",10);
const currentFee=()=>mode?.value!=="delivery"?0:fee;const total=()=>{let t=0;q.forEach(x=>t+=(parseInt(x.value||"0",10)||0)*parseInt(x.dataset.price,10));if(o)o.textContent="\\u00a3"+t.toLocaleString("en-GB");if(z)z.textContent="\\u00a3"+(t+currentFee()).toLocaleString("en-GB")};const syncMode=()=>{const pickup=mode?.value!=="delivery";if(loc){loc.hidden=pickup;loc.required=!pickup}if(feeLabel)feeLabel.textContent=pickup?(mode?.value==="instore"?"In-store charge: FREE":"Pickup charge: FREE"):"Membership delivery: \\u00a3"+fee.toLocaleString("en-GB");total()};q.forEach(x=>x.addEventListener("input",total));mode?.addEventListener("change",syncMode);syncMode();
document.querySelectorAll(".reorder-button").forEach(b=>b.addEventListener("click",()=>{try{const wanted=JSON.parse(b.dataset.reorder||"{}");q.forEach(x=>x.value=String(wanted[x.name.replace("qty_","")]||0));if(mode){const option=[...mode.options].find(x=>x.value===b.dataset.mode&&!x.disabled);mode.value=option?option.value:"pickup"}syncMode();const m=document.getElementById("reorder-message");if(m){m.textContent="Your previous basket is ready below. Check the amounts and press Place Order when ready.";m.classList.add("show")}f?.scrollIntoView({behavior:"smooth",block:"start"})}catch(e){}}));
const send=f?.querySelector('button[type="submit"]');const label=()=>{if(send)send.textContent=mode?.value==="instore"?"Send to Counter - Pay In Store":"Place Order"};mode?.addEventListener("change",label);label();
const tracker=document.querySelector("[data-order-id]");if(!tracker)return;let current=tracker.dataset.orderStatus;
const messages={accepted:"Your order has been accepted!",on_way:"Your driver is on the way!",arrived:"Your SNR Buns driver has arrived and is waiting outside!",ready_for_pickup:"Your order is ready for collection at SNR Buns!",paid:"Your order is complete. Your Golden Tickets were issued and entered automatically!",cancelled:"Your order was cancelled.",wasted_journey:"A \\u00a3500 Wasted Journey fee has been added to your account. Please contact SNR staff."};
setInterval(async()=>{try{const r=await fetch("/order-status",{cache:"no-store"});if(!r.ok)return;const d=await r.json();const eta=document.getElementById("live-estimate");if(eta&&d.eta_text)eta.innerHTML="<strong>Live estimate: "+d.eta_text+"</strong>";if(d.id==tracker.dataset.orderId&&d.status!==current){current=d.status;const toast=document.getElementById("status-toast");let message=(d.status==="paid"&&d.jackpot_won)?"WINNER! One of your automatic Golden Tickets won the \\u00a35,000 jackpot! Speak to SNR staff now.":(messages[d.status]||"Your order status has changed.");if(d.driver&&!["cancelled","wasted_journey"].includes(d.status))message+=(d.fulfillment_type!=="delivery"?" Handling staff: ":" Driver: ")+d.driver;if(toast){toast.textContent=message;toast.classList.add("show")}document.title="SNR UPDATE: "+message;if(navigator.vibrate)navigator.vibrate([200,100,200]);if(d.status==="paid"){location.href="/account#order"}else{setTimeout(()=>location.reload(),1800)}}}catch(e){}},2000);
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
tabs.forEach(t=>t.addEventListener("click",()=>{show(t.dataset.tabTarget);if(t.dataset.orderMode){const m=document.getElementById("fulfillment-type");if(m&&[...m.options].some(o=>o.value===t.dataset.orderMode&&!o.disabled)){m.value=t.dataset.orderMode;m.dispatchEvent(new Event("change"))}}}));const wanted=location.hash.slice(1);if(names.has(wanted))show(wanted);
document.getElementById("rating-later")?.addEventListener("click",()=>document.getElementById("rating-popup")?.remove());
const reason=document.getElementById("low-rating-reason"),reasonSelect=reason?.querySelector("select");document.querySelectorAll('input[name="rating"]').forEach(x=>x.addEventListener("change",()=>{const low=parseInt(x.value,10)<=2;reason?.classList.toggle("show",low);if(reasonSelect)reasonSelect.required=low}));
const banner=document.getElementById("customer-announcement"),bannerLabel=document.getElementById("announcement-label"),bannerMessage=document.getElementById("announcement-message"),bannerLabels={info:"SNR Update",promo:"Special Offer",urgent:"Important Notice"};
const refreshBanner=async()=>{try{const r=await fetch("/announcement-status",{cache:"no-store"});if(!r.ok)return;const d=await r.json();if(!banner)return;banner.hidden=!d.active;if(d.active){const style=bannerLabels[d.style]?d.style:"info";banner.className="customer-banner banner-"+style;if(bannerLabel)bannerLabel.textContent=bannerLabels[style];if(bannerMessage)bannerMessage.textContent=d.message||""}}catch(e){}};setInterval(refreshBanner,5000);
const quickOrder=document.querySelector("[data-quick-order-id]");if(quickOrder)setInterval(async()=>{try{const r=await fetch("/order-status",{cache:"no-store"});if(!r.ok)return;const d=await r.json();if(String(d.id)!==quickOrder.dataset.quickOrderId||d.status!==quickOrder.dataset.quickOrderStatus){const bar=document.querySelector(".top-order");if(bar&&d.status==="paid"&&String(d.id)===quickOrder.dataset.quickOrderId){bar.querySelector("strong").textContent="Order #"+d.id+" - Complete";const track=bar.querySelector("[role=progressbar]");track.setAttribute("aria-valuenow",track.getAttribute("aria-valuemax"));track.firstElementChild.style.width="100%";bar.querySelectorAll(".top-order-steps span").forEach(s=>s.classList.add("done"));setTimeout(()=>location.reload(),1500)}else location.reload()}}catch(e){}},3000);
document.querySelectorAll(".live-action-form").forEach(form=>form.addEventListener("submit",()=>{const button=form.querySelector('button[type="submit"]');if(button){button.disabled=true;button.textContent="Sending to staff..."}}));
setInterval(async()=>{try{const r=await fetch("/service-status",{cache:"no-store"});if(!r.ok)return;const d=await r.json(),toast=document.getElementById("service-toast");const actionChanged=d.action_id&&String(d.action_id)===app.dataset.actionId&&d.action_status!==app.dataset.actionStatus;const rewardChanged=d.reward_id&&String(d.reward_id)===app.dataset.rewardId&&d.reward_status!==app.dataset.rewardStatus;if(actionChanged||rewardChanged){if(toast){toast.textContent=actionChanged?(d.action_message||"SNR staff updated your request."):(d.reward_message||"Your reward request was updated.");toast.classList.add("show")}if(navigator.vibrate)navigator.vibrate([150,80,150]);setTimeout(()=>location.reload(),2200)}}catch(e){}},3000);
});'''
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            elif path == "/raffle.js":
                data = b'''document.addEventListener("DOMContentLoaded",()=>{const f=document.querySelector(".raffle-form"),boxes=[...document.querySelectorAll('.raffle-form input[type="checkbox"]')],out=document.getElementById("raffle-choice-total");if(f){const max=parseInt(f.dataset.max||"10",10),price=parseInt(f.dataset.price||"0",10),sync=()=>{const picked=boxes.filter(x=>x.checked);boxes.forEach(x=>x.disabled=!x.checked&&picked.length>=max);if(out)out.textContent=picked.length?picked.length+" number(s) = \\u00a3"+(picked.length*price).toLocaleString("en-GB"):"Choose up to "+max+" number(s)"};boxes.forEach(x=>x.addEventListener("change",sync));f.addEventListener("submit",e=>{if(!boxes.some(x=>x.checked)){e.preventDefault();alert("Choose at least one available raffle number.")}});sync()}const board=document.querySelector("[data-raffle-id]");if(board)setInterval(async()=>{try{const r=await fetch("/raffle-status",{cache:"no-store"});if(!r.ok)return;const d=await r.json();if(String(d.id)!==board.dataset.raffleId||d.status!==board.dataset.raffleStatus||String(d.confirmed)!==board.dataset.raffleConfirmed)location.reload()}catch(e){}},5000)});'''
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            elif path == "/announcement-status":
                if not self.owner():
                    self.send_json(401, {"error": "login_required"})
                    return
                announcement = orders.customer_announcement()
                self.send_json(200, {"active": bool(announcement.get("active")),
                                     "style": announcement.get("style") or "info",
                                     "message": announcement.get("message") or ""})
            elif path == "/raffle-status":
                if not self.owner():
                    self.send_json(401, {"error": "login_required"})
                    return
                raffle = raffles.current()
                self.send_json(200, ({"id": raffle["id"], "status": raffle["status"],
                                      "confirmed": raffle["confirmed_numbers"],
                                      "pending": raffle["pending_numbers"]}
                                     if raffle else {"id": None, "status": "none", "confirmed": 0, "pending": 0}))
            elif path == "/order-status":
                owner = self.owner()
                if not owner:
                    self.send_json(401, {"error": "login_required"})
                    return
                rows = orders.summary(owner, 1)
                row = rows[0] if rows else None
                outcome = orders.ticket_result(row["id"]) if row and row["status"] == "paid" else {"tickets": 0, "jackpot_won": False}
                estimate = orders.order_estimate(row["id"]) if row else {"queue_position": 0, "eta_text": ""}
                self.send_json(200, ({"id": str(row["id"]), "status": row["status"],
                                      "driver": row.get("assigned_driver_name") or "",
                                      "fulfillment_type": row.get("fulfillment_type") or "delivery",
                                      "tickets": outcome["tickets"], "jackpot_won": outcome["jackpot_won"],
                                      "queue_position": estimate["queue_position"], "eta_text": estimate["eta_text"]}
                                     if row else {"id": None, "status": "none", "driver": ""}))
            elif path == "/service-status":
                owner = self.owner()
                if not owner:
                    self.send_json(401, {"error": "login_required"})
                    return
                action_rows = services.customer_actions(owner, 1)
                action = action_rows[0] if action_rows else None
                rewards = services.reward_requests(owner, 1)
                reward = rewards[0] if rewards else None
                action_message = ""
                if action:
                    action_message = action.get("staff_response") or ("SNR staff declined your request." if action["status"] == "declined" else "Your request is waiting for staff.")
                self.send_json(200, {"action_id": action["id"] if action else None,
                    "action_status": action["status"] if action else "none", "action_message": action_message,
                    "reward_id": reward["id"] if reward else None,
                    "reward_status": reward["status"] if reward else "none",
                    "reward_message": "Your reward request has been updated."})
            elif path == "/health":
                data = json.dumps({"status": "ok"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif path in ("/account", "/card"):
                self.show_account()
            elif path == "/staff-reset":
                query = parse_qs(parsed.query)
                name = query.get("name", [""])[0][:60]
                code = query.get("code", [""])[0][:100]
                if not name or not code:
                    self.send_html(400, login_page(db.customer_names(), "That password-reset link is incomplete."))
                else:
                    self.send_html(200, staff_reset_page(name, code))
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
                    self.send_html(200, page("Account created", f'''<section class="card"><div class="label">Account created</div><h1>You’re ready to go!</h1><p>The SNR account for <strong>{html.escape(result["customer_name"])}</strong> is active now.</p><div class="notice">You can log in immediately using the password you just chose. SNR staff have been notified, but no approval is required.</div><a class="back" href="/">Log in to my account</a></section>'''))
                elif path == "/reset-password":
                    if data.get("password", "") != data.get("confirm", ""):
                        raise ValueError("The two passwords do not match.")
                    accounts.reset_with_answer(
                        data.get("name", ""), data.get("security_question", ""),
                        data.get("security_answer", ""), data.get("password", ""),
                    )
                    self.send_html(200, page("Password reset", '''<section class="card"><div class="label">Password updated</div><h1>Your new password is ready</h1><p>All older website sessions were signed out for your protection.</p><a class="back" href="/">Log in</a></section>'''))
                elif path == "/staff-reset":
                    if data.get("password", "") != data.get("confirm", ""):
                        raise ValueError("The two passwords do not match.")
                    accounts.set_password(
                        data.get("name", ""), data.get("code", ""), data.get("password", ""))
                    self.send_html(200, page("Password reset", '''<section class="card"><div class="label">Password updated</div><h1>Your new password is ready</h1><p>The private reset link has now expired and all older sessions remain signed out.</p><a class="back" href="/">Log in to my card</a></section>'''))
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
                    raise ValueError(
                        "Trading-card packs have retired. Your sticker balance is protected and now powers SNR City Run."
                    )
                elif path == "/city-run-reveal":
                    owner = self.owner()
                    if not owner:
                        raise ValueError("Your login has expired. Please log in again.")
                    key = data.get("city_run_request_key", "")
                    if not valid_form_token(owner, key):
                        raise ValueError("This City Run reveal has expired. Refresh your account and try again.")
                    result = city_run.reveal_one(owner, key)
                    duplicate = bool(result["duplicate"])
                    message = ("Duplicate found — it has been added to your duplicate total."
                               if duplicate else "New business collected and added to your board!")
                    art_key = html.escape(result["business_key"], quote=True)
                    self.send_html(200, page("City Run reveal", f'''<section class="card city-reveal-result"><div class="label">🏁 SNR CITY RUN · STICKER REVEALED</div><h1>{html.escape(result["business_name"])}</h1><div class="city-business"><img class="city-reveal-art" src="/city-art/{art_key}.svg?v=individual-art-2" alt="{html.escape(result["business_name"], quote=True)} sticker artwork"><span class="city-reveal-badge">{html.escape(str(result["rarity"]).replace("_", " ").title())}</span></div><p><strong>{html.escape(result["collection_name"])}</strong></p><div class="notice">{html.escape(message)} Your sticker is now placed on your City Run board.</div><p>{int(result["reveals_left"])} sticker reveal(s) remaining.</p><a class="back" href="/account#city-run">View my updated board</a></section>'''))
                elif path == "/city-run-claim":
                    owner = self.owner()
                    if not owner:
                        raise ValueError("Your login has expired. Please log in again.")
                    key = data.get("city_run_request_key", "")
                    if not valid_form_token(owner, key):
                        raise ValueError("This City Run claim has expired. Refresh your account and try again.")
                    result = city_run.request_reward(owner, data.get("reward_key", ""))
                    self.send_html(200, page("City Run reward requested", f'''<section class="card"><div class="label">🏁 CITY RUN CLAIM #{int(result["id"])}</div><h1>{html.escape(result["reward_name"])}</h1><p>Your verified collection claim has been sent to SNR staff.</p><div class="notice">Your collected businesses remain safely on your board. Staff will confirm the RP reward in Discord.</div><a class="back" href="/account#city-run">Back to my City Run board</a></section>'''))
                elif path == "/raffle-request":
                    owner = self.owner()
                    if not owner:
                        raise ValueError("Your login has expired. Please log in again.")
                    key = data.get("raffle_request_key", "")
                    if not valid_form_token(owner, key):
                        raise ValueError("This raffle form has expired. Refresh your account and try again.")
                    numbers = [int(name.split("_", 1)[1]) for name, value in data.items()
                               if name.startswith("number_") and value]
                    result = raffles.request(owner, numbers, key)
                    chosen = ", ".join(str(number) for number in result["numbers"])
                    self.send_html(200, page("Raffle numbers reserved", f'''<section class="card"><div class="label">🎟️ RAFFLE REQUEST #{int(result["id"])}</div><h1>Your numbers are reserved</h1><p><strong>{chosen}</strong></p><p>Total to pay: <strong>£{int(result["total_price"]):,}</strong></p><div class="notice">Please pay SNR staff in-store. Your numbers will enter the draw once staff confirm your payment.</div><a class="back" href="/account#raffle">Back to the raffle</a></section>'''))
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
                        data.get("discount_code", ""), fulfillment_type, data.get("voucher_code", ""))
                    if fulfillment_type == "instore":
                        self.redirect("/account#order")
                        return
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
                        data.get("comment", ""), data.get("issue_category", ""))
                    stars = "★" * int(result["rating"]) + "☆" * (5 - int(result["rating"]))
                    subject = "pickup experience" if result["fulfillment_type"] == "pickup" else "driver"
                    self.send_html(200, page("Thanks for your rating", f'''<section class="card"><div class="label">Review received</div><h1>Thank you! <span class="stars">{stars}</span></h1><p>Your {subject} rating for <strong>{html.escape(result["staff_name"])}</strong> has been recorded.</p><div class="notice">SNR staff have been notified. Each completed order can only be rated once.</div><a class="back" href="/account#order">Back to my orders</a></section>'''))
                elif path == "/support":
                    owner = self.owner()
                    if not owner:
                        raise ValueError("Your login has expired. Please log in again.")
                    if not valid_form_token(owner, data.get("support_request_key", "")):
                        raise ValueError("This help form has expired. Refresh your account and try again.")
                    result = orders.create_support_authenticated(
                        owner, data.get("order_id", ""), data.get("issue_type", ""),
                        data.get("details", ""))
                    self.send_html(200, page("Problem sent", f'''<section class="card"><div class="label">Order #{int(result["order_id"])} help</div><h1>We’ve told SNR staff</h1><div class="notice">Your problem is safely logged. Staff will review it and mark it resolved after helping you.</div><a class="back" href="/account#order">Back to my orders</a></section>'''))
                elif path == "/customer-action":
                    owner = self.owner()
                    if not owner:
                        raise ValueError("Your login has expired. Please log in again.")
                    key = data.get("service_request_key", "")
                    if not valid_form_token(owner, key):
                        raise ValueError("This request has expired. Refresh your account and try again.")
                    result = services.create_action(owner, data.get("action_type", ""),
                        data.get("order_id", "") or None, data.get("details", ""), key)
                    self.send_html(200, page("Request sent", f'''<section class="card"><div class="label">Live Action #{int(result["id"])}</div><h1>SNR staff have been notified</h1><div class="notice">Your request is in the staff inbox. Its result will appear on your account.</div><a class="back" href="/account#home">Back to my account</a></section>'''))
                elif path == "/reward-choice":
                    owner = self.owner()
                    if not owner:
                        raise ValueError("Your login has expired. Please log in again.")
                    key = data.get("service_request_key", "")
                    if not valid_form_token(owner, key):
                        raise ValueError("This reward request has expired. Refresh your account and try again.")
                    result = services.request_reward(owner, data.get("reward_code", ""), key)
                    self.send_html(200, page("Reward requested", f'''<section class="card"><div class="label">Reward Request #{int(result["id"])}</div><h1>{html.escape(result["reward_name"])}</h1><div class="notice">Staff have been notified. Your stickers are only deducted when they approve the reward.</div><a class="back" href="/account#rewards">Back to rewards</a></section>'''))
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
