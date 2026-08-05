from skill_scripts.data_dictionary import build_alias_index, build_field_index


def test_build_field_index_maps_field_id_to_table_field_list():
    fields = [
        {"TableID": "ACPTA", "ID": "TA001", "FieldName": "憑單單號", "NameVietnam": "Số chứng từ"},
        {"TableID": "PURTA", "ID": "TA001", "FieldName": "採購單號", "NameVietnam": "Số đơn mua"},
        {"TableID": "ACPTA", "ID": "TA002", "FieldName": "憑單日期", "NameVietnam": "Ngày chứng từ"},
    ]

    index = build_field_index(fields)
    assert index["TA001"] == ["ACPTA.TA001", "PURTA.TA001"]
    assert index["TA002"] == ["ACPTA.TA002"]


def test_build_alias_index_includes_lower_cased_id_fieldname_nameviet():
    fields = [
        {"TableID": "ACPTA", "ID": "TA001", "FieldName": "採購單號", "NameVietnam": "So Don Mua"},
    ]

    alias = build_alias_index(fields)
    assert alias["ta001"] == ["ACPTA.TA001"]
    assert alias["採購單號"] == ["ACPTA.TA001"]
    assert alias["so don mua"] == ["ACPTA.TA001"]
