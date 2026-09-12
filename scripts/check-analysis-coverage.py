#!/usr/bin/env python3
"""Report-flow regression: quality metadata is not permission to compose.

Uses real local RPC/artifact/resolve code and synthetic figures. No live ML or
Grafana writes. Replaces assertions for the retired host quality workflow.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    fixtures = runpy.run_path(str(ROOT / 'scripts/check-plotly-native-delivery.py'))
    owner = {'org_id': '1', 'user_id': 'flow-check', 'session_id': 'flow-session'}
    with tempfile.TemporaryDirectory(prefix='report-flow-') as tmp:
        os.environ['ANALYSIS_ARTIFACT_ROOT'] = tmp
        spec = importlib.util.spec_from_file_location('report_flow_bridge', ROOT / 'artifact-bridge-mcp/server.py')
        assert spec and spec.loader
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)

        def call(name, args, actor=owner):
            reply = bridge.handle_rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': {**args, '_server_context': actor}}})
            try:
                return json.loads(reply['result']['content'][0]['text'])
            except (ValueError, KeyError, TypeError) as exc:
                raise AssertionError('invalid tool response') from exc

        def no_quality_verdict(*args):
            raise AssertionError('host analytical assessment must not gate reporting')

        setattr(bridge, '_analysis_coverage', no_quality_verdict)
        run = bridge.ARTIFACTS.create_run(owner)
        results = fixtures['make_results']([{'data': [{'type': 'bar', 'x': ['a'], 'y': [4]}]}])
        execution = bridge.ARTIFACTS.write_json(owner, run, 'sandbox-execution', {'error': None, 'results': results})
        manifest = bridge.ml_report_contract.normalize_report_manifest(execution_ref=execution, results=results)
        ref = bridge.ARTIFACTS.write_json(owner, run, 'report-manifest', manifest)
        # Missing objective/baseline/business-question metadata is no longer a workflow failure.
        artifact = manifest['artifacts'][0]['artifact_id']
        synthesis = {'format': 'ask-o11y-report-synthesis-v1', 'report_title': '4 observations', 'thesis': 'Observed value: 4; exploratory result.', 'sections': [{'section_id': 'result', 'title': 'Result', 'purpose': 'Describe observations', 'panels': [{'artifact_id': artifact, 'view_ids': ['figure'], 'headline': 'Value 4'}]}]}
        args = {'report_manifest_ref': ref, 'synthesis': synthesis, 'uid': 'flow-report', 'title': 'Report'}
        before = {p: p.read_bytes() for p in Path(tmp).rglob('*.json')}
        composed = call('compose_ml_dashboard', args)
        assert composed['ok'], composed
        assert not list(Path(tmp).rglob('report-inspection.json')), 'composition secretly requires inspection'
        resolved = call('resolve_dashboard_refs', {'dashboard': {'$dashboard_ref': composed['refs']['dashboard_ref']}})
        assert resolved['ok'], resolved
        assert 'Automated evidence assessment' not in json.dumps(resolved['dashboard'])
        # The compositor is optional, including for a report-tagged dashboard.
        manual = {'uid': 'manual-report', 'tags': ['ask-o11y-report'], 'panels': [{'id': 1, 'type': 'text', 'options': {'mode': 'markdown', 'content': 'Observed 4 values.'}}]}
        assert call('resolve_dashboard_refs', {'dashboard': manual})['ok']
        for actor in ({**owner, 'user_id': 'other'}, {**owner, 'org_id': 'other'}, {**owner, 'session_id': 'other'}):
            assert not call('compose_ml_dashboard', args, actor)['ok']
        bad = copy.deepcopy(args)
        bad['synthesis']['thesis_evidence'] = [{'fact_ref': 'facts.invented', 'format': 'integer'}]
        assert not call('compose_ml_dashboard', bad)['ok'], 'supplied citations must exist'
        bad = copy.deepcopy(args)
        bad['synthesis']['thesis'] = '<script>alert(1)</script>'
        assert not call('compose_ml_dashboard', bad)['ok']
        # Inspection is an optional reader; reaching EOF is not an error.
        inspected = call('inspect_report_artifacts', {'report_manifest_ref': ref})
        assert inspected['ok'], inspected
        again = call('inspect_report_artifacts', {'inspection_ref': inspected['refs']['inspection_ref']})
        assert again['ok'] and again['evidence']['remaining_artifact_count'] == 0, again
        assert all(p.read_bytes() == data for p, data in before.items())
        # Only corrupt temporary fixture bytes, never retained production receipts.
        path = bridge.ARTIFACTS.root / run / 'report-manifest.json'
        original = path.read_bytes()
        try:
            corrupt = copy.deepcopy(manifest)
            corrupt['artifacts'][0]['render']['sha256'] = '0' * 64
            path.write_text(json.dumps(corrupt))
            assert not call('compose_ml_dashboard', args)['ok'], 'artifact integrity was lost'
        finally:
            path.write_bytes(original)
    print('PASS: direct report composition, no quality/inspection/citation gates; ownership, safe text, supplied references and artifact integrity retained')


if __name__ == '__main__':
    main()
