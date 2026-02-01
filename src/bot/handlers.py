import asyncio
from urllib.parse import urlparse

from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InputMediaPhoto
from aiogram.filters import Command
from aiogram.methods import SendMessage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from src.utils.massbots_balance import get_massbots_balance
from .filters import URLFilter
from .common import ServiceType
from .keyboards import get_admin_keyboard
from .patterns import DomainMatcher
from .texts import MessageTemplates

from src.config import bot, settings, user_registry
from src.celery_app.tasks.media_extractor_worker import extract_info

router = Router(name=__name__)


class BroadcastStates(StatesGroup):
    waiting_for_message = State()

@router.message(Command("start"))
async def handle_start(message: Message) -> None:
    welcome_text = MessageTemplates.WELCOME

    media = [
        InputMediaPhoto(
            media="AgACAgIAAxkBAAKeqWlrj7_Ndxa3XuVPw1ketR9bhyVMAAI0Dmsb3a9hSxGlKwkrg0FQAQADAgADeQADNgQ",
            caption=welcome_text,
            parse_mode="HTML",
        ),
        InputMediaPhoto(
            media="AgACAgIAAxkBAAKeK2lrfydNOln5bbmhz4KfS8ylyiFwAAI1DWsb3a9hS2W9ydJUXSGvAQADAgADeQADNgQ",
        ),
        InputMediaPhoto(
            media="AgACAgIAAxkBAAKeLWlrf1OMccWuIoSSA37hVDKsNgNOAAJEDWsb3a9hS1z8wwe84IO1AQADAgADeQADNgQ",
        ),
        InputMediaPhoto(
            media="AgACAgIAAxkBAAKeL2lrf2LxLcJ3jvf6Ltpp8OXxBEogAAJFDWsb3a9hS1isoqXax2Y1AQADAgADeQADNgQ",
        ),
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

    for user_id in users:
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
        except:
            user_registry.deactivate_user(user_id)

        await asyncio.sleep(0.05)

    await message.answer("Рассылка отправлена!", reply_markup=get_admin_keyboard())


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

        extract_info.delay(
            url=url,
            chat_id=message.chat.id,
            service=service_type.value,
            message_id=message.message_id,
        )

    except Exception as e:
        error_text = MessageTemplates.ERROR
        await message.answer(text=error_text)


async def handle_unsupported_domain(domain: str, message: Message) -> SendMessage:
    """Обработчик неподдерживаемых доменов."""
    supported_services = ", ".join(
        service_type.value for service_type in DomainMatcher.DOMAIN_PATTERNS.keys()
    )

    text = MessageTemplates.UNSUPPORTED_DOMAIN.format(
        domain=domain,
        supported_services=supported_services
    )
    await message.answer(text=text)

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

@router.message()
async def handle_unknown_message(message: Message) -> None:
    """Обработчик неизвестных сообщений."""
    unknown_text = MessageTemplates.UNKNOWN
    await message.answer(text=unknown_text)


