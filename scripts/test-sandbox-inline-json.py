"""Regression for captured JSON -> inline summary -> durable completion (no compute)."""
import ast
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from artifact_store import ArtifactStore

# Importing server.py cleans the live artifact store. Extract only the pure
# summary function and its size constant; never initialize a server in this test.
tree = ast.parse((ROOT / 'sandbox-analysis-mcp/server.py').read_text())
nodes: list[ast.stmt] = [node for node in tree.body if
         isinstance(node, ast.FunctionDef) and node.name == 'inline_result_summary' or
         isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'MAX_INLINE_RESULT_BYTES' for t in node.targets)]
namespace = {'json': json, 'Any': object}
exec(compile(ast.Module(body=nodes, type_ignores=[]), 'inline-summary-source', 'exec'), namespace)
inline_result_summary = namespace['inline_result_summary']
EVIDENCE = Path(sys.argv.pop(1)) if len(sys.argv) > 1 and not sys.argv[1].startswith('-') else None


class InlineJSONTests(unittest.TestCase):
    def test_nonfinite_missing_values_and_literal_text(self):
        execution = {'results': [{'display_name': 'summary.json', 'mime': {
            'application/json': '{"std":NaN,"nested":[Infinity,-Infinity,null,1.5],"label":"NaN"}'}}]}
        original = copy.deepcopy(execution)
        inline, omitted = inline_result_summary(execution)
        self.assertFalse(omitted)
        self.assertEqual(inline[0]['value'], {'std': None, 'nested': [None, None, None, 1.5], 'label': 'NaN'})
        json.dumps(inline, allow_nan=False)
        self.assertEqual(execution, original)

    def test_normal_results_unchanged(self):
        execution = {'results': [{'mime': {'application/json': '{"count":1,"mean":12.5,"std":null}'}},
                                 {'text': 'NaN is unavailable'}]}
        inline, omitted = inline_result_summary(execution)
        self.assertFalse(omitted)
        self.assertEqual(inline[0]['value'], {'count': 1, 'mean': 12.5, 'std': None})
        self.assertEqual(inline[1]['value'], 'NaN is unavailable')

    def test_invalid_and_oversized_results_remain_omitted(self):
        inline, omitted = inline_result_summary({'results': [
            {'mime': {'application/json': '{broken'}},
            {'text': 'x' * (namespace['MAX_INLINE_RESULT_BYTES'] + 1)}]})
        self.assertTrue(omitted)
        self.assertEqual(inline, [])

    @unittest.skipUnless(EVIDENCE, 'supply an existing sandbox-execution.json to check the reported failure')
    def test_existing_execution_can_finish_without_recomputing(self):
        assert EVIDENCE is not None
        original = EVIDENCE.read_bytes()
        try:
            execution = json.loads(original)
        except json.JSONDecodeError as exc:
            self.fail(f'Existing execution evidence is invalid: {exc}')
        self.assertIsNone(execution['error'])
        self.assertIsNotNone(execution['complete'])
        inline, omitted = inline_result_summary(execution)
        self.assertFalse(omitted)
        summary = inline[0]['value']
        self.assertIsNone(summary['countries'][6]['std'])
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(directory)
            context = {'org_id': '1', 'user_id': 'test', 'session_id': 'inline-json-regression'}

            def persist():
                calls.append('persist')
                run = store.create_run(context)
                execution_ref = store.write_json(context, run, 'sandbox-execution', execution)
                provenance_ref = store.write_json(context, run, 'sandbox-provenance', {'output_summary': inline})
                return {'ok': True, 'refs': {'execution_ref': execution_ref, 'provenance_ref': provenance_ref}}

            result = store.run_once(context, 'persist-captured-output', {'source': 'existing-evidence'}, persist)
            receipt = store.reconcile_operation(context, result['evidence']['operation_id'])
            self.assertEqual(receipt['status'], 'completed')
            self.assertFalse(receipt['redispatch_allowed'])
            self.assertEqual(store.run_once(context, 'persist-captured-output', {'source': 'existing-evidence'}, persist), result)
            self.assertEqual(calls, ['persist'])
        self.assertEqual(EVIDENCE.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
