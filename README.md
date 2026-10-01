# ZCode Auto Continue

Windows 桌面工具：同时监控多个 ZCode 对话，在新一轮成功完成后自动发送“继续下一步”，并为每个对话指定续发模型与思考档位。

## 功能

- 按工作区与对话 ID 固定目标，支持多选对话。
- 默认每 300 秒检查一次；每次记录状态与下次检查时间。
- 每个对话独立去重、计数与失败暂停。
- 模型目录来自 ZCode 已配置并启用的供应商；可指定模型或保留对话原模型。
- 只在成功完成后续发；运行中、错误、等待确认均不发送。
- 支持启动时立即续发、自动启动监控、保存配置、暂停全部。
- 监控本身不调用模型推理 API；任务执行消耗所选模型的额度。

## 环境

- Windows 10 / 11。
- Python 3.10 或更高版本，含 tkinter；当前验证环境为 Python 3.14。
- ZCode 桌面版，当前适配版本 3.14.4。

目前 `auto_continue.py` 中的 `EXE` 为 `D:\m_Applications\ZCode\ZCode.exe`；如果安装位置不同，请修改该值。

## 安装与运行

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe multi_continue.py
```

1. 如 ZCode 本地接口未开启，正常退出 ZCode，再点击工具中的“以本地接口模式打开 ZCode”。如果接口已开启，无需重启。
2. 打开目标工作区。用 Ctrl / Shift 在上方列表多选对话，点击“加入选中对话”。
3. 点击“读取可用模型”。在下方监控列表选择一行或多行，再选择模型、思考档位并点击“应用到选中对话”。
4. 点击“开始全部监控”。默认不对历史已完成回复立即续发；需要时勾选“开始时立即续发已完成对话”。
5. 如果希望重开工具自动监控，勾选“下次打开后自动开始”并保存设置。
6. 更改目标或模型前，先暂停并等待监控停止。使用新版前关闭旧版工具，避免对同一目标重复续发。

续发上限按每个对话计算，0 表示不限；每次开始监控重新计数。指定模型只在下一次自动发送时生效，不改变已经在运行的任务。多个对话是否同时执行由 ZCode 和模型供应商的并发限制决定。

## 测试与打包

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m unittest -v
.venv/Scripts/python.exe -m PyInstaller --noconfirm --onefile --windowed --name ZCode自动继续-多对话版 multi_continue.py
```

产物在 `dist/`。可选的 React 服务发现夹具测试：

```powershell
node test_discovery.js
```

24 项 Python 回归测试覆盖目标锁定、回复去重、多模型路由、独立计数、失败隔离、暂停、状态变化与配置保存。真实环境已验证模型目录读取；自动消息发送由单对话版本验证。多对话指定模型的真实推理执行尚未额外测试。

## 实现原理

1. 只读查询本地 `~/.zcode/v2/tasks-index.sqlite` 中的任务状态。
2. 通过 `127.0.0.1:19387` 的 Chrome DevTools Protocol 访问 ZCode 桌面页面。
3. 从 React 服务上下文找到 ZCode 自身的服务对象，读取末条助手回复。
4. 对回复 ID、正文和时间戳生成 SHA-256 指纹，以判断是否出现新的完成结果。
5. 提交 `sendConversationCommandV4` 的 `sendText` 命令，按需携带 `modelSelection`。

每个目标分别保存回复指纹、续发计数与暂停状态。未知提交结果不自动重试，避免重复触发实际任务。

## 配置与数据

运行配置位于用户的 `Documents/ZCodeAutoContinue/` 下，多对话配置文件为 `settings.multi.json`。首次使用可迁移旧版 `settings.json` 中明确保存的目标。仓库不包含任何实际对话配置、数据库、模型目录快照或 API Key。

模型目录返回前已在 ZCode 页面内过滤凭据字段，仅传出名称、ID 与思考档位。无需在工具中另行输入 API Key。

## 已知限制

- 使用 ZCode 内部接口和 React 服务结构，升级后可能需要适配。
- 当前适配桌面本地工作区；远程主机模型目录尚未验证。
- 本地调试端口可被本机其他程序访问。退出 ZCode 后以普通方式重开，即可结束本次调试接口。
- 数据库整体读取失败会结束本次监控；单个目标失败仅暂停该目标。
- 工具不关闭或强行终止 ZCode 任务。

## 文件

| 文件 | 用途 |
| --- | --- |
| `multi_continue.py` | 多对话窗口、模型目录与独立监控状态 |
| `auto_continue.py` | 本地 CDP 接口、对话查询、消息提交，以及早期单对话窗口 |
| `test_multi.py` | 多对话与模型路由测试 |
| `test_monitor.py` | 基础状态与消息接口测试 |
| `test_discovery.js` | React 服务发现夹具测试 |
