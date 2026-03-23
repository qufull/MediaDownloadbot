import asyncio
from typing import Dict, List

from aiogram import Router, F
from aiogram.enums import ChatAction, ChatMemberStatus
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.methods import AnswerCallbackQuery
from aiogram.types import CallbackQuery, InputMediaPhoto
from aiogram.utils.chat_action import ChatActionSender
from aiogram.types import FSInputFile
import random

from src.utils.ads import send_vpn_ad
from src.utils.utils import get_ref_stats_text
from .texts import MessageTemplates
from .common import exetract_image_name
from .keyboards import (
    get_retry_subscription_keyboard,
    get_tribute_payment_keyboard,
    get_timecode_choice_keyboard,
    get_timecode_cancel_keyboard,
)

from src.config import bot, settings, user_registry
from src.core import ImageDictAnnotation

from src.config import user_session_storage, user_activity_queue, media_rate_limiter, settings

>>>>>>> main

from src.utils.telegram_anim import send_error

from src.celery_app.tasks.audio_download_worker import download_audio
from src.celery_app.tasks.video_download_worker import (
    download_reddit_video,
    download_rutube_video,
    download_tiktok_video,
    download_youtube_video,
    download_twitter_video,
    download_instagram_video,
    download_vk_video,
    download_pinterest_video,
    download_vimeo_video,
    download_dailymotion_video,
    download_facebook_video,
    download_okru_video,
    download_twitch_video,
    download_kick_video,
    download_rumble_video,
    download_coub_video,
    download_soundcloud_video,
    delivery_store,
    deliver_to_telegram,
    _deliver_via_drive,
    _fmt_bytes,
)


# ---------------------------------------------------------------------------
# FSM: ожидание таймкодов после нажатия "✂️ Фрагмент"
# ---------------------------------------------------------------------------
class TimecodeStates(StatesGroup):
    waiting_for_timecodes = State()


# ---------------------------------------------------------------------------
# Хелпер: карта сервис → Celery-задача
# ---------------------------------------------------------------------------
_TASK_MAP = {
    "twitter": download_twitter_video,
    "youtube": download_youtube_video,
    "reddit": download_reddit_video,
    "rutube": download_rutube_video,
    "tiktok": download_tiktok_video,
    "instagram": download_instagram_video,
    "vk": download_vk_video,
    "pinterest": download_pinterest_video,
    "vimeo": download_vimeo_video,
    "dailymotion": download_dailymotion_video,
    "facebook": download_facebook_video,
    "okru": download_okru_video,
    "twitch": download_twitch_video,
    "kick": download_kick_video,
    "rumble": download_rumble_video,
    "coub": download_coub_video,
    "soundcloud": download_soundcloud_video,
}


def _get_task_func_for_service(service: str):
    return _TASK_MAP.get(service)


async def _dispatch_download(
    chat_id: int,
    message_id: int,
    url: str,
    width,
    height,
    chosen_id: str,
    merge_audio: bool,
    is_premium: bool,
    is_admin: bool,
    service: str,
) -> None:
    """Запускает Celery-задачу: сразу (premium/admin) или с задержкой (free)."""
    task = _get_task_func_for_service(service)
    if task is None:
        await send_error(chat_id=chat_id, text=f"Сервис {service} пока не поддерживается для скачивания.")
        return

    if is_premium or is_admin:
        task.delay(
            url=url, width=width, height=height, chat_id=chat_id,
            message_id=message_id, video_id=chosen_id, merge_audio=merge_audio,
        )
    else:
        delay = random.randint(60, 180)
        asyncio.create_task(
            process_queue_and_download(
                chat_id, message_id, url, width, height, chosen_id, merge_audio, delay, task,
            )
        )

router = Router(name=__name__)


def _safe_media_id(item: dict) -> str | None:
    """Берём name, если есть, иначе id. Это твой контракт по всему проекту."""
    return (item.get("name") or item.get("id") or "").strip() or None

