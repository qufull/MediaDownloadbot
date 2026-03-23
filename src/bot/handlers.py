import asyncio
from typing import List, Dict
from urllib.parse import urlparse

from aiogram import Router, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InputMediaPhoto,InputMediaVideo,CallbackQuery,InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandObject, CommandStart, Command
from aiogram.methods import SendMessage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from src.utils.massbots_balance import get_massbots_balance
from src.utils.utils import get_ref_stats_text
from .filters import URLFilter
from .common import ServiceType
from .keyboards import get_admin_keyboard, get_tribute_payment_keyboard, get_broadcast_keyboard
from .callback_handlers import TimecodeStates, _dispatch_download, _get_task_func_for_service
from .patterns import DomainMatcher
from .texts import MessageTemplates

from src.config import bot, settings, user_registry, media_rate_limiter
from src.celery_app.tasks.media_extractor_worker import extract_info

import logging

logger = logging.getLogger(__name__)

ERROR_AUTO_DELETE_SECONDS = 10

router = Router(name=__name__)


async def _auto_delete(bot_instance, chat_id: int, message_id: int, delay: int = ERROR_AUTO_DELETE_SECONDS) -> None:
    """Удаляет сообщение через указанное количество секунд."""
    await asyncio.sleep(delay)
    try:
        await bot_instance.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.warning(f"[auto_delete] Не удалось удалить сообщение {message_id}: {e}")


class BroadcastStates(StatesGroup):
    waiting_for_message = State()


def _parse_timecode_range(text: str):
    """
    Парсит диапазон таймкодов из строки вида:
      '1:30-5:00'  → (90, 300)
      '01:30-05:00' → (90, 300)
      '00:01:30-00:05:00' → (90, 300)
    Возвращает (start_sec, end_sec) или None при ошибке.
    """
    import re

    def _to_sec(s: str) -> int:
        parts = [int(x) for x in s.split(":")]
        if len(parts) == 1:
            return parts[0]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return parts[0] * 3600 + parts[1] * 60 + parts[2]

    text = text.strip().replace(" ", "")
    m = re.match(r'^(\d{1,2}(?::\d{2}){0,2})-(\d{1,2}(?::\d{2}){0,2})$', text)
    if not m:
        return None
    try:
        start = _to_sec(m.group(1))
        end = _to_sec(m.group(2))
    except (ValueError, IndexError):
        return None
    if start >= end or end <= 0:
        return None
    return start, end

@router.message(CommandStart())
async def handle_start(message: Message, command: CommandObject) -> None:
    user_id = message.from_user.id
    username = message.from_user.username
    referrer_id = None

    # --- ЛОГИКА РЕФЕРАЛКИ (работает в фоне) ---
    if command.args and command.args.startswith("ref_"):
        try:
            potential_ref = int(command.args.replace("ref_", ""))
            if potential_ref != user_id:
                referrer_id = potential_ref
        except ValueError:
            pass

    # Регистрируем в базе (вернет True, только если юзер реально новый)
    is_new = user_registry.add_user(user_id=user_id, username=username, referrer_id=referrer_id)

    # Уведомляем пригласившего о бонусе
    if is_new and referrer_id:
        try:
            await bot.send_message(
                chat_id=referrer_id,
                text="🎁 <b>У вас новый реферал!</b>\nВаш ежедневный лимит увеличен на <b>2</b> ",
                parse_mode="HTML"
            )
        except Exception:
            pass

    welcome_text = MessageTemplates.WELCOME

    media = [
        InputMediaPhoto(
            media="AgACAgIAAxkBAAKeqWlrj7_Ndxa3XuVPw1ketR9bhyVMAAI0Dmsb3a9hSxGlKwkrg0FQAQADAgADeQADNgQ",
            caption=welcome_text,
            parse_mode="HTML",
        ),
        InputMediaPhoto(media="AgACAgIAAxkBAAKeK2lrfydNOln5bbmhz4KfS8ylyiFwAAI1DWsb3a9hS2W9ydJUXSGvAQADAgADeQADNgQ"),
        InputMediaPhoto(media="AgACAgIAAxkBAAKeLWlrf1OMccWuIoSSA37hVDKsNgNOAAJEDWsb3a9hS1z8wwe84IO1AQADAgADeQADNgQ"),
        InputMediaPhoto(media="AgACAgIAAxkBAAKeL2lrf2LxLcJ3jvf6Ltpp8OXxBEogAAJFDWsb3a9hS1isoqXax2Y1AQADAgADeQADNgQ"),
    ]

    try:
        await message.answer_media_group(media)
    except Exception:
        # file_id привязан к публичному api.telegram.org и не работает через локальный сервер
        await message.answer(text=welcome_text, parse_mode="HTML")


