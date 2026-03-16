import sqlite3
import logging
import time
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
        """Инициализация таблицы с новыми колонками для премиума и юзернейма."""
        with self._get_connection() as conn:
            conn.execute("""
                         CREATE TABLE IF NOT EXISTS users
                         (
                             user_id
                             INTEGER
                             PRIMARY
                             KEY,
                             username
                             TEXT,
                             is_active
                             INTEGER
                             DEFAULT
                             1,
                             referred_by
                             INTEGER,
                             bonus_limit
                             INTEGER
                             DEFAULT
                             0,
                             is_premium
                             INTEGER
                             DEFAULT
                             0
                         )
                         """)

            try:
                conn.execute("ALTER TABLE users ADD COLUMN premium_expires INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            logger.debug("Таблица users проверена/обновлена")

    def add_user(self, user_id: int, username: Optional[str] = None, referrer_id: Optional[int] = None) -> bool:
        """Регистрирует пользователя (теперь с юзернеймом)."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
                if cursor.fetchone():
                    # Если юзер уже есть, просто обновим его юзернейм (вдруг он его сменил)
                    conn.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
                    return False

                conn.execute(
                    "INSERT INTO users (user_id, username, is_active, referred_by) VALUES (?, ?, 1, ?)",
                    (user_id, username, referrer_id)
                )

                if referrer_id and referrer_id != user_id:
                    conn.execute(
                        "UPDATE users SET bonus_limit = bonus_limit + 2 WHERE user_id = ?",
                        (referrer_id,)
                    )
                return True
        except Exception as e:
            logger.error(f"Ошибка при регистрации user_id={user_id}: {e}")
            return False

    def get_all_users(self) -> list[int]:
        """Возвращает ID всех пользователей бота."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT user_id FROM users")
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Ошибка получения всех юзеров: {e}")
            return []

    def get_free_users(self) -> list[int]:
        """Возвращает ID только бесплатных пользователей (у кого нет Premium или он истек)."""
        import time
        current_time = int(time.time())
        try:
            with self._get_connection() as conn:
                # Берем тех, у кого is_premium = 0 ИЛИ время подписки уже вышло
                cursor = conn.execute(
                    "SELECT user_id FROM users WHERE is_premium = 0 OR (premium_expires > 0 AND premium_expires < ?)",
                    (current_time,)
                )
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Ошибка получения бесплатных юзеров: {e}")
            return []

    def is_user_premium(self, user_id: int) -> bool:
        """Проверяет, есть ли премиум, и автоматически забирает его, если время вышло."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT is_premium, premium_expires FROM users WHERE user_id = ?", (user_id,))
                row = cursor.fetchone()

                if not row:
                    return False

                is_premium, expires = row

                if is_premium == 1:
                    # Если время еще не вышло (или expires == 0 для вечного према админам)
                    if expires == 0 or expires > int(time.time()):
                        return True
                    else:
                        # ВРЕМЯ ВЫШЛО! Снимаем премиум
                        conn.execute("UPDATE users SET is_premium = 0, premium_expires = 0 WHERE user_id = ?",
                                     (user_id,))
                        return False

                return False
        except Exception as e:
            return False

    def set_premium(self, user_id: int, days: int = 30):
        """Выдает премиум на указанное количество дней."""
        # Вычисляем время окончания: текущее время + 30 дней (в секундах)
        expires = int(time.time()) + (days * 24 * 60 * 60)
        try:
            with self._get_connection() as conn:
                conn.execute("UPDATE users SET is_premium = 1, premium_expires = ? WHERE user_id = ?", (expires, user_id))
        except Exception as e:
            logger.error(f"Ошибка выдачи премиума {user_id}: {e}")

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