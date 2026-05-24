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

    def get_free_users(self) -> list[int]:
        """Возвращает ID только активных бесплатных пользователей (у кого нет Premium или он истек)."""
        import time
        current_time = int(time.time())
        try:
            with self._get_connection() as conn:
                # Добавлено условие is_active = 1
                cursor = conn.execute(
                    "SELECT user_id FROM users WHERE is_active = 1 AND (is_premium = 0 OR (premium_expires > 0 AND premium_expires < ?))",
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

    def get_premium_users(self) -> List[int]:
        """Возвращает ID активных пользователей с действующей Premium-подпиской."""
        import time
        current_time = int(time.time())
        try:
            with self._get_connection() as conn:
                # Берем активных юзеров, у которых стоит флаг премиума
                # И время подписки либо бесконечно (0), либо еще не вышло
                cursor = conn.execute(
                    """
                    SELECT user_id
                    FROM users
                    WHERE is_active = 1
                      AND is_premium = 1
                      AND (premium_expires = 0 OR premium_expires > ?)
                    """,
                    (current_time,)
                )
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Ошибка получения премиум юзеров: {e}")
            return []

    def set_premium_all(self, days: int = 1):
        """Выдает премиум (добавляет дни) ВСЕМ активным пользователям, учитывая их текущий остаток."""
        import time
        current_time = int(time.time())
        time_to_add = days * 24 * 60 * 60

        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE users
                    SET is_premium      = 1,
                        premium_expires = CASE
                            -- 1. Если у пользователя вечный премиум (0), оставляем 0
                                              WHEN is_premium = 1 AND premium_expires = 0 THEN 0

                            -- 2. Если премиум сейчас активен, плюсуем новые дни к его текущей дате окончания
                                              WHEN is_premium = 1 AND premium_expires > ? THEN premium_expires + ?

                            -- 3. Если премиума нет или он уже истек, выдаем дни начиная с текущей секунды
                                              ELSE ? + ?
                            END
                    WHERE is_active = 1
                    """,
                    (current_time, time_to_add, current_time, time_to_add)
                )
        except Exception as e:
            logger.error(f"Ошибка массовой выдачи премиума: {e}")

    def get_all_users_for_sheet(self) -> list:
        """Получает список ВСЕХ пользователей для выгрузки (без статуса)."""
        from datetime import datetime
        import time
        current_time = int(time.time())

        try:
            with self._get_connection() as conn:
                # Достаем всех юзеров (is_active нам больше не нужен для вывода)
                cursor = conn.execute(
                    """
                    SELECT user_id, username, is_premium, premium_expires
                    FROM users
                    """
                )

                sheet_data = []
                for row in cursor.fetchall():
                    user_id, username, is_premium, expires = row

                    username_str = f"@{username}" if username else "Без юзернейма"

                    # Проверяем, действует ли премиум прямо сейчас
                    is_really_premium = False
                    if is_premium == 1 and (expires == 0 or expires > current_time):
                        is_really_premium = True

                    # Формируем данные в зависимости от наличия премиума
                    if is_really_premium:
                        if expires == 0:
                            sub_time = "2099-12-31"  # Бесконечный премиум
                        else:
                            sub_time = datetime.fromtimestamp(expires).strftime('%Y-%m-%d')
                    else:
                        sub_time = "Нет" # У пользователя нет премиума

                    # Формат колонок: [ID, Username, Expiry Date] (Теперь их 3)
                    sheet_data.append([user_id, username_str, sub_time])

                return sheet_data
        except Exception as e:
            logger.error(f"Ошибка выгрузки для таблицы: {e}")
            return []

    def update_premium_from_sheet(self, user_id: int, expiry_date_str: str):
        """Обновляет статус премиума: если дата валидна — ставит её, если 'Нет' — снимает."""
        from datetime import datetime

        try:
            # Очищаем строку от лишних пробелов
            expiry_date_str = str(expiry_date_str).strip()

            # Если в таблице написано "Нет" или пусто, выключаем премиум
            if expiry_date_str.lower() in ["нет", "none", "", "-"]:
                with self._get_connection() as conn:
                    conn.execute(
                        "UPDATE users SET is_premium = 0, premium_expires = 0 WHERE user_id = ?",
                        (user_id,)
                    )
                return "премиум снят"

            # Иначе пытаемся превратить строку в дату (формат YYYY-MM-DD)
            dt = datetime.strptime(f"{expiry_date_str} 23:59:59", "%Y-%m-%d %H:%M:%S")
            new_expires = int(dt.timestamp())

            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE users SET is_premium = 1, premium_expires = ? WHERE user_id = ?",
                    (new_expires, user_id)
                )
            return f"премиум установлен до {expiry_date_str}"

        except Exception as e:
            logger.error(f"Ошибка обновления из таблицы для {user_id}: {e}")
            raise e

    def get_user_id_by_username(self, username: str):
        """Возвращает ID пользователя по его юзернейму (с @ или без)."""
        clean_username = username.replace('@', '').strip()
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT user_id FROM users WHERE username = ?", (clean_username,))
                row = cursor.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error(f"Ошибка поиска по юзернейму: {e}")
            return None

    def get_premium_status(self, user_id: int):
        """Возвращает статус премиума и дату окончания: (is_premium: bool, expires: int)"""
        import time
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT is_premium, premium_expires FROM users WHERE user_id = ?", (user_id,))
                row = cursor.fetchone()

                if row:
                    is_premium, expires = row
                    if is_premium == 1:
                        # Если время еще не вышло или прем бесконечный (0)
                        if expires == 0 or expires > int(time.time()):
                            return True, expires
                        else:
                            # Время вышло - снимаем премиум
                            conn.execute("UPDATE users SET is_premium = 0, premium_expires = 0 WHERE user_id = ?",
                                         (user_id,))
                            return False, 0
                return False, 0
        except Exception as e:
            logger.error(f"Ошибка получения статуса премиума для {user_id}: {e}")
            return False, 0