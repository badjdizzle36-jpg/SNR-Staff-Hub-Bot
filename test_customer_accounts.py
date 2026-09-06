import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from customer_accounts import Accounts
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

    def test_full_web_account_flow_and_phone_cookie(self):
        code = self.accounts.issue_setup("Cody Ortega", "1", "Staff")
        for _ in range(2):
            self.db.record_sale("Cody Ortega", "share_box", "1", "Staff")
        claims = ClaimStore(self.db)
        claims.configure(100, 200, "1", "Manager")
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
            self.assertNotIn("Golden tickets", public)
            self.assertNotIn("Recent visits", public)
            response, blocked = open_request("/card?name=Cody+Ortega", opener=build_opener())
            self.assertEqual(response.status, 401)
            self.assertNotIn("Golden tickets", blocked)
            response, _ = open_request("/activate", {"name": "Cody Ortega", "code": code,
                                                          "password": "my safe password", "confirm": "my safe password"})
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
            parser = HiddenForm(); parser.feed(body)
            forged = dict(parser.values); forged["claim_request_key"] = "forged"
            self.assertEqual(open_request("/claim", forged, cookie=session_cookie)[0].status, 400)
            response, result = open_request("/claim", parser.values, cookie=session_cookie)
            self.assertEqual(response.status, 200)
            self.assertIn("saved for staff", result)
            self.assertEqual(self.db.get_customer("Cody Ortega")["loyalty_points"], 0)
            response, body = open_request("/account", cookie=session_cookie)
            self.assertIn("Awaiting staff handover", body)
            self.assertNotIn('name="code"', body)
            self.assertEqual(len(claims.pending()), 1)
        finally:
            server.shutdown(); server.server_close()


if __name__ == "__main__":
    unittest.main()