async def process_queue_and_download(chat_id, message_id, url, width, height, chosen_id, merge_audio, delay, task_func):
    """Фоновая задача: держит юзера в очереди, показывает таймер, затем отдает в скачивание"""
    # 1. Отправляем видео ТАЙМЕРА
    try:
        timer_msg = await bot.send_video(
            chat_id=chat_id,
            video=FSInputFile("src/assets/timer.mp4"),
            caption=(
                "⏳ <b>Ваше видео поставлено в очередь!</b>\n"
                f"Ожидаемое время до начала выгрузки: ~3 мин.\n\n"
                "⭐️ <i>Premium-пользователям доступна выгрузка моментально и без очереди!</i>"
            ),
            parse_mode="HTML",
            reply_to_message_id=message_id,
            reply_markup=get_tribute_payment_keyboard(settings.tribute.url)  # Сразу даем кнопку купить!
        )
    except Exception:
        timer_msg = None

    # 2. Ждем нужное время. Aiogram может держать так хоть 10 000 юзеров, не нагружая сервер!
    await asyncio.sleep(delay)

    # 3. Время вышло -> Удаляем сообщение с таймером
    if timer_msg:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=timer_msg.message_id)
        except Exception:
            pass

    # 4. Отправляем задачу в Celery (начнется загрузка, появится голубь)
    task_func.delay(
        url=url, width=width, height=height, chat_id=chat_id,
        message_id=message_id, video_id=chosen_id, merge_audio=merge_audio
    )

@router.callback_query(F.data.startswith("video"))
async def handle_video(callback: CallbackQuery, state: FSMContext) -> AnswerCallbackQuery:
    chat_id = callback.message.chat.id
    await callback.answer()

    session = user_session_storage.get_session(chat_id=chat_id)
    if session is None:
        await send_error(chat_id=chat_id, text=MessageTemplates.CALLBACK_SESSION_EXPIRED)
        return

    service = session["service"]

    # 2. ПРОВЕРЯЕМ ЛИМИТЫ (обязательно передаем service)
    if callback.from_user.id not in settings.telegram.admin_ids:
        if not media_rate_limiter.can_download(chat_id, service=service):

            # Если это обычный пользователь (не премиум)
            if not user_registry.is_user_premium(callback.from_user.id):
                limit_text = await get_ref_stats_text(callback.from_user.id)
                promo_text = (
                    f"{limit_text}\n\n"
                    "⭐️ <b>Хотите качать без ограничений?</b>\n"
                    "Оформите Premium-подписку и забудьте о лимитах и низком качестве!"
                )
                await bot.send_message(
                    chat_id=chat_id,
                    text=promo_text,
                    reply_markup=get_tribute_payment_keyboard(settings.tribute.url),
                    parse_mode="HTML"
                )
            # Если это Premium (значит он исчерпал лимит 30 видео на YouTube)
            else:
                text = (
                    "⚠️ <b>Дневной лимит YouTube исчерпан.</b>\n"
                    "Для безопасности бота даже на Premium действует лимит: 30 YouTube-видео в день. "
                    "Остальные сервисы (TikTok, Reels и др.) остаются безлимитными. Возвращайтесь завтра!"
                )
                await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            return

    if user_activity_queue.get_download(chat_id=chat_id):
        await send_error(chat_id=chat_id, text=MessageTemplates.CALLBACK_PROCESSING_MESSAGE)
        return

    try:
        _, video_id = callback.data.split(":", 1)
        video = next((v for v in session["media_data"]["videos"] if str(_safe_media_id(v)) == str(video_id)), None)
        if not video:
            await send_error(chat_id=chat_id, text="Не нашла выбранный формат видео. Отправь ссылку заново.")
            return

        url = session["url"]
        width = video.get("width")
        height = video.get("height")
        service = session["service"]

        w = int(width) if width else 0
        h = int(height) if height else 0

        if w > 0 and h > 0:
            quality = min(w, h)
        elif h > 0:
            mapping = {1920: 1080, 1280: 720, 854: 480, 640: 360, 2560: 1440, 3840: 2160}
            quality = mapping.get(h, h)
        else:
            quality = w

            # === НОВОЕ: Проверяем сервис ===
        premium_services = ["youtube", "rutube", "vk"]

        # Блокируем ТОЛЬКО если это ютуб/рутуб/вк И качество >= 720
        if service in premium_services and quality >= 720:
            if not user_registry.is_user_premium(callback.from_user.id) and callback.from_user.id not in settings.telegram.admin_ids:
                promo_text = (
                    f"⭐️ Высокое качество (720p и выше) доступно только по Premium-подписке!\n\n"
                    "🔥 PREMIUM\n\n"
                    "✅ 720p / 1080p\n"
                    "✅ Без рекламы\n"
                    "✅ Приоритет загрузки\n\n"
                    "_________________\n\n"
                    "🆓 FREE\n\n"
                    "✅ ≤ 640p\n"
                    "❌ 720p / 1080p\n"
                    "❌ Без рекламы\n"
                    "❌ Приоритет загрузки\n"

                )

                await callback.message.answer(
                    promo_text,
                    reply_markup=get_tribute_payment_keyboard(settings.tribute.url),
                    parse_mode="HTML"
                )
                try:
                    user_activity_queue.delete_download(chat_id=chat_id)
                except Exception:
                    pass
                return

        message_id = callback.message.message_id

        chosen_id = _safe_media_id(video)
        if not chosen_id:
            await send_error(chat_id=chat_id, text="У видео нет идентификатора формата (name/id).")
            return

        if _get_task_func_for_service(service) is None:
            await send_error(chat_id=chat_id, text=f"Сервис {service} пока не поддерживается для скачивания.")
            return

        merge_audio = True if not video.get("has_audio", False) else False
        is_premium = user_registry.is_user_premium(callback.from_user.id)
        is_admin = callback.from_user.id in settings.telegram.admin_ids

        user_activity_queue.create_download(url=url, chat_id=chat_id, service=service)

        # Сохраняем контекст в FSM и предлагаем выбор: целиком или фрагмент
        await state.update_data(
            url=url,
            width=width,
            height=height,
            service=service,
            video_key=chosen_id,
            merge_audio=merge_audio,
            message_id=message_id,
            is_premium=is_premium,
            is_admin=is_admin,
        )

        await bot.send_message(
            chat_id=chat_id,
            text=(
                "🎬 <b>Скачать целиком или выбрать фрагмент?</b>\n\n"
                "• <b>Полностью</b> — скачать всё видео\n"
                "• <b>Фрагмент</b> — выбрать отрезок по таймкодам"
            ),
            reply_markup=get_timecode_choice_keyboard(chosen_id),
            parse_mode="HTML",
        )

    except Exception as e:
        await send_error(chat_id=chat_id, text="Ошибка при обработке видео. Попробуй ещё раз.")
        user_activity_queue.delete_download(chat_id=chat_id)
        raise


