import logging
import hmac
import hashlib
import json
from fastapi import FastAPI, Request, HTTPException

from settings import AppSettings
from src.config import user_registry, bot, settings

logger = logging.getLogger("webhook_server")
app = FastAPI()

TRIBUTE_SECRET = settings.tribute.api_secret

def verify_tribute_signature(body: bytes, signature: str, secret: str) -> bool:
    """Функция для проверки криптографической подписи от Tribute"""
    if not signature:
        return False

    expected_signature = hmac.new(
        key=secret.encode('utf-8'),
        msg=body,
        digestmod=hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected_signature, signature)


@app.post("/tribute")
async def tribute_webhook(request: Request):
    body_bytes = await request.body()

    signature = request.headers.get("trbt-signature")

    if not verify_tribute_signature(body_bytes, signature, TRIBUTE_SECRET):
        logger.warning(f"Неверная подпись вебхука от Tribute. IP: {request.client.host}")
        raise HTTPException(status_code=403, detail="Invalid signature")

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

    if event_name == "new_digital_product":

        days_to_add = 30

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

    elif event_name == "digital_product_refunded":
        user_registry.set_premium(user_id, days=0)

        try:
            await bot.send_message(
                chat_id=user_id,
                text="⚠️ <b>Действие Premium-подписки отменено (возврат средств).</b>\nДля доступа к максимальному качеству вы можете приобрести товар заново.",
                parse_mode="HTML"
            )
        except Exception:
            pass

    return {"status": "ok"}