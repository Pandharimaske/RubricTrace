from importlib.util import find_spec

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
]

# Only needed for the optional S3 / PostgreSQL backends.
OPTIONAL_PACKAGES = [
    "boto3",
    "psycopg",
]


def main() -> None:
    missing = [package for package in CORE_PACKAGES if find_spec(package) is None]
    optional_missing = [package for package in OPTIONAL_PACKAGES if find_spec(package) is None]

    if missing:
        print("Missing required packages (run `uv sync` in backend/):")
        for package in missing:
            print(f"- {package}")
        raise SystemExit(1)

    if optional_missing:
        print("Optional packages not installed (only needed for S3 / PostgreSQL):")
        for package in optional_missing:
            print(f"- {package}")

    print("RubricTrace setup looks good.")


if __name__ == "__main__":
    main()
