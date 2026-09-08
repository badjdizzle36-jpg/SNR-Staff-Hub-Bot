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

    def test_multiple_identical_deals_are_recorded_in_one_staff_action(self):
        result = self.db.record_sale_quantity("Cody Ortega", "share_box", 2, "1", "Staff")
        customer = result["customer"]
        self.assertEqual(result["quantity"], 2)
        self.assertEqual(len(result["transaction_ids"]), 2)
        self.assertEqual(result["loyalty_awarded"], 4)
        self.assertEqual(result["tickets_awarded"], 8)
        self.assertEqual(customer["lifetime_sales"], 2)
        self.assertEqual(customer["revenue"], 2400)
        self.assertEqual(customer["food_sold"], 20)
        self.assertEqual(customer["drinks_sold"], 20)
        self.assertEqual(self.db.report()["sales"], 2)

        with self.assertRaises(ValueError):
            self.db.record_sale_quantity("Cody Ortega", "share_box", 0, "1", "Staff")

    def test_recent_customers_and_owner_undo_reverse_one_quantity_action(self):
        with self.db.connect() as conn:
            conn.execute("UPDATE jackpot SET winning_position=1000,tickets_issued=0 WHERE id=1")
        self.db.record_sale_quantity("First Customer", "quick_fix", 1, "1", "Staff")
        result = self.db.record_sale_quantity("Recent Customer", "share_box", 2, "1", "Staff")
        self.assertEqual(self.db.recent_customer_names(2), ["Recent Customer", "First Customer"])
        latest = self.db.latest_counter_sale_batch()
        self.assertEqual(latest["quantity"], 2)
        self.assertEqual(latest["deal_key"], "share_box")
        self.assertEqual(latest["sale_ids"], [int(value.split("-")[1]) for value in result["transaction_ids"]])
        undone = self.db.undo_counter_sale_batch(
            latest["batch_ref"], latest["sale_ids"], "99", "Owner")
        self.assertEqual(undone["quantity"], 2)
        customer = self.db.get_customer("Recent Customer")
        self.assertEqual(customer["loyalty_points"], 0)
        self.assertEqual(customer["golden_tickets"], 0)
        self.assertEqual(customer["lifetime_sales"], 0)
        self.assertEqual(customer["revenue"], 0)
        self.assertEqual(self.db.report()["sales"], 1)
        with self.assertRaisesRegex(ValueError, "already been undone"):
            self.db.undo_counter_sale_batch(
                latest["batch_ref"], latest["sale_ids"], "99", "Owner")

    def test_owner_quick_undo_refuses_a_jackpot_winner(self):
        with self.db.connect() as conn:
            conn.execute("UPDATE jackpot SET winning_position=1,tickets_issued=0 WHERE id=1")
        self.db.record_sale_quantity("Winner", "quick_fix", 1, "1", "Staff")
        latest = self.db.latest_counter_sale_batch()
        self.assertTrue(latest["has_jackpot_winner"])
        with self.assertRaisesRegex(ValueError, "jackpot-winning"):
            self.db.undo_counter_sale_batch(
                latest["batch_ref"], latest["sale_ids"], "99", "Owner")

    def test_discord_quantity_awards_points_for_every_deal_bought(self):
        mega = self.db.record_sale_quantity("Mega Customer", "mega_deal", 2, "1", "Staff")
        self.assertEqual(mega["base_loyalty_awarded"], 2)
        self.assertEqual(mega["membership_loyalty_awarded"], 0)
        self.assertEqual(mega["loyalty_awarded"], 2)
        self.assertEqual(mega["customer"]["loyalty_points"], 2)
        self.assertEqual(mega["customer"]["lifetime_sales"], 2)

        share = self.db.record_sale_quantity("Share Customer", "share_box", 2, "1", "Staff")
        self.assertEqual(share["base_loyalty_awarded"], 4)
        self.assertEqual(share["membership_loyalty_awarded"], 0)
        self.assertEqual(share["loyalty_awarded"], 4)
        self.assertEqual(share["customer"]["loyalty_points"], 4)
        self.assertEqual(share["customer"]["lifetime_sales"], 2)

    def test_all_discord_quantities_multiply_each_deals_base_points(self):
        from snr_core import DEALS

        for deal_key, deal in DEALS.items():
            for amount in (1, 2, 5, 10):
                customer_name = f"{deal_key} quantity {amount}"
                result = self.db.record_sale_quantity(customer_name, deal_key, amount, "1", "Staff")
                expected = deal.loyalty_points * amount
                self.assertEqual(result["base_loyalty_awarded"], expected)
                self.assertEqual(result["loyalty_awarded"], expected)
                self.assertEqual(result["customer"]["loyalty_points"], expected)
                self.assertEqual(result["customer"]["lifetime_sales"], amount)

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
        self.assertEqual(vip_level_for_sales(0)["delivery_fee"], 100)
        self.assertEqual(vip_level_for_sales(50)["delivery_fee"], 50)
        self.assertEqual(vip_level_for_sales(200)["delivery_fee"], 0)
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
