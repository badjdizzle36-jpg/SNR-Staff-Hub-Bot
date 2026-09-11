import tempfile
import unittest
from pathlib import Path

from customer_services import CustomerServices
from delivery_orders import DeliveryStore
from snr_core import SNRDatabase


class CustomerServicesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = SNRDatabase(str(Path(self.temp.name) / "test.db"), jackpot_pool_size=1000)
        self.orders = DeliveryStore(self.db)
        self.orders.configure("123", "456", "1", "Owner")
        self.services = CustomerServices(self.db)
        with self.db.connect() as conn:
            conn.execute("""INSERT INTO customers
                (customer_key,display_name,loyalty_points,lifetime_sales,created_at,updated_at)
                VALUES('cody ortega','Cody Ortega',20,1,'now','now')""")

    def tearDown(self):
        self.temp.cleanup()

    def test_reward_approval_deducts_exact_points_and_creates_voucher(self):
        request = self.services.request_reward("Cody Ortega", "FREE_DRINK", "request-token-12345")
        self.assertEqual(request["status"], "pending")
        self.assertEqual(self.db.get_customer("Cody Ortega")["loyalty_points"], 20)
        approved = self.services.resolve_reward(request["id"], "approved", "2", "Ash")
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(self.db.get_customer("Cody Ortega")["loyalty_points"], 16)
        vouchers = self.services.vouchers("Cody Ortega", active_only=True)
        self.assertEqual(len(vouchers), 1)
        used = self.services.redeem_voucher(vouchers[0]["voucher_code"], "2", "Ash")
        self.assertEqual(used["status"], "used")

    def test_live_action_round_trip(self):
        action = self.services.create_action(
            "Cody Ortega", "staff_help", None, "Please come to the counter", "action-token-12345")
        self.assertEqual(action["status"], "pending")
        resolved = self.services.resolve_action(action["id"], "resolved", "2", "Ash", "Coming now")
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["staff_response"], "Coming now")

    def test_free_delivery_voucher_is_reserved_and_restored_if_order_cancelled(self):
        request = self.services.request_reward("Cody Ortega", "FREE_DELIVERY", "delivery-reward-12345")
        self.services.resolve_reward(request["id"], "approved", "2", "Ash")
        voucher = self.services.vouchers("Cody Ortega", active_only=True)[0]
        order = self.orders.create_cart_authenticated(
            "Cody Ortega", {"mega_deal": 1}, "Legion Square", "delivery-order-12345",
            fulfillment_type="delivery", voucher_code=voucher["voucher_code"])
        self.assertEqual(order["delivery_fee"], 0)
        self.assertEqual(self.services.vouchers("Cody Ortega")[0]["status"], "reserved")
        self.orders.resolve(order["id"], "cancelled", "2", "Ash", allow_override=True)
        self.assertEqual(self.services.vouchers("Cody Ortega", active_only=True)[0]["status"], "active")


if __name__ == "__main__":
    unittest.main()
