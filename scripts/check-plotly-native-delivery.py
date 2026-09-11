#!/usr/bin/env python3
"""Native producer/consumer regression. No live services or user computation."""
import copy
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import ml_plotly_contract as contract
import ml_report_contract as report


def parse_json(value):
    try:
        return json.loads(value)
    except (TypeError, ValueError) as exc:
        raise AssertionError('regression fixture is not valid JSON') from exc


def produced_figures():
    # A missing observation must remain in place, not be deleted or filled.
    time = go.Figure(go.Scatter(x=['a', 'b', 'c', 'd'], y=np.array([2., np.nan, 4., 3.]), mode='lines+markers'))
    box = go.Figure(go.Box(y=[2, 4, 3], boxmean=True))
    bars = go.Figure(go.Bar(x=[0.2, -0.3], y=['a', 'b'], orientation='h', error_x={'type': 'data', 'array': [0.1, 0.2]}))
    subplots = make_subplots(rows=2, cols=2, subplot_titles=['A', 'B', 'C', 'D'], shared_xaxes=True)
    for i in range(4):
        subplots.add_trace(go.Scatter(x=[1, 2], y=[i, i + 1]), row=i // 2 + 1, col=i % 2 + 1)
    polar = go.Figure(go.Scatterpolar(r=[1, 2, 3], theta=[0, 90, 180], mode='lines', hovertemplate='radius=%{r}<extra></extra>'))
    return [parse_json(str(fig.to_json())) for fig in (time, box, bars, subplots, polar)]


def make_results(figures):
    source = {'format': report.REPORT_SOURCE_FORMAT, 'purpose': 'Compare observations', 'conclusion': 'Exploratory evidence, not causation', 'facts': {'count': {'kind': 'number', 'label': 'Observations', 'value': 4}}, 'artifacts': []}
    results = []
    for i, figure in enumerate(figures):
        name = f'figure-{i}.json'
        results.append({'display_name': name, 'mime': {'application/vnd.plotly.v1+json': json.dumps(figure)}})
        source['artifacts'].append({'artifact_id': f'figure-{i}', 'fact_refs': ['count'], 'plotly_output_name': name})
    results.append({'display_name': 'report-source.json', 'mime': {'application/json': json.dumps(source)}})
    return results


def main():
    figures = produced_figures()
    original = copy.deepcopy(figures)
    clean = [contract.sanitize_figure(f) for f in figures]
    assert figures == original
    assert clean[0]['data'][0]['y'] == [2., None, 4., 3.]
    assert clean[2]['data'][0]['error_x']['array'] == [0.1, 0.2]
    assert clean[3]['layout']['annotations'] == figures[3]['layout']['annotations']
    assert clean[3]['layout']['xaxis']['domain'] == figures[3]['layout']['xaxis']['domain']
    assert clean[4]['data'][0]['type'] == 'scatterpolar'
    for figure in clean:
        assert contract.sanitize_figure(figure) == figure
        json.dumps(figure, allow_nan=False)
    # Real security/resource boundaries, not a chart-feature allowlist.
    benign = {'data': [{'type': 'scatter', 'x': [1], 'y': [2], 'customdata': [{'source': 'Australia', 'url': 'plain metadata'}], 'text': ['transcript p < 0.05']}], 'layout': {}}
    contract.sanitize_figure(benign)
    image = go.Figure(go.Image(z=np.array([[[1, 2, 3], [4, 5, 6]]], dtype=np.uint8)))
    assert contract.sanitize_figure(parse_json(str(image.to_json())))['data'][0]['z'] == [[[1, 2, 3], [4, 5, 6]]]
    for unsafe in (
        {'data': [{'type': 'scattergeo', 'lat': [1], 'lon': [2]}]},
        {'data': [{'type': 'scatter', 'y': [1]}], 'layout': {'images': [{'source': 'https://invalid.example/image.png'}]}},
        {'data': [{'type': 'scatter', 'y': [1], 'text': ['<a href="https://invalid.example">link</a>']}]},
        {'data': [{'type': 'scatter', 'y': [1], 'text': ['<script>alert(1)</script>']}]},
        {'data': [{'type': 'scatter', 'y': [1]}], 'config': {'plotlyServerURL': 'https://invalid.example'}},
        {'data': [{'type': 'scatter', 'y': [2**53]}]},
        {'data': [{'type': 'scatter', 'y': [math.inf]}]},
        {'data': [{'type': 'scatter', 'y': [1]}], 'layout': {'width': math.nan}},
        {'data': [{'type': 'scatter', 'y': [1]}], 'layout': {'annotations': [{'x': 1, 'y': math.nan, 'text': 'position'}]}},
        {'data': [{'type': 'scatter', 'y': [1], 'customdata': [{'y': math.nan}]}]},
    ):
        try:
            contract.sanitize_figure(unsafe)
        except contract.FigureError:
            continue
        else:
            raise AssertionError('unsafe figure accepted')
    results = make_results(figures + [{'data': [{'type': 'not_a_plotly_trace'}]}])
    before = copy.deepcopy(results)
    manifest = report.normalize_report_manifest(execution_ref='artifact://fixture/sandbox-execution', results=results)
    assert manifest['format'] == 'ask-o11y-report-manifest-v2'
    assert manifest['artifacts'][-1]['render']['mode'] == 'error'
    assert all(a['render']['mode'] == 'plotly' for a in manifest['artifacts'][:-1])
    assert manifest['facts']['count']['value'] == 4 and results == before
    # The original rejected figures can be replayed without executing captured Python.
    if '--retained' in sys.argv:
        path = ROOT / '.analysis-artifacts/runs/run_456c5e9c58e447d0aae5e4982077d271/sandbox-execution.json'
        data = parse_json(path.read_text())
        retained = [parse_json(r['mime']['application/vnd.plotly.v1+json']) for r in data['results'] if 'application/vnd.plotly.v1+json' in r.get('mime', {})]
        assert len(retained) == 4
        clean.extend(contract.sanitize_figure(figure) for figure in retained)
    if '--out' in sys.argv:
        benign['layout']['title'] = {'text': '&lt;a href="https://invalid.example"&gt;link&lt;/a&gt; &lt;script&gt;window.__executed=1&lt;/script&gt;'}
        clean.append(contract.sanitize_figure(benign))
        Path(sys.argv[sys.argv.index('--out') + 1]).write_text(json.dumps(clean, ensure_ascii=False, allow_nan=False))
    print('PASS: native producer, missing gap, error bars, subplot defaults, polar, idempotency, per-artifact failure and retained facts')


if __name__ == '__main__':
    main()
