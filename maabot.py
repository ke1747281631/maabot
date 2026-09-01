"""
MAA 运行情况通知 - 基于 napcat HTTP API + MAA Remote Control Schema
本程序由 AI（GitHub Copilot）生成，经人工测试调整。
────────────────────────────────────────────────────────────────────
依赖安装：
  pip install flask waitress pyyaml

使用方式：
  1. 修改下方 CONFIG（bot_qq、admin_qq、log_path 必填）
  2. 运行：python maabot.py
  3. MAA 远程控制填入：
       任务获取端点: http://127.0.0.1:2345/maa/getTask
       任务汇报端点: http://127.0.0.1:2345/maa/reportStatus
  4. napcat 桌面版配置 OneBot11 HTTP Server (端口 3000) + HTTP Client Webhook
     (上报地址 http://127.0.0.1:2345/napcat/event)
"""

import uuid
import threading
import re
import sys
import json
import os
import subprocess
import time
import yaml
import argparse
import traceback
import logging
import secrets
from datetime import datetime
from collections import defaultdict

# ──────────────────────────────────────────────
#  依赖自检：缺失第三方包时给出友好提示并退出
# ──────────────────────────────────────────────
def _check_dependencies():
    """启动前检查第三方依赖，缺失则打印安装提示并退出（exit 1）。"""
    _required = {
        "flask": "flask",
        "waitress": "waitress",
        "yaml": "pyyaml",
    }
    _missing = []
    for _mod, _pkg in _required.items():
        try:
            __import__(_mod)
        except ImportError:
            _missing.append(_pkg)
    if _missing:
        _cmd = f"{sys.executable} -m pip install {' '.join(_missing)}"
        print("=" * 60)
        print("[错误] 缺少以下 Python 依赖:")
        for _p in _missing:
            print(f"       - {_p}")
        print()
        print("       请运行以下命令安装（注意确认是正确的 Python）:")
        print(f"       {_cmd}")
        print()
        print("       或运行同目录下的「运行环境安装.bat」")
        print("=" * 60)
        sys.exit(1)

_check_dependencies()
# ──────────────────────────────────────────────

# 修复 Windows GBK 终端无法输出 emoji 等 Unicode 字符的问题
# （中文 Windows 默认 stdout 编码为 GBK/936，遇到 ✅🛑❓ 等 emoji 会抛 UnicodeEncodeError）
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from flask import Flask, request, jsonify, session, redirect, url_for
from waitress import serve
from werkzeug.security import generate_password_hash, check_password_hash

# ═══════════════════════════════════════════════
#  日志配置：同时输出到控制台和文件
# ═══════════════════════════════════════════════
_LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
_log_file = os.path.join(_LOG_DIR, f"maabot_{datetime.now().strftime('%Y%m%d')}.log")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════
#  命令行参数解析
# ═══════════════════════════════════════════════
_parser = argparse.ArgumentParser(description="MAABot 服务")
_parser.add_argument("--no-qqbot", action="store_true",
                     help="禁用 QQ 推送（独立模式：仅 MAA 监控 + HTTP 服务）")
_args, _ = _parser.parse_known_args()
QQBOT_ENABLED = not _args.no_qqbot

# ═══════════════════════════════════════════════
#  从 config.yaml 读取 QQ 号
# ═══════════════════════════════════════════════
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml"), "r", encoding="utf-8") as _f:
    _yaml_cfg = yaml.safe_load(_f)

CONFIG = {
    "bot_qq":   str(_yaml_cfg.get("bt_uin", "")),   # 从 config.yaml 的 bt_uin 读取
    "admin_qq": str(_yaml_cfg.get("root", "")),      # 从 config.yaml 的 root 读取

    # HTTP 服务配置（GUI 扩展字段，优先读 config.yaml）
    # 0.0.0.0 允许 FRP 穿透访问；若仅需本机可改为 127.0.0.1
    "host": _yaml_cfg.get("http_host", "0.0.0.0"),
    "port": int(_yaml_cfg.get("http_port", 2345)),

    # MAA 路径（优先读 config.yaml GUI 扩展字段）
    "maa_exe":         _yaml_cfg.get("maa_exe",         r"D:\MAA\MAA.exe"),
    "log_path":        _yaml_cfg.get("maa_log_path",    r"D:\MAA\debug\gui.log"),
    "gui_config_path": _yaml_cfg.get("maa_config_path", r"D:\MAA\config\gui.new.json"),

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
    "log_batch_size": int(_yaml_cfg.get("log_batch_size", 5)),
    # 日志推送：最多等待多少秒后强制发送（即使不足 batch_size 条）
    "log_batch_timeout": int(_yaml_cfg.get("log_batch_timeout", 10)),

    # napcat HTTP API（通过 HTTP 与 napcat 桌面版通讯）
    "napcat_http_api_url":   _yaml_cfg.get("napcat_http_api_url", ""),    # 如 http://localhost:3000
    "napcat_http_api_token": _yaml_cfg.get("napcat_http_api_token", ""),  # napcat HTTP Server token
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
    (re.compile(r"任务出错[:：]\s*(.+)"),     "❌ 任务出错：{0}"),
    (re.compile(r"任务已全部完成"),            "🎉 所有任务完成！"),
    (re.compile(r"(理智[:：].+)"),            "💊 {0}"),
    (re.compile(r"当前设施[:：]\s*(.+)"),     "🏭 当前设施：{0}"),
    (re.compile(r"公招识别结果"),             None),   # None = 忽略
    (re.compile(r"(\d+\s*★\s*Tags)"),        "⭐ 公招：{0}"),
    (re.compile(r"掉落统计"),                 None),   # 忽略
    (re.compile(r"(理智将在.+回满)"),         "⏰ {0}"),
    (re.compile(r"(用时\s*\d+h\s*\d+m\s*\d+s)"), "⏱️ {0}"),
    # 肉鸽相关
    (re.compile(r"已开始探索\s*(\d+)\s*次"),  "🎲 已开始第 {0} 次探索"),
    (re.compile(r"(已投资\s*.+存款[:：]\s*\d+)"), "💰 {0}"),
    (re.compile(r"已放弃本次探索"),           "🔄 已放弃本次探索"),
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
    "LinkStart-Mall":          "信用收支",
    "LinkStart-Mission":       "领取奖励",
    "LinkStart-AutoRoguelike": "自动肉鸽",
    "LinkStart-Reclamation":   "生息演算",
    "StopTask":                "停止任务",
    "HeartBeat":               "心跳检测",
}

# ═══════════════════════════════════════════════
#  MAA 任务名称映射
# ═══════════════════════════════════════════════
# gui.new.json 中 TaskQueue 数组的 $type 字段值
ALL_TASKS = [
    "StartUpTask", "FightTask", "InfrastTask", "RecruitTask",
    "MallTask", "AwardTask", "RoguelikeTask", "ReclamationTask",
]
DAILY_TASKS = ["StartUpTask", "FightTask", "InfrastTask", "RecruitTask", "MallTask", "AwardTask"]
ROGUE_TASKS = ["StartUpTask", "RoguelikeTask"]
DAILY_AND_ROGUE = ["StartUpTask", "FightTask", "InfrastTask", "RecruitTask", "MallTask", "AwardTask", "RoguelikeTask"]

# $type -> 中文名称
TASK_TYPE_NAMES = {
    "StartUpTask":     "开始唤醒",
    "FightTask":       "理智作战",
    "InfrastTask":     "基建换班",
    "RecruitTask":     "自动公招",
    "MallTask":        "信用收支",
    "AwardTask":       "领取奖励",
    "RoguelikeTask":   "自动肉鴽",
    "ReclamationTask": "生息演算",
}


def set_maa_task_checks(enabled_tasks: list[str]):
    """
    修改 MAA 的 gui.new.json，设置 TaskQueue 数组中指定任务的 IsEnable=true，其余为 false。
    gui.new.json 是 MAA v6.x 新配置格式，TaskQueue 为对象数组，通过 $type 区分任务类型。
    """
    config_path = CONFIG["gui_config_path"]
    if not os.path.exists(config_path):
        logger.warning(f"[CONFIG] MAA 配置文件不存在: {config_path}")
        return False

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 获取当前配置名
        current = data.get("Current", "Default")
        configs = data.get("Configurations", {})
        if current not in configs:
            logger.warning(f"[CONFIG] MAA 配置 '{current}' 不存在")
            return False

        cfg = configs[current]
        task_queue = cfg.get("TaskQueue", [])

        # 遍历 TaskQueue 数组，根据 $type 设置 IsEnable
        enabled_set = set(enabled_tasks)
        changed = []
        for task in task_queue:
            task_type = task.get("$type", "")
            should_enable = task_type in enabled_set
            task["IsEnable"] = should_enable
            if should_enable:
                changed.append(task.get("Name", task_type))

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info(f"[CONFIG] 已写入 gui.new.json 勾选: {changed}")
        return True
    except Exception as e:
        logger.error(f"[CONFIG] 修改 gui.new.json 失败: {e}\n{traceback.format_exc()}")
        return False


