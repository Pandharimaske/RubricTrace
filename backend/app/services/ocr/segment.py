import re

QUESTION_HEADING = re.compile(r"(?im)^\s*(?:q(?:uestion)?\s*)?(\d+)\s*[.):\-]\s*")


def normalize_question_id(question_id: str) -> str:
    value = question_id.strip().lower()
    value = re.sub(r"^(?:q(?:uestion)?\s*)", "", value)
    return value.lstrip("0") or "0"


def segment_questions(text: str) -> dict[str, str]:
    matches = list(QUESTION_HEADING.finditer(text))
    if not matches:
        return {"1": text.strip()} if text.strip() else {}

    segments: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        question_id = normalize_question_id(match.group(1))
        answer = text[start:end].strip()
        segments[question_id] = answer
    return segments
