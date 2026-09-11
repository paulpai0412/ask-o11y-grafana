#!/usr/bin/env python3
"""Public RPC regression for single-ref report reading; all artifacts are synthetic."""
import copy
import json
import os
from pathlib import Path
import runpy
import tempfile

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
fixtures = runpy.run_path(str(ROOT / 'scripts/check-artifact-bridge-report-synthesis.py'))
OWNER = {'org_id': '1', 'user_id': 'cursor-check', 'session_id': 'cursor-session'}
PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='


def call(bridge, name, args, owner=OWNER):
    response = bridge.handle_rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
        'name': name, 'arguments': {**args, '_server_context': owner},
    }})
    try:
        value = json.loads(response['result']['content'][0]['text'])
    except (KeyError, TypeError, ValueError) as exc:
        raise AssertionError('invalid actual RPC response') from exc
    assert response['result']['isError'] == (not value['ok'])
    schema = next(tool['inputSchema'] for tool in bridge.TOOLS if tool['name'] == name)
    Draft202012Validator.check_schema(schema)
    assert not value['ok'] or not list(Draft202012Validator(schema).iter_errors(args)), 'successful RPC input contradicts public schema'
    return value, response['result']


def seed(bridge, count):
    run = bridge.ARTIFACTS.create_run(OWNER)
    source = {'format': 'ask-o11y-report-source-v1', 'purpose': 'Compare observed signals', 'conclusion': 'Fixture facts',
              'facts': {'signal': {'kind': 'number', 'label': 'Signal', 'value': 0.42}},
              'artifacts': [{'artifact_id': f'chart{i}', 'fact_refs': ['signal'], 'plotly_output_name': f'chart{i}.json', 'png_output_name': f'preview{i}.png'} for i in range(count)]}
    figure = {'data': [{'type': 'bar', 'x': ['group'], 'y': [0.42]}], 'layout': {}}
    results = [{'display_name': 'report-source.json', 'mime': {'application/json': json.dumps(source)}}]
    results += [{'display_name': f'chart{i}.json', 'mime': {'application/vnd.plotly.v1+json': json.dumps(figure)}} for i in range(count)]
    results += [{'display_name': f'preview{i}.png', 'mime': {'image/png': PNG}} for i in range(count)]
    execution = bridge.ARTIFACTS.write_json(OWNER, run, 'sandbox-execution', {'results': results, 'error': None})
    manifest = bridge.ml_report_contract.normalize_report_manifest(execution_ref=execution, results=results, manifest_format=bridge.ml_report_contract.LEGACY_REPORT_MANIFEST_FORMAT)
    ref = bridge.ARTIFACTS.write_json(OWNER, run, 'report-manifest', manifest)
    bridge.ARTIFACTS.write_json(OWNER, run, 'sandbox-provenance', {'executor_kind': 'profile_dataset', 'trusted_ml_contract': False, 'report_manifest_ref': ref})
    return ref


def synthesis():
    report = fixtures['synthesis']()
    panel = report['sections'][0]['panels'][0]
    panel['artifact_id'] = 'chart0'
    panel['view_ids'] = ['view-1']
    panel['view_narratives'] = []
    return report


