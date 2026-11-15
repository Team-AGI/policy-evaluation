from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import time

from . import __version__
from .clients import NETWORK_ERRORS, classify_response, make_request, post_json
from .data import evaluation_input
from .prompts import build_prompt, response_schema
from .storage import atomic_json, digest, file_hash, load_results, now, redact, run_lock
from .validation import ResponseError, validate_response


@dataclass(frozen=True)
class Task:
    case_id: str
    policy_id: str
    model: str
    kind: str
    round: int

    @property
    def id(self) -> str:
        return f'{self.case_id}__{self.model}__{self.kind}__r{self.round}'

    def as_dict(self):
        return {'task_id': self.id, 'case_id': self.case_id, 'policy_id': self.policy_id,
                'model': self.model, 'kind': self.kind, 'round': self.round}


def make_tasks(cases: list, models: list[str], kinds: list[str], rounds: int) -> list[Task]:
    return [Task(c['case_id'], c['policy_id'], m, k, r)
            for r in range(1, rounds + 1) for c in cases for k in kinds for m in models]


def prepare(root: Path, config: dict, data: dict, tasks: list[Task]) -> tuple[dict, dict]:
    cases = {c['case_id']: c for c in data['pilot_cases']}
    instructions = {k: build_prompt(root, k) for k in dict.fromkeys(t.kind for t in tasks)}
    requests = {}
    for t in tasks:
        material = evaluation_input(data, cases[t.case_id])
        requests[t.id] = {'instructions': instructions[t.kind], 'materials': material, 'schema': response_schema(t.kind)}
    # Operational pacing can change on resume; evaluated content/settings cannot.
    public_config = {'models': config['models'], 'rounds': config['run']['rounds']}
    source_files = [*sorted((root / 'data').glob('*.json')), *sorted((root / 'prompts').glob('*.md')),
                    *sorted((root / 'src' / 'policy_eval').glob('*.py')), root / 'run_pilot.py']
    hashes = {p.relative_to(root).as_posix(): file_hash(p) for p in source_files}
    manifest = {'program_version': __version__, 'config': public_config,
                'tasks': [t.as_dict() for t in tasks], 'source_hashes': hashes,
                'request_hashes': {key: digest(value) for key, value in requests.items()}}
    manifest['fingerprint'] = digest(manifest)
    return manifest, requests