@router.callback_query(F.data.startswith("full_dl"))
async def handle_full_download(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь выбрал «🎬 Полностью» — запускаем скачивание без таймкодов."""
    chat_id = callback.message.chat.id
    await callback.answer()

    data = await state.get_data()
    await state.clear()

    if not data:
        await send_error(chat_id=chat_id, text="Сессия истекла. Отправь ссылку заново.")
        return

    try:
        await callback.message.delete()
    except Exception:
        pass

    await _dispatch_download(
        chat_id=chat_id,
        message_id=data.get("message_id", callback.message.message_id),
        url=data["url"],
        width=data.get("width"),
        height=data.get("height"),
        chosen_id=data["video_key"],
        merge_audio=data.get("merge_audio", False),
        is_premium=data.get("is_premium", False),
        is_admin=data.get("is_admin", False),
        service=data["service"],
    )


@router.callback_query(F.data.startswith("frag_dl"))
async def handle_fragment_choice(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь выбрал «✂️ Фрагмент» — просим ввести таймкоды."""
    chat_id = callback.message.chat.id
    await callback.answer()

    data = await state.get_data()
    if not data:
        await send_error(chat_id=chat_id, text="Сессия истекла. Отправь ссылку заново.")
        return

    await state.set_state(TimecodeStates.waiting_for_timecodes)

    tc_text = (
        "⏱ <b>Введите таймкоды фрагмента</b>\n\n"
        "Формат: <code>начало-конец</code>\n\n"
        "Примеры:\n"
        "• <code>1:30-5:00</code>\n"
        "• <code>00:01:30-00:05:00</code>\n"
        "• <code>30-90</code> (в секундах)"
    )
    try:
        await callback.message.edit_text(
            tc_text,
            parse_mode="HTML",
            reply_markup=get_timecode_cancel_keyboard(),
        )
    except Exception:
        await bot.send_message(
            chat_id=chat_id,
            text=tc_text,
            parse_mode="HTML",
            reply_markup=get_timecode_cancel_keyboard(),
        )


@router.callback_query(F.data == "tc_cancel")
async def handle_timecode_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь нажал «❌ Отмена» во время ввода таймкодов."""
    chat_id = callback.message.chat.id
    await callback.answer("Отменено")
    await state.clear()
    try:
        user_activity_queue.delete_download(chat_id=chat_id)
    except Exception:
        pass
    try:
        await callback.message.edit_text("❌ Скачивание отменено.")
    except Exception:
        await bot.send_message(chat_id=chat_id, text="❌ Скачивание отменено.")


@router.callback_query(F.data.startswith("image"))
async def handle_image(callback: CallbackQuery) -> AnswerCallbackQuery:
    chat_id = callback.message.chat.id
    await callback.answer()

    session = user_session_storage.get_session(chat_id=chat_id)
    if session is None:
        await send_error(chat_id=chat_id, text=MessageTemplates.CALLBACK_SESSION_EXPIRED)
        return

    try:
        images_by_name: Dict[int, List[ImageDictAnnotation]] = {}
        for image in session["media_data"]["images"]:
            filename = exetract_image_name(image_url=image["url"])
            if filename not in images_by_name:
                images_by_name[filename] = []
            images_by_name[filename].append(image)

        best_images = [images_by_name[name][-1] for name in images_by_name.keys()]

        if not best_images:
            await send_error(chat_id=chat_id, text="Изображений не найдено.")
            return

        media = []
        for i, image in enumerate(best_images):
            caption = ""
            if i == 0:
                caption = MessageTemplates.CALLBACK_IMAGE_CAPTION.format(
                    url=session["url"],
                    quantity=len(best_images),
                    service=session["service"],
                    botname=settings.telegram.name,
                    author_name=session["media_data"].get("author_name", "Unknown"),
                )
            media.append(InputMediaPhoto(media=image["url"], caption=caption))

        async with ChatActionSender(bot=bot, action=ChatAction.UPLOAD_PHOTO, chat_id=chat_id):
            await callback.message.answer_media_group(media=media)
        await send_vpn_ad(chat_id)
    except Exception:
        await send_error(chat_id=chat_id, text="Ошибка при отправке изображений.")
        raise


@router.callback_query(F.data.startswith("audio"))
async def handle_audio(callback: CallbackQuery) -> AnswerCallbackQuery:
    chat_id = callback.message.chat.id
    await callback.answer()

    if user_activity_queue.get_download(chat_id=chat_id):
        await send_error(chat_id=chat_id, text=MessageTemplates.CALLBACK_PROCESSING_MESSAGE)
        return

    session = user_session_storage.get_session(chat_id=chat_id)
    if session is None:
        await send_error(chat_id=chat_id, text=MessageTemplates.CALLBACK_SESSION_EXPIRED)
        return

    try:
        user_activity_queue.create_download(
            url=session["url"],
            chat_id=chat_id,
            service=session["service"],
        )

        _, audio_id = callback.data.split(":", 1)
        audio = next((a for a in session["media_data"]["audios"] if a.get("id") == audio_id), None)

        if not audio:
            await send_error(chat_id=chat_id, text="Не нашла выбранный формат аудио. Отправь ссылку заново.")
            return

        audio_name = _safe_media_id(audio)
        if not audio_name:
            await send_error(chat_id=chat_id, text="У аудио нет идентификатора (name/id).")
            return

        # TikTok: прямая ссылка на mp3 файл
        if audio_name == "music":
            download_audio.delay(
                direct=True,
                url=audio.get("url"),
                audio_id=audio_name,
                service=session["service"],
                chat_id=chat_id,
                message_id=callback.message.message_id,
                original_url=session["url"],
            )
        # Instagram/Rutube/YouTube: извлечение лучшего аудио из видео
        elif audio_name == "bestaudio":
            download_audio.delay(
                direct=False,
                url=audio.get("url"),
                audio_id="bestaudio",
                service=session["service"],
                chat_id=chat_id,
                message_id=callback.message.message_id,
                original_url=session["url"],
            )
        else:
            # YouTube/Rutube: конкретный format_id
            download_audio.delay(
                direct=False,
                url=session["url"],
                audio_id=audio_name,
                service=session["service"],
                chat_id=chat_id,
                message_id=callback.message.message_id,
            )

    except Exception:
        await send_error(chat_id=chat_id, text="Ошибка при обработке аудио. Попробуй ещё раз.")
        raise
    finally:
        # ✅ всегда снимаем блокировку
        try:
            user_activity_queue.delete_download(chat_id=chat_id)
        except Exception:
            pass


@router.callback_query(F.data.startswith("thumbnail"))
async def handle_thumbnail(callback: CallbackQuery) -> AnswerCallbackQuery:
    chat_id = callback.message.chat.id
    await callback.answer()

    session = user_session_storage.get_session(chat_id=chat_id)
    if session is None:
        await send_error(chat_id=chat_id, text=MessageTemplates.CALLBACK_SESSION_EXPIRED)
        return

    try:
        _, thumbnail_id = callback.data.split(":", 1)
        thumbnail = next((t for t in session["media_data"]["thumbnails"] if t.get("id") == thumbnail_id), None)

        if not thumbnail:
            await send_error(chat_id=chat_id, text="Не нашла превью. Отправь ссылку заново.")
            return

        caption = MessageTemplates.CALLBACK_THUMBNAIL_CAPTION.format(
            url=session["url"],
            service=session["service"],
            botname=settings.telegram.name,
            author_name=session["media_data"].get("author_name", "Unknown"),
        )

        async with ChatActionSender(bot=bot, action=ChatAction.UPLOAD_PHOTO, chat_id=chat_id):
            await callback.message.answer_photo(photo=thumbnail.get("url"), caption=caption)

    except Exception:
        await send_error(chat_id=chat_id, text="Ошибка при отправке превью.")
        raise


# ─── Delivery choice callbacks ───────────────────────────────────────

@router.callback_query(F.data == "deliver_tg")
async def handle_deliver_tg(callback: CallbackQuery) -> None:
    """Пользователь выбрал отправить видео в Telegram."""
    await callback.answer()
    chat_id = callback.message.chat.id

    data = delivery_store.get(chat_id)
    if not data:
        await callback.message.edit_text("⏰ Время ожидания истекло. Запросите видео заново.")
        return

    delivery_store.delete(chat_id)
    try:
        await callback.message.edit_text("📱 Отправляю в Telegram...")
    except Exception:
        pass

    await deliver_to_telegram(
        chat_id=chat_id,
        video_path=data["video_path"],
        file_size=data["file_size"],
        url=data["url"],
        service=data["service"],
        width=data.get("width") or 0,
        height=data.get("height") or 0,
        original_url=data.get("original_url", ""),
        author_name=data.get("author_name", "Unknown"),
        ask_message_id=callback.message.message_id,
    )


@router.callback_query(F.data == "deliver_drive")
async def handle_deliver_drive(callback: CallbackQuery) -> None:
    """Пользователь выбрал получить ссылку на Drive."""
    await callback.answer()
    chat_id = callback.message.chat.id

    data = delivery_store.get(chat_id)
    if not data:
        await callback.message.edit_text("⏰ Время ожидания истекло. Запросите видео заново.")
        return

    delivery_store.delete(chat_id)
    try:
        await callback.message.edit_text("🔗 Загружаю на Google Drive...")
    except Exception:
        pass

    await _deliver_via_drive(
        chat_id=chat_id,
        video_path=data["video_path"],
        file_size=data["file_size"],
        service=data["service"],
    )


@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    bot = callback.bot
    await callback.answer()

    subscription_channels = settings.telegram.subscription_channels

    missing_subscriptions = []
    for channel_username in subscription_channels:
        clean_username = channel_username.lstrip("@")
        try:
            chat = await bot.get_chat(f"@{clean_username}")
            chat_id = chat.id

            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status not in (
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.CREATOR,
            ):
                missing_subscriptions.append({
                    "chat_id": chat_id,
                    "username": f"@{clean_username}",
                    "title": chat.title if hasattr(chat, "title") else f"@{clean_username}"
                })
        except Exception:
            missing_subscriptions.append({
                "chat_id": None,
                "username": f"@{clean_username}",
                "title": f"@{clean_username}"
            })

    if not missing_subscriptions:
        await callback.message.edit_text(text=MessageTemplates.SUBSCRIPTION_SUCCESS)
        await callback.answer(text=MessageTemplates.SUBSCRIPTION)
        return

    reply_markup = get_retry_subscription_keyboard(missing_subscriptions=missing_subscriptions)

    if len(missing_subscriptions) == 1:
        channel_name = missing_subscriptions[0]["title"]
        text = MessageTemplates.SUBSCRIPTION_ERROR_ONE.format(channel_name=channel_name)
    else:
        channels_list = "\n".join([f"• {ch['title']}" for ch in missing_subscriptions])
        text = MessageTemplates.SUBSCRIPTION_ERROR_MANY.format(channel_list=channels_list)

    try:
        await callback.message.edit_text(text=text, reply_markup=reply_markup)
    except Exception:
        pass
    await callback.answer(text=MessageTemplates.NOT_SUBSCRIPTION)