def main():
    bridge = fixtures['load_bridge']()
    with tempfile.TemporaryDirectory(prefix='report-cursor-') as tmp:
        bridge.ARTIFACTS = bridge.ArtifactStore(Path(tmp))
        manifest = seed(bridge, 9)
        initial_args = {'report_manifest_ref': manifest}
        first, wire = call(bridge, 'inspect_report_artifacts', initial_args)
        assert first['ok'], first
        assert first['report_context']['facts']['facts.signal']['value'] == 0.42
        assert len(first['inspection']['artifacts']) == 8 and first['evidence']['remaining_artifact_count'] == 1
        assert all(item['figure']['data'] for item in first['inspection']['artifacts'])
        assert [item['type'] for item in wire['content']] == ['text'], 'default spec must not imply vision'
        first_ref = first['refs']['inspection_ref']
        context_ref = first['refs']['report_context_ref']
        compose_args = {'inspection_ref': first_ref, 'synthesis': synthesis(), 'uid': 'cursor-report', 'title': 'Cursor report'}
        count = len(list(Path(tmp).iterdir()))
        incomplete, _ = call(bridge, 'compose_ml_dashboard', compose_args)
        assert not incomplete['ok'] and 'incomplete' in incomplete['error']
        assert incomplete['evidence']['repair_kind'] == 'report_inspection' and incomplete['evidence']['missing_artifact_ids'] == ['chart8']
        assert len(list(Path(tmp).iterdir())) == count, 'compose must not auto-inspect missing evidence'
        first_receipt = bridge.ARTIFACTS.read_json(OWNER, first_ref)
        context_before = bridge.ARTIFACTS.read_json(OWNER, context_ref)
        fresh, _ = call(bridge, 'prepare_ml_report', {'report_manifest_ref': manifest})
        assert fresh['ok'] and fresh['refs']['report_context_ref'] != context_ref, 'prepare overwrote prior context'
        assert bridge.ARTIFACTS.read_json(OWNER, context_ref) == context_before
        # The cursor must resume from stored evidence, not in-memory session state.
        bridge.ARTIFACTS = bridge.ArtifactStore(Path(tmp))
        continuation = {'inspection_ref': first_ref, 'mode': 'vision'}
        second, second_wire = call(bridge, 'inspect_report_artifacts', continuation)
        assert second['ok'], second
        assert second['evidence']['remaining_artifact_count'] == 0
        assert [a['artifact_id'] for a in second['inspection']['artifacts']] == ['chart8']
        assert [item['type'] for item in second_wire['content']] == ['text', 'image']
        assert second['refs']['previous_inspection_ref'] == first_ref
        assert bridge.ARTIFACTS.read_json(OWNER, first_ref) == first_receipt
        last_ref = second['refs']['inspection_ref']
        compose_args['inspection_ref'] = last_ref
        before = copy.deepcopy(compose_args)
        composed, _ = call(bridge, 'compose_ml_dashboard', compose_args)
        assert composed['ok'] and composed['refs']['inspection_ref'] == last_ref, composed
        assert composed['refs']['report_context_ref'] == context_ref
        assert compose_args == before and composed['evidence']['inspection_modes'] == ['spec', 'vision']
        resolved = bridge.resolve_dashboard_refs({'dashboard': {'$dashboard_ref': composed['refs']['dashboard_ref']}, '_server_context': OWNER})
        assert resolved['ok'] and '42.0%' in str(resolved['dashboard'])
        replay, _ = call(bridge, 'inspect_report_artifacts', {'inspection_ref': last_ref})
        assert not replay['ok'] and 'already complete' in replay['error']
        false_visual = synthesis()
        view = fixtures['synthesis']()['sections'][0]['panels'][0]['view_narratives'][0]
        false_visual['sections'][0]['panels'][0]['view_narratives'] = [view]
        rejected, _ = call(bridge, 'compose_ml_dashboard', {**compose_args, 'synthesis': false_visual})
        assert not rejected['ok'] and 'spec-only' in rejected['error'], rejected
        for actor in ({**OWNER, 'user_id': 'other'}, {**OWNER, 'session_id': 'other'}, {**OWNER, 'org_id': 'other'}):
            for tool, args in [('inspect_report_artifacts', {'inspection_ref': first_ref}), ('compose_ml_dashboard', compose_args)]:
                denied, _ = call(bridge, tool, args, actor)
                assert not denied['ok'] and not denied['recoverable'], denied
        for tool, args in [
            ('inspect_report_artifacts', {'report_manifest_ref': manifest, 'inspection_ref': first_ref}),
            ('inspect_report_artifacts', {'report_context_ref': context_ref, 'inspection_ref': first_ref}),
            ('compose_ml_dashboard', {**compose_args, 'report_context_ref': context_ref}),
            ('compose_ml_dashboard', {**compose_args, 'inspection_refs': [first_ref]}),
            ('compose_ml_dashboard', {**compose_args, 'inspection_ref': manifest}),
        ]:
            assert not call(bridge, tool, args)[0]['ok'], tool
        # Legacy multi-ref calls remain valid, without implicit expansion or new receipts.
        legacy_args = {**compose_args, 'report_context_ref': context_ref, 'inspection_refs': [first_ref, last_ref]}
        legacy_args.pop('inspection_ref')
        assert call(bridge, 'compose_ml_dashboard', legacy_args)[0]['ok']
        legacy_refs = []
        for ref in [first_ref, last_ref]:
            stored = bridge.ARTIFACTS.read_json(OWNER, ref)
            # Exact pre-S2b receipt fields, not an in-place migration of any receipt.
            old_shape = {key: stored[key] for key in ['report_context_ref', 'mode', 'coverage']}
            legacy_refs.append(bridge.ARTIFACTS.write_json(OWNER, bridge.ARTIFACTS.create_run(OWNER), 'report-inspection', old_shape))
        assert call(bridge, 'compose_ml_dashboard', {**legacy_args, 'inspection_refs': legacy_refs})[0]['ok']
        assert not call(bridge, 'compose_ml_dashboard', {**compose_args, 'inspection_ref': legacy_refs[-1]})[0]['ok']
        other, _ = call(bridge, 'inspect_report_artifacts', {'report_manifest_ref': seed(bridge, 1)})
        assert other['ok'] and other['evidence']['remaining_artifact_count'] == 0
        assert call(bridge, 'compose_ml_dashboard', {**compose_args, 'inspection_ref': other['refs']['inspection_ref']})[0]['ok']
        assert not call(bridge, 'compose_ml_dashboard', {**legacy_args, 'inspection_refs': [first_ref, other['refs']['inspection_ref']]})[0]['ok']
        # Deliberate corruption of scratch metadata: do not alter any real receipt.
        run, _ = bridge.parse_artifact_ref(last_ref)
        receipt_path = Path(tmp) / run / 'report-inspection.json'
        original = receipt_path.read_bytes()
        receipt = bridge.ARTIFACTS.read_json(OWNER, last_ref)
        for refs in ([last_ref], [first_ref] * 2, ['artifact://missing/report-inspection'], [other['refs']['inspection_ref']], [first_ref] * 8, 'not-a-list'):
            receipt_path.write_text(json.dumps({**receipt, 'prior_inspection_refs': refs}))
            invalid, _ = call(bridge, 'compose_ml_dashboard', compose_args)
            assert not invalid['ok'] and not invalid['recoverable'] and invalid['evidence']['repair_kind'] == 'report_identity', invalid
        receipt_path.write_bytes(original)
        context_run, _ = bridge.parse_artifact_ref(context_ref)
        context_path = Path(tmp) / context_run / 'report-context-manifest.json'
        original_context = context_path.read_bytes()
        context_path.write_text(json.dumps({**context_before, 'facts': {}}))
        for tool, args in [('compose_ml_dashboard', compose_args), ('inspect_report_artifacts', {'inspection_ref': first_ref})]:
            stale, _ = call(bridge, tool, args)
            assert not stale['ok'] and not stale['recoverable'] and stale['evidence']['repair_kind'] == 'report_identity', stale
        context_path.write_bytes(original_context)
        assert call(bridge, 'compose_ml_dashboard', compose_args)[0]['ok']
        if out := os.environ.get('REPORT_CURSOR_FIXTURE_OUT'):
            Path(out).write_text(json.dumps({'tools': [tool for tool in bridge.TOOLS if tool['name'] in {'inspect_report_artifacts', 'compose_ml_dashboard'}], 'initial_args': initial_args, 'initial': first, 'continuation_args': continuation, 'continuation': second, 'compose_args': compose_args, 'composed': composed}, ensure_ascii=False))
        print('PASS: actual manifest→bounded RPC evidence batches→single-ref composition; legacy, ownership, corruption, spec/vision, no hidden inspection')


if __name__ == '__main__':
    main()
