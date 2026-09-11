#!/usr/bin/env python3
"""Scientific text through the real sanitizer; synthetic data only."""
import copy
import json
import os
from pathlib import Path
import runpy
import tempfile

ROOT = Path(__file__).resolve().parents[1]
fixtures = runpy.run_path(str(ROOT / 'scripts/check-ml-plotly-contract.py'))
contract = fixtures['load_module']()
labels = ['p < 0.05', 'Temperature > 100', 'a < b', '1 < x < 3', 'x <= 2', 'x ≥ 0', 'a<b', 'a&lt;b', 'A & B', '&lt;b&gt;literal&lt;/b&gt;']
figure = {'data': [{'type': 'bar', 'x': labels, 'y': [0.42] * len(labels), 'name': 'p < 0.05'}], 'layout': {'title': 'Temperature > 100', 'xaxis': {'title': 'a < b', 'tickvals': labels, 'ticktext': labels}, 'annotations': [{'text': '1 < x < 3', 'x': 0, 'y': 1, 'showarrow': False}]}}
original = copy.deepcopy(figure)
clean = contract.sanitize_figure(figure)
assert figure == original and clean['data'][0]['x'] == labels
assert contract.sanitize_figure(clean) == clean
for text in ['<b>x</b>', '<script>alert(1)</script>', '<svg/onload=alert(1)>', '<a href="https://evil.invalid">x</a>', '<span style="background:url(https://evil.invalid)">x</span>', '<!--x-->', '<?x?>', '<!DOCTYPE html>', '<![bogus]>', '<![CDATA[x]]>', '</text><image href="x">', 'javascript:alert(1)', 'https://evil.invalid']:
    for changed in [{'data': [{'type': 'bar', 'x': [text], 'y': [1]}]}, {'data': [{'type': 'bar', 'y': [1]}], 'layout': {'title': text}}]:
        fixtures['expect_reject'](contract, changed, 'forbidden character')
fixtures['expect_reject'](contract, {'data': [{'type': 'bar', 'x': ['x' * 201], 'y': [1]}]}, 'string length')
assert (ROOT / 'ml_plotly_contract.py').read_bytes() == (ROOT / 'sandbox-analysis-mcp/ml_plotly_contract.py').read_bytes()
# Use the public report cursor with real isolated store/normalization/compose/resolve.
cursor = runpy.run_path(str(ROOT / 'scripts/check-report-evidence-cursor.py'))
bridge = cursor['fixtures']['load_bridge']()
owner = cursor['OWNER']
with tempfile.TemporaryDirectory(prefix='plotly-labels-') as tmp:
    bridge.ARTIFACTS = bridge.ArtifactStore(Path(tmp))
    run = bridge.ARTIFACTS.create_run(owner)
    source = {'format': 'ask-o11y-report-source-v1', 'purpose': 'Inspect literal labels', 'conclusion': 'Synthetic text fixture', 'facts': {'signal': {'kind': 'number', 'label': 'Signal', 'value': 0.42}}, 'artifacts': [{'artifact_id': 'chart0', 'fact_refs': ['signal'], 'plotly_output_name': 'chart0.json', 'png_output_name': 'preview.png'}]}
    results = [{'display_name': 'report-source.json', 'mime': {'application/json': json.dumps(source)}}, {'display_name': 'chart0.json', 'mime': {'application/vnd.plotly.v1+json': json.dumps(clean)}}, {'display_name': 'preview.png', 'mime': {'image/png': cursor['PNG']}}]
    execution = bridge.ARTIFACTS.write_json(owner, run, 'sandbox-execution', {'results': results, 'error': None})
    manifest = bridge.ARTIFACTS.write_json(owner, run, 'report-manifest', bridge.ml_report_contract.normalize_report_manifest(execution_ref=execution, results=results, manifest_format=bridge.ml_report_contract.LEGACY_REPORT_MANIFEST_FORMAT))
    bridge.ARTIFACTS.write_json(owner, run, 'sandbox-provenance', {'executor_kind': 'profile_dataset', 'trusted_ml_contract': False, 'report_manifest_ref': manifest})
    inspected, _ = cursor['call'](bridge, 'inspect_report_artifacts', {'report_manifest_ref': manifest})
    assert inspected['ok'] and inspected['inspection']['artifacts'][0]['figure'] == clean, inspected
    composed, _ = cursor['call'](bridge, 'compose_ml_dashboard', {'inspection_ref': inspected['refs']['inspection_ref'], 'synthesis': cursor['synthesis'](), 'uid': 'labels-check', 'title': 'Label check'})
    assert composed['ok'], composed
    resolved = bridge.resolve_dashboard_refs({'dashboard': {'$dashboard_ref': composed['refs']['dashboard_ref']}, '_server_context': owner})
    assert resolved['ok'] and 'p < 0.05' in json.dumps(resolved['dashboard']), resolved
if output := os.environ.get('PLOTLY_LABEL_FIXTURE_OUT'):
    Path(output).write_text(json.dumps(clean, ensure_ascii=False))
print('PASS scientific labels, unchanged categories/input, idempotency, markup/scheme rejection and matching Sandbox copy')
