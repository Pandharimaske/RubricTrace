"""Application configuration loaded once from environment variables and ``.env``."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The directory that owns pyproject.toml, .env and data/ (not the repository root).
BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT  # legacy name still used by the maintenance scripts


class Settings(BaseSettings):
    """Validated configuration shared by the API, workers, scripts, and services."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="RUBRICTRACE_",
        extra="ignore",
    )

    # --- API ---
    # Origins allowed to call the API from a browser. The dev frontend proxies /api, so
    # this only matters when the UI is served from somewhere else. JSON list in the env var.
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    max_upload_mb: int = 50
    allowed_upload_suffixes: tuple[str, ...] = (
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".tif",
        ".tiff",
        ".txt",
    )

    # --- Storage / database ---
    # Where uploads, page images and caches live (default: backend/data).
    data_root: Path | None = None
    storage_backend: str = "local"
    storage_endpoint_url: str = ""
    storage_access_key: str = ""
    storage_secret_key: str = ""
    storage_bucket: str = "rubrictrace"
    storage_region: str = "us-east-1"
    # PostgreSQL connection string (Supabase pooler or direct). Required: there is no local
    # database fallback. The schema comes from supabase/migrations/.
    database_url: str = ""
    db_pool_max: int = 10

    supabase_url: str = Field(default="", validation_alias="SUPABASE_URL")
    supabase_service_key: str = Field(default="", validation_alias="SUPABASE_SERVICE_KEY")
    supabase_key: str = Field(default="", validation_alias="SUPABASE_KEY")

    # --- Models (NVIDIA API Catalog; the only supported provider) ---
    # Vision model that reads exam pages: one structured call per page. Fallback if its free
    # endpoint is slow or throttled: meta/llama-3.2-90b-vision-instruct.
    nvidia_vlm_model: str = "moonshotai/kimi-k3"
    # Text model that grades answers (objective answers it can't decide itself, and all
    # descriptive ones).
    nvidia_llm_model: str = "openai/gpt-oss-20b"
    model_timeout: float = 300.0
    model_temperature: float = 0.0
    model_max_tokens: int = 8192
    nvidia_sdk_max_retries: int = 5
    nvidia_vlm_extra_body: str = ""
    structured_max_retries: int = 2
    debug_vlm: bool = False

    extraction_cache: bool = True
    extraction_cache_dir: Path | None = None
    nvidia_api_key: str = Field(default="", validation_alias="NVIDIA_API_KEY")
    nvidia_api_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1",
        validation_alias="NVIDIA_API_BASE_URL",
    )

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def data_dir(self) -> Path:
        return self.data_root or BACKEND_ROOT / "data"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def upload_dir(self) -> Path:
        return self.raw_dir / "uploads"

    @property
    def page_image_dir(self) -> Path:
        return self.processed_dir / "page_images"

    @property
    def resolved_extraction_cache_dir(self) -> Path:
        return self.extraction_cache_dir or self.processed_dir / "extraction_cache"

    def ensure_data_dirs(self) -> None:
        for directory in (self.raw_dir, self.processed_dir, self.upload_dir, self.page_image_dir):
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()

# Path constants for the maintenance scripts, which evaluate defaults at import time.
DATA_DIR = settings.data_dir
RAW_DIR = settings.raw_dir
PROCESSED_DIR = settings.processed_dir
UPLOAD_DIR = settings.upload_dir
PAGE_IMAGE_DIR = settings.page_image_dir
DATABASE_URL = settings.database_url


def ensure_data_dirs() -> None:
    """Create the application data directories used by file-backed workflows."""
    settings.ensure_data_dirs()
