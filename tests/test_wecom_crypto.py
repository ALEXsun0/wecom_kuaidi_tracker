import base64
import unittest
import xml.etree.ElementTree as ET

from wecom_kuaidi_tracker.wecom_crypto import WeComCrypto


class WeComCryptoTest(unittest.TestCase):
    def test_encrypt_and_decrypt_roundtrip(self) -> None:
        aes_key = b"0123456789abcdef0123456789abcdef"
        encoding_aes_key = base64.b64encode(aes_key).decode("utf-8")[:-1]
        crypto = WeComCrypto(
            token="token123",
            encoding_aes_key=encoding_aes_key,
            receive_id="wwcorp123",
        )

        plaintext = "<xml><Event><![CDATA[kf_msg_or_event]]></Event></xml>"
        timestamp = "1710000000"
        nonce = "nonce123"

        encrypted_xml = crypto.encrypt_message(plaintext, timestamp=timestamp, nonce=nonce)
        root = ET.fromstring(encrypted_xml)
        msg_signature = root.findtext("MsgSignature")
        encrypt = root.findtext("Encrypt")

        self.assertEqual(
            crypto.verify_url(msg_signature or "", timestamp, nonce, encrypt or ""),
            plaintext,
        )
        self.assertEqual(
            crypto.decrypt_message(msg_signature or "", timestamp, nonce, encrypted_xml),
            plaintext,
        )


if __name__ == "__main__":
    unittest.main()
