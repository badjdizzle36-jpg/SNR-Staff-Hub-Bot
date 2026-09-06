import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from customer_accounts import Accounts
from delivery_orders import DeliveryStore
from phone_pairing import PhonePairings
from snr_core import DEALS, SNRDatabase
from web_portal import start_web_server


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
        self.assertIsNone(sale)
        self.assertEqual(self.db.report()["sales"], 0)

    def test_lb_phone_api_delivery_flow(self):
        old_secret = os.environ.get("LB_PHONE_API_SECRET")
        os.environ["LB_PHONE_API_SECRET"] = "test-secret-value-with-enough-length"
        pairs = PhonePairings(self.db)
        pending = pairs.request("Cody Ortega", "qb:abc", "555111", "Cody Ortega", "pair-request-123")
        pairs.resolve(pending["id"], "approved", "1", "Manager")
        server = start_web_server(self.db, 0)
        base = f"http://127.0.0.1:{server.server_port}"

        def request(path, values):
            req = Request(base + path, data=json.dumps(values).encode(), headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test-secret-value-with-enough-length",
            })
            response = build_opener().open(req)
            return response, json.loads(response.read())

        try:
            identity = {"character_id": "qb:abc", "phone_number": "555111", "rp_name": "Cody Ortega"}
            response, body = request("/api/lb/state", identity)
            self.assertEqual(response.status, 200)
            for deal in DEALS.values():
                self.assertIn(deal.name, [row["name"] for row in body["deals"]])
            values = {**identity,
                "request_key": "delivery-api-request-123",
                "deal": "blue_light",
                "postal": "Postal 401 Mission Row",
            }
            response, confirmation = request("/api/lb/order", values)
            self.assertEqual(response.status, 200)
            self.assertEqual(600, confirmation["price"])
            self.assertEqual(self.db.report()["sales"], 0)
            self.assertEqual(len(self.orders.pending()), 1)
            response, body = request("/api/lb/state", identity)
            self.assertEqual("pending", body["orders"][0]["status"])
        finally:
            server.shutdown()
            server.server_close()
            if old_secret is None:
                os.environ.pop("LB_PHONE_API_SECRET", None)
            else:
                os.environ["LB_PHONE_API_SECRET"] = old_secret


if __name__ == "__main__":
    unittest.main()
