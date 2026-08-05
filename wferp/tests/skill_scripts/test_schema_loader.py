import pathlib

from skill_scripts.schema_loader import load_schema_bundle


def test_load_schema_bundle_has_required_keys(tmp_path: pathlib.Path):
    source = tmp_path / "_Source"
    source.mkdir()
    (source / "MoudleName.json").write_text("[]", encoding="utf-8")
    (source / "TableName.json").write_text("[]", encoding="utf-8")
    (source / "TableStructure.json").write_text("[]", encoding="utf-8")
    (source / "TableIndexKey.json").write_text("[]", encoding="utf-8")

    bundle = load_schema_bundle(str(source))
    assert set(bundle.keys()) == {"modules", "tables", "fields", "index_keys"}


def test_load_schema_bundle_smoke_real_source_has_expected_schema_keys():
    bundle = load_schema_bundle("_Source")
    assert bundle["tables"]
    assert bundle["fields"]
    assert bundle["index_keys"]

    field = bundle["fields"][0]
    assert {"TableID", "ID", "Type", "Length", "FieldName", "NameVietnam", "ModuleID", "DB"}.issubset(field.keys())

    table = bundle["tables"][0]
    assert {"TableID", "TableName", "TableNameViet", "ModuleID", "DB"}.issubset(table.keys())

    idx = bundle["index_keys"][0]
    assert {"TableName", "IndexColumnName", "isPrimaryKey"}.issubset(idx.keys())
