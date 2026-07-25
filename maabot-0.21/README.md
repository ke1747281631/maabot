# MAABot

> ⚠️ 本项目代码由 AI（GitHub Copilot）生成，经人工测试调整。

通过 QQ 机器人远程控制 [MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights)（明日方舟自动化助手），支持任务调度、日志推送和进度通知。

## 更新日志

### v0.21 (2026-04-28)

**状态持久化**
- 当前运行模式（全选、肉鸽、WebUI 等）自动保存到 `data/state.json`
- 程序重启后自动恢复上次的运行模式
- 肉鸽自动切换后状态持久化，确保重启不丢失

**日志系统增强**
- 新增统一日志配置，同时输出到控制台和 `logs/maabot_YYYYMMDD.log`
- MAA 重启流程详细日志：进程检查、taskkill 结果、Popen 返回值、逐秒等待状态
- 配置文件写入结果和任务变更详情
- 异常自动捕获并输出堆栈信息

**进程管理改进**
- 优化 MAA 启动方式，使用 `os.startfile()` 替代复杂的 subprocess 参数
- 重启前自动发送 StopTask，确保任务安全停止
- 添加 90 秒超时保护，避免无限等待

**GUI 重启逻辑修复**
- 修复服务重启时"服务进程已退出"误报问题
- 停止服务前清空日志队列，防止旧 pump 线程误判
- pump 线程增加进程 ID 检查，确保只处理当前活跃进程的日志

## 功能

- **GUI 控制面板** — 独立桌面窗口，支持服务启停、任务下发、配置修改、日志查看
- **QQ 指令控制** — 通过私聊消息启动/停止 MAA 任务
- **任务组合** — 支持日常长草、肉鸽、全选+肉鸽等预设组合，一条消息搞定
- **自动勾选** — Bot 自动修改 MAA 配置并重启，无需手动操作 GUI
- **日志推送** — 实时监控 MAA 运行日志，关键事件（肉鸽探索次数、投资、任务完成/出错等）推送到 QQ
- **肉鸽自动切换** — 检测到肉鸽常乐节点或肉鸽任务完成后，自动切换为日常全选任务并重启 MAA
- **进程管理** — Bot 启动时自动拉起 MAA，MAA 独立运行不随 Bot 退出而关闭

## 支持的指令

| 指令 | 说明 |
|------|------|
| `全选+肉鸽` / `长草+肉鸽` | 日常全部 + 自动肉鸽 |
| `全选` / `长草` / `开始` | 日常全部（唤醒、作战、基建、公招、购物、奖励） |
| `肉鸽` | 唤醒 + 自动肉鸽 |
| `基建` / `公招` / `作战` / `购物` / `奖励` | 单项任务 |
| `停止` | 停止当前任务 |
| `心跳` | 检测 MAA 连接状态 |
| `帮助` | 显示指令列表 |

## 技术架构

```
GUI 窗口 ──→ maabot_gui.py ──→ maabot.py
                                    ──→ 修改 gui.new.json
                                    ──→ 重启 MAA
                                    ──→ HTTP API ←──→ MAA 远程控制轮询
                                    ──→ 监控 gui.log ──→ QQ 通知

QQ 消息 ──→ NcatBot ──→ maabot.py
```

- **NcatBot** — QQ Bot 框架（基于 NapCat）
- **Flask + Waitress** — 提供 MAA 远程控制 HTTP 端点
- **MAA Remote Control** — MAA 通过轮询 `/maa/getTask` 获取任务，通过 `/maa/reportStatus` 上报状态

## 安装

### 前置要求

- Windows 10/11
- Python 3.9+
- [MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights) 已安装
- 一个可用的 QQ 号作为机器人账号

### 一键安装

以管理员身份运行：

```bat
安装maabot.bat
```

脚本会自动安装 VC++ 运行库、QQ 9.9.26（NapCat 兼容版本）和 Python 依赖。

### 手动安装

```bash
pip install ncatbot flask waitress
```

## 配置

### 1. 配置 config.yaml

编辑 `config.yaml`，填写机器人 QQ 号和管理员 QQ 号：

```yaml
bt_uin: '你的机器人QQ号'
root: '你的管理员QQ号'
```

`maabot.py` 会自动从 `config.yaml` 读取这两个值，无需修改代码。如需修改 MAA 路径，编辑 `maabot.py` 顶部的 `CONFIG`。

### 2. 配置 MAA 远程控制

打开 MAA → 设置 → 远程控制，填入：

| 项目 | 值 |
|------|------|
| 任务获取端点 | `http://127.0.0.1:2345/maa/getTask` |
| 任务汇报端点 | `http://127.0.0.1:2345/maa/reportStatus` |

### 3. 配置 NcatBot

`config.yaml` 中的其他选项（NapCat WebSocket 地址等）一般无需修改，保持默认即可。

## 运行

### 方式一：GUI 控制面板（推荐）

双击运行 `maabot_gui.py`，打开独立桌面窗口：

```bash
python maabot_gui.py
```

控制面板功能：
- 服务启停、重启
- 快捷任务下发（全选+肉鸽、日常、肉鸽等）
- 自定义任务勾选
- 配置修改（QQ 号、MAA 路径等）
- 实时日志查看

### 方式二：命令行模式

```bash
python maabot.py
```

首次运行会自动下载 NapCat 并弹出 QQ 登录界面，用机器人 QQ 号扫码登录即可。

登录后给机器人发送 `帮助` 查看可用指令。

> ⚠️ Web 控制面板（`启动.bat` + 浏览器访问）存在兼容性问题，暂不推荐使用。

## 工作原理

1. 收到 QQ 指令后，Bot 修改 MAA 的 `gui.new.json` 配置文件中的任务勾选状态
2. 如果任务配置有变更，自动重启 MAA 使配置生效
3. MAA 启动后通过远程控制轮询端点获取 `LinkStart` 指令
4. MAA 按勾选的任务一次性顺序执行
5. Bot 实时监控 MAA 日志，推送关键信息到管理员 QQ
6. 检测到肉鸽常乐节点或肉鸽任务完成后，自动切换为日常全选任务并重启 MAA

## 相关项目

- [MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights) — 明日方舟自动化助手
- [NapCat](https://github.com/NapNeko/NapCatQQ/) — QQ Bot 框架
- [NextNapCatWebUI](https://github.com/bietiaop/NextNapCatWebUI) — NapCat WebUI

## 许可

MIT
