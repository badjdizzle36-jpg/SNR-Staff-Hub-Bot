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
        self.assertEqual(row["price"], 1200)
        self.assertEqual(row["postal"], "Postal 123 Legion Square")
        self.assertEqual(self.db.report()["sales"], 0)
        self.assertEqual(self.db.get_customer("Cody Ortega")["loyalty_points"], 0)

    def test_paid_order_records_sale_loyalty_and_finance_once(self):
        order = self.orders.create_authenticated(
            "Cody Ortega", "mega_deal", "Postal 505", "paid-request-key"
        )
        self.orders.advance(order["id"], "accepted", "9", "Delivery Staff")
        self.orders.advance(order["id"], "on_way", "9", "Delivery Staff")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda _: self.orders.resolve(order["id"], "paid", "9", "Delivery Staff"),
                range(2),
            ))
        self.assertTrue(all(result[0]["status"] == "paid" for result in results))
        report = self.db.report()
        customer = self.db.get_customer("Cody Ortega")
        self.assertEqual(report["sales"], 1)
        self.assertEqual(report["revenue"], 500)
        self.assertEqual(customer["loyalty_points"], 1)
        self.assertEqual(customer["golden_tickets"], 1)
        self.assertEqual(results[0][0]["sale_transaction_id"], results[1][0]["sale_transaction_id"])

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
            self.assertIn("£600", confirmation)
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
        finally:
            server.shutdown()
            server.server_close()

    def test_multi_item_cart_subtotal_and_each_sale_recorded(self):
        row = self.orders.create_cart_authenticated(
            "Cody Ortega", {"mega_deal": 1, "quick_fix": 2}, "Postal 1", "multi-cart-request")
        self.assertEqual(row["price"], 800)
        self.assertEqual(sum(item["quantity"] for item in self.orders.items(row)), 3)
        self.orders.advance(row["id"], "accepted", "9", "Delivery Staff")
        on_way = self.orders.advance(row["id"], "on_way", "9", "Delivery Staff")
        self.assertEqual(on_way["status"], "on_way")
        paid, sales = self.orders.resolve(row["id"], "paid", "9", "Delivery Staff")
        self.assertEqual(paid["status"], "paid")
        self.assertEqual(len(sales), 3)
        self.assertEqual(self.db.report()["sales"], 3)
        self.assertEqual(self.db.report()["revenue"], 800)


if __name__ == "__main__":
    unittest.main()
