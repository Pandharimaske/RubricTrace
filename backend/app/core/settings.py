import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Load .env once, here, since this module is imported (directly or transitively)
# by every entry point: the API app, the batch scripts, and the test suite.
# override=False so real environment variables (e.g. set in CI or the shell)
# always win over whatever is sitting in .env.
load_dotenv(PROJECT_ROOT / ".env", override=False)

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
UPLOAD_DIR = RAW_DIR / "uploads"
PAGE_IMAGE_DIR = PROCESSED_DIR / "page_images"

STORAGE_BACKEND = os.getenv("RUBRICTRACE_STORAGE_BACKEND", "local").lower()
STORAGE_ENDPOINT_URL = os.getenv("RUBRICTRACE_STORAGE_ENDPOINT_URL", "")
STORAGE_ACCESS_KEY = os.getenv("RUBRICTRACE_STORAGE_ACCESS_KEY", "")
STORAGE_SECRET_KEY = os.getenv("RUBRICTRACE_STORAGE_SECRET_KEY", "")
STORAGE_BUCKET = os.getenv("RUBRICTRACE_STORAGE_BUCKET", "rubrictrace")
STORAGE_REGION = os.getenv("RUBRICTRACE_STORAGE_REGION", "us-east-1")
DATABASE_URL = os.getenv("RUBRICTRACE_DATABASE_URL", "")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")


def ensure_data_dirs() -> None:
    for directory in [RAW_DIR, PROCESSED_DIR, UPLOAD_DIR, PAGE_IMAGE_DIR]:
        directory.mkdir(parents=True, exist_ok=True)
