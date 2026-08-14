import sqlite3
import time
from pathlib import Path


class DeliveryStore:
    """Small durable idempotency store keyed by GitHub's delivery GUID."""

    def __init__(self, database_path: str):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS webhook_deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    response_id TEXT,
                    last_error TEXT,
                    updated_at REAL NOT NULL
                )
                """
            )

    def claim(self, delivery_id: str, stale_after_seconds: int) -> bool:
        now = time.time()
        stale_before = now - stale_after_seconds
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, updated_at FROM webhook_deliveries WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO webhook_deliveries
                        (delivery_id, status, attempts, updated_at)
                    VALUES (?, 'processing', 1, ?)
                    """,
                    (delivery_id, now),
                )
                return True
            if row["status"] == "failed" or (
                row["status"] == "processing" and row["updated_at"] < stale_before
            ):
                connection.execute(
                    """
                    UPDATE webhook_deliveries
                    SET status = 'processing', attempts = attempts + 1,
                        last_error = NULL, updated_at = ?
                    WHERE delivery_id = ?
                    """,
                    (now, delivery_id),
                )
                return True
            return False

    def complete(self, delivery_id: str, response_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE webhook_deliveries
                SET status = 'completed', response_id = ?, last_error = NULL,
                    updated_at = ?
                WHERE delivery_id = ?
                """,
                (response_id, time.time(), delivery_id),
            )

    def fail(self, delivery_id: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE webhook_deliveries
                SET status = 'failed', last_error = ?, updated_at = ?
                WHERE delivery_id = ?
                """,
                (error[:2000], time.time(), delivery_id),
            )

    def status(self, delivery_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT delivery_id, status, attempts, response_id, last_error, updated_at
                FROM webhook_deliveries
                WHERE delivery_id = ?
                """,
                (delivery_id,),
            ).fetchone()
        return dict(row) if row is not None else None
