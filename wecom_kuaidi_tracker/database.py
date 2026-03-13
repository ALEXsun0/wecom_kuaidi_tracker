from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class Database:
    def __init__(self, path: Path) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    external_userid TEXT NOT NULL,
                    open_kfid TEXT NOT NULL,
                    last_user_message_at INTEGER NOT NULL,
                    proactive_count INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (external_userid, open_kfid)
                );

                CREATE TABLE IF NOT EXISTS shipments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    external_userid TEXT NOT NULL,
                    open_kfid TEXT NOT NULL,
                    tracking_number TEXT NOT NULL,
                    phone_tail TEXT NOT NULL,
                    company_code TEXT NOT NULL DEFAULT '',
                    ship_from TEXT NOT NULL DEFAULT '',
                    ship_to TEXT NOT NULL DEFAULT '',
                    subscribe_status TEXT NOT NULL DEFAULT '',
                    subscribe_response TEXT NOT NULL DEFAULT '',
                    kuaidi_status TEXT NOT NULL DEFAULT '',
                    kuaidi_state TEXT NOT NULL DEFAULT '',
                    latest_context TEXT NOT NULL DEFAULT '',
                    latest_time TEXT NOT NULL DEFAULT '',
                    last_payload TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE (external_userid, open_kfid, tracking_number)
                );

                CREATE TABLE IF NOT EXISTS cursors (
                    open_kfid TEXT PRIMARY KEY,
                    next_cursor TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS processed_messages (
                    msgid TEXT PRIMARY KEY,
                    processed_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shipment_id INTEGER NOT NULL,
                    event_key TEXT NOT NULL,
                    send_status TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    UNIQUE (shipment_id, event_key)
                );
                """
            )

    def get_cursor(self, open_kfid: str) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT next_cursor FROM cursors WHERE open_kfid = ?",
                (open_kfid,),
            ).fetchone()
        return row["next_cursor"] if row else ""

    def set_cursor(self, open_kfid: str, next_cursor: str) -> None:
        now = int(time.time())
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO cursors (open_kfid, next_cursor, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(open_kfid) DO UPDATE SET
                    next_cursor = excluded.next_cursor,
                    updated_at = excluded.updated_at
                """,
                (open_kfid, next_cursor, now),
            )

    def remember_processed_message(self, msgid: str) -> bool:
        if not msgid:
            return True
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    "INSERT INTO processed_messages (msgid, processed_at) VALUES (?, ?)",
                    (msgid, int(time.time())),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def touch_conversation(self, external_userid: str, open_kfid: str, send_time: int) -> None:
        now = int(time.time())
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO conversations (
                    external_userid,
                    open_kfid,
                    last_user_message_at,
                    proactive_count,
                    updated_at
                )
                VALUES (?, ?, ?, 0, ?)
                ON CONFLICT(external_userid, open_kfid) DO UPDATE SET
                    last_user_message_at = excluded.last_user_message_at,
                    proactive_count = 0,
                    updated_at = excluded.updated_at
                """,
                (external_userid, open_kfid, send_time, now),
            )

    def can_send_proactive(self, external_userid: str, open_kfid: str, now_ts: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT last_user_message_at, proactive_count
                FROM conversations
                WHERE external_userid = ? AND open_kfid = ?
                """,
                (external_userid, open_kfid),
            ).fetchone()
        if not row:
            return False
        within_window = now_ts <= int(row["last_user_message_at"]) + 48 * 3600
        under_quota = int(row["proactive_count"]) < 5
        return within_window and under_quota

    def increment_proactive_count(self, external_userid: str, open_kfid: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE conversations
                SET proactive_count = proactive_count + 1, updated_at = ?
                WHERE external_userid = ? AND open_kfid = ?
                """,
                (int(time.time()), external_userid, open_kfid),
            )

    def upsert_shipment(
        self,
        *,
        external_userid: str,
        open_kfid: str,
        tracking_number: str,
        phone_tail: str,
        company_code: str,
        ship_from: str,
        ship_to: str,
        subscribe_status: str,
        subscribe_response: str,
    ) -> int:
        now = int(time.time())
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO shipments (
                    external_userid,
                    open_kfid,
                    tracking_number,
                    phone_tail,
                    company_code,
                    ship_from,
                    ship_to,
                    subscribe_status,
                    subscribe_response,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(external_userid, open_kfid, tracking_number) DO UPDATE SET
                    phone_tail = excluded.phone_tail,
                    company_code = excluded.company_code,
                    ship_from = excluded.ship_from,
                    ship_to = excluded.ship_to,
                    subscribe_status = excluded.subscribe_status,
                    subscribe_response = excluded.subscribe_response,
                    updated_at = excluded.updated_at
                """,
                (
                    external_userid,
                    open_kfid,
                    tracking_number,
                    phone_tail,
                    company_code,
                    ship_from,
                    ship_to,
                    subscribe_status,
                    subscribe_response,
                    now,
                    now,
                ),
            )
            row = self._conn.execute(
                """
                SELECT id FROM shipments
                WHERE external_userid = ? AND open_kfid = ? AND tracking_number = ?
                """,
                (external_userid, open_kfid, tracking_number),
            ).fetchone()
        return int(row["id"])

    def update_shipment_snapshot(
        self,
        tracking_number: str,
        *,
        kuaidi_status: str,
        kuaidi_state: str,
        latest_context: str,
        latest_time: str,
        raw_payload: str,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE shipments
                SET kuaidi_status = ?,
                    kuaidi_state = ?,
                    latest_context = ?,
                    latest_time = ?,
                    last_payload = ?,
                    updated_at = ?
                WHERE tracking_number = ?
                """,
                (
                    kuaidi_status,
                    kuaidi_state,
                    latest_context,
                    latest_time,
                    raw_payload,
                    int(time.time()),
                    tracking_number,
                ),
            )

    def find_shipments_by_tracking(self, tracking_number: str) -> list[sqlite3.Row]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM shipments WHERE tracking_number = ?",
                (tracking_number,),
            ).fetchall()
        return list(rows)

    def claim_notification(self, shipment_id: int, event_key: str, payload: str) -> bool:
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    """
                    INSERT INTO notifications (shipment_id, event_key, send_status, payload, created_at)
                    VALUES (?, ?, 'pending', ?, ?)
                    """,
                    (shipment_id, event_key, payload, int(time.time())),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def finish_notification(self, shipment_id: int, event_key: str, send_status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE notifications
                SET send_status = ?
                WHERE shipment_id = ? AND event_key = ?
                """,
                (send_status, shipment_id, event_key),
            )
