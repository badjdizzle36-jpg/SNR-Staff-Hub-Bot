import tempfile
import unittest
import json
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from customer_accounts import Accounts
from delivery_orders import DeliveryStore
from snr_core import DEALS, SNRDatabase
from web_portal import start_web_server
from staff_shifts import StaffShifts


class HiddenForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("type") == "hidden":
            self.values[attrs["name"]] = attrs.get("value", "")


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SNRDatabase(self.tmp.name + "/data.db")
        self.accounts = Accounts(self.db)
        code = self.accounts.issue_setup("Cody Ortega", "1", "Staff")
        self.session = self.accounts.set_password("Cody Ortega", code, "correct horse 123")
        self.orders = DeliveryStore(self.db)
        self.orders.configure(100, 200, "1", "Manager")

    def tearDown(self):
        self.tmp.cleanup()

    def test_order_uses_server_price_and_requires_location(self):
        with self.assertRaises(ValueError):
            self.orders.create_authenticated("Cody Ortega", "mega_deal", "", "request-key-12345")
        with self.assertRaises(ValueError):
            self.orders.create_authenticated("Cody Ortega", "fake_deal", "Legion Square", "request-key-12345")
        row = self.orders.create_authenticated(
            "Cody Ortega", "share_box", "Postal  123   Legion Square", "request-key-12345"
        )
        self.assertEqual(row["subtotal"], 1200)
        self.assertEqual(row["delivery_fee"], 100)
        self.assertEqual(row["price"], 1300)
        self.assertEqual(row["postal"], "Postal 123 Legion Square")
        self.assertEqual(self.db.report()["sales"], 0)
        self.assertEqual(self.db.get_customer("Cody Ortega")["loyalty_points"], 0)

    def test_paid_order_records_sale_loyalty_and_finance_once(self):
        order = self.orders.create_authenticated(
            "Cody Ortega", "mega_deal", "Postal 505", "paid-request-key"
        )
        self.orders.advance(order["id"], "accepted", "9", "Delivery Staff")
        self.orders.advance(order["id"], "on_way", "9", "Delivery Staff")
        self.orders.advance(order["id"], "arrived", "9", "Delivery Staff")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda _: self.orders.resolve(order["id"], "paid", "9", "Delivery Staff"),
                range(2),
            ))
        self.assertTrue(all(result[0]["status"] == "paid" for result in results))
        report = self.db.report()
        customer = self.db.get_customer("Cody Ortega")
        self.assertEqual(report["sales"], 1)
        self.assertEqual(report["revenue"], 600)
        self.assertEqual(customer["loyalty_points"], 1)
        self.assertEqual(customer["golden_tickets"], 1)
        self.assertEqual(results[0][0]["sale_transaction_id"], results[1][0]["sale_transaction_id"])

    def test_order_is_locked_to_first_driver(self):
        order = self.orders.create_authenticated(
            "Cody Ortega", "mega_deal", "Postal 505", "driver-lock-request"
        )
        accepted = self.orders.advance(order["id"], "accepted", "9", "Driver One")
        self.assertEqual(accepted["assigned_driver_name"], "Driver One")

        with self.assertRaisesRegex(ValueError, "already been accepted by Driver One"):
            self.orders.advance(order["id"], "accepted", "10", "Driver Two")
        with self.assertRaisesRegex(ValueError, "already been accepted by Driver One"):
            self.orders.advance(order["id"], "on_way", "10", "Driver Two")

        current = self.orders.get(order["id"])
        self.assertEqual(current["status"], "accepted")
        self.assertEqual(current["assigned_driver_id"], "9")

    def test_simultaneous_accept_has_exactly_one_winning_driver(self):
        order = self.orders.create_authenticated(
            "Cody Ortega", "share_box", "Postal 808", "simultaneous-driver-lock"
        )

        def accept(driver):
            try:
                row = self.orders.advance(order["id"], "accepted", driver, f"Driver {driver}")
                return ("accepted", row["assigned_driver_id"])
            except ValueError as exc:
                return ("blocked", str(exc))

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(accept, ("9", "10")))

        self.assertEqual(sum(result[0] == "accepted" for result in results), 1)
        self.assertEqual(sum(result[0] == "blocked" for result in results), 1)
        current = self.orders.get(order["id"])
        self.assertIn(current["assigned_driver_id"], ("9", "10"))
        blocked_message = next(result[1] for result in results if result[0] == "blocked")
        self.assertIn(current["assigned_driver_name"], blocked_message)

    def test_only_assigned_driver_can_confirm_payment_but_manager_can_override(self):
        order = self.orders.create_authenticated(
            "Cody Ortega", "mega_deal", "Postal 505", "driver-payment-lock"
        )
        self.orders.advance(order["id"], "accepted", "9", "Driver One")
        self.orders.advance(order["id"], "on_way", "9", "Driver One")
        self.orders.advance(order["id"], "arrived", "9", "Driver One")

        with self.assertRaisesRegex(ValueError, "already been accepted by Driver One"):
            self.orders.resolve(order["id"], "paid", "10", "Driver Two")
        self.assertEqual(self.orders.get(order["id"])["status"], "arrived")
        self.assertEqual(self.db.report()["sales"], 0)

        paid, sales = self.orders.resolve(
            order["id"], "paid", "1", "Manager", allow_override=True)
        self.assertEqual(paid["status"], "paid")
        self.assertEqual(len(sales), 1)
        self.assertEqual(self.db.report()["sales"], 1)

    def test_payment_waits_until_driver_has_arrived(self):
        order = self.orders.create_authenticated(
            "Cody Ortega", "quick_fix", "Postal 707", "arrive-before-payment"
        )
        self.orders.advance(order["id"], "accepted", "9", "Driver One")
        self.orders.advance(order["id"], "on_way", "9", "Driver One")
        with self.assertRaisesRegex(ValueError, "arrived before confirming payment"):
            self.orders.resolve(order["id"], "paid", "9", "Driver One")
        self.assertEqual(self.db.report()["sales"], 0)
        self.orders.advance(order["id"], "arrived", "9", "Driver One")
        paid, sales = self.orders.resolve(order["id"], "paid", "9", "Driver One")
        self.assertEqual(paid["status"], "paid")
        self.assertEqual(len(sales), 1)

    def test_cancelled_order_adds_nothing(self):
        order = self.orders.create_authenticated(
            "Cody Ortega", "quick_fix", "Sandy Hospital", "cancel-request-key"
        )
        row, sale = self.orders.resolve(order["id"], "cancelled", "9", "Delivery Staff")
        self.assertEqual(row["status"], "cancelled")
        self.assertEqual(sale, [])
        self.assertEqual(self.db.report()["sales"], 0)

    def test_logged_in_web_delivery_flow(self):
        StaffShifts(self.db).clock_in("9", "Delivery Staff", "200")
        server = start_web_server(self.db, 0)
        base = f"http://127.0.0.1:{server.server_port}"

        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        browser = build_opener(NoRedirect())
        cookie = "snr_session=" + self.session

        def request(path, values=None):
            headers = {"Cookie": cookie}
            req = Request(
                base + path,
                data=urlencode(values).encode() if values is not None else None,
                headers=headers,
            )
            try:
                response = browser.open(req)
            except HTTPError as error:
                response = error
            return response, response.read().decode()

        try:
            response, body = request("/account")
            self.assertEqual(response.status, 200)
            self.assertIn("SNR Customer Membership", body)
            self.assertIn("Regular", body)
            self.assertIn("Membership delivery: £100", body)
            self.assertIn('name="discount_code"', body)
            self.assertIn("entered automatically", body)
            self.assertNotIn("Owned by", body)
            for deal in DEALS.values():
                self.assertIn(deal.name, body)
                self.assertIn(f"£{deal.price:,}", body)
            parser = HiddenForm()
            parser.feed(body)
            values = {
                "order_request_key": parser.values["order_request_key"],
                "qty_blue_light": "1",
                "postal": "Postal 401 Mission Row",
                "notes": "Meet outside and call me",
            }
            response, confirmation = request("/order", values)
            self.assertEqual(response.status, 200)
            self.assertIn("Food subtotal: <strong>£600", confirmation)
            self.assertIn("Regular delivery: <strong>£100", confirmation)
            self.assertIn("Total to pay: <strong>£700", confirmation)
            self.assertIn("Postal 401 Mission Row", confirmation)
            self.assertIn("Meet outside and call me", confirmation)
            self.assertEqual(self.db.report()["sales"], 0)
            self.assertEqual(len(self.orders.pending()), 1)
            response, body = request("/account")
            self.assertIn("Order #1: Waiting for a driver to accept", body)
            self.assertNotIn('action="/order"', body)
            self.orders.advance(1, "accepted", "9", "Delivery Staff")
            response, status_body = request("/order-status")
            status = json.loads(status_body)
            self.assertEqual(status["status"], "accepted")
            self.assertEqual(status["driver"], "Delivery Staff")
            self.orders.advance(1, "on_way", "9", "Delivery Staff")
            self.assertEqual(json.loads(request("/order-status")[1])["status"], "on_way")
            self.orders.advance(1, "arrived", "9", "Delivery Staff")
            arrived = json.loads(request("/order-status")[1])
            self.assertEqual(arrived["status"], "arrived")
            self.assertEqual(arrived["driver"], "Delivery Staff")
            self.orders.charge_wasted_journey(1, "9", "Delivery Staff")
            response, body = request("/account")
            self.assertEqual(response.status, 200)
            self.assertIn("DELIVERY ACCOUNT: £500 OWED", body)
            self.assertIn("New deliveries are unavailable", body)
            self.assertNotIn('action="/order"', body)
            self.assertEqual(json.loads(request("/order-status")[1])["status"], "wasted_journey")
        finally:
            server.shutdown()
            server.server_close()

    def test_multi_item_cart_subtotal_and_each_sale_recorded(self):
        row = self.orders.create_cart_authenticated(
            "Cody Ortega", {"mega_deal": 1, "quick_fix": 2}, "Postal 1", "multi-cart-request")
        self.assertEqual(row["subtotal"], 800)
        self.assertEqual(row["delivery_fee"], 100)
        self.assertEqual(row["price"], 900)
        self.assertEqual(sum(item["quantity"] for item in self.orders.items(row)), 3)
        self.orders.advance(row["id"], "accepted", "9", "Delivery Staff")
        on_way = self.orders.advance(row["id"], "on_way", "9", "Delivery Staff")
        self.assertEqual(on_way["status"], "on_way")
        arrived = self.orders.advance(row["id"], "arrived", "9", "Delivery Staff")
        self.assertEqual(arrived["status"], "arrived")
        paid, sales = self.orders.resolve(row["id"], "paid", "9", "Delivery Staff")
        self.assertEqual(paid["status"], "paid")
        self.assertEqual(len(sales), 3)
        self.assertEqual(self.db.report()["sales"], 3)
        self.assertEqual(self.db.report()["revenue"], 900)
        self.assertEqual(self.orders.ticket_result(row["id"])["tickets"], 3)

    def test_discount_code_changes_total_and_finance(self):
        code = self.orders.create_discount_code(
            "SNR10", "percent", 10, 1, "", "1", "Owner")
        self.assertEqual(code["uses"], 0)
        row = self.orders.create_cart_authenticated(
            "Cody Ortega", {"mega_deal": 1}, "Postal 33", "discount-order-key",
            discount_code="snr10")
        self.assertEqual(row["subtotal"], 500)
        self.assertEqual(row["discount_amount"], 50)
        self.assertEqual(row["delivery_fee"], 100)
        self.assertEqual(row["price"], 550)
        self.assertEqual(self.orders.discount_code("SNR10")["uses"], 1)
        self.orders.advance(row["id"], "accepted", "9", "Driver")
        self.orders.advance(row["id"], "on_way", "9", "Driver")
        self.orders.advance(row["id"], "arrived", "9", "Driver")
        self.orders.resolve(row["id"], "paid", "9", "Driver")
        self.assertEqual(self.db.report()["revenue"], 550)
        with self.assertRaisesRegex(ValueError, "usage limit"):
            self.orders.create_cart_authenticated(
                "Cody Ortega", {"quick_fix": 1}, "Postal 34", "discount-limit-key",
                discount_code="SNR10")

    def test_disabled_discount_code_is_rejected(self):
        self.orders.create_discount_code(
            "SAVE50", "fixed", 50, "", "", "1", "Owner")
        self.orders.disable_discount_code("SAVE50", "1", "Owner")
        with self.assertRaisesRegex(ValueError, "not valid"):
            self.orders.create_cart_authenticated(
                "Cody Ortega", {"mega_deal": 1}, "Postal 35", "disabled-code-key",
                discount_code="SAVE50")

    def test_snr_vip_gets_free_delivery(self):
        self.db.set_vip_override("Cody Ortega", "SNR VIP", "1", "Owner")
        row = self.orders.create_authenticated(
            "Cody Ortega", "quick_fix", "Postal 44", "vip-free-delivery")
        self.assertEqual(row["membership_level"], "SNR VIP")
        self.assertEqual(row["subtotal"], 150)
        self.assertEqual(row["delivery_fee"], 0)
        self.assertEqual(row["price"], 150)

    def test_wasted_journey_adds_fee_blocks_orders_and_adds_no_rewards(self):
        order = self.orders.create_authenticated(
            "Cody Ortega", "mega_deal", "Postal 909", "wasted-request-key"
        )
        self.orders.advance(order["id"], "accepted", "9", "Delivery Staff")
        self.orders.advance(order["id"], "on_way", "9", "Delivery Staff")
        self.orders.advance(order["id"], "arrived", "9", "Delivery Staff")
        wasted, fee = self.orders.charge_wasted_journey(
            order["id"], "9", "Delivery Staff"
        )
        self.assertEqual(wasted["status"], "wasted_journey")
        self.assertEqual(fee["amount"], 500)
        self.assertEqual(fee["status"], "owed")
        self.assertEqual(self.db.report()["sales"], 0)
        self.assertEqual(self.db.get_customer("Cody Ortega")["loyalty_points"], 0)
        with self.assertRaisesRegex(ValueError, "£500 Wasted Journey fee"):
            self.orders.create_authenticated(
                "Cody Ortega", "quick_fix", "Postal 909", "blocked-request-key"
            )
        self.orders.resolve_fee(fee["id"], "paid", "1", "Manager")
        replacement = self.orders.create_authenticated(
            "Cody Ortega", "quick_fix", "Postal 909", "allowed-request-key"
        )
        self.assertEqual(replacement["status"], "pending")

    def test_wasted_journey_only_allowed_after_driver_has_arrived(self):
        order = self.orders.create_authenticated(
            "Cody Ortega", "quick_fix", "Postal 22", "too-early-wasted-key"
        )
        with self.assertRaisesRegex(ValueError, "only be added after"):
            self.orders.charge_wasted_journey(order["id"], "9", "Delivery Staff")
        self.assertIsNone(self.orders.outstanding_fee("Cody Ortega"))

    def test_owner_can_manually_clock_staff_off(self):
        shifts = StaffShifts(self.db)
        shifts.clock_in("9", "Delivery Staff", "200")
        removed = shifts.force_clock_out("9", "1", "Cody Owner")
        self.assertEqual(removed["staff_name"], "Delivery Staff")
        self.assertEqual(shifts.active("200"), [])
        with self.db.connect() as conn:
            audit = conn.execute("SELECT action FROM audit_log ORDER BY rowid DESC LIMIT 1").fetchone()
        self.assertEqual(audit["action"], "owner_clocked_staff_off")


if __name__ == "__main__":
    unittest.main()
