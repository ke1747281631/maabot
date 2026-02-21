"""
MAA 运行情况通知 - 基于 NcatBot 后台模式 + MAA Remote Control Schema
────────────────────────────────────────────────────────────────────
依赖安装：
  pip install ncatbot flask waitress

使用方式：
  1. 修改下方 CONFIG（bot_qq、admin_qq、log_path 必填）
  2. 运行：python maa_ncatbot_notifier.py
  3. MAA 远程控制填入：
       任务获取端点: http://127.0.0.1:6000/maa/getTask
       任务汇报端点: http://127.0.0.1:6000/maa/reportStatus
"""

import uuid
import asyncio
import threading
import re
from datetime import datetime
from collections import defaultdict
from flask import Flask, request, jsonify
from waitress import serve
from ncatbot.core import BotClient
from ncatbot.core.event import PrivateMessageEvent

# ═══════════════════════════════════════════════
#  ★ 修改这里的配置 ★
# ═══════════════════════════════════════════════
CONFIG = {
    "bot_qq":   "123456789",   # 机器人的 QQ 号（运行前需退出该 QQ 在电脑上的登录）
    "admin_qq": "987654321",   # 管理员 QQ 号（接收通知的账号）

    # HTTP 服务配置
    "host": "127.0.0.1",       # 仅本机访问用 127.0.0.1，局域网/公网访问改为 0.0.0.0
    "port": 6000,

    # MAA 的 gui.log 路径
    "log_path": r".\MAA\debug\gui.log",   # 改成你的maa程序debug文件夹的路径

    # 需要通知的任务类型（远程控制上报用）
    "notify_task_types": [
        "LinkStart",
        "LinkStart-Base",
        "LinkStart-WakeUp",
        "LinkStart-Combat",
        "LinkStart-Recruiting",
        "LinkStart-Mall",
        "LinkStart-Mission",
        "LinkStart-AutoRoguelike",
        "LinkStart-Reclamation",
    ],

    # 日志推送：积攒多少条后合并发一次（避免消息轰炸）
    "log_batch_size": 5,
    # 日志推送：最多等待多少秒后强制发送（即使不足 batch_size 条）
    "log_batch_timeout": 10,
}

# ═══════════════════════════════════════════════
#  需要推送的日志关键词（匹配 TaskQueueViewModel 的内容）
# ═══════════════════════════════════════════════
LOG_RULES = [
    # (正则, 推送模板)  {0} = 匹配到的内容
    (re.compile(r"正在连接模拟器"),           "🔗 正在连接模拟器..."),
    (re.compile(r"正在运行中"),               "▶️ 开始运行"),
    (re.compile(r"开始任务[:：]\s*(.+)"),     "📌 开始任务：{0}"),
    (re.compile(r"完成任务[:：]\s*(.+)"),     "✅ 完成任务：{0}"),
    (re.compile(r"任务已全部完成"),            "🎉 所有任务完成！"),
    (re.compile(r"(理智[:：].+)"),            "💊 {0}"),
    (re.compile(r"当前设施[:：]\s*(.+)"),     "🏭 当前设施：{0}"),
    (re.compile(r"公招识别结果"),             None),   # None = 忽略
    (re.compile(r"(\d+\s*★\s*Tags)"),        "⭐ 公招：{0}"),
    (re.compile(r"掉落统计"),                 None),   # 忽略
    (re.compile(r"(理智将在.+回满)"),         "⏰ {0}"),
    (re.compile(r"(用时\s*\d+h\s*\d+m\s*\d+s)"), "⏱️ {0}"),
]

# 不推送的噪音行（匹配到则跳过）
LOG_IGNORE = re.compile(
    r"截图耗时|最快截图|Scheduled|Timer|Index \d|IsEnable|Build Time|Resource Time"
    r"|Main windows|AsstProxy|HttpService|HttpResponse|RemoteControl"
    r"|当前槽位已刷新|已刷新标签"
)

# ═══════════════════════════════════════════════
#  任务类型 -> 中文名（远程控制用）
# ═══════════════════════════════════════════════
TASK_NAMES = {
    "LinkStart":               "一键长草（全部）",
    "LinkStart-Base":          "基建换班",
    "LinkStart-WakeUp":        "开始唤醒",
    "LinkStart-Combat":        "自动作战",
    "LinkStart-Recruiting":    "自动公招",
    "LinkStart-Mall":          "购物",
    "LinkStart-Mission":       "领取奖励",
    "LinkStart-AutoRoguelike": "自动肉鸽",
    "LinkStart-Reclamation":   "生息演算",
    "StopTask":                "停止任务",
    "HeartBeat":               "心跳检测",
}

QQ_COMMANDS = {
    "开始": "LinkStart",
    "长草": "LinkStart",
    "基建": "LinkStart-Base",
    "公招": "LinkStart-Recruiting",
    "作战": "LinkStart-Combat",
    "购物": "LinkStart-Mall",
    "奖励": "LinkStart-Mission",
    "肉鸽": "LinkStart-AutoRoguelike",
    "停止": "StopTask",
    "心跳": "HeartBeat",
}

