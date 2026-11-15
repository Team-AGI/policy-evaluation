from __future__ import annotations

from collections import Counter
import csv
import json
from pathlib import Path
from statistics import mean

from .prompts import CRITERIA
from .storage import atomic_json, now


def _csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    # Excel-friendly UTF-8, with textual formula injection protection.
    def safe(value):
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
            return "'" + value
        return value
    with path.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: safe(row.get(k, '')) for k in fields} for row in rows)


def aggregate(manifest: dict, results: dict) -> dict:
    models = manifest['config']['models']
    task_list = manifest['tasks']
    by_key = {(t['case_id'], t['kind'], t['round'], t['model']): t for t in task_list}
    rows = []
    groups = list(dict.fromkeys((t['case_id'], t['policy_id'], t['kind'], t['round']) for t in task_list))
    for case_id, policy_id, kind, round_n in groups:
        evaluations = {}
        missing = []
        for name in models:
            task = by_key.get((case_id, kind, round_n, name))
            result = results.get(task['task_id']) if task else None
            if not result or result['status'] != 'complete':
                missing.append({'model': name, 'status': result['status'] if result else 'not_available'})
            else:
                evaluations[name] = result['evaluation']
        scores = None
        basic_100 = None
        if not missing:
            scores = {c: sum(models[name]['weight'] * e['scores'][c] for name, e in evaluations.items()) for c in CRITERIA[kind]}
            if kind == 'basic':
                basic_100 = (mean(scores.values()) - 1) / 4 * 100
        rows.append({'case_id': case_id, 'policy_id': policy_id, 'kind': kind, 'round': round_n,
                     'status': 'complete' if not missing else 'incomplete', 'scores': scores,
                     'basic_100': basic_100, 'missing': missing})
    representatives = []
    for case_id, policy_id, kind in dict.fromkeys((r['case_id'], r['policy_id'], r['kind']) for r in rows):
        group = [r for r in rows if (r['case_id'], r['kind']) == (case_id, kind)]
        complete = len(group) == manifest['config']['rounds'] and all(r['status'] == 'complete' for r in group)
        scores = {c: mean(r['scores'][c] for r in group) for c in CRITERIA[kind]} if complete else None
        basic_values = [r['basic_100'] for r in group] if complete and kind == 'basic' else []
        representatives.append({
            'case_id': case_id, 'policy_id': policy_id, 'kind': kind,
            'status': 'complete' if complete else 'incomplete',
            'rounds_expected': manifest['config']['rounds'], 'rounds_complete': sum(r['status'] == 'complete' for r in group),
            'scores': scores, 'basic_100': mean(basic_values) if basic_values else None,
            'basic_min': min(basic_values) if basic_values else None,
            'basic_max': max(basic_values) if basic_values else None,
            'basic_range': max(basic_values) - min(basic_values) if basic_values else None,
        })
    return {'rounds': rows, 'representatives': representatives}


def export(root: Path, folder: Path, manifest: dict, results: dict) -> dict:
    report = aggregate(manifest, results)
    statuses = Counter(results[t['task_id']]['status'] if t['task_id'] in results else 'pending' for t in manifest['tasks'])
    attempts = []
    for task in manifest['tasks']:
        for path in sorted((folder / 'attempts' / task['task_id']).glob('*.json')):
            record = json.loads(path.read_text(encoding='utf-8'))
            usage = record.get('usage') or {}
            attempts.append({
                'case_id': task['case_id'], 'model': task['model'], 'kind': task['kind'], 'round': task['round'],
                'attempt': record['attempt'], 'status': record['status'], 'budget': record['budget'],
                'input_tokens': usage.get('input_tokens', usage.get('prompt_tokens')),
                'output_tokens': usage.get('output_tokens', usage.get('completion_tokens')),
                'requested_model': manifest['config']['models'][task['model']]['api_model'],
                'returned_model': record.get('returned_model'), 'raw_usage': usage,
            })
    summary = {**report, 'generated_at': now(), 'tasks_expected': len(manifest['tasks']),
               'task_statuses': dict(statuses), 'attempts_recorded': len(attempts),
               'note': '예비 평가 결과. 기본 평가와 FLASK 기준별 점수를 구분하며 정책 완료 판정을 자동 생성하지 않는다.'}
    atomic_json(folder / 'summary.json', summary)
    flat_rounds = [{**r, **(r['scores'] or {})} for r in report['rounds']]
    flat_representatives = [{**r, **(r['scores'] or {})} for r in report['representatives']]
    metrics = list(CRITERIA['basic'] + CRITERIA['flask'])
    _csv(folder / 'round_scores.csv', flat_rounds, ['case_id', 'policy_id', 'kind', 'round', 'status', 'basic_100', *metrics, 'missing'])
    _csv(folder / 'summary.csv', flat_representatives, ['case_id', 'policy_id', 'kind', 'status', 'rounds_expected', 'rounds_complete',
         'basic_100', 'basic_min', 'basic_max', 'basic_range', *metrics])
    model_rows = []
    for task in manifest['tasks']:
        result = results.get(task['task_id'], {})
        evaluation = result.get('evaluation', {})
        for criterion in CRITERIA[task['kind']]:
            model_rows.append({**task, 'status': result.get('status', 'pending'), 'criterion': criterion,
                               'score': evaluation.get('scores', {}).get(criterion),
                               'reason': evaluation.get('reasons', {}).get(criterion),
                               'improvements': evaluation.get('improvements', []),
                               'missing_reason': evaluation.get('missing_reasons', {}).get(criterion),
                               'error': result.get('error', '')})
    _csv(folder / 'model_scores.csv', model_rows, ['task_id', 'case_id', 'policy_id', 'model', 'kind', 'round', 'status',
         'criterion', 'score', 'reason', 'improvements', 'missing_reason', 'error'])
    _csv(folder / 'usage.csv', attempts, ['case_id', 'model', 'kind', 'round', 'attempt', 'status', 'budget',
         'input_tokens', 'output_tokens', 'requested_model', 'returned_model', 'raw_usage'])
    # This file is deliberately read only AFTER evaluation, never by prompt construction.
    expected = json.loads((root / 'review' / 'expected_findings.json').read_text(encoding='utf-8'))
    reps = {(r['case_id'], r['kind']): r for r in report['representatives']}
    comparisons = []
    for pair in expected['pairs']:
        for kind, field in [('basic', 'basic_focus'), ('flask', 'flask_focus')]:
            criterion = pair[field]
            baseline = (reps.get((pair['baseline'], kind), {}).get('scores') or {}).get(criterion)
            contrast = (reps.get((pair['contrast'], kind), {}).get('scores') or {}).get(criterion)
            comparisons.append({'baseline': pair['baseline'], 'contrast': pair['contrast'], 'kind': kind,
                                'criterion': criterion, 'baseline_score': baseline, 'contrast_score': contrast,
                                'contrast_minus_baseline': contrast - baseline if baseline is not None and contrast is not None else None,
                                'review_focus': pair['expected_issue'], 'review_result': ''})
    _csv(folder / 'case_comparison.csv', comparisons, ['baseline', 'contrast', 'kind', 'criterion', 'baseline_score',
         'contrast_score', 'contrast_minus_baseline', 'review_focus', 'review_result'])
    return summary
