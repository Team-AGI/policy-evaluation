from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

from .aggregate import export
from .config import ROOT, load_config, load_env
from .data import load_data
from .prompts import CRITERIA
from .runner import make_tasks, prepare, run
from .storage import atomic_json, load_results, run_lock


def parser():
    p = argparse.ArgumentParser(description='MCP 보안 정책 8개 사례 예비 평가. 실제 실행에는 API 비용이 발생합니다.')
    p.add_argument('--config', type=Path, default=ROOT / 'config.toml')
    p.add_argument('--dry-run', action='store_true', help='API 호출 없이 자료와 요청 구성을 점검')
    p.add_argument('--resume', type=Path, metavar='RUN_DIR', help='완료된 응답을 유지하고 오류와 미실행 요청만 이어 실행')
    p.add_argument('--summarize', type=Path, metavar='RUN_DIR', help='API 호출 없이 기존 기록을 다시 집계')
    p.add_argument('--cases', nargs='+', metavar='PIL-01')
    p.add_argument('--models', nargs='+', choices=('opus', 'deepseek', 'gpt'))
    p.add_argument('--tasks', nargs='+', choices=tuple(CRITERIA))
    p.add_argument('--rounds', type=int)
    return p


def _select(values, available, label):
    values = values if values is not None else available
    if not values or len(set(values)) != len(values) or any(v not in available for v in values):
        raise ValueError(f'{label} 선택을 확인하세요. 사용 가능: {", ".join(available)}')
    return values


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if sys.version_info < (3, 11):
        print('Python 3.11 이상이 필요합니다.', file=sys.stderr)
        return 1
    if args.summarize and (args.resume or args.dry_run):
        parser().error('--summarize는 --resume, --dry-run과 함께 사용할 수 없습니다.')
    folder = None
    manifest = None
    try:
        if args.summarize:
            folder = args.summarize.resolve()
            with run_lock(folder):
                manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
                summary = export(ROOT, folder, manifest, load_results(folder, [t['task_id'] for t in manifest['tasks']]))
            print(f'집계 완료: {folder}\n상태: {summary["task_statuses"]}')
            return 0
        config = load_config(args.config.resolve())
        load_env(ROOT / '.env')
        data = load_data(ROOT)
        previous = None
        if args.resume:
            folder = args.resume.resolve()
            previous = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        def selected(argument, key, fallback):
            if argument is not None:
                return argument
            if previous:
                return list(dict.fromkeys(t[key] for t in previous['tasks']))
            return fallback
        case_ids = _select(selected(args.cases, 'case_id', None), [c['case_id'] for c in data['pilot_cases']], '사례')
        model_ids = _select(selected(args.models, 'model', None), list(config['models']), '모델')
        kinds = _select(selected(args.tasks, 'kind', None), list(CRITERIA), '평가')
        rounds = args.rounds if args.rounds is not None else previous['config']['rounds'] if previous else config['run']['rounds']
        if rounds < 1:
            raise ValueError('라운드 수는 1 이상이어야 합니다.')
        config['run']['rounds'] = rounds
        by_id = {c['case_id']: c for c in data['pilot_cases']}
        cases = [by_id[c] for c in case_ids]
        tasks = make_tasks(cases, model_ids, kinds, rounds)
        manifest, requests = prepare(ROOT, config, data, tasks)
        label = 'dryrun' if args.dry_run else 'pilot'
        if folder is None:
            output = Path(config['run']['output_dir'])
            if not output.is_absolute():
                output = ROOT / output
            folder = output / (label + '_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid4().hex[:6])
        print(f'사례 {len(cases)}개 × 모델 {len(model_ids)}개 × 평가 {len(kinds)}개 × 라운드 {rounds}회 = {len(tasks)}개 요청')
        if args.dry_run:
            with run_lock(folder):
                if previous and previous['fingerprint'] != manifest['fingerprint']:
                    raise ValueError('이전 실행과 평가 조건이 다릅니다.')
                if not previous:
                    atomic_json(folder / 'manifest.json', manifest)
                for task_id, request in requests.items():
                    atomic_json(folder / 'requests' / f'{task_id}.json', request)
                atomic_json(folder / 'plan.json', {'api_calls': 0, 'planned_requests': len(tasks),
                    'input_characters': {tid: len(r['instructions']) + len(json.dumps(r['materials'], ensure_ascii=False)) for tid, r in requests.items()},
                    'note': '문자 수는 토큰 수가 아닙니다. API 호출과 채점은 수행하지 않았습니다.'})
            print(f'구성 점검 완료. API 호출 없음. 요청 미리보기: {folder}')
            return 0
        print(f'API 비용이 발생합니다. 결과 폴더: {folder}', flush=True)
        try:
            results = run(ROOT, folder, config, tasks, manifest, requests, resume=bool(args.resume))
        except KeyboardInterrupt:
            if (folder / 'manifest.json').exists():
                with run_lock(folder):
                    export(ROOT, folder, manifest, load_results(folder, [t.id for t in tasks]))
            print(f'중단되었습니다. --resume "{folder}"로 이어 실행할 수 있습니다.', file=sys.stderr)
            return 130
        with run_lock(folder):
            summary = export(ROOT, folder, manifest, results)
        print(f'상태: {summary["task_statuses"]}\n결과: {folder / "summary.csv"}')
        if all(results.get(t.id, {}).get('status') == 'complete' for t in tasks):
            return 0
        print('자료 부족 또는 실행 오류가 남았습니다. model_scores.csv와 attempts를 확인하세요.', file=sys.stderr)
        return 2
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(f'실행할 수 없습니다: {exc}', file=sys.stderr)
        return 1
