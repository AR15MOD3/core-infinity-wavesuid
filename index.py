"""TelyinCore Infinity 插件 · 鸣潮（Wuthering Waves）查询桥接。

上游：XutheringWavesUID <https://github.com/Loping151/XutheringWavesUID>（GPL-3.0-or-later）。
本文件是原创的桥接层：只通过本机 HTTP 调用 waves service，不 import、不复制上游代码。

三条硬约束（对应《Infinity 插件开发规范 v1.2》）：
· `create` 不做事；连接与子进程只在 `start` 里做；
· `stop` 必须收掉自己拉起的子进程，否则重载会双开监听；
· 服务不可达时给一句人话，不把失败伪装成成功。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PLUGIN_VERSION = "0.1.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9777
DEFAULT_TIMEOUT = 90.0
START_TIMEOUT = 120.0
MAX_RECEIPT = 8000


def _merged(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merged(out[key], value)
        else:
            out[key] = value
    return out


def _config(folder: Path) -> dict[str, Any]:
    """读 config.example.json + config.json（后者覆盖前者）。每次调用都重读，配置重载即生效。"""
    got: dict[str, Any] = {}
    for name in ("config.example.json", "config.json"):
        path = folder / name
        if not path.is_file():
            continue
        try:
            one = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(one, dict):
            got = _merged(got, one)
    return got


def _http(url: str, payload: Any = None, timeout: float = DEFAULT_TIMEOUT) -> Any:
    body = (json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None else None)
    request = urllib.request.Request(
        url, data=body, method="POST" if body is not None else "GET",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _why(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            detail = exc.read().decode("utf-8", "replace").strip()[:300]
        except Exception:  # noqa: BLE001 —— 只为把错误说清楚，读不出来就不说细节
            detail = ""
        return f"服务返回 HTTP {exc.code}" + (f"：{detail}" if detail else "")
    if isinstance(exc, urllib.error.URLError):
        return f"连不上服务（{exc.reason}）"
    if isinstance(exc, TimeoutError):
        return "服务超时未响应"
    return f"{type(exc).__name__}: {exc}"


def _absolute(base: str, url: str) -> str:
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return base + ("" if url.startswith("/") else "/") + url


def _media_url(base: str, url: str, fmt: str) -> str:
    full = _absolute(base, url)
    # 服务把卡片存成 .html；同名的 PNG 在 <html 链接>/png 上按需渲染。
    if fmt == "png" and full.endswith(".html"):
        return full + "/png"
    return full


def _receipt(got: Any, base: str, fmt: str) -> str:
    """把服务返回的 segments 折成模型看得懂的一段回执：正文 + 卡片文字 + 可点的链接。"""
    if not isinstance(got, dict):
        return "鸣潮服务返回了无法解析的内容。"

    lines: list[str] = []

    def walk(segments: Any) -> None:
        for seg in segments or []:
            if not isinstance(seg, dict):
                continue
            kind = seg.get("type")
            if kind == "text":
                text = str(seg.get("text") or "").strip()
                if text:
                    lines.append(text)
            elif kind in ("html", "image"):
                digest = str(seg.get("digest") or "").strip()
                if digest:
                    lines.append(("卡片文字：" if kind == "html" else "图片文字：") + digest)
                url = _media_url(base, str(seg.get("url") or ""), fmt)
                if url:
                    lines.append(("卡片图：" if kind == "html" else "图片：") + url)
            elif kind == "node":
                walk(seg.get("items"))
            elif kind == "file":
                name = str(seg.get("name") or "文件")
                url = _media_url(base, str(seg.get("url") or ""), "html")
                lines.append(f"文件：{name}" + (f" {url}" if url else ""))
            elif kind == "buttons":
                labels = []
                for row in seg.get("buttons") or []:
                    for button in (row if isinstance(row, list) else [row]):
                        if isinstance(button, dict) and button.get("text"):
                            labels.append(str(button["text"]))
                if labels:
                    lines.append("可用快捷指令：" + " / ".join(labels))

    if not got.get("ok"):
        lines.append(str(got.get("summary") or "命令没有执行。"))
    else:
        walk(got.get("segments"))

    if not lines:
        lines.append(str(got.get("summary") or "已执行，服务没有返回正文。"))

    text = "\n".join(lines)
    if len(text) > MAX_RECEIPT:
        text = (text[:MAX_RECEIPT]
                + "\n…（回执过长，已截断；完整内容见上面的卡片链接）")
    return text


def create(ctx):
    folder = Path(str(ctx.get("packageDir") or Path.cwd()))
    note = ctx.get("log")

    def log(line: str) -> None:
        if callable(note):
            note(line)

    child: dict[str, Any] = {"proc": None}
    guard = threading.Lock()

    def config() -> dict[str, Any]:
        return _config(folder)

    def base() -> str:
        got = config()
        host = str(got.get("host") or DEFAULT_HOST)
        try:
            port = int(got.get("port") or DEFAULT_PORT)
        except (TypeError, ValueError):
            port = DEFAULT_PORT
        return f"http://{host}:{port}"

    def health(timeout: float = 2.0) -> dict[str, Any] | None:
        try:
            got = _http(base() + "/api/health", None, timeout)
        except Exception:  # noqa: BLE001 —— 探活失败就是"没起来"，不往上抛
            return None
        return got if isinstance(got, dict) else None

    def ask(command: str = "", user: str = "", images: list | None = None,
            **rest) -> str:
        text = str(command or "").strip()
        if not text:
            return "没有给命令；先用 waves_help 查一条可用的命令词。"
        got = config()
        payload: dict[str, Any] = {"command": text,
                                   "scene": str(got.get("scene") or "group")}
        if str(user or "").strip():
            payload["user_id"] = str(user).strip()
        if isinstance(images, list):
            usable = [str(one).strip() for one in images if str(one or "").strip()]
            if usable:
                payload["images"] = usable
        try:
            answer = _http(base() + "/api/chat", payload,
                           timeout=float(got.get("commandTimeout") or DEFAULT_TIMEOUT))
        except Exception as exc:  # noqa: BLE001 —— 必须变成一句可读的回执
            return (f"鸣潮服务不可用：{_why(exc)}。"
                    "请确认 waves service 已启动（或在配置里打开 autoStart）。")
        return _receipt(answer, base(), str(got.get("mediaFormat") or "png"))

    def help_command(topic: str = "", **rest) -> str:
        word = str(topic or "").strip()
        try:
            got = _http(base() + "/api/commands", None, 15)
        except Exception as exc:  # noqa: BLE001
            return f"鸣潮服务不可用：{_why(exc)}。"
        rows = [row for row in (got.get("commands") or []) if isinstance(row, dict)]
        if word:
            rows = [row for row in rows
                    if word in str(row.get("sv") or "")
                    or word in str(row.get("trigger") or "")]
        seen: set[str] = set()
        triggers: list[str] = []
        for row in rows:
            key = str(row.get("trigger") or "").strip()
            if key and key not in seen:
                seen.add(key)
                triggers.append(key)
        if not triggers:
            return (f"没有匹配「{word}」的命令。" if word
                    else "服务没有返回命令表。")
        head = f"共 {len(triggers)} 条命令" + (f"（匹配「{word}」）" if word else "") + "："
        body = ["· " + one for one in triggers[:80]]
        if len(triggers) > 80:
            body.append(f"…另有 {len(triggers) - 80} 条，给 topic 缩小范围。")
        return "\n".join([head, *body])

    def status(**rest) -> str:
        got = health(3.0)
        if got is None:
            return (f"waves service 没起来（{base()}）。"
                    "检查服务是否运行，或打开 autoStart 并填好 python / serviceDir。")
        proc = child.get("proc")
        mine = "是" if proc is not None and proc.poll() is None else "否"
        return ("waves service 在线："
                f"{base()}\n"
                f"命令 {got.get('commands')} 条；前缀 {got.get('prefixes')}；"
                f"已加载插件 {got.get('plugins')}\n"
                f"由本插件启动：{mine}")

    class Waves:
        def start(self) -> None:
            if health() is not None:
                log("waves service 已在运行，沿用现有进程")
                return
            got = config()
            if not got.get("autoStart"):
                log("waves service 没有运行；autoStart 关闭 —— 工具会如实报不可用")
                return
            python = str(got.get("python") or "").strip() or sys.executable
            service = Path(str(got.get("serviceDir") or "").strip()
                           or (folder / "service"))
            if not (service / "rover" / "main.py").is_file():
                log(f"找不到服务目录：{service}（应包含 rover/main.py）")
                return
            with guard:
                running = child.get("proc")
                if running is not None and running.poll() is None:
                    return
                log_path = Path(str(ctx.get("dataDir") or folder)) / "waves-service.log"
                try:
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    sink: Any = open(log_path, "ab")
                except OSError:
                    sink = subprocess.DEVNULL
                command = [python, "-m", "rover.main",
                           "--host", str(got.get("host") or DEFAULT_HOST),
                           "--port", str(int(got.get("port") or DEFAULT_PORT))]
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
                env = os.environ.copy()
                env.setdefault("PYTHONUTF8", "1")
                env.setdefault("PYTHONIOENCODING", "utf-8")
                try:
                    child["proc"] = subprocess.Popen(
                        command, cwd=str(service), stdin=subprocess.DEVNULL,
                        stdout=sink, stderr=subprocess.STDOUT, env=env,
                        creationflags=flags)
                except OSError as exc:
                    log(f"waves service 启动失败：{exc}")
                    return
                finally:
                    if sink is not subprocess.DEVNULL:
                        # 子进程已继承句柄；父进程这边关掉，避免重载时句柄泄漏。
                        sink.close()
                deadline = time.monotonic() + START_TIMEOUT
                while time.monotonic() < deadline:
                    if health(1.5) is not None:
                        log("waves service 已就绪")
                        return
                    proc = child.get("proc")
                    if proc is None or proc.poll() is not None:
                        log(f"waves service 启动即退出（code={getattr(proc, 'returncode', '?')}）"
                            f"，见 {log_path}")
                        child["proc"] = None
                        return
                    time.sleep(0.5)
                log(f"waves service 在 {START_TIMEOUT:.0f}s 内没有就绪，见 {log_path}")

        def stop(self) -> None:
            with guard:
                proc = child.get("proc")
                child["proc"] = None
            if proc is None or proc.poll() is not None:
                return
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    pass
            log("waves service 已停止")

        def tools(self):
            from telyincore.tools import ToolDef, ToolTag

            return [ToolDef(
                name="waves_command",
                description="执行一条鸣潮（Wuthering Waves）命令，例如「ww椿面板」"
                            "「ww角色查询 椿」「ww深渊」「ww抽卡记录」「ww体力」。"
                            "命令词不确定时先用 waves_help 查。"
                            "本工具既能查，也能执行绑定、签到、导入抽卡记录这类"
                            "**会真实改变账号状态**的操作——做这些之前先跟用户确认。"
                            "返回正文、卡片上的文字和可打开的图片链接。",
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "完整命令，可带或不带 ww 前缀；不要自己编命令词",
                        },
                        "user": {
                            "type": "string",
                            "description": "可选：发起者的 QQ 号；留空则用服务端默认使用者",
                        },
                        "images": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "可选：声骸评分等命令需要的图片地址（http(s) 或 base64://）",
                        },
                    },
                    "required": ["command"],
                },
                tags=(ToolTag.READ, ToolTag.ACT),
                handler=ask,
            ), ToolDef(
                name="waves_help",
                description="查鸣潮命令词：不知道「ww」后面该接什么时先调它。"
                            "topic 可填角色名、功能词（面板/深渊/抽卡/体力/排行/签到）或命令前缀。",
                parameters={
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "关键词；留空列出全部命令",
                        },
                    },
                },
                tags=(ToolTag.READ,),
                handler=help_command,
            ), ToolDef(
                name="waves_status",
                description="查鸣潮服务是否在线、加载了多少命令、是否由本插件启动。"
                            "命令报「服务不可用」时先调它。",
                parameters={"type": "object", "properties": {}},
                tags=(ToolTag.READ,),
                handler=status,
            )]

    return Waves()
