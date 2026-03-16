import logging
from fastapi import FastAPI, Request
from src.config import user_registry, bot

logger = logging.getLogger("webhook_server")
app = FastAPI()


@app.post("/tribute")
async def tribute_webhook(request: Request):
    try:
        data = await request.json()
        logger.info(f"[Tribute] Получен вебхук: {data}")
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}

    # Достаем название события из поля "name"
    event_name = data.get("name")

    # Все нужные нам данные лежат внутри объекта "payload"
    payload = data.get("payload", {})
    user_id = payload.get("telegram_user_id")

    if not user_id:
        return {"status": "ok", "message": "No telegram_user_id found"}

    user_id = int(user_id)

    # 1. Если это новая ПОДПИСКА или её успешное ПРОДЛЕНИЕ
    if event_name in ["new_subscription", "renewed_subscription"]:
        # Выдаем премиум на 31 день (с запасом в 1 день, чтобы бот не отрубил права раньше, чем Tribute спишет деньги за следующий месяц)
        user_registry.set_premium(user_id, days=31)

        # Разный текст для новой покупки и для автопродления
        if event_name == "new_subscription":
            text = "🎉 <b>Подписка успешно оформлена!</b>\n\n⭐️ Вам выдан Premium! Теперь вам доступно скачивание видео в максимальном качестве."
        else:
            text = "🔄 <b>Ваша Premium-подписка успешно продлена!</b>\nСпасибо, что остаетесь с нами."

        try:
            await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Не удалось отправить сообщение {user_id}: {e}")

    # 2. Если подписка ОТМЕНЕНА (пользователь сам отменил или кончились деньги)
    elif event_name == "cancelled_subscription":
        # Сразу обнуляем премиум
        user_registry.set_premium(user_id, days=0)

        try:
            await bot.send_message(
                chat_id=user_id,
                text="⚠️ <b>Действие Premium-подписки завершено.</b>\nДоступ к максимальному качеству. Вы можете возобновить подписку в любой момент!",
                parse_mode="HTML"
            )
        except Exception as e:
            pass

    return {"status": "ok"}
