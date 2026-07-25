"""
MAABot GUI 控制面板
────────────────────────────────────────────────────────────────────
功能：
  - 启动 / 停止 / 重启 maabot 服务
  - 独立模式（禁用 QQ Bot，仅 MAA 监控 + HTTP 服务）
  - 图形化配置：MAA 路径、端口、QQ 号、日志批次等
  - 实时日志输出（stdout 重定向到 GUI）
  - MAA 快捷操作（通过 WebUI API 下发任务）
  - FRP 内网穿透管理 + WebUI 一键打开浏览器
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import subprocess
import sys
import os
import yaml
import json
import time
import queue
import signal
import webbrowser
from datetime import datetime

# ─────────────────────────────────────────────
#  路径常量
# ─────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
MAABOT_SCRIPT = os.path.join(BASE_DIR, "maabot.py")
RESTART_SCRIPT = os.path.join(BASE_DIR, "restart_maa.py")
RESTART_LOG = os.path.join(BASE_DIR, "logs", "restart_maa.log")

# ──────────────────────────────────────
#  任务列表常量（与 maabot.py 保持一致）
# ──────────────────────────────────────
DAILY_TASKS  = ["StartUpTask", "FightTask", "InfrastTask", "RecruitTask", "MallTask", "AwardTask"]
ROGUE_TASKS = ["StartUpTask", "RoguelikeTask"]

# ─────────────────────────────────────────────
#  颜色 / 主题
# ─────────────────────────────────────────────
CLR_BG      = "#1e1e2e"
CLR_SURFACE = "#2a2a3e"
CLR_BORDER  = "#3a3a5e"
CLR_ACCENT  = "#7c6af7"
CLR_GREEN   = "#a6e3a1"
CLR_RED     = "#f38ba8"
CLR_YELLOW  = "#f9e2af"
CLR_TEXT    = "#cdd6f4"
CLR_MUTED   = "#6c7086"
CLR_LOG_BG  = "#11111b"


def load_yaml_config() -> dict:
    """读取 config.yaml，若不存在返回默认结构"""
    defaults = {
        "root": "",
        "bt_uin": "",
        "enable_webui_interaction": True,
        "debug": False,
        "github_proxy": None,
        "check_ncatbot_update": True,
        "skip_ncatbot_install_check": False,
        "websocket_timeout": 15,
        "napcat": {
            "ws_uri": "ws://localhost:3001",
            "ws_token": "NcatBot",
            "ws_listen_ip": "localhost",
            "webui_uri": "http://localhost:8765",
            "webui_token": "",
            "enable_webui": True,
            "check_napcat_update": False,
            "stop_napcat": False,
            "remote_mode": False,
            "report_self_message": False,
            "report_forward_message_detail": True,
        },
        "plugin": {
            "plugins_dir": "plugins",
            "plugin_whitelist": [],
            "plugin_blacklist": [],
            "skip_plugin_load": False,
        },
        # 以下为 GUI 扩展字段（写入 yaml，maabot.py 读取时忽略未知字段）
        "maa_exe":          r"D:\MAA\MAA.exe",
        "maa_log_path":     r"D:\MAA\debug\gui.log",
        "maa_config_path":  r"D:\MAA\config\gui.new.json",
        "http_port":        2345,
        "log_batch_size":   5,
        "log_batch_timeout": 10,
        "qqbot_enabled":    True,
    }
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        # 合并缺失字段
        for k, v in defaults.items():
            if k not in data:
                data[k] = v
        return data
    return defaults


def save_yaml_config(data: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


# ═══════════════════════════════════════════════
#  主窗口
# ═══════════════════════════════════════════════
class MaaBotGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MAABot 控制面板")
        self.geometry("920x680")
        self.minsize(780, 560)
        self.configure(bg=CLR_BG)

        # 状态
        self._service_proc: subprocess.Popen | None = None
        self._log_queue: queue.Queue = queue.Queue()
        self._running = True

        # 记录所有由 GUI 启动的子进程 PID，用于关闭时清理残留
        self._child_pids: set[int] = set()
        self._service_pid: int | None = None  # 当前服务进程 PID
        self._pid_file = os.path.join(BASE_DIR, "data", "child_pids.json")
        self._restart_signal = os.path.join(BASE_DIR, "data", "restart_signal.json")

        # MAA 崩溃自动恢复：去抖标志（恢复流程进行中不再重复触发）
        self._auto_recover_in_progress = False

        # 配置数据
        self._cfg = load_yaml_config()

        self._build_ui()
        self._start_log_pump()
        self._refresh_status_loop()

        # 启动时清理上次 GUI 会话残留的孤儿进程
        self.after(500, self._cleanup_previous_session)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ─────────────────────────────────────────────
    #  UI 构建
    # ─────────────────────────────────────────────
    def _build_ui(self):
        # 顶部标题栏
        header = tk.Frame(self, bg=CLR_SURFACE, height=56)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="⚙  MAABot 控制面板",
                 bg=CLR_SURFACE, fg=CLR_TEXT,
                 font=("微软雅黑", 15, "bold")).pack(side=tk.LEFT, padx=20, pady=10)
        self._status_badge = tk.Label(header, text="● 未运行",
                                      bg=CLR_SURFACE, fg=CLR_MUTED,
                                      font=("微软雅黑", 10, "bold"))
        self._status_badge.pack(side=tk.RIGHT, padx=20)

        # Notebook
        nb_frame = tk.Frame(self, bg=CLR_BG)
        nb_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 0))

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook",
                         background=CLR_BG, borderwidth=0, tabmargins=[0, 4, 0, 0])
        style.configure("TNotebook.Tab",
                         background=CLR_SURFACE, foreground=CLR_MUTED,
                         font=("微软雅黑", 10), padding=[14, 6],
                         borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", CLR_ACCENT)],
                  foreground=[("selected", "#ffffff")])

        self._nb = ttk.Notebook(nb_frame)
        self._nb.pack(fill=tk.BOTH, expand=True)

        self._tab_control  = self._make_control_tab()
        self._tab_config   = self._make_config_tab()
        self._tab_tasks    = self._make_tasks_tab()
        self._tab_log      = self._make_log_tab()

        self._nb.add(self._tab_control, text="  控制  ")
        self._nb.add(self._tab_config,  text="  配置  ")
        self._nb.add(self._tab_tasks,   text="  MAA任务  ")
        self._nb.add(self._tab_log,     text="  运行日志  ")

    # ── 控制页 ──────────────────────────────────
    def _make_control_tab(self) -> tk.Frame:
        f = tk.Frame(self._nb, bg=CLR_BG)

        # ---- 服务控制区 ----
        svc_card = self._card(f, "服务控制")
        svc_card.pack(fill=tk.X, padx=16, pady=(14, 6))

        btn_row = tk.Frame(svc_card, bg=CLR_SURFACE)
        btn_row.pack(fill=tk.X, padx=10, pady=10)

        self._btn_start   = self._btn(btn_row, "▶  启动服务", CLR_GREEN,   self._start_service)
        self._btn_stop    = self._btn(btn_row, "■  停止服务", CLR_RED,     self._stop_service,  state=tk.DISABLED)
        self._btn_restart = self._btn(btn_row, "↺  重启服务", CLR_YELLOW,  self._restart_service, state=tk.DISABLED)
        self._btn_start.pack(side=tk.LEFT, padx=(0, 8))
        self._btn_stop.pack(side=tk.LEFT, padx=(0, 8))
        self._btn_restart.pack(side=tk.LEFT)

        # 模式切换
        mode_card = self._card(f, "运行模式")
        mode_card.pack(fill=tk.X, padx=16, pady=6)

        mode_inner = tk.Frame(mode_card, bg=CLR_SURFACE)
        mode_inner.pack(fill=tk.X, padx=10, pady=10)

        self._qqbot_var = tk.BooleanVar(value=self._cfg.get("qqbot_enabled", True))
        cb_qq = tk.Checkbutton(
            mode_inner,
            text="启用 QQ Bot（NcatBot 推送）",
            variable=self._qqbot_var,
            bg=CLR_SURFACE, fg=CLR_TEXT,
            selectcolor=CLR_BG,
            activebackground=CLR_SURFACE,
            activeforeground=CLR_TEXT,
            font=("微软雅黑", 10),
            command=self._on_mode_change,
        )
        cb_qq.pack(side=tk.LEFT)

        self._mode_hint = tk.Label(
            mode_inner,
            text="",
            bg=CLR_SURFACE, fg=CLR_MUTED,
            font=("微软雅黑", 9),
        )
        self._mode_hint.pack(side=tk.LEFT, padx=12)
        self._on_mode_change()  # 初始化 hint

        # MAA 状态区
        maa_card = self._card(f, "MAA 状态")
        maa_card.pack(fill=tk.X, padx=16, pady=6)

        maa_inner = tk.Frame(maa_card, bg=CLR_SURFACE)
        maa_inner.pack(fill=tk.X, padx=10, pady=10)

        self._maa_status_label = tk.Label(
            maa_inner, text="MAA：未检测",
            bg=CLR_SURFACE, fg=CLR_MUTED,
            font=("微软雅黑", 10),
        )
        self._maa_status_label.pack(side=tk.LEFT)

        self._btn_maa_restart = self._btn(
            maa_inner, "重启 MAA", CLR_YELLOW, self._manual_restart_maa,
            state=tk.DISABLED,
        )
        self._btn_maa_restart.pack(side=tk.LEFT, padx=12)

        # 进程 PID 显示
        self._pid_label = tk.Label(
            maa_inner, text="",
            bg=CLR_SURFACE, fg=CLR_MUTED,
            font=("微软雅黑", 9),
        )
        self._pid_label.pack(side=tk.RIGHT)

        # ---- FRP + WebUI 访问区 ----
        web_card = self._card(f, "WebUI & FRP 内网穿透")
        web_card.pack(fill=tk.X, padx=16, pady=6)

        web_inner = tk.Frame(web_card, bg=CLR_SURFACE)
        web_inner.pack(fill=tk.X, padx=10, pady=10)

        self._btn_open_webui = self._btn(
            web_inner, "🌐  打开 WebUI", CLR_ACCENT, self._open_webui,
            fg="#ffffff",
        )
        self._btn_open_webui.pack(side=tk.LEFT, padx=(0, 8))

        self._btn_open_remote = self._btn(
            web_inner, "🔗  远程访问", "#89b4fa", self._open_remote_webui,
            fg="#1e1e2e",
        )
        self._btn_open_remote.pack(side=tk.LEFT, padx=(0, 8))

        self._frp_status_label = tk.Label(
            web_inner, text="FRP：未检测",
            bg=CLR_SURFACE, fg=CLR_MUTED,
            font=("微软雅黑", 10),
        )
        self._frp_status_label.pack(side=tk.LEFT, padx=8)

        # WebUI 地址提示
        addr_row = tk.Frame(web_card, bg=CLR_SURFACE)
        addr_row.pack(fill=tk.X, padx=10, pady=(0, 8))

        self._webui_local_label = tk.Label(
            addr_row, text="本地：http://127.0.0.1:2345/webui",
            bg=CLR_SURFACE, fg=CLR_MUTED,
            font=("Consolas", 9),
        )
        self._webui_local_label.pack(side=tk.LEFT)

        self._webui_remote_label = tk.Label(
            addr_row, text="远程：未配置",
            bg=CLR_SURFACE, fg=CLR_MUTED,
            font=("Consolas", 9),
        )
        self._webui_remote_label.pack(side=tk.LEFT, padx=20)

        return f

    # ── 配置页 ──────────────────────────────────
    def _make_config_tab(self) -> tk.Frame:
        f = tk.Frame(self._nb, bg=CLR_BG)

        canvas = tk.Canvas(f, bg=CLR_BG, highlightthickness=0)
        sb = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=CLR_BG)

        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        def _mousewheel(event):
            canvas.yview_scroll(-1 * (event.delta // 120), "units")
        canvas.bind_all("<MouseWheel>", _mousewheel)

        # QQ 配置
        qq_card = self._card(inner, "QQ Bot 配置")
        qq_card.pack(fill=tk.X, padx=16, pady=(14, 6))
        self._ef_admin_qq = self._entry_row(qq_card, "管理员 QQ", self._cfg.get("root", ""))
        self._ef_bot_qq   = self._entry_row(qq_card, "机器人 QQ", self._cfg.get("bt_uin", ""))

        # MAA 路径配置
        maa_card = self._card(inner, "MAA 路径配置")
        maa_card.pack(fill=tk.X, padx=16, pady=6)
        self._ef_maa_exe    = self._entry_row_browse(maa_card, "MAA 可执行文件",
                                                      self._cfg.get("maa_exe", r"D:\MAA\MAA.exe"),
                                                      filetypes=[("可执行文件", "*.exe")])
        self._ef_log_path   = self._entry_row_browse(maa_card, "MAA 日志路径",
                                                      self._cfg.get("maa_log_path", r"D:\MAA\debug\gui.log"),
                                                      filetypes=[("日志文件", "*.log")])
        self._ef_cfg_path   = self._entry_row_browse(maa_card, "MAA 配置文件",
                                                      self._cfg.get("maa_config_path", r"D:\MAA\config\gui.new.json"),
                                                      filetypes=[("JSON 文件", "*.json")])

        # HTTP 服务配置
        http_card = self._card(inner, "HTTP 服务配置")
        http_card.pack(fill=tk.X, padx=16, pady=6)
        self._ef_port = self._entry_row(http_card, "监听端口", str(self._cfg.get("http_port", 2345)))

        # 日志推送配置
        log_card = self._card(inner, "日志推送配置")
        log_card.pack(fill=tk.X, padx=16, pady=6)
        self._ef_batch_size    = self._entry_row(log_card, "批次大小（条）",    str(self._cfg.get("log_batch_size", 5)))
        self._ef_batch_timeout = self._entry_row(log_card, "批次超时（秒）",    str(self._cfg.get("log_batch_timeout", 10)))

        # FRP / WebUI 配置
        frp_card = self._card(inner, "FRP 内网穿透配置")
        frp_card.pack(fill=tk.X, padx=16, pady=6)
        self._ef_frp_server  = self._entry_row(frp_card, "FRP 服务器地址",   self._cfg.get("frp_server_addr", ""))
        self._ef_frp_port    = self._entry_row(frp_card, "本地服务端口",     str(self._cfg.get("http_port", 2345)))
        self._ef_frp_remote1 = self._entry_row(frp_card, "远程端口（Caddy）", str(self._cfg.get("frp_remote_port1", 8081)))
        self._ef_frp_remote2 = self._entry_row(frp_card, "远程端口（备用）",  str(self._cfg.get("frp_remote_port2", 8082)))
        self._ef_domain      = self._entry_row(frp_card, "HTTPS 域名",      self._cfg.get("webui_domain", ""))

        # 保存按钮
        btn_row = tk.Frame(inner, bg=CLR_BG)
        btn_row.pack(fill=tk.X, padx=16, pady=10)
        self._btn(btn_row, "💾  保存配置", CLR_ACCENT, self._save_config,
                  fg="#ffffff").pack(side=tk.LEFT)
        tk.Label(btn_row, text="保存后需重启服务生效",
                 bg=CLR_BG, fg=CLR_MUTED, font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=10)

        return f

    # ── MAA 任务页 ──────────────────────────────
    def _make_tasks_tab(self) -> tk.Frame:
        f = tk.Frame(self._nb, bg=CLR_BG)

        tip = tk.Label(f, text="⚡ 快捷任务操作（通过 WebUI API 下发，服务运行时有效）",
                       bg=CLR_BG, fg=CLR_MUTED, font=("微软雅黑", 9))
        tip.pack(anchor=tk.W, padx=16, pady=(12, 4))

        # 任务预设按钮组
        presets = [
            ("全选 + 肉鸽",  "#7c6af7", ["StartUpTask","FightTask","InfrastTask","RecruitTask","MallTask","AwardTask","RoguelikeTask"]),
            ("全选（日常）", CLR_GREEN,  ["StartUpTask","FightTask","InfrastTask","RecruitTask","MallTask","AwardTask"]),
            ("肉鸽模式",     "#cba6f7",  ["StartUpTask","RoguelikeTask"]),
            ("基建换班",     "#89dceb",  ["InfrastTask"]),
            ("自动公招",     "#a6e3a1",  ["RecruitTask"]),
            ("理智作战",     "#fab387",  ["FightTask"]),
            ("信用购物",     "#f9e2af",  ["MallTask"]),
            ("领取奖励",     "#89b4fa",  ["AwardTask"]),
        ]

        grid = tk.Frame(f, bg=CLR_BG)
        grid.pack(fill=tk.X, padx=16, pady=4)
        for idx, (name, color, tasks) in enumerate(presets):
            btn = self._btn(grid, name, color, lambda t=tasks, n=name: self._dispatch_tasks(t, n))
            btn.grid(row=idx // 4, column=idx % 4, padx=6, pady=6, sticky="ew")
        for c in range(4):
            grid.columnconfigure(c, weight=1)

        # 分隔
        ttk.Separator(f, orient="horizontal").pack(fill=tk.X, padx=16, pady=8)

        # 控制命令
        ctrl_label = tk.Label(f, text="控制命令", bg=CLR_BG, fg=CLR_MUTED, font=("微软雅黑", 9))
        ctrl_label.pack(anchor=tk.W, padx=16)

        ctrl_row = tk.Frame(f, bg=CLR_BG)
        ctrl_row.pack(fill=tk.X, padx=16, pady=6)
        self._btn(ctrl_row, "🛑  停止任务", CLR_RED,    self._dispatch_stop).pack(side=tk.LEFT, padx=(0,8))
        self._btn(ctrl_row, "💓  心跳检测", CLR_MUTED,  self._dispatch_heartbeat).pack(side=tk.LEFT, padx=(0,8))
        self._btn(ctrl_row, "↺  重启 MAA",  CLR_YELLOW, self._api_restart_maa).pack(side=tk.LEFT)

        # 自定义任务勾选
        custom_card = self._card(f, "自定义任务勾选")
        custom_card.pack(fill=tk.X, padx=16, pady=10)
        inner = tk.Frame(custom_card, bg=CLR_SURFACE)
        inner.pack(fill=tk.X, padx=10, pady=8)

        TASK_LIST = [
            ("StartUpTask",     "开始唤醒"),
            ("FightTask",       "理智作战"),
            ("InfrastTask",     "基建换班"),
            ("RecruitTask",     "自动公招"),
            ("MallTask",        "信用收支"),
            ("AwardTask",       "领取奖励"),
            ("RoguelikeTask",   "自动肉鸽"),
            ("ReclamationTask", "生息演算"),
        ]
        self._task_vars: dict[str, tk.BooleanVar] = {}
        for i, (task_id, label) in enumerate(TASK_LIST):
            var = tk.BooleanVar(value=(task_id in ["StartUpTask","FightTask","InfrastTask","RecruitTask","MallTask","AwardTask"]))
            self._task_vars[task_id] = var
            cb = tk.Checkbutton(inner, text=label, variable=var,
                                bg=CLR_SURFACE, fg=CLR_TEXT,
                                selectcolor=CLR_BG,
                                activebackground=CLR_SURFACE, activeforeground=CLR_TEXT,
                                font=("微软雅黑", 10))
            cb.grid(row=i // 4, column=i % 4, sticky=tk.W, padx=12, pady=4)
        for c in range(4):
            inner.columnconfigure(c, weight=1)

        dispatch_row = tk.Frame(custom_card, bg=CLR_SURFACE)
        dispatch_row.pack(fill=tk.X, padx=10, pady=(0, 8))
        self._btn(dispatch_row, "▶  应用并下发",  CLR_GREEN,  self._dispatch_custom).pack(side=tk.LEFT, padx=(0,8))
        self._btn(dispatch_row, "写入配置文件",    CLR_ACCENT, self._write_custom_config).pack(side=tk.LEFT, padx=(0,8))
        self._btn(dispatch_row, "仅写 gui.new.json", CLR_MUTED, self._api_write_config_only, fg=CLR_TEXT).pack(side=tk.LEFT)

        return f

    # ── 日志页 ──────────────────────────────────
    def _make_log_tab(self) -> tk.Frame:
        f = tk.Frame(self._nb, bg=CLR_BG)

        toolbar = tk.Frame(f, bg=CLR_BG)
        toolbar.pack(fill=tk.X, padx=10, pady=(8, 2))
        self._btn(toolbar, "清空日志", CLR_MUTED, self._clear_log, pady=3).pack(side=tk.LEFT)
        self._btn(toolbar, "复制全部", CLR_MUTED, self._copy_log,  pady=3).pack(side=tk.LEFT, padx=6)
        tk.Label(toolbar, text="服务 stdout / stderr 实时输出",
                 bg=CLR_BG, fg=CLR_MUTED, font=("微软雅黑", 9)).pack(side=tk.RIGHT)

        self._log_text = scrolledtext.ScrolledText(
            f,
            bg=CLR_LOG_BG, fg="#a6e3a1",
            insertbackground=CLR_TEXT,
            font=("Consolas", 9),
            wrap=tk.WORD,
            relief=tk.FLAT,
            borderwidth=0,
        )
        self._log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
        self._log_text.configure(state=tk.DISABLED)

        # 颜色 tag
        self._log_text.tag_config("error",   foreground=CLR_RED)
        self._log_text.tag_config("warn",    foreground=CLR_YELLOW)
        self._log_text.tag_config("success", foreground=CLR_GREEN)
        self._log_text.tag_config("muted",   foreground=CLR_MUTED)

        return f

    # ─────────────────────────────────────────────
    #  通用小组件
    # ─────────────────────────────────────────────
    def _card(self, parent, title: str) -> tk.Frame:
        outer = tk.Frame(parent, bg=CLR_SURFACE, bd=0)
        tk.Label(outer, text=title,
                 bg=CLR_SURFACE, fg=CLR_ACCENT,
                 font=("微软雅黑", 10, "bold")).pack(anchor=tk.W, padx=10, pady=(8, 2))
        ttk.Separator(outer, orient="horizontal").pack(fill=tk.X, padx=8)
        return outer

    def _btn(self, parent, text, color, cmd=None, state=tk.NORMAL, fg="#1e1e2e", pady=6) -> tk.Button:
        b = tk.Button(
            parent, text=text, command=cmd,
            bg=color, fg=fg, activebackground=color,
            activeforeground=fg,
            font=("微软雅黑", 10, "bold"),
            relief=tk.FLAT, cursor="hand2",
            padx=14, pady=pady,
            state=state,
        )
        return b

    def _entry_row(self, parent, label: str, default: str = "") -> tk.Entry:
        row = tk.Frame(parent, bg=CLR_SURFACE)
        row.pack(fill=tk.X, padx=10, pady=4)
        tk.Label(row, text=label, width=18, anchor=tk.W,
                 bg=CLR_SURFACE, fg=CLR_TEXT,
                 font=("微软雅黑", 10)).pack(side=tk.LEFT)
        e = tk.Entry(row, bg=CLR_BG, fg=CLR_TEXT,
                     insertbackground=CLR_TEXT,
                     relief=tk.FLAT, font=("微软雅黑", 10))
        e.insert(0, default)
        e.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 4))
        return e

    def _entry_row_browse(self, parent, label: str, default: str, filetypes=None) -> tk.Entry:
        row = tk.Frame(parent, bg=CLR_SURFACE)
        row.pack(fill=tk.X, padx=10, pady=4)
        tk.Label(row, text=label, width=18, anchor=tk.W,
                 bg=CLR_SURFACE, fg=CLR_TEXT,
                 font=("微软雅黑", 10)).pack(side=tk.LEFT)
        e = tk.Entry(row, bg=CLR_BG, fg=CLR_TEXT,
                     insertbackground=CLR_TEXT,
                     relief=tk.FLAT, font=("微软雅黑", 10))
        e.insert(0, default)
        e.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 4))

        def _browse():
            if filetypes and any("exe" in ft[1] for ft in filetypes):
                path = filedialog.askopenfilename(filetypes=filetypes)
            else:
                path = filedialog.askopenfilename(filetypes=filetypes or [("所有文件", "*.*")])
            if path:
                e.delete(0, tk.END)
                e.insert(0, path)

        tk.Button(row, text="浏览", command=_browse,
                  bg=CLR_BORDER, fg=CLR_TEXT,
                  relief=tk.FLAT, font=("微软雅黑", 9),
                  padx=8, cursor="hand2").pack(side=tk.LEFT)
        return e

    # ─────────────────────────────────────────────
    #  服务管理
    # ─────────────────────────────────────────────
    def _start_service(self):
        if self._service_proc and self._service_proc.poll() is None:
            self._log("[GUI] 服务已在运行中\n", "warn")
            return

        # 启动前强制杀掉所有残留子进程（包括活进程），避免端口占用导致 MAA 无法启动
        self._kill_all_child_processes()
        # 额外安全检查：确保端口未被占用
        port = int(self._cfg.get("http_port", 2345))
        if not self._wait_port_free(port, timeout=5):
            self._log(f"[GUI] ⚠️ 端口 {port} 仍被占用，强制释放...\n", "warn")
            self._force_free_port(port)

        # 生成启动参数
        args = [sys.executable, MAABOT_SCRIPT]
        if not self._qqbot_var.get():
            args.append("--no-qqbot")

        self._log(f"[GUI] 正在启动服务: {' '.join(args)}\n", "muted")

        try:
            self._service_proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=BASE_DIR,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as e:
            self._log(f"[GUI] 启动失败: {e}\n", "error")
            return

        pid = self._service_proc.pid
        self._service_pid = pid
        self._child_pids.add(pid)
        self._save_pids_to_file()
        self._log(f"[GUI] 服务已启动 PID={pid}（已记录到子进程列表，当前共 {len(self._child_pids)} 个）\n", "success")

        # 启动日志读取线程
        threading.Thread(target=self._read_proc_output, daemon=True).start()
        self._update_btn_state(running=True)

    def _stop_service(self, confirm=True):
        if not self._service_proc or self._service_proc.poll() is not None:
            self._log("[GUI] 服务未在运行\n", "warn")
            return
        if confirm:
            if not messagebox.askyesno("停止服务", "确认停止 MAABot 服务？"):
                return

        self._log("[GUI] 正在停止服务...\n", "warn")
        # 清空队列，防止旧 pump 线程误判
        while not self._log_queue.empty():
            try:
                self._log_queue.get_nowait()
            except queue.Empty:
                break

        pid = self._service_proc.pid
        try:
            self._service_proc.terminate()
            try:
                self._service_proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self._service_proc.kill()
                self._service_proc.wait(timeout=3)
        except Exception as e:
            self._log(f"[GUI] 停止服务时出错: {e}\n", "error")

        # 从记录中移除该 PID
        self._child_pids.discard(pid)
        self._service_pid = None
        self._save_pids_to_file()
        self._update_btn_state(running=False)
        self._log(f"[GUI] 服务已停止 PID={pid}（剩余子进程 {len(self._child_pids)} 个）\n", "success")

    def _restart_service(self):
        self._log("[GUI] 正在重启服务...\n", "warn")
        self._stop_service(confirm=False)
        # 等待旧进程完全退出 + 端口释放
        self._wait_port_free(int(self._cfg.get("http_port", 2345)), timeout=10)
        time.sleep(1)
        self._start_service()

    def _auto_recover_from_maa_crash(self, reason: str):
        """
        MAA 启动/初始化崩溃后自动恢复：
          1) 停止当前服务
          2) 用 os.startfile 拉起 MAA（避免 GUI 关闭后残留进程互相杀）
          3) 等几秒让 MAA 稳定
          4) 重新启动服务
        通过 _auto_recover_in_progress 去抖，重复触发会被忽略。
        """
        if self._auto_recover_in_progress:
            self._log("[RECOVER] 恢复流程已在进行中，跳过本次触发\n", "warn")
            return
        self._auto_recover_in_progress = True
        threading.Thread(target=self._do_auto_recover, args=(reason,), daemon=True).start()

    def _do_auto_recover(self, reason: str):
        try:
            self._log("\n" + "=" * 56 + "\n", "error")
            self._log(f"[RECOVER] 检测到 MAA 初始化崩溃: {reason}\n", "error")
            self._log("[RECOVER] 自动恢复流程启动：停止服务 → 启动 MAA → 启动服务\n", "warn")

            # 1) 停止服务
            self._log("[RECOVER] 步骤 1/4：停止服务...\n", "warn")
            self._stop_service(confirm=False)

            port = int(self._cfg.get("http_port", 2345))
            if not self._wait_port_free(port, timeout=10):
                self._log(f"[RECOVER] ⚠️ 端口 {port} 仍未释放，强制清理\n", "warn")
                self._force_free_port(port)
            # 多等一拍，确保完全退出
            time.sleep(1)

            # 2) 启动 MAA（直接 os.startfile，行为与 maabot.py 的 restart_maa 一致）
            maa_exe = self._ef_maa_exe.get().strip() or self._cfg.get("maa_exe", "")
            self._log(f"[RECOVER] 步骤 2/4：启动 MAA → {maa_exe}\n", "warn")
            if not maa_exe or not os.path.exists(maa_exe):
                self._log(f"[RECOVER] ❌ MAA 可执行文件不存在: {maa_exe}\n", "error")
                self._log("[RECOVER] 自动恢复中止，请手动启动 MAA 和服务\n", "error")
                return
            try:
                os.startfile(maa_exe)
            except Exception as e:
                self._log(f"[RECOVER] ❌ 启动 MAA 失败: {e}\n", "error")
                return

            # 3) 等 MAA 稳定
            self._log("[RECOVER] 步骤 3/4：等待 MAA 稳定（5 秒）...\n", "warn")
            time.sleep(5)

            # 4) 重新启动服务
            self._log("[RECOVER] 步骤 4/4：重新启动服务...\n", "warn")
            self._start_service()
            # ★ 如果是因为自动切换失败触发的恢复，恢复后自动下发 LinkStart
            if "请求 GUI 恢复" in reason:
                self._log("[RECOVER] 恢复后等待服务就绪，然后自动下发任务...\n", "warn")
                port = int(self._cfg.get("http_port", 2345))
                # 等待服务就绪（最多 30 秒）
                svc_ready = False
                for i in range(30):
                    try:
                        ret = self._http_get(f"http://127.0.0.1:{port}/api/status")
                        if ret:
                            self._log(f"[RECOVER] 服务已就绪（等待了 {i+1} 秒）\n", "success")
                            svc_ready = True
                            break
                    except Exception:
                        pass
                    time.sleep(1)
                if svc_ready:
                    # 下发「全选」任务（含 LinkStart）
                    self._dispatch_tasks(DAILY_TASKS, "全选")
                else:
                    self._log("[RECOVER] ⚠️ 服务未就绪，跳过自动下发，请手动操作\n", "warn")
            self._log("[RECOVER] ✅ 自动恢复完成\n", "success")
            self._log("=" * 56 + "\n", "error")
        except Exception as e:
            import traceback
            self._log(f"[RECOVER] ❌ 自动恢复异常: {e}\n{traceback.format_exc()}\n", "error")
        finally:
            self._auto_recover_in_progress = False

    def _update_btn_state(self, running: bool):
        if running:
            self._btn_start.config(state=tk.DISABLED)
            self._btn_stop.config(state=tk.NORMAL)
            self._btn_restart.config(state=tk.NORMAL)
            self._btn_maa_restart.config(state=tk.NORMAL)
            self._status_badge.config(text="● 运行中", fg=CLR_GREEN)
        else:
            self._btn_start.config(state=tk.NORMAL)
            self._btn_stop.config(state=tk.DISABLED)
            self._btn_restart.config(state=tk.DISABLED)
            self._btn_maa_restart.config(state=tk.DISABLED)
            self._status_badge.config(text="● 已停止", fg=CLR_RED)

    def _is_pid_alive(self, pid: int) -> bool:
        """检查指定 PID 的进程是否仍在运行"""
        try:
            ret = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True, text=True, timeout=3,
            )
            return str(pid) in ret.stdout
        except Exception:
            return False

    def _kill_pid(self, pid: int, name: str = "python.exe"):
        """强制终止指定 PID 的进程，先校验进程名防止误杀（OS 可能复用 PID）"""
        try:
            # 校验进程名，防止 PID 被 OS 复用后误杀其他进程
            ret = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True, text=True, timeout=3,
            )
            if name.lower() not in ret.stdout.lower():
                self._log(f"[GUI] PID={pid} 已非 Python 进程，跳过清理\n", "muted")
                return
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=5)
            self._log(f"[GUI] 已终止残留进程 PID={pid}\n", "warn")
        except Exception as e:
            self._log(f"[GUI] 终止进程 PID={pid} 失败: {e}\n", "error")

    def _wait_port_free(self, port: int, timeout: int = 5) -> bool:
        """等待指定端口释放，返回 True 表示端口已空闲"""
        import time as _time
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            try:
                ret = subprocess.run(
                    ["netstat", "-ano"],
                    capture_output=True, text=True, timeout=3,
                )
                if f":{port} " not in ret.stdout or "LISTENING" not in ret.stdout:
                    return True
            except Exception:
                pass
            _time.sleep(0.5)
        return False

    def _force_free_port(self, port: int):
        """强制杀掉占用指定端口的进程"""
        try:
            # 用 PowerShell 精确查找占用端口的 PID
            ps_cmd = (
                f"$c = Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue; "
                f"if ($c) {{ $c.OwningProcess | ForEach-Object {{ Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }} }}"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            self._log(f"[GUI] 已强制释放端口 {port}\n", "warn")
        except Exception as e:
            self._log(f"[GUI] 释放端口 {port} 失败: {e}\n", "error")

    def _cleanup_zombie_processes(self):
        """清理已死但仍在 _child_pids 中的僵尸 PID"""
        alive = {pid for pid in self._child_pids if self._is_pid_alive(pid)}
        dead = self._child_pids - alive
        if dead:
            self._log(f"[GUI] 清理 {len(dead)} 个僵尸进程: {dead}\n", "muted")
            self._child_pids = alive
            self._save_pids_to_file()

    def _kill_all_child_processes(self):
        """强制终止所有记录的子进程"""
        # 先清理僵尸
        self._cleanup_zombie_processes()
        if not self._child_pids:
            return
        self._log(f"[GUI] 正在清理 {len(self._child_pids)} 个残留子进程...\n", "warn")
        for pid in list(self._child_pids):
            self._kill_pid(pid)
        self._child_pids.clear()
        self._service_pid = None
        self._save_pids_to_file()

    # ── PID 持久化（跨会话清理） ──────────────
    def _save_pids_to_file(self):
        """将当前 _child_pids 写入文件，用于下次启动时清理"""
        try:
            os.makedirs(os.path.dirname(self._pid_file), exist_ok=True)
            data = {
                "pids": list(self._child_pids),
                "service_pid": self._service_pid,
                "updated_at": datetime.now().isoformat(),
            }
            with open(self._pid_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _cleanup_previous_session(self):
        """启动时读取上一次会话留下的 PID 文件，清理孤儿进程"""
        try:
            if not os.path.exists(self._pid_file):
                return
            with open(self._pid_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            prev_pids = data.get("pids", [])
            if not prev_pids:
                return

            # 时间保护：PID 文件超过 5 分钟则跳过（OS 可能已复用 PID）
            updated_at = data.get("updated_at", "")
            if updated_at:
                try:
                    file_time = datetime.fromisoformat(updated_at)
                    age = (datetime.now() - file_time).total_seconds()
                    if age > 300:
                        self._log(f"[GUI] PID 文件已过期（{age:.0f}s），跳过清理\n", "muted")
                        return
                except Exception:
                    pass

            alive = [pid for pid in prev_pids if self._is_pid_alive(pid)]
            if not alive:
                return
            self._log(f"[GUI] 检测到上次会话残留 {len(alive)} 个进程: {alive}\n", "warn")
            for pid in alive:
                self._kill_pid(pid)
            self._log(f"[GUI] 已完成启动清理\n", "success")
        except Exception as e:
            self._log(f"[GUI] 启动清理异常: {e}\n", "error")
        finally:
            # 无论成功与否，清理残留文件
            try:
                if os.path.exists(self._pid_file):
                    os.remove(self._pid_file)
                if os.path.exists(self._restart_signal):
                    os.remove(self._restart_signal)
            except Exception:
                pass

    def _check_restart_signal(self):
        """检测服务发来的重启 MAA 信号文件，由 GUI 调用 restart_maa.py 执行实际重启"""
        try:
            if not os.path.exists(self._restart_signal):
                return
            # 读取信号文件
            with open(self._restart_signal, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 过期保护：超过 5 分钟的信号忽略
            ts = data.get("timestamp", "")
            if ts:
                try:
                    sig_time = datetime.fromisoformat(ts)
                    if (datetime.now() - sig_time).total_seconds() > 300:
                        self._log("[SIGNAL] 重启信号已过期，忽略\n", "muted")
                        os.remove(self._restart_signal)
                        return
                except Exception:
                    pass
            self._log("[SIGNAL] 收到服务重启 MAA 信号，调用重启脚本\n", "muted")
            # 调用重启脚本
            subprocess.run(
                [sys.executable, RESTART_SCRIPT],
                capture_output=True, timeout=60,
                cwd=BASE_DIR,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self._read_restart_log()
            # 删除信号文件
            os.remove(self._restart_signal)
        except Exception as e:
            self._log(f"[SIGNAL] 处理重启信号异常: {e}\n", "error")
            try:
                if os.path.exists(self._restart_signal):
                    os.remove(self._restart_signal)
            except Exception:
                pass

    # ─────────────────────────────────────────────
    #  MAA 操作（通过 WebUI API）
    # ─────────────────────────────────────────────
    def _read_restart_log(self):
        """读取重启脚本日志并显示到 GUI"""
        try:
            if not os.path.exists(RESTART_LOG):
                return
            with open(RESTART_LOG, "r", encoding="utf-8") as f:
                lines = f.readlines()
            # 只取最后 20 行
            recent = lines[-20:] if len(lines) > 20 else lines
            for line in recent:
                line = line.strip()
                if not line:
                    continue
                if "ERROR" in line or "失败" in line:
                    self._log(f"[RESTART] {line}\n", "error")
                elif "DONE" in line or "成功" in line or "已启动" in line:
                    self._log(f"[RESTART] {line}\n", "success")
                elif "WARN" in line:
                    self._log(f"[RESTART] {line}\n", "warn")
                else:
                    self._log(f"[RESTART] {line}\n", "muted")
        except Exception:
            pass
    def _get_port(self) -> int:
        try:
            return int(self._ef_port.get().strip())
        except Exception:
            return self._cfg.get("http_port", 2345)

    def _http_post(self, path: str, data: dict) -> dict | None:
        import urllib.request
        url = f"http://127.0.0.1:{self._get_port()}{path}"
        try:
            body = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(url, data=body,
                                          headers={"Content-Type": "application/json"},
                                          method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            self._log(f"[HTTP] 请求失败 {url}: {e}\n", "error")
            return None

    def _http_get(self, path: str) -> dict | None:
        import urllib.request
        url = f"http://127.0.0.1:{self._get_port()}{path}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            self._log(f"[HTTP] 请求失败 {url}: {e}\n", "error")
            return None

    def _dispatch_tasks(self, tasks: list, name: str):
        """通过 WebUI API 下发任务"""
        threading.Thread(target=self._do_dispatch_tasks, args=(tasks, name), daemon=True).start()

    def _do_dispatch_tasks(self, tasks: list, name: str):
        self._log(f"[GUI] 下发任务模式：{name}\n", "success")
        result = self._http_post("/api/maa/dispatch", {"tasks": tasks})
        if result and result.get("ok"):
            self._log(f"[GUI] ✅ 任务已下发：{name}\n", "success")
        else:
            self._log(f"[GUI] ❌ 任务下发失败，服务可能未运行\n", "error")

    def _dispatch_stop(self):
        self._log("[GUI] 发送停止命令...\n", "warn")
        result = self._http_post("/api/maa/dispatch", {"stop": True})
        if result and result.get("ok"):
            self._log("[GUI] ✅ 停止命令已发送\n", "success")
        else:
            self._log("[GUI] ❌ 停止命令发送失败\n", "error")

    def _dispatch_heartbeat(self):
        result = self._http_get("/api/status")
        if result:
            self._log(f"[GUI] 💓 心跳正常 | MAA: {'✅' if result.get('maa_running') else '❌'} | "
                       f"设备: {result.get('device_count', 0)} | 待处理: {result.get('pending_count', 0)}\n",
                       "success")
        else:
            self._log("[GUI] ❌ 心跳失败，服务可能未运行\n", "error")

    def _api_restart_maa(self):
        if not messagebox.askyesno("重启 MAA", "将通过 API 重启 MAA.exe，确认？"):
            return
        threading.Thread(target=self._do_api_restart_maa, daemon=True).start()

    def _do_api_restart_maa(self):
        self._log("[MAA] 正在通过 API 重启 MAA...\n", "warn")
        result = self._http_post("/api/maa/restart", {})
        if result and result.get("ok"):
            self._log("[MAA] ✅ 重启指令已发送\n", "success")
        else:
            self._log("[MAA] ❌ 重启指令发送失败\n", "error")

    def _dispatch_custom(self):
        tasks = [t for t, v in self._task_vars.items() if v.get()]
        if not tasks:
            messagebox.showwarning("提示", "请至少勾选一个任务")
            return
        name = "自定义: " + "、".join(tasks)
        self._dispatch_tasks(tasks, name)

    def _write_custom_config(self):
        """写入 gui.new.json 并通过 API 下发"""
        tasks = [t for t, v in self._task_vars.items() if v.get()]
        if not tasks:
            messagebox.showwarning("提示", "请至少勾选一个任务")
            return
        # 通过 API 写入
        result = self._http_post("/api/maa/write-config", {"tasks": tasks})
        if result and result.get("ok"):
            self._log(f"[CONFIG] ✅ 已写入 gui.new.json：{tasks}\n", "success")
            # 接着下发
            self._dispatch_tasks(tasks, "自定义写入后下发")
        else:
            # 回退到本地文件操作
            cfg_path = self._ef_cfg_path.get().strip()
            if not cfg_path or not os.path.exists(cfg_path):
                messagebox.showerror("错误", f"MAA 配置文件不存在:\n{cfg_path}")
                return
            if self._write_task_config_local(cfg_path, tasks):
                messagebox.showinfo("成功", f"已写入 gui.new.json\n勾选任务：{', '.join(tasks)}")

    def _api_write_config_only(self):
        """仅通过 API 写入 gui.new.json，不下发"""
        tasks = [t for t, v in self._task_vars.items() if v.get()]
        if not tasks:
            messagebox.showwarning("提示", "请至少勾选一个任务")
            return
        result = self._http_post("/api/maa/write-config", {"tasks": tasks})
        if result and result.get("ok"):
            self._log(f"[CONFIG] ✅ 仅写入 gui.new.json：{tasks}\n", "success")
        else:
            self._log("[CONFIG] ❌ 写入失败\n", "error")

    def _write_task_config_local(self, cfg_path: str, enabled_tasks: list) -> bool:
        """本地直接写 gui.new.json（API 不可用时的回退方案）"""
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            current = data.get("Current", "Default")
            configs = data.get("Configurations", {})
            if current not in configs:
                self._log(f"[CONFIG] MAA 配置 '{current}' 不存在\n", "warn")
                return False
            task_queue = configs[current].get("TaskQueue", [])
            enabled_set = set(enabled_tasks)
            for task in task_queue:
                task["IsEnable"] = task.get("$type", "") in enabled_set
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self._log(f"[CONFIG] 写入 gui.new.json 成功，勾选: {enabled_tasks}\n", "success")
            return True
        except Exception as e:
            self._log(f"[CONFIG] 写入失败: {e}\n", "error")
            return False

    def _manual_restart_maa(self):
        """直接重启 MAA（不走 API，GUI 直接操作进程）"""
        if not messagebox.askyesno("重启 MAA", "将强制关闭并重启 MAA.exe，确认？"):
            return
        threading.Thread(target=self._do_manual_restart_maa, daemon=True).start()

    def _do_manual_restart_maa(self):
        self._log("[MAA] 正在调用重启脚本...\n", "warn")
        try:
            ret = subprocess.run(
                [sys.executable, RESTART_SCRIPT],
                capture_output=True, text=True, timeout=60,
                cwd=BASE_DIR,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            # 读取脚本日志
            self._read_restart_log()
            if ret.returncode == 0:
                self._log("[MAA] 重启脚本执行成功\n", "success")
            else:
                self._log(f"[MAA] 重启脚本返回非零: {ret.returncode}\n", "error")
        except subprocess.TimeoutExpired:
            self._log("[MAA] 重启脚本执行超时（60s）\n", "error")
        except Exception as e:
            self._log(f"[MAA] 调用重启脚本失败: {e}\n", "error")

    # ─────────────────────────────────────────────
    #  WebUI & FRP 操作
    # ─────────────────────────────────────────────
    def _open_webui(self):
        """在浏览器中打开本地 WebUI"""
        port = self._get_port()
        url = f"http://127.0.0.1:{port}/webui"
        webbrowser.open(url)
        self._log(f"[WebUI] 已打开本地 WebUI: {url}\n", "muted")

    def _open_remote_webui(self):
        """在浏览器中打开远程 WebUI"""
        domain = self._cfg.get("webui_domain", "")
        if not domain:
            self._log("[WebUI] 未配置远程域名，请在配置页设置 HTTPS 域名\n", "warning")
            return
        url = f"https://{domain}/webui"
        webbrowser.open(url)
        self._log(f"[WebUI] 已打开远程 WebUI: {url}\n", "muted")

    # ─────────────────────────────────────────────
    #  配置读写
    # ─────────────────────────────────────────────
    def _save_config(self):
        self._cfg["root"]             = self._ef_admin_qq.get().strip()
        self._cfg["bt_uin"]           = self._ef_bot_qq.get().strip()
        self._cfg["maa_exe"]          = self._ef_maa_exe.get().strip()
        self._cfg["maa_log_path"]     = self._ef_log_path.get().strip()
        self._cfg["maa_config_path"]  = self._ef_cfg_path.get().strip()
        self._cfg["qqbot_enabled"]    = self._qqbot_var.get()
        self._cfg["frp_server_addr"]  = self._ef_frp_server.get().strip()
        self._cfg["webui_domain"]     = self._ef_domain.get().strip()
        try:
            self._cfg["http_port"]          = int(self._ef_port.get().strip())
            self._cfg["log_batch_size"]     = int(self._ef_batch_size.get().strip())
            self._cfg["log_batch_timeout"]  = int(self._ef_batch_timeout.get().strip())
            self._cfg["frp_remote_port1"]   = int(self._ef_frp_remote1.get().strip())
            self._cfg["frp_remote_port2"]   = int(self._ef_frp_remote2.get().strip())
        except ValueError as e:
            messagebox.showerror("输入错误", f"数字字段格式不正确:\n{e}")
            return
        save_yaml_config(self._cfg)
        self._log("[GUI] 配置已保存到 config.yaml\n", "success")
        # 更新 WebUI 地址标签
        self._refresh_webui_labels()
        messagebox.showinfo("保存成功", "配置已保存！\n重启服务后生效。")

    # ─────────────────────────────────────────────
    #  模式切换
    # ─────────────────────────────────────────────
    def _on_mode_change(self):
        if self._qqbot_var.get():
            self._mode_hint.config(
                text="完整模式：QQ 推送 + MAA 监控 + HTTP 服务",
                fg=CLR_GREEN,
            )
        else:
            self._mode_hint.config(
                text="独立模式：仅 MAA 监控 + HTTP 服务（无 QQ 推送）",
                fg=CLR_YELLOW,
            )

    # ─────────────────────────────────────────────
    #  日志系统
    # ─────────────────────────────────────────────
    def _read_proc_output(self):
        """读取子进程 stdout，放入队列"""
        proc = self._service_proc
        if proc is None:
            return
        current_pid = proc.pid
        try:
            for line in proc.stdout:
                # 如果进程被替换（重启），停止读取
                if self._service_proc is None or self._service_proc.pid != current_pid:
                    break
                self._log_queue.put(line)
            # 只有进程未被替换时才发送结束哨兵
            if self._service_proc is not None and self._service_proc.pid == current_pid:
                self._log_queue.put(None)
        except Exception:
            pass

    def _start_log_pump(self):
        """每 100ms 从队列取日志刷到 UI"""
        def pump():
            try:
                while True:
                    item = self._log_queue.get_nowait()
                    if item is None:
                        # None 哨兵：服务进程退出，仅当当前没有运行中的服务时才更新状态
                        if self._service_proc is None or self._service_proc.poll() is not None:
                            self._log("[GUI] 服务进程已退出\n", "warn")
                            self.after(0, lambda: self._update_btn_state(running=False))
                        break
                    self._log(item)
            except queue.Empty:
                pass
            if self._running:
                self.after(100, pump)
        self.after(100, pump)

    def _log(self, text: str, tag: str = ""):
        def _do():
            self._log_text.configure(state=tk.NORMAL)
            if not tag:
                # 自动识别 tag
                low = text.lower()
                if "[error]" in low or "error" in low or "失败" in text or "错误" in text:
                    tag_ = "error"
                elif "[warn]" in low or "warn" in low or "警告" in text:
                    tag_ = "warn"
                elif "✅" in text or "成功" in text or "[info]" in low or "就绪" in text:
                    tag_ = "success"
                else:
                    tag_ = ""
            else:
                tag_ = tag
            ts = datetime.now().strftime("%H:%M:%S")
            line = f"[{ts}] {text}"
            self._log_text.insert(tk.END, line, tag_)
            self._log_text.see(tk.END)
            self._log_text.configure(state=tk.DISABLED)
            # ★ 触发器：检测到 maabot 报 MAA 初始化崩溃时，启动自动恢复流程
            if "MAA 初始化期间退出" in text:
                self._auto_recover_from_maa_crash(text.strip())
            # ★ 触发器：检测到 maabot 报 MAA 自动切换后重启失败，启动自动恢复流程
            if "MAA 重启失败" in text and "请求 GUI 恢复" in text:
                self._auto_recover_from_maa_crash(text.strip())
            # ★ 触发器：检测到模拟器断开重连失败，启动自动恢复流程
            if "模拟器连接断开" in text or ("模拟器断开" in text and "重连失败" in text):
                self._auto_recover_from_maa_crash(text.strip())
        self.after(0, _do)

    def _clear_log(self):
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.delete("1.0", tk.END)
        self._log_text.configure(state=tk.DISABLED)

    def _copy_log(self):
        content = self._log_text.get("1.0", tk.END)
        self.clipboard_clear()
        self.clipboard_append(content)
        self._log("[GUI] 日志已复制到剪贴板\n", "muted")

    # ─────────────────────────────────────────────
    #  状态刷新
    # ─────────────────────────────────────────────
    def _refresh_status_loop(self):
        def check():
            # 检测 MAA.exe 是否在运行
            try:
                ret = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq MAA.exe"],
                    capture_output=True, text=True, timeout=3,
                )
                maa_running = "MAA.exe" in ret.stdout
            except Exception:
                maa_running = False

            if maa_running:
                self._maa_status_label.config(text="MAA：✅ 运行中", fg=CLR_GREEN)
            else:
                self._maa_status_label.config(text="MAA：❌ 未运行", fg=CLR_RED)

            # 检测 frpc.exe 是否在运行
            try:
                ret = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq frpc.exe"],
                    capture_output=True, text=True, timeout=3,
                )
                frp_running = "frpc.exe" in ret.stdout
            except Exception:
                frp_running = False

            if frp_running:
                self._frp_status_label.config(text="FRP：✅ 已连接", fg=CLR_GREEN)
            else:
                self._frp_status_label.config(text="FRP：❌ 未运行", fg=CLR_RED)

            # 服务状态
            svc_running = self._service_proc is not None and self._service_proc.poll() is None
            if svc_running:
                self._pid_label.config(text=f"PID: {self._service_proc.pid}", fg=CLR_MUTED)
            else:
                self._pid_label.config(text="", fg=CLR_MUTED)
                # 服务未运行时，定期清理僵尸子进程
                if self._child_pids:
                    self._cleanup_zombie_processes()

            # 检测服务重启 MAA 信号文件
            self._check_restart_signal()

            # 更新 WebUI 地址标签
            self._refresh_webui_labels()

            if self._running:
                self.after(3000, check)
        self.after(1000, check)

    def _refresh_webui_labels(self):
        port = self._get_port()
        domain = self._cfg.get("webui_domain", "")
        self._webui_local_label.config(text=f"本地：http://127.0.0.1:{port}/webui")
        if domain:
            self._webui_remote_label.config(text=f"远程：https://{domain}/webui")
        else:
            self._webui_remote_label.config(text="远程：未配置（请在配置页设置域名）")

    # ─────────────────────────────────────────────
    #  关闭
    # ─────────────────────────────────────────────
    def _on_close(self):
        svc_running = self._service_proc and self._service_proc.poll() is None
        if svc_running:
            ans = messagebox.askyesnocancel(
                "退出确认",
                "MAABot 服务仍在运行。\n\n"
                "是：停止服务并退出\n"
                "否：仅关闭窗口（服务继续运行）\n"
                "取消：不退出"
            )
            if ans is None:
                return
            if ans:
                self._stop_service(confirm=False)
        # 强制清理所有残留子进程
        self._kill_all_child_processes()
        # 删除 PID 持久化文件（正常退出后无需残留）
        try:
            if os.path.exists(self._pid_file):
                os.remove(self._pid_file)
        except Exception:
            pass
        self._running = False
        self.destroy()


# ═══════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════
if __name__ == "__main__":
    app = MaaBotGUI()
    app.mainloop()
