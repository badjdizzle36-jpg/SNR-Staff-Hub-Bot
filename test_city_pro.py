import tempfile
import unittest
from pathlib import Path
from city_artwork import SLOTS, poster_markup, sticker_svg
from city_run import BUSINESSES, queue_completions
from test_city_run import CityRunTests


class ArtworkTests(unittest.TestCase):
    def test_rendered_city_page_has_one_poster_no_blue_board(self):
        from unittest.mock import Mock
        from web_portal import city_run_section
        store=Mock()
        store.customer_board.return_value={'campaign':{'status':'active'},'collections':[],
            'available_reveals':0,'unique_collected':0,'duplicates':0,'corners':[]}
        page=city_run_section({'customer_key':'tester'},store,'x'*24)
        self.assertEqual(page.count('data-board-build="poster-pro-4"'),1)
        self.assertNotIn('class="city-board-viewport"',page)
        self.assertLess(page.index('poster-pro-4'),page.index('Board corners'))

    def test_every_business_has_explicit_geometry(self):
        self.assertEqual(set(SLOTS), {b['key'] for b in BUSINESSES})
        self.assertEqual(SLOTS['shops-4'][0][0], 1086)
        self.assertEqual(SLOTS['finance-4'][0][0], 425)
        for boxes in SLOTS.values():
            for x,y,w,h in boxes:
                self.assertLessEqual(x+w,1254)
                self.assertLessEqual(y+h,1254)
        self.assertEqual(len(SLOTS['services-4']), 2)

    def test_poster_remains_single_and_marks_duplicates(self):
        markup=poster_markup({'food-5','shops-4'})
        self.assertNotIn('city-board-viewport', markup)
        self.assertEqual(markup.count('class="poster-collected"'),2)
        self.assertIn('collected-grey',markup)
        self.assertNotIn('<text', markup)
        self.assertEqual(markup.count('class="poster-tick"'), 2)

    def test_art_is_business_specific(self):
        self.assertIsNone(sticker_svg('invalid','Unknown'))
        self.assertIn(b'data:image/png;base64,',sticker_svg('shops-4','Pharmacy'))
        self.assertNotEqual(sticker_svg('shops-4','Pharmacy'),sticker_svg('mechanics-5','Route 68'))

    def test_uwu_has_one_record_and_one_collectible_tile(self):
        self.assertEqual(len([b for b in BUSINESSES if b['name'] == 'UwU Cafe']), 1)
        self.assertEqual(len(SLOTS['food-5']), 1)
        self.assertEqual(set(SLOTS), {b['key'] for b in BUSINESSES})
        page = poster_markup(['food-5', 'food-5'])
        self.assertEqual(page.count('class="poster-collected"'), 1)
        self.assertNotIn('city-food-progress', page)
        self.assertNotIn('/5</text>', poster_markup([f'food-{i}' for i in range(1, 6)]))


class EventTests(CityRunTests):
    def test_paid_cart_awards_six_stickers_once(self):
        from customer_accounts import Accounts
        from delivery_orders import DeliveryStore
        accounts=Accounts(self.db)
        code=accounts.issue_setup('Cart Customer','1','Staff')
        accounts.set_password('Cart Customer',code,'correct horse 123')
        orders=DeliveryStore(self.db)
        orders.configure(100,200,'1','Manager')
        self._activate_test_campaign()
        row=orders.create_cart_authenticated('Cart Customer',{'mega_deal':2,'share_box':2},'',
            'cart-sticker-request-12345',fulfillment_type='instore')
        self.assertEqual(self.city.customer_board('Cart Customer')['available_reveals'],0)
        orders.resolve(row['id'],'paid','2','Counter Staff')
        orders.resolve(row['id'],'paid','2','Counter Staff')
        self.assertEqual(self.city.customer_board('Cart Customer')['available_reveals'],6)
        self.assertEqual(self.db.get_customer('Cart Customer')['loyalty_points'],0)
        self.assertEqual(self.db.report()['sales'],4)

    def test_reveal_triggers_completion_without_sale(self):
        from unittest.mock import patch
        self.db.record_sale('Reveal Customer','share_box','1','Staff')
        campaign=self._activate_test_campaign()
        with self.db.connect() as conn:
            for b in BUSINESSES:
                if b['collection_key']=='food' and b['key']!='food-1':
                    conn.execute('INSERT INTO city_run_customer_cards VALUES(?,?,?,?,?,?)',(campaign['id'],'reveal customer',b['key'],1,'now','now'))
        with patch('city_run._weighted_choice',side_effect=lambda rows,owned:next(r for r in rows if r['business_key']=='food-1')):
            first=self.city.reveal_one('Reveal Customer','unique-reveal-request-123')
            again=self.city.reveal_one('Reveal Customer','unique-reveal-request-123')
        self.assertEqual(first['id'],again['id'])
        self.assertEqual(len(self.city.pending_events()),1)

    def test_set_queue_is_idempotent_and_persistent(self):
        self.db.record_sale('Queue Customer','mega_deal','1','Staff')
        campaign=self._activate_test_campaign()
        with self.db.connect() as conn:
            for b in BUSINESSES:
                if b['collection_key']=='food':
                    conn.execute('INSERT INTO city_run_customer_cards VALUES(?,?,?,?,?,?)',(campaign['id'],'queue customer',b['key'],1,'now','now'))
            queue_completions(conn,campaign['id'],'queue customer','now')
            queue_completions(conn,campaign['id'],'queue customer','now')
        events=self.city.pending_events()
        self.assertEqual(len(events),1)
        self.assertIn('Food & Cafes',events[0]['description'])
        self.assertEqual(self.city.pending_events(),events)
        self.city.mark_event_sent(events[0]['id'],123)
        self.assertEqual(self.city.pending_events(),[])

    def test_reward_handover_queued_once(self):
        self.test_set_queue_is_idempotent_and_persistent()
        claim=self.city.request_reward('Queue Customer','food')
        self.city.resolve_claim(claim['id'],'fulfilled','1','Staff')
        events=self.city.pending_events()
        self.assertTrue(any('fulfilled by Staff' in e['description'] for e in events))
        with self.assertRaises(ValueError):
            self.city.resolve_claim(claim['id'],'fulfilled','1','Staff')

if __name__=='__main__':
    unittest.main()
