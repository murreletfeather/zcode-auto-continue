"""多对话监控：每个目标独立去重、选择模型、计数和暂停。"""
import copy
import json
import math
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox, filedialog
import auto_continue as backend

VERSION = '0.1.0'
CONFIG = backend.SETTINGS.with_name('settings.multi.json')


def identity(task):
    return task['workspace_key'], task['task_id']


def read_models():
    client = backend.connect_service()
    try:
        # 在界面进程里过滤，API Key 不经过调试连接，也不保存完整配置。
        return client.evaluate('''(async()=>{
            const v=await window.__zcodeContinueServices.modelSelectionService.getView();
            return v.providers.filter(p=>p.config?.access?.entitled!==false).flatMap(p=>
                p.models.filter(m=>m.config?.enabled!==false).map(m=>({
                    providerId:p.providerId,providerName:p.providerName,modelId:m.modelId,
                    reasoningLevels:m.config?.optionSpecs?.reasoningLevel?.values||[]
                })));
        })()''')
    finally:
        client.close()


def load_config():
    try:
        value = json.loads(CONFIG.read_text(encoding='utf8'))
        if not isinstance(value.get('entries'), list):
            raise ValueError('监控配置缺少 entries 列表')
        return value
    except FileNotFoundError:
        # 迁移旧版保存的明确目标，不自动选择另一个对话。
        target = backend.load_target()
        task = backend.resolve_target(backend.read_tasks(), target) if target else None
        return {'entries': [{'task': task, 'modelSelection': None}] if task else [],
                'interval': 300, 'limit': 20, 'immediate': False, 'autostart': False}


def save_config(value):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temp = CONFIG.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    temp.replace(CONFIG)


class MonitorRunner:
    def __init__(self, entries, interval, limit, immediate, stop, emit,
                 reader=backend.read_tasks, snapshot=backend.completion_key, sender=backend.send):
        self.entries = copy.deepcopy(entries)
        self.interval, self.limit, self.immediate = interval, limit, immediate
        self.stop, self.emit = stop, emit
        self.reader, self.snapshot, self.sender = reader, snapshot, sender
        self.states = {identity(e['task']): {'previous': None, 'initial': False,
                       'count': 0, 'paused': False} for e in self.entries}

    def update(self, entry, status, message):
        state = self.states[identity(entry['task'])]
        self.emit({'key': identity(entry['task']), 'status': status,
                   'count': state['count'], 'message': message})

    def initialize(self):
        rows = {identity(t): t for t in self.reader()}
        for entry in self.entries:
            if self.stop.is_set():
                return
            state = self.states[identity(entry['task'])]
            current = rows.get(identity(entry['task']))
            try:
                if not current:
                    raise RuntimeError('对话不存在或已归档')
                if current['task_status'] == 'completed':
                    state['previous'] = self.snapshot(current)
                    state['initial'] = self.immediate and state['previous'] is not None
            except Exception as exc:
                state['paused'] = True
                self.update(entry, '已暂停', str(exc))

    def check_once(self):
        rows = {identity(t): t for t in self.reader()}
        for entry in self.entries:
            if self.stop.is_set():
                break
            key = identity(entry['task'])
            state = self.states[key]
            if state['paused']:
                continue
            try:
                current = rows.get(key)
                if not current:
                    raise RuntimeError('对话不存在或已归档')
                status = current['task_status']
                labels = {'running': '运行中', 'error': '错误，等待恢复', 'waiting': '等待确认',
                          'paused': '对话已暂停', 'idle': '尚未运行', 'completed': '成功完成'}
                self.update(entry, labels.get(status, status), '检查状态：' + status)
                if status != 'completed':
                    continue
                signature = self.snapshot(current)
                if not signature or (not state['initial'] and signature == state['previous']):
                    self.update(entry, '等待新回复', '本次没有新的成功完成回复，不重复发送')
                    continue
                # 每个目标临发送前再次确认；一个目标失败不影响其他目标。
                latest = next((t for t in self.reader() if identity(t) == key), None)
                if not latest or latest['task_status'] != 'completed' or self.stop.is_set():
                    continue
                self.sender(current, '继续下一步', entry.get('modelSelection'))
                state['previous'], state['initial'] = signature, False
                state['count'] += 1
                self.update(entry, '已发送，等待执行', 'ZCode 已接受“继续下一步”')
                if self.limit and state['count'] >= self.limit:
                    state['paused'] = True
                    self.update(entry, '达到上限', '达到此对话的续发上限')
            except Exception as exc:
                # 不确定的提交结果不自动重试，避免重复触发实际任务。
                state['paused'] = True
                self.update(entry, '已暂停', str(exc))

    def run(self):
        try:
            self.initialize()
            while not self.stop.is_set():
                self.check_once()
                if all(s['paused'] for s in self.states.values()):
                    break
                self.emit({'message': '下次检查：' + time.strftime('%H:%M:%S',
                           time.localtime(time.time() + self.interval)), 'key': None})
                if self.stop.wait(self.interval):
                    break
        except Exception as exc:
            self.emit({'key': None, 'message': '监控暂停：' + str(exc)})
        finally:
            self.emit({'done': True})


