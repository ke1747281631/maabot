"""
MAA 重启独立脚本
- 杀掉旧 MAA 进程
- 启动新 MAA 进程（使用 os.startfile）
- 所有输出写入日志文件（logs/restart_maa.log），不弹控制台窗口
- 可独立运行，也可被 maabot.py / maabot_gui.py import 调用

用法：
  独立运行: python restart_maa.py
  import:   from restart_maa import run_restart; run_restart()
"""
import os
import sys
import time
import json
import yaml
import subprocess
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
_LOG_DIR = os.path.join(BASE_DIR, "logs")
_LOG_PATH = os.path.join(_LOG_DIR, "restart_maa.log")
_MAA_GUI_JSON = None  # 延迟从 config.yaml 推导

_log_lines: list[str] = []


def _log(msg: str):
    """记录日志（内存 + 文件双写）"""
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    _log_lines.append(line)
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_maa_path() -> str:
    """从 config.yaml 读取 MAA 可执行文件路径"""
    if not os.path.exists(CONFIG_PATH):
        _log(f"[ERROR] 配置文件不存在: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    maa_exe = cfg.get("maa_exe", r"D:\MAA\MAA.exe")
    if not os.path.exists(maa_exe):
        _log(f"[ERROR] MAA 可执行文件不存在: {maa_exe}")
        sys.exit(1)
    return maa_exe


def kill_maa():
    """杀掉所有 MAA.exe 进程"""
    _log("[MAA] 正在关闭旧 MAA 进程...")
    try:
        ret = subprocess.run(
            ["taskkill", "/f", "/im", "MAA.exe"],
            capture_output=True, text=True, timeout=10,
        )
        if ret.returncode == 0:
            _log("[MAA] 已发送终止信号")
        elif "没有找到" in ret.stderr or "not found" in ret.stderr.lower():
            _log("[MAA] 没有运行中的 MAA 进程")
    except Exception as e:
        _log(f"[WARN] taskkill 失败: {e}")

    # 等待进程真正退出（最多 10 秒）
    for i in range(10):
        time.sleep(1)
        if not _is_maa_running():
            _log(f"[MAA] 旧进程已退出（等待 {i+1}s）")
            return
    _log("[WARN] MAA 进程未完全退出，继续启动")


def _is_maa_running() -> bool:
    try:
        ret = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq MAA.exe"],
            capture_output=True, text=True, timeout=5,
        )
        return "MAA.exe" in ret.stdout
    except Exception:
        return False


def _get_maa_gui_json() -> str:
    """获取 MAA gui.json 路径"""
    global _MAA_GUI_JSON
    if _MAA_GUI_JSON:
        return _MAA_GUI_JSON
    cfg_path = os.path.join(BASE_DIR, "config.yaml")
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        maa_config = cfg.get("maa_config_path", r"D:\MAA\config\gui.new.json")
        _MAA_GUI_JSON = os.path.join(os.path.dirname(maa_config), "gui.json")
    else:
        _MAA_GUI_JSON = r"D:\MAA\config\gui.json"
    return _MAA_GUI_JSON


def _clear_maa_proxy() -> dict | None:
    """临时清空 MAA gui.json 中的 HTTP 代理设置"""
    gui_json = _get_maa_gui_json()
    if not os.path.exists(gui_json):
        _log("[PROXY] gui.json 不存在，跳过代理清理")
        return None
    try:
        with open(gui_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        vu = data.get("Global", {}).get("VersionUpdate", {})
        proxy = vu.get("Proxy", "")
        proxy_type = vu.get("ProxyType", "")
        if not proxy:
            return None
        backup = {"Proxy": proxy, "ProxyType": proxy_type}
        vu["Proxy"] = ""
        vu["ProxyType"] = ""
        with open(gui_json, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        _log(f"[PROXY] 已临时清空 MAA 代理: {proxy}")
        return backup
    except Exception as e:
        _log(f"[ERROR] 清理代理失败: {e}")
        return None


def _restore_maa_proxy(backup: dict | None):
    """恢复 MAA gui.json 中的 HTTP 代理设置"""
    if not backup:
        return
    gui_json = _get_maa_gui_json()
    if not os.path.exists(gui_json):
        return
    try:
        with open(gui_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        vu = data.setdefault("Global", {}).setdefault("VersionUpdate", {})
        vu["Proxy"] = backup.get("Proxy", "")
        vu["ProxyType"] = backup.get("ProxyType", "")
        with open(gui_json, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        _log(f"[PROXY] 已恢复 MAA 代理: {backup.get('Proxy')}")
    except Exception as e:
        _log(f"[ERROR] 恢复代理失败: {e}")


def start_maa(maa_exe: str) -> bool:
    """使用 os.startfile 启动 MAA（ShellExecute，GUI 必备）并等待进程稳定运行"""
    maa_dir = os.path.dirname(maa_exe)
    _log(f"[MAA] 启动 MAA: {maa_exe}")
    _log(f"[MAA] 工作目录: {maa_dir}")
    try:
        os.startfile(maa_exe)
        _log("[MAA] MAA 启动命令已执行（os.startfile）")
    except Exception as e:
        _log(f"[ERROR] 启动 MAA 失败: {e}")
        return False

    # 等待 MAA 进程出现（最多 10 秒）
    for i in range(10):
        time.sleep(1)
        if _is_maa_running():
            _log(f"[MAA] MAA 进程已出现（等待 {i+1}s）")
            # 再等 2 秒确保进程稳定
            time.sleep(2)
            if _is_maa_running():
                _log("[MAA] MAA 进程稳定运行")
                return True
            else:
                _log("[ERROR] MAA 启动后立即退出")
                return False
    _log("[ERROR] MAA 启动超时（10s）")
    return False


def run_restart() -> bool:
    """
    执行 MAA 重启流程。返回 True 表示成功。
    可被外部 import 调用。
    """
    global _log_lines
    _log_lines.clear()

    # 写入启动标记
    _log("=" * 40)
    _log("MAA 重启脚本开始")
    _log("=" * 40)

    maa_exe = load_maa_path()
    _log(f"[MAA] 路径: {maa_exe}")

    kill_maa()

    # 额外等待 2 秒确保资源完全释放
    time.sleep(2)

    # ★ 清空 MAA 代理（避免代理阻断 localhost 连接）
    proxy_backup = _clear_maa_proxy()

    ok = start_maa(maa_exe)
    if not ok:
        _log("[ERROR] MAA 启动失败")
        _restore_maa_proxy(proxy_backup)
        _log("=" * 40)
        return False

    # MAA 启动成功后恢复代理（后续外部请求仍需要代理）
    _restore_maa_proxy(proxy_backup)

    _log("[DONE] MAA 重启完成")
    _log("=" * 40)
    return True


# ═══════════════════════════════════════════════
#  独立运行入口
# ═══════════════════════════════════════════════
if __name__ == "__main__":
    success = run_restart()
    sys.exit(0 if success else 1)
