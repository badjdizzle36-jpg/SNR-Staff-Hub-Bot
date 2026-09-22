"""Customer City Run overview, rendered from the authenticated board snapshot."""
import html
from datetime import datetime, timezone


def esc(value):
    return html.escape(str(value), quote=True)


def dashboard(board, token):
    campaign = board['campaign']
    groups = board['collections']
    active = campaign['status'] == 'active'
    claims_open = campaign['status'] in ('active', 'paused')
    ready = [g for g in groups if g['complete'] and g.get('reward')
             and (g.get('claim') or {}).get('status') not in ('pending', 'fulfilled')]
    incomplete = [g for g in groups if not g['complete']]
    nearest = min(incomplete, key=lambda g: (g['total']-g['collected'], -g['collected']/g['total'])) if incomplete else None
    items = [i for g in groups for i in g['items']]
    recent = sorted([i for i in items if i['owned']], key=lambda i: i.get('first_collected_at') or '', reverse=True)[:5]
    missing = [i for i in items if not i['owned']]
    market = board.get('marketplace') or {'open': 0, 'mine': 0, 'completed': 0, 'recent': []}
    available = int(board['available_reveals'])
    form_token = f'<input type="hidden" name="city_run_request_key" value="{esc(token)}">'
    if active and available:
        action = f'<form method="post" action="/city-run-reveal">{form_token}<button class="city-main-action" type="submit">Open My Stickers</button></form>'
        hint = 'Opens one sticker. Your remaining stickers stay ready for the next reveal.'
    elif claims_open and ready:
        action = f'<form method="post" action="/city-run-claim">{form_token}<input type="hidden" name="reward_key" value="{esc(ready[0]["key"])}"><button class="city-main-action" type="submit">Claim Route Reward</button></form>'
        hint = f'{ready[0]["name"]} • {ready[0]["reward"]["reward_name"]}'
    elif active and (board['duplicates'] or market['open']):
        action = '<a class="city-main-action" href="/city-run-trades">Visit Marketplace</a>'
        hint = 'Browse offers and exchange spare stickers for missing businesses.'
    else:
        action = '<button class="city-main-action" type="button" data-city-collection>View My Collection</button>'
        hint = 'Explore your businesses, route rewards and grand prize.'
    deadline = 'End date to be announced'
    raw_end = campaign.get('ends_at')
    if raw_end:
        try:
            end = datetime.fromisoformat(raw_end.replace('Z', '+00:00'))
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            seconds = max(0, int((end-datetime.now(timezone.utc)).total_seconds()))
            countdown = f'{seconds//86400}d {(seconds%86400)//3600}h {(seconds%3600)//60}m remaining' if seconds else 'Season deadline reached'
            deadline = f'Ends {esc(end.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC"))}<br><b data-city-countdown="{esc(end.isoformat())}">{countdown}</b>'
        except (ValueError, TypeError):
            deadline = 'End date to be confirmed'
    if campaign['status'] == 'ended':
        deadline = 'Season ended' + (f' • {esc(campaign["ended_at"][:10])}' if campaign.get('ended_at') else '')
    nearest_html = 'All eight routes completed.'
    if nearest:
        names = ', '.join(i['name'] for i in nearest['items'] if not i['owned'])
        nearest_html = f'<b>{esc(nearest["name"])}</b><p>{nearest["collected"]} of {nearest["total"]} • {nearest["total"]-nearest["collected"]} to go</p><small>Missing: {esc(names)}</small>'
    completed = []
    for g in groups:
        if not g['complete']:
            continue
        claim = g.get('claim') or {}
        state = {'pending': 'Awaiting staff', 'fulfilled': 'Reward issued', 'cancelled': 'Unclaimed • request again'}.get(claim.get('status'), 'Unclaimed' if g.get('reward') else 'Reward not configured')
        if not claims_open and state.startswith('Unclaimed'):
            state += ' • claims closed'
        completed.append(f'<li><b>{esc(g["name"])}</b><span>{esc(state)}</span></li>')
    grand_state = ''
    if board.get('grand_complete'):
        grand_state = '<p><b>Full set complete: 38 of 38.</b> See your grand-prize claim below.</p>'
    recent_html = ''.join(f'<li>{esc(i["name"])}</li>' for i in recent) or '<li>Your first business will appear here.</li>'
    missing_html = ''.join(f'<li>{esc(i["name"])}</li>' for i in missing) or '<li>You have collected every business.</li>'
    names = {i['key']: i['name'] for i in items}
    market_rows = ''.join(f'<li>#{int(t["id"])} • {esc(names.get(t["offered"],t["offered"]))} ⇄ {esc(names.get(t["wanted"],t["wanted"]))}<span>{esc(t["status"].title())}</span></li>' for t in market['recent'])
    status = {'active': 'SEASON LIVE', 'paused': 'SEASON PAUSED', 'ended': 'SEASON ENDED'}.get(campaign['status'], 'CITY RUN')
    return f'''<style>
.customer-shell .brand{{display:none}}.city-overview{{color:#f8fafc;margin-bottom:8px;font-size:12px}}.city-overview header{{display:flex;justify-content:space-between;gap:8px;align-items:start}}.city-overview h3{{margin:0;font-size:17px}}.city-overview p{{margin:5px 0}}.city-season{{text-align:right;font-size:10px;color:#cbd5e1}}.city-overview .city-run-score{{margin:8px 0;gap:5px}}.city-overview .city-run-score>div{{padding:8px 3px}}.city-overview .city-main-action{{display:flex;box-sizing:border-box;justify-content:center;align-items:center;width:100%;min-height:44px;padding:10px;border-radius:10px;background:#f0c963;color:#211302;border:0;font-size:15px;font-weight:800;text-align:center;text-decoration:none;cursor:pointer;box-shadow:none;touch-action:manipulation}}.city-action-hint{{font-size:10px;color:#b2bac7;text-align:center}}.city-overview details,.city-board-page details.city-board-details{{margin:6px 0;border:1px solid #343d4c;border-radius:10px;background:#141923;overflow:hidden}}.city-overview summary,.city-board-page details.city-board-details>summary{{box-sizing:border-box;min-height:44px;padding:12px;font-size:12px;cursor:pointer;touch-action:manipulation;line-height:1.4}}.city-overview summary span{{color:#f3cf78;font-weight:bold}}.city-detail-body{{padding:0 12px 10px;overflow-wrap:anywhere;font-size:12px}}.city-detail-body ul{{margin:4px 0;padding-left:17px}}.city-detail-body li{{margin:5px 0}}.city-detail-body li span{{display:block;color:#b9c5d8;font-size:11px}}.city-detail-body button{{min-height:44px;font-size:12px;padding:8px;box-shadow:none}}.city-market-short{{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:10px 12px;margin:6px 0;border:1px solid #826735;border-radius:10px;color:#f5d078!important;text-decoration:none;min-height:44px;box-sizing:border-box}}.city-market-short small{{display:block;font-size:10px;color:#b9c5d8}}.city-overview :focus-visible{{outline:2px solid #38bdf8;outline-offset:2px}}.city-board-page .city-route .city-business-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.city-board-page .city-business-art{{max-height:90px}}.city-board-page .city-business-art img{{max-height:90px;object-fit:contain}}.city-board-page .city-route-reward{{padding:8px}}.city-board-page .city-route-reward button{{font-size:12px;padding:10px;min-height:44px;box-shadow:none}}.city-board-page .city-route{{margin:6px}}.city-board-page .city-route summary{{min-height:44px;font-size:12px;padding:10px}}.city-board-page .city-grand{{padding:10px;margin:0}}.city-board-page .city-board-art-wrap{{margin:8px}}.city-board-page .city-corners{{padding:8px}}.city-board-page .drawer-body{{padding:10px!important}}
</style><section class="city-overview" aria-label="City Run overview"><header><div><small>{status}</small><h3>Your City Run</h3></div><div class="city-season">{deadline}</div></header>
<div class="city-run-score"><div><b>{available}</b><small>STICKERS READY</small></div><div><b>{int(board['unique_collected'])} of 38</b><small>COLLECTED</small></div><div><b>{int(board['duplicates'])}</b><small>DUPLICATES</small></div></div>
{action}<p class="city-action-hint">{esc(hint)}</p>
<details><summary>Nearest route · <span>{esc(nearest['name']) + ' · ' + str(nearest['total']-nearest['collected']) + ' to go' if nearest else 'All complete'}</span></summary><div class="city-detail-body">{nearest_html}</div></details>
<a class="city-market-short" href="/city-run-trades"><span><b>Live Marketplace →</b><small>{market['open']} live offers · {market['mine']} yours · {market['completed']} swaps</small></span></a>
<details><summary>Rewards · <span>{sum(g['complete'] for g in groups)} of 8 routes complete · {len(ready)} unclaimed</span></summary><div class="city-detail-body"><ul>{''.join(completed) or '<li>Complete a route to unlock its reward.</li>'}</ul>{grand_state}<button type="button" class="secondary" data-city-collection>View reward details</button></div></details>
<details><summary>Newly collected businesses · <span>{len(recent)}</span></summary><div class="city-detail-body"><ul>{recent_html}</ul></div></details>
<details><summary>Missing businesses · {len(missing)}</summary><div class="city-detail-body"><ul>{missing_html}</ul></div></details>
</section><script src="/city-dashboard.js?v=compact-2" defer></script>'''


DASHBOARD_JS = r'''
(function(){
function init(){
 Array.prototype.forEach.call(document.querySelectorAll('[data-city-collection]'),function(button){button.addEventListener('click',function(){var collection=document.getElementById('city-collection-details');if(collection){collection.open=true;collection.scrollIntoView({block:'start'});var summary=collection.querySelector('summary');if(summary)summary.focus();}});});
 function update(){Array.prototype.forEach.call(document.querySelectorAll('[data-city-countdown]'),function(el){var deadline=Date.parse(el.dataset.cityCountdown);if(!isFinite(deadline))return;var seconds=Math.max(0,Math.floor((deadline-Date.now())/1000));el.textContent=seconds?Math.floor(seconds/86400)+'d '+Math.floor(seconds%86400/3600)+'h '+Math.floor(seconds%3600/60)+'m '+seconds%60+'s remaining':'Season deadline reached';});}
 update();setInterval(update,1000);
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
'''