class App:
    def __init__(self, root):
        self.root = root
        root.title('ZCode 自动继续 · 多对话与模型选择 v' + VERSION)
        root.geometry('1120x760')
        root.minsize(880, 620)
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.running = False
        self.tasks, self.models = [], []
        self.entries = []
        self.row_keys = {}
        try:
            config = load_config()
        except Exception as exc:
            config = {'entries': []}
            self.log('配置读取失败，未自动选取目标：' + str(exc))
        self.entries = config.get('entries', [])
        if config.get('zcode_exe'):
            backend.EXE = Path(config['zcode_exe'])
        box = ttk.Frame(root, padding=12)
        box.pack(fill='both', expand=True)
        ttk.Label(box, text='每个对话独立选择模型；只在成功完成后续发，运行中与错误状态不发送。').pack(anchor='w')
        controls = ttk.Frame(box)
        controls.pack(fill='x', pady=8)
        for title, command in [('以本地接口模式打开 ZCode', self.launch),
                               ('选择 ZCode 路径', self.choose_executable), ('刷新对话', self.refresh), ('读取可用模型', self.fetch_models)]:
            ttk.Button(controls, text=title, command=command).pack(side='left', padx=3)
        self.search = tk.StringVar()
        ttk.Label(controls, text='筛选对话：').pack(side='left', padx=8)
        ttk.Entry(controls, textvariable=self.search, width=24).pack(side='left')
        self.search.trace_add('write', lambda *_: self.show_tasks())
        ttk.Label(box, text='可用对话（Ctrl / Shift 多选，然后加入监控）').pack(anchor='w')
        self.available = self.make_table(box, ('title', 'workspace', 'state'),
                                         ('对话标题', '工作区', '当前状态'), 6)
        row = ttk.Frame(box)
        row.pack(fill='x', pady=5)
        ttk.Button(row, text='加入选中对话 ↓', command=self.add).pack(side='left')
        ttk.Button(row, text='移除监控列表选中项', command=self.remove).pack(side='left', padx=8)
        ttk.Label(box, text='监控列表（选中行后，可设置模型；配置会保存）').pack(anchor='w')
        self.monitored = self.make_table(box, ('title', 'model', 'state', 'count'),
                                         ('对话标题', '续发使用的模型', '监控状态', '本次续发次数'), 6)
        self.monitored.bind('<<TreeviewSelect>>', self.selected_entry)
        row = ttk.Frame(box)
        row.pack(fill='x', pady=6)
        ttk.Label(row, text='模型：').pack(side='left')
        self.model = ttk.Combobox(row, state='readonly', width=46, values=['保留对话原模型'])
        self.model.current(0)
        self.model.pack(side='left')
        self.model.bind('<<ComboboxSelected>>', lambda _: self.reason_options())
        ttk.Label(row, text='思考档位：').pack(side='left', padx=6)
        self.reason = ttk.Combobox(row, state='readonly', width=12, values=['模型默认'])
        self.reason.current(0)
        self.reason.pack(side='left')
        ttk.Button(row, text='应用到选中对话', command=self.apply_model).pack(side='left', padx=8)
        row = ttk.Frame(box)
        row.pack(fill='x', pady=5)
        self.interval = tk.StringVar(value=str(config.get('interval', 300)))
        self.limit = tk.StringVar(value=str(config.get('limit', 20)))
        ttk.Label(row, text='检查间隔（秒）：').pack(side='left')
        ttk.Entry(row, textvariable=self.interval, width=6).pack(side='left')
        ttk.Label(row, text='每个对话续发上限（0=不限）：').pack(side='left', padx=8)
        ttk.Entry(row, textvariable=self.limit, width=6).pack(side='left')
        self.immediate = tk.BooleanVar(value=config.get('immediate', False))
        self.autostart = tk.BooleanVar(value=config.get('autostart', False))
        ttk.Checkbutton(row, text='开始时立即续发已完成对话', variable=self.immediate).pack(side='left', padx=8)
        ttk.Checkbutton(box, text='下次打开后自动开始（需本地接口已开启）', variable=self.autostart).pack(anchor='w')
        row = ttk.Frame(box)
        row.pack(fill='x', pady=5)
        ttk.Button(row, text='开始全部监控', command=self.start).pack(side='left')
        ttk.Button(row, text='暂停全部', command=self.pause).pack(side='left', padx=8)
        ttk.Button(row, text='保存设置', command=self.save).pack(side='left')
        self.logbox = tk.Text(box, height=10, state='disabled', wrap='word')
        self.logbox.pack(fill='both', expand=True, pady=6)
        self.refresh()
        self.show_entries()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(150, self.drain)
        self.log('默认检查间隔 300 秒；模型选择仅在下一次自动续发时生效。')
        self.fetch_models()
        if self.autostart.get():
            root.after(3000, self.auto_start)

    def make_table(self, parent, columns, headings, height):
        frame = ttk.Frame(parent)
        frame.pack(fill='x')
        table = ttk.Treeview(frame, columns=columns, show='headings', selectmode='extended', height=height)
        for name, heading in zip(columns, headings):
            table.heading(name, text=heading)
            table.column(name, width=230 if name in ('title', 'workspace', 'model') else 110, minwidth=70)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=table.yview)
        table.configure(yscrollcommand=scroll.set)
        table.pack(side='left', fill='x', expand=True)
        scroll.pack(side='right', fill='y')
        return table

    def log(self, message):
        self.events.put({'key': None, 'message': str(message)})

    def drain(self):
        while not self.events.empty():
            event = self.events.get()
            if event.get('done'):
                self.running = False
                self.log('监控已停止；已提交的 ZCode 任务继续执行。')
                continue
            if 'models' in event:
                self.models = event['models']
                self.model['values'] = ['保留对话原模型'] + [self.model_label(m) for m in self.models]
                self.model.current(0)
                self.reason_options()
                self.selected_entry()
                continue
            key = event.get('key')
            prefix = ''
            for index, entry in enumerate(self.entries):
                if identity(entry['task']) == key:
                    prefix = '[' + entry['task']['title'] + '] '
                    if str(index) in self.monitored.get_children():
                        self.monitored.set(str(index), 'state', event.get('status', ''))
                        self.monitored.set(str(index), 'count', event.get('count', 0))
                    break
            text = time.strftime('%H:%M:%S ') + prefix + event.get('message', '')
            self.logbox.configure(state='normal')
            self.logbox.insert('end', text + '\n')
            self.logbox.see('end')
            self.logbox.configure(state='disabled')
        self.root.after(150, self.drain)

    def editable(self):
        if self.running:
            self.log('请先暂停监控，等待停止后再修改目标或模型。')
            return False
        return True

    def refresh(self):
        try:
            self.tasks = backend.read_tasks()
            self.show_tasks()
        except Exception as exc:
            self.log('读取对话失败：' + str(exc))

    def show_tasks(self):
        self.available.delete(*self.available.get_children())
        self.row_keys = {}
        query = self.search.get().strip().lower()
        for i, task in enumerate(self.tasks):
            if query and query not in (task['title'] + task['workspace_path']).lower():
                continue
            key = str(i)
            self.row_keys[key] = task
            self.available.insert('', 'end', iid=key, values=(task['title'], task['workspace_path'], task['task_status']))

    def show_entries(self):
        self.monitored.delete(*self.monitored.get_children())
        for i, entry in enumerate(self.entries):
            selection = entry.get('modelSelection')
            label = selection['providerId'] + ' / ' + selection['modelId'] if selection else '保留对话原模型'
            if selection and selection.get('options', {}).get('reasoningLevel'):
                label += ' [' + selection['options']['reasoningLevel'] + ']'
            self.monitored.insert('', 'end', iid=str(i), values=(entry['task']['title'], label, '尚未开始', 0))

    def add(self):
        if not self.editable(): return
        existing = {identity(e['task']) for e in self.entries}
        for row in self.available.selection():
            task = self.row_keys[row]
            if identity(task) not in existing:
                self.entries.append({'task': copy.deepcopy(task), 'modelSelection': None})
                existing.add(identity(task))
        self.show_entries()
        self.save()

    def remove(self):
        if not self.editable(): return
        selected = {int(i) for i in self.monitored.selection()}
        self.entries = [e for i, e in enumerate(self.entries) if i not in selected]
        self.show_entries()
        self.save()

    @staticmethod
    def model_label(model):
        return model['providerName'] + ' / ' + model['modelId'] + ' [' + model['providerId'] + ']'

    def reason_options(self):
        index = self.model.current() - 1
        values = self.models[index].get('reasoningLevels', []) if index >= 0 else []
        self.reason['values'] = ['模型默认'] + values
        self.reason.current(0)

    def selected_entry(self, _=None):
        selected = self.monitored.selection()
        if not selected: return
        selection = self.entries[int(selected[0])].get('modelSelection')
        index = next((i + 1 for i, model in enumerate(self.models)
                      if selection and model['providerId'] == selection['providerId']
                      and model['modelId'] == selection['modelId']), 0)
        self.model.current(index)
        self.reason_options()
        if selection:
            level = selection.get('options', {}).get('reasoningLevel')
            values = list(self.reason['values'])
            if level in values: self.reason.current(values.index(level))

    def apply_model(self):
        if not self.editable(): return
        selected = self.monitored.selection()
        if not selected:
            self.log('请先在监控列表里选中一个或多个对话。')
            return
        index = self.model.current() - 1
        selection = None
        if index >= 0:
            model = self.models[index]
            selection = {'providerId': model['providerId'], 'modelId': model['modelId']}
            if self.reason.current() > 0:
                selection['options'] = {'reasoningLevel': self.reason.get()}
        for row in selected:
            self.entries[int(row)]['modelSelection'] = copy.deepcopy(selection)
        self.show_entries()
        self.save()

    def fetch_models(self):
        def work():
            try:
                models = read_models()
                self.events.put({'models': models})
                self.log('已读取 ' + str(len(models)) + ' 个可用模型；不保存 API Key。')
            except Exception as exc:
                self.log('读取模型失败：' + str(exc) + '；先用本地接口模式打开 ZCode。')
        threading.Thread(target=work, daemon=True).start()

    def save(self):
        try:
            interval = float(self.interval.get())
            limit = int(self.limit.get())
            if not math.isfinite(interval) or interval < 1 or limit < 0:
                raise ValueError('间隔至少 1 秒，上限必须为非负整数')
            save_config({'entries': self.entries, 'interval': interval, 'limit': limit,
                         'immediate': self.immediate.get(), 'autostart': self.autostart.get(),
                         'zcode_exe': str(backend.EXE) if backend.EXE.is_file() else None})
            return True
        except Exception as exc:
            self.log('保存失败：' + str(exc))
            return False

    def start(self):
        if self.running: return
        if not self.entries:
            self.log('请先加入需要监控的对话。')
            return
        if not self.save(): return
        self.running = True
        self.stop_event = threading.Event()
        runner = MonitorRunner(self.entries, float(self.interval.get()), int(self.limit.get()),
                               self.immediate.get(), self.stop_event, self.events.put)
        self.log('开始监控 ' + str(len(self.entries)) + ' 个对话；各自独立去重和计数。')
        threading.Thread(target=runner.run, daemon=True).start()

    def pause(self):
        self.stop_event.set()
        self.auto_pending = False
        self.log('已请求暂停全部监控。')

    def auto_start(self):
        if not getattr(self, 'auto_pending', True): return
        try:
            with backend.urlopen(f'http://127.0.0.1:{backend.PORT}/json/list', timeout=0.4): pass
        except Exception:
            self.root.after(3000, self.auto_start)
            return
        self.start()

    def choose_executable(self):
        if not self.editable(): return False
        filename = filedialog.askopenfilename(title='选择 ZCode.exe',
                    filetypes=[('ZCode executable', 'ZCode.exe'), ('Windows executable', '*.exe')])
        if not filename: return False
        path = Path(filename)
        if not path.is_file() or path.name.lower() != 'zcode.exe':
            messagebox.showerror('路径无效', '请选择 ZCode 安装目录中的 ZCode.exe。')
            return False
        backend.EXE = path
        self.save()
        self.log('已保存 ZCode 安装路径。')
        return True

    def launch(self):
        if not backend.EXE.is_file() and not self.choose_executable():
            return
        result = backend.subprocess.run(['tasklist', '/FI', 'IMAGENAME eq ZCode.exe', '/FO', 'CSV'],
                                        capture_output=True, text=True, creationflags=0x08000000)
        if 'zcode.exe' in result.stdout.lower():
            messagebox.showinfo('请先退出 ZCode', '请正常退出 ZCode 后再点击，本工具不会强行停止任务。')
            return
        backend.subprocess.Popen([str(backend.EXE), f'--remote-debugging-port={backend.PORT}',
                                  '--remote-debugging-address=127.0.0.1'], creationflags=0x08000000)
        self.log('已启动 ZCode，请打开目标工作区并读取可用模型。')

    def close(self):
        self.stop_event.set()
        self.root.destroy()


if __name__ == '__main__':
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        # 发布包冒烟检查仅验证运行库，不读取对话，也不提交任务。
        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        Path(sys.argv[2]).write_text(json.dumps({'version': VERSION, 'tkinter': True,
                                              'websocket': True, 'sqlite': True}), encoding='utf8')
    else:
        root = tk.Tk()
        App(root)
        root.mainloop()
