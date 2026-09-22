import ast
import copy
import html
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from snr_core import SNRDatabase, utc_now
from city_run import CityRunStore, BUSINESSES, queue_completions
from city_trades import TradeStore
from city_dashboard import dashboard, DASHBOARD_JS
from city_notifications import event_style

class CityOverviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = SNRDatabase(self.tmp.name+'/test.db')
        self.city = CityRunStore(self.db)
        self.trades = TradeStore(self.city)
        self.now = utc_now()
        with self.db.connect() as c:
            self.db._ensure_customer(c, 'Jamie')
            self.db._ensure_customer(c, 'Ash')
            c.execute('CREATE TABLE discord_alert_channels(alert_type TEXT,guild_id TEXT,channel_id TEXT,updated_at TEXT)')
            c.execute("INSERT INTO discord_alert_channels VALUES('city_run_claims','1','2',?)",(self.now,))
            c.execute("UPDATE city_run_inventory SET rarity='common'")
        self.city.activate('1','Owner')

    def card(self, key, copies=1, owner='jamie', first=None):
        with self.db.connect() as c:
            c.execute('INSERT OR REPLACE INTO city_run_customer_cards VALUES(1,?,?,?,?,?)',(owner,key,copies,first or self.now,self.now))

    def tokens(self, n):
        with self.db.connect() as c:
            c.execute("INSERT OR REPLACE INTO city_run_tokens VALUES(1,'jamie','Jamie',?,?,0,?)",(n,n,self.now))

    def route(self):
        for b in BUSINESSES:
            if b['collection_key']=='food': self.card(b['key'])

    def board(self):
        return self.city.customer_board('Jamie')

    def markup(self): return dashboard(self.board(),'x'*24)

    def test_blue_light_discord_quantity(self):
        result = self.db.record_sale_quantity('Jamie', 'blue_light', 3, '1', 'Staff')
        self.assertEqual(result['city_run_stickers_awarded'], 3)
        self.assertEqual(self.board()['available_reveals'], 3)
        self.assertEqual(result['loyalty_awarded'], 0)

    def test_blue_light_vip_exactly_one_and_replay(self):
        with self.db.connect() as c:
            c.execute("UPDATE customers SET vip_override='Gold' WHERE customer_key='jamie'")
        self.db.record_sale('Jamie', 'blue_light', '1', 'Staff', source_ref='blue-test')
        self.db.record_sale('Jamie', 'blue_light', '1', 'Staff', source_ref='blue-test')
        self.assertEqual(self.board()['available_reveals'], 1)

    def test_blue_light_paused_no_stickers(self):
        self.city.set_status('paused', '1', 'Owner')
        result = self.db.record_sale_quantity('Jamie', 'blue_light', 1, '1', 'Staff')
        self.assertEqual(result['city_run_stickers_awarded'], 0)

    def test_empty_collection(self):
        page=self.markup()
        self.assertIn('View My Collection',page)
        self.assertIn('Missing businesses · 38',page)
        self.assertIn('End date to be announced',page)

    def test_reveal_button_and_idempotent_reveal(self):
        self.tokens(2)
        self.assertIn('Open My Stickers',self.markup())
        one=self.city.reveal_one('Jamie','x'*24)
        two=self.city.reveal_one('Jamie','x'*24)
        self.assertEqual(one['id'],two['id'])
        self.assertEqual(self.board()['available_reveals'],1)

    def test_reward_button_and_pending_claim(self):
        self.route()
        self.tokens(0) # corner bonuses may be issued by board reads
        self.board()
        self.tokens(0)
        self.assertIn('Claim Route Reward',self.markup())
        claim=self.city.request_reward('Jamie','food')
        self.assertEqual(claim['status'],'pending')
        self.assertIn('Awaiting staff',self.markup())
        self.assertNotIn('Claim Route Reward',self.markup())

    def test_cancelled_claim_can_be_requested_again(self):
        self.route()
        claim=self.city.request_reward('Jamie','food')
        self.city.resolve_claim(claim['id'],'cancelled','1','Owner')
        self.tokens(0)
        self.assertIn('Claim Route Reward',self.markup())
        again=self.city.request_reward('Jamie','food')
        self.assertEqual(again['id'],claim['id'])
        self.assertEqual(again['status'],'pending')

    def test_failed_award_rolls_back_and_queues_one_alert(self):
        self.tokens(2)
        with self.assertLogs(level='ERROR'), patch('city_run.queue_completions', side_effect=RuntimeError('test failure')):
            with self.assertRaises(ValueError): self.city.reveal_one('Jamie','x'*24)
        self.assertEqual(self.board()['available_reveals'],2)
        self.assertEqual(self.board()['unique_collected'],0)
        errors=[e for e in self.city.pending_events() if e['event_key'].startswith('error:')]
        self.assertEqual(len(errors),1)
        self.assertNotIn('test failure',errors[0]['description'])

    def test_duplicate_marketplace_and_recent_order(self):
        self.card('food-1',3,first='2026-01-01T00:00:00+00:00')
        self.card('food-2',first='2026-09-01T00:00:00+00:00')
        self.assertEqual(self.board()['duplicates'],2)
        self.assertIn('Visit Marketplace',self.markup())
        self.assertIn('2 of 38',self.markup())

    def test_trade_activity_private_and_events(self):
        self.card('food-1',2)
        self.card('food-2',2,'ash')
        self.trades.create('jamie','food-1','food-2','x'*24)
        self.assertEqual(self.board()['marketplace']['mine'],1)
        with self.db.connect() as c: ident=c.execute('SELECT id FROM city_run_trades').fetchone()[0]
        self.trades.resolve('ash',ident,'accept')
        self.assertEqual(self.board()['marketplace']['completed'],1)
        self.assertEqual(self.board()['unique_collected'],2)
        self.assertTrue(any(e['event_key']==f'trade:{ident}' for e in self.city.pending_events()))
        self.assertEqual(self.city.marketplace_activity(1,'someone-else')['recent'],[])

    def test_expired_offers_excluded(self):
        self.card('food-1',2)
        self.trades.create('jamie','food-1','food-2','x'*24)
        with self.db.connect() as c: c.execute("UPDATE city_run_trades SET expires_at='2000-01-01T00:00:00+00:00'")
        m=self.board()['marketplace']
        self.assertEqual(m['open'],0)
        self.assertEqual(m['recent'][0]['status'],'expired')

    def test_completions_deduplicated(self):
        for b in BUSINESSES: self.card(b['key'])
        with self.db.connect() as c:
            queue_completions(c,1,'jamie',self.now)
            queue_completions(c,1,'jamie',self.now)
        keys=[e['event_key'] for e in self.city.pending_events()]
        self.assertEqual(sum(k.startswith('set:') for k in keys),8)
        self.assertEqual(sum(k.startswith('full:') for k in keys),1)
        self.assertEqual(sum(k.startswith('grand:') for k in keys),1)

    def test_issued_reward_and_season_events(self):
        self.route()
        claim=self.city.request_reward('Jamie','food')
        self.city.resolve_claim(claim['id'],'fulfilled','1','Owner')
        self.assertIn('Reward issued',self.markup())
        self.city.set_status('ended','1','Owner')
        keys=[e['event_key'] for e in self.city.pending_events()]
        self.assertIn('season-start:1',keys)
        self.assertIn('season-end:1',keys)
        self.assertTrue(any(':fulfilled:' in k for k in keys))
        self.assertIn('SEASON ENDED',self.markup())
        self.assertNotIn('Open My Stickers',self.markup())

    def test_paused_hides_reveal(self):
        self.tokens(3)
        self.city.set_status('paused','1','Owner')
        self.assertIn('SEASON PAUSED',self.markup())
        self.assertNotIn('Open My Stickers',self.markup())

    def test_countdown_timezone_and_bad_date(self):
        with self.db.connect() as c: c.execute("UPDATE city_run_campaigns SET ends_at='2030-10-01T20:00:00+01:00'")
        self.assertIn('19:00 UTC',self.markup())
        self.assertIn('data-city-countdown',self.markup())
        with self.db.connect() as c: c.execute("UPDATE city_run_campaigns SET ends_at='bad'")
        self.assertIn('End date to be confirmed',self.markup())

    def test_unowned_zero_copy_rows_do_not_count(self):
        self.card('food-1',0)
        self.assertEqual(self.board()['unique_collected'],0)

    def test_escaping(self):
        b=self.board()
        b['collections'][0]['items'][0]['name']='<script>bad()</script>'
        self.assertNotIn('<script>bad()',dashboard(b,'" onfocus="bad'))
        self.assertIn('&lt;script&gt;',dashboard(b,'safe'))

    def test_scheduled_end_and_one_notification(self):
        self.city.set_end_date('2030-10-01T20:00:00+01:00','1','Owner')
        self.assertEqual(self.city.current()['ends_at'],'2030-10-01T19:00:00+00:00')
        self.tokens(2)
        with self.db.connect() as c: c.execute("UPDATE city_run_campaigns SET ends_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual(self.city.current()['status'],'ended')
        self.city.current()
        self.assertEqual(sum(e['event_key']=='season-end:1' for e in self.city.pending_events()),1)
        with self.assertRaises(ValueError): self.city.reveal_one('Jamie','x'*24)
        self.assertEqual(self.board()['available_reveals'],2)
        with self.assertRaises(ValueError): self.city.set_end_date('2030-01-01T00:00:00+00:00','1','Owner')

    def test_claim_embeds(self):
        import discord
        tree=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='city_run_claim_embed')
        scope={'discord':discord}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'bot.py','exec'),scope)
        row={'id':1,'customer_name':'Jamie','reward_name':'Meal','reward_description':'Verified route reward'}
        for status,title in [('pending','Reward Awaiting Staff'),('fulfilled','Reward Issued'),('cancelled','Reward Cancelled')]:
            embed=scope['city_run_claim_embed'](dict(row,status=status))
            self.assertEqual(embed.title,title)
            self.assertLess(len(embed),6000)

    def test_notification_styles(self):
        expected={'set':'Route Completed','full':'Full Set Completed','grand':'Grand Prize Completed','trade':'Marketplace Trade Completed','error':'System Error / Sticker Failed','season-start':'Season Started','season-end':'Season Ended'}
        for key,title in expected.items(): self.assertEqual(event_style(key+':1')[0],title)
        self.assertEqual(event_style('claim:1:fulfilled:date')[0],'Reward Issued')

if __name__=='__main__': unittest.main()
