import hashlib, json, sys, sqlite3, subprocess, time, uuid, threading, queue
from pathlib import Path
from urllib.request import urlopen
import tkinter as tk
from tkinter import ttk, messagebox
import websocket
DB=Path.home()/'.zcode/v2/tasks-index.sqlite'
PORT=19387
EXE=Path(r'D:\m_Applications\ZCode\ZCode.exe')
SETTINGS=Path.home()/'Documents/ZCodeAutoContinue/settings.json'
def load_target():
 try:return json.loads(SETTINGS.read_text(encoding='utf8')).get('target')
 except (OSError,ValueError):return None
def save_target(task):
 SETTINGS.parent.mkdir(parents=True,exist_ok=True)
 SETTINGS.write_text(json.dumps({'target':{'workspace_key':task['workspace_key'],'task_id':task['task_id']}},ensure_ascii=False,indent=2),encoding='utf8')
def resolve_target(tasks,target=None):
 if target:
  return next((t for t in tasks if t['workspace_key']==target['workspace_key'] and t['task_id']==target['task_id']),None)
 return tasks[0]if tasks else None
DISCOVER=r'''(()=>{const seen=new Set(),stack=[];
for(const el of document.querySelectorAll('*')){for(const k of Object.keys(el))if(k.startsWith('__reactContainer$')||k.startsWith('__reactFiber$'))stack.push(el[k]);if(stack.length)break;}
let count=0;while(stack.length&&count++<100000){const f=stack.pop();if(!f||seen.has(f))continue;seen.add(f);
const candidates=[f.memoizedProps?.value,f.memoizedProps?.services];
for(let d=f.dependencies?.firstContext;d;d=d.next)candidates.push(d.memoizedValue);
for(const c of candidates)if(c&&Object.prototype.hasOwnProperty.call(c,'zcodeAgentService')){window.__zcodeContinueService=c.zcodeAgentService;window.__zcodeContinueServices=c;return true;}
stack.push(f.child,f.sibling,f.return,f.current);}return false;})()'''
class CDP:
 def __init__(self,target):
  self.ws=websocket.create_connection(target['webSocketDebuggerUrl'],timeout=15,suppress_origin=True);self.counter=0
 def evaluate(self,expression):
  self.counter+=1;ident=self.counter
  self.ws.send(json.dumps({'id':ident,'method':'Runtime.evaluate','params':{'expression':expression,'awaitPromise':True,'returnByValue':True}}))
  while True:
   response=json.loads(self.ws.recv())
   if response.get('id')!=ident:continue
   if 'error'in response:raise RuntimeError(str(response['error']))
   result=response['result']
   if 'exceptionDetails'in result:raise RuntimeError(result['exceptionDetails'].get('exception',{}).get('description',str(result['exceptionDetails'])))
   return result.get('result',{}).get('value')
 def close(self):self.ws.close()
def connect_service():
 with urlopen(f'http://127.0.0.1:{PORT}/json/list',timeout=3)as response:targets=json.load(response)
 for target in targets:
  if target.get('type')!='page' or not ('/renderer/'in target.get('url','') or 'index.html'in target.get('url','')):continue
  client=CDP(target)
  try:
   if client.evaluate(DISCOVER):return client
  except Exception:client.close();raise
  client.close()
 raise RuntimeError('未找到消息服务，请打开目标工作区；升级后接口可能改变')
def read_tasks(db=DB):
 # 只读访问，读取 SQLite 已提交的 WAL，不直接改写 ZCode 数据。
 with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)as c:
  c.row_factory=sqlite3.Row
  return [dict(r)for r in c.execute('SELECT workspace_key,workspace_path,workspace_identity,task_id,title,task_status,updated_at FROM tasks WHERE deleted=0 AND archived=0 ORDER BY updated_at DESC')]
def send(task,text,model_selection=None):
 c=connect_service()
 command={'commandId':str(uuid.uuid4()),'clientId':'zcode-auto-continue','sessionId':task['task_id'],'type':'sendText','payload':{'text':text,'requestedDelivery':'startNow'},'issuedAt':int(time.time()*1000)}
 if model_selection is not None:
  command['payload']['modelSelection']=json.loads(json.dumps(model_selection))
 request={'workspacePath':task['workspace_path'],'envelope':command}
 if task.get('workspace_identity'):request['workspaceIdentity']=task['workspace_identity']
 try:
  expression='''(async()=>{const r=%s;r.envelope.clientId=localStorage.getItem('zcode-v4-client-id:v1')||r.envelope.clientId;return await window.__zcodeContinueService.sendConversationCommandV4(r);})()'''%json.dumps(request,ensure_ascii=True)
  result=c.evaluate(expression)
  if not isinstance(result,dict)or result.get('status')!='accepted':raise RuntimeError('消息未被接受：'+json.dumps(result,ensure_ascii=False))
  return result
 finally:c.close()