# ═══════════════════════════════════════════════
#  MAA 进程重启
# ═══════════════════════════════════════════════
# 记录上一次写入 gui.json 的任务，只有变更时才重启
_last_task_set: set | None = None
# 记录当前执行的指令模式（如 "全选+肉鸽"），用于日志自动切换
_current_mode: str = ""
# 防抖：上次自动重启的时间戳，避免短时间内重复重启
_last_auto_restart_time: float = 0
# 自动重启冷却期（秒）
_AUTO_RESTART_COOLDOWN = 30

# 状态持久化路径
_STATE_FILE = os.path.join(os.path.dirname(__file__), "data", "state.json")
# GUI 重启 MAA 信号文件
_RESTART_SIGNAL_FILE = os.path.join(os.path.dirname(__file__), "data", "restart_signal.json")
# MAA 重启独立脚本路径
_RESTART_SCRIPT = os.path.join(os.path.dirname(__file__), "restart_maa.py")
# MAA GUI 配置文件路径（用于清代理）
_MAA_GUI_JSON = os.path.join(os.path.dirname(CONFIG["gui_config_path"]), "gui.json")
# 重启互斥锁：防止多个线程同时重启 MAA
_restart_lock = threading.Lock()
# 代理备份（用于重启后恢复）
_proxy_backup: dict | None = None

def _load_state() -> dict:
    """从文件加载持久化状态"""
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[WARN] 加载状态文件失败: {e}")
    return {}

def _save_state(state: dict):
    """保存状态到文件"""
    try:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERROR] 保存状态文件失败: {e}")

def _set_mode(mode: str):
    """设置当前模式（内存+持久化）"""
    global _current_mode
    _current_mode = mode
    _save_state({"current_mode": mode, "last_task_set": list(_last_task_set) if _last_task_set else []})

# 启动时恢复状态
_init_state = _load_state()
if _init_state.get("current_mode"):
    _current_mode = _init_state["current_mode"]
    print(f"[STATE] 已恢复上次的运行模式: {_current_mode}")


def _is_maa_running() -> bool:
    """检查 MAA 进程是否在运行"""
    try:
        ret = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq MAA.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        result = "MAA.exe" in ret.stdout
        logger.debug(f"[MAA] 进程检查: {'运行中' if result else '未运行'}")
        return result
    except Exception as e:
        logger.error(f"[MAA] 进程检查失败: {e}")
        return False


def _get_maa_pid() -> int | None:
    """获取 MAA.exe 的 PID，未找到返回 None"""
    try:
        ret = subprocess.run(
            ["wmic", "process", "where", "name='MAA.exe'", "get", "ProcessId", "/format:csv"],
            capture_output=True, text=True, timeout=5,
        )
        for line in ret.stdout.strip().splitlines():
            line = line.strip()
            if line and "," in line:
                parts = line.rsplit(",", 1)
                if parts[-1].strip().isdigit():
                    pid = int(parts[-1].strip())
                    return pid
        return None
    except Exception:
        return None


def _snapshot_maa_log(lines: int = 50) -> str | None:
    """截取 MAA gui.log 最后 N 行用于崩溃诊断（防止被新启动覆盖）"""
    log_path = CONFIG.get("maa_log_path", "")
    if not log_path or not os.path.exists(log_path):
        logger.warning(f"[CRASH] MAA 日志文件不存在: {log_path}")
        return None
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
        # 写入带时间戳的快照文件，避免覆盖
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        snap_dir = os.path.join(os.path.dirname(__file__), "logs", "crash_snapshots")
        os.makedirs(snap_dir, exist_ok=True)
        snap_file = os.path.join(snap_dir, f"gui_log_{ts}.snap.txt")
        with open(snap_file, "w", encoding="utf-8") as f:
            f.writelines(tail)
        logger.info(f"[CRASH] 已保存 MAA 日志快照 ({len(tail)} 行) → {snap_file}")
        # 同时输出最后几行到 maabot 日志
        for ln in tail[-10:]:
            logger.info(f"[CRASH-GUILOG] {ln.rstrip()}")
        return snap_file
    except Exception as e:
        logger.error(f"[CRASH] 截取 MAA 日志失败: {e}")
        return None

