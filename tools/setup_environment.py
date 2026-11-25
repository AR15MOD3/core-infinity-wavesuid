"""Operator setup for one Waves installation. The worker never writes Bot config."""
import hashlib
import json
import os
from pathlib import Path
import platform
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from http.client import HTTPConnection, HTTPException

installer = runpy.run_path(str(Path(__file__).with_name("install_native.py")))
BIN = "Scripts" if os.name == "nt" else "bin"
EXE = ".exe" if os.name == "nt" else ""
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def acquire(stream):
    stream.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def active(folder):
    try:
        with (folder / "install.lock").open("r+b") as lock:
            acquire(lock)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return False


def write_state(folder, phase, message):
    with tempfile.NamedTemporaryFile(dir=folder, mode="w", encoding="utf8", delete=False) as f:
        json.dump(dict(phase=phase, message=message), f)
    os.replace(f.name, folder / "progress.json")


def uv_path(folder):
    private = folder / "bootstrap" / BIN / ("uv" + EXE)
    return str(private) if private.is_file() else shutil.which("uv")


def pinned(folder, source):
    marker = folder / ".telyin-upstream"
    return marker.is_file() and marker.read_text().strip() == source[1]


def components(home):
    venv = home / ".venv"
    site = list(venv.glob("lib/python*/site-packages")) + [venv / "Lib/site-packages"]
    plugin = home / "gsuid_core/plugins/XutheringWavesUID"
    browsers = home / "browsers"
    shell_names = {"headless_shell", "headless_shell.exe", "chrome-headless-shell", "chrome-headless-shell.exe"}
    return {
        "GsCore": pinned(home, installer["CORE"]) and (home / "gsuid_core/core.py").is_file(),
        "XutheringWavesUID": pinned(plugin, installer["WAVES"]) and (plugin / "XutheringWavesUID").is_dir(),
        "Python": (venv / BIN / ("python" + EXE)).is_file(),
        "鸣潮依赖": any(all((p / mod).is_dir() for mod in ("playwright", "pypinyin", "rapidfuzz", "cv2", "uvicorn")) for p in site),
        "Chromium": any(p.name in shell_names and p.is_file() for p in browsers.glob("chromium_headless_shell-*/**/*")),
    }


def available_port(preferred, exclude):
    def free(s, port):
        if os.name == "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind(("127.0.0.1", port))
        return s.getsockname()[1]
    if type(preferred) is int and 1024 <= preferred <= 65535 and preferred != exclude:
        try:
            with socket.socket() as s:
                return free(s, preferred)
        except OSError:
            pass
    while True:
        with socket.socket() as s:
            port = free(s, 0)
        if port != exclude:
            return port


def local_check(port, code):
    if type(port) is not int or not 1024 <= port <= 65535:
        return False
    conn = HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        conn.request("GET", "/waves/login-status")
        res = conn.getresponse()
        return res.status == 200 and ("入口校验码 " + code + "。").encode() in res.read(512)
    except (OSError, HTTPException):
        return False
    finally:
        conn.close()


def handle(context, action):
    config = Path(context["config_path"]).absolute()
    scope = config.parent
    owner = context["bot_id"]
    home = scope / "runtime/gscore"
    folder = scope / "runtime/setup"
    if (config.name != "config.json" or scope.parts[-5:] != ("bots", owner, "infinity", "plugins", "core-infinity-wavesuid")
            or config.resolve() != config or home.resolve() != home or folder.resolve() != folder
            or Path(context["data_dir"]) != home.parent):
        raise ValueError("需要当前 Bot 的正式鸣潮插件目录")
    cfg = json.loads(config.read_text(encoding="utf-8-sig")) if config.exists() else {}
    if cfg.get("ownerBot") not in (None, "", owner):
        raise ValueError("配置归属与当前 Bot 不一致")
    working = active(folder)
    checks = components(home)
    ready = all(checks.values())
    native = cfg.get("backend") == "gscore"
    loaded = context["plugin"].get("loaded") is True
    if action == "install_native" and not ready and not working:
        if native and loaded:
            return {"message": "请在插件总览停用鸣潮后修复环境。"}
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "install.log").open("ab") as log:
            child = subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve()), str(home)],
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=NO_WINDOW)
        # Wait only for startup, never hold the host admin lock during downloads.
        until = time.monotonic() + 2
        while not active(folder) and child.poll() is None and time.monotonic() < until:
            time.sleep(.04)
        return {"message": "已提交安装，请查看进度。"}
    if action == "install_native":
        return {"message": "安装正在进行。" if working else "环境已就绪。"}

    saved = folder / "progress.json"
    progress = json.loads(saved.read_text()) if saved.is_file() else {}
    if progress.get("phase") == "running" and not working:
        progress = {"phase":"interrupted", "message":"上次安装中断，可以重试。"}
    # Stable choices also keep newly configured, stopped Bots off the same defaults.
    preferred = 20000 + int(hashlib.sha256(owner.encode()).hexdigest()[:8], 16) % 12000 * 2
    port = cfg.get("nativePort", 18765) if native else preferred
    login = cfg.get("nativeLoginPort", 0)
    code = hashlib.sha256(str(home.resolve()).encode()).hexdigest()[:10]
    listening = local_check(login, code)
    suggested_core = port if native and loaded else available_port(port, login)
    suggested_login = login or available_port(suggested_core + 1, suggested_core)
    return dict(bot=owner, checks=checks, ready=ready, running=working, progress=progress,
                uv=bool(uv_path(folder)), hostPython=platform.python_version(), code=code,
                canInstall=not (native and loaded and not ready), loaded=loaded,
                nativePort=suggested_core, loginPort=suggested_login, listening=listening)


def work(home):
    folder = home.parent / "setup"
    with (folder / "install.lock").open("a+b") as lock:
        try:
            acquire(lock)
        except OSError:
            return
        stage = "准备安装"
        def progress(message):
            nonlocal stage
            stage = message
            write_state(folder, "running", stage)
        try:
            progress("准备 uv")
            uv = uv_path(folder)
            if not uv:
                bootstrap = folder / "bootstrap"
                subprocess.run([sys.executable, "-I", "-m", "venv", str(bootstrap)], check=True)
                subprocess.run([str(bootstrap / BIN / ("python" + EXE)), "-I", "-m", "pip", "install",
                                "--only-binary=:all:", "--no-deps", "uv==0.13.10"], check=True)
                uv = uv_path(folder)
            installer["install_runtime"](home, uv, progress)
            progress("核对已安装的组件")
            if not all(components(home).values()):
                raise RuntimeError("环境组件不完整")
            write_state(folder, "done", "安装完成，可以保存登录设置。")
        except Exception:
            write_state(folder, "failed", stage + "失败。请检查磁盘空间、网络和 runtime/setup/install.log，然后重试。")
            raise


if __name__ == "__main__":
    work(Path(sys.argv[1]))
