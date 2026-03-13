import unittest

from wecom_kuaidi_tracker.message_parser import parse_subscription_request


class MessageParserTest(unittest.TestCase):
    def test_parse_simple_message(self) -> None:
        request = parse_subscription_request("YT9693083639795 3975")
        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request.tracking_number, "YT9693083639795")
        self.assertEqual(request.phone_tail, "3975")

    def test_parse_labeled_message(self) -> None:
        request = parse_subscription_request(
            "单号: YT9693083639795 手机号后四位: 3975 公司: yuantong 发货地: 江门市 收货地: 深圳市"
        )
        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request.company_code, "yuantong")
        self.assertEqual(request.ship_from, "江门市")
        self.assertEqual(request.ship_to, "深圳市")

    def test_parse_requires_tracking_and_phone(self) -> None:
        self.assertIsNone(parse_subscription_request("帮我查下这个快递"))


if __name__ == "__main__":
    unittest.main()