def _clear_maa_proxy() -> dict | None:
    """清空 MAA gui.json 中所有位置的 HTTP 代理设置，返回备份。

    MAA v6.x 会使用 VersionUpdate.Proxy 作为全局 HttpClient 代理，
    导致对 127.0.0.1:2345 的远程控制请求也通过代理转发，代理无法访问本机。
    gui.json 中代理可能出现在 Global.VersionUpdate 或 Configurations.Default.VersionUpdate
    两个位置，需全部清理。
    """
    gui_json = _MAA_GUI_JSON
    if not os.path.exists(gui_json):
        logger.debug("[PROXY] gui.json 不存在，跳过代理清理")
        return None
    try:
        with open(gui_json, "r", encoding="utf-8") as f:
            data = json.load(f)

        backup = None
        cleaned_any = False

        # 清理 Global.VersionUpdate.Proxy（MAA 全局配置）
        global_section = data.get("Global", {})
        vu_global = global_section.get("VersionUpdate", {})
        proxy_global = vu_global.get("Proxy", "")
        proxy_type_global = vu_global.get("ProxyType", "")
        if proxy_global:
            backup = {"Proxy": proxy_global, "ProxyType": proxy_type_global}
            vu_global["Proxy"] = ""
            vu_global["ProxyType"] = ""
            cleaned_any = True

        # 清理 Configurations.Default.VersionUpdate.Proxy（MAA 配置实例）
        cfg_default = data.get("Configurations", {}).get("Default", {})
        vu_cfg = cfg_default.get("VersionUpdate", {})
        proxy_cfg = vu_cfg.get("Proxy", "")
        proxy_type_cfg = vu_cfg.get("ProxyType", "")
        if proxy_cfg:
            if not backup:
                backup = {"Proxy": proxy_cfg, "ProxyType": proxy_type_cfg}
            vu_cfg["Proxy"] = ""
            vu_cfg["ProxyType"] = ""
            cleaned_any = True

        if not cleaned_any:
            logger.debug("[PROXY] MAA 未配置代理，无需清理")
            return None

        # 写回文件
        with open(gui_json, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        # 验证：确认写入后文件中的值确实为空
        with open(gui_json, "r", encoding="utf-8") as f:
            verify = json.load(f)
        p1 = verify.get("Global", {}).get("VersionUpdate", {}).get("Proxy", "(missing)")
        p2 = verify.get("Configurations", {}).get("Default", {}).get("VersionUpdate", {}).get("Proxy", "(missing)")
        logger.info(f"[PROXY] 已清空 MAA 代理 (Global={p1!r}, Default={p2!r})，原值: {backup['Proxy']}")
        return backup
    except Exception as e:
        logger.error(f"[PROXY] 清理代理失败: {e}")
        return None


def _restore_maa_proxy(backup: dict | None):
    """恢复 MAA gui.json 中的 HTTP 代理设置（两个位置都恢复）"""
    if not backup:
        return
    gui_json = _MAA_GUI_JSON
    if not os.path.exists(gui_json):
        return
    try:
        with open(gui_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 恢复 Global
        vu_g = data.setdefault("Global", {}).setdefault("VersionUpdate", {})
        vu_g["Proxy"] = backup.get("Proxy", "")
        vu_g["ProxyType"] = backup.get("ProxyType", "")
        # 恢复 Configurations.Default
        vu_c = data.setdefault("Configurations", {}).setdefault("Default", {}).setdefault("VersionUpdate", {})
        vu_c["Proxy"] = backup.get("Proxy", "")
        vu_c["ProxyType"] = backup.get("ProxyType", "")
        with open(gui_json, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"[PROXY] 已恢复 MAA 代理: {backup.get('Proxy')}")
    except Exception as e:
        logger.error(f"[PROXY] 恢复代理失败: {e}")


def _kill_maa_processes() -> bool:
    """彻底结束 MAA.exe 进程树，返回是否成功。

    仅返回 True 表示命令已发出（进程是否真正退出由调用方轮询 _is_maa_running 确认）。
    taskkill 返回 1 通常是权限不足（MAA 以管理员权限运行，而本程序未提权），
    此时会尝试 PowerShell Stop-Process 兜底，并给出明确提示。
    """
    # 方式1: taskkill 强制结束进程树（/t 连带子进程）
    try:
        ret = subprocess.run(
            ["taskkill", "/f", "/t", "/im", "MAA.exe"],
            capture_output=True, text=True, timeout=15,
        )
        logger.debug(f"[MAA] taskkill 返回: {ret.returncode} | {(ret.stdout or ret.stderr).strip()[:120]}")
        if ret.returncode == 0:
            logger.info("[MAA] taskkill 已执行（进程树）")
            return True
        logger.warning(f"[MAA] taskkill 失败(返回 {ret.returncode}): {(ret.stderr or ret.stdout).strip()[:150]}")
    except Exception as e:
        logger.warning(f"[MAA] taskkill 执行异常: {e}")

    # 方式2: PowerShell Stop-Process 兜底（错误信息更明确，可区分权限不足）
    try:
        ps_script = "Stop-Process -Name MAA -Force -ErrorAction Stop"
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=15,
        )
        if ps.returncode == 0:
            logger.info("[MAA] PowerShell Stop-Process 成功")
            return True
        perr = (ps.stderr or ps.stdout).strip()
        logger.warning(f"[MAA] Stop-Process 失败(返回 {ps.returncode}): {perr[:200]}")
        if "denied" in perr.lower() or "拒绝" in perr:
            logger.error("[MAA] ⚠️ 权限不足：MAA 可能以管理员权限运行，而本程序未以管理员权限运行！")
            logger.error("[MAA] 请以管理员权限启动 GUI 或 maabot.py，否则无法重启 MAA")
    except Exception as e:
        logger.warning(f"[MAA] Stop-Process 执行异常: {e}")
    return False


def restart_maa(tasks: list[str] | None = None) -> bool:
    """关闭并重启 MAA，等待它连接到我们的端点。

    tasks 参数：若指定，会在旧进程确认退出后（启动新进程前）写入任务配置，
    避免被 MAA 运行中的配置写回覆盖。
    """
    global _proxy_backup
    maa_exe = CONFIG["maa_exe"]

    # 获取重启锁（非阻塞，如果已有重启在进行中则跳过）
    if not _restart_lock.acquire(blocking=False):
        logger.warning("[MAA] ========== 重启已在执行中，跳过本次请求 ==========")
        return False

    try:
        logger.info("=" * 50)
        logger.info("[MAA] ========== 开始重启 MAA ==========")

        # 检查 MAA 可执行文件是否存在
        if not os.path.exists(maa_exe):
            logger.error(f"[MAA] 可执行文件不存在: {maa_exe}")
            return False

        logger.info(f"[MAA] 检查进程状态...")
        was_running = _is_maa_running()
        logger.info(f"[MAA] MAA 进程状态: {'运行中' if was_running else '未运行'}")

        # 关闭 MAA（多种方式彻底杀死，防止"假重启"：旧实例未被杀掉时
        # 新进程会检测到已有实例并激活旧窗口退出，旧实例仍按旧配置执行）
        old_pid = _get_maa_pid() if was_running else None
        if was_running:
            logger.info("[MAA] 正在关闭 MAA...")
            killed = _kill_maa_processes()
            if not killed:
                logger.error("[MAA] ========== 无法终止 MAA 进程，中止重启 ==========")
                logger.error("[MAA] 请以管理员权限运行本程序（GUI 或 maabot.py）后重试")
                _restore_maa_proxy(_proxy_backup)
                return False

        # 等待 MAA 进程真正退出（最多 15 秒）
        logger.info("[MAA] 等待进程退出...")
        process_exited = False
        for i in range(15):
            time.sleep(1)
            if not _is_maa_running():
                logger.info(f"[MAA] 进程已退出（等待了 {i+1} 秒）")
                process_exited = True
                break

        if not process_exited:
            logger.error("[MAA] ========== 进程未完全退出（15秒超时），中止重启 ==========")
            logger.error("[MAA] 请手动关闭 MAA 或以管理员权限运行本程序后重试")
            _restore_maa_proxy(_proxy_backup)
            return False

        # 额外等待 2 秒，确保 MAA 文件锁和资源完全释放
        time.sleep(2)

        # 先清空 devices
        logger.debug(f"[MAA] 清空 devices（重启前数量: {len(devices)}）")
        devices.clear()
        logger.debug("[MAA] devices 已清空")

        # ★ 清空 MAA 代理设置（关键修复）
        # MAA 的 VersionUpdate.Proxy 会作为全局 HttpClient 代理，导致
        # 对 127.0.0.1:2345 的远程控制请求也走代理，代理无法访问本机。
        _proxy_backup = _clear_maa_proxy()

        # ★ 旧进程已确认退出，此刻写入任务配置最安全
        # （MAA 已死，不会再有运行中的配置写回覆盖；新进程启动时读到最新勾选）
        if tasks is not None:
            logger.info(f"[MAA] 写入任务配置: {tasks}")
            set_maa_task_checks(tasks)

        # 使用 os.startfile 启动 MAA（最简单可靠）
        logger.info(f"[MAA] 启动 MAA: {maa_exe}")
        logger.info(f"[MAA] 工作目录: {os.path.dirname(maa_exe)}")
        # 记录启动前 gui.json 修改时间
        _gui_mtime = os.path.getmtime(_MAA_GUI_JSON) if os.path.exists(_MAA_GUI_JSON) else 0
        from datetime import datetime
        logger.info(f"[MAA] gui.json 修改时间(启动前): {datetime.fromtimestamp(_gui_mtime)}")
        try:
            os.startfile(maa_exe)
            logger.info("[MAA] MAA 启动命令已执行（os.startfile）")
        except Exception as e:
            logger.error(f"[MAA] 启动失败: {e}\n{traceback.format_exc()}")
            _restore_maa_proxy(_proxy_backup)
            return False

        # 先等待 MAA 进程出现（最多 15 秒）
        logger.info("[MAA] 等待 MAA 进程出现...")
        process_appeared = False
        maa_pid = None
        for i in range(15):
            time.sleep(1)
            if _is_maa_running():
                maa_pid = _get_maa_pid()
                logger.info(f"[MAA] MAA 进程已出现（等待了 {i+1} 秒, PID={maa_pid}）")
                process_appeared = True
                break

        if not process_appeared:
            logger.error("[MAA] ========== MAA 进程未出现（15秒超时）==========")
            _restore_maa_proxy(_proxy_backup)
            return False

        # 校验 PID：若 PID 与重启前相同，说明旧实例未死透（或新进程复用了实例），视为重启失败
        if old_pid is not None and maa_pid == old_pid:
            logger.error(f"[MAA] ========== MAA PID 未变化（{old_pid}），疑似旧实例仍在运行，重启失败 ==========")
            _restore_maa_proxy(_proxy_backup)
            return False

        # 再等 3 秒让 MAA 完成初始化（加载配置、启动远程控制等）
        logger.info("[MAA] 等待 MAA 初始化（3 秒）...")
        time.sleep(3)

        # 检查初始化后进程是否还活着
        if not _is_maa_running():
            maa_pid = _get_maa_pid()
            logger.error(f"[MAA] ========== MAA 初始化期间退出！PID={maa_pid}（启动后 ~3秒）==========")
            _snapshot_maa_log(80)
            _restore_maa_proxy(_proxy_backup)
            return False

        # 等待 MAA 连接到我们的端点（前30秒每秒检查存活，之后降低频率）
        logger.info("[MAA] 等待 MAA 远程控制就绪...")
        crash_detected = False
        for i in range(90):
            time.sleep(1)

            # 每 3 秒检查一次进程存活（前 30 秒高频检测），之后每 5 秒
            check_interval = 3 if i < 30 else 5
            if i % check_interval == 0 or i < 5:
                if not _is_maa_running():
                    total_sec = i + 1 + 3
                    maa_pid = _get_maa_pid()
                    logger.error(f"[MAA] ========== MAA 进程意外退出！（启动后 {total_sec} 秒, PID最后={maa_pid}）==========")
                    # ★ 截取 MAA 日志快照（关键：在新 MAA 覆盖之前保存）
                    _snapshot_maa_log(100)
                    # 检查 gui.json 是否被外部修改
                    if os.path.exists(_MAA_GUI_JSON):
                        new_mtime = os.path.getmtime(_MAA_GUI_JSON)
                        logger.error(f"[MAA] gui.json 修改时间(当前): {datetime.fromtimestamp(new_mtime)}")
                        if new_mtime != _gui_mtime:
                            logger.error("[MAA] ⚠️ gui.json 在 MAA 运行期间被修改过！可能是 MAA 自身写回或竞争条件")
                    crash_detected = True
                    break

            if devices:
                total_wait = i + 1 + 3  # 包括初始化等待
                logger.info(f"[MAA] ========== MAA 已就绪（等待了 {total_wait} 秒）==========")
                logger.info(f"[MAA] 已连接设备: {list(devices.keys())}")
                _restore_maa_proxy(_proxy_backup)
                return True
            if i % 10 == 0 and i > 0:
                logger.debug(f"[MAA] 仍在等待连接... ({i+3}秒)")

        if crash_detected:
            _restore_maa_proxy(_proxy_backup)
            return False

        logger.error("[MAA] ========== MAA 启动超时（90秒）==========")
        logger.error("[MAA] 可能原因:")
        logger.error("  1. MAA 配置的远程控制端点未指向本服务")
        logger.error("  2. MAA 启动后崩溃或卡住")
        logger.error("  3. 防火墙阻止了 HTTP 连接")
        _restore_maa_proxy(_proxy_backup)
        return False
    finally:
        _restart_lock.release()



def apply_tasks_and_restart(tasks: list[str]) -> bool:
    """
    写入 gui.json 并在必要时重启 MAA。
    如果任务列表未变化，跳过重启。
    返回 True 表示 MAA 已就绪。
    """
    global _last_task_set
    new_set = set(tasks)
    logger.info("=" * 50)
    logger.info(f"[MAA] apply_tasks_and_restart 被调用")
    logger.info(f"[MAA] 新任务: {tasks}")

    # 总是写入 gui.json（确保持久化）
    logger.info(f"[MAA] 写入配置文件...")
    result = set_maa_task_checks(tasks)
    logger.info(f"[MAA] 配置文件写入结果: {result}")

    # 任务未变 + MAA 在运行 → 不需要重启
    if _last_task_set == new_set and devices:
        logger.info("[MAA] 任务未变更，跳过重启")
        return True

    old_set = _last_task_set
    _last_task_set = new_set
    logger.info(f"[MAA] 任务变更: {old_set} -> {new_set}")

    send_private_msg("⚙️ 正在重启 MAA 并应用新任务配置...")

    # 重启前先发送 StopTask 停止当前任务
    if devices:
        logger.info(f"[MAA] 发送 StopTask（{len(devices)} 个设备）")
        for _ in range(3):
            dispatch_task("StopTask")
        time.sleep(2)  # 等待任务停止

    ok = restart_maa(tasks)
    if not ok:
        send_private_msg("❌ MAA 启动超时，请检查 MAA 状态")
    logger.info(f"[MAA] apply_tasks_and_restart 返回: {ok}")
    return ok


# ═══════════════════════════════════════════════
#  命令预设
# ═══════════════════════════════════════════════
# 每个命令定义：
#   "tasks_to_check": 需要勾选的 MAA 任务列表（写入 gui.json）
#   "remote_cmds":    发送给 MAA 远程控制的命令
#
# LinkStart = MAA 按 gui.json 中勾选的任务一次性运行（单个会话）
# LinkStart-X = 仅运行单个任务（独立会话）
# ═══════════════════════════════════════════════

QQ_COMMANDS = {
    "全选+肉鸽": {"tasks": DAILY_AND_ROGUE, "cmds": ["LinkStart"]},
    "长草+肉鸽": {"tasks": DAILY_AND_ROGUE, "cmds": ["LinkStart"]},
    "全选":      {"tasks": DAILY_TASKS,     "cmds": ["LinkStart"]},
    "开始":      {"tasks": DAILY_TASKS,     "cmds": ["LinkStart"]},
    "长草":      {"tasks": DAILY_TASKS,     "cmds": ["LinkStart"]},
    "基建":      {"tasks": ["InfrastTask"],     "cmds": ["LinkStart"]},
    "公招":      {"tasks": ["RecruitTask"],     "cmds": ["LinkStart"]},
    "作战":      {"tasks": ["FightTask"],       "cmds": ["LinkStart"]},
    "购物":      {"tasks": ["MallTask"],        "cmds": ["LinkStart"]},
    "奖励":      {"tasks": ["AwardTask"],       "cmds": ["LinkStart"]},
    "肉鸽":      {"tasks": ROGUE_TASKS,     "cmds": ["LinkStart"]},
    "停止":      {"tasks": None,            "cmds": ["StopTask"]},
    "心跳":      {"tasks": None,            "cmds": ["HeartBeat"]},
}

HELP_TEXT = (
    "MAA 远程控制指令：\n"
    "  全选+肉鸽 - 日常全部 + 肉鸽（一次性运行）\n"
    "  全选 / 长草 - 日常全部（一次性运行）\n"
    "  肉鸽 - 唤醒 + 自动肉鸽（一次性运行）\n"
    "  基建 / 公招 / 作战 / 购物 / 奖励\n"
    "  停止 - 停止当前任务\n"
    "  心跳 - 检测连接状态\n"
    "  帮助 - 显示此菜单"
)

# ═══════════════════════════════════════════════
#  全局状态
# ═══════════════════════════════════════════════

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
#  NapCat HTTP API 客户端（通过 OneBot11 HTTP 协议与 napcat 桌面版通讯）
# ═══════════════════════════════════════════════
class NapCatHTTPAPI:
    """通过 napcat 的 OneBot11 HTTP API 发送消息。"""

    def __init__(self, base_url: str, token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _request(self, method: str, path: str, data: dict = None) -> dict:
        import urllib.request
        import urllib.error
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = json.dumps(data).encode("utf-8") if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"[ERROR] napcat HTTP API 请求失败 ({path}): {e}")
            return {}

    def send_private_msg(self, user_id: int, message: str) -> bool:
        result = self._request("POST", "/send_private_msg", {
            "user_id": user_id,
            "message": [{"type": "text", "data": {"text": message}}],
        })
        return result.get("status") == "ok"

    def send_group_msg(self, group_id: int, message: str) -> bool:
        result = self._request("POST", "/send_group_msg", {
            "group_id": group_id,
            "message": [{"type": "text", "data": {"text": message}}],
        })
        return result.get("status") == "ok"

    def get_login_info(self) -> dict:
        return self._request("GET", "/get_login_info").get("data", {})


# napcat HTTP API 单例（惰性初始化）
_napcat_http_api: NapCatHTTPAPI | None = None


def _get_napcat_http_api() -> NapCatHTTPAPI | None:
    """返回配置好的 NapCatHTTPAPI 实例，未配置则返回 None。"""
    global _napcat_http_api
    if _napcat_http_api is None and CONFIG.get("napcat_http_api_url"):
        _napcat_http_api = NapCatHTTPAPI(
            CONFIG["napcat_http_api_url"],
            CONFIG.get("napcat_http_api_token", ""),
        )
    return _napcat_http_api


# ═══════════════════════════════════════════════
#  发私聊消息（通过 napcat HTTP API）
# ═══════════════════════════════════════════════
def send_private_msg(text: str):
    if not QQBOT_ENABLED:
        print(f"[NOTIFY] {text[:80]}")
        return

    http_api = _get_napcat_http_api()
    if http_api:
        ok = http_api.send_private_msg(int(CONFIG["admin_qq"]), text)
        if ok:
            print(f"[INFO] 已通知(HTTP): {text[:60]}")
        else:
            print(f"[ERROR] HTTP 通知失败: {text[:60]}")
        return

    # napcat HTTP API 未配置，仅打印
    print(f"[NOTIFY] {text[:80]}")


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


def _write_restart_signal():
    """写入信号文件，通知 GUI 重启 MAA"""
    try:
        os.makedirs(os.path.dirname(_RESTART_SIGNAL_FILE), exist_ok=True)
        signal_data = {
            "action": "restart_maa",
            "timestamp": datetime.now().isoformat(),
        }
        with open(_RESTART_SIGNAL_FILE, "w", encoding="utf-8") as f:
            json.dump(signal_data, f)
        logger.info("[SIGNAL] 已写入 GUI 重启信号文件")
    except Exception as e:
        logger.error(f"[SIGNAL] 写入信号文件失败: {e}")


def _run_restart_script() -> bool:
    """
    调用 restart_maa.py 独立脚本重启 MAA（无窗口）。
    返回 True 表示脚本执行成功。
    """
    logger.info("[RESTART] 调用重启脚本: %s", _RESTART_SCRIPT)
    try:
        ret = subprocess.run(
            [sys.executable, _RESTART_SCRIPT],
            capture_output=True, text=True, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if ret.returncode == 0:
            logger.info("[RESTART] 重启脚本执行成功")
            return True
        else:
            logger.error("[RESTART] 重启脚本返回 %d, stderr: %s", ret.returncode, ret.stderr[:200])
            return False
    except subprocess.TimeoutExpired:
        logger.error("[RESTART] 重启脚本执行超时")
        return False
    except Exception as e:
        logger.error("[RESTART] 调用重启脚本失败: %s", e)
        return False


def watch_log_file():
    """tail -f 式监控 gui.log，解析并推送关键日志行"""
    log_path = CONFIG["log_path"]
    logger.info(f"[LOG] 开始监控日志文件: {log_path}")

    import os, time

    # 等待文件存在
    while not os.path.exists(log_path):
        logger.info(f"[LOG] 等待日志文件出现: {log_path}")
        time.sleep(5)

    # 跳到文件末尾（只看新增内容）
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        f.seek(0, 2)  # seek to end
        logger.info(f"[LOG] 日志文件已就绪，开始监听新增内容")

        pending_line = ""  # 处理跨行的情况（如理智行紧跟在完成任务后）
        last_log_line = ""  # 记录上一条日志内容，用于判断任务类型
        # 肉鸽异常退出检测：本轮肉鸽任务是否已启动、是否已找到常乐节点
        # 使用 _rogue_task_active 而非 _rogue_started（后者依赖「已开始探索」，崩溃时可能不出）
        _rogue_task_active = False   # 「开始任务：自动肉鸽」出现过即为 True
        _rogue_chanle_found = False  # 「发现目标常乐节点」出现过即为 True

        while True:
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue

            raw = line.rstrip("\r\n")

            # 开始新一轮肉鸽任务时：标记本轮肉鸽已启动，并重置常乐检测状态
            if "开始任务" in raw and ("自动肉鸽" in raw or "RoguelikeTask" in raw or "AutoRoguelike" in raw):
                logger.info("[LOG] 检测到肉鸽任务开始，标记 rogue_task_active=True，重置异常检测状态")
                _rogue_task_active = True
                _rogue_chanle_found = False

            # 「已开始探索」仅作补充说明，不再作为异常检测的必要信号
            if "已开始探索" in raw:
                logger.info("[LOG] 肉鸽已开始探索（rogue_task_active=%s）", _rogue_task_active)
            if "发现目标常乐节点" in raw:
                _rogue_chanle_found = True
                logger.info(f"[LOG] ===== 检测到常乐节点 =====")
                logger.info(f"[LOG] 当前行: {raw}")
                logger.info(f"[LOG] 当前模式: {_current_mode}")

                # 从日志行提取时间戳，判断是否为实时日志（非旧日志刷缓存）
                ts_match = re.match(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\]', raw)
                is_recent = False
                if ts_match:
                    try:
                        log_time = datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S")
                        age_sec = (datetime.now() - log_time).total_seconds()
                        is_recent = age_sec < 1800  # 30 分钟内的日志视为实时
                        logger.info(f"[LOG] 日志时间: {log_time}, 距今 {age_sec:.0f} 秒, 判定: {'实时' if is_recent else '过旧'}")
                    except ValueError:
                        logger.info("[LOG] 无法解析日志时间戳")
                else:
                    logger.info("[LOG] 日志行缺少时间戳")

                if not is_recent:
                    # 时间戳过旧或无法解析：用 _current_mode 兜底判断
                    if "肉鸽" not in _current_mode and "rogue" not in _current_mode.lower():
                        logger.info("[LOG] [_auto_switch] 时间戳过旧 且 当前模式 %s 不含肉鸽，跳过", _current_mode)
                        last_log_line = raw
                        continue
                    logger.info("[LOG] [_auto_switch] 时间戳过旧但当前模式含肉鸽，按旧日志处理跳过")
                    last_log_line = raw
                    continue

                queue_log_line("🎯 检测到常乐节点，肉鸽已完成，自动切换为全选任务")

                def _auto_switch():
                    global _last_auto_restart_time
                    # 防抖：冷却期内跳过
                    elapsed = time.time() - _last_auto_restart_time
                    if elapsed < _AUTO_RESTART_COOLDOWN:
                        logger.info("[LOG] [_auto_switch] 冷却期内（%.1fs < %ds），跳过", elapsed, _AUTO_RESTART_COOLDOWN)
                        return
                    logger.info("[LOG] [_auto_switch] 线程启动")
                    try:
                        _set_mode("全选")
                        logger.info("[LOG] [_auto_switch] 状态已设置")
                        _last_task_set = set(DAILY_TASKS)
                        _last_auto_restart_time = time.time()
                        send_private_msg("🔄 已自动从「全选+肉鸽」切换为「全选」任务，正在重启 MAA...")
                        # restart_maa(tasks)：先确认旧进程退出，再写入任务配置，再启动
                        if restart_maa(DAILY_TASKS):
                            dispatch_task("LinkStart")
                            send_private_msg("✅ MAA 已重连，全选任务已下发")
                        else:
                            send_private_msg("⚠️ MAA 重启失败（可能权限不足），请手动检查")
                            # ★ 通知 GUI 进行自动恢复（停服务 → 启 MAA → 启服务）
                            logger.error("[MAA] ⚠️ MAA 重启失败（常乐切换），请求 GUI 恢复")
                    except Exception as e:
                        logger.error(f"[LOG] [_auto_switch] 异常: {e}\n{traceback.format_exc()}")
                threading.Thread(target=_auto_switch, daemon=True).start()

            # 检测所有任务完成：上一条日志包含"已投资"说明刚执行完肉鸽，自动切换为全选
            if "任务已全部完成" in raw and "已投资" in last_log_line:
                logger.info(f"[LOG] ===== 检测到肉鸽任务全部完成 =====")
                logger.info(f"[LOG] 当前行: {raw}")
                logger.info(f"[LOG] 上一行: {last_log_line}")
                logger.info(f"[LOG] 当前模式: {_current_mode}")

                # 从日志行提取时间戳，判断是否为实时日志
                ts_match = re.match(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\]', raw)
                is_recent = False
                if ts_match:
                    try:
                        log_time = datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S")
                        age_sec = (datetime.now() - log_time).total_seconds()
                        is_recent = age_sec < 1800  # 30 分钟内的日志视为实时
                        logger.info(f"[LOG] 日志时间: {log_time}, 距今 {age_sec:.0f} 秒, 判定: {'实时' if is_recent else '过旧'}")
                    except ValueError:
                        logger.info("[LOG] 无法解析日志时间戳")
                else:
                    logger.info("[LOG] 日志行缺少时间戳")

                if not is_recent:
                    # 时间戳过旧或无法解析：用 _current_mode 兜底判断
                    if "肉鸽" not in _current_mode and "rogue" not in _current_mode.lower():
                        logger.info("[LOG] [_auto_switch_on_complete] 时间戳过旧 且 当前模式 %s 不含肉鸽，跳过", _current_mode)
                        last_log_line = raw
                        continue
                    logger.info("[LOG] [_auto_switch_on_complete] 时间戳过旧但当前模式含肉鸽，按旧日志处理跳过")
                    last_log_line = raw
                    continue

                queue_log_line("🎯 肉鸽任务已完成，自动切换为全选任务")

                def _auto_switch_on_complete():
                    global _last_auto_restart_time
                    # 防抖：冷却期内跳过
                    elapsed = time.time() - _last_auto_restart_time
                    if elapsed < _AUTO_RESTART_COOLDOWN:
                        logger.info("[LOG] [_auto_switch_on_complete] 冷却期内（%.1fs < %ds），跳过", elapsed, _AUTO_RESTART_COOLDOWN)
                        return
                    logger.info("[LOG] [_auto_switch_on_complete] 线程启动")
                    try:
                        _set_mode("全选")
                        logger.info("[LOG] [_auto_switch_on_complete] 状态已设置")
                        _last_task_set = set(DAILY_TASKS)
                        _last_auto_restart_time = time.time()
                        send_private_msg("🔄 肉鸽任务已完成，已自动切换为「全选」任务，正在重启 MAA...")
                        # restart_maa(tasks)：先确认旧进程退出，再写入任务配置，再启动
                        if restart_maa(DAILY_TASKS):
                            dispatch_task("LinkStart")
                            send_private_msg("✅ MAA 已重连，全选任务已下发")
                        else:
                            send_private_msg("⚠️ MAA 重启失败（可能权限不足），请手动检查")
                            # ★ 通知 GUI 进行自动恢复（停服务 → 启 MAA → 启服务）
                            logger.error("[MAA] ⚠️ MAA 重启失败（肉鸽完成切换），请求 GUI 恢复")
                    except Exception as e:
                        logger.error(f"[LOG] [_auto_switch_on_complete] 异常: {e}\n{traceback.format_exc()}")

                threading.Thread(target=_auto_switch_on_complete, daemon=True).start()

            # 检测肉鸽异常完成（游戏崩溃）：
            #   - 本轮肉鸽任务已启动（_rogue_task_active）
            #   - 且从未找到常乐节点（_rogue_chanle_found）
            #   - 且上一条日志不含「已投资」（排除正常完成路径，避免双触发）
            #   - 不依赖 _current_mode（该字段更新滞后，不可靠）
            if ("任务已全部完成" in raw
                    and _rogue_task_active
                    and not _rogue_chanle_found
                    and "已投资" not in last_log_line):
                # 时间戳实时检查（窗口放宽到 2 小时，避免用户睡一觉起来后失效）
                ts_match2 = re.match(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\]', raw)
                is_recent2 = False
                age_sec2 = -1
                if ts_match2:
                    try:
                        log_time2 = datetime.strptime(ts_match2.group(1), "%Y-%m-%d %H:%M:%S")
                        age_sec2 = (datetime.now() - log_time2).total_seconds()
                        is_recent2 = age_sec2 < 7200  # 2 小时
                    except ValueError:
                        pass
                else:
                    # 没时间戳的日志：放宽到允许（兜底）
                    is_recent2 = True

                logger.info(
                    "[LOG] [异常完成检测] 命中: rogue_task_active=%s, chanle_found=%s, "
                    "current_mode=%s, age=%.0fs, recent=%s, raw=%s",
                    _rogue_task_active, _rogue_chanle_found, _current_mode,
                    age_sec2, is_recent2, raw[:120],
                )

                if is_recent2:
                    logger.info("[LOG] ===== 检测到肉鸽异常完成（未找到常乐节点）=====")
                    queue_log_line("⚠️ 肉鸽异常退出（未找到常乐节点），重启明日方舟并重新下发任务")
                    # 重置状态，防止重复触发
                    _rogue_task_active = False
                    _rogue_chanle_found = False

                    def _auto_recover_rogue_crash():
                        global _last_auto_restart_time
                        elapsed = time.time() - _last_auto_restart_time
                        if elapsed < _AUTO_RESTART_COOLDOWN:
                            logger.info("[LOG] [_recover_rogue] 冷却期内（%.1fs < %ds），跳过",
                                        elapsed, _AUTO_RESTART_COOLDOWN)
                            return
                        logger.info("[LOG] [_recover_rogue] 线程启动")
                        try:
                            _last_auto_restart_time = time.time()
                            send_private_msg("⚠️ 检测到明日方舟异常退出（肉鸽未完成），正在重启 MAA 并重新开始...")
                            if restart_maa():
                                dispatch_task("LinkStart")
                                send_private_msg("✅ MAA 已重连，肉鸽任务已重新下发")
                            else:
                                send_private_msg("⚠️ MAA 重启超时（90s），请手动检查")
                        except Exception as e:
                            logger.error(f"[LOG] [_recover_rogue] 异常: {e}\n{traceback.format_exc()}")

                    threading.Thread(target=_auto_recover_rogue_crash, daemon=True).start()

            # ── 模拟器断开检测 ──
            # MAA 日志出现"重连失败，连接断开"或"截图失败.*重启或更换模拟器"
            # 说明 ADB 连接已彻底断开，MAA 无法继续工作
            # 需要重启 MAA（会重新建立 adb 连接）并重新下发任务
            _emulator_crash_keywords = ("重连失败，连接断开", "截图失败")
            if any(kw in raw for kw in _emulator_crash_keywords):
                logger.info("[LOG] ===== 检测到模拟器断开 =====")
                logger.info("[LOG] 当前行: %s", raw[:150])
                queue_log_line("⚠️ 模拟器连接断开，正在自动重启 MAA 并恢复任务")

                def _auto_recover_emulator_crash():
                    global _last_auto_restart_time
                    elapsed = time.time() - _last_auto_restart_time
                    if elapsed < _AUTO_RESTART_COOLDOWN:
                        logger.info("[LOG] [_recover_emulator] 冷却期内（%.1fs < %ds），跳过",
                                    elapsed, _AUTO_RESTART_COOLDOWN)
                        return
                    logger.info("[LOG] [_recover_emulator] 线程启动")
                    try:
                        _last_auto_restart_time = time.time()
                        send_private_msg("⚠️ 检测到模拟器连接断开，正在重启 MAA 并恢复任务...")
                        if restart_maa():
                            dispatch_task("LinkStart")
                            send_private_msg("✅ MAA 已重连，任务已重新下发")
                        else:
                            # restart_maa 失败 → 通知 GUI 接管恢复
                            logger.error("[MAA] ⚠️ MAA 重启失败（模拟器断开），请求 GUI 恢复")
                            send_private_msg("⚠️ MAA 重启超时（90s），请手动检查")
                    except Exception as e:
                        logger.error(f"[LOG] [_recover_emulator] 异常: {e}\n{traceback.format_exc()}")

                threading.Thread(target=_auto_recover_emulator_crash, daemon=True).start()

            last_log_line = raw

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
        # StopTask：先清空所有待发任务，避免停止后又执行残留任务
        if task_type == "StopTask":
            pending_tasks.clear()
            issued_tasks.clear()
        pending_tasks.append(task)
        issued_task_detail[task_id] = task
    print(f"[DISPATCH] 任务入队: {task_type} ({task_id[:8]}...)")
    return task_id


def pop_new_tasks_for_device(device_id: str) -> list:
    """取出尚未发送给该设备的任务，并清理已发送的旧任务。"""
    result = []
    with pending_tasks_lock:
        for task in pending_tasks:
            tid = task["id"]
            if tid not in issued_tasks[device_id]:
                issued_tasks[device_id].add(tid)
                result.append(task)
        # 清理已发送给所有设备的旧任务（防止 pending_tasks 无限增长）
        if devices and pending_tasks:
            all_device_ids = set(issued_tasks.keys())
            pending_tasks[:] = [
                t for t in pending_tasks
                if not all(t["id"] in issued_tasks[d] for d in all_device_ids)
            ]
    return result


# ═══════════════════════════════════════════════
#  Flask：MAA 的 HTTP 端点
# ═══════════════════════════════════════════════
app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# ── WebUI 登录认证 ────────────────────────────────
# 默认用户名（首次运行时写入 config.yaml，密码随机生成）
_WEUI_DEFAULT_USER = "admin"

def _generate_random_password(length: int = 16) -> str:
    """生成随机密码"""
    import string as _string
    alphabet = _string.ascii_letters + _string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))

