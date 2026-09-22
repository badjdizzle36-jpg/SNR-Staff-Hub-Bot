"""Phone-first marketplace: native links/forms, paged offers and safe refresh."""
import html
import secrets
from urllib.parse import urlencode

PAGE_SIZE = 3
TABS = ('live', 'duplicates', 'make', 'activity')

def esc(value):
    return html.escape(str(value), quote=True)

def url(tab='live', page=1, rarity='all', **extra):
    return '/city-run-trades?' + urlencode(dict(tab=tab, page=page, rarity=rarity, **extra))

def normal_options(query=None):
    query=query or {}
    tab=query.get('tab','live')
    if tab not in TABS: tab='live'
    rarity=query.get('rarity','all')
    if rarity not in ('all','common','rare','ultra_rare'): rarity='all'
    try: page=max(1,min(100000,int(query.get('page',1))))
    except (TypeError,ValueError): page=1
    return tab,page,rarity

def token_input(token):
    return f'<input type="hidden" name="city_run_request_key" value="{esc(token)}">'

def artwork(key):
    return f'<img src="/city-thumb/{esc(key)}.webp?v=compact-2" alt="" loading="lazy" width="48" height="70">'

def trade_form(row, token, action, label):
    return (f'<form method="post" action="/city-run-trade">{token_input(token)}'
            f'<input type="hidden" name="trade_id" value="{int(row["id"])}">'
            f'<input type="hidden" name="action" value="{action}">'
            f'<button class="market-action" type="submit">{label}</button></form>')

def feed_html(data, token, tab, rarity):
    feed=data['feed'];pieces={p['key']:p for p in data['pieces']}
    rows=[]
    for row in feed['rows']:
        offered=pieces[row['offered']]; wanted=pieces[row['wanted']]
        mine=row['maker']==data['owner']
        eligible=data['active'] and wanted['spares']>0 and offered['rarity']==wanted['rarity']
        action=''
        if row['status']=='open':
            if mine: action=trade_form(row,token,'cancel','Cancel offer')
            elif eligible: action=trade_form(row,token,'accept','Confirm swap')
            else:
                reason='Trading unavailable' if not data['active'] else 'You need a spare '+wanted['name']
                action=f'<p class="market-ineligible">{esc(reason)}</p>'
        seller=f'Your offer #{row["id"]}' if mine else f'{row.get("display_name", "Player")} • #{row["id"]}'
        rows.append(f'''<article class="market-offer" data-offer-id="{int(row['id'])}">
<header><strong>{esc(seller)}</strong><small>{esc(row['status'].title())} · {esc(offered['rarity'].replace('_',' '))}</small></header>
<div class="market-pair"><div>{artwork(row['offered'])}<span><small>{'YOU OFFER' if mine else 'YOU RECEIVE'}</small><b>{esc(offered['name'])}</b></span></div><b aria-hidden="true">⇄</b><div>{artwork(row['wanted'])}<span><small>{'YOU WANT' if mine else 'YOU GIVE'}</small><b>{esc(wanted['name'])}</b></span></div></div>
<footer><small>{'Expires '+esc(row['expires_at'][:16].replace('T',' '))+' UTC' if row['status']=='open' else esc(row['status'].title())}</small>{action}</footer></article>''')
    empty='No other players’ live offers yet. Publish a spare from My duplicates.' if tab=='live' else 'You have no offers or completed trades yet.'
    if rarity!='all' and tab=='live': empty='No live offers match this rarity. Try All.'
    pages=feed['pages']; page=feed['page']
    prev=f'<a href="{esc(url(tab,page-1,rarity))}">Previous</a>' if page>1 else '<span></span>'
    nxt=f'<a href="{esc(url(tab,page+1,rarity))}">Next</a>' if page<pages else '<span></span>'
    return ''.join(rows or [f'<p class="market-empty">{empty}</p>'])+f'<nav class="market-pager" aria-label="Offer pages">{prev}<span>{page} / {pages} · {feed["total"]} offers</span>{nxt}</nav>'


