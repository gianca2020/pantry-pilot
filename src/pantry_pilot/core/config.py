from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PANTRY_", env_file=".env")
    db_path: Path = Path("data/pantry.db") # where is the pantry db file?
    echo_sql: bool = False # should we print every db query?
    # Spoonacular API key for Phase-2b recipe retrieval. Reads PANTRY_SPOONACULAR_API_KEY
    # (or the .env file); defaults to "" so the app imports fine without a key configured.
    spoonacular_api_key: str = ""

    @property   # lets you read a computed value like a normal attribute
    def database_url(self) -> str:  # computed value
        return f"sqlite:///{self.db_path}"
