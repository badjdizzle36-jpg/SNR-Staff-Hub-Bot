import tempfile
import unittest
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

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
            self.assertIn("Pickup from SNR Buns", body)
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
            self.assertIn("Estimated", status["eta_text"])
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

    def test_web_pickup_works_when_no_driver_is_clocked_in(self):
        server = start_web_server(self.db, 0)
        base = f"http://127.0.0.1:{server.server_port}"
        cookie = "snr_session=" + self.session

        def request(path, values=None):
            req = Request(
                base + path,
                data=urlencode(values).encode() if values is not None else None,
                headers={"Cookie": cookie},
            )
            response = build_opener().open(req)
            return response, response.read().decode()

        try:
            response, body = request("/account")
            self.assertEqual(response.status, 200)
            self.assertIn("Pickup ordering is still available", body)
            parser = HiddenForm()
            parser.feed(body)
            response, confirmation = request("/order", {
                "order_request_key": parser.values["order_request_key"],
                "fulfillment_type": "pickup",
                "qty_quick_fix": "1",
                "postal": "",
            })
            self.assertEqual(response.status, 200)
            self.assertIn("Pickup order sent", confirmation)
            self.assertIn("Pickup charge: <strong>FREE", confirmation)
            self.assertIn("Collection: <strong>SNR Buns", confirmation)
            row = self.orders.pending()[0]
            self.assertEqual(row["fulfillment_type"], "pickup")
            self.assertEqual(row["price"], 150)
            response, tracking = request("/account")
            self.assertIn('class="order-progress"', tracking)
            self.assertIn('data-track-status="ready_for_pickup"', tracking)
            self.orders.advance(row["id"], "accepted", "11", "Counter Staff")
            self.orders.advance(row["id"], "ready_for_pickup", "11", "Counter Staff")
            self.orders.resolve(row["id"], "paid", "11", "Counter Staff")
            response, previous = request("/account")
            self.assertIn("Order Again", previous)
            self.assertIn('data-reorder="{&quot;quick_fix&quot;:1}"', previous)
        finally:
            server.shutdown()
            server.server_close()

    def test_birthday_reward_is_secure_automatic_and_annual(self):
        now = datetime.now(ZoneInfo("Europe/London"))
        saved = self.orders.set_birthday_authenticated("Cody Ortega", now.month, now.day)
        self.assertTrue(saved["saved"])
        self.assertEqual(saved["reason"], "security_wait")
        with self.assertRaisesRegex(ValueError, "already saved"):
            self.orders.set_birthday_authenticated("Cody Ortega", 1 if now.month != 1 else 2, 1)
        self.orders.configure_birthday_reward("fixed", 150, "1", "Owner")
        corrected = self.orders.set_birthday_by_owner(
            "Cody Orteg", now.month, now.day, "1", "Owner")
        self.assertEqual(corrected["customer_name"], "Cody Ortega")
        self.assertTrue(self.orders.birthday_status("Cody Ortega")["eligible"])
        row = self.orders.create_cart_authenticated(
            "Cody Ortega", {"quick_fix": 1}, "", "birthday-order-one", fulfillment_type="pickup")
        self.assertEqual(row["birthday_discount"], 150)
        self.assertEqual(row["price"], 0)
        self.orders.advance(row["id"], "accepted", "11", "Counter Staff")
        self.orders.advance(row["id"], "ready_for_pickup", "11", "Counter Staff")
        self.orders.resolve(row["id"], "paid", "11", "Counter Staff")
        self.assertEqual(self.orders.birthday_status("Cody Ortega")["reason"], "used")
        second = self.orders.create_cart_authenticated(
            "Cody Ortega", {"quick_fix": 1}, "", "birthday-order-two", fulfillment_type="pickup")
        self.assertEqual(second["birthday_discount"], 0)
        self.assertEqual(second["price"], 150)

    def test_daily_closing_summary_counts_orders_adjustments_and_open_work(self):
        self.orders.configure_birthday_reward("fixed", 50, "1", "Owner")
        now = datetime.now(ZoneInfo("Europe/London"))
        self.orders.set_birthday_by_owner("Cody Ortega", now.month, now.day, "1", "Owner")
        paid = self.orders.create_cart_authenticated(
            "Cody Ortega", {"mega_deal": 1}, "", "closing-paid", fulfillment_type="pickup")
        self.orders.advance(paid["id"], "accepted", "11", "Counter Staff")
        self.orders.advance(paid["id"], "ready_for_pickup", "11", "Counter Staff")
        self.orders.resolve(paid["id"], "paid", "11", "Counter Staff")
        self.accounts.issue_setup("Open Customer", "1", "Staff")
        self.orders.create_cart_authenticated(
            "Open Customer", {"quick_fix": 1}, "", "closing-open", fulfillment_type="pickup")
        report = self.orders.daily_summary("200")
        self.assertEqual(report["orders"], 1)
        self.assertEqual(report["pickups"], 1)
        self.assertEqual(report["deliveries"], 0)
        self.assertEqual(report["birthday_discounts"], 50)
        self.assertEqual(report["collected"], 450)
        self.assertEqual(report["active_orders"], 1)

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

    def test_pickup_is_free_and_uses_collection_workflow(self):
        row = self.orders.create_cart_authenticated(
            "Cody Ortega", {"mega_deal": 1}, "", "pickup-order-key",
            fulfillment_type="pickup")
        self.assertEqual(row["fulfillment_type"], "pickup")
        self.assertEqual(row["postal"], "SNR Buns — customer collection")
        self.assertEqual(row["subtotal"], 500)
        self.assertEqual(row["delivery_fee"], 0)
        self.assertEqual(row["price"], 500)
        self.orders.advance(row["id"], "accepted", "11", "Counter Staff")
        with self.assertRaisesRegex(ValueError, "Ready for Collection"):
            self.orders.resolve(row["id"], "paid", "11", "Counter Staff")
        ready = self.orders.advance(
            row["id"], "ready_for_pickup", "11", "Counter Staff")
        self.assertEqual(ready["status"], "ready_for_pickup")
        paid, sales = self.orders.resolve(row["id"], "paid", "11", "Counter Staff")
        self.assertEqual(paid["status"], "paid")
        self.assertEqual(len(sales), 1)
        self.assertEqual(self.db.report()["revenue"], 500)

    def test_pickup_cannot_use_delivery_driver_steps_or_wasted_fee(self):
        row = self.orders.create_cart_authenticated(
            "Cody Ortega", {"quick_fix": 1}, "", "pickup-route-lock",
            fulfillment_type="pickup")
        self.orders.advance(row["id"], "accepted", "11", "Counter Staff")
        with self.assertRaisesRegex(ValueError, "Ready for Collection"):
            self.orders.advance(row["id"], "on_way", "11", "Counter Staff")
        with self.assertRaisesRegex(ValueError, "cannot receive"):
            self.orders.charge_wasted_journey(row["id"], "11", "Counter Staff")

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

    def test_reviews_are_verified_one_per_paid_order_and_rank_staff(self):
        order = self.orders.create_cart_authenticated(
            "Cody Ortega", {"mega_deal": 1}, "", "reviewed-pickup",
            fulfillment_type="pickup")
        self.orders.advance(order["id"], "accepted", "11", "Counter Star")
        with self.assertRaisesRegex(ValueError, "after staff confirm"):
            self.orders.create_review_authenticated("Cody Ortega", order["id"], 5, "Great")
        self.orders.advance(order["id"], "ready_for_pickup", "11", "Counter Star")
        self.orders.resolve(order["id"], "paid", "11", "Counter Star")

        with self.assertRaisesRegex(ValueError, "1 to 5"):
            self.orders.create_review_authenticated("Cody Ortega", order["id"], 6, "")
        other_code = self.accounts.issue_setup("Other Customer", "1", "Staff")
        self.accounts.set_password("Other Customer", other_code, "another password 123")
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.orders.create_review_authenticated("Other Customer", order["id"], 5, "")

        review = self.orders.create_review_authenticated(
            "Cody Ortega", order["id"], 5, "Fantastic pickup service")
        self.assertEqual(review["staff_id"], "11")
        self.assertEqual(review["staff_name"], "Counter Star")
        self.assertEqual(review["fulfillment_type"], "pickup")
        with self.assertRaisesRegex(ValueError, "already reviewed"):
            self.orders.create_review_authenticated("Cody Ortega", order["id"], 1, "changed")

        alerts = self.orders.unnotified_reviews()
        self.assertEqual([row["id"] for row in alerts], [review["id"]])
        self.orders.review_notified(review["id"], "999")
        self.assertEqual(self.orders.unnotified_reviews(), [])
        weekly = self.orders.review_leaderboard("200", 7)
        self.assertEqual(weekly[0]["staff_name"], "Counter Star")
        self.assertEqual(weekly[0]["average_rating"], 5.0)
        self.assertEqual(weekly[0]["reviews"], 1)
        self.assertEqual(weekly[0]["pickups"], 1)

    def test_logged_in_customer_can_rate_completed_order_on_web(self):
        order = self.orders.create_cart_authenticated(
            "Cody Ortega", {"quick_fix": 1}, "", "web-review-order",
            fulfillment_type="pickup")
        self.orders.advance(order["id"], "accepted", "11", "Helpful Staff")
        self.orders.advance(order["id"], "ready_for_pickup", "11", "Helpful Staff")
        self.orders.resolve(order["id"], "paid", "11", "Helpful Staff")
        server = start_web_server(self.db, 0)
        base = f"http://127.0.0.1:{server.server_port}"
        cookie = "snr_session=" + self.session

        def request(path, values=None):
            req = Request(base + path,
                          data=urlencode(values).encode() if values is not None else None,
                          headers={"Cookie": cookie})
            response = build_opener().open(req)
            return response, response.read().decode()

        try:
            response, body = request("/account")
            self.assertEqual(response.status, 200)
            self.assertIn("Rate your pickup experience", body)
            self.assertIn("Helpful Staff", body)
            self.assertIn('id="rating-popup"', body)
            self.assertIn('role="dialog"', body)
            self.assertIn('name="rating" value="5"', body)
            self.assertIn('id="low-rating-reason"', body)
            self.assertIn('name="issue_category"', body)
            self.assertIn('action="/support"', body)
            self.assertEqual(body.count('action="/review"'), 1)
            self.assertLess(body.index('id="rating-popup"'), body.index('data-app-view="home"'))
            response, script = request("/delivery.js")
            self.assertEqual(response.status, 200)
            self.assertIn('location.href="/account#order"', script)
            self.assertIn('d.eta_text', script)
            self.assertIn('},2000);', script)
            parser = HiddenForm()
            parser.feed(body)
            response, thanks = request("/review", {
                "review_request_key": parser.values["review_request_key"],
                "order_id": str(order["id"]), "rating": "4",
                "comment": "Fast and friendly",
            })
            self.assertEqual(response.status, 200)
            self.assertIn("★★★★☆", thanks)
            self.assertIn("Helpful Staff", thanks)
            response, updated = request("/account")
            self.assertIn("Your pickup experience rating", updated)
            self.assertIn("Fast and friendly", updated)
            self.assertNotIn("Rate your pickup experience", updated)
            self.assertNotIn('id="rating-popup"', updated)
        finally:
            server.shutdown()
            server.server_close()

    def test_service_modes_and_live_queue_estimates(self):
        self.orders.set_service_mode("delivery_paused", "1", "Owner")
        with self.assertRaisesRegex(ValueError, "Deliveries are currently paused"):
            self.orders.create_authenticated(
                "Cody Ortega", "quick_fix", "Postal 1", "paused-delivery-order")
        pickup = self.orders.create_cart_authenticated(
            "Cody Ortega", {"quick_fix": 1}, "", "paused-pickup-order",
            fulfillment_type="pickup")
        self.assertEqual(pickup["fulfillment_type"], "pickup")
        estimate = self.orders.order_estimate(pickup["id"])
        self.assertEqual(estimate["queue_position"], 1)
        self.assertIn("queue position", estimate["eta_text"])
        self.orders.resolve(pickup["id"], "cancelled", "1", "Owner", allow_override=True)
        self.orders.set_service_mode("closed", "1", "Owner")
        with self.assertRaisesRegex(ValueError, "currently closed"):
            self.orders.create_cart_authenticated(
                "Cody Ortega", {"quick_fix": 1}, "", "closed-pickup-order",
                fulfillment_type="pickup")

    def test_low_rating_rescue_and_order_problem_are_tracked(self):
        order = self.orders.create_cart_authenticated(
            "Cody Ortega", {"mega_deal": 1}, "", "rescue-order",
            fulfillment_type="pickup")
        self.orders.advance(order["id"], "accepted", "11", "Counter Star")
        self.orders.advance(order["id"], "ready_for_pickup", "11", "Counter Star")
        self.orders.resolve(order["id"], "paid", "11", "Counter Star")
        with self.assertRaisesRegex(ValueError, "what went wrong"):
            self.orders.create_review_authenticated("Cody Ortega", order["id"], 1, "Late")
        review = self.orders.create_review_authenticated(
            "Cody Ortega", order["id"], 2, "Too slow", "delivery_time")
        self.assertEqual(review["resolution_status"], "open")
        self.assertEqual(self.orders.open_low_reviews()[0]["id"], review["id"])
        resolved = self.orders.resolve_low_review(review["id"], "1", "Manager")
        self.assertEqual(resolved["resolution_status"], "resolved")
        problem = self.orders.create_support_authenticated(
            "Cody Ortega", order["id"], "missing_items", "Drink missing")
        self.assertEqual(problem["status"], "pending")
        self.assertEqual(self.orders.pending_support(unsent=True)[0]["id"], problem["id"])
        self.orders.support_notified(problem["id"], "123")
        self.assertEqual(self.orders.pending_support(unsent=True), [])
        self.assertEqual(self.orders.resolve_support(problem["id"], "1", "Manager")["status"], "resolved")

    def test_late_order_alerts_fire_once_per_level_and_reset_on_stage_change(self):
        order = self.orders.create_authenticated(
            "Cody Ortega", "quick_fix", "Postal 99", "late-order-test")
        orange_time = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(timespec="seconds")
        with self.db.connect() as conn:
            conn.execute("UPDATE web_delivery_orders SET status_updated_at=? WHERE id=?",
                         (orange_time, order["id"]))
        orange = self.orders.late_alerts()
        self.assertEqual(len(orange), 1)
        self.assertEqual(orange[0]["late_level"], 1)
        self.assertEqual(orange[0]["late_colour"], "orange")
        self.orders.mark_late_alert(order["id"], 1)
        self.assertEqual(self.orders.late_alerts(), [])

        red_time = (datetime.now(timezone.utc) - timedelta(minutes=8)).isoformat(timespec="seconds")
        with self.db.connect() as conn:
            conn.execute("UPDATE web_delivery_orders SET status_updated_at=? WHERE id=?",
                         (red_time, order["id"]))
        red = self.orders.late_alerts()
        self.assertEqual(red[0]["late_level"], 2)
        self.assertEqual(red[0]["late_colour"], "red")
        self.orders.mark_late_alert(order["id"], 2)
        self.assertEqual(self.orders.late_alerts(), [])

        advanced = self.orders.advance(order["id"], "accepted", "9", "Driver One")
        self.assertEqual(advanced["late_alert_level"], 0)
        self.assertEqual(self.orders.order_health(advanced)["colour"], "green")

    def test_owner_customer_announcement_is_safe_live_and_removable(self):
        with self.assertRaisesRegex(ValueError, "between 3 and 300"):
            self.orders.set_customer_announcement("x", "info", "1", "Owner")
        result = self.orders.set_customer_announcement(
            "Big offer <script>alert(1)</script>", "promo", "1", "Owner")
        self.assertTrue(result["active"])
        self.assertEqual(result["style"], "promo")

        server = start_web_server(self.db, 0)
        base = f"http://127.0.0.1:{server.server_port}"
        cookie = "snr_session=" + self.session
        try:
            response = build_opener().open(Request(base + "/account", headers={"Cookie": cookie}))
            body = response.read().decode()
            self.assertIn('class="customer-banner banner-promo"', body)
            self.assertIn("Big offer &lt;script&gt;alert(1)&lt;/script&gt;", body)
            self.assertNotIn("Big offer <script>", body)
            response = build_opener().open(Request(
                base + "/announcement-status", headers={"Cookie": cookie}))
            status = json.loads(response.read().decode())
            self.assertTrue(status["active"])
            self.assertEqual(status["message"], "Big offer <script>alert(1)</script>")
            self.orders.clear_customer_announcement("1", "Owner")
            response = build_opener().open(Request(
                base + "/announcement-status", headers={"Cookie": cookie}))
            self.assertFalse(json.loads(response.read().decode())["active"])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
