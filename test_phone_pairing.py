import tempfile
import unittest

from phone_pairing import PhonePairings
from delivery_orders import DeliveryStore
from snr_core import SNRDatabase


class PhonePairingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = SNRDatabase(self.temp.name + "/test.db")
        self.db.record_sale("John Ash Carter", "mega_deal", "1", "Jamie")
        DeliveryStore(self.db).configure("10", "20", "1", "Jamie")
        self.pairs = PhonePairings(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_pairing_requires_staff_and_binds_both_identifiers(self):
        row = self.pairs.request("john ash carter", "char:abc", "555-111", "John Ash Carter", "request-key-123")
        self.assertEqual("pending", row["status"])
        self.assertEqual(1, row["identity_match"])
        self.assertIsNone(self.pairs.owner("char:abc", "555-111"))
        self.pairs.resolve(row["id"], "approved", "9", "Manager")
        self.assertEqual("john ash carter", self.pairs.owner("char:abc", "555-111"))
        self.assertIsNone(self.pairs.owner("char:abc", "different"))
        self.assertIsNone(self.pairs.owner("different", "555-111"))

    def test_mismatch_is_visible_and_replacement_revokes_old_phone(self):
        first = self.pairs.request("John Ash Carter", "char:abc", "111", "Suspicious Name", "request-key-111")
        self.assertEqual(0, first["identity_match"])
        self.pairs.resolve(first["id"], "approved", "9", "Manager")
        replacement = self.pairs.request("John Ash Carter", "char:abc", "222", "John Ash Carter", "request-key-222")
        self.assertEqual("replace", replacement["request_type"])
        self.pairs.resolve(replacement["id"], "approved", "9", "Manager")
        self.assertIsNone(self.pairs.owner("char:abc", "111"))
        self.assertEqual("john ash carter", self.pairs.owner("char:abc", "222"))

    def test_one_character_cannot_take_two_accounts(self):
        self.db.record_sale("Billy Pickering", "quick_fix", "1", "Jamie")
        first = self.pairs.request("John Ash Carter", "char:abc", "111", "John Ash Carter", "request-key-aaa")
        self.pairs.resolve(first["id"], "approved", "9", "Manager")
        second = self.pairs.request("Billy Pickering", "char:abc", "111", "Billy Pickering", "request-key-bbb")
        self.pairs.resolve(second["id"], "approved", "9", "Manager")
        self.assertIsNone(self.pairs.owner("char:abc", "999"))
        self.assertEqual("billy pickering", self.pairs.owner("char:abc", "111"))
        self.assertEqual("Not linked", self.pairs.status("John Ash Carter"))


if __name__ == "__main__":
    unittest.main()
