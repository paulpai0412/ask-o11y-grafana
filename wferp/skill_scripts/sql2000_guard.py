import re


FORBIDDEN_NON_SELECT = (
    "insert",
    "update",
    "delete",
    "create",
    "alter",
    "drop",
    "merge",
    "truncate",
    "exec",
    "execute",
)

FORBIDDEN_SQL2000_REGEX = (
    r"\bwith\b\s+[\[\(a-zA-Z_][\w\]\)]*\s+as\s*\(",
    r"\bover\b",
    r"\bpartition\s+by\b",
    r"\brow_number\b",
    r"\brank\b",
    r"\bdense_rank\b",
    r"\boffset\b",
    r"\bfetch\b",
    r"\bexcept\b",
    r"\bintersect\b",
    r"\bconcat\s*\(",
)


def _statement_count(sql: str) -> int:
    parts = [p.strip() for p in sql.split(";") if p.strip()]
    return len(parts)


def validate_sql(sql: str) -> tuple[bool, str]:
    s = str(sql or "").strip()
    lowered = s.lower()

    if any(re.search(rf"\b{tok}\b", lowered) for tok in FORBIDDEN_NON_SELECT):
        return False, "NON_SELECT_INTENT"

    if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in FORBIDDEN_SQL2000_REGEX):
        return False, "UNSUPPORTED_SQL2000_FEATURE"

    if _statement_count(s) > 1:
        return False, "MULTI_STATEMENT_NOT_ALLOWED"

    if not lowered.startswith("select"):
        return False, "NON_SELECT_INTENT"

    return True, "OK"