@router.message(F.text == "📤 Рассылка")
async def handle_broadcast_button(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in settings.telegram.admin_ids:
        return

    await state.set_state(BroadcastStates.waiting_for_message)
    await message.answer(
        "📤 Отправьте сообщение для рассылки",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        )
    )


@router.message(BroadcastStates.waiting_for_message, F.text == "❌ Отмена")
async def handle_broadcast_cancel(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in settings.telegram.admin_ids:
        return

    await state.clear()
    await message.answer("Отменено", reply_markup=get_admin_keyboard())


async def ask_audience_and_save(message: Message, state: FSMContext, messages: List[Message]):
    """Сохраняет сообщение во временный словарь и выдает инлайн-клавиатуру выбора аудитории"""
    # Сохраняем пост по ID админа
    pending_broadcasts[message.from_user.id] = messages

    # Отправляем сообщение с выбором (клавиатура берется из твоего файла keyboards)
    await message.answer(
        "✅ Сообщение загружено! Кому будем отправлять?",
        reply_markup=get_broadcast_keyboard()
    )

media_group_storage: Dict[str, List[Message]] = {}
pending_broadcasts: Dict[int, List[Message]] = {}



@router.message(BroadcastStates.waiting_for_message)
async def handle_broadcast_message(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in settings.telegram.admin_ids:
        return

    # Обработка кнопки "Отмена"
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Рассылка отменена", reply_markup=get_admin_keyboard())
        # Удаляем само сообщение об отмене, если нужно
        try:
            await message.delete()
        except:
            pass
        return

    # --- ЛОГИКА СБОРА И ОЧИСТКИ ---

    if message.media_group_id:
        # Если это часть альбома
        if message.media_group_id not in media_group_storage:
            media_group_storage[message.media_group_id] = [message]

            # Ждем чуть меньше секунды, чтобы все фото долетели
            await asyncio.sleep(0.8)

            # Извлекаем все собранные сообщения
            messages = media_group_storage.pop(message.media_group_id)
            await ask_audience_and_save(message, state, messages)
        else:
            # Добавляем фото в уже существующую группу
            media_group_storage[message.media_group_id].append(message)
    else:
        # Одиночное сообщение (текст, фото, видео или документ)
        await ask_audience_and_save(message, state, [message])


@router.callback_query(F.data.startswith("broadcast:"))
async def handle_broadcast_audience(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user.id not in settings.telegram.admin_ids:
        return

    await callback.answer()
    action = callback.data.split(":")[1]

    # Достаем сохраненные сообщения
    messages = pending_broadcasts.pop(callback.from_user.id, None)

    if action == "cancel":
        await state.clear()
        await callback.message.edit_text("🚫 Рассылка отменена.")
        await callback.message.answer("Возврат в меню", reply_markup=get_admin_keyboard())

        # Удаляем исходники, чтобы не засорять чат админа
        if messages:
            for msg in messages:
                try:
                    await msg.delete()
                except Exception:
                    pass
        return

    if not messages:
        await callback.message.edit_text("❌ Ошибка: сообщение для рассылки потеряно. Попробуйте еще раз.")
        await callback.message.answer("Возврат в меню", reply_markup=get_admin_keyboard())
        await state.clear()
        return

    if action == "all":
        users = user_registry.get_all_users()
    elif action == "free":
        users = user_registry.get_free_users()
    else:
        users = []

    if not users:
        await callback.message.edit_text("❌ Выбранная база пользователей пуста.")
        await callback.message.answer("Возврат в меню", reply_markup=get_admin_keyboard())
        await state.clear()
        return

    # Меняем текст на инлайн-кнопках на статус работы
    await callback.message.edit_text(f"🚀 <b>Рассылка запущена для {len(users)} пользователей...</b>", parse_mode="HTML")

    # ВОЗВРАЩАЕМ КНОПКУ СРАЗУ, чтобы она не висела как "Отмена", пока идет долгая рассылка
    await callback.message.answer("Рассылка работает в фоне. Вы можете продолжать пользоваться ботом.",
                                  reply_markup=get_admin_keyboard())

    asyncio.create_task(start_broadcast_and_cleanup(callback.message, state, messages, users))


async def start_broadcast_and_cleanup(message: Message, state: FSMContext, messages: List[Message], users: List[int]):
    """Запуск рассылки и последующее удаление исходных сообщений"""
    await state.clear()

    is_media_group = len(messages) > 1
    media_list = []

    if is_media_group:
        for msg in messages:
            if msg.photo:
                media_list.append(InputMediaPhoto(media=msg.photo[-1].file_id, caption=msg.caption,
                                                  caption_entities=msg.caption_entities))
            elif msg.video:
                media_list.append(InputMediaVideo(media=msg.video.file_id, caption=msg.caption,
                                                  caption_entities=msg.caption_entities))

    sent_count = 0
    blocked_count = 0

    # ОСНОВНОЙ ЦИКЛ РАССЫЛКИ
    for user_id in users:
        try:
            if is_media_group:
                await bot.send_media_group(chat_id=user_id, media=media_list)
            else:
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=messages[0].chat.id,
                    message_id=messages[0].message_id
                )
            sent_count += 1
        except Exception:
            blocked_count += 1
            user_registry.deactivate_user(user_id)

        # Флуд-контроль
        await asyncio.sleep(0.05 if not is_media_group else 0.1)

    # --- ОЧИСТКА ХВОСТОВ ---

    # 1. Удаляем сообщения, которые прислал админ (исходники рассылки)
    for msg in messages:
        try:
            await msg.delete()
        except Exception as e:
            logger.warning(f"Не удалось удалить исходное сообщение: {e}")

    # 2. Отправляем финальный отчет
    await message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📤 Доставлено: <b>{sent_count}</b>\n",
        reply_markup=get_admin_keyboard(),
        parse_mode="HTML"
    )

@router.message(Command("products"))
async def handle_products(message: Message) -> SendMessage:
    """Обработчик команды /products."""
    products_text = MessageTemplates.PRODUCTS
    await message.answer(text=products_text)


@router.message(Command("help"))
async def handle_help(message: Message) -> SendMessage:
    """Показать расширенную справку."""
    _PLATFORM_LABELS = {
        "youtube":      "🎬 YouTube",
        "rutube":       "📺 RuTube",
        "instagram":    "📸 Instagram",
        "tiktok":       "🎵 TikTok",
        "twitter":      "🐦 X (Twitter)",
        "reddit":       "👽 Reddit",
        "vk":           "🇷🇺 VK",
        "pinterest":    "📌 Pinterest",
        "vimeo":        "🎞️ Vimeo",
        "dailymotion":  "📡 Dailymotion",
        "facebook":     "👥 Facebook",
        "okru":         "🟠 OK.ru",
        "twitch":       "🎮 Twitch",
        "kick":         "🟩 Kick",
        "rumble":       "🦁 Rumble",
        "coub":         "♾️ Coub",
        "soundcloud":   "🎧 SoundCloud",
    }

    supported_services = []
    for service_type, domains in DomainMatcher.DOMAIN_PATTERNS.items():
        key = service_type.value
        label = _PLATFORM_LABELS.get(key, f"🌐 {key.upper()}")
        domain_example = domains[0] if domains else key
        supported_services.append(f"• {label} — <code>{domain_example}</code>")

    tail_supported_services = "\n".join(supported_services)
    help_text = MessageTemplates.HELP.format(tail_supported_services=tail_supported_services)
    await message.answer(text=help_text)


@router.message(Command("support"))
async def support_command(message:Message):
    builder = InlineKeyboardBuilder()

    # Вставь сюда юзернейм твоего аккаунта поддержки (без @)
    support_username = "skynetaivpn_support"

    # Создаем кнопку-ссылку
    builder.button(
        text="👨‍💻 Написать специалисту",
        url=f"https://t.me/{support_username}"
    )

    await message.answer(
        "📝 **Служба поддержки**\n\n"
        "Если у вас возникли вопросы, проблемы с оплатой или вы нашли баг, "
        "пожалуйста, напишите нам. Мы ответим в кратчайшие сроки!",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )

@router.message(Command("donate"))
async def handle_donate(message: Message) -> SendMessage:
    """Обработчик команды /donate — поддержка проекта."""

    donate_text = (
        "Лучшая поддержка от вас — это небольшой регулярный платёж "
        "за сервис, которым вы и так уже пользуетесь ежедневно.\n\n"
        "🛡 <b>Сервис SkyNet VPN.</b>\n"
        "Быстрый. Надёжный. Безопасный."
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Купить подписку", url="https://t.me/skynetaivpn_bot")],
    ])

    await message.answer(text=donate_text, reply_markup=keyboard)


