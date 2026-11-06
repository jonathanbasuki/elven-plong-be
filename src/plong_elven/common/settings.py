from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str = ""
    access_token_minutes: int = 15
    refresh_token_days: int = 30

    app_env: str = "prod"
    internal_api_key: str = ""

    apns_key_id: str = ""
    apns_key_path: str = ""
    apns_team_id: str = ""
    apns_topic: str = ""  # the app's bundle id
    apns_use_sandbox: bool = True
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_footage_bucket: str = "plong-task-footage"
    mediamtx_recordings_dir: str = "./recordings"
    mediamtx_record_path: str = "cam1"
    mediamtx_record_segment_seconds: int = 10


def check_security_settings(s: Settings) -> None:
    """Validate security settings at startup.

    Outside APP_ENV=dev, INTERNAL_API_KEY and JWT_SECRET must be at least
    32 characters. Raises RuntimeError on violation.
    """
    if s.app_env == "dev":
        return
    if len(s.internal_api_key) < 32:
        raise RuntimeError(
            "INTERNAL_API_KEY must be at least 32 characters outside APP_ENV=dev"
        )
    if len(s.jwt_secret) < 32:
        raise RuntimeError(
            "JWT_SECRET must be at least 32 characters outside APP_ENV=dev"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
