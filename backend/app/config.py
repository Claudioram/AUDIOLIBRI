from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Auth
    app_password: str = "changeme"
    jwt_secret: str = "dev-secret-change-in-production"
    jwt_expiry_hours: int = 720

    # MiniMax
    minimax_api_key: str = ""
    minimax_group_id: str = ""
    minimax_model: str = "speech-02-hd"
    minimax_default_voice_id: str = ""

    # Reference voice
    reference_voice_path: Path = Path("/app/storage/reference/narrator_it.wav")

    # Storage
    storage_path: Path = Path("/app/storage")
    data_path: Path = Path("/app/data")

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Domain
    domain: str = "localhost"
    acme_email: str = "admin@example.com"

    # Limits
    max_pdf_size_mb: int = 200
    max_chapter_chars: int = 50_000

    @property
    def pdfs_dir(self) -> Path:
        return self.storage_path / "pdfs"

    @property
    def audio_dir(self) -> Path:
        return self.storage_path / "audio"

    @property
    def m4b_dir(self) -> Path:
        return self.storage_path / "m4b"

    @property
    def db_path(self) -> Path:
        return self.data_path / "audiobook.db"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def async_db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"


settings = Settings()
