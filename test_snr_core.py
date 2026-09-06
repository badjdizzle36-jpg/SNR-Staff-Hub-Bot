import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from snr_core import (
    AVERAGE_DESSERT_COST,
    AVERAGE_DRINK_COST,
    AVERAGE_FOOD_COST,
    SNRDatabase,
    birdy_post,
    vip_level_for_sales,
)


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

    def test_loyalty_points_keep_building_without_card_reward(self):
        for _ in range(4):
            result = self.db.record_sale("Ash", "mega_deal", "1", "Staff")
        self.assertEqual(result["customer"]["loyalty_points"], 4)
        self.assertEqual(len(result["card_reward_codes"]), 0)
        rewards = self.db.unclaimed_rewards("ASH")
        self.assertEqual(rewards, [])

    def test_jackpot_is_awarded_and_resets(self):
        with self.db.connect() as conn:
            conn.execute("UPDATE jackpot SET winning_position = 1, tickets_issued = 0 WHERE id = 1")
        result = self.db.record_sale("Lola", "mega_deal", "1", "Staff")
        self.assertTrue(result["jackpot_won"])
        self.assertEqual(result["customer"]["jackpot_wins"], 1)
        self.assertEqual(self.db.jackpot_status()["cycle"], 2)
        reward = self.db.unclaimed_rewards("Lola")[0]
        self.assertIn("£5,000 cash", reward["description"])

    def test_claim_reward(self):
        with self.db.connect() as conn:
            conn.execute("UPDATE jackpot SET winning_position = 1, tickets_issued = 0 WHERE id = 1")
        result = self.db.record_sale("Jamie", "mega_deal", "1", "Staff")
        claimed = self.db.claim_reward(result["jackpot_reward_code"], "1", "Staff")
        self.assertEqual(claimed["status"], "claimed")

    def test_name_suggestion(self):
        self.db.record_sale("Cody Ortega", "mega_deal", "1", "Staff")
        self.assertEqual(self.db.suggest_name("cody ortega"), "Cody Ortega")
        self.assertEqual(self.db.suggest_name("cody ortga"), "Cody Ortega")

    def test_birdy_deal_uses_locked_prices(self):
        post = birdy_post("deal", "share_box")
        self.assertIn("£1,200", post)
        self.assertIn("10 FOOD + 10 DRINKS", post)
        self.assertIn("£5,000 cash jackpot", post)

    def test_menu_matches_the_snr_meal_board(self):
        from snr_core import DEALS

        self.assertEqual(
            list(DEALS),
            ["quick_fix", "happy_meal", "sweet_treat", "mega_deal", "blue_light", "share_box"],
        )

    def test_finance_report_calculates_cost_profit_and_margin(self):
        self.db.record_sale("Cody", "quick_fix", "1", "Staff")
        self.db.record_sale("Cody", "sweet_treat", "1", "Staff")
        report = self.db.report()
        self.assertEqual(report["revenue"], 550)
        self.assertEqual(report["production_cost"], 37.38)
        self.assertEqual(report["gross_profit"], 512.62)
        self.assertEqual(report["profit_margin"], 93.2)

    def test_supplied_category_averages(self):
        self.assertEqual(round(AVERAGE_FOOD_COST, 2), 6.65)
        self.assertEqual(round(AVERAGE_DRINK_COST, 2), 2.90)
        self.assertEqual(round(AVERAGE_DESSERT_COST, 2), 5.57)

    def test_vip_levels_bonuses_and_owner_override(self):
        self.assertEqual(vip_level_for_sales(0)["name"], "Regular")
        self.assertEqual(vip_level_for_sales(10)["name"], "Bronze")
        self.assertEqual(vip_level_for_sales(25)["name"], "Silver")
        self.assertEqual(vip_level_for_sales(50)["name"], "Gold")
        self.assertEqual(vip_level_for_sales(100)["name"], "Platinum")
        self.assertEqual(vip_level_for_sales(200)["name"], "SNR VIP")
        for _ in range(25):
            result = self.db.record_sale("Member", "quick_fix", "1", "Staff")
        self.assertEqual(result["customer"]["membership"]["name"], "Silver")
        self.assertEqual(result["loyalty_awarded"], 0)
        self.assertEqual(result["tickets_awarded"], 2)
        customer = self.db.set_vip_override("Member", "SNR VIP", "99", "Owner")
        self.assertTrue(customer["membership"]["manual"])
        result = self.db.record_sale("Member", "quick_fix", "1", "Staff")
        self.assertEqual(result["loyalty_awarded"], 2)
        self.assertEqual(result["tickets_awarded"], 4)
        customer = self.db.set_vip_override("Member", "Automatic", "99", "Owner")
        self.assertEqual(customer["membership"]["name"], "Silver")
        self.assertFalse(customer["membership"]["manual"])


if __name__ == "__main__":
    unittest.main()
