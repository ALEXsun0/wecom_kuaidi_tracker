from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request


class WeComAPIError(RuntimeError):
    def __init__(self, code: int, message: str, response: dict | None = None) -> None:
        super().__init__(f"WeCom API error {code}: {message}")
        self.code = code
        self.message = message
        self.response = response or {}


class WeComClient:
    def __init__(self, corp_id: str, corp_secret: str) -> None:
        self.corp_id = corp_id
        self.corp_secret = corp_secret
        self._token_lock = threading.Lock()
        self._access_token = ""
        self._expires_at = 0.0

    def get_access_token(self, force_refresh: bool = False) -> str:
        with self._token_lock:
            now = time.time()
            if not force_refresh and self._access_token and now < self._expires_at - 60:
                return self._access_token

            query = urllib.parse.urlencode(
                {"corpid": self.corp_id, "corpsecret": self.corp_secret}
            )
            url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?{query}"
            response = self._request_json(urllib.request.Request(url, method="GET"))
            errcode = int(response.get("errcode", 0))
            if errcode != 0:
                raise WeComAPIError(errcode, str(response.get("errmsg", "unknown error")), response)

            self._access_token = str(response["access_token"])
            self._expires_at = now + int(response.get("expires_in", 7200))
            return self._access_token

    def sync_messages(self, *, open_kfid: str, callback_token: str, cursor: str = "") -> dict:
        payload: dict[str, object] = {
            "token": callback_token,
            "limit": 1000,
            "voice_format": 0,
            "open_kfid": open_kfid,
        }
        if cursor:
            payload["cursor"] = cursor
        return self._post_api("/cgi-bin/kf/sync_msg", payload)

    def send_text(self, *, external_userid: str, open_kfid: str, content: str) -> dict:
        payload = {
            "touser": external_userid,
            "open_kfid": open_kfid,
            "msgtype": "text",
            "text": {"content": content},
        }
        return self._post_api("/cgi-bin/kf/send_msg", payload)

    def _post_api(self, path: str, payload: dict) -> dict:
        retriable_codes = {40014, 42001}
        for attempt in range(2):
            access_token = self.get_access_token(force_refresh=attempt > 0)
            url = (
                "https://qyapi.weixin.qq.com"
                f"{path}?access_token={urllib.parse.quote(access_token)}"
            )
            request = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            response = self._request_json(request)
            errcode = int(response.get("errcode", 0))
            if errcode == 0:
                return response
            if errcode in retriable_codes and attempt == 0:
                continue
            raise WeComAPIError(errcode, str(response.get("errmsg", "unknown error")), response)
        raise WeComAPIError(-1, "unexpected token refresh flow")

    @staticmethod
    def _request_json(request: urllib.request.Request) -> dict:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8")
        return json.loads(body)
