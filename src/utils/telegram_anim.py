# src/utils/telegram_anim.py
import logging
from pathlib import Path
from typing import Optional

from aiogram.types import FSInputFile, Message

from src.config import bot

logger = logging.getLogger(__name__)

# /app/src/utils/telegram_anim.py -> parents[1] == /app/src
ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"
WAIT_ANIM = ASSETS_DIR / "load.mp4"
ERROR_ANIM = ASSETS_DIR / "Error.mp4"


def _file_or_none(path: Path) -> Optional[FSInputFile]:
    try:
        if path.exists() and path.is_file():
            return FSInputFile(str(path))
    except Exception:
        pass
    return None


async def send_waiting(chat_id: int, text: str, reply_to_message_id: int | None = None) -> Message:
    anim = _file_or_none(WAIT_ANIM)
    if anim:
        return await bot.send_animation(
            chat_id=chat_id,
            animation=anim,
            caption=text,
            reply_to_message_id=reply_to_message_id,
        )
    # fallback (если файла нет/не примонтирован)
    logger.warning("WAIT_ANIM not found, fallback to text message")
    return await bot.send_message(chat_id=chat_id, text=text, reply_to_message_id=reply_to_message_id)


async def send_error(chat_id: int, text: str, reply_to_message_id: int | None = None) -> Message:
    anim = _file_or_none(ERROR_ANIM)
    if anim:
        return await bot.send_animation(
            chat_id=chat_id,
            animation=anim,
            caption=text,
            reply_to_message_id=reply_to_message_id,
        )
    logger.warning("ERROR_ANIM not found, fallback to text message")
    return await bot.send_message(chat_id=chat_id, text=text, reply_to_message_id=reply_to_message_id)
