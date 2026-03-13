from __future__ import annotations

import base64
import hashlib
import os
import shutil
import struct
import subprocess
import time
import xml.etree.ElementTree as ET


class WeComCryptoError(RuntimeError):
    """Raised when callback verification or decryption fails."""


class WeComCrypto:
    def __init__(self, token: str, encoding_aes_key: str, receive_id: str) -> None:
        self.token = token
        self.receive_id = receive_id
        self.aes_key = base64.b64decode(f"{encoding_aes_key}=")
        if len(self.aes_key) != 32:
            raise WeComCryptoError("invalid WECOM_ENCODING_AES_KEY length")
        self.iv = self.aes_key[:16]
        self.openssl = shutil.which("openssl")
        if not self.openssl:
            raise WeComCryptoError("openssl binary is required for enterprise wechat callback decryption")

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        expected = self._signature(timestamp, nonce, echostr)
        if expected != msg_signature:
            raise WeComCryptoError("wecom callback signature mismatch")
        return self._decrypt_ciphertext(echostr)

    def decrypt_message(self, msg_signature: str, timestamp: str, nonce: str, body_xml: str) -> str:
        root = ET.fromstring(body_xml)
        encrypt_node = root.find("Encrypt")
        encrypted = encrypt_node.text.strip() if encrypt_node is not None and encrypt_node.text else ""
        if not encrypted:
            raise WeComCryptoError("missing Encrypt field in wecom callback")
        expected = self._signature(timestamp, nonce, encrypted)
        if expected != msg_signature:
            raise WeComCryptoError("wecom callback signature mismatch")
        return self._decrypt_ciphertext(encrypted)

    def encrypt_message(self, plaintext: str, timestamp: str | None = None, nonce: str | None = None) -> str:
        timestamp = timestamp or str(int(time.time()))
        nonce = nonce or os.urandom(8).hex()
        plain = self._pack_plaintext(plaintext)
        encrypted = self._openssl(
            [
                "enc",
                "-aes-256-cbc",
                "-nopad",
                "-K",
                self.aes_key.hex(),
                "-iv",
                self.iv.hex(),
            ],
            self._pad(plain),
        )
        ciphertext = base64.b64encode(encrypted).decode("utf-8")
        signature = self._signature(timestamp, nonce, ciphertext)
        return (
            "<xml>"
            f"<Encrypt><![CDATA[{ciphertext}]]></Encrypt>"
            f"<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
            f"<TimeStamp>{timestamp}</TimeStamp>"
            f"<Nonce><![CDATA[{nonce}]]></Nonce>"
            "</xml>"
        )

    def _pack_plaintext(self, plaintext: str) -> bytes:
        message = plaintext.encode("utf-8")
        receive_id = self.receive_id.encode("utf-8")
        return os.urandom(16) + struct.pack(">I", len(message)) + message + receive_id

    def _signature(self, timestamp: str, nonce: str, encrypted: str) -> str:
        pieces = sorted([self.token, timestamp, nonce, encrypted])
        return hashlib.sha1("".join(pieces).encode("utf-8")).hexdigest()

    def _decrypt_ciphertext(self, encrypted: str) -> str:
        ciphertext = base64.b64decode(encrypted)
        padded = self._openssl(
            [
                "enc",
                "-aes-256-cbc",
                "-d",
                "-nopad",
                "-K",
                self.aes_key.hex(),
                "-iv",
                self.iv.hex(),
            ],
            ciphertext,
        )
        plain = self._unpad(padded)
        if len(plain) < 20:
            raise WeComCryptoError("decrypted payload too short")
        content = plain[16:]
        msg_len = struct.unpack(">I", content[:4])[0]
        if msg_len < 0 or msg_len > len(content) - 4:
            raise WeComCryptoError("invalid decrypted message length")
        msg = content[4 : 4 + msg_len]
        receive_id = content[4 + msg_len :].decode("utf-8")
        if receive_id != self.receive_id:
            raise WeComCryptoError("receive_id mismatch")
        return msg.decode("utf-8")

    def _openssl(self, args: list[str], payload: bytes) -> bytes:
        proc = subprocess.run(
            [self.openssl, *args],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0:
            raise WeComCryptoError(proc.stderr.decode("utf-8").strip() or "openssl failed")
        return proc.stdout

    @staticmethod
    def _pad(payload: bytes) -> bytes:
        pad_length = 32 - (len(payload) % 32)
        if pad_length == 0:
            pad_length = 32
        return payload + bytes([pad_length]) * pad_length

    @staticmethod
    def _unpad(payload: bytes) -> bytes:
        if not payload:
            raise WeComCryptoError("empty decrypted payload")
        pad_length = payload[-1]
        if pad_length < 1 or pad_length > 32:
            raise WeComCryptoError("invalid PKCS7 padding")
        if payload[-pad_length:] != bytes([pad_length]) * pad_length:
            raise WeComCryptoError("invalid PKCS7 padding bytes")
        return payload[:-pad_length]
