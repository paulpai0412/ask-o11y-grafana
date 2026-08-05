from skill_scripts.metadata_validator import validate_metadata_references


def _bundle():
    return {
        "tables": [
            {"TableID": "ACTMK"},
            {"TableID": "ACTMJ"},
        ],
        "fields": [
            {"TableID": "ACTMK", "ID": "MK002"},
            {"TableID": "ACTMK", "ID": "MK006"},
            {"TableID": "ACTMJ", "ID": "MJ002"},
        ],
    }


def test_validate_metadata_references_rejects_unknown_table():
    ok, code = validate_metadata_references(
        "SELECT [MK002] FROM [VPIC1].[dbo].[ACTXX]",
        _bundle(),
    )
    assert ok is False
    assert code == "UNKNOWN_TABLE"


def test_validate_metadata_references_rejects_unbracketed_table_reference():
    ok, code = validate_metadata_references(
        "SELECT MK002 FROM ACTMK",
        _bundle(),
    )
    assert ok is False
    assert code == "TABLE_REFERENCE_FORMAT_INVALID"


def test_validate_metadata_references_rejects_unknown_column():
    ok, code = validate_metadata_references(
        "SELECT [MK999] FROM [VPIC1].[dbo].[ACTMK]",
        _bundle(),
    )
    assert ok is False
    assert code == "UNKNOWN_COLUMN"


def test_validate_metadata_references_accepts_known_references():
    ok, code = validate_metadata_references(
        "SELECT [MK002],[MK006] FROM [VPIC1].[dbo].[ACTMK]",
        _bundle(),
    )
    assert ok is True
    assert code == "OK"


def test_validate_metadata_references_rejects_qualified_cross_table_column_mismatch():
    ok, code = validate_metadata_references(
        "SELECT [ACTMK].[MJ002] FROM [VPIC1].[dbo].[ACTMK] JOIN [VPIC1].[dbo].[ACTMJ] ON [ACTMK].[MK002] = [ACTMJ].[MJ002]",
        _bundle(),
    )
    assert ok is False
    assert code == "UNKNOWN_COLUMN_FOR_TABLE"
