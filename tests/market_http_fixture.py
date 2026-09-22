"""Test-only HTTP harness using the real portal Handler and isolated SQLite.
Unrelated account/deployment dependencies are replaced by a test session map.
"""
import ast,html,secrets,hmac,hashlib,time,json
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import parse_qs,urlparse
from threading import Thread
from types import SimpleNamespace
from collections import defaultdict,deque
from test_city_overview import CityOverviewTests
from city_trades import exchange_html,MARKETPLACE_JS
from compact_market import feed_html,normal_options
from city_dashboard import dashboard,DASHBOARD_JS
from city_run import CityRunStore, BUSINESSES
from city_artwork import poster_markup,sticker_path,sticker_svg

class Fixture:
    def __init__(self):
        self.case=CityOverviewTests();self.case.setUp()
        self.case.card('food-1',20,'jamie');self.case.card('food-2',20,'ash')
        self.case.trades.admin_configure(True,20,24,'1','Owner','HTTP test fixture')
        for n in range(7):self.case.trades.create('ash','food-2','food-1',f'other-offer-{n:020d}')
        root=Path(__file__).resolve().parents[1]
        tree=ast.parse((root/'web_portal.py').read_text())
        start=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='start_web_server')
        nodes=[n for n in start.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in ('signature','make_form_token','valid_form_token','Handler')]
        globals_to_copy=('page','city_run_section','Limiter')
        top=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in globals_to_copy or isinstance(n,ast.ClassDef) and n.name=='Limiter']
        css=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='CSS' for t in n.targets))
        accounts=SimpleNamespace(owner=lambda token: token if token in ('jamie','ash') else None)
        self.ns=dict(globals(),CSS=css,db=self.case.db,accounts=accounts,city_run=self.case.city,trades=self.case.trades,
                     form_secret=secrets.token_bytes(32),MAX_REQUESTS_PER_MINUTE=100,
                     LOGO_IMAGE=(root/'city-run-board-v4.png').read_bytes(),
                     CITY_RUN_BOARD_IMAGE=(root/'city-run-board-v4.png').read_bytes(),
                     __file__=str(root/'web_portal.py'),login_page=lambda names,message='': '<h1>Login required</h1>'+html.escape(message))
        exec(compile(ast.Module(body=top,type_ignores=[]),'web_portal.py','exec'),self.ns)
        self.ns['limiter']=self.ns['Limiter']()
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'web_portal.py','exec'),self.ns)
        Base=self.ns['Handler'];ns=self.ns
        class Handler(Base):
            def do_GET(self):
                if self.path=='/qa-account':
                    content=ns['city_run_section']({'customer_key':'jamie'},ns['city_run'],ns['make_form_token']('jamie'))
                    self.send_html(200,ns['page']('City Run','<div id="customer-app">'+content+'</div>'))
                elif self.path=='/qa-frame':
                    self.send_html(200,'<html><body style="margin:0;background:#333"><iframe title="Phone" src="/city-run-trades" style="border:0;width:390px;height:740px"></iframe></body></html>')
                else:super().do_GET()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def close(self):
        self.server.shutdown();self.server.server_close();self.case.doCleanups()

if __name__=='__main__':
    f=Fixture();print(f.base,flush=True)
    try:
        while True:time.sleep(60)
    finally:f.close()
