from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass


class Kuaidi100Error(RuntimeError):
    """Raised when kuaidi100 API invocation fails."""


@dataclass(frozen=True)
class TrackingSnapshot:
    tracking_number: str
    company_code: str
    kuaidi_status: str
    kuaidi_state: str
    latest_context: str
    latest_time: str


@dataclass(frozen=True)
class TrackingEvent:
    event_type: str
    label: str
    event_key: str


class Kuaidi100Client:
    poll_url = "https://poll.kuaidi100.com/poll"

    def __init__(
        self,
        *,
        key: str,
        callback_url: str,
        salt: str = "",
        default_from: str = "",
        default_to: str = "",
    ) -> None:
        self.key = key
        self.callback_url = callback_url
        self.salt = salt
        self.default_from = default_from
        self.default_to = default_to

    def subscribe(
        self,
        *,
        tracking_number: str,
        phone_tail: str,
        company_code: str = "",
        ship_from: str = "",
        ship_to: str = "",
    ) -> dict:
        if not self.callback_url:
            raise Kuaidi100Error("KUAIDI100_CALLBACK_URL or BASE_URL is required for subscription")

        company_code = company_code.strip()
        payload = {
            "company": company_code,
            "number": tracking_number,
            "key": self.key,
            "parameters": {
                "callbackurl": self.callback_url,
                "resultv2": "1",
                "autoCom": "0" if company_code else "1",
                "interCom": "0",
                "phone": phone_tail,
            },
        }
        if ship_from or self.default_from:
            payload["from"] = ship_from or self.default_from
        if ship_to or self.default_to:
            payload["to"] = ship_to or self.default_to
        if self.salt:
            payload["parameters"]["salt"] = self.salt

        body = urllib.parse.urlencode(
            {
                "schema": "json",
                "param": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.poll_url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}

    def parse_callback(self, body: bytes, content_type: str) -> dict:
        body_text = body.decode("utf-8")
        if "application/json" in content_type:
            return json.loads(body_text)

        form = urllib.parse.parse_qs(body_text, keep_blank_values=True)
        param = self._first(form, "param")
        if not param:
            raise Kuaidi100Error("missing param field in kuaidi100 callback")

        sign = self._first(form, "sign")
        salt = self._first(form, "salt") or self.salt
        ts = self._first(form, "ts")
        if sign:
            expected = hashlib.md5(f"{param}{salt}{ts}{self.key}".encode("utf-8")).hexdigest().upper()
            if expected != sign.upper():
                raise Kuaidi100Error("kuaidi100 callback signature mismatch")
        return json.loads(param)

    @staticmethod
    def extract_snapshot(payload: dict) -> TrackingSnapshot:
        latest_result = payload.get("lastResult")
        result = latest_result if isinstance(latest_result, dict) else payload
        entries = result.get("data")
        if not isinstance(entries, list):
            entries = payload.get("data") if isinstance(payload.get("data"), list) else []
        latest = entries[0] if entries else {}

        tracking_number = str(
            result.get("nu")
            or payload.get("nu")
            or result.get("number")
            or payload.get("number")
            or ""
        )
        company_code = str(
            result.get("com")
            or payload.get("comNew")
            or payload.get("com")
            or ""
        )
        kuaidi_status = _string_or_empty(_coalesce(payload.get("status"), result.get("status")))
        kuaidi_state = _string_or_empty(_coalesce(result.get("state"), payload.get("state")))
        latest_context = _string_or_empty(
            latest.get("context") or latest.get("status") or result.get("message")
        )
        latest_time = _string_or_empty(latest.get("ftime") or latest.get("time"))

        return TrackingSnapshot(
            tracking_number=tracking_number,
            company_code=company_code,
            kuaidi_status=kuaidi_status,
            kuaidi_state=kuaidi_state,
            latest_context=latest_context,
            latest_time=latest_time,
        )

    @staticmethod
    def classify_event(snapshot: TrackingSnapshot) -> TrackingEvent | None:
        state_map = {
            "1": ("picked_up", "已揽收"),
            "3": ("delivered", "已签收"),
            "5": ("out_for_delivery", "派送中"),
            "2": ("exception", "物流异常"),
            "4": ("returned", "已退签"),
            "6": ("returned", "已退回"),
        }
        fallback_rules = (
            ("签收", ("delivered", "已签收")),
            ("妥投", ("delivered", "已签收")),
            ("派送", ("out_for_delivery", "派送中")),
            ("派件", ("out_for_delivery", "派送中")),
            ("揽收", ("picked_up", "已揽收")),
            ("发货", ("picked_up", "已发货")),
            ("发出", ("picked_up", "已发货")),
            ("退回", ("returned", "已退回")),
            ("退签", ("returned", "已退签")),
            ("异常", ("exception", "物流异常")),
            ("问题件", ("exception", "物流异常")),
        )

        event = state_map.get(snapshot.kuaidi_state)
        if not event:
            for keyword, matched in fallback_rules:
                if keyword in snapshot.latest_context:
                    event = matched
                    break
        if not event:
            return None

        event_type, label = event
        key = "|".join(
            part
            for part in (
                event_type,
                snapshot.latest_time or "-",
                snapshot.latest_context[:80] or snapshot.kuaidi_state or "-",
            )
        )
        return TrackingEvent(event_type=event_type, label=label, event_key=key)

    @staticmethod
    def _first(form: dict[str, list[str]], key: str) -> str:
        values = form.get(key)
        return values[0] if values else ""


def _string_or_empty(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _coalesce(*values: object) -> object | None:
    for value in values:
        if value is not None:
            return value
    return None
