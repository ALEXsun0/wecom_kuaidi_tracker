from __future__ import annotations

import json
import logging
import time
import xml.etree.ElementTree as ET
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from wecom_kuaidi_tracker.config import Settings
from wecom_kuaidi_tracker.database import Database
from wecom_kuaidi_tracker.kuaidi100_client import Kuaidi100Client, Kuaidi100Error
from wecom_kuaidi_tracker.message_parser import SubscriptionRequest, parse_subscription_request
from wecom_kuaidi_tracker.wecom_client import WeComAPIError, WeComClient
from wecom_kuaidi_tracker.wecom_crypto import WeComCrypto, WeComCryptoError


LOG = logging.getLogger(__name__)


class Application:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings.db_path)
        self.wecom_crypto = WeComCrypto(
            settings.wecom_token,
            settings.wecom_encoding_aes_key,
            settings.wecom_receive_id,
        )
        self.wecom = WeComClient(settings.wecom_corp_id, settings.wecom_corp_secret)
        self.kuaidi100 = Kuaidi100Client(
            key=settings.kuaidi100_key,
            callback_url=settings.kuaidi100_callback_url,
            salt=settings.kuaidi100_salt,
            default_from=settings.kuaidi100_default_from,
            default_to=settings.kuaidi100_default_to,
        )

    def handle_health(self) -> bytes:
        return json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")

    def handle_wecom_verify(self, query: str) -> bytes:
        params = parse_qs(query, keep_blank_values=True)
        plaintext = self.wecom_crypto.verify_url(
            self._query_arg(params, "msg_signature"),
            self._query_arg(params, "timestamp"),
            self._query_arg(params, "nonce"),
            self._query_arg(params, "echostr"),
        )
        return plaintext.encode("utf-8")

    def handle_wecom_callback(self, query: str, body: bytes) -> bytes:
        params = parse_qs(query, keep_blank_values=True)
        plaintext = self.wecom_crypto.decrypt_message(
            self._query_arg(params, "msg_signature"),
            self._query_arg(params, "timestamp"),
            self._query_arg(params, "nonce"),
            body.decode("utf-8"),
        )
        root = ET.fromstring(plaintext)
        if self._xml_text(root, "Event") == "kf_msg_or_event":
            callback_token = self._xml_text(root, "Token")
            open_kfid = self._xml_text(root, "OpenKfId")
            self._process_wecom_event(callback_token=callback_token, open_kfid=open_kfid)
        return b"success"

    def handle_kuaidi100_callback(self, body: bytes, content_type: str) -> bytes:
        payload = self.kuaidi100.parse_callback(body, content_type)
        snapshot = self.kuaidi100.extract_snapshot(payload)
        if not snapshot.tracking_number:
            raise Kuaidi100Error("tracking number missing from kuaidi100 payload")

        payload_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        self.db.update_shipment_snapshot(
            snapshot.tracking_number,
            kuaidi_status=snapshot.kuaidi_status,
            kuaidi_state=snapshot.kuaidi_state,
            latest_context=snapshot.latest_context,
            latest_time=snapshot.latest_time,
            raw_payload=payload_text,
        )

        event = self.kuaidi100.classify_event(snapshot)
        if event:
            for shipment in self.db.find_shipments_by_tracking(snapshot.tracking_number):
                shipment_id = int(shipment["id"])
                if not self.db.claim_notification(shipment_id, event.event_key, payload_text):
                    continue
                external_userid = str(shipment["external_userid"])
                open_kfid = str(shipment["open_kfid"])
                now_ts = int(time.time())
                if self.db.can_send_proactive(external_userid, open_kfid, now_ts):
                    message = self._build_tracking_message(snapshot, event.label)
                    sent, reason = self._safe_send_text(
                        external_userid=external_userid,
                        open_kfid=open_kfid,
                        content=message,
                        now_ts=now_ts,
                    )
                    self.db.finish_notification(
                        shipment_id,
                        event.event_key,
                        "sent" if sent else f"failed:{reason}",
                    )
                else:
                    self.db.finish_notification(shipment_id, event.event_key, "suppressed:window_closed")

        return json.dumps(
            {"result": True, "returnCode": "200", "message": "成功"},
            ensure_ascii=False,
        ).encode("utf-8")

    def _process_wecom_event(self, *, callback_token: str, open_kfid: str) -> None:
        cursor = self.db.get_cursor(open_kfid)
        for _ in range(20):
            response = self.wecom.sync_messages(
                open_kfid=open_kfid,
                callback_token=callback_token,
                cursor=cursor,
            )
            for message in response.get("msg_list", []):
                msgid = str(message.get("msgid", ""))
                if not self.db.remember_processed_message(msgid):
                    continue
                self._process_synced_message(message)

            next_cursor = str(response.get("next_cursor", cursor))
            if next_cursor:
                self.db.set_cursor(open_kfid, next_cursor)
                cursor = next_cursor

            if int(response.get("has_more", 0)) != 1:
                return
        raise RuntimeError("sync_msg exceeded the safety page limit")

    def _process_synced_message(self, message: dict) -> None:
        if int(message.get("origin", 0)) != 3:
            return
        if message.get("msgtype") != "text":
            return

        text_payload = message.get("text") or {}
        content = str(text_payload.get("content", "")).strip()
        if not content:
            return

        external_userid = str(message.get("external_userid", ""))
        open_kfid = str(message.get("open_kfid", ""))
        if not external_userid or not open_kfid:
            return

        send_time = _normalize_ts(message.get("send_time"))
        self.db.touch_conversation(external_userid, open_kfid, send_time)

        request = parse_subscription_request(content)
        if not request:
            self._safe_send_text(
                external_userid=external_userid,
                open_kfid=open_kfid,
                content=self._help_message(),
                now_ts=send_time,
            )
            return

        try:
            subscribe_response = self.kuaidi100.subscribe(
                tracking_number=request.tracking_number,
                phone_tail=request.phone_tail,
                company_code=request.company_code,
                ship_from=request.ship_from,
                ship_to=request.ship_to,
            )
        except Exception as exc:
            LOG.exception("kuaidi100 subscribe failed for %s", request.tracking_number)
            self._safe_send_text(
                external_userid=external_userid,
                open_kfid=open_kfid,
                content=f"订阅失败：{exc}",
                now_ts=send_time,
            )
            return

        subscribe_status, subscribe_message = self._read_subscribe_result(subscribe_response)
        self.db.upsert_shipment(
            external_userid=external_userid,
            open_kfid=open_kfid,
            tracking_number=request.tracking_number,
            phone_tail=request.phone_tail,
            company_code=request.company_code,
            ship_from=request.ship_from,
            ship_to=request.ship_to,
            subscribe_status=subscribe_status,
            subscribe_response=json.dumps(subscribe_response, ensure_ascii=False),
        )

        if subscribe_status == "success":
            reply = self._subscription_success_message(request, subscribe_message)
        else:
            reply = f"订阅失败：{subscribe_message}"
        self._safe_send_text(
            external_userid=external_userid,
            open_kfid=open_kfid,
            content=reply,
            now_ts=send_time,
        )

    def _safe_send_text(
        self,
        *,
        external_userid: str,
        open_kfid: str,
        content: str,
        now_ts: int | None = None,
    ) -> tuple[bool, str]:
        now_ts = now_ts or int(time.time())
        if not self.db.can_send_proactive(external_userid, open_kfid, now_ts):
            return False, "outside wecom proactive window"
        try:
            self.wecom.send_text(
                external_userid=external_userid,
                open_kfid=open_kfid,
                content=content,
            )
        except WeComAPIError as exc:
            LOG.warning("send wecom message failed: %s", exc)
            return False, exc.message
        self.db.increment_proactive_count(external_userid, open_kfid)
        return True, "ok"

    @staticmethod
    def _read_subscribe_result(response: dict) -> tuple[str, str]:
        success = str(response.get("returnCode", "")) == "200" or bool(response.get("result"))
        message = str(
            response.get("message")
            or response.get("reason")
            or response.get("errmsg")
            or response.get("raw")
            or "提交成功"
        )
        return ("success" if success else "failed"), message

    @staticmethod
    def _help_message() -> str:
        return (
            "请发送：快递单号 + 手机号后四位\n"
            "示例：YT9693083639795 3975\n"
            "可选：公司:yuantong 发货地:江门市 收货地:深圳市"
        )

    @staticmethod
    def _subscription_success_message(request: SubscriptionRequest, message: str) -> str:
        return (
            f"已订阅单号 {request.tracking_number}。\n"
            f"手机号后四位：{request.phone_tail}\n"
            f"{message}\n"
            "后续会在揽收、派送、签收等关键节点提醒。"
        )

    @staticmethod
    def _build_tracking_message(snapshot, label: str) -> str:
        parts = [
            "物流提醒",
            f"单号：{snapshot.tracking_number}",
            f"状态：{label}",
        ]
        if snapshot.latest_time:
            parts.append(f"时间：{snapshot.latest_time}")
        if snapshot.latest_context:
            parts.append(snapshot.latest_context)
        return "\n".join(parts)

    @staticmethod
    def _query_arg(params: dict[str, list[str]], name: str) -> str:
        values = params.get(name)
        return values[0] if values else ""

    @staticmethod
    def _xml_text(root: ET.Element, tag: str) -> str:
        node = root.find(tag)
        return node.text.strip() if node is not None and node.text else ""


class AppHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], app: Application) -> None:
        super().__init__(server_address, RequestHandler)
        self.app = app


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "PacketSub/0.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/healthz":
                self._send_bytes(HTTPStatus.OK, "application/json; charset=utf-8", self.server.app.handle_health())
                return
            if parsed.path == "/callbacks/wecom":
                body = self.server.app.handle_wecom_verify(parsed.query)
                self._send_bytes(HTTPStatus.OK, "text/plain; charset=utf-8", body)
                return
            self._send_bytes(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"not found")
        except Exception as exc:
            LOG.exception("GET %s failed", parsed.path)
            self._send_bytes(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "text/plain; charset=utf-8",
                str(exc).encode("utf-8"),
            )

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")

        try:
            if parsed.path == "/callbacks/wecom":
                payload = self.server.app.handle_wecom_callback(parsed.query, body)
                self._send_bytes(HTTPStatus.OK, "text/plain; charset=utf-8", payload)
                return
            if parsed.path == "/callbacks/kuaidi100":
                payload = self.server.app.handle_kuaidi100_callback(body, content_type)
                self._send_bytes(HTTPStatus.OK, "application/json; charset=utf-8", payload)
                return
            self._send_bytes(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"not found")
        except (WeComCryptoError, WeComAPIError, Kuaidi100Error, ValueError) as exc:
            LOG.warning("POST %s rejected: %s", parsed.path, exc)
            if parsed.path == "/callbacks/kuaidi100":
                payload = json.dumps(
                    {"result": False, "returnCode": "500", "message": str(exc)},
                    ensure_ascii=False,
                ).encode("utf-8")
                self._send_bytes(HTTPStatus.BAD_REQUEST, "application/json; charset=utf-8", payload)
            else:
                self._send_bytes(HTTPStatus.BAD_REQUEST, "text/plain; charset=utf-8", str(exc).encode("utf-8"))
        except Exception as exc:
            LOG.exception("POST %s failed", parsed.path)
            if parsed.path == "/callbacks/kuaidi100":
                payload = json.dumps(
                    {"result": False, "returnCode": "500", "message": str(exc)},
                    ensure_ascii=False,
                ).encode("utf-8")
                self._send_bytes(HTTPStatus.INTERNAL_SERVER_ERROR, "application/json; charset=utf-8", payload)
            else:
                self._send_bytes(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "text/plain; charset=utf-8",
                    str(exc).encode("utf-8"),
                )

    def log_message(self, fmt: str, *args) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def _send_bytes(self, status: HTTPStatus, content_type: str, payload: bytes) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()
    app = Application(settings)
    server = AppHTTPServer((settings.host, settings.port), app)
    LOG.info("listening on %s:%s", settings.host, settings.port)
    server.serve_forever()


def _normalize_ts(value: object) -> int:
    if value is None:
        return int(time.time())
    number = int(value)
    if number > 10_000_000_000:
        number //= 1000
    return number
