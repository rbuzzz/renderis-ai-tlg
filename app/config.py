from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8')

    # Telegram
    bot_token: str = Field(..., alias='BOT_TOKEN')
    telegram_admin_ids: str = Field('', alias='TELEGRAM_ADMIN_IDS')

    # Database
    database_url: str = Field(..., alias='DATABASE_URL')

    # Kie.ai
    kie_api_key: str = Field(..., alias='KIE_API_KEY')

    # Payments (Stars)
    stars_provider_token: str = Field('', alias='STARS_PROVIDER_TOKEN')
    stars_currency: str = Field('XTR', alias='STARS_CURRENCY')

    # Defaults
    signup_bonus_credits: int = Field(3, alias='SIGNUP_BONUS_CREDITS')
    admin_free_mode_default: bool = Field(True, alias='ADMIN_FREE_MODE_DEFAULT')
    max_outputs_per_request: int = Field(4, alias='MAX_OUTPUTS_PER_REQUEST')
    per_user_max_concurrent_jobs: int = Field(2, alias='PER_USER_MAX_CONCURRENT_JOBS')
    global_max_poll_concurrency: int = Field(10, alias='GLOBAL_MAX_POLL_CONCURRENCY')
    per_user_generate_cooldown_seconds: int = Field(5, alias='PER_USER_GENERATE_COOLDOWN_SECONDS')
    daily_spend_cap_credits: int = Field(500, alias='DAILY_SPEND_CAP_CREDITS')
    refund_on_fail: bool = Field(True, alias='REFUND_ON_FAIL')
    max_prompt_length: int = Field(20000, alias='MAX_PROMPT_LENGTH')

    # Reference images
    reference_storage_path: str = Field('/var/www/tonmd.cloud/ref', alias='REFERENCE_STORAGE_PATH')
    public_file_base_url: str = Field('https://tonmd.cloud/ref', alias='PUBLIC_FILE_BASE_URL')
    max_reference_images: int = Field(8, alias='MAX_REFERENCE_IMAGES')

    # Admin web
    admin_web_enabled: bool = Field(False, alias='ADMIN_WEB_ENABLED')
    admin_web_host: str = Field('127.0.0.1', alias='ADMIN_WEB_HOST')
    admin_web_port: int = Field(9001, alias='ADMIN_WEB_PORT')
    admin_web_username: str = Field('admin', alias='ADMIN_WEB_USERNAME')
    admin_web_password: str = Field('', alias='ADMIN_WEB_PASSWORD')
    admin_web_secret: str = Field('change-me', alias='ADMIN_WEB_SECRET')

    # Polling
    poll_max_wait_seconds: int = Field(180, alias='POLL_MAX_WAIT_SECONDS')
    poll_backoff_sequence: str = Field('1,2,3,5,8,13,20', alias='POLL_BACKOFF_SEQUENCE')

    # Safety
    nsfw_blocklist: str = Field('', alias='NSFW_BLOCKLIST')

    # Logging
    log_level: str = Field('INFO', alias='LOG_LEVEL')

    def admin_ids(self) -> List[int]:
        if not self.telegram_admin_ids:
            return []
        return [int(x.strip()) for x in self.telegram_admin_ids.split(',') if x.strip()]

    def poll_backoff_list(self) -> List[int]:
        return [int(x.strip()) for x in self.poll_backoff_sequence.split(',') if x.strip()]

    def nsfw_terms(self) -> List[str]:
        if not self.nsfw_blocklist:
            return []
        return [x.strip().lower() for x in self.nsfw_blocklist.split(',') if x.strip()]


@lru_cache

def get_settings() -> Settings:
    return Settings()
