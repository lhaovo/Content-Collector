from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    zhipu_api_key: str = ""
    content_collector_db: str = "./data/content.db"
    content_collector_model: str = "glm-4.6v-flash"
    content_collector_auto_accept_confidence: float = 0.86
    content_collector_enable_ai_grouping: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def database_url(self) -> str:
        db_path = Path(self.content_collector_db)
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db_path.as_posix()}"


settings = Settings()