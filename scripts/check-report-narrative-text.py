#!/usr/bin/env python3
"""Public report narrative path with synthetic data and unchanged evidence rules."""
import copy
import json
import os
from pathlib import Path
import runpy
import tempfile
from html import escape

ROOT = Path(__file__).resolve().parents[1]
cursor = runpy.run_path(str(ROOT / 'scripts/check-report-evidence-cursor.py'))
bridge = cursor['fixtures']['load_bridge']()
owner = cursor['OWNER']
text = '候選 < 基準；&lt;b&gt; 是原始文字；差異 > 門檻仍不代表因果。'
with tempfile.TemporaryDirectory(prefix='report-text-') as tmp:
    bridge.ARTIFACTS = bridge.ArtifactStore(Path(tmp))
    run = bridge.ARTIFACTS.create_run(owner)
    source = {'format': 'ask-o11y-report-source-v1', 'purpose': '檢查 A < B', 'conclusion': 'Synthetic comparison', 'facts': {'signal': {'kind': 'number', 'label': '訊號 < 門檻', 'value': 0.42}, 'threshold': {'kind': 'text', 'label': '保留的檢定描述', 'value': 'p < 0.05'}}, 'artifacts': [{'artifact_id': 'chart0', 'fact_refs': ['signal', 'threshold'], 'plotly_output_name': 'chart.json', 'png_output_name': 'preview.png'}]}
    results = [{'display_name': 'report-source.json', 'mime': {'application/json': json.dumps(source)}}, {'display_name': 'chart.json', 'mime': {'application/vnd.plotly.v1+json': json.dumps({'data': [{'type': 'bar', 'x': ['A', 'B'], 'y': [0.42, 0.42]}], 'layout': {}})}}, {'display_name': 'preview.png', 'mime': {'image/png': cursor['PNG']}}]
    execution = bridge.ARTIFACTS.write_json(owner, run, 'sandbox-execution', {'results': results, 'error': None})
    manifest = bridge.ARTIFACTS.write_json(owner, run, 'report-manifest', bridge.ml_report_contract.normalize_report_manifest(execution_ref=execution, results=results))
    bridge.ARTIFACTS.write_json(owner, run, 'sandbox-provenance', {'executor_kind': 'profile_dataset', 'trusted_ml_contract': False, 'report_manifest_ref': manifest})
    inspected, _ = cursor['call'](bridge, 'inspect_report_artifacts', {'report_manifest_ref': manifest})
    assert inspected['ok'], inspected
    report = cursor['synthesis']()
    report['report_title'] = '候選 < 基準'
    report['thesis'] = text
    evidence = [{'fact_ref': 'facts.signal', 'format': 'percent_1', 'label': '訊號 < 門檻'}, {'fact_ref': 'facts.threshold', 'format': 'auto'}]
    report['thesis_evidence'] = copy.deepcopy(evidence)
    section = report['sections'][0]
    section['title'] = '觀察 < 推論'
    section['purpose'] = text
    section['narrative_blocks'] = [{'block_id': 'context', 'title': 'a < b', 'body': text, 'evidence': copy.deepcopy(evidence), 'priority': 'supporting'}]
    panel = section['panels'][0]
    panel['view_ids'] = ['figure']
    for field in bridge.ml_report_contract.TEXT_FIELDS:
        panel[field] = text
    panel['evidence'] = copy.deepcopy(evidence)
    view = copy.deepcopy(cursor['fixtures']['synthesis']()['sections'][0]['panels'][0]['view_narratives'][0])
    for field in bridge.ml_report_contract.VIEW_TEXT_FIELDS:
        view[field] = text
    view['view_id'] = 'figure'
    view['visual_observation'] = None
    view['evidence'] = copy.deepcopy(evidence)
    panel['view_narratives'] = [view]
    args = {'inspection_ref': inspected['refs']['inspection_ref'], 'synthesis': report, 'uid': 'narrative-text', 'title': 'Narrative text'}
    before = copy.deepcopy(args)
    composed, _ = cursor['call'](bridge, 'compose_ml_dashboard', args)
    assert composed['ok'] and args == before, composed
    resolved = bridge.resolve_dashboard_refs({'dashboard': {'$dashboard_ref': composed['refs']['dashboard_ref']}, '_server_context': owner})
    assert resolved['ok'], resolved
    panels = bridge.ml_dashboard_contract._panels(resolved['dashboard']['panels'])
    html_panels = [p['options']['content'] for p in panels if p['type'] == 'text']
    assert any(escape(text) in content for content in html_panels)
    assert all(text not in content for content in html_panels)
    plot = next(p for p in panels if p['type'] == 'asko11y-plotly-panel')
    assert plot['askO11yNarrative']['observation'] == text
    assert plot['options']['narrative']['evidence'][1]['display'] == 'p < 0.05'
    for bad in ['<b>bad</b>', '<script>bad</script>', '<svg/onload=bad>', '<!--bad-->', '<!DOCTYPE html>', '<![bad]>', '<?bad?>', 'https://evil.invalid', 'javascript:bad']:
        for target in ['thesis', 'panel']:
            changed = copy.deepcopy(report)
            if target == 'thesis': changed['thesis'] = bad
            else: changed['sections'][0]['panels'][0]['observation'] = bad
            rejected, _ = cursor['call'](bridge, 'compose_ml_dashboard', {**args, 'synthesis': changed})
            assert not rejected['ok'] and 'unsafe' in rejected['error'], rejected
        try:
            bridge.ml_report_contract._report_text(bad, 'source text')
        except ValueError: pass
        else: raise AssertionError('unsafe source metadata accepted')
    long_report = copy.deepcopy(report); long_report['thesis'] = 'a < b ' * 60
    assert cursor['call'](bridge, 'compose_ml_dashboard', {**args, 'synthesis': long_report})[0]['ok'], 'report text must not inherit the shorter Plotly label limit'
    too_long = copy.deepcopy(report); too_long['thesis'] = 'x' * (bridge.ml_report_contract.MAX_TEXT + 1)
    assert not cursor['call'](bridge, 'compose_ml_dashboard', {**args, 'synthesis': too_long})[0]['ok']
    for value in ['p < 0.05', '提升 42%']:
        changed = copy.deepcopy(report); changed['thesis'] = value
        rejected, _ = cursor['call'](bridge, 'compose_ml_dashboard', {**args, 'synthesis': changed})
        assert not rejected['ok'] and 'numeric' in rejected['error'], rejected
    changed = copy.deepcopy(report); changed['thesis_evidence'][0]['fact_ref'] = 'unknown'
    assert not cursor['call'](bridge, 'compose_ml_dashboard', {**args, 'synthesis': changed})[0]['ok']
    changed = copy.deepcopy(report); changed['sections'][0]['panels'][0]['view_narratives'][0]['visual_observation'] = text
    rejected, _ = cursor['call'](bridge, 'compose_ml_dashboard', {**args, 'synthesis': changed})
    assert not rejected['ok'] and 'spec-only' in rejected['error'], rejected
    for bad in ['<a href="https://evil.invalid">bad</a>', '<img src=x>', '<svg onload=bad></svg>']:
        altered = copy.deepcopy(resolved['dashboard'])
        bridge.ml_dashboard_contract._panels(altered['panels'])[0]['options']['content'] = bad
        try: bridge.ml_dashboard_contract.validate_ml_dashboard_minimum(altered)
        except ValueError: pass
        else: raise AssertionError('unsafe rendered HTML accepted')
    if output := os.environ.get('REPORT_SYNTHESIS_FIXTURE_OUT'):
        Path(output).write_text(json.dumps({'narrative_text': plot['options']}, ensure_ascii=False))
    if output := os.environ.get('REPORT_TEXT_HTML_OUT'):
        Path(output).write_text('\n'.join(html_panels))
print('PASS public narrative/source/evidence text, immutable input, escaped HTML, markup/number/fact/vision guards')
