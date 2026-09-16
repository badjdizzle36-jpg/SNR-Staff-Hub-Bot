import os
import tempfile
import unittest

from city_run import BUSINESSES, COLLECTIONS, REWARD_KEYS, CityRunStore
from alert_channels import AlertChannels
from snr_core import SNRDatabase


class CityRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = SNRDatabase(os.path.join(self.temp.name, "snr.db"), jackpot_pool_size=1000)
        self.city = CityRunStore(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_all_confirmed_businesses_are_unique_and_campaign_starts_draft(self):
        self.assertEqual(len(BUSINESSES), 38)
        self.assertEqual(len({row["name"] for row in BUSINESSES}), 38)
        self.assertEqual(sum(len(row["businesses"]) for row in COLLECTIONS.values()), 38)
        status = self.city.current()
        self.assertEqual(status["status"], "draft")
        self.assertEqual(status["business_count"], 38)
        self.assertEqual(status["configured_rewards"], len(REWARD_KEYS))

    def test_draft_campaign_never_issues_reveals(self):
        sale = self.db.record_sale("Test Customer", "mega_deal", "1", "Staff")
        self.assertEqual(sale["city_run_reveals_awarded"], 0)
        self.assertEqual(self.city.customer_board("test customer")["available_reveals"], 0)

    def _activate_test_campaign(self):
        for row in BUSINESSES:
            self.city.configure_piece(row["key"], "common", None, "owner", "Owner")
        AlertChannels(self.db).configure(
            "city_run_claims", "200", "1", "owner", "Owner")
        return self.city.activate("owner", "Owner")

    def test_existing_points_carry_over_and_new_points_create_digital_reveals(self):
        self.db.record_sale("Jamie Test", "share_box", "1", "Staff")
        active = self._activate_test_campaign()
        self.assertEqual(active["status"], "active")
        board = self.city.customer_board("jamie test")
        self.assertEqual(board["available_reveals"], 2)
        sale = self.db.record_sale("Jamie Test", "mega_deal", "1", "Staff", source_ref="test:city:1")
        self.assertEqual(sale["city_run_reveals_awarded"], 1)
        board = self.city.customer_board("jamie test")
        self.assertEqual(board["available_reveals"], 3)
        revealed = self.city.reveal_one("Jamie Test", "test-reveal-request-0001")
        self.assertIn("business_name", revealed)
        self.assertEqual(revealed["reveals_left"], 2)
        repeated = self.city.reveal_one("Jamie Test", "test-reveal-request-0001")
        self.assertEqual(repeated["id"], revealed["id"])
        self.assertEqual(repeated["reveals_left"], 2)
        board = self.city.customer_board("jamie test")
        self.assertEqual(board["available_reveals"], 2)
        self.assertEqual(board["unique_collected"], 1)

    def test_idempotent_sale_does_not_issue_a_second_reveal_credit(self):
        self._activate_test_campaign()
        first = self.db.record_sale("Jamie Test", "mega_deal", "1", "Staff", source_ref="test:city:2")
        second = self.db.record_sale("Jamie Test", "mega_deal", "1", "Staff", source_ref="test:city:2")
        self.assertEqual(first["city_run_reveals_awarded"], second["city_run_reveals_awarded"])
        self.assertEqual(self.city.customer_board("jamie test")["available_reveals"], 1)

    def test_share_box_quantity_aggregates_stickers(self):
        self._activate_test_campaign()
        result = self.db.record_sale_quantity("Jamie Test", "share_box", 2, "1", "Staff")
        self.assertEqual(result["city_run_stickers_awarded"], 4)
        self.assertEqual(result["city_run_reveals_awarded"], 4)
        self.assertEqual(self.city.customer_board("jamie test")["available_reveals"], 4)

    def test_undo_removes_unused_reveal_but_refuses_a_spent_one(self):
        self._activate_test_campaign()
        result = self.db.record_sale_quantity("Jamie Test", "mega_deal", 1, "1", "Staff")
        undone = self.db.undo_counter_sale_batch(
            result["batch_ref"], [self.db.latest_counter_sale_batch()["sale_ids"][0]], "owner", "Owner")
        self.assertEqual(undone["city_run_reveals_removed"], 1)
        result = self.db.record_sale_quantity("Jamie Test", "mega_deal", 1, "1", "Staff")
        self.city.reveal_one("Jamie Test")
        latest = self.db.latest_counter_sale_batch()
        with self.assertRaisesRegex(ValueError, "already been used"):
            self.db.undo_counter_sale_batch(result["batch_ref"], latest["sale_ids"], "owner", "Owner")

    def test_recommended_rarities_and_verified_collection_claim(self):
        self.db.record_sale("Jamie Test", "mega_deal", "1", "Staff")
        configured = self.city.apply_recommended_rarities("owner", "Owner")
        self.assertEqual(configured["configured_pieces"], 38)
        AlertChannels(self.db).configure(
            "city_run_claims", "200", "1", "owner", "Owner")
        self.city.activate("owner", "Owner")
        with self.db.connect() as conn:
            campaign_id = self.city.current()["id"]
            for business in [row for row in BUSINESSES if row["collection_key"] == "food"]:
                conn.execute(
                    """INSERT INTO city_run_customer_cards
                       (campaign_id,customer_key,business_key,copies_owned,first_collected_at,updated_at)
                       VALUES(?,?,?,1,'now','now')""",
                    (campaign_id, "jamie test", business["key"]),
                )
        claim = self.city.request_reward("Jamie Test", "food")
        self.assertEqual(claim["status"], "pending")
        self.assertEqual(claim["channel_id"], "200")
        fulfilled = self.city.resolve_claim(claim["id"], "fulfilled", "1", "Staff")
        self.assertEqual(fulfilled["status"], "fulfilled")


if __name__ == "__main__":
    unittest.main()