def render_market(data, token, options=None):
    options=options or {};tab,page,rarity=normal_options(options)
    pieces=data['pieces']; feed=data['feed']
    nav=''.join(f'<a href="{esc(url(key))}" aria-current="{str(tab==key).lower()}">{label}</a>' for key,label in [('live','Live market'),('duplicates','My duplicates'),('make','Make offer'),('activity','My activity')])
    body=''
    if tab in ('live','activity'):
        filters=''
        if tab=='live':
            filters='<nav class="market-filters" aria-label="Filter rarity">'+''.join(f'<a href="{esc(url(tab,1,key))}" aria-current="{str(key==rarity).lower()}">{label}</a>' for key,label in [('all','All'),('common','Common'),('rare','Rare'),('ultra_rare','Ultra rare')])+'</nav>'
        body=filters+f'<div class="market-results" data-feed-url="/city-market-feed?{esc(urlencode(dict(tab=tab,page=feed["page"],rarity=rarity)))}">'+feed_html(data,token,tab,rarity)+'</div>'
    elif tab=='duplicates':
        spares=[p for p in pieces if p['spares']>0]
        pages=max(1,(len(spares)+PAGE_SIZE-1)//PAGE_SIZE);page=min(page,pages)
        for piece in spares[(page-1)*PAGE_SIZE:page*PAGE_SIZE]:
            action=f'<a class="market-action" href="{esc(url("make",offered=piece["key"]))}">Offer this card</a>' if data['active'] else '<small>Trading unavailable</small>'
            body+=f'<article class="market-spare">{artwork(piece["key"])}<div><b>{esc(piece["name"])}</b><small>{piece["spares"]} spare · {esc(piece["rarity"].replace("_"," "))}</small></div>{action}</article>'
        if not spares: body='<p class="market-empty">No unreserved duplicates. Your listed stickers appear in My activity.</p>'
        prev=f'<a href="{esc(url(tab,page-1))}">Previous</a>' if page>1 else '<span></span>'
        nxt=f'<a href="{esc(url(tab,page+1))}">Next</a>' if page<pages else '<span></span>'
        body+=f'<nav class="market-pager">{prev}<span>{page} / {pages}</span>{nxt}</nav>'
    else:
        spares=[p for p in pieces if p['spares']>0 and any(q['key']!=p['key'] and q['rarity']==p['rarity'] for q in pieces)]
        selected=(next((p for p in spares if p['key']==options['offered']),None) if options.get('offered') else spares[0] if spares else None)
        if not selected or not data['active']:
            body='<p class="market-empty">No tradeable spare is ready, or trading is unavailable. Check My duplicates or My activity.</p>'
        else:
            # Native radios avoid operating-system select popups inside FiveM NUI.
            give=''.join(f'<label class="market-choice"><input type="radio" name="offered" value="{esc(p["key"])}" data-rarity="{esc(p["rarity"])}" {"checked" if p==selected else ""} required><span>{esc(p["name"])} <small>×{p["spares"]} · {esc(p["rarity"].replace("_"," "))}</small></span></label>' for p in spares)
            want=''.join(f'<label class="market-choice" data-wanted-rarity="{esc(p["rarity"])}"><input type="radio" name="wanted" value="{esc(p["key"])}" required><span>{esc(p["name"])} <small>{esc(p["rarity"].replace("_"," "))}</small></span></label>' for p in pieces)
            body=f'''<form id="swap-create" method="post" action="/city-run-trade">{token_input(token)}<input type="hidden" name="request_key" value="{secrets.token_urlsafe(24)}"><input type="hidden" name="action" value="create"><fieldset><legend>1. Give one spare</legend><div class="market-choices">{give}</div></fieldset><fieldset><legend>2. Choose what you want</legend><div class="market-choices">{want}</div></fieldset><p class="market-help">Same-rarity swaps only. Your original stays on your board.</p><button class="market-action" type="submit">Publish offer</button></form>'''
    notices={'listed':'Your offer is live. It appears in My activity and in other players’ Live Marketplace.', 'accepted':'Trade completed. Your collection is updated.', 'cancelled':'Offer cancelled. Your spare is available again.'}
    notice=f'<p class="market-notice" role="status">{notices[options["notice"]]}</p>' if options.get('notice') in notices else ''
    if not data['active']: notice+='<p class="market-notice">Trading is unavailable. You can still view activity and cancel your offers.</p>'
    refresh=f'<a href="{esc(url(tab,feed["page"],rarity))}">Refresh</a>'
    return f'''{STYLES}<section class="market" id="marketplace" data-tab="{tab}"><header class="market-heading"><a href="/account#city-run">← City Run</a><small>FIRST COPIES PROTECTED</small><h1>Live Marketplace</h1><p><span id="market-live-total">{data['live_total']}</span> other players’ offers · {sum(p['spares'] for p in pieces)} available spares</p></header><nav class="market-tabs" aria-label="Marketplace sections">{nav}</nav>{notice}<div class="market-content"><div class="market-toolbar"><b>{dict(live='Other players’ offers',duplicates='Your available spares',make='Make a trade',activity='Your offers & trades')[tab]}</b>{refresh}</div><small id="market-live-status" role="status">{'Updates every 15 seconds' if tab=='live' else ''}</small>{body}</div></section><script src="/city-marketplace.js?v=compact-2" defer></script>'''

STYLES='''<style>
.marketplace-shell .brand,.marketplace-shell>.wrap>footer{display:none}.marketplace-shell .wrap{width:100%;max-width:720px;padding:8px 10px 20px;box-sizing:border-box}.market{color:#f4f2eb;font-size:13px}.market *{box-sizing:border-box}.market [hidden]{display:none!important}.market a{color:#f5d171;text-decoration:none}.market-heading{padding:6px 2px 12px}.market-heading>a{display:inline-flex;align-items:center;min-height:44px}.market-heading>small{float:right;font-size:9px;line-height:44px;color:#aaa}.market-heading h1{font-size:23px;margin:2px 0 5px}.market-heading p{margin:0;color:#aaa;font-size:12px}.market .market-tabs{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:4px;margin:0 0 10px;position:static}.market-tabs a,.market-filters a{display:flex;align-items:center;justify-content:center;min-height:44px;background:#191b23;border:1px solid #3b3e48;border-radius:9px;font-size:11px;font-weight:bold;text-align:center;padding:4px}.market a[aria-current="true"]{background:#483617;border-color:#e6bf61;color:#fff0b1}.market-toolbar{display:flex;justify-content:space-between;align-items:center;min-height:36px}.market-toolbar a{padding:10px}.market #market-live-status{display:block;font-size:10px;color:#a6b2b0;min-height:15px}.market-filters{display:flex;gap:5px;margin:8px 0}.market-filters a{flex:1;font-size:11px}.market-offer{border:1px solid #3a3e47;background:#11151d;border-radius:12px;margin:7px 0;padding:10px}.market-offer header{display:flex;justify-content:space-between;gap:6px;font-size:11px;overflow-wrap:anywhere}.market-offer header small{color:#b9c2cf;font-size:10px}.market-pair{display:grid;grid-template-columns:minmax(0,1fr) 18px minmax(0,1fr);gap:5px;align-items:center;margin:8px 0}.market-pair>div{display:flex;gap:6px;align-items:center;min-width:0}.market-pair img,.market-spare img{object-fit:contain;width:42px;height:60px;flex-shrink:0}.market-pair span{min-width:0}.market-pair small,.market-pair b,.market-spare small{display:block;overflow-wrap:anywhere}.market-pair small{font-size:8px;color:#e8c76a;margin-bottom:4px}.market-pair b{font-size:11px;line-height:1.2}.market-offer footer{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:0;padding:0;border:0;text-align:left}.market-offer footer>small{font-size:9px;color:#aab0bc}.market form{display:block;margin:0}.market-offer form{flex:0 0 auto}.market button,.market .market-action{display:inline-flex;justify-content:center;align-items:center;width:auto;min-height:44px;padding:8px 12px;font-size:12px;font-weight:bold;line-height:1.2;color:#241600;background:#f0c963;border:0;border-radius:8px;box-shadow:none;transform:none;touch-action:manipulation;cursor:pointer}.market a,.market label{touch-action:manipulation}.market-ineligible{font-size:10px;margin:0;color:#aeb6c5;max-width:55%}.market-pager{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:10px;text-align:center;font-size:11px;margin-top:8px}.market-pager a{padding:12px;min-height:44px;background:#1a202b;border-radius:8px}.market-spare{display:flex;align-items:center;gap:8px;padding:12px 8px;border:1px solid #353b48;border-radius:10px;margin:8px 0}.market-spare>div{flex:1;min-width:0;overflow-wrap:anywhere}.market-spare small{font-size:10px;color:#aab0bc;margin-top:4px}.market-spare .market-action{max-width:100px;text-align:center}.market fieldset{min-width:0;border:1px solid #363d49;border-radius:10px;padding:8px;margin:10px 0}.market legend{padding:0 6px;color:#f0cd7b;font-weight:bold}.market-choices{max-height:145px;overflow-y:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch}.market .market-choice{display:flex;align-items:center;gap:8px;min-height:44px;padding:7px;margin:0;border-bottom:1px solid #2c333e;cursor:pointer;color:#eee}.market .market-choice input{position:static;appearance:auto;opacity:1;width:18px;height:18px;min-height:0;margin:0;flex:none;accent-color:#edc86e;pointer-events:auto}.market-choice span{font-size:12px}.market-choice small{font-size:10px;color:#abb7ca}.market .market-choice:has(input:checked){background:#433619}.market-help{font-size:11px;color:#aab4c3}.market #swap-create>button{width:100%}.market-empty,.market-notice{padding:12px;border-radius:10px;background:#171e29;font-size:12px;line-height:1.4}.market-notice{border:1px solid #806626}.market :focus-visible{outline:2px solid #64ddff;outline-offset:2px}@media(max-width:340px){.market-pair img{width:30px;height:45px}.market button,.market .market-action{padding:7px;font-size:11px}.market-heading h1{font-size:21px}}
</style>'''

MARKETPLACE_JS=r'''(function(){
function init(){
 var root=document.getElementById('marketplace');if(!root)return;
 var form=document.getElementById('swap-create');
 if(form){
  function filter(){var give=form.querySelector('input[name="offered"]:checked');if(!give)return;
   Array.prototype.forEach.call(form.querySelectorAll('[data-wanted-rarity]'),function(label){var input=label.querySelector('input');var available=label.getAttribute('data-wanted-rarity')===give.getAttribute('data-rarity')&&input.value!==give.value;label.hidden=!available;input.disabled=!available;if(!available)input.checked=false;});
  }
  form.addEventListener('change',filter);filter();
  var checked=form.querySelector('input[name="offered"]:checked');if(checked)checked.parentNode.parentNode.scrollTop=checked.parentNode.offsetTop-checked.parentNode.parentNode.offsetTop;
 }
 var results=root.querySelector('.market-results'),status=document.getElementById('market-live-status'),busy=false;
 function refresh(){
  if(!results||root.getAttribute('data-tab')!=='live'||document.hidden||busy)return;
  if(document.activeElement&&results.contains(document.activeElement))return;
  busy=true;
  fetch(results.getAttribute('data-feed-url'),{credentials:'same-origin',cache:'no-store'}).then(function(response){if(!response.ok)throw Error(response.status===401?'Please log in again':'Refresh unavailable');return response.json();}).then(function(data){
   if(document.activeElement&&results.contains(document.activeElement))return;
   results.innerHTML=data.html;var total=document.getElementById('market-live-total');if(total)total.textContent=data.live_total;status.textContent='Live · updated '+new Date().toLocaleTimeString();
  }).catch(function(error){status.textContent=error.message+' — use Refresh to retry';}).then(function(){busy=false;});
 }
 if(window.fetch)setInterval(refresh,15000);
 // Use click/submit only. No pointer capture, drag handlers or touch cancellation.
 root.addEventListener('submit',function(event){var f=event.target;if(f.getAttribute('data-submitting')==='yes'){event.preventDefault();return;}f.setAttribute('data-submitting','yes');var button=f.querySelector('button[type="submit"]');if(button){button.setAttribute('aria-busy','true');button.textContent='Please wait…';}});
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();'''