@router.message(URLFilter(check_support=False))
async def handle_url_message(message: Message) -> SendMessage:
    """Обработчик сообщений с URL."""
    url = message.text or message.caption
    if not url:
        return

    try:
        parsed = urlparse(url.strip())
        domain = parsed.netloc.lower()
        service_type = DomainMatcher.get_service_type(domain)

        if service_type == ServiceType.UNSUPPORTED:
            await handle_unsupported_domain(domain=domain, message=message)
            return

        # === Проверка дневных лимитов ===
        if message.from_user.id not in settings.telegram.admin_ids:
            if not media_rate_limiter.can_download(message.chat.id, service=service_type.value):

                # Если это обычный пользователь (не премиум)
                if not user_registry.is_user_premium(message.from_user.id):
                    limit_text = await get_ref_stats_text(message.from_user.id)
                    promo_text = (
                        f"{limit_text}\n\n"
                        "⭐️ <b>Хотите качать без ограничений?</b>\n"
                        "Оформите Premium-подписку и забудьте о лимитах и сжатом качестве!"
                    )
                    await message.answer(
                        text=promo_text,
                        reply_markup=get_tribute_payment_keyboard(settings.tribute.url),
                        parse_mode="HTML"
                    )
                # Если это Premium (исчерпал лимит 30 видео на YouTube)
                else:
                    text = (
                        "⚠️ <b>Дневной лимит YouTube исчерпан.</b>\n"
                        "Для безопасности бота даже на Premium действует лимит: 30 YouTube-видео в день. "
                        "Остальные сервисы (TikTok, Reels и др.) остаются безлимитными. Возвращайтесь завтра!"
                    )
                    await message.answer(text=text, parse_mode="HTML")
                return

        # Если лимиты не превышены (или это админ) — отправляем задачу в парсер
        extract_info.delay(
            url=url,
            chat_id=message.chat.id,
            service=service_type.value,
            message_id=message.message_id,
        )

    except Exception as e:
        error_text = MessageTemplates.ERROR
        error_msg = await message.answer(text=error_text)
        asyncio.create_task(_auto_delete(message.bot, message.chat.id, error_msg.message_id))


