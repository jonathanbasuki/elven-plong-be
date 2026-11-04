from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str = ""
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    internal_job_secret: str = ""

    apns_key_id: str = ""
    apns_key_path: str = ""
    apns_team_id: str = ""
    apns_topic: str = ""  # the app's bundle id
    apns_use_sandbox: bool = True
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_snapshots_bucket: str = "plong-snapshots"


@lru_cache
def get_settings() -> Settings:
    return Settings()
