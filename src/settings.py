from typing import List, Annotated, Optional
from pydantic import Field, DirectoryPath, FilePath
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    token: Annotated[str, Field(validation_alias="bot_token")]
    name: Annotated[str, Field(validation_alias="bot_name", min_length=1)]
    server_url: Annotated[str, Field(validation_alias="bot_server_url")]
    admin_ids: Annotated[List[int], Field(validation_alias="bot_admin_ids")]
    subscription_channels: Annotated[List[str], Field(validation_alias="bot_subscription_channels")]


class TelethonSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    api_id: Annotated[int, Field(validation_alias="telegram_api_id")]
    api_hash: Annotated[str, Field(validation_alias="telegram_api_hash")]


class RedditSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    client_id: Annotated[str, Field(validation_alias="reddit_client_id")]
    client_secret: Annotated[str, Field(validation_alias="reddit_client_secret")]


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    host: Annotated[str, Field(validation_alias="redis_host")]
    port: Annotated[int, Field(validation_alias="redis_port")]
    broker_db: Annotated[int, Field(validation_alias="redis_broker_db")]
    backend_db: Annotated[int, Field(validation_alias="redis_backend_db")]
    media_cache_db: Annotated[int, Field(validation_alias="redis_media_cache_db")]
    user_session_db: Annotated[int, Field(validation_alias="redis_user_session_db")]
    user_activity_db: Annotated[int, Field(validation_alias="redis_user_activity_db")]
    file_id_db: Annotated[int, Field(validation_alias="redis_file_id_db")]


class LocalStorageSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    browser_cookie_path: Annotated[Optional[str], Field(default=None, validation_alias="browser_cookie_path")]
    media_storage_path: Annotated[DirectoryPath, Field(validation_alias="media_storage_path")]


class ProxySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    # Формат: http://user:pass@host:port или http://host:port
    rutube_proxy: Annotated[Optional[str], Field(default=None, validation_alias="rutube_proxy")]
    youtube_proxy: Annotated[Optional[str], Field(default=None, validation_alias="YOUTUBE_PROXY")]


class MassbotsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    token: Annotated[str, Field(validation_alias="massbots_token")]
    bot_id: Annotated[Optional[str], Field(default=None, validation_alias="massbots_bot_id")]


class AppSettings:
    telegram: TelegramSettings
    telethon: TelethonSettings
    reddit: RedditSettings
    redis: RedisSettings
    local_storage: LocalStorageSettings
    proxy: ProxySettings
    massbots: MassbotsSettings

    def __init__(self):
        self.telegram = TelegramSettings()
        self.telethon = TelethonSettings()
        self.reddit = RedditSettings()
        self.redis = RedisSettings()
        self.local_storage = LocalStorageSettings()
        self.proxy = ProxySettings()
        self.massbots = MassbotsSettings()