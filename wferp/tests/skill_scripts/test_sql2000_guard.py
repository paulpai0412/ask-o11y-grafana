from skill_scripts.sql2000_guard import validate_sql


def test_rejects_non_select_sql():
    ok, code = validate_sql("DELETE FROM ACPTA")
    assert ok is False
    assert code == "NON_SELECT_INTENT"


def test_rejects_exec_and_update_tokens_case_insensitive():
    ok, code = validate_sql("select * from ACPTA; EXEC xp_cmdshell 'x'")
    assert ok is False
    assert code == "NON_SELECT_INTENT"


def test_rejects_cte_and_windowing_and_set_ops_for_sql2000():
    bad_sql = [
        "WITH x AS (SELECT 1 AS A) SELECT * FROM x",
        "SELECT ROW_NUMBER() OVER(PARTITION BY TA001 ORDER BY TA002) FROM ACPTA",
        "SELECT * FROM A OFFSET 10 ROWS FETCH NEXT 10 ROWS ONLY",
        "SELECT * FROM A EXCEPT SELECT * FROM B",
        "SELECT * FROM A INTERSECT SELECT * FROM B",
        "SELECT CONCAT([MK002], [MK006]) FROM [ACTMK]",
    ]
    for sql in bad_sql:
        ok, code = validate_sql(sql)
        assert ok is False
        assert code == "UNSUPPORTED_SQL2000_FEATURE"


def test_rejects_multi_statement_batches():
    ok, code = validate_sql("SELECT * FROM ACPTA; SELECT * FROM ACPTB")
    assert ok is False
    assert code == "MULTI_STATEMENT_NOT_ALLOWED"


def test_accepts_single_select_top_syntax():
    ok, code = validate_sql("SELECT TOP 20 [TA001] FROM [ACPTA]")
    assert ok is True
    assert code == "OK"
