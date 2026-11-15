from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
import json
import math
import socket
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


@dataclass
class HTTPResponse:
    status: int
    body: str
    headers: dict


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_json(endpoint: str, headers: dict, body: dict, timeout: float) -> HTTPResponse:
    """One network attempt only; redirects never forward credentials."""
    request = Request(endpoint, data=json.dumps(body, ensure_ascii=False).encode('utf-8'), headers=headers, method='POST')
    try:
        response = build_opener(NoRedirect()).open(request, timeout=timeout)
    except HTTPError as exc:
        response = exc
    with response:
        raw = response.read(64 * 1024 * 1024 + 1)
        if len(raw) > 64 * 1024 * 1024:
            raise ValueError('API 응답 크기 제한을 초과했습니다.')
        return HTTPResponse(response.status, raw.decode('utf-8', errors='replace'), {k.lower(): v for k, v in response.headers.items()})


def retry_after(headers: dict) -> float:
    value = headers.get('retry-after', '')
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else 0.0
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0.0


def make_request(model: dict, instructions: str, materials: dict, schema: dict, budget: int) -> tuple[dict, dict]:
    user = json.dumps(materials, ensure_ascii=False)
    provider = model['provider']
    if provider == 'openai':
        payload = {
            'model': model['api_model'], 'instructions': instructions, 'input': user,
            'max_output_tokens': budget, 'store': False,
            'text': {'format': {'type': 'json_schema', 'name': 'policy_evaluation', 'strict': True, 'schema': schema}},
        }
        if model.get('effort'):
            payload['reasoning'] = {'effort': model['effort']}
    elif provider == 'anthropic':
        payload = {'model': model['api_model'], 'system': instructions,
                   'messages': [{'role': 'user', 'content': user}], 'max_tokens': budget}
        if model.get('effort'):
            payload['output_config'] = {'effort': model['effort']}
    else:
        payload = {'model': model['api_model'], 'messages': [
            {'role': 'system', 'content': instructions}, {'role': 'user', 'content': user}],
            'max_tokens': budget, 'response_format': {'type': 'json_object'},
            'thinking': {'type': 'enabled'}}
        if model.get('effort'):
            payload['reasoning_effort'] = model['effort']
    headers = {'Content-Type': 'application/json', 'User-Agent': 'policy-evaluation/0.1'}
    if provider == 'anthropic':
        headers['anthropic-version'] = model.get('api_version', '2026-10-21')
    return payload, headers


def classify_response(provider: str, response: HTTPResponse) -> dict:
    """Normalize provider completion signals before accepting any score."""
    info = {'http_status': response.status, 'request_id': response.headers.get('x-request-id') or response.headers.get('request-id'),
            'retry_after': retry_after(response.headers), 'text': '', 'usage': {}, 'returned_model': None,
            'finish_reason': None, 'retryable': False, 'block_model': False, 'status': 'api_error'}
    if response.status != 200:
        info['retryable'] = response.status in (408, 409, 425, 429) or response.status >= 500
        # A case-specific input error must not prevent other cases from running.
        info['block_model'] = response.status in (401, 403, 404)
        info['error'] = f'HTTP {response.status}. attempts의 원본 오류 응답을 확인하세요.'
        return info
    try:
        value = json.loads(response.body)
        info['usage'] = value.get('usage') or {}
        info['returned_model'] = value.get('model')
        if provider == 'openai':
            finish = value.get('status')
            info['finish_reason'] = finish
            messages = [x for x in value.get('output', []) if x.get('type') == 'message']
            blocks = [b for m in messages for b in m.get('content', [])]
            if any(b.get('type') == 'refusal' for b in blocks):
                info.update(status='refusal', error='모델이 응답을 거부했습니다.')
                return info
            text = ''.join(b['text'] for b in blocks if b.get('type') == 'output_text')
            reason = (value.get('incomplete_details') or {}).get('reason')
            if finish == 'incomplete' and reason == 'max_output_tokens':
                info.update(status='truncated', retryable=True, error='출력 토큰 한도에 도달했습니다.', text=text)
                return info
            if finish != 'completed':
                info.update(error=f'완료되지 않은 응답: {finish}/{reason}', text=text)
                return info
        elif provider == 'anthropic':
            finish = value.get('stop_reason')
            info['finish_reason'] = finish
            text = ''.join(b['text'] for b in value.get('content', []) if b.get('type') == 'text')
            if finish in ('max_tokens', 'model_context_window_exceeded'):
                info.update(status='truncated', retryable=finish == 'max_tokens', error=f'응답이 잘렸습니다: {finish}', text=text)
                return info
            if finish != 'end_turn':
                info.update(status='refusal' if finish == 'refusal' else 'api_error', error=f'정상 종료가 아닙니다: {finish}', text=text)
                return info
        else:
            choice = value['choices'][0]
            finish = choice.get('finish_reason')
            info['finish_reason'] = finish
            text = choice['message'].get('content') or ''
            if finish == 'length':
                info.update(status='truncated', retryable=True, error='출력 토큰 한도에 도달했습니다.', text=text)
                return info
            if finish != 'stop':
                info.update(status='refusal' if finish == 'content_filter' else 'api_error', error=f'정상 종료가 아닙니다: {finish}', text=text)
                return info
        if not isinstance(text, str) or not text.strip():
            info.update(status='empty_response', retryable=True, error='완료 신호는 있으나 응답 본문이 없습니다.')
        else:
            info.update(status='received', text=text)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        info.update(status='invalid_api_response', retryable=True, error='API 응답 구조를 해석할 수 없습니다.')
    return info


NETWORK_ERRORS = (URLError, TimeoutError, socket.timeout, ConnectionError, OSError, HTTPException)
