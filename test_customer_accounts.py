import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from customer_accounts import Accounts
from delivery_orders import DeliveryStore
from reward_claims import ClaimStore
from snr_core import SNRDatabase
from web_portal import start_web_server


class HiddenForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("type") == "hidden":
            self.values[attrs["name"]] = attrs.get("value", "")


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SNRDatabase(self.tmp.name + "/data.db")
        self.accounts = Accounts(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, name="Cody Ortega", password="correct horse 123"):
        code = self.accounts.issue_setup(name, "1", "Staff")
        return code, self.accounts.set_password(name, code, password)

    def test_staff_setup_login_reset_and_staff_data_access(self):
        code, first = self.create()
        self.assertEqual(self.accounts.owner(first), "cody ortega")
        second = self.accounts.login("  CODY   ORTEGA ", "correct horse 123")
        self.assertEqual(self.accounts.owner(second), "cody ortega")
        self.assertEqual(self.accounts.status("Cody Ortega"), "Password set")
        self.assertIsNotNone(self.db.get_customer("Cody Ortega"))
        reset = self.accounts.issue_setup("Cody Ortega", "1", "Staff", reset=True)
        self.assertIsNone(self.accounts.owner(first))
        self.assertIsNone(self.accounts.owner(second))
        with self.assertRaises(ValueError):
            self.accounts.login("Cody Ortega", "correct horse 123")
        new_session = self.accounts.set_password("Cody Ortega", reset, "brand new password")
        self.assertEqual(self.accounts.owner(new_session), "cody ortega")
        with self.db.connect() as conn:
            raw = str([tuple(row) for row in conn.execute("SELECT * FROM customer_accounts")])
            audits = str([tuple(row) for row in conn.execute("SELECT * FROM audit_log")])
        self.assertNotIn("brand new password", raw + audits)
        self.assertNotIn(reset, raw + audits)

    def test_wrong_password_lock_and_new_setup_replaces_old_code(self):
        first = self.accounts.issue_setup("Cody Ortega", "1", "Staff")
        second = self.accounts.issue_setup("Cody Ortega", "1", "Staff")
        with self.assertRaises(ValueError):
            self.accounts.set_password("Cody Ortega", first, "long enough password")
        self.accounts.set_password("Cody Ortega", second, "long enough password")
        for _ in range(5):
            with self.assertRaises(ValueError):
                self.accounts.login("Cody Ortega", "wrong password")
        with self.assertRaises(ValueError):
            self.accounts.login("Cody Ortega", "long enough password")

    def test_self_service_create_is_immediate_and_memorable_answer_resets(self):
        DeliveryStore(self.db).configure(100, 200, "1", "Manager")
        request = self.accounts.request_access(
            "Cody Ortega", "customer chosen password", "first_pet", "Buster")
        self.assertEqual(request["request_type"], "create")
        self.assertEqual(request["status"], "created")
        self.assertEqual(self.db.get_customer("Cody Ortega")["lifetime_sales"], 0)
        first_session = self.accounts.login("Cody Ortega", "customer chosen password")
        with self.assertRaises(ValueError):
            self.accounts.reset_with_answer("Cody Ortega", "first_pet", "wrong", "customer new password")
        self.accounts.reset_with_answer("CODY ORTEGA", "first_pet", "buster", "customer new password")
        self.assertIsNone(self.accounts.owner(first_session))
        with self.assertRaises(ValueError):
            self.accounts.login("Cody Ortega", "customer chosen password")
        self.assertEqual(
            self.accounts.owner(self.accounts.login("Cody Ortega", "customer new password")),
            "cody ortega",
        )
        with self.db.connect() as conn:
            raw = str([tuple(row) for row in conn.execute("SELECT * FROM customer_account_requests")])
        self.assertNotIn("customer chosen password", raw)
        self.assertNotIn("customer new password", raw)

    def test_duplicate_self_service_account_cannot_replace_password(self):
        DeliveryStore(self.db).configure(100, 200, "1", "Manager")
        request = self.accounts.request_access(
            "Cody Ortega", "customer chosen password", "first_pet", "Buster")
        with self.assertRaises(ValueError):
            self.accounts.request_access(
                "Cody Ortega", "attacker password", "first_pet", "Wrong Answer")
        self.assertEqual(self.accounts.owner(
            self.accounts.login("Cody Ortega", "customer chosen password")), "cody ortega")
        self.assertEqual(len(self.accounts.created_notifications(unsent=True)), 1)

    def test_new_account_notifications_use_their_own_channel(self):
        delivery = DeliveryStore(self.db)
        delivery.configure(100, 200, "1", "Owner")
        self.assertFalse(self.accounts.notifications_configured())
        first = self.accounts.request_access(
            "First Customer", "customer password 123", "first_pet", "Buster")
        self.assertEqual(first["channel_id"], "100")

        self.accounts.configure_notifications(222, 200, "1", "Owner")
        self.assertTrue(self.accounts.notifications_configured())
        # Any alert not posted yet moves out of Delivery Orders as well.
        self.assertEqual(self.accounts.get_request(first["id"])["channel_id"], "222")
        second = self.accounts.request_access(
            "Second Customer", "customer password 456", "first_job", "Mechanic")
        self.assertEqual(second["channel_id"], "222")

        delivery_order = delivery.create_authenticated(
            "First Customer", "quick_fix", "Postal 100", "separate-delivery-channel")
        self.assertEqual(delivery_order["channel_id"], "100")
        self.assertEqual(delivery.pending(unsent=True)[0]["channel_id"], "100")

    def test_authenticated_claim_is_bound_to_session_owner(self):
        self.create("Cody Ortega")
        self.create("Other Person", "other password 123")
        for _ in range(2):
            self.db.record_sale("Cody Ortega", "share_box", "1", "Staff")
            self.db.record_sale("Other Person", "share_box", "1", "Staff")
        claims = ClaimStore(self.db)
        claims.configure(100, 200, "1", "Manager")
        row = claims.request_authenticated("cody ortega", "authenticated-request-1")
        self.assertEqual(row["customer_key"], "cody ortega")
        self.assertEqual(self.db.get_customer("Cody Ortega")["loyalty_points"], 4)
        self.assertEqual(self.db.get_customer("Other Person")["loyalty_points"], 4)
        with self.assertRaises(ValueError):
            claims.request_authenticated("other person", "authenticated-request-1")
        claims.resolve(row["id"], "fulfilled", "1", "Staff")
        self.assertEqual(self.db.get_customer("Cody Ortega")["loyalty_points"], 0)

    def test_concurrent_claim_reserves_once(self):
        self.create()
        for _ in range(4):
            self.db.record_sale("Cody Ortega", "share_box", "1", "Staff")
        claims = ClaimStore(self.db)
        claims.configure(100, 200, "1", "Manager")
        with ThreadPoolExecutor(max_workers=6) as pool:
            rows = list(pool.map(lambda i: claims.request_authenticated("cody ortega", f"request-key-number-{i}"), range(10)))
        self.assertEqual(len({row["id"] for row in rows}), 1)
        self.assertEqual(self.db.get_customer("Cody Ortega")["loyalty_points"], 8)
        claims.resolve(rows[0]["id"], "fulfilled", "1", "Staff")
        self.assertEqual(self.db.get_customer("Cody Ortega")["loyalty_points"], 4)
        with self.assertRaises(ValueError):
            claims.resolve(rows[0]["id"], "fulfilled", "1", "Staff")
        next_claim = claims.request_authenticated("cody ortega", "second-pack-request")
        self.db.record_sale("Cody Ortega", "share_box", "1", "Staff")
        claims.resolve(next_claim["id"], "fulfilled", "1", "Staff")
        self.assertEqual(self.db.get_customer("Cody Ortega")["loyalty_points"], 2)

    def test_claim_uses_existing_orders_channel_and_cancel_keeps_points(self):
        self.create()
        self.db.record_sale("Cody Ortega", "share_box", "1", "Staff")
        self.db.record_sale("Cody Ortega", "share_box", "1", "Staff")
        DeliveryStore(self.db).configure(321, 654, "1", "Manager")
        claims = ClaimStore(self.db)
        self.assertTrue(claims.configured())
        row = claims.request_authenticated("Cody Ortega", "fallback-channel-request")
        self.assertEqual(row["channel_id"], "321")
        self.assertEqual(self.db.get_customer("Cody Ortega")["loyalty_points"], 4)
        claims.resolve(row["id"], "cancelled", "1", "Staff")
        self.assertEqual(self.db.get_customer("Cody Ortega")["loyalty_points"], 4)

    def test_full_web_account_flow_and_phone_cookie(self):
        for _ in range(2):
            self.db.record_sale("Cody Ortega", "share_box", "1", "Staff")
        claims = ClaimStore(self.db)
        claims.configure(100, 200, "1", "Manager")
        DeliveryStore(self.db).configure(100, 200, "1", "Manager")
        server = start_web_server(self.db, 0)
        base = f"http://127.0.0.1:{server.server_port}"
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        browser = build_opener(NoRedirect())
        session_cookie = ""

        def open_request(path, values=None, opener=browser, cookie=""):
            headers = {"Cookie": cookie} if cookie else {}
            request = Request(base + path, data=urlencode(values).encode() if values else None, headers=headers)
            try:
                response = opener.open(request)
            except HTTPError as error:
                response = error
            return response, response.read().decode()

        try:
            response, public = open_request("/")
            self.assertIn("Cody Ortega", public)
            self.assertIn("Log in to my card", public)
            self.assertIn("Create a new loyalty card", public)
            self.assertIn("Forgot my password", public)
            self.assertNotIn("Open your loyalty account", public)
            self.assertNotIn("one-time setup code", public.lower())
            self.assertNotIn("Golden tickets", public)
            self.assertNotIn("Recent visits", public)
            response, blocked = open_request("/card?name=Cody+Ortega", opener=build_opener())
            self.assertEqual(response.status, 401)
            self.assertNotIn("Golden tickets", blocked)
            response, request_sent = open_request("/request-access", {
                "name": "Cody Ortega", "password": "my safe password", "confirm": "my safe password",
                "security_question": "first_pet", "security_answer": "Buster",
            })
            self.assertEqual(response.status, 200)
            self.assertIn("no approval is required", request_sent)
            self.assertEqual(len(self.accounts.pending()), 0)
            self.assertEqual(len(self.accounts.created_notifications(unsent=True)), 1)
            response, _ = open_request("/login", {
                "name": "Cody Ortega", "password": "my safe password"
            })
            self.assertEqual(response.status, 303)
            raw_cookie = response.headers.get("Set-Cookie", "")
            self.assertIn("HttpOnly", raw_cookie)
            self.assertIn("Secure", raw_cookie)
            self.assertIn("SameSite=None", raw_cookie)
            self.assertIn("Partitioned", raw_cookie)
            session_cookie = raw_cookie.split(";", 1)[0]
            response, body = open_request("/account", cookie=session_cookie)
            self.assertEqual(response.status, 200)
            self.assertIn("Golden tickets", body)
            self.assertIn("Your free pack is ready", body)
            self.assertIn('aria-valuenow="4"', body)
            self.assertIn('data-tab-target="home"', body)
            self.assertIn('data-tab-target="order"', body)
            self.assertIn('data-tab-target="rewards"', body)
            self.assertIn('data-tab-target="visits"', body)
            self.assertIn('data-app-view="home"', body)
            self.assertIn('src="/portal.js"', body)
            self.assertIn('class="card quick-hub"', body)
            self.assertIn('class="home-pack-form"', body)
            self.assertIn('Request Pack — 4 Points', body)
            self.assertIn('All membership cards', body)
            self.assertIn('SNR VIP membership card', body)
            self.assertIn("CUSTOMER APP", body)
            self.assertIn('data-order-mode="pickup"', body)
            self.assertIn('data-order-mode="delivery"', body)
            self.assertNotIn('value="instore"', body)
            self.assertIn('<span class="tab-icon">☰</span>More', body)
            response, portal_script = open_request("/portal.js", cookie=session_cookie)
            self.assertEqual(response.status, 200)
            self.assertIn("app-ready", portal_script)
            self.assertIn("data-quick-order-id", portal_script)
            parser = HiddenForm(); parser.feed(body)
            self.assertIn('class="live-action-form"', body)
            self.assertIn('value="staff_help"', body)
            self.assertIn('name="order_id" value=""', body)
            self.assertNotIn('<select name="action_type"', body)
            action_values = {
                "service_request_key": parser.values["service_request_key"],
                "order_id": "0", "action_type": "staff_help", "details": "Please help me",
            }
            response, action_sent = open_request("/customer-action", action_values, cookie=session_cookie)
            self.assertEqual(response.status, 200)
            self.assertIn("SNR staff have been notified", action_sent)
            response, action_repeated = open_request("/customer-action", action_values, cookie=session_cookie)
            self.assertEqual(response.status, 200)
            self.assertIn("SNR staff have been notified", action_repeated)
            with self.db.connect() as conn:
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM customer_live_actions WHERE status='pending'"
                ).fetchone()[0], 1)
            now = datetime.now(ZoneInfo("Europe/London"))
            response, birthday_error = open_request("/birthday", {
                "birthday_request_key": "forged", "birthday_day": str(now.day),
                "birthday_month": str(now.month),
            }, cookie=session_cookie)
            self.assertEqual(response.status, 400)
            response, birthday_saved = open_request("/birthday", {
                "birthday_request_key": parser.values["birthday_request_key"],
                "birthday_day": str(now.day), "birthday_month": str(now.month),
            }, cookie=session_cookie)
            self.assertEqual(response.status, 200)
            self.assertIn("Birthday Reward", birthday_saved)
            forged = dict(parser.values); forged["claim_request_key"] = "forged"
            self.assertEqual(open_request("/claim", forged, cookie=session_cookie)[0].status, 400)
            response, result = open_request("/claim", parser.values, cookie=session_cookie)
            self.assertEqual(response.status, 200)
            self.assertIn("sent to SNR staff", result)
            self.assertEqual(self.db.get_customer("Cody Ortega")["loyalty_points"], 4)
            response, body = open_request("/account", cookie=session_cookie)
            self.assertIn("Your pack request is with staff", body)
            self.assertIn("Pack history (1)", body)
            self.assertNotIn('name="code"', body)
            self.assertEqual(len(claims.pending()), 1)
            claims.resolve(claims.pending()[0]["id"], "fulfilled", "1", "Staff")
            self.assertEqual(self.db.get_customer("Cody Ortega")["loyalty_points"], 0)
        finally:
            server.shutdown(); server.server_close()


if __name__ == "__main__":
    unittest.main()
