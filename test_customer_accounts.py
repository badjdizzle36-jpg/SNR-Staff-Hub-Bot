import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

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

    def test_self_service_create_and_reset_require_staff_approval(self):
        self.db.record_sale("Cody Ortega", "quick_fix", "1", "Staff")
        DeliveryStore(self.db).configure(100, 200, "1", "Manager")
        request = self.accounts.request_access("Cody Ortega", "customer chosen password")
        self.assertEqual(request["request_type"], "create")
        with self.assertRaises(ValueError):
            self.accounts.login("Cody Ortega", "customer chosen password")
        approved = self.accounts.resolve_request(request["id"], "approved", "1", "Staff")
        self.assertEqual(approved["status"], "approved")
        first_session = self.accounts.login("Cody Ortega", "customer chosen password")
        reset = self.accounts.request_access("Cody Ortega", "customer new password")
        self.assertEqual(reset["request_type"], "reset")
        self.assertEqual(self.accounts.owner(first_session), "cody ortega")
        self.accounts.resolve_request(reset["id"], "approved", "1", "Staff")
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

    def test_rejected_self_service_request_changes_nothing(self):
        self.db.record_sale("Cody Ortega", "quick_fix", "1", "Staff")
        DeliveryStore(self.db).configure(100, 200, "1", "Manager")
        request = self.accounts.request_access("Cody Ortega", "customer chosen password")
        self.accounts.resolve_request(request["id"], "rejected", "1", "Staff")
        with self.assertRaises(ValueError):
            self.accounts.login("Cody Ortega", "customer chosen password")

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
        self.assertEqual(self.db.get_customer("Cody Ortega")["loyalty_points"], 0)
        self.assertEqual(self.db.get_customer("Other Person")["loyalty_points"], 4)
        with self.assertRaises(ValueError):
            claims.request_authenticated("other person", "authenticated-request-1")

    def test_concurrent_claim_reserves_once(self):
        self.create()
        for _ in range(4):
            self.db.record_sale("Cody Ortega", "share_box", "1", "Staff")
        claims = ClaimStore(self.db)
        claims.configure(100, 200, "1", "Manager")
        with ThreadPoolExecutor(max_workers=6) as pool:
            rows = list(pool.map(lambda i: claims.request_authenticated("cody ortega", f"request-key-number-{i}"), range(10)))
        self.assertEqual(len({row["id"] for row in rows}), 1)
        self.assertEqual(self.db.get_customer("Cody Ortega")["loyalty_points"], 4)

    def test_public_page_has_no_customer_list_or_password_form(self):
        for _ in range(2):
            self.db.record_sale("Cody Ortega", "share_box", "1", "Staff")
        claims = ClaimStore(self.db)
        claims.configure(100, 200, "1", "Manager")
        DeliveryStore(self.db).configure(100, 200, "1", "Manager")
        server = start_web_server(self.db, 0)
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            response = build_opener().open(base + "/")
            public = response.read().decode()
            self.assertEqual(response.status, 200)
            self.assertNotIn("Cody Ortega", public)
            self.assertIn("No password is required", public)
            self.assertIn("LB Phone", public)
            self.assertNotIn('name="password"', public)
            self.assertNotIn("Choose your character name", public)
            self.assertNotIn("Golden tickets", public)
            self.assertNotIn("Recent visits", public)
        finally:
            server.shutdown(); server.server_close()


if __name__ == "__main__":
    unittest.main()