def run(root: Path, folder: Path, config: dict, tasks: list[Task], manifest: dict, requests: dict,
        resume: bool = False, transport=post_json, sleep=time.sleep, emit=print) -> dict:
    run_config = config['run']
    model_names = list(dict.fromkeys(t.model for t in tasks))
    keys = {name: os.environ.get(config['models'][name]['api_key_env'], '').strip() for name in model_names}
    missing = [config['models'][name]['api_key_env'] for name, key in keys.items() if not key or key.lower() in ('your_key_here', 'changeme')]
    if missing:
        raise ValueError('API 키가 없습니다: ' + ', '.join(missing) + '. .env 또는 환경 변수에 입력하세요.')
    secrets = list(keys.values())
    with run_lock(folder):
        manifest_path = folder / 'manifest.json'
        if resume:
            if not manifest_path.exists():
                raise ValueError('이어 실행할 manifest.json이 없습니다.')
            previous = json.loads(manifest_path.read_text(encoding='utf-8'))
            if previous['fingerprint'] != manifest['fingerprint']:
                raise ValueError('이전 실행과 자료, 지시문, 모델 설정 또는 선택 범위가 다릅니다. 새 실행을 시작하세요.')
        else:
            if manifest_path.exists():
                raise ValueError('이미 실행 기록이 있습니다. --resume을 사용하세요.')
            atomic_json(manifest_path, {**manifest, 'created_at': now()})
        for task_id, request in requests.items():
            snapshot = folder / 'requests' / f'{task_id}.json'
            if snapshot.exists():
                if digest(json.loads(snapshot.read_text(encoding='utf-8'))) != manifest['request_hashes'][task_id]:
                    raise ValueError(f'저장된 요청이 변경되었습니다: {task_id}')
            else:
                atomic_json(snapshot, request)
        results = load_results(folder, [t.id for t in tasks])
        blocked = set()
        total = len(tasks)
        for position, task in enumerate(tasks, 1):
            current = results.get(task.id)
            if current and current['status'] in ('complete', 'insufficient_material'):
                emit(f'[{position}/{total}] {task.id}: 저장된 결과 유지')
                continue
            if task.model in blocked:
                emit(f'[{position}/{total}] {task.id}: 해당 모델의 API 설정 오류로 보류')
                continue
            model = config['models'][task.model]
            request = requests[task.id]
            attempt_folder = folder / 'attempts' / task.id
            attempt_files = sorted(attempt_folder.glob('*.json'))
            attempt_n = max((int(p.stem) for p in attempt_files), default=0)
            budget = model['max_output_tokens']
            if current and current.get('status') == 'truncated':
                budget = min(model['max_output_tokens_limit'], max(budget, current.get('budget', budget) * 2))
            for invocation_attempt in range(run_config['max_attempts']):
                attempt_n += 1
                payload, headers = make_request(model, request['instructions'], request['materials'], request['schema'], budget)
                if model['provider'] == 'anthropic':
                    headers['x-api-key'] = keys[task.model]
                else:
                    headers['Authorization'] = 'Bearer ' + keys[task.model]
                record = {**task.as_dict(), 'attempt': attempt_n, 'started_at': now(), 'budget': budget,
                          'request_payload': payload}
                # Persist start first, including a task interrupted during a network call.
                record.update(status='interrupted', error='응답 수신 전에 실행이 중단되었습니다.')
                attempt_path = attempt_folder / f'{attempt_n:04}.json'
                atomic_json(attempt_path, redact(record, secrets))
                try:
                    response = transport(model['endpoint'], headers, payload, run_config['timeout_seconds'])
                    record['raw_response'] = response.body
                    info = classify_response(model['provider'], response)
                    record.update(info)
                    record.pop('error', None)
                    if info.get('error'):
                        record['error'] = info['error']
                    if info['status'] == 'received':
                        try:
                            value = validate_response(info['text'], task.kind, request['materials'])
                            record['evaluation'] = value
                            record['status'] = 'insufficient_material' if any(v is None for v in value['scores'].values()) else 'complete'
                            record['retryable'] = False
                        except ResponseError as exc:
                            record.update(status='invalid_response', retryable=True, error=str(exc))
                except NETWORK_ERRORS:
                    # Do not serialize a request/exception that may contain credentials.
                    record.update(status='network_error', retryable=True, error='연결 또는 대기 시간 오류. 서버 처리 여부는 확인되지 않았습니다.')
                except ValueError as exc:
                    record.update(status='invalid_api_response', retryable=False, error=str(exc))
                record['finished_at'] = now()
                record = redact(record, secrets)
                atomic_json(attempt_path, record)
                # Latest record is only a view; all attempts are preserved separately.
                atomic_json(folder / 'results' / f'{task.id}.json', record)
                results[task.id] = record
                emit(f'[{position}/{total}] {task.id}: {record["status"]} (시도 {attempt_n})')
                if record.get('block_model'):
                    blocked.add(task.model)
                if not record.get('retryable') or invocation_attempt + 1 >= run_config['max_attempts']:
                    break
                if record['status'] == 'truncated':
                    if budget >= model['max_output_tokens_limit']:
                        break
                    budget = min(budget * 2, model['max_output_tokens_limit'])
                delay = min(run_config['max_retry_delay_seconds'], max(
                    run_config['retry_delay_seconds'] * 2 ** invocation_attempt, record.get('retry_after', 0)))
                sleep(delay)
        return results