def _get_auth_config() -> dict:
    """从 config.yaml 读取 WebUI 认证配置，不存在则随机生成密码并写入"""
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        changed = False
        if "webui_user" not in cfg:
            cfg["webui_user"] = _WEUI_DEFAULT_USER
            changed = True
        if "webui_password_hash" not in cfg or not cfg.get("webui_password_hash"):
            # 首次运行：随机生成密码，避免硬编码默认密码的安全风险
            random_pw = _generate_random_password()
            cfg["webui_password_hash"] = generate_password_hash(random_pw)
            changed = True
            print(f"[AUTH] WebUI 首次初始化，随机生成密码:")
            print(f"       用户名: {cfg['webui_user']}")
            print(f"       密码:   {random_pw}")
            print(f"       (请妥善保存，可在 WebUI 中修改密码)")
        if changed:
            with open(cfg_path, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        return cfg
    except Exception as e:
        print(f"[AUTH] 读取认证配置失败: {e}")
        return {}

def _check_auth() -> bool:
    """检查当前 session 是否已登录"""
    return session.get("webui_user") is not None

def _save_password_hash(new_hash: str) -> bool:
    """将新密码哈希写入 config.yaml"""
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        cfg["webui_password_hash"] = new_hash
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        print("[AUTH] 密码已更新")
        return True
    except Exception as e:
        print(f"[AUTH] 保存密码失败: {e}")
        return False

# 不再使用模块级变量，改为每次从文件读取（修改密码后即时生效）
# _AUTH_CFG 删除，各接口改用 _get_auth_config() 动态读取

@app.before_request
def _auth_before_request():
    """拦截所有 API 和 WebUI 页面请求，未登录则拒绝"""
    path = request.path
    # 放行：登录/登出/状态接口 + MAA 远程控制端点（由 MAA 调用，无 session）
    white_paths = {
        "/api/login", "/api/logout", "/api/auth/status",
        "/maa/getTask", "/maa/reportStatus", "/maa/status",
    }
    if path in white_paths:
        return
    # API 请求：检查 session
    if path.startswith("/api/"):
        if not _check_auth():
            return jsonify({"ok": False, "error": "未登录", "code": 401}), 401
        return
    # WebUI 页面：未登录时返回登录页
    if path.startswith("/webui"):
        if not _check_auth():
            return _LOGIN_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}
        return

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "")
    password = data.get("password", "")
    cfg = _get_auth_config()
    expected_user = cfg.get("webui_user", _WEUI_DEFAULT_USER)
    expected_hash = cfg.get("webui_password_hash", "")
    if username == expected_user and expected_hash and check_password_hash(expected_hash, password):
        session["webui_user"] = username
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "账号或密码错误"}), 401

