from __future__ import annotations

import json
from .prompts import CRITERIA


class ResponseError(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ResponseError(f'중복 JSON 키: {key}')
        result[key] = value
    return result


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ResponseError(f'{label}: 필수 필드가 누락되었거나 추가 필드가 있습니다.')


def _text(value, label, required=False):
    if not isinstance(value, str) or (required and not value.strip()):
        raise ResponseError(f'{label}: {"비어 있지 않은 " if required else ""}문자열이 필요합니다.')


def validate_response(text: str, kind: str, materials: dict) -> dict:
    try:
        value = json.loads(text, object_pairs_hook=_unique_object, parse_constant=lambda _: (_ for _ in ()).throw(ResponseError('유효하지 않은 JSON 숫자')))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ResponseError('완전한 JSON 객체가 아닙니다.') from exc
    _keys(value, ('policy_id', 'scores', 'reasons', 'improvements', 'missing_reasons'), '응답')
    if value['policy_id'] != materials['policy']['id']:
        raise ResponseError('요청과 응답의 정책 ID가 다릅니다.')
    criteria = CRITERIA[kind]
    for field in ('scores', 'reasons', 'missing_reasons'):
        _keys(value[field], criteria, field)
    allowed = {
        'policy_ids': {materials['policy']['id']} | {p['id'] for p in materials['related_policies']},
        'risk_ids': {r['id'] for r in materials['risks']},
        'requirement_ids': {r['id'] for r in materials['requirements']},
    }
    for c in criteria:
        score = value['scores'][c]
        if score is not None and (type(score) is not int or not 1 <= score <= 5):
            raise ResponseError(f'{c}: 점수는 1~5의 정수 또는 null이어야 합니다.')
        missing = value['missing_reasons'][c]
        _text(missing, f'{c}.missing_reasons')
        if score is None and not missing.strip():
            raise ResponseError(f'{c}: null의 사유가 없습니다.')
        if score is not None and missing.strip():
            raise ResponseError(f'{c}: 점수와 미채점 사유가 동시에 있습니다.')
        reason = value['reasons'][c]
        _keys(reason, ('judgment', 'policy_ids', 'risk_ids', 'requirement_ids', 'comparison'), f'{c}.reasons')
        for key in ('judgment', 'comparison'):
            _text(reason[key], f'{c}.{key}', required=score is not None)
        for key, permitted in allowed.items():
            ids = reason[key]
            if not isinstance(ids, list) or any(not isinstance(i, str) or i not in permitted for i in ids):
                raise ResponseError(f'{c}.{key}: 제공되지 않은 ID 또는 잘못된 배열입니다.')
            if len(set(ids)) != len(ids):
                raise ResponseError(f'{c}.{key}: ID가 중복되었습니다.')
    improvements = value['improvements']
    if not isinstance(improvements, list):
        raise ResponseError('improvements는 배열이어야 합니다.')
    for item in improvements:
        _text(item, 'improvements', required=True)
    return value
