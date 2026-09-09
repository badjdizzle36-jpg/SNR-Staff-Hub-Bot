import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from customer_accounts import Accounts
from delivery_orders import DeliveryStore
from raffles import RaffleStore
from snr_core import SNRDatabase
from web_portal import start_web_server


class HiddenForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("type") == "hidden":
            self.values[attrs.get("name")] = attrs.get("value", "")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RaffleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SNRDatabase(self.tmp.name + "/data.db")
        self.db.record_sale("Cody Ortega", "quick_fix", "1", "Staff")
        self.db.record_sale("Kay Armstrong", "quick_fix", "1", "Staff")
        DeliveryStore(self.db).configure(100, 200, "1", "Owner")
        self.raffles = RaffleStore(self.db)
        self.raffle = self.raffles.create("Summer Cash Draw", "£10,000 cash", 100, "1", "Owner")

    def tearDown(self):
        self.tmp.cleanup()

    def test_website_request_reserves_then_staff_confirms_payment(self):
        row = self.raffles.request("cody ortega", [7, 12, 99], "web-request-one")
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["total_price"], 300)
        self.assertEqual(self.raffles.current()["pending_numbers"], 3)
        with self.assertRaisesRegex(ValueError, "just taken"):
            self.raffles.request("kay armstrong", [12], "web-request-two")
        paid = self.raffles.resolve(row["id"], True, "9", "Staff Member")
        self.assertEqual(paid["status"], "confirmed")
        self.assertEqual(self.raffles.current()["revenue"], 300)
        self.assertTrue(all(entry["status"] == "confirmed" for entry in self.raffles.customer_entries("Cody Ortega")))

    def test_reject_releases_numbers_and_limit_is_enforced(self):
        row = self.raffles.request("cody ortega", [1, 2], "reject-this")
        self.raffles.resolve(row["id"], False, "9", "Staff")
        replacement = self.raffles.request("kay armstrong", [1, 2], "replacement")
        self.assertEqual(replacement["numbers"], [1, 2])
        with self.assertRaisesRegex(ValueError, "10"):
            self.raffles.request("cody ortega", list(range(10, 21)), "too-many")

    def test_only_confirmed_entries_can_win_and_draw_is_final(self):
        paid = self.raffles.request("cody ortega", [4], "paid", confirmed=True,
                                    source="staff", staff_id="9", staff_name="Staff")
        self.raffles.request("kay armstrong", [5], "unpaid")
        with self.assertRaisesRegex(ValueError, "pending payment"):
            self.raffles.close("1", "Owner")
        self.raffles.resolve(self.raffles.pending()[0]["id"], False, "9", "Staff")
        self.raffles.close("1", "Owner")
        result = self.raffles.draw("1", "Owner")
        self.assertEqual(result["status"], "drawn")
        self.assertEqual(result["winning_number"], 4)
        self.assertEqual(result["winner_name"], "Cody Ortega")
        with self.assertRaisesRegex(ValueError, "Close entries"):
            self.raffles.draw("1", "Owner")
        self.assertEqual(paid["total_price"], 100)

    def test_simultaneous_number_requests_have_one_winner(self):
        def reserve(name, key):
            try:
                return self.raffles.request(name, [50], key)["customer_name"]
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda args: reserve(*args),
                                    [("Cody Ortega", "race-one"), ("Kay Armstrong", "race-two")]))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(len([entry for entry in self.raffles.entries() if entry["number"] == 50]), 1)

    def test_price_cannot_change_after_reservation(self):
        self.raffles.request("cody ortega", [8], "price-lock")
        with self.assertRaisesRegex(ValueError, "price cannot change"):
            self.raffles.update("Updated Draw", "Bigger prize", 200, "1", "Owner")
        updated = self.raffles.update("Updated Draw", "Bigger prize", 100, "1", "Owner")
        self.assertEqual(updated["title"], "Updated Draw")

    def test_logged_in_customer_can_request_from_webpage(self):
        accounts = Accounts(self.db)
        code = accounts.issue_setup("Cody Ortega", "1", "Staff")
        session = accounts.set_password("Cody Ortega", code, "correct horse 123")
        server = start_web_server(self.db, 0)
        base = f"http://127.0.0.1:{server.server_port}"
        browser = build_opener(NoRedirect())
        cookie = "snr_session=" + session

        def request(path, values=None):
            req = Request(base + path, data=urlencode(values).encode() if values is not None else None,
                          headers={"Cookie": cookie})
            try:
                response = browser.open(req)
            except HTTPError as error:
                response = error
            return response, response.read().decode()

        try:
            response, page = request("/account#raffle")
            self.assertEqual(response.status, 200)
            self.assertIn('data-tab-target="raffle"', page)
            self.assertIn("Summer Cash Draw", page)
            self.assertIn('name="number_22"', page)
            parser = HiddenForm()
            parser.feed(page)
            token = parser.values["raffle_request_key"]
            response, result = request("/raffle-request", {
                "raffle_request_key": token, "number_22": "22", "number_23": "23",
            })
            self.assertEqual(response.status, 200)
            self.assertIn("Your numbers are reserved", result)
            pending = self.raffles.pending()
            self.assertEqual(pending[0]["numbers"], [22, 23])
            self.assertEqual(pending[0]["channel_id"], "100")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
