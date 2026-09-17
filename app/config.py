from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg2://maiders:maiders@localhost:5432/maiders"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
