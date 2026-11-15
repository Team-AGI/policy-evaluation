from __future__ import annotations

import math
import os
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


def load_env(path: Path) -> None:
    """Load plain NAME=value entries without executing or interpolating text."""
    if not path.exists():
        return
    for n, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, sep, value = line.partition('=')
        name, value = name.strip(), value.strip()
        if not sep or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            raise ValueError(f'.env {n}행: NAME=value 형식이 필요합니다.')
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f'.env {n}행: 따옴표를 확인하세요.')
            value = value[1:-1]
        # Existing shell environment takes priority.
        os.environ.setdefault(name, value)


def _number(value, label, minimum, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label}: 유한한 숫자가 필요합니다.')
    if value < minimum or (integer and type(value) is not int):
        raise ValueError(f'{label}: {minimum} 이상 {"정수" if integer else "숫자"}가 필요합니다.')


def load_config(path: Path) -> dict:
    with path.open('rb') as f:
        config = tomllib.load(f)
    run, models = config['run'], config['models']
    if not models:
        raise ValueError('평가 모델이 없습니다.')
    for key in ('rounds', 'max_attempts'):
        _number(run[key], key, 1, integer=True)
    for key in ('timeout_seconds', 'retry_delay_seconds', 'max_retry_delay_seconds'):
        _number(run[key], key, 0 if key != 'timeout_seconds' else 1)
    if not isinstance(run.get('output_dir'), str) or not run['output_dir']:
        raise ValueError('output_dir가 필요합니다.')
    for name, model in models.items():
        if not re.fullmatch(r'[a-z][a-z0-9_-]*', name):
            raise ValueError('모델 별칭은 영문 소문자, 숫자, 밑줄, 하이픈만 사용하세요.')
        if model['provider'] not in ('openai', 'anthropic', 'deepseek'):
            raise ValueError(f'{name}: 지원하지 않는 provider입니다.')
        for key in ('api_model', 'display_name', 'api_key_env'):
            if not isinstance(model[key], str) or not model[key].strip():
                raise ValueError(f'{name}.{key}가 필요합니다.')
        u = urlsplit(model['endpoint'])
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError(f'{name}: 인증정보가 없는 HTTPS endpoint를 사용하세요.')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', model['api_key_env']):
            raise ValueError(f'{name}: api_key_env에는 환경 변수 이름만 적으세요.')
        for key in ('max_output_tokens', 'max_output_tokens_limit'):
            _number(model[key], f'{name}.{key}', 1, integer=True)
        if model['max_output_tokens_limit'] < model['max_output_tokens']:
            raise ValueError(f'{name}: 출력 한도의 상한이 초기값보다 작습니다.')
        _number(model['weight'], f'{name}.weight', 0)
        if model['weight'] == 0:
            raise ValueError('모델 가중치는 0보다 커야 합니다.')
    if not math.isclose(sum(m['weight'] for m in models.values()), 1.0, abs_tol=1e-9):
        raise ValueError('전체 모델 가중치의 합은 1이어야 합니다.')
    return config