HELP_TEXT = (
    "MAA 远程控制指令：\n"
    "  开始 / 长草 - 一键长草\n"
    "  基建 - 基建换班\n"
    "  公招 - 自动公招\n"
    "  作战 - 自动作战\n"
    "  购物 - 商店购物\n"
    "  奖励 - 领取奖励\n"
    "  肉鸽 - 自动肉鸽\n"
    "  停止 - 停止当前任务\n"
    "  心跳 - 检测连接状态\n"
    "  帮助 - 显示此菜单"
)

# ═══════════════════════════════════════════════
#  全局状态
# ═══════════════════════════════════════════════
api = None
bot = BotClient()
bot_loop: asyncio.AbstractEventLoop = None

pending_tasks: list = []
pending_tasks_lock = threading.Lock()
issued_tasks: dict = defaultdict(set)
issued_task_detail: dict = {}
devices: dict = {}
# 日志批量发送缓冲
log_buffer: list = []
log_buffer_lock = threading.Lock()
log_buffer_timer = None


# ═══════════════════════════════════════════════
#  发私聊消息（线程安全，投递到 ncatbot 事件循环）
# ═══════════════════════════════════════════════
def send_private_msg(text: str):
    if api is None or bot_loop is None:
        print(f"[WARN] Bot 未就绪: {text[:50]}")
        return

    async def _do():
        try:
            await api.post_private_msg(CONFIG["admin_qq"], text=text)
            print(f"[INFO] 已通知: {text[:60]}")
        except Exception as e:
            print(f"[ERROR] 发送失败: {e}")

    asyncio.run_coroutine_threadsafe(_do(), bot_loop)


# ═══════════════════════════════════════════════
#  日志批量推送
# ═══════════════════════════════════════════════
def flush_log_buffer():
    """将缓冲区的日志合并发送"""
    global log_buffer_timer
    with log_buffer_lock:
        if not log_buffer:
            return
        lines = log_buffer[:]
        log_buffer.clear()
        log_buffer_timer = None

    msg = "\n".join(lines)
    send_private_msg(msg)


def queue_log_line(line: str):
    """将一条日志加入缓冲，达到批次上限或超时后发送"""
    global log_buffer_timer

    with log_buffer_lock:
        log_buffer.append(line)
        count = len(log_buffer)

    if count >= CONFIG["log_batch_size"]:
        # 达到批次上限，立即发送
        if log_buffer_timer:
            log_buffer_timer.cancel()
        flush_log_buffer()
    else:
        # 重置超时定时器
        if log_buffer_timer:
            log_buffer_timer.cancel()
        log_buffer_timer = threading.Timer(CONFIG["log_batch_timeout"], flush_log_buffer)
        log_buffer_timer.daemon = True
        log_buffer_timer.start()


# ═══════════════════════════════════════════════
#  日志文件监控线程
# ═══════════════════════════════════════════════
def parse_log_line(raw_line: str):
    """
    解析一行 gui.log，返回需要推送的文本，或 None（不推送）。
    只处理 TaskQueueViewModel 的 INF 行。
    """
    # 只关注 TaskQueueViewModel 的 INF 日志
    if "[INF][TaskQueueViewModel]" not in raw_line:
        # 检查是否是理智行（紧跟在完成任务后，没有前缀）
        stripped = raw_line.strip()
        if re.match(r"^理智[:：]", stripped):
            return f"💊 {stripped}"
        if re.match(r"^理智将在", stripped):
            return f"⏰ {stripped}"
        return None

    # 忽略噪音行
    if LOG_IGNORE.search(raw_line):
        return None

    # 提取 > 后面的内容
    m = re.search(r"<\d+>\s*(.+)", raw_line)
    if not m:
        return None
    content = m.group(1).strip()
    if not content:
        return None

    # 匹配规则
    for pattern, template in LOG_RULES:
        m2 = pattern.search(content)
        if m2:
            if template is None:
                return None  # 忽略
            groups = m2.groups()
            text = template.format(*groups) if groups else template
            return text

    return None


def watch_log_file():
    """tail -f 式监控 gui.log，解析并推送关键日志行"""
    log_path = CONFIG["log_path"]
    print(f"[LOG] 开始监控日志文件: {log_path}")

    import os, time

    # 等待文件存在
    while not os.path.exists(log_path):
        print(f"[LOG] 等待日志文件出现: {log_path}")
        time.sleep(5)

    # 跳到文件末尾（只看新增内容）
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        f.seek(0, 2)  # seek to end
        print(f"[LOG] 日志文件已就绪，开始监听新增内容")

        pending_line = ""  # 处理跨行的情况（如理智行紧跟在完成任务后）

        while True:
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue

            raw = line.rstrip("\r\n")

            # 处理上一行遗留的 pending（理智数据有时在下一行）
            if pending_line:
                stripped = raw.strip()
                if re.match(r"^理智[:：]", stripped) or re.match(r"^理智将在", stripped):
                    msg = f"💊 {stripped}"
                    queue_log_line(msg)
                pending_line = ""

            msg = parse_log_line(raw)
            if msg is not None:
                queue_log_line(msg)
                # 如果是"完成任务"行，下一行可能有理智信息
                if "完成任务" in raw:
                    pending_line = raw


