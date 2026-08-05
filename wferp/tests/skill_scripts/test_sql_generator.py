from skill_scripts.sql_generator import generate_select_sql


def sample_bundle():
    return {
        "modules": [
            {"ModuleID": "PUR", "ModuleName": "採購管理系統"},
            {"ModuleID": "ADM", "ModuleName": "管理維護系統"},
        ],
        "tables": [
            {"DB": "DSCSYS.dbo.", "TableID": "PURTA", "TableName": "採購單單頭檔", "TableNameViet": "HS tiêu đề đơn mua", "ModuleID": "PUR", "ModuleName": "採購管理系統"},
            {"DB": "DSCSYS.dbo.", "TableID": "ADMMC", "TableName": "使用者資料檔", "TableNameViet": "HS dữ liệu người dùng", "ModuleID": "ADM", "ModuleName": "管理維護系統"},
        ],
        "fields": [],
        "index_keys": [],
    }


def test_generate_select_sql_returns_select_statement():
    sql = generate_select_sql("查詢採購單", sample_bundle())
    assert sql.startswith("SELECT TOP 50")
    assert "FROM [DSCSYS].[dbo].[PURTA]" in sql


def test_generate_select_sql_parses_top_from_prompt_variants():
    sql_zh = generate_select_sql("查詢採購單前 20 筆", sample_bundle())
    assert sql_zh.startswith("SELECT TOP 20")

    sql_en = generate_select_sql("top 15 purchase orders", sample_bundle())
    assert sql_en.startswith("SELECT TOP 15")


def test_generate_select_sql_fallback_to_admmc():
    sql = generate_select_sql("查詢未知資料", sample_bundle())
    assert "FROM [DSCSYS].[dbo].[ADMMC]" in sql


def test_generate_select_sql_rejects_non_select_intent():
    try:
        generate_select_sql("更新採購單狀態", sample_bundle())
        assert False, "expected ValueError"
    except ValueError as exc:
        assert str(exc) == "NON_SELECT_INTENT"


def test_generate_select_sql_deterministic_on_tie_scores():
    bundle = {
        "modules": [{"ModuleID": "INV", "ModuleName": "庫存管理系統"}],
        "tables": [
            {"DB": "DSCSYS.dbo.", "TableID": "INVTB", "TableName": "庫存交易檔", "TableNameViet": "", "ModuleID": "INV"},
            {"DB": "DSCSYS.dbo.", "TableID": "INVTA", "TableName": "庫存主檔", "TableNameViet": "", "ModuleID": "INV"},
        ],
        "fields": [],
        "index_keys": [],
    }
    sql = generate_select_sql("查詢庫存管理系統資料", bundle)
    assert "FROM [DSCSYS].[dbo].[INVTA]" in sql


def test_generate_select_sql_budget_detail_with_year_uses_actmk_and_year_filter():
    bundle = {
        "modules": [
            {"ModuleID": "ACT", "ModuleName": "會計總帳管理系統"},
            {"ModuleID": "ADM", "ModuleName": "管理維護系統"},
        ],
        "tables": [
            {"DB": "VPIC1.dbo.", "TableID": "ACTMJ", "TableName": "科目/部門預算單頭檔", "TableNameViet": "", "ModuleID": "ACT"},
            {"DB": "VPIC1.dbo.", "TableID": "ACTMK", "TableName": "科目/部門預算單身檔", "TableNameViet": "", "ModuleID": "ACT"},
            {"DB": "DSCSYS.dbo.", "TableID": "ADMMC", "TableName": "使用者資料檔", "TableNameViet": "", "ModuleID": "ADM"},
        ],
        "fields": [],
        "index_keys": [],
    }
    sql = generate_select_sql("查詢2026年的工程預算明細", bundle)
    assert sql.startswith("SELECT * FROM [VPIC1].[dbo].[ACTMK]")
    assert "WHERE [MK002] = '2026'" in sql


def test_generate_select_sql_honors_default_db_override(monkeypatch):
    monkeypatch.setenv("WFERP_DEFAULT_DB", "CHD")
    sql = generate_select_sql("查詢採購單前 20 筆", sample_bundle())
    assert sql.startswith("SELECT TOP 20")
    assert "FROM [CHD].[dbo].[PURTA]" in sql
