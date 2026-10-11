import sys
from importlib.util import find_spec
from pathlib import Path

# Everything the backend imports at runtime (see pyproject.toml).
CORE_PACKAGES = [
    "fastapi",
    "uvicorn",
    "pydantic_settings",
    "pandas",
    "numpy",
    "PIL",
    "pymupdf",
    "requests",
    "openai",
    "psycopg",
    "psycopg_pool",
]

# Only needed when uploads are mirrored to S3-compatible storage.
OPTIONAL_PACKAGES = [
    "boto3",
]


def check_packages() -> bool:
    missing = [package for package in CORE_PACKAGES if find_spec(package) is None]
    optional_missing = [package for package in OPTIONAL_PACKAGES if find_spec(package) is None]

    if missing:
        print("Missing required packages (run `uv sync` in backend/):")
        for package in missing:
            print(f"- {package}")
        return False

    if optional_missing:
        print("Optional packages not installed (only needed for S3 mirroring):")
        for package in optional_missing:
            print(f"- {package}")
    return True


def check_database() -> bool:
    """The database is required: it must be reachable and have the schema applied."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from backend.app.core.config import settings
    from backend.app.db.database import Database

    if not settings.database_url:
        print("RUBRICTRACE_DATABASE_URL is not set (see backend/.env.example).")
        return False
    db = Database.from_settings(settings)
    try:
        db.check_schema()
    except Exception as exc:
        print(f"Database check failed: {exc}")
        return False
    finally:
        db.close()
    print("Database reachable, schema present.")
    return True


def main() -> None:
    if not (check_packages() and check_database()):
        raise SystemExit(1)
    print("RubricTrace setup looks good.")


if __name__ == "__main__":
    main()
