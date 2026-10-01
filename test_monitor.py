import unittest, json, threading
from unittest.mock import patch
import auto_continue as app

def task(status='completed',stamp=1):
 return dict(workspace_key='w',workspace_path='C:/work',workspace_identity=None,task_id='t',title='测试',task_status=status,updated_at=stamp)
class Client:
 def __init__(self,value):self.value=value;self.closed=False
 def evaluate(self,expression):self.expression=expression;return self.value
 def close(self):self.closed=True
class StopAfterWait(threading.Event):
 def wait(self,interval):self.interval=interval;self.set();return True
class Tests(unittest.TestCase):
 def test_pin_error_target_does_not_fall_back_to_completed(self):
  wanted=task('error');other=dict(task(),task_id='other',updated_at=9)
  selected={'workspace_key':'w','task_id':'t'}
  self.assertEqual(app.resolve_target([other,wanted],selected),wanted)
 def test_missing_pin_never_falls_back(self):
  self.assertIsNone(app.resolve_target([task()],{'workspace_key':'w','task_id':'missing'}))
 def test_recent_target_not_filtered_by_success(self):
  running=task('running');completed=dict(task(),task_id='other')
  self.assertEqual(app.resolve_target([running,completed]),running)
 def test_running_poll_reports_state_and_next_check_without_sending(self):
  instance=object.__new__(app.App);instance.running=True;logs=[];instance.log=logs.append
  event=StopAfterWait()
  with patch.object(app,'read_tasks',return_value=[task('running')]),patch.object(app,'completion_key')as snapshot,patch.object(app,'send')as send:
   instance.monitor(task('running'),[],300,20,False,event)
  snapshot.assert_not_called();send.assert_not_called()
  self.assertTrue(any('运行中' in line for line in logs))
  self.assertTrue(any('300 秒后' in line for line in logs))
 def test_deleted_target_pauses_with_reason(self):
  instance=object.__new__(app.App);instance.running=True;logs=[];instance.log=logs.append
  with patch.object(app,'read_tasks',return_value=[]):instance.monitor(task('running'),[],300,20,False,StopAfterWait())
  self.assertFalse(instance.running)
  self.assertTrue(any('已删除或归档' in line for line in logs))
 def key(self,message,status='completed'):
  client=Client({'meta':{'status':status},'messages':[message]})
  with patch.object(app,'connect_service',return_value=client):result=app.completion_key(task())
  self.assertTrue(client.closed);return result
 def test_feedback_and_title_changes_not_new_completion(self):
  m={'id':'a','content':'答复','timestamp':1,'role':'assistant'}
  self.assertEqual(self.key(m),self.key(dict(m,feedback='like',title='new')))
 def test_new_assistant_reply_changes_completion_key(self):
  m={'id':'a','content':'答复','timestamp':1,'role':'assistant'}
  self.assertNotEqual(self.key(m),self.key(dict(m,id='b',content='下一步',timestamp=2)))
 def test_noncompleted_statuses_never_ready(self):
  for status in ['running','waiting','paused','error','idle']:
   self.assertIsNone(self.key({'role':'assistant','id':'a'},status))
 def test_send_failure_closes_connection(self):
  client=Client({'status':'rejected'})
  with patch.object(app,'connect_service',return_value=client):
   with self.assertRaises(RuntimeError):app.send(task(),'继续下一步')
  self.assertTrue(client.closed)
 def test_accepted_send_contains_target_and_text(self):
  client=Client({'status':'accepted'})
  with patch.object(app,'connect_service',return_value=client):app.send(task(),'继续下一步')
  self.assertIn('sendConversationCommandV4',client.expression);self.assertIn('"sessionId": "t"',client.expression)
 def monitor(self,signatures,immediate=False,status='completed'):
  instance=object.__new__(app.App);instance.running=True;instance.log=lambda _:None;event=StopAfterWait()
  with patch.object(app,'completion_key',side_effect=signatures),patch.object(app,'read_tasks',return_value=[task(status,2)]),patch.object(app,'send',return_value={'status':'accepted'})as send:
   instance.monitor(task(),[],300,1,immediate,event);return send.call_count
 def test_long_interval_catches_completed_run_without_running_poll(self):
  self.assertEqual(self.monitor(['old','new']),1)
 def test_unchanged_completion_does_not_repeat(self):
  self.assertEqual(self.monitor(['old','old']),0)
 def test_explicit_immediate_sends_existing_completion_once(self):
  self.assertEqual(self.monitor(['old','old'],immediate=True),1)
 def test_error_state_does_not_send(self):
  self.assertEqual(self.monitor(['old'],status='error'),0)
if __name__=='__main__':unittest.main()
