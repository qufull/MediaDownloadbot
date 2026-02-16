from typing import Dict, List

from aiogram import Router, F
from aiogram.enums import ChatAction, ChatMemberStatus
from aiogram.methods import AnswerCallbackQuery
from aiogram.types import CallbackQuery, InputMediaPhoto
from aiogram.utils.chat_action import ChatActionSender

from .texts import MessageTemplates
from .common import exetract_image_name
from .keyboards import get_retry_subscription_keyboard

from src.config import bot, settings
from src.core import ImageDictAnnotation
from src.config import user_session_storage, user_activity_queue

from src.utils.telegram_anim import send_error  # ✅ добавили

from src.celery_app.tasks.audio_download_worker import download_audio
from src.celery_app.tasks.video_download_worker import (
    download_reddit_video,
    download_rutube_video,
    download_tiktok_video,
    download_youtube_video,
    download_twitter_video,
    download_instagram_video,
    download_vk_video,
)

router = Router(name=__name__)


def _safe_media_id(item: dict) -> str | None:
    """Берём name, если есть, иначе id. Это твой контракт по всему проекту."""
    return (item.get("name") or item.get("id") or "").strip() or None


@router.callback_query(F.data.startswith("video"))
async def handle_video(callback: CallbackQuery) -> AnswerCallbackQuery:
    chat_id = callback.message.chat.id

    # Всегда снимаем "часики" на кнопке
    await callback.answer()

    if user_activity_queue.get_download(chat_id=chat_id):
        await send_error(chat_id=chat_id, text=MessageTemplates.CALLBACK_PROCESSING_MESSAGE)
        return

    session = user_session_storage.get_session(chat_id=chat_id)
    if session is None:
        await send_error(chat_id=chat_id, text=MessageTemplates.CALLBACK_SESSION_EXPIRED)
        return

    try:
        _, video_id = callback.data.split(":", 1)
        video = next((v for v in session["media_data"]["videos"] if v.get("id") == video_id), None)

        if not video:
            await send_error(chat_id=chat_id, text="Не нашла выбранный формат видео. Отправь ссылку заново.")
            return

        url = session["url"]
        width = video.get("width")
        height = video.get("height")
        service = session["service"]
        message_id = callback.message.message_id
        author_name = session["media_data"].get("author_name", "Unknown")

        user_activity_queue.create_download(url=url, chat_id=chat_id, service=service)

        task_map = {
            "twitter": download_twitter_video,
            "youtube": download_youtube_video,
            "reddit": download_reddit_video,
            "rutube": download_rutube_video,
            "tiktok": download_tiktok_video,
            "instagram": download_instagram_video,
            "vk": download_vk_video,
        }
        task = task_map.get(service)
        if not task:
            await send_error(chat_id=chat_id, text=f"Сервис {service} пока не поддерживается для скачивания.")
            return

        chosen_id = _safe_media_id(video)
        if not chosen_id:
            await send_error(chat_id=chat_id, text="У видео нет идентификатора формата (name/id).")
            return

        merge_audio = True if not video.get("has_audio", False) else False

        task.delay(
            url=url,
            width=width,
            height=height,
            chat_id=chat_id,
            message_id=message_id,
            video_id=chosen_id,   # ✅ теперь не падает на name
            merge_audio=merge_audio,
        )

    except Exception as e:
        # Любая ошибка -> гифка
        await send_error(chat_id=chat_id, text="Ошибка при обработке видео. Попробуй ещё раз.")
        raise
    finally:
        # ✅ Критично: всегда снимаем блокировку
        try:
            user_activity_queue.delete_download(chat_id=chat_id)
        except Exception:
            pass


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

    await callback.message.edit_text(text=text, reply_markup=reply_markup)
    await callback.answer(text=MessageTemplates.NOT_SUBSCRIPTION)