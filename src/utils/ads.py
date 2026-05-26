import logging
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from src.config import bot  # Импортируем глобальный объект бота

logger = logging.getLogger(__name__)

async def send_vpn_ad(chat_id: int) -> None:
    """Универсальная функция для отправки рекламы VPN."""
    vpn_url = "https://t.me/vpnskynetai_bot?start=318875085"  # Твоя ссылка

    text = (
        f"💙 Подключись к <a href='https://t.me/vpnskynetai_bot?start=refeZYFehrQ'>Skynet VPN</a> и используй Загрузчик видео Premium функции бесплатно.\n\n"
        f"🎁 Бонус +100₽ при первом пополнении от 100₽\n"
        "<blockquote>"
        "🏳️ Обход глушилок ❞\n"
        "🌍 Разные локации\n"
        "📱 Несколько устройств\n"
        "🌐 Безлимит на трафик\n"
        "</blockquote>"
    )

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👉 Включить ВПН ↗️", url=vpn_url)]
    ])

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=markup,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        logger.debug(f"Реклама ВПН отправлена в чат {chat_id}")
    except Exception as e:
        logger.error(f"[send_vpn_ad] Ошибка отправки рекламы: {e}")