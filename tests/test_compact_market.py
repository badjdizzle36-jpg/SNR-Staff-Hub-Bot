import json,re,sys,unittest
from pathlib import Path
from urllib.request import Request,urlopen,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError
from urllib.parse import urlencode
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from market_http_fixture import Fixture
from compact_market import normal_options

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args):return None

class CompactMarketTests(unittest.TestCase):
    def setUp(self):self.f=Fixture();self.addCleanup(self.f.close)
    def get(self,path,owner='jamie'):
        return urlopen(Request(self.f.base+path,headers={'Cookie':'snr_session='+owner}))
    def post(self,fields,owner='jamie'):
        req=Request(self.f.base+'/city-run-trade',data=urlencode(fields).encode(),headers={'Cookie':'snr_session='+owner,'Content-Type':'application/x-www-form-urlencoded'})
        try:return build_opener(NoRedirect).open(req)
        except HTTPError as error:return error
    def token(self,owner='jamie'):return self.f.ns['make_form_token'](owner)
    def test_public_feed_excludes_own_offers_and_paginates(self):
        self.f.case.trades.create('jamie','food-1','food-2','j'*24)
        data=self.f.case.trades.page_snapshot('jamie')
        self.assertEqual(data['live_total'],7);self.assertEqual(len(data['feed']['rows']),3)
        self.assertEqual(data['feed']['pages'],3)
        self.assertTrue(all(r['maker']=='ash' for r in data['feed']['rows']))
        page2=self.f.case.trades.page_snapshot('jamie',{'page':'2'})
        self.assertFalse({r['id'] for r in data['feed']['rows']} & {r['id'] for r in page2['feed']['rows']})
    def test_authenticated_refresh_and_real_headers(self):
        with self.get('/city-market-feed?tab=live') as r:
            self.assertEqual(r.headers['Cache-Control'],'no-store')
            self.assertEqual(json.load(r)['html'].count('data-offer-id='),3)
        with self.assertRaises(HTTPError) as ctx:self.get('/city-market-feed',owner='unknown')
        self.assertEqual(ctx.exception.code,401)
    def test_create_redirects_to_live_then_activity_cancel(self):
        fields=dict(action='create',offered='food-1',wanted='food-2',request_key='c'*24,city_run_request_key=self.token())
        r=self.post(fields);self.assertEqual(r.code,303);self.assertEqual(r.headers['Location'],'/city-run-trades?tab=live&notice=listed')
        own=self.f.case.trades.page_snapshot('jamie',{'tab':'activity'})['feed']['rows']
        self.assertEqual(len(own),1)
        # Publishing again with the same request key must not reserve twice.
        self.assertEqual(self.post(fields).code,303)
        self.assertEqual(self.f.case.trades.page_snapshot('jamie',{'tab':'activity'})['feed']['total'],1)
        r=self.post(dict(action='cancel',trade_id=own[0]['id'],city_run_request_key=self.token()))
        self.assertEqual(r.code,303)
        self.assertEqual(self.f.case.trades.page_snapshot('jamie',{'tab':'activity'})['feed']['rows'][0]['status'],'cancelled')
    def test_accept_is_atomic_and_retry_does_not_trade_twice(self):
        row=self.f.case.trades.page_snapshot('jamie')['feed']['rows'][0]
        fields=dict(action='accept',trade_id=row['id'],city_run_request_key=self.token())
        self.assertEqual(self.post(fields).code,303)
        board=self.f.case.city.customer_board('jamie')
        self.assertEqual(board['unique_collected'],2)
        before=board['duplicates']
        self.post(fields)
        self.assertEqual(self.f.case.city.customer_board('jamie')['duplicates'],before)
    def test_csrf_and_other_players_cancel_rejected(self):
        self.assertEqual(self.post(dict(action='create',offered='food-1',wanted='food-2',request_key='f'*24,city_run_request_key='invalid')).code,400)
        row=self.f.case.trades.page_snapshot('jamie')['feed']['rows'][0]
        self.assertEqual(self.post(dict(action='cancel',trade_id=row['id'],city_run_request_key=self.token())).code,400)
    def test_pause_rarity_and_empty_pagination(self):
        self.assertEqual(self.f.case.trades.page_snapshot('jamie',{'rarity':'ultra_rare'})['feed']['total'],0)
        self.f.case.city.set_status('paused','1','Owner')
        with self.get('/city-run-trades') as r:
            body=r.read().decode();self.assertNotIn('>Confirm swap</button>',body)
        self.assertEqual(normal_options({'tab':'<script>','page':'bad'}),('live',1,'all'))
    def test_javascript_route_and_no_pointer_capture(self):
        with self.get('/city-marketplace.js') as r:
            source=r.read().decode();self.assertNotIn('setPointerCapture',source)
            self.assertEqual(r.headers['Cache-Control'],'no-store')
        with self.get('/city-run-trades?tab=make&offered=food-1') as r:
            body=r.read().decode();self.assertIn('name="wanted"',body);self.assertNotIn('<select',body)
            self.assertIn('script-src',r.headers['Content-Security-Policy'])
    def test_small_thumbnails_and_traversal_rejection(self):
        with self.get('/city-thumb/food-1.webp') as r:
            self.assertEqual(r.headers['Content-Type'],'image/webp')
            self.assertLess(len(r.read()),25000)
        with self.assertRaises(HTTPError) as ctx:self.get('/city-thumb/../web_portal.webp')
        self.assertEqual(ctx.exception.code,404)

    def test_compact_collection_closed_by_default(self):
        with self.get('/qa-account') as r:
            body=r.read().decode();self.assertNotRegex(body,r'<details[^>]*\bopen\b')
            self.assertIn('id="city-board-top"',body)
            self.assertLess(body.index('id="city-board-top"'),body.index('aria-label="City Run overview"'))

if __name__=='__main__':unittest.main()
