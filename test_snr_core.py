import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from snr_core import SNRDatabase, birdy_post


class TestSNRCore(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = SNRDatabase(str(Path(self.temp.name) / "test.db"), jackpot_pool_size=1000)

    def tearDown(self):
        self.temp.cleanup()

    def test_sale_awards_correct_values(self):
        result = self.db.record_sale("cody ortega", "share_box", "1", "Staff")
        customer = result["customer"]
        self.assertEqual(customer["display_name"], "Cody Ortega")
        self.assertEqual(customer["loyalty_points"], 2)
        self.assertEqual(customer["golden_tickets"], 4)
        self.assertEqual(customer["revenue"], 1200)
        self.assertEqual(customer["food_sold"], 10)
        self.assertEqual(customer["drinks_sold"], 10)

    def test_four_points_create_card_reward(self):
        for _ in range(4):
            result = self.db.record_sale("Ash", "loyalty", "1", "Staff")
        self.assertEqual(result["customer"]["loyalty_points"], 0)
        self.assertEqual(len(result["card_reward_codes"]), 1)
        rewards = self.db.unclaimed_rewards("ASH")
        self.assertEqual(rewards[0]["reward_type"], "card_pack")

    def test_jackpot_is_awarded_and_resets(self):
        with self.db.connect() as conn:
            conn.execute("UPDATE jackpot SET winning_position = 1, tickets_issued = 0 WHERE id = 1")
        result = self.db.record_sale("Lola", "loyalty", "1", "Staff")
        self.assertTrue(result["jackpot_won"])
        self.assertEqual(result["customer"]["jackpot_wins"], 1)
        self.assertEqual(self.db.jackpot_status()["cycle"], 2)
        reward = self.db.unclaimed_rewards("Lola")[0]
        self.assertIn("£5,000 cash", reward["description"])

    def test_claim_reward(self):
        with self.db.connect() as conn:
            conn.execute("UPDATE jackpot SET winning_position = 1, tickets_issued = 0 WHERE id = 1")
        result = self.db.record_sale("Jamie", "loyalty", "1", "Staff")
        claimed = self.db.claim_reward(result["jackpot_reward_code"], "1", "Staff")
        self.assertEqual(claimed["status"], "claimed")

    def test_name_suggestion(self):
        self.db.record_sale("Cody Ortega", "loyalty", "1", "Staff")
        self.assertEqual(self.db.suggest_name("cody ortega"), "Cody Ortega")
        self.assertEqual(self.db.suggest_name("cody ortga"), "Cody Ortega")

    def test_birdy_deal_uses_locked_prices(self):
        post = birdy_post("deal", "share_box")
        self.assertIn("£1,200", post)
        self.assertIn("10 FOOD + 10 DRINKS", post)
        self.assertIn("£5,000 cash jackpot", post)


if __name__ == "__main__":
    unittest.main()
