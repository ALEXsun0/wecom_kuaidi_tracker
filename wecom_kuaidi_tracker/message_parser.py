from __future__ import annotations

import re
from dataclasses import dataclass


TRACKING_LABEL_RE = re.compile(
    r"(?:单号|运单号|快递单号|tracking)\s*[:： ]\s*([A-Za-z0-9-]{8,})",
    re.IGNORECASE,
)
PHONE_LABEL_RE = re.compile(
    r"(?:手机(?:号)?后四位|手机号后四位|尾号|phone)\s*[:： ]\s*(\d{4})",
    re.IGNORECASE,
)
COMPANY_LABEL_RE = re.compile(
    r"(?:公司|快递公司|company)\s*[:： ]\s*([A-Za-z0-9_]+)",
    re.IGNORECASE,
)
FROM_LABEL_RE = re.compile(r"(?:发货地|寄出地|始发地|from)\s*[:： ]\s*([^\s,，;；]+)", re.IGNORECASE)
TO_LABEL_RE = re.compile(r"(?:收货地|目的地|to)\s*[:： ]\s*([^\s,，;；]+)", re.IGNORECASE)
TOKEN_RE = re.compile(r"[A-Za-z0-9-]{4,}")


@dataclass(frozen=True)
class SubscriptionRequest:
    tracking_number: str
    phone_tail: str
    company_code: str = ""
    ship_from: str = ""
    ship_to: str = ""


def _pick_tracking_number(text: str, phone_tail: str) -> str:
    label_match = TRACKING_LABEL_RE.search(text)
    if label_match:
        return label_match.group(1)

    tokens = TOKEN_RE.findall(text)
    candidates = []
    for token in tokens:
        if token == phone_tail:
            continue
        has_letters = any(char.isalpha() for char in token)
        if has_letters or len(token) >= 8:
            candidates.append(token)
    if not candidates:
        return ""
    candidates.sort(key=lambda item: (len(item), any(char.isalpha() for char in item)), reverse=True)
    return candidates[0]


def parse_subscription_request(text: str) -> SubscriptionRequest | None:
    stripped = text.strip()
    if not stripped:
        return None

    phone_match = PHONE_LABEL_RE.search(stripped)
    phone_tail = phone_match.group(1) if phone_match else ""
    if not phone_tail:
        digit_tokens = re.findall(r"(?<!\d)(\d{4})(?!\d)", stripped)
        phone_tail = digit_tokens[-1] if digit_tokens else ""

    tracking_number = _pick_tracking_number(stripped, phone_tail)
    if not tracking_number or not phone_tail:
        return None

    company_match = COMPANY_LABEL_RE.search(stripped)
    ship_from_match = FROM_LABEL_RE.search(stripped)
    ship_to_match = TO_LABEL_RE.search(stripped)

    return SubscriptionRequest(
        tracking_number=tracking_number,
        phone_tail=phone_tail,
        company_code=company_match.group(1) if company_match else "",
        ship_from=ship_from_match.group(1) if ship_from_match else "",
        ship_to=ship_to_match.group(1) if ship_to_match else "",
    )