@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("webui_user", None)
    return jsonify({"ok": True})

@app.route("/api/auth/change-password", methods=["POST"])
def api_change_password():
    """修改密码：需先验证旧密码"""
    if not _check_auth():
        return jsonify({"ok": False, "error": "未登录"}), 401
    data = request.get_json(silent=True) or {}
    old_pw = data.get("old_password", "")
    new_pw = data.get("new_password", "")
    if not old_pw or not new_pw:
        return jsonify({"ok": False, "error": "请填写完整"}), 400
    cfg = _get_auth_config()
    expected_hash = cfg.get("webui_password_hash", "")
    if not expected_hash or not check_password_hash(expected_hash, old_pw):
        return jsonify({"ok": False, "error": "旧密码错误"}), 400
    new_hash = generate_password_hash(new_pw)
    if _save_password_hash(new_hash):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "保存失败"}), 500

@app.route("/api/auth/status", methods=["GET"])
def api_auth_status():
    """返回当前登录状态"""
    return jsonify({"logged_in": _check_auth(), "user": session.get("webui_user", "")})


# ── 登录页 HTML（未登录时直接返回，不依赖前端 JS）────────────────────────────────
_LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MAABot 登录</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    font-family: 'Segoe UI', '微软雅黑', sans-serif;
    background: #0f0f1a;
    color: #cdd6f4;
    min-height: 100vh;
    display: flex; align-items: center; justify-content: center;
  }
  .login-box {
    background: #1a1a2e;
    border: 1px solid #2e2e4a;
    border-radius: 16px;
    padding: 40px 36px;
    width: 360px;
    box-shadow: 0 8px 32px rgba(0,0,0,.4);
  }
  .login-box h1 { text-align:center; font-size:22px; margin-bottom:28px; }
  .login-box h1 span { color: #7c6af7; }
  .field { margin-bottom:18px; }
  .field label { display:block; font-size:13px; color:#6c7086; margin-bottom:6px; }
  .field input {
    width:100%; padding:10px 14px;
    background:#0a0a14; border:1px solid #2e2e4a; border-radius:8px;
    color:#cdd6f4; font-size:14px; outline:none; transition:border-color .2s;
  }
  .field input:focus { border-color:#7c6af7; }
  .btn-login {
    width:100%; padding:11px; margin-top:8px;
    background:#7c6af7; color:#fff; border:none; border-radius:8px;
    font-size:15px; font-weight:600; cursor:pointer; transition:background .15s;
  }
  .btn-login:hover { background:#5a4cc7; }
  .btn-login:active { transform:scale(.97); }
  .err-msg { color:#f38ba8; font-size:13px; text-align:center; margin-top:12px; min-height:20px; }
</style>
</head>
<body>
<div class="login-box">
  <h1>⚙ <span>MAABot</span> 登录</h1>
  <div class="field">
    <label>账号</label>
    <input id="username" type="text" placeholder="请输入账号" autocomplete="username">
  </div>
  <div class="field">
    <label>密码</label>
    <input id="password" type="password" placeholder="请输入密码" autocomplete="current-password">
  </div>
  <button class="btn-login" onclick="doLogin()">登  录</button>
  <div class="err-msg" id="errMsg"></div>
</div>
<script>
function doLogin() {
  const u = document.getElementById('username').value.trim();
  const p = document.getElementById('password').value;
  const err = document.getElementById('errMsg');
  if (!u || !p) { err.textContent = '请输入账号和密码'; return; }
  fetch('/api/login', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({username:u, password:p}),
  }).then(r=>r.json()).then(d=>{
    if (d.ok) window.location.href = '/webui/';
    else err.textContent = d.error || '登录失败';
  }).catch(e=>{ err.textContent = '网络错误'; });
}
document.getElementById('password').addEventListener('keydown', e=>{ if(e.key==='Enter') doLogin(); });
document.getElementById('username').addEventListener('keydown', e=>{ if(e.key==='Enter') doLogin(); });
</script>
</body>
</html>"""


@app.route("/maa/getTask", methods=["POST"])
def get_task():
    data = request.get_json(silent=True) or {}
    device = data.get("device", "unknown")
    user   = data.get("user",   "unknown")
    devices[device] = {"user": user, "last_seen": datetime.now().isoformat()}
    tasks = pop_new_tasks_for_device(device)
    if tasks:
        print(f"[getTask] 返回给 MAA 的任务: {[t['type'] for t in tasks]}")
    return jsonify({"tasks": tasks})


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
#  WebUI API 端点
# ═══════════════════════════════════════════════
import urllib.parse

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

# 日志 SSE 订阅者
_log_subscribers: list = []
_log_sub_lock = threading.Lock()
# 日志历史缓冲（本次运行的全部日志）
_log_history: list = []
_LOG_HISTORY_MAX = 5000


def _broadcast_log(line: str):
    """向所有 SSE 订阅者广播日志行，同时存入历史"""
    with _log_sub_lock:
        # 存历史
        _log_history.append(line)
        if len(_log_history) > _LOG_HISTORY_MAX:
            _log_history.pop(0)
        # 广播
        dead = []
        for q in _log_subscribers:
            try:
                q.put_nowait(line)
            except Exception:
                dead.append(q)
        for q in dead:
            _log_subscribers.remove(q)


# ── WebUI 页面 ──────────────────────────────
@app.route("/webui")
@app.route("/webui/")
def webui_page():
    from flask import send_from_directory
    return send_from_directory(WEB_DIR, "index.html")


# ── 综合状态 ────────────────────────────────
@app.route("/api/status", methods=["GET"])
def api_status():
    # MAA 进程状态
    try:
        ret = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq MAA.exe"],
            capture_output=True, text=True, timeout=3,
        )
        maa_running = "MAA.exe" in ret.stdout
    except Exception:
        maa_running = False

    return jsonify({
        "service_running": True,  # 这个 API 能响应本身就说明服务在跑
        "maa_running": maa_running,
        "device_count": len(devices),
        "pending_count": len(pending_tasks),
        "napcat_http": True,
        "qqbot_enabled": QQBOT_ENABLED,
        "mode": "qqbot" if QQBOT_ENABLED else "standalone",
    })


# ── 配置读写 ────────────────────────────────
@app.route("/api/config", methods=["GET"])
def api_get_config():
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/config", methods=["POST"])
def api_set_config():
    data = request.get_json(silent=True) or {}
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    try:
        # 读取现有配置，合并更新
        existing = {}
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}
        existing.update(data)
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.dump(existing, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        print(f"[CONFIG] WebUI 更新配置: {list(data.keys())}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── MAA 操作 ────────────────────────────────
@app.route("/api/maa/dispatch", methods=["POST"])
def api_maa_dispatch():
    data = request.get_json(silent=True) or {}
    # 停止任务
    if data.get("stop"):
        for _ in range(5):
            dispatch_task("StopTask")
        return jsonify({"ok": True})

    # 任务下发
    tasks = data.get("tasks", [])
    if not tasks:
        return jsonify({"ok": False, "error": "未指定任务"}), 400

    def _do():
        ok = apply_tasks_and_restart(tasks)
        if ok:
            _set_mode("WebUI")  # 持久化状态
            dispatch_task("LinkStart")
            send_private_msg("🌐 WebUI 下发任务，已重启 MAA")

    threading.Thread(target=_do, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/maa/write-config", methods=["POST"])
def api_maa_write_config():
    data = request.get_json(silent=True) or {}
    tasks = data.get("tasks", [])
    if not tasks:
        return jsonify({"ok": False, "error": "未指定任务"}), 400
    ok = set_maa_task_checks(tasks)
    return jsonify({"ok": ok})


@app.route("/api/maa/restart", methods=["POST"])
def api_maa_restart():
    def _do():
        ok = restart_maa()
        if ok:
            send_private_msg("🌐 WebUI 重启了 MAA")
    threading.Thread(target=_do, daemon=True).start()
    return jsonify({"ok": True})


# ── 服务级控制 ──────────────────────────────
@app.route("/api/service/start", methods=["POST"])
def api_service_start():
    """启动服务（启动 MAA 进程）"""
    def _do():
        ok = restart_maa()
        if ok:
            send_private_msg("🌐 WebUI 启动了服务")
    threading.Thread(target=_do, daemon=True).start()
    return jsonify({"ok": True, "running": True})


@app.route("/api/service/stop", methods=["POST"])
def api_service_stop():
    """停止服务（杀掉 MAA 进程，清空任务）"""
    try:
        # 停止 MAA
        subprocess.run(["taskkill", "/F", "/IM", "MAA.exe"],
                        capture_output=True, timeout=10)
        devices.clear()
        pending_tasks.clear()
        issued_tasks.clear()
        print("[WebUI] 服务已停止")
        return jsonify({"ok": True, "running": False})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/service/restart", methods=["POST"])
def api_service_restart():
    """重启服务（重启 MAA 进程）"""
    data = request.get_json(silent=True) or {}
    def _do():
        ok = restart_maa()
        if ok:
            send_private_msg("🌐 WebUI 重启了服务")
    threading.Thread(target=_do, daemon=True).start()
    return jsonify({"ok": True, "running": True})


# ── 日志历史 ────────────────────────────────
@app.route("/api/logs/history", methods=["GET"])
def api_logs_history():
    """返回本次运行的完整日志历史"""
    tail = request.args.get("tail", default=0, type=int)  # 0=全部, N=最近N条
    with _log_sub_lock:
        if tail > 0:
            lines = _log_history[-tail:]
        else:
            lines = _log_history[:]
    return jsonify({"lines": lines, "total": len(_log_history)})


# ── 日志 SSE ────────────────────────────────
@app.route("/api/logs/stream", methods=["GET"])
def api_logs_stream():
    import queue as _queue

    q = _queue.Queue(maxsize=500)
    with _log_sub_lock:
        _log_subscribers.append(q)

    def generate():
        try:
            while True:
                try:
                    line = q.get(timeout=30)
                    yield f"data: {line}\n\n"
                except _queue.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            with _log_sub_lock:
                if q in _log_subscribers:
                    _log_subscribers.remove(q)

    return app.response_class(generate(), mimetype="text/event-stream",
                              headers={"Cache-Control": "no-cache",
                                       "X-Accel-Buffering": "no"})


# ── 劫持 print，让日志也推送到 WebUI SSE ───
_original_print = print
def _patched_print(*args, **kwargs):
    _original_print(*args, **kwargs)
    # 广播到 SSE
    try:
        text = " ".join(str(a) for a in args)
        _broadcast_log(text)
    except Exception:
        pass

# 替换全局 print
import builtins
builtins.print = _patched_print


# ═══════════════════════════════════════════════
#  私聊消息处理（napcat HTTP Webhook 调用）
# ═══════════════════════════════════════════════
def _handle_private_message(user_id, text: str):
    """处理管理员私聊消息，通过 napcat HTTP API 回复。"""
    if str(user_id) != str(CONFIG["admin_qq"]):
        return

    text = text.strip()
    print(f"[QQ] 收到管理员消息: {text}")

    if text in ("帮助", "help"):
        send_private_msg(HELP_TEXT)
        return

    for keyword, cmd_def in QQ_COMMANDS.items():
        if keyword in text:
            tasks = cmd_def["tasks"]

            if tasks is not None:
                # 在线程中执行重启和任务下发，避免阻塞
                task_desc = "、".join(TASK_TYPE_NAMES.get(t, t) for t in tasks)

                def _do_restart_and_dispatch(kw=keyword, ts=tasks, desc=task_desc):
                    ok = apply_tasks_and_restart(ts)
                    if ok:
                        _set_mode(kw)  # 持久化状态
                        dispatch_task("LinkStart")
                        send_private_msg(
                            f"✅ 指令已下发\n"
                            f"┌ 模式：{kw}\n"
                            f"└ 任务：{desc}"
                        )

                threading.Thread(target=_do_restart_and_dispatch, daemon=True).start()
                send_private_msg(f"📨 收到指令「{keyword}」，正在配置任务...")
            else:
                # 控制命令（停止/心跳）
                if "StopTask" in cmd_def["cmds"]:
                    # StopTask 需要反复发送：MAA 内部顺序队列中的 LinkStart
                    # 可能在首次 Stop 后才开始执行，需要多次覆盖
                    def _do_stop():
                        for i in range(5):
                            dispatch_task("StopTask")
                            if i < 4:
                                time.sleep(2)
                        send_private_msg("🛑 停止指令已连续发送完毕")

                    threading.Thread(target=_do_stop, daemon=True).start()
                    send_private_msg("🛑 正在停止 MAA 任务（持续发送中）...")
                else:
                    for cmd in cmd_def["cmds"]:
                        dispatch_task(cmd)
                    task_desc = "、".join(TASK_NAMES.get(c, c) for c in cmd_def["cmds"])
                    send_private_msg(f"✅ 已发送：{task_desc}")
            return

    send_private_msg("❓ 未识别指令，发「帮助」查看可用命令")


# ═══════════════════════════════════════════════
#  napcat HTTP Webhook 端点（接收 napcat 桌面版的 OneBot11 事件上报）
# ═══════════════════════════════════════════════
@app.route("/napcat/event", methods=["POST"])
def napcat_event():
    """接收 napcat 的 OneBot11 HTTP Webhook 事件上报。"""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "invalid json"}), 400

    # 独立模式：忽略所有消息事件
    if not QQBOT_ENABLED:
        return jsonify({"status": "ok"})

    post_type = data.get("post_type")
    if post_type == "message":
        message_type = data.get("message_type")
        if message_type == "private":
            user_id = data.get("user_id")
            raw_message = data.get("raw_message", "")
            # 在线程中处理，避免阻塞 Flask 请求
            threading.Thread(
                target=_handle_private_message,
                args=(user_id, raw_message),
                daemon=True,
            ).start()

    return jsonify({"status": "ok"})


# ═══════════════════════════════════════════════
#  启动入口
# ═══════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 50)
    mode_str = "QQ Bot + MAA 监控" if QQBOT_ENABLED else "独立模式（仅 MAA 监控，无 QQ 推送）"
    print(f"[INFO] 运行模式: {mode_str}")
    print(f"[INFO] 机器人 QQ: {CONFIG['bot_qq']}")
    print(f"[INFO] 管理员 QQ: {CONFIG['admin_qq']}")
    print(f"[INFO] 监控日志: {CONFIG['log_path']}")
    print(f"[INFO] MAA 端点: http://{CONFIG['host']}:{CONFIG['port']}/maa/getTask")
    print(f"[INFO] napcat API: {CONFIG.get('napcat_http_api_url', '(未配置)')}")
    print("=" * 50)

    # 检测 MAA 状态（不再自动启动/修改配置，由 GUI 或用户手动控制）
    try:
        ret = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq MAA.exe"],
            capture_output=True, text=True, timeout=5,
        )
        maa_running = "MAA.exe" in ret.stdout
    except Exception:
        maa_running = False

    if maa_running:
        print("[MAA] MAA 已在运行")
    else:
        print("[MAA] MAA 未运行，可通过 GUI「重启 MAA」按钮或手动启动")

    # 启动日志监控线程
    threading.Thread(target=watch_log_file, daemon=True).start()

    send_private_msg("✅ MAA 通知服务已启动，发送「帮助」查看可用指令")

    print(f"[INFO] HTTP 服务启动于 http://{CONFIG['host']}:{CONFIG['port']}")
    serve(app, host=CONFIG["host"], port=CONFIG["port"], threads=4)