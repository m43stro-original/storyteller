from typing import List, Optional, Union
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    TELEGRAM_BOT_TOKEN: str = ""
    CHANNEL_ID: Optional[Union[int, str]] = None
    DISCUSSION_CHAT_ID: Optional[Union[int, str]] = None
    ADMIN_IDS: List[int] = []

    # Narrator: CodeCraft API
    CODECRAFT_API_KEY: str = ""
    CODECRAFT_BASE_URL: str = "https://codecraftapi.com/v1"
    CODECRAFT_MODEL: str = "deepseek-v4-flash-0731"

    # Comments: Groq API
    GROQ_API_KEY: str = ""
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_MODEL: str = "openai/gpt-oss-120b"

    # Scheduling
    SCHEDULE_CRON: str = "10 12 * * *; 0 19 * * *"
    SCHEDULE_INTERVAL_HOURS: Optional[int] = None
    TIMEZONE: str = "Europe/Moscow"

    # Storage
    DB_PATH: str = "data/kudrovo.db"
    LORE_BIBLE_PATH: str = "data/lore_bible.json"
    STORY_PROMPT_PATH: str = "prompts/story_prompt.txt"

    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def parse_admin_ids(cls, v: Union[str, List[int], int]) -> List[int]:
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            if not v.strip():
                return []
            return [int(x.strip()) for x in v.split(",") if x.strip().isdigit()]
        if isinstance(v, list):
            return [int(x) for x in v]
        return []

    @field_validator("CHANNEL_ID", "DISCUSSION_CHAT_ID", mode="before")
    @classmethod
    def parse_chat_id(cls, v: Optional[Union[str, int]]) -> Optional[Union[int, str]]:
        if v is None:
            return None
        if isinstance(v, str):
            val = v.strip()
            if not val:
                return None
            if (val.startswith("-") and val[1:].isdigit()) or val.isdigit():
                return int(val)
            return val
        return v

    @property
    def database_path(self) -> Path:
        p = Path(self.DB_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
