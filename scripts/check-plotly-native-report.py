#!/usr/bin/env python3
"""Real local Store/RPC/compose/resolve; synthetic figures, no remote executor."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
fixtures = runpy.run_path(str(ROOT / 'scripts/check-plotly-native-delivery.py'))


def main():
    owner = {'org_id': '1', 'user_id': 'native-check', 'session_id': 'native-session'}
    with tempfile.TemporaryDirectory(prefix='native-report-') as tmp:
        os.environ['ANALYSIS_ARTIFACT_ROOT'] = tmp
        spec = importlib.util.spec_from_file_location('native_report_bridge', ROOT / 'artifact-bridge-mcp/server.py')
        assert spec and spec.loader
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)

        def call(name, args, actor=owner):
            response = bridge.handle_rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': {**args, '_server_context': actor}}})
            value = fixtures['parse_json'](response['result']['content'][0]['text'])
            assert response['result'].get('isError', False) == (not value['ok'])
            return value

        def seed(results, legacy=False):
            run = bridge.ARTIFACTS.create_run(owner)
            execution = bridge.ARTIFACTS.write_json(owner, run, 'sandbox-execution', {'error': None, 'results': results})
            report = bridge.ml_report_contract
            manifest = report.normalize_report_manifest(execution_ref=execution, results=results, manifest_format=report.LEGACY_REPORT_MANIFEST_FORMAT if legacy else report.REPORT_MANIFEST_FORMAT)
            ref = bridge.ARTIFACTS.write_json(owner, run, 'report-manifest', manifest)
            bridge.ARTIFACTS.write_json(owner, run, 'sandbox-provenance', {'executor_kind': 'execute_python_analysis', 'computation_status': 'succeeded', 'report_status': 'partial' if report.presentation_errors(manifest) else 'accepted', 'trusted_ml_contract': False, 'report_manifest_ref': ref, 'business_question': manifest['purpose']})
            return ref

        figures = fixtures['produced_figures']()
        results = fixtures['make_results'](figures + [{'data': [{'type': 'invalid_trace'}]}])
        ref = seed(results)
        retained = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(tmp).rglob('*.json')}
        inspected = call('inspect_report_artifacts', {'report_manifest_ref': ref})
        assert inspected['ok'], inspected
        assert len(inspected['evidence']['presentation_errors']) == 1
        assert inspected['report_context']['facts']['facts.count']['value'] == 4
        items = inspected['inspection']['artifacts']
        assert len(items) == 6 and items[-1]['presentation_error'] == 'invalid_native_figure'
        assert all(item['figure_spec']['views'][0]['view_id'] == 'figure' for item in items)
        assert items[3]['figure']['layout']['annotations'] == figures[3]['layout']['annotations']
        evidence = [{'fact_ref': 'facts.count', 'format': 'integer'}]
        panels = [{'artifact_id': item['artifact_id'], 'view_ids': ['figure'], 'headline': 'Retained observations', 'observation': 'The supplied observations remain available.', 'interpretation': 'Read the evidence and limitations together.', 'limitation': 'Exploratory, not causal.', 'evidence': evidence} for item in items]
        synthesis = {'format': 'ask-o11y-report-synthesis-v1', 'report_title': 'Native report', 'thesis': 'Evidence remains available despite a failed figure.', 'thesis_evidence': evidence, 'sections': [{'section_id': 'observations', 'title': 'Observations', 'purpose': 'Explain retained observations', 'panels': panels}]}
        args = {'inspection_ref': inspected['refs']['inspection_ref'], 'synthesis': synthesis, 'uid': 'native-check', 'title': 'Native report'}
        saved_args = copy.deepcopy(args)
        composed = call('compose_ml_dashboard', args)
        assert composed['ok'] and composed['delivery_status'] == 'partial', composed
        assert args == saved_args
        resolved = call('resolve_dashboard_refs', {'dashboard': {'$dashboard_ref': composed['refs']['dashboard_ref']}})
        assert resolved['ok'], resolved
        dashboard = resolved['dashboard']
        charts = [p for p in dashboard['panels'] if p.get('type') == 'asko11y-plotly-panel']
        assert len(charts) == 6 and dashboard['askO11yDeliveryStatus'] == 'partial'
        assert charts[-1]['options']['figure']['error'] == 'invalid_native_figure'
        assert all(p['options']['figureFormat'] == 'ask-o11y-ml-plotly-v2' for p in charts)
        assert charts[3]['options']['figure']['layout']['annotations'] == figures[3]['layout']['annotations']
        assert charts[-1]['options']['narrative']['evidence'][0]['display'] == '4'
        # Ownership, fact citation, spec-only truthfulness and immutable receipts.
        for actor in ({**owner, 'user_id': 'other'}, {**owner, 'session_id': 'other'}, {**owner, 'org_id': '2'}):
            assert not call('inspect_report_artifacts', {'report_manifest_ref': ref}, actor)['ok']
        invalid = copy.deepcopy(args)
        invalid['synthesis']['thesis_evidence'][0]['fact_ref'] = 'facts.invented'
        assert not call('compose_ml_dashboard', invalid)['ok']
        # Deliberate filesystem corruption in this temporary fixture only.
        # Even a locally displayed error must remain bound to its original payload.
        manifest_path = bridge.ARTIFACTS.root / bridge.parse_artifact_ref(ref)[0] / 'report-manifest.json'
        original_manifest = manifest_path.read_bytes()
        try:
            changed = fixtures['parse_json'](original_manifest)
            changed['artifacts'][-1]['render']['sha256'] = '0' * 64
            manifest_path.write_text(json.dumps(changed))
            assert not call('inspect_report_artifacts', {'report_manifest_ref': ref})['ok']
        finally:
            manifest_path.write_bytes(original_manifest)
        for path, digest in retained.items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
        # Generic exploratory reports can be displayed without claiming ML-contract verification.
        generic_ref = seed(fixtures['make_results'](figures))
        generic_before = {p: p.read_bytes() for p in Path(tmp).rglob('*.json')}
        generic = call('inspect_report_artifacts', {'report_manifest_ref': generic_ref})
        coverage = generic['report_context']['analysis_coverage']
        assert generic['ok'] and coverage['status'] == 'not_assessed'
        assert coverage['gaps'] == [], 'absence of an applicable assessment is not a known report gap'
        generic_synthesis = copy.deepcopy(synthesis)
        generic_synthesis['sections'][0]['panels'] = panels[:-1]
        generic_args = {**args, 'inspection_ref': generic['refs']['inspection_ref'], 'synthesis': generic_synthesis}
        generic_composed = call('compose_ml_dashboard', generic_args)
        assert generic_composed['ok'] and generic_composed['delivery_status'] == 'standard', generic_composed
        assert generic_composed['evidence']['analysis_coverage'] == coverage
        generic_resolved = call('resolve_dashboard_refs', {'dashboard': {'$dashboard_ref': generic_composed['refs']['dashboard_ref']}})
        assert generic_resolved['ok']
        assert len([p for p in generic_resolved['dashboard']['panels'] if p.get('type') == 'asko11y-plotly-panel']) == len(figures)
        assert 'not_assessed' in json.dumps(generic_resolved['dashboard'])
        assert generic_resolved['dashboard'].get('askO11yDeliveryStatus') != 'partial'
        notice = generic_resolved['dashboard']['panels'][0]['options']['content']
        assert 'not a finding of missing work or failure' in notice
        assert 'analysis is not complete' not in notice
        assert 'analysis is not complete' not in dashboard['panels'][0]['options']['content']
        if out := os.environ.get('ASSESSMENT_NOTICE_OUT'):
            Path(out).write_text(notice)
        assert all(p.read_bytes() == original for p, original in generic_before.items())
        if out := os.environ.get('Y5_DELIVERY_FIXTURE_OUT'):
            Path(out).write_text(json.dumps({'cases': [{
                'name': 'unassessed_standard_report', 'question': generic['report_context']['purpose'], 'status': 'not_assessed',
                'calls': [
                    {'name': 'artifact-bridge_inspect_report_artifacts', 'arguments': {'report_manifest_ref': generic_ref}, 'result': generic},
                    {'name': 'artifact-bridge_compose_ml_dashboard', 'arguments': generic_args, 'result': generic_composed},
                ],
            }], 'originals_unchanged': True, 'repair_added_compute_calls': 0}))
        # The unchanged v1 reader preserves its digest and view identity.
        old_results = fixtures['make_results']([{'data': [{'type': 'bar', 'x': ['a'], 'y': [1]}], 'layout': {'title': 'Legacy'}}])
        old_ref = seed(old_results, legacy=True)
        old = call('inspect_report_artifacts', {'report_manifest_ref': old_ref})
        assert old['ok'], old
        assert old['inspection']['artifacts'][0]['figure_spec']['views'][0]['view_id'] == 'view-1'
        if '--out' in sys.argv:
            Path(sys.argv[sys.argv.index('--out') + 1]).write_text(json.dumps(dashboard, ensure_ascii=False))
    print('PASS: real RPC/store/inspect/compose/resolve, partial figure not lost, whole figure, retained facts, identity negatives, legacy v1 and immutable receipts')


if __name__ == '__main__':
    main()
