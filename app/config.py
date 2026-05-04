from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Nelomai Panel"
    debug: bool = True
    secret_key: str = "dev-only-change-me-with-a-long-random-value"
    access_token_expire_minutes: int = 720
    database_url: str = "sqlite+pysqlite:///./nelomai-panel.db"
    panel_public_base_url: str = "http://127.0.0.1:8000"
    nelomai_git_repo: str = ""
    peer_agent_command: str | None = None
    peer_agent_timeout_seconds: int = 20
    peer_agent_bootstrap_timeout_seconds: int = 1800
    login_rate_limit_window_seconds: int = 900
    login_rate_limit_max_attempts: int = 10
    login_rate_limit_lockout_seconds: int = 900


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
