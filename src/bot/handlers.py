import asyncio
from urllib.parse import urlparse

from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InputMediaPhoto
from aiogram.filters import CommandObject, CommandStart, Command
from aiogram.methods import SendMessage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from src.utils.massbots_balance import get_massbots_balance
from .filters import URLFilter
from .common import ServiceType
from .keyboards import get_admin_keyboard
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

@router.message(CommandStart())
async def handle_start(message: Message, command: CommandObject) -> None:
    user_id = message.from_user.id
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
    is_new = user_registry.add_user(user_id, referrer_id)

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

    await message.answer_media_group(media)


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


@router.message(BroadcastStates.waiting_for_message)
async def handle_broadcast_message(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in settings.telegram.admin_ids:
        return

    await state.clear()

    users = user_registry.get_all_users()

    if not users:
        await message.answer("❌ Нет пользователей", reply_markup=get_admin_keyboard())
        return

    sent_count = 0  # Счетчик успешно отправленных сообщений

    for user_id in users:
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            sent_count += 1  # Увеличиваем счетчик успешных отправок
        except:
            user_registry.deactivate_user(user_id)

        await asyncio.sleep(0.05)

    # Формируем сообщение с результатами рассылки
    result_text = f"✅ Рассылка завершена!\n"
    result_text += f"📤 Отправлено: {sent_count}\n"

    await message.answer(result_text, reply_markup=get_admin_keyboard())


@router.message(Command("products"))
async def handle_products(message: Message) -> SendMessage:
    """Обработчик команды /products."""
    products_text = MessageTemplates.PRODUCTS
    await message.answer(text=products_text)


@router.message(Command("help"))
async def handle_help(message: Message) -> SendMessage:
    """Показать расширенную справку."""
    supported_services = []
    for service_type, domains in DomainMatcher.DOMAIN_PATTERNS.items():
        domain_examples = ", ".join(domains[:3])
        supported_services.append(
            f"• <b>{service_type.value.upper()}</b> - {domain_examples}..."
        )

    tail_supported_services = chr(10).join(supported_services)
    help_text = MessageTemplates.HELP.format(tail_supported_services=tail_supported_services)
    await message.answer(text=help_text)


@router.message(Command("donate"))
async def handle_donate(message: Message) -> SendMessage:
    """Обработчик команды /donate — поддержка проекта."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

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

        # === Проверка дневного лимита YouTube (админы — без ограничений) ===
        if service_type == ServiceType.YOUTUBE and message.from_user.id not in settings.telegram.admin_ids:
            if not media_rate_limiter.can_download(message.chat.id):
                limit_msg = await message.answer(
                    text=MessageTemplates.YOUTUBE_DAILY_LIMIT_REACHED.format(
                        used=media_rate_limiter.get_used(message.chat.id),
                        limit=media_rate_limiter.DAILY_LIMIT,
                    )
                )
                asyncio.create_task(_auto_delete(message.bot, message.chat.id, limit_msg.message_id))
                return
            media_rate_limiter.increment(message.chat.id)

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

@router.message()
async def handle_unknown_message(message: Message) -> None:
    """Обработчик неизвестных сообщений."""
    unknown_text = MessageTemplates.UNKNOWN
    error_msg = await message.answer(text=unknown_text)
    asyncio.create_task(_auto_delete(message.bot, message.chat.id, error_msg.message_id))