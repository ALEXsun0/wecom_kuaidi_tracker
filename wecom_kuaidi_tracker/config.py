from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    base_url: str
    db_path: Path
    kuaidi100_key: str
    kuaidi100_customer: str
    kuaidi100_salt: str
    kuaidi100_callback_url: str
    kuaidi100_default_from: str
    kuaidi100_default_to: str
    wecom_corp_id: str
    wecom_corp_secret: str
    wecom_token: str
    wecom_encoding_aes_key: str
    wecom_receive_id: str

    @classmethod
    def from_env(cls, root: Path | None = None) -> "Settings":
        project_root = root or Path.cwd()
        load_dotenv(project_root / ".env")

        base_url = os.getenv("BASE_URL", "").strip().rstrip("/")
        callback_url = os.getenv("KUAIDI100_CALLBACK_URL", "").strip()
        if not callback_url and base_url:
            callback_url = f"{base_url}/callbacks/kuaidi100"

        db_path = Path(os.getenv("DB_PATH", project_root / "data" / "wecom_kuaidi_tracker.db"))
        db_path.parent.mkdir(parents=True, exist_ok=True)

        corp_id = require_env("WECOM_CORP_ID")
        return cls(
            host=os.getenv("APP_HOST", "0.0.0.0"),
            port=int(os.getenv("APP_PORT", "8000")),
            base_url=base_url,
            db_path=db_path,
            kuaidi100_key=require_env("KUAIDI100_KEY"),
            kuaidi100_customer=os.getenv("KUAIDI100_CUSTOMER", "").strip(),
            kuaidi100_salt=os.getenv("KUAIDI100_SALT", "").strip(),
            kuaidi100_callback_url=callback_url,
            kuaidi100_default_from=os.getenv("KUAIDI100_DEFAULT_FROM", "").strip(),
            kuaidi100_default_to=os.getenv("KUAIDI100_DEFAULT_TO", "").strip(),
            wecom_corp_id=corp_id,
            wecom_corp_secret=require_env("WECOM_CORP_SECRET"),
            wecom_token=require_env("WECOM_TOKEN"),
            wecom_encoding_aes_key=require_env("WECOM_ENCODING_AES_KEY"),
            wecom_receive_id=os.getenv("WECOM_RECEIVE_ID", corp_id).strip() or corp_id,
        )
