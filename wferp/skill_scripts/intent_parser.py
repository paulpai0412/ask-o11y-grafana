import re
from typing import Any


JsonDict = dict[str, Any]


NON_SELECT_PATTERNS = (
    r"更新",
    r"刪除",
    r"删除",
    r"新增",
    r"修改",
    r"insert",
    r"update",
    r"delete",
    r"create",
    r"alter",
    r"drop",
)


def parse_intent(prompt: str) -> JsonDict:
    text = str(prompt or "").strip()
    lowered = text.lower()

    top = 50
    explicit_top = False
    m_top = re.search(r"\btop\s*(\d+)\b", lowered)
    m_front = re.search(r"前\s*(\d+)\s*筆", text)
    if m_top:
        top = int(m_top.group(1))
        explicit_top = True
    elif m_front:
        top = int(m_front.group(1))
        explicit_top = True

    m_year = re.search(r"(?<!\d)(20\d{2}|19\d{2})(?!\d)", text)
    year = m_year.group(1) if m_year else None

    if year and not explicit_top:
        top = None

    non_select_intent = any(re.search(pat, lowered if pat.isascii() else text, re.IGNORECASE) for pat in NON_SELECT_PATTERNS)

    return {
        "raw": text,
        "top": max(1, min(top, 5000)) if top is not None else None,
        "year": year,
        "explicit_top": explicit_top,
        "non_select_intent": non_select_intent,
    }
