import datetime
import logging
import hmac
import hashlib
import json
from fastapi import FastAPI, Request, HTTPException,Header
from src.config import user_registry, bot, settings
from pydantic import BaseModel

logger = logging.getLogger("webhook_server")
app = FastAPI()

# 🔐 ТВОЙ СЕКРЕТНЫЙ КЛЮЧ TRIBUTE (найди его в настройках приложения в Tribute)
# В рабочей версии бота лучше вынести его в файл .env (например, settings.TRIBUTE_SECRET)
TRIBUTE_SECRET = "f28d5343-9aa6-4251-a2d1-c0c9fd35"


def verify_tribute_signature(body: bytes, signature: str, secret: str) -> bool:
    """Функция для проверки криптографической подписи от Tribute"""
    if not signature:
        return False

    # Создаем хэш на основе тела запроса и нашего секретного ключа
    expected_signature = hmac.new(
        key=secret.encode('utf-8'),
        msg=body,
        digestmod=hashlib.sha256
    ).hexdigest()

    # Сравниваем подписи безопасно (защита от timing attacks)
    return hmac.compare_digest(expected_signature, signature)


@app.post("/tribute")
async def tribute_webhook(request: Request):
    # 1. Читаем сырое тело запроса (в байтах), это ВАЖНО для проверки подписи
    body_bytes = await request.body()

    # 2. Получаем подпись из заголовков, которую прислал Tribute
    signature = request.headers.get("trbt-signature")

    # 3. 🛡 ЗАЩИТА: Проверяем подлинность запроса
    if not verify_tribute_signature(body_bytes, signature, TRIBUTE_SECRET):
        logger.warning(f"⚠️ ВЗЛОМ! Неверная подпись вебхука от Tribute. IP: {request.client.host}")
        # Сбрасываем запрос с ошибкой 403 (Доступ запрещен)
        raise HTTPException(status_code=403, detail="Invalid signature")

    # 4. Если проверка пройдена, распаковываем JSON
    try:
        data = json.loads(body_bytes)
        event_name = data.get("name", "Неизвестное событие")
        logger.info(f"[Tribute] Получен и проверен вебхук: {event_name}")
    except json.JSONDecodeError:
        return {"status": "error", "message": "Invalid JSON"}

    # Достаем нужные данные
    event_name = data.get("name")
    payload = data.get("payload", {})
    user_id = payload.get("telegram_user_id")

    if not user_id:
        return {"status": "ok", "message": "No telegram_user_id found"}

    user_id = int(user_id)

    # === ЛОГИКА ДЛЯ ЦИФРОВЫХ ТОВАРОВ ===
    if event_name == "new_digital_product":
        product_id = payload.get("product_id")

        # Задаем количество дней Премиума (можно настроить в зависимости от product_id)
        days_to_add = 30

        # Выдаем премиум в базе
        user_registry.set_premium(user_id, days=days_to_add)

        text = (
            "🎉 <b>Оплата прошла успешно!</b>\n\n"
            f"⭐️ Вам выдан Premium на {days_to_add} дней!\n"
            "Теперь вам доступно скачивание видео в максимальном качестве без очередей и рекламы."
        )

        try:
            await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Не удалось отправить сообщение {user_id}: {e}")

    # === ЕСЛИ ОФОРМЛЕН ВОЗВРАТ СРЕДСТВ ===
    elif event_name == "digital_product_refunded":
        # Обнуляем премиум
        user_registry.set_premium(user_id, days=0)

        try:
            await bot.send_message(
                chat_id=user_id,
                text="⚠️ <b>Действие Premium-подписки отменено (возврат средств).</b>\nДля доступа к максимальному качеству вы можете приобрести товар заново.",
                parse_mode="HTML"
            )
        except Exception:
            pass

    # Возвращаем 200 OK, чтобы Tribute понял, что мы приняли запрос
    return {"status": "ok"}


API_KEY = "rAdi8YYvr54ghTjv97TTZxQ1BSwpELkjfgj9Ft07TDC0BJIY4l73L8n0oanRIHzMX7p5aP4NHVlzkQOoabOmduek3c2NMQT10zpAPgINSAI9zf5UaNHrHSZ5Iuxqgqhr"


# Схема для получения данных об обновлении
class ClientUpdate(BaseModel):
    user_id: int
    name: str
    sub_time: str


def verify_api_key(x_api_key: str):
    """Проверка ключа доступа."""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Неверный API ключ")


@app.get("/media/clients")
async def get_clients(x_api_key: str = Header(None)):
    """Вебхук для выгрузки данных в таблицу."""
    verify_api_key(x_api_key)

    # Получаем данные ВСЕХ пользователей из БД в виде готовых строк
    data = user_registry.get_all_users_for_sheet()

    # Отдаем JSON, который Google Script сразу сможет разобрать в массив
    return data

@app.post("/media/update_client")
async def update_client(payload: ClientUpdate, x_api_key: str = Header(None)):
    """Вебхук для приема обновлений из таблицы (для всех типов юзеров)."""
    verify_api_key(x_api_key)

    try:
        # 1. Обновляем данные в базе
        user_registry.update_premium_from_sheet(
            user_id=payload.user_id,
            expiry_date_str=payload.sub_time
        )

        # 2. Форматируем дату для красивого сообщения
        sub_time_str = str(payload.sub_time).strip()
        if sub_time_str.lower() in ["нет", "none", "", "-"]:
            formatted_date = "Нет"
        else:
            try:
                # Переводим из YYYY-MM-DD в DD.MM.YYYY
                from datetime import datetime
                dt = datetime.strptime(sub_time_str, "%Y-%m-%d")
                formatted_date = dt.strftime("%d.%m.%Y")
            except ValueError:
                # На случай, если пришел другой формат
                formatted_date = sub_time_str

        # 3. Формируем нужный тебе текст
        admin_text = (
            f"✅ Данные изменены для пользователя <b>{payload.name}</b>\n"
            f"Дата: <b>{formatted_date}</b>"
        )

        # 4. Рассылаем админам
        for admin_id in settings.telegram.admin_ids:
            try:
                await bot.send_message(chat_id=admin_id, text=admin_text, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")

        return {"status": "success", "message": "Данные успешно обновлены"}

    except Exception as e:
        logger.error(f"Ошибка API при обновлении: {e}")
        raise HTTPException(status_code=400, detail="Ошибка формата данных")