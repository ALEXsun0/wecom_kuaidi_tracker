import hashlib
import json
import unittest
from urllib.parse import urlencode

from wecom_kuaidi_tracker.kuaidi100_client import Kuaidi100Client


class Kuaidi100ClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = Kuaidi100Client(
            key="test-key",
            callback_url="https://example.com/callback",
            salt="abc123",
        )

    def test_parse_callback_with_signature(self) -> None:
        payload = {
            "status": "polling",
            "state": "3",
            "lastResult": {
                "nu": "YT9693083639795",
                "com": "yuantong",
                "state": "3",
                "data": [{"ftime": "2026-03-11 12:00:00", "context": "您的快件已签收"}],
            },
        }
        param = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        ts = "1710000000"
        sign = hashlib.md5(f"{param}abc123{ts}test-key".encode("utf-8")).hexdigest().upper()
        body = urlencode({"param": param, "sign": sign, "salt": "abc123", "ts": ts}).encode("utf-8")

        parsed = self.client.parse_callback(body, "application/x-www-form-urlencoded")
        snapshot = self.client.extract_snapshot(parsed)
        event = self.client.classify_event(snapshot)

        self.assertEqual(snapshot.tracking_number, "YT9693083639795")
        self.assertEqual(snapshot.kuaidi_state, "3")
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.label, "已签收")

    def test_state_zero_is_preserved(self) -> None:
        snapshot = self.client.extract_snapshot(
            {
                "status": 0,
                "state": 0,
                "nu": "YT9693083639795",
                "data": [{"ftime": "2026-03-11 12:00:00", "context": "快件运输中"}],
            }
        )
        self.assertEqual(snapshot.kuaidi_status, "0")
        self.assertEqual(snapshot.kuaidi_state, "0")


if __name__ == "__main__":
    unittest.main()
