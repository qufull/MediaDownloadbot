from aiogram import Bot
from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.telegram import TelegramAPIServer
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession

from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

import logging
logger = logging.getLogger(__name__)

from .settings import AppSettings

from src.core import Downloader
from src.databases import (
    MediaCacheStorage,
    UserActivityQueue,
    UserSessionStorage,
    FileIdCache,
    YouTubeRateLimiter,
)
from src.databases.user_registry import UserRegistry
from src.core import (
    RedditExtractor,
    TikTokExtractor,
    RutubeExtractor,
    YoutubeExtractor,
    InstagramExtractor,
    TwitterExtractor,
    VKExtractor,
)


# ======= Settings =======
settings = AppSettings()


# ======= Extractors =======
youtube_extractor = YoutubeExtractor(
    massbots_token=settings.massbots.token,
    massbots_bot_id=settings.massbots.bot_id,
)
logger.warning(">>> MASSBOTS BUILD: YouTube через massbots API, yt-dlp для YouTube ОТКЛЮЧЕН <<<")


# ======= Downloader =======
downloader = Downloader(
    output_path=settings.local_storage.media_storage_path,
    cookie_path=settings.local_storage.browser_cookie_path,
    rutube_proxy=settings.proxy.rutube_proxy,
    massbots_token=settings.massbots.token,
    massbots_bot_id=settings.massbots.bot_id,
    bot_token=settings.telegram.token,
)

reddit_extractor = RedditExtractor(
    client_id=settings.reddit.client_id,
    client_secret=settings.reddit.client_secret,
    cookie_path=settings.local_storage.browser_cookie_path,
)

tiktok_extractor = TikTokExtractor(
    cookie_path=settings.local_storage.browser_cookie_path,
)

rutube_extractor = RutubeExtractor(
    cookie_path=settings.local_storage.browser_cookie_path,
    proxy=settings.proxy.rutube_proxy,
)

instagram_extractor = InstagramExtractor(
    cookie_path=settings.local_storage.browser_cookie_path,
)

twitter_extractor = TwitterExtractor(
    cookie_path=settings.local_storage.browser_cookie_path,
)

vk_extractor = VKExtractor(
    cookie_path=settings.local_storage.browser_cookie_path,
)

# ======= Databases =======
file_id_cache = FileIdCache(
    host=settings.redis.host,
    port=settings.redis.port,
    db=settings.redis.file_id_db,
)

media_cache_storage = MediaCacheStorage(
    host=settings.redis.host,
    port=settings.redis.port,
    db=settings.redis.media_cache_db,
)

user_session_storage = UserSessionStorage(
    host=settings.redis.host,
    port=settings.redis.port,
    db=settings.redis.user_session_db,
)

user_activity_queue = UserActivityQueue(
    host=settings.redis.host,
    port=settings.redis.port,
    db=settings.redis.user_activity_db,
)
user_activity_queue.clear_all()

youtube_rate_limiter = YouTubeRateLimiter(
    host=settings.redis.host,
    port=settings.redis.port,
    db=settings.redis.user_activity_db,
)

user_registry = UserRegistry(
    db_path="data/users.db",
)


# ======= Telegram =======
session = AiohttpSession(
    api=TelegramAPIServer.from_base(
        base=settings.telegram.server_url
    )
)

default_properties = DefaultBotProperties(
    parse_mode=ParseMode.HTML,
)

bot = Bot(
    token=settings.telegram.token,
    default=default_properties,
    session=session,
)

fsm_redis = Redis(
    host=settings.redis.host,
    port=settings.redis.port,
    db=settings.redis.user_session_db,
)
fsm_storage = RedisStorage(redis=fsm_redis)

dp = Dispatcher(storage=fsm_storage)