from importlib.util import find_spec

CORE_PACKAGES = [
    "fastapi",
    "pandas",
    "sklearn",
    "PIL",
    "pymupdf",
    "requests",
]

OPTIONAL_PACKAGES = [
    "cv2",
    "sentence_transformers",
]


def main() -> None:
    missing = [package for package in CORE_PACKAGES if find_spec(package) is None]
    optional_missing = [package for package in OPTIONAL_PACKAGES if find_spec(package) is None]

    if missing:
        print("Missing required packages:")
        for package in missing:
            print(f"- {package}")
        raise SystemExit(1)

    if optional_missing:
        print("Optional packages not installed yet:")
        for package in optional_missing:
            print(f"- {package}")

    print("RubricTrace setup looks good.")


if __name__ == "__main__":
    main()
