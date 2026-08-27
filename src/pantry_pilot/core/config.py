from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PANTRY_", env_file=".env")
    db_path: Path = Path("data/pantry.db") # where is the pantry db file?
    echo_sql: bool = False # should we print every db query?
    anthropic_api_key: str = Field(  # LLM key; read the SDK's standard env var name
        default="", validation_alias="ANTHROPIC_API_KEY"
    )

    @property   # lets you read a computed value like a normal attribute
    def database_url(self) -> str:  # computed value
        return f"sqlite:///{self.db_path}"
