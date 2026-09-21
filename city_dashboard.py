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
.city-overview{{color:#f8fafc;margin-bottom:20px}}.city-overview header{{display:flex;justify-content:space-between;gap:16px;align-items:start}}.city-overview h3{{margin:0 0 10px;font-size:17px}}.city-overview p{{margin:8px 0}}.city-season{{text-align:right;font-size:12px;color:#cbd5e1}}.city-overview .city-main-action{{display:block;box-sizing:border-box;width:100%;padding:17px;border-radius:15px;background:linear-gradient(110deg,#fde68a,#f59e0b);color:#211302;border:0;font-size:18px;font-weight:800;text-align:center;text-decoration:none;cursor:pointer}}.city-action-hint{{font-size:12px;color:#cbd5e1;text-align:center}}.city-summary-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:20px}}.city-summary-card{{min-width:0;background:#141923;border:1px solid #30394b;padding:18px;border-radius:16px;overflow-wrap:anywhere}}.city-summary-card ul{{padding-left:18px;margin:8px 0;font-size:13px}}.city-summary-card li{{margin:8px 0}}.city-summary-card li span{{display:block;color:#b8c4d8;font-size:12px}}.city-summary-card a{{color:#fde68a}}.city-summary-card small{{color:#b8c4d8}}.city-overview details summary{{cursor:pointer;font-weight:700}}.city-overview button:focus-visible,.city-overview a:focus-visible{{outline:3px solid #38bdf8;outline-offset:3px}}@media(max-width:540px){{.city-summary-grid{{grid-template-columns:1fr}}.city-overview header{{display:block}}.city-season{{text-align:left;margin:8px 0 14px}}}}
</style><section class="city-overview" aria-label="City Run overview"><header><div><small>{status}</small><h3>Your City Run</h3></div><div class="city-season">{deadline}</div></header>
<div class="city-run-score"><div><b>{available}</b><small>STICKERS READY</small></div><div><b>{int(board['unique_collected'])} of 38</b><small>COLLECTED</small></div><div><b>{int(board['duplicates'])}</b><small>DUPLICATES</small></div></div>
{action}<p class="city-action-hint">{esc(hint)}</p><div class="city-summary-grid">
<article class="city-summary-card"><h3>Nearest route to completion</h3>{nearest_html}</article>
<article class="city-summary-card"><h3>Completed sets & rewards</h3><p>{sum(g['complete'] for g in groups)} of 8 routes complete • {len(ready)} unclaimed</p><ul>{''.join(completed) or '<li>Complete a route to unlock its reward.</li>'}</ul>{grand_state}<button type="button" class="secondary" data-city-collection>View reward details</button></article>
<article class="city-summary-card"><h3>Newly collected businesses</h3><ul>{recent_html}</ul></article>
<article class="city-summary-card"><h3>Marketplace activity</h3><p>{market['open']} live offers • {market['mine']} yours</p><p>{market['completed']} completed trades</p><ul>{market_rows or '<li>No marketplace activity yet.</li>'}</ul><a href="/city-run-trades">View marketplace activity →</a></article>
<article class="city-summary-card"><details><summary>Missing businesses · {len(missing)}</summary><ul>{missing_html}</ul></details></article>
</div></section><script src="/city-dashboard.js" defer></script>'''


DASHBOARD_JS = r'''
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-city-collection]').forEach(button => button.addEventListener('click', () => {
    const collection = document.getElementById('city-collection-details');
    if (collection) { collection.open = true; collection.scrollIntoView({behavior:'smooth', block:'start'}); collection.querySelector('summary')?.focus(); }
  }));
  const update = () => document.querySelectorAll('[data-city-countdown]').forEach(el => {
    const deadline = Date.parse(el.dataset.cityCountdown);
    if (!Number.isFinite(deadline)) return;
    const seconds = Math.max(0, Math.floor((deadline-Date.now())/1000));
    el.textContent = seconds ? `${Math.floor(seconds/86400)}d ${Math.floor(seconds%86400/3600)}h ${Math.floor(seconds%3600/60)}m ${seconds%60}s remaining` : 'Season deadline reached';
  });
  update(); setInterval(update, 1000);
});
'''
