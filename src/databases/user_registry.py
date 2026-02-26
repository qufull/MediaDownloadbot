import sqlite3
import logging
from pathlib import Path
from typing import List, Optional
from contextlib import contextmanager

logger = logging.getLogger("user_registry")


class UserRegistry:
    """SQLite хранилище пользователей с поддержкой реферальной системы."""

    def __init__(self, db_path: str = "data/users.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        logger.info(f"UserRegistry инициализирован: {self.db_path}")

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        """Инициализация таблицы с новыми колонками для рефералов."""
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
                             1,
                             referred_by
                             INTEGER,
                             bonus_limit
                             INTEGER
                             DEFAULT
                             0
                         )
                         """)
            logger.debug("Таблица users проверена/создана")

    def add_user(self, user_id: int, referrer_id: Optional[int] = None) -> bool:
        """
        Регистрирует пользователя.
        Если пользователь новый и пришел по ссылке — начисляет бонус пригласившему.
        """
        try:
            with self._get_connection() as conn:
                # 1. Проверяем, есть ли уже такой пользователь
                cursor = conn.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
                if cursor.fetchone():
                    logger.debug(f"Пользователь {user_id} уже зарегистрирован")
                    return False

                    # 2. Добавляем нового пользователя
                conn.execute(
                    "INSERT INTO users (user_id, is_active, referred_by) VALUES (?, 1, ?)",
                    (user_id, referrer_id)
                )

                # 3. Если есть пригласитель (и это не сам себя) — начисляем ему +2
                if referrer_id and referrer_id != user_id:
                    conn.execute(
                        "UPDATE users SET bonus_limit = bonus_limit + 2 WHERE user_id = ?",
                        (referrer_id,)
                    )
                    logger.info(f"Бонус +2 начислен пользователю {referrer_id} за приглашение {user_id}")

                return True
        except Exception as e:
            logger.error(f"Ошибка при регистрации user_id={user_id}: {e}")
            return False

    def get_bonus(self, user_id: int) -> int:
        """Получить текущий бонусный лимит скачиваний пользователя."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT bonus_limit FROM users WHERE user_id = ?", (user_id,))
                row = cursor.fetchone()
                return row[0] if row else 0
        except Exception as e:
            logger.error(f"Ошибка получения бонуса для {user_id}: {e}")
            return 0

    def deactivate_user(self, user_id: int) -> bool:
        """Пометить пользователя как неактивного (например, если заблокировал бота)."""
        try:
            with self._get_connection() as conn:
                conn.execute("UPDATE users SET is_active = 0 WHERE user_id = ?", (user_id,))
                return True
        except Exception as e:
            logger.error(f"Ошибка деактивации {user_id}: {e}")
            return False

    def get_all_users(self) -> List[int]:
        """Для рассылки: получить список ID всех активных пользователей."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT user_id FROM users WHERE is_active = 1")
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Ошибка получения списка пользователей: {e}")
            return []

    def get_users_count(self) -> int:
        """Общее количество активных пользователей."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM users WHERE is_active = 1")
                return cursor.fetchone()[0]
        except Exception:
            return 0