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
from snr_core import DEALS, VIP_LEVELS, SNRDatabase, normalize_name

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
.app-tabs{position:sticky;top:8px;z-index:8;display:grid;grid-template-columns:repeat(5,1fr);gap:7px;margin:0 0 16px;padding:7px;background:#260607e8;border:1px solid #ffda4e80;border-radius:16px;box-shadow:0 8px 28px #16000099;backdrop-filter:blur(10px)}.app-tab{min-width:0;padding:11px 5px;border-radius:11px;background:#5f100d;color:#fff4db;border:1px solid #ffda4e55;box-shadow:none;font-size:12px}.app-tab[aria-selected="true"]{background:linear-gradient(135deg,#fff05b,#ffbf18);color:#60100b;border-color:#fff08b}.app-tab .tab-icon{display:block;font-size:21px;line-height:1.1}.app-view{display:block}.app-ready .app-view{display:none}.app-ready .app-view.active{display:block;animation:page-in .16s ease-out}@keyframes page-in{from{opacity:.4;transform:translateY(5px)}to{opacity:1;transform:none}}.app-page{border:2px solid #ffb52b;border-radius:18px;background:#290808;overflow:hidden}.app-page.delivery{margin-top:0;padding:0}.app-page-title{margin:0;padding:16px 18px;color:var(--gold);font-size:22px;font-weight:950;border-bottom:1px solid #ffda4e33}.section-drawer{margin-top:18px;border:2px solid #ffb52b;border-radius:18px;background:#290808;overflow:hidden}.section-drawer>summary{padding:18px;cursor:pointer;list-style:none;color:var(--gold);font-size:20px;font-weight:950}.section-drawer>summary::-webkit-details-marker{display:none}.section-drawer>summary:after{content:'+';float:right;font-size:25px}.section-drawer[open]>summary:after{content:'−'}.drawer-body{padding:18px 20px 22px}.section-drawer.delivery{padding:0}.section-drawer .history{margin-top:18px}.compact-info{margin-top:16px}.compact-info summary{cursor:pointer;color:var(--gold);font-weight:900}.account-head{display:flex;align-items:end;justify-content:space-between;gap:12px;margin-bottom:12px}.account-head .name{margin:2px 0}.page-hint{margin:0;color:var(--muted)}.review-box{margin-top:10px;padding:13px;border:1px solid #ffda4e66;border-radius:14px;background:#3c0a0a}.stars{color:var(--gold);font-size:20px;letter-spacing:2px}.reviewed{color:#9dffab;font-weight:850}.rating-overlay{position:fixed;inset:0;z-index:100;display:grid;place-items:center;padding:16px;background:#130000e8;backdrop-filter:blur(8px)}.rating-popup{width:min(560px,100%);max-height:94vh;overflow:auto;padding:24px;background:linear-gradient(145deg,#8b1710,#310708);border:3px solid var(--gold);border-radius:24px;box-shadow:0 0 45px #ffbd2490;text-align:center}.rating-popup h2{font-size:30px;line-height:1.1;margin:7px 0}.rating-popup form{display:block}.rating-options{display:grid;grid-template-columns:repeat(5,1fr);gap:7px;margin:18px 0}.star-choice{position:relative;cursor:pointer}.star-choice input{position:absolute;opacity:0;pointer-events:none}.star-choice span{display:block;padding:12px 3px;border:2px solid #ffda4e70;border-radius:12px;background:#4b0a08;color:#ffe53b;font-size:18px;font-weight:950}.star-choice input:checked+span{background:#ffe53b;color:#5b0b07;border-color:#fff;transform:scale(1.06);box-shadow:0 0 16px #ffe53b99}.rating-popup input[type="text"]{margin-bottom:12px}.rating-popup .later{display:block;width:100%;margin-top:12px;padding:10px;background:transparent;color:#ffeab3;border:0;box-shadow:none;text-decoration:underline}
.raffle-hero{padding:17px;border-radius:16px;background:linear-gradient(135deg,#ffdf36,#ff8a13);color:#4f0906;box-shadow:0 8px 26px #ff9b2550}.raffle-hero .label{color:#5a0905}.raffle-hero h3{font-size:28px;margin:4px 0}.raffle-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:13px 0}.raffle-stats div{text-align:center;padding:10px 5px;border-radius:12px;background:#3b0909;border:1px solid #ffda4e55}.raffle-numbers{display:grid;grid-template-columns:repeat(10,1fr);gap:6px;margin:16px 0}.raffle-number{position:relative}.raffle-number input{position:absolute;opacity:0;pointer-events:none}.raffle-number span{display:grid;place-items:center;aspect-ratio:1;border-radius:8px;background:#68120e;border:1px solid #ffda4e77;font-size:13px;font-weight:900}.raffle-number input:checked+span{background:var(--gold);color:#5b0b07;border-color:#fff;box-shadow:0 0 12px #ffe53b99}.raffle-number.taken span{background:#250707;color:#8f6560;border-color:#52231f;text-decoration:line-through}.raffle-number.mine-pending span{background:#915512;color:white;text-decoration:none}.raffle-number.mine-confirmed span{background:#178447;color:white;text-decoration:none}.raffle-legend{display:flex;flex-wrap:wrap;gap:8px;font-size:12px}.raffle-legend span{padding:5px 8px;border:1px solid #ffda4e55;border-radius:99px}.raffle-winner{text-align:center;padding:22px;border:3px solid var(--gold);border-radius:18px;background:linear-gradient(135deg,#8a170f,#3a0808);box-shadow:0 0 30px #ffcf3160}.raffle-winner strong{display:block;font-size:28px;color:var(--gold)}
.customer-banner{padding:15px 17px;margin:0 0 14px;border:2px solid #67b8ff;border-radius:16px;background:linear-gradient(135deg,#153b6b,#0b1d39);color:#fff;box-shadow:0 6px 20px #0007}.customer-banner strong{display:block;margin-bottom:4px;color:#a9dcff;text-transform:uppercase;letter-spacing:.7px}.customer-banner.banner-promo{border-color:#ffe04b;background:linear-gradient(135deg,#a94708,#671207)}.customer-banner.banner-promo strong{color:#fff36f}.customer-banner.banner-urgent{border-color:#ff7b70;background:linear-gradient(135deg,#aa1414,#4a0505);box-shadow:0 0 24px #ff312f55}.customer-banner.banner-urgent strong{color:#fff36f}.customer-banner[hidden]{display:none}.service-banner{padding:14px 16px;margin:0 0 14px;border-radius:14px;background:#4b0a08;border:2px solid var(--gold);font-weight:900}.service-busy{background:#7b3e08}.service-closed{background:#6b0b0b;border-color:#ff7468}.eta-card{margin:12px 0;padding:14px;border-radius:14px;background:#160505;border:1px solid #ffe53b;color:#ffe53b;font-size:18px;font-weight:950}.problem-form{display:flex;flex-direction:column;gap:8px;margin-top:10px}.problem-form input,.problem-form select{padding:11px}.low-rating-reason{display:none;margin-bottom:12px}.low-rating-reason.show{display:block}
@media(max-width:560px){form,.location-row{flex-direction:column}button{width:100%}.wrap{width:min(100% - 18px,760px);padding-top:10px}.brand{margin-bottom:9px}.logo-frame{width:125px;border-radius:13px;margin-bottom:7px}.tag{padding:4px 12px;font-size:10px}.card{padding:14px;border-radius:19px}.account-head{margin-bottom:8px}.account-head .label{font-size:11px}.account-head .name{font-size:25px}.grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.stat{padding:12px}.stat .label{font-size:11px;letter-spacing:.3px}.num{font-size:22px}.wide,.vip-card{grid-column:1/-1}.vip-benefits{font-size:14px}.deal-list{grid-template-columns:1fr}.app-tabs{top:5px;margin-bottom:12px;gap:4px;padding:5px}.app-tab{padding:8px 1px;font-size:9px}.app-tab .tab-icon{font-size:17px}.app-page-title{font-size:19px;padding:13px 14px}.drawer-body{padding:13px 14px 17px}.delivery{margin-top:0}.deal-box{padding:12px}.deal-box .price{font-size:20px}input,select,textarea{padding:13px}.subtotal{font-size:20px}.notice{padding:14px}.ownership{display:none}.order-progress{gap:2px}.order-step{font-size:9px}.order-step:before{width:25px;height:25px}.reorder-row{align-items:flex-start;flex-direction:column}.reorder-row button{width:auto}.raffle-numbers{grid-template-columns:repeat(10,1fr);gap:3px}.raffle-number span{font-size:10px;border-radius:5px}.raffle-stats{font-size:12px}}
@media(max-width:560px){.rating-popup{padding:19px 14px}.rating-popup h2{font-size:25px}.star-choice span{font-size:15px;padding:11px 1px}}
.store-card{grid-column:1/-1;position:relative;isolation:isolate;overflow:hidden;width:100%;max-width:540px;justify-self:center;aspect-ratio:1.586;min-height:224px;display:flex;flex-direction:column;justify-content:space-between;gap:10px;padding:clamp(17px,4vw,28px);border:1px solid #ffe9a3;border-radius:22px;text-align:left;color:#fff7dd;background:radial-gradient(ellipse at 95% 0%,#ffce4266,transparent 58%),linear-gradient(125deg,#260909 0%,#9f101b 35%,#eb2630 60%,#61091a 100%);box-shadow:0 12px 30px #0c000088,inset 0 1px 0 #fff6,inset 0 -2px 0 #490510;transition:transform .2s,box-shadow .2s}.store-card:before{content:'';position:absolute;z-index:-1;inset:-60%;background:repeating-radial-gradient(ellipse at 85% 65%,transparent 0 22px,#ffd98a25 23px 24px,transparent 25px 43px);transform:rotate(-24deg);pointer-events:none}.store-card:after{content:'';position:absolute;z-index:-1;inset:0;background:linear-gradient(115deg,transparent 25%,#fff4 26%,#fff1 35%,transparent 52%);pointer-events:none}.store-card:hover{transform:translateY(-2px);box-shadow:0 16px 35px #090000aa,0 0 22px #ffba332a;filter:none}.store-card strong{font-size:clamp(20px,5vw,29px);line-height:1.1;text-shadow:0 2px 2px #35030a}.store-card>span{font-size:12px}.store-card .store-card-top{display:flex;justify-content:space-between;align-items:center;gap:10px;font-size:12px;letter-spacing:1.5px;color:#ffe6a4}.store-card-top b{font-size:10px;background:#24060980;padding:5px 9px;border:1px solid #ffd27566;border-radius:99px;letter-spacing:.6px}.store-card .card-hardware{display:flex;align-items:center;gap:13px}.card-chip{display:block;width:43px;height:33px;border:1px solid #b58523;border-radius:7px;background:linear-gradient(90deg,transparent 32%,#8a671c 33% 35%,transparent 36% 65%,#8a671c 66% 68%,transparent 69%),linear-gradient(0deg,transparent 31%,#8a671c 32% 35%,transparent 36% 65%,#8a671c 66% 69%,transparent 70%),linear-gradient(135deg,#f9eab1,#c9973c,#fff1b7);box-shadow:inset 0 1px 2px #fff9,0 1px 3px #240504}.card-contactless{font-size:25px;color:#ffe4a7;transform:rotate(90deg);display:block}.store-card .card-title{display:flex;flex-direction:column;gap:5px}.store-card small{font-size:10px;color:#ffe9cf}.store-card .store-card-bottom{display:flex;justify-content:space-between;align-items:end;gap:10px;font-size:12px}.card-holder{min-width:0;text-transform:uppercase;letter-spacing:1px;text-shadow:0 1px 1px #320107}.card-holder small{display:block;font-size:8px;letter-spacing:1.8px;margin-bottom:3px}.card-holder span{display:block;overflow-wrap:anywhere}.store-card-bottom b{flex-shrink:0;font-size:9px;color:#ffe0a0;letter-spacing:.5px}.store-card:focus-visible{outline:3px solid #fff;outline-offset:4px}@media(prefers-reduced-motion:reduce){.store-card{transition:none}.store-card:hover{transform:none}}.customer-shell{padding-bottom:82px}.customer-shell .brand{display:none}.customer-shell .wrap{width:min(760px,100%);padding-top:10px}.quick-hub{padding:0;overflow:hidden;border-top-width:2px;background:#260707}.quick-app-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:13px 16px;background:#1d0808;border-bottom:1px solid #ffda4e33}.quick-brand{display:flex;align-items:center;gap:10px}.quick-brand img{width:46px;height:46px;object-fit:cover;border-radius:13px;border:1px solid #ffda4e88}.quick-brand span,.quick-brand strong,.quick-brand small{display:block}.quick-brand strong{font-size:18px}.quick-brand small{color:#e2b9ab;font-size:10px;letter-spacing:1px}.quick-service{flex:none;padding:6px 10px;border-radius:99px;font-size:11px;font-weight:900}.quick-open{background:#17492b;color:#9affb2}.quick-pickup,.quick-busy{background:#70430d;color:#ffe595}.quick-closed{background:#6e1511;color:#ffb2a8}.quick-welcome{display:flex;align-items:end;justify-content:space-between;gap:12px;padding:14px 18px 8px}.quick-welcome .name{margin:1px 0;font-size:30px}.quick-welcome p{flex:none;margin:0 0 4px;padding:5px 9px;border-radius:99px;background:#4a1210;color:#ffe7a0;font-size:12px;font-weight:900}.quick-content{padding:8px 16px 24px}.quick-loyalty{padding:17px;border-radius:20px;background:linear-gradient(135deg,#ffe35a,#f29320);color:#4f0d08;box-shadow:0 9px 25px #ff9e2044}.quick-loyalty-top{display:flex;justify-content:space-between;gap:10px;font-size:11px;font-weight:900}.quick-loyalty h2{margin:7px 0 3px;font-size:24px}.quick-loyalty p{margin:0;font-size:13px}.quick-progress{height:9px;margin-top:13px;border-radius:99px;background:#7c481c55;overflow:hidden}.quick-progress span{display:block;height:100%;border-radius:99px;background:#55110b}.quick-actions{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin:12px 0}.quick-action{min-height:82px;padding:12px;border:1px solid #ffda4e33;border-radius:16px;background:#49110f;color:#fff;text-align:left;box-shadow:none}.quick-action-main{grid-column:1/-1;background:linear-gradient(135deg,#a62117,#74130e)}.quick-action span,.quick-action strong,.quick-action small{display:block}.quick-action span{font-size:21px}.quick-action strong{margin-top:2px;font-size:15px}.quick-action small{color:#e6beb0;font-size:11px}.quick-section-title{display:flex;justify-content:space-between;align-items:center;margin:17px 2px 8px}.quick-section-title span{color:var(--gold);font-size:10px;font-weight:900;letter-spacing:.7px}.quick-order{display:grid;width:100%;grid-template-columns:48px 1fr auto;align-items:center;gap:11px;padding:12px;border:1px solid #ffda4e33;border-radius:16px;background:#3d0d0d;color:#fff;text-align:left;box-shadow:none}.quick-order-icon{display:grid;place-items:center;width:48px;height:48px;border-radius:13px;background:#681812;font-size:24px}.quick-order span small,.quick-order span strong{display:block}.quick-order span small{color:#dfb4a8;font-size:10px}.quick-order span strong{margin:2px 0;font-size:14px}.quick-order b{color:var(--gold);font-size:25px}.quick-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin:11px 0}.quick-metrics div{min-width:0;padding:10px 6px;border-radius:14px;background:#40100e;text-align:center}.quick-metrics small,.quick-metrics strong{display:block}.quick-metrics small{color:#d3a296;font-size:8px}.quick-metrics strong{margin-top:3px;font-size:14px;overflow-wrap:anywhere}.quick-jackpot{padding:13px;border-radius:15px;background:#ffcf35;color:#4c0c07;font-size:12px}.quick-jackpot>strong{display:block;margin-bottom:4px}.quick-jackpot details summary{color:#4c0c07}.quick-more-page{margin-top:14px}.quick-hub>.app-tabs{position:fixed;left:50%;bottom:8px;top:auto;transform:translateX(-50%);width:min(720px,calc(100vw - 18px));margin:0;z-index:50;background:#1d0808f5;border-color:#ffda4e55;box-shadow:0 10px 35px #000c}.quick-hub .app-tab{background:transparent;border:0;box-shadow:none;color:#d5aaa0}.quick-hub .app-tab[aria-selected="true"]{background:#ffcf35;color:#53100a}.quick-hub .app-view{padding-bottom:4px}
@media(max-width:560px){.customer-shell .wrap{width:100%;padding:0}.quick-hub{min-height:100vh;border:0;border-radius:0}.quick-app-head{padding:10px 12px}.quick-brand img{width:41px;height:41px}.quick-welcome{padding:12px 13px 6px}.quick-welcome .name{font-size:25px}.quick-content{padding:7px 10px 24px}.quick-loyalty{padding:14px}.quick-loyalty h2{font-size:21px}.quick-actions{gap:7px}.quick-action{min-height:75px;padding:10px}.quick-order{grid-template-columns:43px 1fr auto;padding:10px}.quick-order-icon{width:43px;height:43px}.quick-metrics{gap:5px}.quick-metrics div{padding:8px 3px}.quick-metrics strong{font-size:12px}.quick-hub>.app-tabs{bottom:5px;width:calc(100vw - 10px);border-radius:15px}.quick-hub .app-tab{width:auto}.customer-shell footer{display:none}}
.unified-card{max-width:600px;margin:8px auto 15px;aspect-ratio:auto;min-height:275px;gap:15px}.unified-card:hover{transform:none}.member-identity{display:flex;align-items:center;gap:14px}.member-identity .card-holder{font-size:14px;font-weight:700}.card-reward{display:flex;justify-content:space-between;align-items:center;gap:10px;width:100%;padding:0;background:transparent;color:#fff6db;text-align:left;box-shadow:none}.card-reward strong{display:block;font-size:clamp(19px,4.8vw,26px)}.card-reward small{display:block;margin-top:5px}.reward-arrow{font-size:25px;color:#ffe4a3}.member-progress{height:5px;overflow:hidden;border-radius:8px;background:#ffffff26;margin-top:-5px}.member-progress>span{display:block;height:100%;background:linear-gradient(90deg,#b78b35,#fff1b5);border-radius:8px}.member-actions{display:grid;grid-template-columns:1fr 1fr;gap:9px;border-top:1px solid #f8dc9444;padding-top:13px}.member-actions button{width:100%;min-width:0;padding:12px 7px;border-radius:11px;background:linear-gradient(120deg,#d9b65f,#fff1b8,#c99c45);color:#221909;box-shadow:none;font-size:13px}.member-actions button+button{background:#180b1180;border:1px solid #ddc78a88;color:#fff1c9}.member-actions button:disabled{opacity:.55;cursor:not-allowed;filter:none}.member-actions span,.member-actions small{display:block}.member-actions small{font-size:9px;color:inherit;opacity:.85;margin-top:4px}.vip-black{background:radial-gradient(ellipse at 90% 0%,#a28b4b33,transparent 65%),linear-gradient(125deg,#08090c,#292a2f 45%,#101114 72%,#050507);border-color:#baa06a;box-shadow:0 14px 35px #0009,inset 0 1px 0 #e8d4a766,inset 0 -1px 0 #000}.vip-black:before{opacity:.35}.vip-black:after{opacity:.45}.vip-black .store-card-top{color:#e9d7aa}.vip-black .store-card-top b{background:#d1b16b1a;border-color:#cbb47888}.vip-black .card-holder,.vip-black strong{text-shadow:0 1px 2px #000}.vip-black .card-holder small{color:#bdb9ad}.vip-black .member-actions button+button{background:#121316}.vip-black:hover{box-shadow:0 14px 35px #0009,inset 0 1px 0 #e8d4a766}@media(max-width:360px){.unified-card{padding:15px;gap:12px}.member-actions button{font-size:11px}.member-identity .card-holder{font-size:12px}}
.home-pack-form{margin:0;display:block}.home-pack-form button{width:100%;padding:11px;font-size:13px}.home-pack-form button:disabled{opacity:.6;cursor:not-allowed}.membership-link{padding:4px;background:none;border:0;color:#ffe5ad;text-align:left;font-size:12px;box-shadow:none}.membership-gallery{margin:18px 0}.membership-gallery h2{font-size:21px}.membership-gallery>p{font-size:13px}.tier-cards{display:flex;gap:13px;overflow-x:auto;scroll-snap-type:x mandatory;padding:4px 3px 16px}.tier-tile{flex:0 0 min(285px,88%);scroll-snap-align:start;background:#231516;border:1px solid #806643;border-radius:17px;padding:12px}.tier-current{border:2px solid #ffdf74}.tier-tile svg{display:block;width:100%;height:auto}.tier-tile h3{font-size:17px;color:#ffe3a3;margin:12px 0 5px}.tier-tile p,.tier-tile li{font-size:12px}.tier-tile ul{padding-left:17px;margin:9px 0}.tier-tile li{margin:6px 0}
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
.action-centre{margin:13px 0;padding:14px;border:1px solid #6f5b31;border-radius:16px;background:linear-gradient(145deg,#171719,#0b0b0c)}.action-centre h3{margin:0 0 5px;color:#efd180}.action-centre form{margin-top:10px}.action-status{display:flex;justify-content:space-between;gap:10px;padding:9px 0;border-top:1px solid #63522d55;font-size:13px}.action-status strong{color:#efd180}.reward-shop{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin:14px 0}.reward-choice{padding:13px;border:1px solid #62522f;border-radius:14px;background:#111113}.reward-choice strong,.reward-choice small{display:block}.reward-choice small{color:#c8bfa9}.reward-choice form{display:block;margin-top:9px}.reward-choice button{width:100%;padding:10px}.voucher{padding:12px;margin:8px 0;border:1px dashed #d5b45d;border-radius:13px;background:#d5b45d12}.voucher code{color:#f6d77d;font-weight:900}.voucher-used{opacity:.58}.inbox-count{display:inline-grid;place-items:center;min-width:24px;height:24px;padding:0 7px;border-radius:99px;background:#e5c365;color:#111;font-size:12px;font-weight:950}@media(max-width:460px){.reward-shop{grid-template-columns:1fr}.action-centre form{flex-direction:column}}
#service-toast{position:fixed;left:50%;top:18px;transform:translate(-50%,-160%);z-index:110;width:min(560px,92vw);padding:17px;border:2px solid #e2c269;border-radius:16px;background:#0b0b0c;color:#fff8df;text-align:center;font-weight:900;box-shadow:0 15px 45px #000;transition:transform .25s}#service-toast.show{transform:translate(-50%,0)}
"""


def page(title: str, content: str) -> str:
    body_class = ' class="customer-shell"' if 'id="customer-app"' in content else ""
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{html.escape(title)}</title><style>{CSS}</style></head><body{body_class}><main class="wrap"><div class="brand"><div class="logo-frame"><img src="/snr-logo.png" alt="Official Snr. Buns logo" width="1254" height="1254"></div><div class="tag">LOYALTY • DELIVERY • VIP</div></div>{content}<footer>SNR Buns • Your account is protected by your password</footer></main></body></html>'''


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
    labels = {"pending": "Claim sent — awaiting staff handover", "fulfilled": "Pack handed over — 4 points used", "cancelled": "Claim cancelled — points unchanged"}
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
        action = f'<form method="post" action="/claim"><input type="hidden" name="claim_request_key" value="{html.escape(form_token, quote=True)}"><button type="submit">Request Pack — 4 Points</button></form>'
    badge = " — READY" if points >= 4 else f" — {points}/4 points"
    return f'<section class="app-page" id="rewards"><h2 class="app-page-title">🎁 Claim Reward{badge}</h2><div class="drawer-body"><p><strong>Reach 4 points to claim 1 pack containing 2 trading cards.</strong></p>{progress_bar}<p>{points} available points.</p><p class="muted">Each pack costs 4 points. Staff deduct those 4 points when handing over the pack; all extra points stay on your card.</p>{action}<div class="muted">{history}</div></div></section>'


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
        "paid": "Delivered, paid and added to loyalty",
        "cancelled": "Cancelled",
        "wasted_journey": "Wasted journey — £500 delivery fee owed",
    }
    can_reorder = not active and not fee and orders.configured()
    recent_parts = []
    for row in rows:
        pickup_row = (row.get("fulfillment_type") or "delivery") == "pickup"
        instore_row = row.get("fulfillment_type") == "instore"
        row_labels = dict(labels, paid="Served in store, paid and added to loyalty", pending="Awaiting counter payment") if instore_row else labels
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
            f'''<div class="deal-box"><strong>{html.escape(deal.name)}</strong><span>{html.escape(deal.item_summary)}</span><span>{deal.loyalty_points} loyalty point(s) • {deal.golden_tickets} Golden ticket(s)</span><span class="price">£{deal.price:,} each</span><label class="quantity">Amount <input class="deal-qty" type="number" name="qty_{deal.key}" value="0" min="0" max="10" step="1" data-price="{deal.price}" aria-label="Amount of {html.escape(deal.name, quote=True)}"></label></div>'''
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
        art = f'''<svg viewBox="0 0 320 196" role="img" aria-label="{html.escape(name)} membership card"><defs><linearGradient id="tier-{index}" x2="1" y2="1"><stop stop-color="{light}"/><stop offset="1" stop-color="{dark}"/></linearGradient></defs><rect x="1" y="1" width="318" height="194" rx="19" fill="url(#tier-{index})" stroke="#dbc38a"/><path d="M120 0L320 155M180 0L320 95" stroke="#ffffff" stroke-opacity=".12" stroke-width="28"/><text x="22" y="35" fill="{ink}" font-family="Arial,sans-serif" font-size="15" font-weight="bold">SNR BUNS</text><rect x="23" y="66" width="38" height="29" rx="6" fill="#e2c577" stroke="#998344"/><path d="M35 66v29m13-29v29M23 80h38" stroke="#998344"/><text x="22" y="138" fill="{ink}" font-family="Arial,sans-serif" font-size="25" font-weight="bold">{html.escape(name).upper()}</text><text x="22" y="172" fill="{ink}" font-family="Arial,sans-serif" font-size="10" letter-spacing="2">SNR MEMBERSHIP</text></svg>'''
        fee = "Free delivery" if not level["delivery_fee"] else f'£{level["delivery_fee"]} delivery'
        tiles.append(f'''<article class="tier-tile {'tier-current' if active else ''}">{art}<h3>{html.escape(name)} {'— Your level' if active else ''}</h3><p>{level["minimum_sales"]}+ purchases</p><ul><li>{level["bonus_points"]} extra loyalty point(s) per meal deal</li><li>{level["bonus_tickets"]} extra Golden Ticket(s) per meal deal</li><li>{fee}</li></ul></article>''')
    return '<section class="membership-gallery"><h2>All membership cards</h2><p>Swipe to compare all six levels. Bonuses are added to each meal deal’s normal rewards. Click &amp; Collect is free at every level.</p><div class="tier-cards">' + ''.join(tiles) + '</div></section>'


def customer_page(customer: dict, claims: ClaimStore, orders: DeliveryStore, shifts: StaffShifts,
                  accounts: Accounts, raffles: RaffleStore, services: CustomerServices,
                  claim_token: str, order_token: str, security_token: str, raffle_token: str,
                  service_token: str) -> str:
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
    pending_pack = any(row["status"] == "pending" for row in claims.summary(customer["display_name"]))
    points = int(customer["loyalty_points"])
    can_request = points >= 4 and not pending_pack and claims.configured()
    pack_label = "Pack Requested — Awaiting Handover" if pending_pack else "Request Pack — 4 Points"
    pack_hint = "Your request is with staff." if pending_pack else "Ask staff to enable pack requests." if not claims.configured() else f"You need {max(0, 4-points)} more point(s)." if points < 4 else "Extra points stay on your card."
    request_pack = f'''<form class="home-pack-form" method="post" action="/claim"><input type="hidden" name="claim_request_key" value="{html.escape(claim_token, quote=True)}"><button type="submit" {'disabled' if not can_request else ''}>{pack_label}</button></form><small>{pack_hint}</small>'''
    progress = min(points, 4)
    reward_title = "Your reward is ready!" if points >= 4 else f"{4 - points} point(s) to your reward"
    latest_orders = orders.summary(customer["customer_key"], 1)
    active_order = next((row for row in latest_orders if row["status"] in ("pending", "accepted", "on_way", "arrived", "ready_for_pickup", "processing")), None)
    if active_order:
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
    active_id = int(active_order["id"]) if active_order else 0
    request_choices = ['<option value="staff_help">Call SNR staff</option>']
    if active_order:
        if active_order["status"] == "pending":
            request_choices.extend(['<option value="change_order">Change my order</option>',
                                    '<option value="cancel_order">Cancel my order</option>'])
        if (active_order.get("fulfillment_type") or "delivery") == "delivery":
            request_choices.append('<option value="driver_help">Driver cannot find me</option>')
    help_centre = f'''<details class="action-centre"><summary><strong>💬 Need help from SNR staff?</strong></summary><p class="muted">Send one clear request. It appears in the staff Live Actions inbox.</p><form method="post" action="/customer-action"><input type="hidden" name="service_request_key" value="{html.escape(service_token, quote=True)}"><input type="hidden" name="order_id" value="{active_id}"><select name="action_type" required>{''.join(request_choices)}</select><input name="details" maxlength="250" placeholder="Short message for staff"><button type="submit">Send Request</button></form>{action_history}</details>'''
    catalog = services.catalog()
    pending_custom = next((row for row in services.pending_rewards() if row["customer_key"] == customer["customer_key"]), None)
    reward_cards = "".join(
        f'''<article class="reward-choice"><strong>{html.escape(row["name"])}</strong><small>{int(row["points_cost"])} points</small><form method="post" action="/reward-choice"><input type="hidden" name="service_request_key" value="{html.escape(service_token, quote=True)}"><input type="hidden" name="reward_code" value="{html.escape(row["code"], quote=True)}"><button type="submit" {'disabled' if pending_custom or points < int(row['points_cost']) else ''}>Request</button></form></article>'''
        for row in catalog)
    vouchers = services.vouchers(customer["customer_key"])
    voucher_rows = "".join(
        f'''<div class="voucher {'voucher-used' if row['status'] != 'active' else ''}"><strong>{html.escape(row['title'])}</strong><br><code>{html.escape(row['voucher_code'])}</code> · {html.escape(row['status'].title())}</div>'''
        for row in vouchers[:8]) or '<p class="muted">No vouchers yet.</p>'
    reward_wallet = f'''<section class="app-page quick-more-page"><h2 class="app-page-title">✨ Choose a Reward</h2><div class="drawer-body"><p>Spend your points on the reward you want. Staff approve it once, then it appears in your wallet.</p>{f'<div class="notice">Your {html.escape(pending_custom["reward_name"])} request is awaiting staff.</div>' if pending_custom else ''}<div class="reward-shop">{reward_cards}</div><h3>My voucher wallet</h3>{voucher_rows}</div></section>'''
    latest_action = action_rows[0] if action_rows else None
    latest_rewards = services.reward_requests(customer["customer_key"], 1)
    latest_reward = latest_rewards[0] if latest_rewards else None
    sync_data = (f' data-action-id="{int(latest_action["id"])}" data-action-status="{latest_action["status"]}"' if latest_action else '')
    sync_data += (f' data-reward-id="{int(latest_reward["id"])}" data-reward-status="{latest_reward["status"]}"' if latest_reward else '')
    return page(f'{customer["display_name"]} • SNR Loyalty', f'''<section class="card quick-hub" id="customer-app"{sync_data}>{review_popup}<div id="service-toast" role="status"></div>
      <header class="quick-app-head"><div class="quick-brand"><img src="/snr-logo.png" alt="SNR Buns"><span><strong>SNR Buns</strong><small aria-label="CUSTOMER APP">SNR MEMBERS CLUB</small></span></div><span class="quick-service {service_class}">● {service_text}</span></header>
      <div class="quick-welcome"><div><div class="label">Welcome back</div><div class="name">{html.escape(customer["display_name"])}</div></div><p>{membership["emoji"]} {html.escape(membership["name"])}</p></div>
      <div class="quick-content">{banner}
      <div class="app-view active" data-app-view="home" role="tabpanel">{recovery}
        <section class="store-card unified-card {'vip-black' if membership['name'] == 'SNR VIP' else ''}" aria-label="Your membership card">
          <div class="store-card-top"><span>SNR BUNS</span><b>{html.escape(membership["name"]).upper()}</b></div>
          <div class="member-identity"><span class="card-chip" aria-hidden="true"></span><span class="card-holder"><small>LOYALTY CARDHOLDER</small><span>{html.escape(customer["display_name"])}</span></span></div>
          <button class="card-reward" type="button" data-tab-target="rewards"><span><strong>{reward_title}</strong><small>{points} loyalty points · View rewards ↗</small></span><span class="reward-arrow" aria-hidden="true">↗</span></button>
          <div class="member-progress" role="progressbar" aria-label="Loyalty reward progress" aria-valuemin="0" aria-valuemax="4" aria-valuenow="{progress}"><span style="width:{progress * 25}%"></span></div>
          {request_pack}
          <button type="button" class="membership-link" data-tab-target="visits">View membership cards &amp; benefits ↗</button>
          <div class="member-actions"><button type="button" data-tab-target="order" data-order-mode="pickup"><span>Click &amp; Collect</span><small>Collect from SNR Buns</small></button><button type="button" data-tab-target="order" data-order-mode="delivery" {'disabled aria-disabled="true"' if not drivers or service["mode"] in ("closed", "pickup_only", "delivery_paused") else ''}><span>Delivery</span><small>{'Currently unavailable' if not drivers or service["mode"] in ("closed", "pickup_only", "delivery_paused") else 'Delivered to you'}</small></button></div>
        </section>
        <div class="quick-actions"><button type="button" class="quick-action quick-action-main" data-tab-target="raffle" aria-selected="false"><span>🎟️</span><strong>Live Raffle</strong><small>Choose numbers</small></button></div>
        <div class="quick-section-title"><strong>Current order</strong><span>LIVE STATUS</span></div>{quick_order}{help_centre}
        <div class="quick-metrics"><div><small>MEMBERSHIP</small><strong>{membership["emoji"]} {html.escape(membership["name"])}</strong></div><div><small>Golden tickets</small><strong>🎟️ {int(customer["golden_tickets"])}</strong></div><div><small>VISITS</small><strong>🍔 {int(customer["lifetime_sales"])}</strong></div></div>
        <div class="quick-jackpot"><strong>🎟️ £5,000 Golden Ticket Jackpot</strong><div>{jackpot}</div></div>
      </div>
      <div class="app-view" data-app-view="order" role="tabpanel">{delivery_section(customer, orders, shifts, services, order_token)}</div>
      <div class="app-view" data-app-view="raffle" role="tabpanel">{raffle_section(customer, raffles, raffle_token)}</div>
      <div class="app-view" data-app-view="rewards" role="tabpanel">{claim_section(customer, claims, claim_token)}{reward_wallet}</div>
      <div class="app-view" data-app-view="visits" role="tabpanel"><div class="grid">{vip}</div>{membership_gallery(membership["name"])}{birthday_box}<section class="app-page quick-more-page" id="history"><h2 class="app-page-title">📋 My Recent Visits</h2><div class="drawer-body">{recent}</div></section><form method="post" action="/logout"><input type="hidden" name="logout" value="1"><button class="secondary" type="submit">Log Out</button></form></div>
      </div>
      <nav class="app-tabs" aria-label="Customer account pages" role="tablist">
        <button class="app-tab" type="button" role="tab" aria-selected="true" data-tab-target="home"><span class="tab-icon">🏠</span>Home</button>
        <button class="app-tab" type="button" role="tab" aria-selected="false" data-tab-target="order"><span class="tab-icon">🍔</span>Order</button>
        <button class="app-tab" type="button" role="tab" aria-selected="false" data-tab-target="raffle"><span class="tab-icon">🎟️</span>Raffle</button>
        <button class="app-tab" type="button" role="tab" aria-selected="false" data-tab-target="rewards"><span class="tab-icon">🎁</span>Rewards</button>
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
    limiter, claims, orders, accounts, shifts, raffles, services = (Limiter(), ClaimStore(db), DeliveryStore(db),
                                                          Accounts(db), StaffShifts(db), RaffleStore(db), CustomerServices(db))
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
                customer, claims, orders, shifts, accounts, raffles, services, make_form_token(owner),
                make_form_token(owner), make_form_token(owner), make_form_token(owner), make_form_token(owner)
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
const quickOrder=document.querySelector("[data-quick-order-id]");if(quickOrder)setInterval(async()=>{try{const r=await fetch("/order-status",{cache:"no-store"});if(!r.ok)return;const d=await r.json();if(String(d.id)!==quickOrder.dataset.quickOrderId||d.status!==quickOrder.dataset.quickOrderStatus)location.reload()}catch(e){}},3000);
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
                    message = {"pending": "Your claim has been sent to SNR staff. Visit SNR Buns to collect your pack. Only 4 points will be deducted when staff mark it handed over. Any extra points are kept.", "fulfilled": "Staff marked this pack as handed over and 4 points have been used. Any extra points are kept.", "cancelled": "This claim was cancelled and your points were not changed."}[result["status"]]
                    self.send_html(200, page("Reward request", f'<section class="card"><h1>Request #{result["id"]}</h1><p>{message}</p><a class="back" href="/account">Back to my account</a></section>'))
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
                    self.send_html(200, page("Reward requested", f'''<section class="card"><div class="label">Reward Request #{int(result["id"])}</div><h1>{html.escape(result["reward_name"])}</h1><div class="notice">Staff have been notified. Your points are only deducted when they approve the reward.</div><a class="back" href="/account#rewards">Back to rewards</a></section>'''))
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