def completion_key(task):
 c=connect_service()
 request={'workspacePath':task['workspace_path'],'taskId':task['task_id'],'messageLimit':1}
 if task.get('workspace_identity'):request['workspaceIdentity']=task['workspace_identity']
 try:
  snapshot=c.evaluate('(async()=>await window.__zcodeContinueServices.zcodeTaskService.getTaskSnapshot(%s))()'%json.dumps(request))
  if not snapshot or snapshot.get('meta',{}).get('status')!='completed':return None
  messages=[m for m in snapshot.get('messages',[])if m.get('role')=='assistant']
  if not messages:return None
  last=messages[-1]
  # 反馈、标题、模型配置的修改不代表完成了新一轮；只比较末条回复身份与正文。
  payload={k:last.get(k)for k in ('id','content','timestamp')}
  return hashlib.sha256(json.dumps(payload,ensure_ascii=True,sort_keys=True).encode()).hexdigest()
 finally:c.close()

class App:
 def __init__(self,root):
  self.root=root;root.title('ZCode 自动继续（零 token 监控）');root.geometry('780x500')
  self.events=queue.Queue();self.stop_event=threading.Event();self.running=False;self.tasks=[];self.saved_target=load_target()
  box=ttk.Frame(root,padding=14);box.pack(fill='both',expand=True)
  ttk.Label(box,text='成功完成后自动发送“继续下一步”；错误、额度限制、待确认状态不续发。').pack(anchor='w')
  ttk.Label(box,text='首次使用：正常退出 ZCode → 以本地接口模式打开 → 打开目标工作区。').pack(anchor='w',pady=6)
  ttk.Button(box,text='以本地接口模式打开 ZCode',command=self.launch).pack(anchor='w')
  self.target=ttk.Combobox(box,state='readonly',width=95);self.target.pack(fill='x',pady=8)
  self.target.bind('<<ComboboxSelected>>',lambda _:self.select_target())
  row=ttk.Frame(box);row.pack(fill='x')
  ttk.Label(row,text='检查间隔（秒）').pack(side='left');self.interval=tk.StringVar(value='300');ttk.Entry(row,textvariable=self.interval,width=5).pack(side='left')
  ttk.Label(row,text='   续发上限（0=不限）').pack(side='left');self.limit=tk.StringVar(value='20');ttk.Entry(row,textvariable=self.limit,width=5).pack(side='left')
  self.immediate=tk.BooleanVar(value=True);ttk.Checkbutton(box,text='开始时立即续发所选对话（仅在已成功完成时）',variable=self.immediate).pack(anchor='w',pady=5)
  row=ttk.Frame(box);row.pack(fill='x',pady=5)
  for title,func in [('开始监控',self.start),('暂停',self.stop),('刷新列表',self.refresh),('检查接口（不发送）',lambda:self.background(self.probe))]:ttk.Button(row,text=title,command=func).pack(side='left',padx=4)
  self.logbox=tk.Text(box,height=14,wrap='word',state='disabled');self.logbox.pack(fill='both',expand=True,pady=8)
  root.protocol('WM_DELETE_WINDOW',self.close);self.refresh();root.after(150,self.drain)
  self.log('按保存的对话 ID 锁定目标；不因状态或其他对话更新切换目标。检测到接口后自动开始。')
  self.autostart=True;root.after(2000,self.auto_start)
 def auto_start(self):
  if not self.autostart:return
  try:
   with urlopen(f'http://127.0.0.1:{PORT}/json/list',timeout=0.4)as r:json.load(r)
  except Exception:self.root.after(2000,self.auto_start);return
  self.autostart=False;self.start()
 def log(self,text):self.events.put(time.strftime('%H:%M:%S ')+str(text))
 def drain(self):
  while not self.events.empty():
   self.logbox.config(state='normal');self.logbox.insert('end',self.events.get()+'\n');self.logbox.see('end');self.logbox.config(state='disabled')
  self.root.after(150,self.drain)
 def background(self,func):threading.Thread(target=func,daemon=True).start()
 def select_target(self):
  self.stop()
  index=self.target.current()
  self.saved_target=({'workspace_key':self.tasks[index-1]['workspace_key'],'task_id':self.tasks[index-1]['task_id']}if index>0 else None)
  SETTINGS.parent.mkdir(parents=True,exist_ok=True)
  SETTINGS.write_text(json.dumps({'target':self.saved_target},ensure_ascii=False,indent=2),encoding='utf8')
 def refresh(self):
  if self.running:return
  try:
   self.tasks=read_tasks();self.target['values']=['最近活动的对话（开始时锁定，不按成功状态筛选）']+[t['title']+' ['+t['task_id'][-8:]+'] '+t['task_status']+' — '+t['workspace_path']for t in self.tasks]
   chosen=resolve_target(self.tasks,self.saved_target)
   index=next((i+1 for i,t in enumerate(self.tasks)if chosen and t['task_id']==chosen['task_id'] and t['workspace_key']==chosen['workspace_key']),0)if self.saved_target else 0
   self.target.current(index)
   if self.saved_target and not chosen:self.log('保存的目标不存在或已归档，不会自动切换到其他对话。')
  except Exception as e:self.log('读取失败：'+str(e))
 def launch(self):
  if not EXE.exists():messagebox.showerror('路径错误',str(EXE));return
  r=subprocess.run(['tasklist','/FI','IMAGENAME eq ZCode.exe','/FO','CSV'],capture_output=True,text=True,creationflags=0x08000000)
  if 'zcode.exe'in r.stdout.lower():messagebox.showinfo('请先退出 ZCode','请先正常退出 ZCode，本工具不会强行终止任务。');return
  subprocess.Popen([str(EXE),f'--remote-debugging-port={PORT}','--remote-debugging-address=127.0.0.1'],creationflags=0x08000000)
  self.log('已启动 ZCode，本地接口只供本机访问，请打开目标工作区。');self.autostart=True;self.root.after(5000,self.auto_start)
 def probe(self):
  try:c=connect_service();c.close();self.log('接口检查通过，未发送消息。')
  except Exception as e:self.log('接口不可用：'+str(e))
 def start(self):
  if self.running:return
  try:
   interval=max(1,float(self.interval.get()));limit=int(self.limit.get())
   if limit<0:raise ValueError('上限不能为负数')
   tasks=read_tasks();index=self.target.current()
   selected=({'workspace_key':self.tasks[index-1]['workspace_key'],'task_id':self.tasks[index-1]['task_id']}if index>0 else self.saved_target)
   chosen=resolve_target(tasks,selected)
   if not chosen:raise ValueError('指定对话不存在或已归档，请重新选择目标')
   save_target(chosen);self.saved_target={'workspace_key':chosen['workspace_key'],'task_id':chosen['task_id']}
  except Exception as e:messagebox.showerror('无法开始',str(e));return
  immediate=self.immediate.get();self.running=True;self.stop_event=threading.Event();stop_event=self.stop_event
  self.log('监控目标：'+chosen['title']+' ['+chosen['task_id']+']；当前状态：'+chosen['task_status'])
  if chosen['task_status']!='completed':self.log('保持锁定此对话，等待成功完成；不会转去其他已完成对话。')
  self.background(lambda:self.monitor(chosen,tasks,interval,limit,immediate,stop_event))
 def monitor(self,chosen,tasks,interval,limit,immediate,stop_event):
  key=(chosen['workspace_key'],chosen['task_id']);count=0
  try:
   previous=completion_key(chosen)if chosen['task_status']=='completed'else None
   initial=immediate and previous is not None
   while not stop_event.is_set():
    current=next((r for r in read_tasks()if(r['workspace_key'],r['task_id'])==key),None)
    if not current:raise RuntimeError('目标对话已删除或归档，请重新选择')
    state=current['task_status']
    labels={'running':'运行中，等待结束','error':'错误结束，等待恢复','waiting':'等待确认','paused':'对话已暂停','idle':'尚未运行','completed':'已成功完成'}
    self.log('检查结果：'+labels.get(state,state)+' ['+state+']')
    if current and current['task_status']=='completed':
     signature=completion_key(current)
     if signature and(initial or signature!=previous):
      initial=False;previous=signature # 不自动重试不确定的发送结果，以免重复提交。
      latest=next((r for r in read_tasks()if(r['workspace_key'],r['task_id'])==key),None)
      if latest and latest['task_status']=='completed'and not stop_event.is_set():
       self.log('发送中：继续下一步');send(current,'继续下一步');count+=1;self.log('ZCode 已接受；累计续发 '+str(count)+' 次')
       if limit and count>=limit:self.log('达到上限，已暂停。');break
     elif signature:self.log('末条回复没有变化，本次不重复发送。')
     else:self.log('尚未取得成功完成的回复，继续等待。')
    self.log('下次检查：'+time.strftime('%H:%M:%S',time.localtime(time.time()+interval))+'（'+str(int(interval))+' 秒后）')
    if stop_event.wait(interval):break
  except Exception as e:self.log('已暂停：'+str(e)+'；检查接口后可重新开始。')
  finally:self.running=False
 def stop(self):self.autostart=False;self.stop_event.set();self.log('已请求暂停；已经提交的任务由 ZCode 继续执行。')
 def close(self):self.stop_event.set();self.root.destroy()
if __name__=='__main__':
 root=tk.Tk();App(root);root.mainloop()