# ═══════════════════════════════════════════════
#  任务队列管理（远程控制用）
# ═══════════════════════════════════════════════
def dispatch_task(task_type: str, params: str = None) -> str:
    task_id = str(uuid.uuid4())
    task = {"id": task_id, "type": task_type}
    if params is not None:
        task["params"] = params
    with pending_tasks_lock:
        pending_tasks.append(task)
        issued_task_detail[task_id] = task
    print(f"[DISPATCH] 任务入队: {task_type} ({task_id[:8]}...)")
    return task_id


def pop_new_tasks_for_device(device_id: str) -> list:
    result = []
    with pending_tasks_lock:
        for task in pending_tasks:
            tid = task["id"]
            if tid not in issued_tasks[device_id]:
                issued_tasks[device_id].add(tid)
                result.append(task)
    return result


# ═══════════════════════════════════════════════
#  Flask：MAA 的 HTTP 端点
# ═══════════════════════════════════════════════
app = Flask(__name__)


@app.route("/maa/getTask", methods=["POST"])
def get_task():
    data = request.get_json(silent=True) or {}
    device = data.get("device", "unknown")
    user   = data.get("user",   "unknown")
    devices[device] = {"user": user, "last_seen": datetime.now().isoformat()}
    return jsonify({"tasks": pop_new_tasks_for_device(device)})


@app.route("/maa/reportStatus", methods=["POST"])
def report_status():
    data    = request.get_json(silent=True) or {}
    task_id = data.get("task",   "")
    status  = data.get("status", "UNKNOWN")
    task_type = issued_task_detail.get(task_id, {}).get("type", "UnknownTask")
    print(f"[REPORT] task={task_type} status={status}")
    return jsonify({"ok": True})


@app.route("/maa/status", methods=["GET"])
def status_api():
    return jsonify({"devices": devices, "pending_tasks": len(pending_tasks)})


# ═══════════════════════════════════════════════
#  NcatBot：监听管理员私聊指令
# ═══════════════════════════════════════════════
@bot.on_private_message()
async def on_private_message(event: PrivateMessageEvent):
    global bot_loop
    if bot_loop is None:
        bot_loop = asyncio.get_event_loop()
        print(f"[INFO] 已捕获 ncatbot 事件循环")

    if event.user_id != CONFIG["admin_qq"]:
        return

    text = event.raw_message.strip()
    print(f"[QQ] 收到管理员消息: {text}")

    if text in ("帮助", "help"):
        await api.post_private_msg(CONFIG["admin_qq"], text=HELP_TEXT)
        return

    for keyword, task_type in QQ_COMMANDS.items():
        if keyword in text:
            task_id = dispatch_task(task_type)
            task_name = TASK_NAMES.get(task_type, task_type)
            await api.post_private_msg(
                CONFIG["admin_qq"],
                text=(
                    f"✅ 指令已下发\n"
                    f"┌ 任务：{task_name}\n"
                    f"└ ID：{task_id[:8]}..."
                )
            )
            return

    await api.post_private_msg(CONFIG["admin_qq"], text="❓ 未识别指令，发「帮助」查看可用命令")


# ═══════════════════════════════════════════════
#  启动入口
# ═══════════════════════════════════════════════
if __name__ == "__main__":
    api = bot.run_backend()

    # 尝试获取 ncatbot 事件循环
    try:
        bot_loop = asyncio.get_event_loop()
        if not bot_loop.is_running():
            bot_loop = None
    except Exception:
        bot_loop = None

    print("=" * 50)
    print(f"[INFO] NcatBot 已启动，机器人 QQ: {CONFIG['bot_qq']}")
    print(f"[INFO] 管理员 QQ: {CONFIG['admin_qq']}")
    print(f"[INFO] 监控日志: {CONFIG['log_path']}")
    print(f"[INFO] MAA 端点: http://{CONFIG['host']}:{CONFIG['port']}/maa/getTask")
    print("=" * 50)

    # 启动日志监控线程
    threading.Thread(target=watch_log_file, daemon=True).start()

    send_private_msg("✅ MAA 通知服务已启动，发送「帮助」查看可用指令")

    print(f"[INFO] HTTP 服务启动于 http://{CONFIG['host']}:{CONFIG['port']}")
    serve(app, host=CONFIG["host"], port=CONFIG["port"], threads=4)

    bot.exit()
