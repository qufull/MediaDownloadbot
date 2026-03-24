import logging
import hmac
import hashlib
import json
from fastapi import FastAPI, Request, HTTPException
from src.config import user_registry, bot

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