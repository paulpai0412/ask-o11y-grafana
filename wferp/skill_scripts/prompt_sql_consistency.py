import re


YEAR_PATTERN = re.compile(r"(?<!\d)(20\d{2}|19\d{2})(?!\d)")
TOP_PATTERN = re.compile(r"\btop\s*(\d+)\b", re.IGNORECASE)


def validate_prompt_sql_consistency(prompt: str, sql: str) -> tuple[bool, str]:
    prompt_text = str(prompt or "")
    sql_text = str(sql or "")
    sql_upper = sql_text.upper()

    year_match = YEAR_PATTERN.search(prompt_text)
    if year_match and year_match.group(1) not in sql_text:
        return False, "YEAR_MISMATCH"

    top_match_en = TOP_PATTERN.search(prompt_text)
    top_match_zh = re.search(r"前\s*(\d+)\s*筆", prompt_text)
    expected_top = None
    if top_match_en:
        expected_top = top_match_en.group(1)
    elif top_match_zh:
        expected_top = top_match_zh.group(1)

    if expected_top and f"TOP {expected_top}" not in sql_upper:
        return False, "TOP_MISMATCH"

    if "預算" in prompt_text and "ACTM" not in sql_upper and "BUDGET" not in sql_upper:
        return False, "DOMAIN_MISMATCH"

    return True, "OK"
