"""Offline regression tests; real Ask O11y acceptance is performed separately."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
TEMP = tempfile.TemporaryDirectory()
ENV = patch.dict(os.environ, {"ANALYSIS_ARTIFACT_ROOT": TEMP.name + "/artifacts",
                              "UPLOAD_DATASET_ROOT": TEMP.name + "/uploads"})
ENV.start()
spec = importlib.util.spec_from_file_location("query_server_test", HERE / "server.py")
assert spec is not None and spec.loader is not None
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
mssql = server.mssql
TABLES = [{"schema": "reporting", "name": "v_sales_order_line"}]
CONTEXT = {"org_id": "1", "user_id": "test", "session_id": "test-session"}
DATASET = {"id": "sales", "title": "Sales", "query_kind": "mssql", "database": "SalesDatamart",
           "datasource_uid": "sales-uid", "datasource_type": "mssql", "org_id": "1", "schemas": ["reporting"]}
LIVE = {"uid": "sales-uid", "type": "mssql", "orgId": 1, "jsonData": {"database": "SalesDatamart"}}


def response(fields, rows):
    return {"results": {"A": {"status": 200, "frames": [{"schema": {"fields": [{"name": n} for n in fields]},
                                                       "data": {"values": [list(v) for v in zip(*rows)] if rows else [[] for _ in fields]}}]}}}


SCHEMA = response(["TABLE_SCHEMA", "TABLE_NAME", "COLUMN_NAME", "DATA_TYPE", "IS_NULLABLE", "ORDINAL_POSITION"],
                  [["reporting", "v_sales_order_line", "ProductCategoryName", "nvarchar", "YES", 1],
                   ["reporting", "v_sales_order_line", "LineNetAmount", "decimal", "NO", 2]])
SQL = "SELECT ProductCategoryName, SUM(LineNetAmount) AS amount FROM reporting.v_sales_order_line GROUP BY ProductCategoryName ORDER BY amount DESC"


class SelectTests(unittest.TestCase):
    def test_category_aggregation_and_cte_are_allowed(self):
        for sql in [SQL, "WITH s AS (" + SQL.split(" ORDER BY")[0] + ") SELECT * FROM s",
                    "SELECT DATEFROMPARTS(YEAR(OrderDate),MONTH(OrderDate),1),COUNT(DISTINCT SalesOrderID) FROM reporting.v_sales_order_line GROUP BY YEAR(OrderDate),MONTH(OrderDate)"]:
            with self.subTest(sql=sql):
                self.assertIn("TOP 101", mssql.bounded_select(sql, TABLES, 100))

    def test_smaller_limit_preserved_overflow_detectable(self):
        self.assertIn("TOP 2", mssql.bounded_select("SELECT TOP 2 * FROM reporting.v_sales_order_line", TABLES, 100))
        self.assertIn("TOP 101", mssql.bounded_select("SELECT TOP 900 * FROM reporting.v_sales_order_line", TABLES, 100))

    def test_effects_unknown_sources_and_external_access_rejected(self):
        cases = ["DELETE FROM reporting.v_sales_order_line", SQL + "; DROP TABLE reporting.x",
                 "SELECT * INTO reporting.x FROM reporting.v_sales_order_line",
                 "SELECT * FROM other.v_sales_order_line", "SELECT * FROM reporting.unknown",
                 "SELECT * FROM otherdb.reporting.v_sales_order_line", "SELECT * FROM server.db.reporting.v_sales_order_line",
                 "SELECT * FROM v_sales_order_line", "EXEC sp_help", "SELECT @x=LineNetAmount FROM reporting.v_sales_order_line",
                 "SELECT dbo.side_effect(LineNetAmount) FROM reporting.v_sales_order_line",
                 "SELECT * FROM OPENROWSET('x','y','z')", "SELECT * FROM OPENQUERY(linked,'SELECT 1')",
                 "SELECT * FROM reporting.v_sales_order_line WITH (UPDLOCK)",
                 "SELECT TOP 1 PERCENT * FROM reporting.v_sales_order_line", "SELECT TOP 1 WITH TIES * FROM reporting.v_sales_order_line ORDER BY LineNetAmount",
                 "WITH r AS (SELECT * FROM reporting.v_sales_order_line UNION ALL SELECT * FROM r) SELECT * FROM r",
                 "SELECT * FROM sys.tables", "SELECT * FROM INFORMATION_SCHEMA.COLUMNS",
                 "SELECT * FROM reporting.v_sales_order_line WHERE $__timeFilter(OrderDate)",
                 "SELECT NEXT VALUE FOR reporting.sequence FROM reporting.v_sales_order_line"]
        for sql in cases:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                mssql.bounded_select(sql, TABLES, 100)

    def test_schema_name_is_quoted_as_literal(self):
        self.assertIn("'x''; DROP TABLE t;--'", mssql.schema_sql(["x'; DROP TABLE t;--"]))


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = patch.object(server, "configured_datasets", return_value=[DATASET])
        self.get = patch.object(server, "get_grafana", return_value=LIVE)
        self.catalog.start()
        self.get.start()
        self.addCleanup(self.catalog.stop)
        self.addCleanup(self.get.stop)

    def test_inspection_and_category_query_use_grafana(self):
        result = response(["ProductCategoryName", "amount"], [["Bikes", 12.5], ["Clothing", 2]])
        with patch.object(server, "post_grafana", side_effect=[SCHEMA, result]) as post:
            out = server.tool_query_dataset({"dataset_id": "sales", "sql": SQL, "_server_context": CONTEXT, "_server_session_id": CONTEXT["session_id"]})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["row_count"], 2)
        self.assertEqual(out["result_preview"]["rows"], [["Bikes", 12.5], ["Clothing", 2]])
        self.assertEqual(out["evidence"]["executed_by"], "Grafana /api/ds/query")
        self.assertEqual(len(post.call_args_list), 2)
        for call in post.call_args_list:
            self.assertEqual(call.args[0], "/api/ds/query")
            self.assertEqual(call.args[1]["queries"][0]["datasource"]["uid"], "sales-uid")
        self.assertIn("TOP 100001", post.call_args_list[1].args[1]["queries"][0]["rawSql"])
        frames = server.ARTIFACTS.read_json(CONTEXT, out["frame_ref"])
        self.assertEqual(frames[0]["data"]["values"], [["Bikes", "Clothing"], [12.5, 2]])

    def test_wrong_org_database_and_unknown_dataset_rejected_before_query(self):
        for args, live in [({"dataset_id": "sales", "_server_context": {**CONTEXT, "org_id": "2"}}, LIVE),
                           ({"dataset_id": "sales", "_server_context": CONTEXT}, {**LIVE, "jsonData": {"database": "other"}}),
                           ({"dataset_id": "unknown", "_server_context": CONTEXT}, LIVE)]:
            with self.subTest(args=args), patch.object(server, "get_grafana", return_value=live), patch.object(server, "post_grafana") as post:
                out = server.tool_inspect_dataset(args)
                self.assertFalse(out["ok"])
                post.assert_not_called()

    def test_sql_write_rejected_before_business_query(self):
        with patch.object(server, "post_grafana", return_value=SCHEMA) as post:
            out = server.tool_query_dataset({"dataset_id": "sales", "sql": "DELETE FROM reporting.v_sales_order_line", "_server_context": CONTEXT, "_server_session_id": CONTEXT["session_id"]})
        self.assertFalse(out["ok"])
        self.assertEqual(post.call_count, 1)  # metadata only

    def test_no_authenticated_session_cannot_query(self):
        with patch.object(server, "post_grafana") as post:
            out = server.tool_query_dataset({"dataset_id": "sales", "sql": SQL, "_server_context": {"org_id": "1", "user_id": "test"}})
        self.assertFalse(out["ok"])
        post.assert_not_called()

    def test_empty_and_failed_schema_not_fabricated(self):
        for value in [response(["TABLE_NAME"], []), {"results": {"A": {"status": 500, "error": "connection failed"}}}]:
            with patch.object(server, "post_grafana", return_value=value):
                self.assertFalse(server.tool_inspect_dataset({"dataset_id": "sales", "_server_context": CONTEXT})["ok"])

    def test_overflow_frame_not_saved(self):
        oversized = response(["amount"], [[1]] * (server.MAX_RESULT_ROWS + 1))
        with patch.object(server, "post_grafana", side_effect=[SCHEMA, oversized]) as post:
            out = server.tool_query_dataset({"dataset_id": "sales", "sql": SQL, "_server_context": CONTEXT, "_server_session_id": CONTEXT["session_id"]})
        self.assertFalse(out["ok"])
        self.assertEqual(post.call_count, 2)
        self.assertNotIn("frame_ref", out)

    def test_discovery_scopes_mssql_to_authorized_org(self):
        with patch.object(server, "get_grafana", return_value=[LIVE]):
            self.assertEqual(server.tool_discover_datasets({"_server_context": CONTEXT})["datasets"][0]["dataset_id"], "sales")
            self.assertEqual(server.tool_discover_datasets({"_server_context": {**CONTEXT, "org_id": "2"}})["datasets"], [])

    def test_upload_inspection_preserved_after_sql_branch_removal(self):
        upload = {"filename": "data.csv", "fields": [{"name": "value", "type": "number"}], "session_id": "test-session", "rows": 1}
        with patch.object(server, "get_grafana", return_value={"type": "yesoreyeram-infinity-datasource"}), patch.object(server.uploaded_datasets, "inspect_upload", return_value=upload), patch.object(server.uploaded_datasets, "sign_csv_url", return_value="http://local/signed"):
            out = server.tool_inspect_dataset({"dataset_id": "upload_test", "_server_context": CONTEXT})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["metadata"]["query_kind"], "uploaded_csv")

    def test_retired_tool_not_advertised(self):
        tools = server.handle_rpc({"id": 1, "method": "tools/list"})["result"]["tools"]
        self.assertNotIn("wferp", json.dumps(tools).lower())
        self.assertEqual({t["name"] for t in tools}, {"discover_datasets", "inspect_dataset", "query_dataset"})


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        ENV.stop()
        TEMP.cleanup()
