import tempfile
import unittest
from pathlib import Path

from alert_channels import AlertChannels, CHANNEL_TYPES
from snr_core import SNRDatabase


class AlertChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = SNRDatabase(str(Path(self.temp.name) / "test.db"))
        self.channels = AlertChannels(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_categories_are_independent_and_have_fallbacks(self):
        self.assertEqual(self.channels.channel_id("active_orders", "1", "99"), "99")
        self.channels.configure("active_orders", "100", "1", "7", "Owner")
        self.channels.configure("pack_requests", "200", "1", "7", "Owner")
        self.assertEqual(self.channels.channel_id("active_orders", "1", "99"), "100")
        self.assertEqual(self.channels.channel_id("pack_requests", "1", "99"), "200")
        self.assertEqual(self.channels.channel_id("active_orders", "2", "99"), "99")
        self.assertEqual(len(self.channels.status("1")), len(CHANNEL_TYPES))

    def test_unknown_category_is_rejected(self):
        with self.assertRaises(ValueError):
            self.channels.configure("unknown", "100", "1", "7", "Owner")


if __name__ == "__main__":
    unittest.main()
