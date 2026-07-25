# MAABot

> v0.25 · 本项目代码由 AI（GitHub Copilot）生成，经人工测试调整。

通过 QQ 机器人远程控制 [MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights)（明日方舟自动化助手），支持任务调度、日志推送和进度通知。

## 功能一览

- **GUI 控制面板** — 独立桌面窗口，支持服务启停、任务下发、配置修改、日志查看
- **QQ 指令控制** — 通过私聊消息启动/停止 MAA 任务
- **任务组合** — 支持日常长草、肉鸽、全选+肉鸽等预设组合，一条消息搞定
- **自动勾选** — Bot 自动修改 MAA 配置并重启，无需手动操作 GUI
- **日志推送** — 实时监控 MAA 运行日志，关键事件推送到 QQ
- **肉鸽自动切换** — 检测到肉鸽常乐节点或肉鸽任务完成后，自动切换为日常全选任务并重启 MAA
- **异常自动恢复** — MAA 初始化崩溃、模拟器断开、肉鸽异常退出时自动恢复
- **WebUI** — 浏览器访问的控制面板，支持远程操作

---

## 技术架构

```
┌─────────────────────────────────────────────────────────┐
│                    napcat 桌面版                         │
│  ┌──────────────┐         ┌──────────────────┐          │
│  │  HTTP Server  │         │  HTTP Client     │          │
│  │  (端口 3000)  │         │  (Webhook)       │          │
│  └──────┬───────┘         └────────┬─────────┘          │
└─────────┼──────────────────────────┼────────────────────┘
          │ POST /send_private_msg   │ POST /napcat/event
          │ (maabot → napcat)        │ (napcat → maabot)
          ▼                          ▼
┌─────────────────────────────────────────────────────────┐
│                    maabot.py (Flask)                     │
│                                                          │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ NapCatHTTPAPI│  │ /napcat/event│  │ MAA 远程控制    │  │
│  │ (发消息)     │  │ (收消息)     │  │ /maa/getTask   │  │
│  └─────────────┘  └──────────────┘  │ /maa/reportStatus│ │
│                                      └───────┬────────┘  │
│  ┌─────────────────────────────────────────┐            │
│  │ watch_log_file — 监控 MAA gui.log       │            │
│  │ → 关键事件推送到 QQ                      │            │
│  │ → 异常自动恢复                           │            │
│  └─────────────────────────────────────────┘            │
└──────────────────────────┬──────────────────────────────┘
                           │ HTTP 轮询
                           ▼
                    ┌──────────────┐
                    │   MAA.exe    │
                    │ (明日方舟助手) │
                    └──────────────┘
```

**通讯方式**：maabot.py 与 napcat 桌面版之间完全通过 HTTP 通讯（OneBot11 协议），不依赖 WebSocket 或 ncatbot 库。

---

## 安装

### 前置要求

