from src.config import media_rate_limiter, user_registry, bot


async def get_ref_stats_text(user_id: int) -> str:
    """Формирует расширенный текст с информацией о лимитах и рефералке."""
    max_allowed = media_rate_limiter.get_max_allowed(user_id)
    remaining = media_rate_limiter.get_remaining(user_id)
    bonus = user_registry.get_bonus(user_id)

    # Генерируем реферальную ссылку
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"

    text = (
        "🚫 <b>Дневной лимит исчерпан!</b>\n\n"
        "Приглашайте друзей и увеличивайте свой ежедневный лимит!\n"
        "За каждого приглашенного вы получаете <b>+2</b> к лимиту навсегда.\n\n"
        f"📊 <b>Твоя статистика:</b>\n"
        f"└ Базовый лимит: {media_rate_limiter.DAILY_LIMIT}\n"
        f"└ Бонусы за друзей: +{bonus}\n"
        f"└ Итого доступно: <b>{max_allowed}</b> в сутки\n"
        f"└ Осталось сегодня: <b>{remaining}</b>\n\n"
        f"🔗 <b>Твоя ссылка для приглашения:</b>\n"
        f"<code>{ref_link}</code>"
    )
    return text