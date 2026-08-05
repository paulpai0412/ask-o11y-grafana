from skill_scripts.schema_context_builder import build_context_slice


def test_build_context_slice_returns_topk_tables_and_columns():
    bundle = {
        "modules": [{"ModuleID": "ACT", "ModuleName": "會計總帳管理系統"}],
        "tables": [
            {"TableID": "ACTMK", "TableName": "科目/部門預算單身檔", "ModuleID": "ACT", "DB": "VPIC1.dbo."},
            {"TableID": "ACTMJ", "TableName": "科目/部門預算單頭檔", "ModuleID": "ACT", "DB": "VPIC1.dbo."},
        ],
        "fields": [
            {"TableID": "ACTMK", "ID": "MK002", "FieldName": "會計年度", "NameVietnam": "Năm kế toán", "Type": "C", "Length": 4},
            {"TableID": "ACTMK", "ID": "MK006", "FieldName": "期預算", "NameVietnam": "Dự toán kỳ", "Type": "N", "Length": 21.6},
        ],
        "index_keys": [
            {"TableName": "ACTMJ", "IndexColumnName": "MJ001+MJ002+MJ003+MJ005", "isPrimaryKey": "1"},
            {"TableName": "ACTMK", "IndexColumnName": "MK001+MK002+MK003+MK004+MK005", "isPrimaryKey": "1"},
        ],
    }

    ctx = build_context_slice("查詢2026年的工程預算明細，輸出會計年度與期預算", bundle, top_k=2)
    assert "tables" in ctx
    assert "columns" in ctx
    assert any(t["TableID"] == "ACTMK" for t in ctx["tables"])
    assert "ACTMK" in ctx["columns"]
    assert any(col["ID"] == "MK002" for col in ctx["columns"]["ACTMK"])
