from skill_scripts.prompt_sql_consistency import validate_prompt_sql_consistency


def test_validate_prompt_sql_consistency_detects_year_mismatch():
    ok, code = validate_prompt_sql_consistency(
        "查詢2026年的工程預算明細",
        "SELECT * FROM [VPIC1].[dbo].[ACTMK] WHERE [MK002] = '2025'",
    )
    assert ok is False
    assert code == "YEAR_MISMATCH"


def test_validate_prompt_sql_consistency_detects_top_mismatch():
    ok, code = validate_prompt_sql_consistency(
        "top 10 查詢預算",
        "SELECT TOP 20 * FROM [VPIC1].[dbo].[ACTMK]",
    )
    assert ok is False
    assert code == "TOP_MISMATCH"


def test_validate_prompt_sql_consistency_accepts_matched_constraints():
    ok, code = validate_prompt_sql_consistency(
        "查詢2026年的工程預算明細前 20 筆",
        "SELECT TOP 20 * FROM [VPIC1].[dbo].[ACTMK] WHERE [MK002] = '2026'",
    )
    assert ok is True
    assert code == "OK"
