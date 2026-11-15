from __future__ import annotations

from collections import Counter
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from policy_eval.aggregate import aggregate, export
from policy_eval.clients import HTTPResponse, classify_response, make_request, post_json, retry_after
from policy_eval.config import load_config, load_env
from policy_eval.data import evaluation_input, load_data
from policy_eval.prompts import CRITERIA, build_prompt, response_schema
from policy_eval.runner import make_tasks, prepare, run
from policy_eval.storage import atomic_json, run_lock
from policy_eval.validation import ResponseError, validate_response

KEYS = {'ANTHROPIC_API_KEY': 'test-only-opus-key-1234', 'DEEPSEEK_API_KEY': 'test-only-deepseek-key-1234', 'OPENAI_API_KEY': 'test-only-gpt-key-1234'}


def valid_value(material, kind, score=4):
    return {
        'policy_id': material['policy']['id'],
        'scores': {c: score for c in CRITERIA[kind]},
        'reasons': {c: {'judgment': '모의 응답의 판단', 'policy_ids': [material['policy']['id']],
                       'risk_ids': [r['id'] for r in material['risks']],
                       'requirement_ids': [r['id'] for r in material['requirements']],
                       'comparison': '모의 응답의 대조 결과'} for c in CRITERIA[kind]},
        'improvements': [], 'missing_reasons': {c: '' for c in CRITERIA[kind]},
    }


def api_response(provider, value, truncated=False):
    text = json.dumps(value, ensure_ascii=False)
    if provider == 'openai':
        raw = {'model': 'gpt-6-luna', 'status': 'incomplete' if truncated else 'completed',
               'incomplete_details': {'reason': 'max_output_tokens'} if truncated else None,
               'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}],
               'usage': {'input_tokens': 12, 'output_tokens': 8}}
    elif provider == 'anthropic':
        raw = {'model': 'claude-opus-5-5', 'stop_reason': 'max_tokens' if truncated else 'end_turn',
               'content': [{'type': 'thinking', 'thinking': 'test'}, {'type': 'text', 'text': text}],
               'usage': {'input_tokens': 12, 'output_tokens': 8}}
    else:
        raw = {'model': 'deepseek-flash', 'choices': [{'finish_reason': 'length' if truncated else 'stop',
               'message': {'content': text, 'reasoning_content': 'test'}}],
               'usage': {'prompt_tokens': 12, 'completion_tokens': 8}}
    return HTTPResponse(200, json.dumps(raw, ensure_ascii=False), {'x-request-id': 'test-request'})


def payload_material(payload):
    return json.loads(payload['input'] if 'input' in payload else payload['messages'][-1]['content'])


def payload_kind(payload):
    instructions = payload.get('instructions', payload.get('system', payload.get('messages', [{}])[0].get('content', '')))
    return 'flask' if 'logical_robustness' in instructions else 'basic'


class FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, endpoint, headers, payload, timeout):
        self.calls.append((endpoint, headers, deepcopy(payload)))
        provider = 'anthropic' if 'anthropic' in endpoint else 'deepseek' if 'deepseek' in endpoint else 'openai'
        return api_response(provider, valid_value(payload_material(payload), payload_kind(payload)))


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.data = load_data(ROOT)
        self.config = load_config(ROOT / 'config.toml')
        self.config['run'].update(retry_delay_seconds=0, max_retry_delay_seconds=0)
        self.material = evaluation_input(self.data, self.data['pilot_cases'][0])

    def test_real_eight_cases_and_crosswalk(self):
        self.assertEqual(len(self.data['pilot_cases']), 8)
        self.assertEqual(len(self.data['indexes']['policies']), 12)
        self.assertEqual(self.data['pilot_cases'][0]['requirement_ids'], ['SR-008'])
        self.assertEqual(self.data['indexes']['requirements']['SR-008']['risk_ids'], ['RSK-07'])
        original, bad = [evaluation_input(self.data, self.data['pilot_cases'][i]) for i in (1, 5)]
        self.assertNotEqual(original['policy']['text'], bad['policy']['text'])
        self.assertEqual(original['risks'], bad['risks'])
        self.assertEqual(original['requirements'], bad['requirements'])
        baseline, omission = [evaluation_input(self.data, self.data['pilot_cases'][i]) for i in (3, 7)]
        self.assertIn('패치가 없으면', baseline['policy']['text'])
        self.assertNotIn('패치가 없으면', omission['policy']['text'])

    def test_expected_findings_and_original_target_never_leak(self):
        for case in self.data['pilot_cases']:
            material = evaluation_input(self.data, {**case, 'expected_issue': 'DO_NOT_SEND', 'case_type': 'DO_NOT_SEND'})
            text = json.dumps(material, ensure_ascii=False)
            self.assertNotIn('DO_NOT_SEND', text)
            self.assertNotIn('case_id', text)
            self.assertNotIn('source', text)
            self.assertNotIn(case['policy_id'], [p['id'] for p in material['related_policies']])
        for kind in CRITERIA:
            self.assertNotIn('expected_findings', build_prompt(ROOT, kind))

    def test_request_counts_and_fresh_context(self):
        models = list(self.config['models'])
        self.assertEqual(len(make_tasks(self.data['pilot_cases'], models, list(CRITERIA), 1)), 48)
        self.assertEqual(len(make_tasks(self.data['pilot_cases'], models, list(CRITERIA), 3)), 144)
        for model in self.config['models'].values():
            payload, headers = make_request(model, 'instruction', self.material, response_schema('basic'), 12000)
            self.assertNotIn('Authorization', headers)
            self.assertNotIn('previous_response_id', payload)
            self.assertNotIn('conversation', payload)
            if model['provider'] == 'openai':
                self.assertFalse(payload['store'])
            else:
                self.assertEqual([m['role'] for m in payload['messages']].count('assistant'), 0)
            self.assertEqual(payload_material(payload), self.material)

    def test_strict_json_and_score_validation(self):
        good = valid_value(self.material, 'basic')
        self.assertEqual(validate_response(json.dumps(good), 'basic', self.material), good)
        for score in (True, False, 0, 6, 4.0, '4'):
            bad = deepcopy(good)
            bad['scores']['coherence'] = score
            with self.assertRaises(ResponseError):
                validate_response(json.dumps(bad), 'basic', self.material)
        for modifier in (
            lambda v: v.update(policy_id='POL-OTHER'),
            lambda v: v['scores'].pop('relevance'),
            lambda v: v['reasons']['coherence'].update(policy_ids=['POL-OTHER']),
            lambda v: v['reasons']['coherence'].update(comparison=''),
            lambda v: v.update(unexpected='extra'),
        ):
            bad = deepcopy(good)
            modifier(bad)
            with self.assertRaises(ResponseError):
                validate_response(json.dumps(bad), 'basic', self.material)
        for text in ('{"policy_id":"x", "policy_id":"y"}', '```json\n{}\n```', '{"scores": NaN}'):
            with self.assertRaises(ResponseError):
                validate_response(text, 'basic', self.material)

    def test_null_requires_reason_and_is_not_zero(self):
        good = valid_value(self.material, 'basic')
        good['scores']['coherence'] = None
        with self.assertRaises(ResponseError):
            validate_response(json.dumps(good), 'basic', self.material)
        good['missing_reasons']['coherence'] = '적용 조건 자료가 없음'
        self.assertIsNone(validate_response(json.dumps(good), 'basic', self.material)['scores']['coherence'])

    def test_provider_completion_signals(self):
        for provider in ('openai', 'anthropic', 'deepseek'):
            result = classify_response(provider, api_response(provider, {}, truncated=True))
            self.assertEqual(result['status'], 'truncated')
            self.assertTrue(result['retryable'])
        self.assertTrue(classify_response('openai', HTTPResponse(429, '{}', {}))['retryable'])
        self.assertTrue(classify_response('openai', HTTPResponse(401, '{}', {}))['block_model'])
        self.assertFalse(classify_response('openai', HTTPResponse(413, '{}', {}))['block_model'])
        for text in ('[]', '{}', 'broken'):
            self.assertEqual(classify_response('openai', HTTPResponse(200, text, {}))['status'], 'invalid_api_response' if text != '{}' else 'api_error')
        self.assertEqual(retry_after({'retry-after': 'NaN'}), 0)
        self.assertEqual(retry_after({'retry-after': '20'}), 20)

    def test_known_weighted_scores_and_no_partial_renormalization(self):
        tasks = make_tasks(self.data['pilot_cases'][:1], list(self.config['models']), ['basic', 'flask'], 1)
        manifest, _ = prepare(ROOT, self.config, self.data, tasks)
        values = {'opus': [5, 4, 4, 3], 'deepseek': [4, 4, 4, 4], 'gpt': [3, 3, 3, 3]}
        results = {}
        for t in tasks:
            value = valid_value(self.material, t.kind)
            if t.kind == 'basic':
                value['scores'] = dict(zip(CRITERIA[t.kind], values[t.model]))
            results[t.id] = {'status': 'complete', 'evaluation': value}
        report = aggregate(manifest, results)
        self.assertAlmostEqual(report['representatives'][0]['basic_100'], 68.75)
        self.assertEqual(report['representatives'][1]['scores'], {c: 4 for c in CRITERIA['flask']})
        self.assertIsNone(report['representatives'][1]['basic_100'])
        missing = tasks[0].id
        results[missing]['status'] = 'insufficient_material'
        self.assertIsNone(aggregate(manifest, results)['representatives'][0]['basic_100'])
        del results[missing]
        self.assertIsNone(aggregate(manifest, results)['representatives'][0]['scores'])

    @patch.dict(os.environ, KEYS)
    def test_full_pilot_48_calls_export_and_resume_zero_calls(self):
        tasks = make_tasks(self.data['pilot_cases'], list(self.config['models']), list(CRITERIA), 1)
        manifest, requests = prepare(ROOT, self.config, self.data, tasks)
        fake = FakeTransport()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            results = run(ROOT, folder, self.config, tasks, manifest, requests, transport=fake, sleep=lambda _: None, emit=lambda _: None)
            self.assertEqual(len(fake.calls), 48)
            self.assertEqual(Counter(r['status'] for r in results.values()), {'complete': 48})
            report = export(ROOT, folder, manifest, results)
            self.assertEqual(report['task_statuses'], {'complete': 48})
            self.assertEqual(len(report['representatives']), 16)
            self.assertEqual(len(list((folder / 'attempts').glob('*/*.json'))), 48)
            for name in ('summary.csv', 'model_scores.csv', 'case_comparison.csv', 'usage.csv', 'round_scores.csv'):
                self.assertTrue((folder / name).stat().st_size)
            run(ROOT, folder, self.config, tasks, manifest, requests, resume=True, transport=fake, emit=lambda _: None)
            self.assertEqual(len(fake.calls), 48)
            for p in folder.rglob('*.json'):
                text = p.read_text(encoding='utf-8')
                for key in KEYS.values():
                    self.assertNotIn(key, text)
            # All evaluators get exactly the same instructions and material for a case/task.
            for i in range(0, 48, 3):
                payloads = [call[2] for call in fake.calls[i:i + 3]]
                self.assertEqual(payload_material(payloads[0]), payload_material(payloads[1]))
                self.assertEqual(payload_material(payloads[1]), payload_material(payloads[2]))
                self.assertEqual(payloads[0]['system'], payloads[1]['messages'][0]['content'])
                self.assertEqual(payloads[0]['system'], payloads[2]['instructions'])

    @patch.dict(os.environ, KEYS)
    def test_truncation_grows_budget_without_new_round(self):
        tasks = make_tasks(self.data['pilot_cases'][:1], ['gpt'], ['basic'], 1)
        manifest, requests = prepare(ROOT, self.config, self.data, tasks)
        budgets = []
        def fake(endpoint, headers, payload, timeout):
            budgets.append(payload['max_output_tokens'])
            value = valid_value(payload_material(payload), 'basic')
            return api_response('openai', value, truncated=len(budgets) == 1)
        with tempfile.TemporaryDirectory() as tmp:
            results = run(ROOT, Path(tmp), self.config, tasks, manifest, requests, transport=fake, sleep=lambda _: None, emit=lambda _: None)
            self.assertEqual(budgets, [16384, 32768])
            self.assertEqual(results[tasks[0].id]['round'], 1)
            self.assertEqual(results[tasks[0].id]['attempt'], 2)

    @patch.dict(os.environ, KEYS)
    def test_auth_error_stops_only_affected_model_and_redacts_error(self):
        tasks = make_tasks(self.data['pilot_cases'][:2], list(self.config['models']), ['basic'], 1)
        manifest, requests = prepare(ROOT, self.config, self.data, tasks)
        fake = FakeTransport()
        def transport(endpoint, headers, payload, timeout):
            if 'anthropic' in endpoint:
                fake.calls.append((endpoint, headers, payload))
                return HTTPResponse(401, json.dumps({'error': KEYS['ANTHROPIC_API_KEY']}), {})
            return fake(endpoint, headers, payload, timeout)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            results = run(ROOT, folder, self.config, tasks, manifest, requests, transport=transport, emit=lambda _: None)
            self.assertEqual(len(fake.calls), 5)
            self.assertEqual(sum(r['status'] == 'complete' for r in results.values()), 4)
            first = json.loads(next((folder / 'attempts' / tasks[0].id).glob('*.json')).read_text(encoding='utf-8'))
            self.assertIn('[REDACTED]', first['raw_response'])

    @patch.dict(os.environ, KEYS)
    def test_failed_only_resume_and_configuration_mismatch(self):
        tasks = make_tasks(self.data['pilot_cases'][:2], ['gpt'], ['basic'], 1)
        self.config['run']['max_attempts'] = 1
        manifest, requests = prepare(ROOT, self.config, self.data, tasks)
        called = []
        fake = FakeTransport()
        def transport(endpoint, headers, payload, timeout):
            called.append(1)
            if len(called) == 1:
                raise TimeoutError()
            return fake(endpoint, headers, payload, timeout)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            result = run(ROOT, folder, self.config, tasks, manifest, requests, transport=transport, emit=lambda _: None)
            self.assertEqual(Counter(r['status'] for r in result.values()), {'network_error': 1, 'complete': 1})
            run(ROOT, folder, self.config, tasks, manifest, requests, resume=True, transport=transport, emit=lambda _: None)
            self.assertEqual(len(called), 3)
            bad = deepcopy(manifest)
            bad['fingerprint'] = 'different'
            with self.assertRaisesRegex(ValueError, '다릅니다'):
                run(ROOT, folder, self.config, tasks, bad, requests, resume=True, transport=transport, emit=lambda _: None)
            self.assertEqual(len(called), 3)

    @patch.dict(os.environ, KEYS)
    def test_null_is_saved_without_pointless_retry(self):
        tasks = make_tasks(self.data['pilot_cases'][:1], ['gpt'], ['basic'], 1)
        manifest, requests = prepare(ROOT, self.config, self.data, tasks)
        called = []
        def fake(endpoint, headers, payload, timeout):
            called.append(1)
            value = valid_value(payload_material(payload), 'basic')
            value['scores']['coherence'] = None
            value['missing_reasons']['coherence'] = '필요 자료 없음'
            return api_response('openai', value)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            results = run(ROOT, folder, self.config, tasks, manifest, requests, transport=fake, emit=lambda _: None)
            self.assertEqual(results[tasks[0].id]['status'], 'insufficient_material')
            run(ROOT, folder, self.config, tasks, manifest, requests, resume=True, transport=fake, emit=lambda _: None)
            self.assertEqual(len(called), 1)

    def test_env_is_not_executed_and_shell_value_wins(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'POLICY_EVAL_TEST': 'shell'}):
            path = Path(tmp) / '.env'
            path.write_text('POLICY_EVAL_TEST=file\nPOLICY_EVAL_LITERAL="$(not-a-command)"\n', encoding='utf-8')
            load_env(path)
            self.assertEqual(os.environ['POLICY_EVAL_TEST'], 'shell')
            self.assertEqual(os.environ['POLICY_EVAL_LITERAL'], '$(not-a-command)')

    def test_run_lock_prevents_concurrent_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with run_lock(Path(tmp)):
                with self.assertRaises(ValueError):
                    with run_lock(Path(tmp)):
                        pass
            with run_lock(Path(tmp)):
                pass

    @patch.dict(os.environ, KEYS)
    def test_three_round_144_call_integration(self):
        self.config['run']['rounds'] = 3
        tasks = make_tasks(self.data['pilot_cases'], list(self.config['models']), list(CRITERIA), 3)
        manifest, requests = prepare(ROOT, self.config, self.data, tasks)
        fake = FakeTransport()
        with tempfile.TemporaryDirectory() as tmp:
            results = run(ROOT, Path(tmp), self.config, tasks, manifest, requests, transport=fake, emit=lambda _: None)
            report = aggregate(manifest, results)
            self.assertEqual(len(fake.calls), 144)
            self.assertEqual(len(report['rounds']), 48)
            self.assertEqual(len(report['representatives']), 16)
            self.assertTrue(all(r['rounds_complete'] == 3 for r in report['representatives']))
            self.assertTrue(all(r['basic_100'] == 75 for r in report['representatives'] if r['kind'] == 'basic'))

    def test_http_transport_posts_json_and_preserves_failure_body(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append((self.path, self.headers.get('Authorization'), payload))
                self.send_response(429)
                self.send_header('Retry-After', '7')
                self.end_headers()
                self.wfile.write(b'{"error":"rate limit"}')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            response = post_json(f'http://127.0.0.1:{server.server_port}/test',
                                 {'Content-Type': 'application/json', 'Authorization': 'Bearer test'}, {'text': '한국어'}, 5)
            self.assertEqual(response.status, 429)
            self.assertEqual(response.headers['retry-after'], '7')
            self.assertIn('rate limit', response.body)
            self.assertEqual(received, [('/test', 'Bearer test', {'text': '한국어'})])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    @patch.dict(os.environ, KEYS)
    def test_cli_selected_dry_run_and_resume_infers_selection(self):
        from policy_eval import cli
        real_run = run
        fake = FakeTransport()
        def delegated(*args, **kwargs):
            return real_run(*args, **kwargs, transport=fake, emit=lambda _: None)
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / 'config.toml'
            text = (ROOT / 'config.toml').read_text(encoding='utf-8')
            text = text.replace('output_dir = "outputs"', 'output_dir = ' + json.dumps(Path(tmp).as_posix()))
            config_path.write_text(text, encoding='utf-8')
            with patch('sys.stdout', new=io.StringIO()), patch('policy_eval.cli.load_env'), patch('policy_eval.cli.run', side_effect=delegated):
                self.assertEqual(cli.main(['--config', str(config_path), '--dry-run', '--cases', 'PIL-02', '--models', 'gpt', '--tasks', 'flask', '--rounds', '3']), 0)
                folder = next(Path(tmp).glob('dryrun_*'))
                self.assertEqual(len(fake.calls), 0)
                self.assertEqual(cli.main(['--config', str(config_path), '--resume', str(folder)]), 0)
                self.assertEqual(len(fake.calls), 3)
                report = json.loads((folder / 'summary.json').read_text(encoding='utf-8'))
                # A single selected model never gets promoted to the full weight.
                self.assertTrue(all(r['scores'] is None for r in report['representatives']))


if __name__ == '__main__':
    unittest.main()