| 项目         | 要求                  |
| ---------- | ------------------- |
| 操作系统       | Windows 10/11       |
| Python     | 3.9+                |
| MAA        | 已安装并能正常运行           |
| QQ         | 已安装 NapCat 兼容版本     |
| napcat 桌面版 | 已安装 [NapCatQQ-Desktop](https://github.com/NapNeko/NapCatQQ-Desktop/releases) |

### 第一步：安装 Python 依赖

```bash
pip install flask waitress pyyaml
```

或运行项目自带的 `运行环境安装.bat`（会自动检测 Python 并安装依赖）。

### 第二步：配置 napcat 桌面版

#### 2.1 启动 napcat

打开 **NapCatQQ-Desktop**（桌面快捷方式或开始菜单），点击启动 Bot，用机器人 QQ 号扫码登录。

> napcat 桌面版是独立安装的 MSI 程序，不是放在本项目目录下的。HTTP 服务由登录后的 QQ 进程提供，无需手动运行 bat 文件。
>
> 登录成功后，QQ 进程会监听配置的 HTTP 端口（默认 3000）。

#### 2.2 配置 OneBot11 HTTP 通讯

打开 napcat 桌面端，进入 **网络配置** 页面，添加以下两个网络服务：

**① HTTP 服务器**（maabot 通过此端口发送消息给 napcat）

| 配置项    | 值                              |
| ------ | ------------------------------ |
| 名称     | `HttpServer`                   |
| 启用     | ✅                              |
| 监听地址   | `localhost`                    |
| 端口     | `3000`                         |
| 消息格式   | `array`                        |
| 强制推送事件 | ✅                              |
| Token  | 自定义一个密码（记下来，后面要填到 config.yaml） |

**② HTTP 客户端 / Webhook**（napcat 把收到的 QQ 消息推送给 maabot）

| 配置项    | 值                                    |
| ------ | ------------------------------------ |
| 名称     | `WebhookToMAABot`                    |
| 启用     | ✅                                    |
| URL    | `http://127.0.0.1:2345/napcat/event` |
| 消息格式   | `array`                              |
| 上报自身消息 | ❌                                    |
| Token  | 留空                                   |

> **Token 说明**：HTTP 服务器的 Token 是一个自定义密码，maabot 发消息时需要带上。请确保此 Token 与 `config.yaml` 中的 `napcat_http_api_token` 一致。

#### 2.3 验证 napcat 已就绪

启动 napcat 后，确认端口 3000 正在监听：

```bash
netstat -ano | findstr 3000
```

应看到 `LISTENING` 状态。

### 第三步：配置 config.yaml

编辑项目根目录的 `config.yaml`：

```yaml
# ===== 必填 =====
root: '你的管理员QQ号'        # 接收通知、发送指令的QQ
bt_uin: '你的机器人QQ号'      # napcat 登录的QQ

# ===== MAA 路径 =====
maa_exe: D:\MAA\MAA.exe
maa_log_path: D:\MAA\debug\gui.log
maa_config_path: D:\MAA\config\gui.new.json

# ===== HTTP 服务 =====
http_port: 2345

# ===== napcat HTTP API =====
napcat_http_api_url: http://localhost:3000
napcat_http_api_token: '你的token'    # 与 onebot11 JSON 中的 token 一致

# ===== 日志推送 =====
log_batch_size: 5           # 积攒多少条日志后合并发送
log_batch_timeout: 10       # 最多等待多少秒后强制发送
```



> 首次运行 `maabot.py` 时会自动生成随机 WebUI 密码并打印到控制台，请妥善保存。可在 WebUI 设置页修改密码。

### 第四步：配置 MAA 远程控制

打开 MAA → 设置 → 远程控制，填入：

| 项目     | 值                                        |
| ------ | ---------------------------------------- |
| 任务获取端点 | `http://127.0.0.1:2345/maa/getTask`      |
| 任务汇报端点 | `http://127.0.0.1:2345/maa/reportStatus` |

---

## 运行

### 启动顺序

```
1. 以管理员权限启动 napcat（napcat/launcher.bat）
2. 启动 maabot（GUI 或命令行）
3. 启动 MAA（可由 maabot 自动拉起，或手动启动）
```

### 方式一：GUI 控制面板（推荐）

```bash
python maabot_gui.py
```

GUI 功能：

- 服务启停、重启
- 快捷任务下发（全选+肉鸽、日常、肉鸽等）
- 自定义任务勾选
- 配置修改（QQ 号、MAA 路径等）
- 实时日志查看
- MAA 启停、重启

> GUI 启动服务前会自动检测 napcat HTTP API（端口 3000）是否就绪，未就绪时弹窗提示。

### 方式二：命令行

```bash
python maabot.py
```

### 方式三：WebUI

服务启动后，浏览器访问：

```
http://127.0.0.1:2345/webui
```

首次运行使用控制台打印的随机密码登录（用户名 `admin`）。

WebUI 功能：

- 服务启停、重启
- MAA 启停、重启
- 任务下发
- 配置修改
- 实时日志流（SSE）

---

## QQ 指令

给机器人 QQ 号发送私聊消息即可控制 MAA。发送 `帮助` 查看所有指令。

### 任务指令

| 指令                 | 说明        | 执行的任务                |
| ------------------ | --------- | -------------------- |
| `全选+肉鸽` / `长草+肉鸽`  | 日常全部 + 肉鸽 | 唤醒、作战、基建、公招、购物、奖励、肉鸽 |
| `全选` / `长草` / `开始` | 日常全部      | 唤醒、作战、基建、公招、购物、奖励    |
| `肉鸽`               | 仅肉鸽       | 唤醒、肉鸽                |
| `基建`               | 仅基建换班     | 基建换班                 |
| `公招`               | 仅自动公招     | 自动公招                 |
| `作战`               | 仅理智作战     | 理智作战                 |
| `购物`               | 仅信用收支     | 信用收支                 |
| `奖励`               | 仅领取奖励     | 领取奖励                 |

### 控制指令

| 指令   | 说明                                  |
| ---- | ----------------------------------- |
| `停止` | 停止当前 MAA 任务（连续发送 5 次 StopTask 确保生效） |
| `心跳` | 检测 MAA 连接状态                         |
| `帮助` | 显示指令列表                              |

### 指令执行流程

1. 收到 QQ 指令后，Bot 修改 MAA 的 `gui.new.json` 配置文件中的任务勾选状态
2. 如果任务配置有变更，自动重启 MAA 使配置生效
3. MAA 启动后通过远程控制轮询端点获取 `LinkStart` 指令
4. MAA 按勾选的任务一次性顺序执行
5. Bot 实时监控 MAA 日志，推送关键信息到管理员 QQ
6. 检测到肉鸽常乐节点或肉鸽任务完成后，自动切换为日常全选任务并重启 MAA

---

## 日志推送

maabot 实时监控 MAA 的 `gui.log`，匹配到以下关键事件时推送到 QQ：

| 事件    | 推送内容示例             |
| ----- | ------------------ |
| 连接模拟器 | 🔗 正在连接模拟器...      |
| 开始运行  | ▶️ 开始运行            |
| 开始任务  | 📌 开始任务：自动肉鸽       |
| 完成任务  | ✅ 完成任务：基建换班        |
| 任务出错  | ❌ 任务出错：战斗失误        |
| 全部完成  | 🎉 所有任务完成！         |
| 理智信息  | 💊 理智：120/135      |
| 设施信息  | 🏭 当前设施：制造站        |
| 公招识别  | ⭐ 公招：4★ Tags       |
| 理智回满  | ⏰ 理智将在2h30m回满      |
| 用时统计  | ⏱️ 用时 1h23m45s     |
| 肉鸽探索  | 🎲 已开始第 3 次探索      |
| 肉鸽投资  | 💰 已投资 200 存款：5000 |
| 放弃探索  | 🔄 已放弃本次探索         |

为避免消息轰炸，日志会积攒到 `log_batch_size` 条（默认 5 条）或等待 `log_batch_timeout` 秒（默认 10 秒）后合并发送。

---

## 自动恢复机制

maabot 内置多种异常自动恢复：

| 异常场景      | 检测方式                      | 恢复动作                    |
| --------- | ------------------------- | ----------------------- |
| MAA 初始化崩溃 | 日志出现 "MAA 初始化期间退出"        | 通知 QQ → 重启 MAA → 重新下发任务 |
| 模拟器断开     | 日志出现 "重连失败，连接断开" 或 "截图失败" | 通知 QQ → 重启 MAA → 重新下发任务 |
| 肉鸽异常退出    | 肉鸽任务启动但未找到常乐节点即完成         | 通知 QQ → 重启 MAA → 重新下发任务 |
| 肉鸽完成自动切换  | 肉鸽任务正常完成（已投资）             | 自动切换为日常全选 → 重启 MAA      |

---

## config.yaml 完整配置说明

| 字段                         | 类型     | 说明                                                |
| -------------------------- | ------ | ------------------------------------------------- |
| `root`                     | string | 管理员 QQ 号（接收通知、发送指令）                               |
| `bt_uin`                   | string | 机器人 QQ 号（napcat 登录的 QQ）                           |
| `maa_exe`                  | string | MAA.exe 路径                                        |
| `maa_log_path`             | string | MAA gui.log 路径                                    |
| `maa_config_path`          | string | MAA gui.new.json 路径                               |
| `http_port`                | int    | maabot HTTP 服务端口（默认 2345）                         |
| `napcat_http_api_url`      | string | napcat HTTP Server 地址（默认 <http://localhost:3000）> |
| `napcat_http_api_token`    | string | napcat HTTP Server token（与 onebot11 JSON 中一致）     |
| `log_batch_size`           | int    | 日志合并发送条数（默认 5）                                    |
| `log_batch_timeout`        | int    | 日志强制发送超时秒数（默认 10）                                 |
| `enable_webui_interaction` | bool   | 是否启用 WebUI 交互（默认 true）                            |
| `debug`                    | bool   | 调试模式（默认 false）                                    |
| `webui_user`               | string | WebUI 用户名（默认 admin）                               |
| `webui_password_hash`      | string | WebUI 密码哈希（首次运行自动生成）                              |
| `frp_server_addr`          | string | FRP 服务器地址（远程访问用，可选）                               |
| `webui_domain`             | string | WebUI 域名（远程访问用，可选）                                |
| `frp_remote_port1`         | int    | FRP 远程端口 1（可选）                                    |
| `frp_remote_port2`         | int    | FRP 远程端口 2（可选）                                    |

---

## HTTP API 端点

maabot 提供以下 HTTP 端点：

### MAA 远程控制

| 端点                  | 方法   | 说明           |
| ------------------- | ---- | ------------ |
| `/maa/getTask`      | POST | MAA 轮询获取任务指令 |
| `/maa/reportStatus` | POST | MAA 上报任务执行状态 |
| `/maa/status`       | GET  | 获取 MAA 连接状态  |

### napcat 事件接收

| 端点              | 方法   | 说明                          |
| --------------- | ---- | --------------------------- |
| `/napcat/event` | POST | 接收 napcat Webhook 事件（私聊消息等） |

### WebUI API

| 端点                          | 方法       | 说明         |
| --------------------------- | -------- | ---------- |
| `/webui`                    | GET      | WebUI 页面   |
| `/api/status`               | GET      | 服务状态       |
| `/api/config`               | GET/POST | 读取/修改配置    |
| `/api/login`                | POST     | WebUI 登录   |
| `/api/logout`               | POST     | WebUI 登出   |
| `/api/auth/status`          | GET      | 登录状态       |
| `/api/auth/change-password` | POST     | 修改密码       |
| `/api/maa/dispatch`         | POST     | 下发任务       |
| `/api/maa/write-config`     | POST     | 写入 MAA 配置  |
| `/api/maa/restart`          | POST     | 重启 MAA     |
| `/api/service/start`        | POST     | 启动服务       |
| `/api/service/stop`         | POST     | 停止服务       |
| `/api/service/restart`      | POST     | 重启服务       |
| `/api/logs/history`         | GET      | 历史日志       |
| `/api/logs/stream`          | GET      | 实时日志流（SSE） |

---

## 常见问题

### Q: 启动时提示 "napcat HTTP API（端口 3000）未启动"

napcat 没有以管理员权限运行，或 onebot11 配置中 HTTP Server 未启用。请：

1. 以管理员权限运行 `napcat/launcher.bat`
2. 检查 `onebot11_<QQ号>.json` 中 `httpServers` 的 `enable` 是否为 `true`
3. 确认端口 3000 正在监听：`netstat -ano | findstr 3000`

### Q: QQ 收不到通知消息

检查 `config.yaml` 中 `napcat_http_api_token` 是否与 `onebot11_<QQ号>.json` 中 HTTP Server 的 `token` 一致。

### Q: QQ 发指令没反应

检查 napcat 的 HTTP Client（Webhook）配置：

- `url` 应为 `http://127.0.0.1:2345/napcat/event`
- `enable` 应为 `true`

### Q: MAA 远程控制连不上

确认 maabot 服务已启动（端口 2345 正在监听），且 MAA 设置中的远程控制端点地址正确。

### Q: WebUI 忘记密码

删除 `config.yaml` 中的 `webui_password_hash` 行，重新启动 maabot 会生成新密码并打印到控制台。

---

## 相关项目

- [MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights) — 明日方舟自动化助手
- [NapCat](https://github.com/NapNeko/NapCatQQ/) — 基于 QQNT 的 Bot 框架（napcat 桌面版）

## 许可

MIT
