from skill_scripts.relationship_graph import build_primary_key_map, infer_relationships


def test_build_primary_key_map_only_primary_rows():
    index_keys = [
        {"TableName": "ACPTA", "IndexColumnName": "TA001+TA002", "isPrimaryKey": "1"},
        {"TableName": "ACPTA", "IndexColumnName": "TA003", "isPrimaryKey": "0"},
        {"TableName": "ACPTB", "IndexColumnName": "TB001+TB002+TB003", "isPrimaryKey": "1"},
    ]
    pk_map = build_primary_key_map(index_keys)
    assert pk_map == {"ACPTA": ["TA001", "TA002"], "ACPTB": ["TB001", "TB002", "TB003"]}


def test_infer_relationships_header_detail_medium_confidence():
    index_keys = [
        {"TableName": "ACPTA", "IndexColumnName": "TA001+TA002", "isPrimaryKey": "1"},
        {"TableName": "ACPTB", "IndexColumnName": "TB001+TB002+TB003", "isPrimaryKey": "1"},
    ]
    edges = infer_relationships([], index_keys)
    assert edges
    edge = edges[0]
    assert edge["from_table"] == "ACPTA"
    assert edge["to_table"] == "ACPTB"
    assert edge["from_columns"] == ["TA001", "TA002"]
    assert edge["to_columns"] == ["TB001", "TB002"]
    assert edge["confidence"] == "medium"
    assert edge["cardinality"] == "1:N"
    assert "prefix" in edge["reason"].lower()


def test_infer_relationships_prefix_only_never_high_confidence():
    index_keys = [
        {"TableName": "ABCZZ", "IndexColumnName": "ZZ001", "isPrimaryKey": "1"},
        {"TableName": "ABCAA", "IndexColumnName": "AA001", "isPrimaryKey": "1"},
    ]
    edges = infer_relationships([], index_keys)
    assert edges
    assert all(edge["confidence"] != "high" for edge in edges)


def test_infer_relationships_returns_confidence_cardinality_domain():
    index_keys = [
        {"TableName": "AAA", "IndexColumnName": "AA001", "isPrimaryKey": "1"},
        {"TableName": "AAB", "IndexColumnName": "AB001", "isPrimaryKey": "1"},
    ]
    edges = infer_relationships([], index_keys)
    for edge in edges:
        assert edge["confidence"] in {"high", "medium", "low"}
        assert edge["cardinality"] in {"1:1", "1:N", "N:N", "unknown"}