async def handle_unsupported_domain(domain: str, message: Message) -> SendMessage:
    """Обработчик неподдерживаемых доменов."""
    supported_services = ", ".join(
        service_type.value for service_type in DomainMatcher.DOMAIN_PATTERNS.keys()
    )

    text = MessageTemplates.UNSUPPORTED_DOMAIN.format(
        domain=domain,
        supported_services=supported_services
    )
    error_msg = await message.answer(text=text)
    asyncio.create_task(_auto_delete(message.bot, message.chat.id, error_msg.message_id))

@router.message(Command("balance"))
async def massbots_balance_cmd(message: Message):
    try:
        balance = get_massbots_balance()

        if balance is None:
            await message.answer("⚠️ Не удалось получить баланс (нет поля balance в ответе API)")
            return

        text = (
            "📊 <b>MassBots баланс</b>\n\n"
            f"Баланс: <b>{balance}</b>\n"
        )

        await message.answer(text, parse_mode="HTML")

    except Exception as e:
        await message.answer(f"❌ Ошибка получения баланса:\n<code>{e}</code>", parse_mode="HTML")


@router.message(Command("ref"))
async def handle_ref_command(message: Message) -> None:
    """Вся информация о рефералах здесь."""
    user_id = message.from_user.id

    # Считаем данные
    max_allowed = media_rate_limiter.get_max_allowed(user_id)
    remaining = media_rate_limiter.get_remaining(user_id)
    bonus = user_registry.get_bonus(user_id)

    # Генерируем ссылку
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"

    text = (
        "🎁 <b>Реферальная программа</b>\n\n"
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

    await message.answer(text, parse_mode="HTML")

@router.message(TimecodeStates.waiting_for_timecodes)
async def handle_timecodes_input(message: Message, state: FSMContext) -> None:
    """Пользователь ввёл таймкоды после нажатия '✂️ Фрагмент'."""
    chat_id = message.chat.id
    data = await state.get_data()
    await state.clear()

    parsed = _parse_timecode_range(message.text or "")
    if not parsed:
        await message.answer(
            "❌ Неверный формат таймкодов.\n\n"
            "Примеры:\n"
            "• <code>1:30-5:00</code>\n"
            "• <code>00:01:30-00:05:00</code>\n\n"
            "Попробуйте ещё раз — нажмите на нужное качество заново.",
            parse_mode="HTML",
        )
        return

    start_sec, end_sec = parsed
    video_key = data.get("video_key", "")
    # Кодируем таймкоды в video_id: "1080p|90-300"
    video_id_with_tc = f"{video_key}|{start_sec}-{end_sec}"

    # Восстанавливаем параметры скачивания из сохранённого контекста
    url = data.get("url", "")
    width = data.get("width", 0)
    height = data.get("height", 0)
    service = data.get("service", "")
    merge_audio = data.get("merge_audio", False)
    message_id = data.get("message_id", message.message_id)

    def _fmt_tc(sec: int) -> str:
        h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    await message.answer(
        f"✂️ Скачиваю фрагмент <b>{_fmt_tc(start_sec)}–{_fmt_tc(end_sec)}</b>...",
        parse_mode="HTML",
    )

    await _dispatch_download(
        chat_id=chat_id,
        message_id=message_id,
        url=url,
        width=data.get("width"),
        height=data.get("height"),
        chosen_id=video_id_with_tc,
        merge_audio=data.get("merge_audio", False),
        is_premium=data.get("is_premium", False),
        is_admin=data.get("is_admin", False),
        service=service,
    )


@router.message()
async def handle_unknown_message(message: Message) -> None:
    """Обработчик неизвестных сообщений."""
    unknown_text = MessageTemplates.UNKNOWN
    error_msg = await message.answer(text=unknown_text)
    asyncio.create_task(_auto_delete(message.bot, message.chat.id, error_msg.message_id))