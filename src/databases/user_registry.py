import sqlite3
import logging
from pathlib import Path
from typing import List
from contextlib import contextmanager

logger = logging.getLogger("user_registry")


class UserRegistry:
    """SQLite хранилище пользователей для рассылки."""

    def __init__(self, db_path: str = "data/users.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        logger.info(f"UserRegistry: {self.db_path}")

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("""
                         CREATE TABLE IF NOT EXISTS users
                         (
                             user_id
                             INTEGER
                             PRIMARY
                             KEY,
                             is_active
                             INTEGER
                             DEFAULT
                             1
                         )
                         """)

    def add_user(self, user_id: int, **kwargs) -> bool:
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO users (user_id, is_active) VALUES (?, 1)",
                    (user_id,)
                )
                return True
        except Exception as e:
            logger.error(f"Ошибка добавления user_id={user_id}: {e}")
            return False

    def deactivate_user(self, user_id: int) -> bool:
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE users SET is_active = 0 WHERE user_id = ?",
                    (user_id,)
                )
                return True
        except Exception as e:
            logger.error(f"Ошибка деактивации user_id={user_id}: {e}")
            return False

    def get_all_users(self) -> List[int]:
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT user_id FROM users WHERE is_active = 1"
                )
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Ошибка получения пользователей: {e}")
            return []

    def get_users_count(self) -> int:
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM users WHERE is_active = 1"
                )
                return cursor.fetchone()[0]
        except Exception:
            return 0