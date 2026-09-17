from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg2://maiders:maiders@localhost:5432/maiders"

    # Signs the session cookie. MUST be set to a fixed, random value in any
    # real deployment (see .env.example) — every uvicorn worker process
    # needs to share the same key, and it needs to survive restarts, or
    # logged-in sessions become invalid at random.
    secret_key: str = "insecure-dev-secret-change-me"
    session_max_age_seconds: int = 60 * 60 * 24 * 7  # 7 days

    # If set (and no users exist yet), an admin user is created on startup.
    admin_username: str | None = None
    admin_password: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
