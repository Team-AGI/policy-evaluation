from __future__ import annotations

import json
import re
from pathlib import Path


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _index(records, key):
    index = {}
    for record in records:
        rid = record[key]
        if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', rid) or rid in index:
            raise ValueError(f'중복되거나 유효하지 않은 ID: {rid!r}')
        index[rid] = record
    return index


def load_data(root: Path) -> dict:
    data = {name: read_json(root / 'data' / f'{name}.json') for name in ('pilot_cases', 'policies', 'risks', 'requirements')}
    indexes = {name: _index(data[name], 'id') for name in ('policies', 'risks', 'requirements')}
    _index(data['pilot_cases'], 'case_id')
    for name, items in indexes.items():
        for record in items.values():
            if not isinstance(record['text'], str) or not record['text'].strip():
                raise ValueError(f'{name}: 본문이 없습니다.')
    for case in data['pilot_cases']:
        pid = case['policy_id']
        if pid not in indexes['policies']:
            raise ValueError(f'{case["case_id"]}: 정책이 없습니다: {pid}')
        if 'policy_text' in case and not isinstance(case['policy_text'], str):
            raise ValueError('policy_text는 문자열이어야 합니다.')
        if pid in case['related_policy_ids']:
            raise ValueError('대조 사례의 원본을 같은 ID의 관련 정책으로 제공할 수 없습니다.')
        for rid in case['related_policy_ids']:
            if rid not in indexes['policies']:
                raise ValueError(f'관련 정책이 없습니다: {rid}')
        for rid in case['requirement_ids']:
            if rid not in indexes['requirements']:
                raise ValueError(f'요구사항이 없습니다: {rid}')
            for risk in indexes['requirements'][rid]['risk_ids']:
                if risk not in indexes['risks']:
                    raise ValueError(f'위험 분석이 없습니다: {risk}')
    data['indexes'] = indexes
    return data


def evaluation_input(data: dict, case: dict) -> dict:
    """Allowlist only evaluation materials; never serialize case/review metadata."""
    indexes = data['indexes']
    policy = indexes['policies'][case['policy_id']]
    requirements = [indexes['requirements'][r] for r in case['requirement_ids']]
    risk_ids = sorted({rid for r in requirements for rid in r['risk_ids']})
    return {
        'policy': {'id': policy['id'], 'text': case.get('policy_text', policy['text'])},
        'related_policies': [{'id': rid, 'text': indexes['policies'][rid]['text']} for rid in case['related_policy_ids']],
        'risks': [{'id': rid, 'title': indexes['risks'][rid]['title'], 'text': indexes['risks'][rid]['text']} for rid in risk_ids],
        'requirements': [{'id': r['id'], 'text': r['text'], 'risk_ids': r['risk_ids']} for r in requirements],
    }
