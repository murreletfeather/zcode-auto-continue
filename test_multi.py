import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import auto_continue as backend
import multi_continue as multi


def task(name, status='completed'):
    return {'workspace_key': 'workspace-' + name, 'workspace_path': 'C:/work/' + name,
            'workspace_identity': None, 'task_id': name, 'title': '对话' + name,
            'task_status': status, 'updated_at': 1}


class Tests(unittest.TestCase):
    def make_runner(self, rows, immediate=False, limit=20):
        entries = [{'task': task('a'), 'modelSelection': {'providerId': 'minimax', 'modelId': 'MiniMax-M3'}},
                   {'task': task('b'), 'modelSelection': {'providerId': 'xiaomi-mimo', 'modelId': 'mimo-v2.6-pro'}}]
        logs, sent = [], []
        reader = lambda: copy.deepcopy(rows)
        snapshot = lambda t: 'reply-' + t['task_id']
        sender = lambda t, text, model: sent.append((t['task_id'], text, model))
        runner = multi.MonitorRunner(entries, 300, limit, immediate, threading.Event(), logs.append,
                                     reader, snapshot, sender)
        return runner, logs, sent

    def test_two_targets_keep_their_models_and_do_not_repeat(self):
        runner, logs, sent = self.make_runner([task('a'), task('b')], immediate=True)
        runner.initialize()
        runner.check_once()
        runner.check_once()
        self.assertEqual([s[0] for s in sent], ['a', 'b'])
        self.assertEqual(sent[0][2]['providerId'], 'minimax')
        self.assertEqual(sent[1][2]['providerId'], 'xiaomi-mimo')
        self.assertEqual([s['count'] for s in runner.states.values()], [1, 1])

    def test_one_send_failure_does_not_stop_other_target_or_retry(self):
        runner, logs, sent = self.make_runner([task('a'), task('b')], immediate=True)
        def sender(t, text, model):
            if t['task_id'] == 'a':
                raise RuntimeError('提交响应未知')
            sent.append(t['task_id'])
        runner.sender = sender
        runner.initialize()
        runner.check_once()
        runner.check_once()
        self.assertEqual(sent, ['b'])
        self.assertTrue(runner.states[multi.identity(task('a'))]['paused'])
        self.assertFalse(runner.states[multi.identity(task('b'))]['paused'])

    def test_error_and_running_are_not_sent(self):
        runner, logs, sent = self.make_runner([task('a', 'error'), task('b', 'running')], immediate=True)
        runner.initialize()
        runner.check_once()
        self.assertEqual(sent, [])

    def test_limit_is_per_target(self):
        runner, logs, sent = self.make_runner([task('a'), task('b')], immediate=True, limit=1)
        runner.initialize()
        runner.check_once()
        self.assertEqual(len(sent), 2)
        self.assertTrue(all(s['paused'] for s in runner.states.values()))

    def test_missing_target_does_not_switch_to_another(self):
        runner, logs, sent = self.make_runner([task('b')], immediate=True)
        runner.initialize()
        runner.check_once()
        self.assertEqual([s[0] for s in sent], ['b'])
        self.assertTrue(runner.states[multi.identity(task('a'))]['paused'])

    def test_pause_after_snapshot_prevents_send(self):
        runner, logs, sent = self.make_runner([task('a'), task('b')], immediate=True)
        runner.initialize()
        def snapshot(t):
            runner.stop.set()
            return 'new'
        runner.snapshot = snapshot
        runner.check_once()
        self.assertEqual(sent, [])

    def test_state_change_just_before_send_does_not_submit(self):
        runner, logs, sent = self.make_runner([task('a'), task('b')], immediate=True)
        runner.initialize()
        reads = iter([[task('a'), task('b')], [task('a', 'running'), task('b')],
                      [task('a', 'running'), task('b')]])
        runner.reader = lambda: next(reads)
        runner.check_once()
        self.assertEqual([s[0] for s in sent], ['b'])

    def test_new_completion_between_polls_is_detected_independently(self):
        runner, logs, sent = self.make_runner([task('a'), task('b')])
        runner.initialize()
        runner.snapshot = lambda t: 'new-a' if t['task_id'] == 'a' else 'reply-b'
        runner.check_once()
        self.assertEqual([s[0] for s in sent], ['a'])

    def test_model_selection_is_inside_send_command(self):
        class Client:
            def evaluate(self, expression):
                self.expression = expression
                return {'status': 'accepted'}
            def close(self): pass
        client = Client()
        selection = {'providerId': 'minimax', 'modelId': 'MiniMax-M3',
                     'options': {'reasoningLevel': 'enabled'}}
        with patch.object(backend, 'connect_service', return_value=client):
            backend.send(task('a'), '继续下一步', selection)
        payload = json.loads(client.expression.split('const r=', 1)[1].split(';r.envelope', 1)[0])
        self.assertEqual(payload['envelope']['sessionId'], 'a')
        self.assertEqual(payload['envelope']['payload']['modelSelection'], selection)

    def test_config_restores_multiple_targets_and_reasoning(self):
        value = {'entries': [{'task': task('a'), 'modelSelection': None},
                            {'task': task('b'), 'modelSelection': {'providerId': 'minimax',
                             'modelId': 'MiniMax-M3', 'options': {'reasoningLevel': 'enabled'}}}]}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(multi, 'CONFIG', Path(directory) / 'settings.json'):
                multi.save_config(value)
                self.assertEqual(multi.load_config(), value)


if __name__ == '__main__':
    unittest.main()
