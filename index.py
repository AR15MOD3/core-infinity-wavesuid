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

PLUGIN_VERSION = "0.3.1"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9777
DEFAULT_TIMEOUT = 90.0
START_TIMEOUT = 120.0
MAX_RECEIPT = 8000

#: 命令表里带这些字符的，是上游的正则触发器（`ww^(?P<char>…）` 那种），
#: 给模型看没有意义 —— 帮助里只列能照着念的普通命令词。
_REGEX_CHARS = set("\\^$()[]{}|?*+")


def _merged(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merged(out[key], value)
        else:
            out[key] = value
    return out


def _config(folder: Path) -> tuple[dict[str, Any], list[str]]:
    """读 config.example.json + config.json（后者覆盖前者）。

    交回 (配置, 问题清单)。**读坏了要说出来** —— 静默退回默认值会让
    「我明明开了 autoStart」变成一句无从查起的话（规范：失败不许伪装成成功）。
    """
    got: dict[str, Any] = {}
    problems: list[str] = []
    for name in ("config.example.json", "config.json"):
        path = folder / name
        if not path.is_file():
            continue
        try:
            one = json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            problems.append(f"{name} 不是合法 JSON（{exc}），已忽略")
            continue
        except OSError as exc:
            problems.append(f"{name} 读不出来（{exc}），已忽略")
            continue
        if not isinstance(one, dict):
            problems.append(f"{name} 的内容不是 JSON 对象，已忽略")
            continue
        got = _merged(got, one)
    return got, problems


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
    # Upstream error bodies/exception messages may contain credentials or URLs.
    if isinstance(exc, urllib.error.HTTPError):
        return f"服务返回 HTTP {exc.code}（响应正文未回传）"
    if isinstance(exc, urllib.error.URLError):
        return "连不上鸣潮服务"
    if isinstance(exc, TimeoutError):
        return "服务超时，结果未确认；请勿自动重复执行账号操作"
    return "鸣潮服务调用失败（详细异常未回传）"


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
                # Keep the actionable card URL before a potentially long digest.
                url = _media_url(base, str(seg.get("url") or ""), fmt)
                if url:
                    lines.append(("卡片图：" if kind == "html" else "图片：") + url)
                digest = str(seg.get("digest") or "").strip()
                if digest:
                    lines.append(("卡片文字：" if kind == "html" else "图片文字：") + digest)
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

    if got.get("job_id"):
        lines.append("任务状态：" + str(got.get("state") or "unknown") + "；" + str(got.get("summary") or ""))
        lines.append("任务编号：" + str(got["job_id"]) + "；回执游标：" + str(got.get("cursor", 0)))
        if got.get("pending"):
            lines.append("任务尚未完成。先将已有回执告知用户；等待用户操作或询问后，用 waves_task_status 查询后续，不要循环催查或重发命令；是否自动通知以本次回执登记结果为准。")
        if got.get("existing_task"):
            lines.append("复用了当前用户本会话的已有任务；没有启动新命令。")
        diagnostics = got.get('login_diagnostics')
        labels = {
            'creating':'正在创建网页登录会话','created':'网页登录会话已创建',
            'checking_page':'正在检查网页登录入口','waiting_browser':'等待本人网页授权',
            'polling':'等待外置服务返回登录结果','page_unconfirmed':'网页登录页面状态未确认',
            'saving':'正在保存本人登录','saved':'本人登录已保存',
            'public_service_unavailable':'外置登录服务不可用','public_session_expired':'外置网页登录会话提前失效',
            'public_page_unconfirmed':'新网页登录页面未确认有效','public_result_timeout':'等待已结束，结果未确认',
        }
        if isinstance(diagnostics, dict):
            phase = diagnostics.get('stage')
            if isinstance(phase, str) and phase in labels:
                lines.append('网页登录阶段：'+labels[phase])
                code = diagnostics.get('HTTP')
                if type(code) is int and 100 <= code <= 599:
                    lines.append('最近外置响应：HTTP '+str(code))
        walk(got.get("segments"))
    elif not got.get("ok"):
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


def _scoped_config(ctx, package):
    """Formal host configuration/resources only, never infer from QQ settings."""
    from telyincore.plugin_scope_store import identifier, path_key
    owner = ctx.get('bot_id')
    try:identifier(owner)
    except ValueError:return {}, ['需要宿主稳定 bot_id']
    if ctx.get('pluginSettingsBot')!=owner:
        return {}, ['宿主插件设置归属与 bot_id 不匹配']
    raw,resource=ctx.get('pluginConfigPath'),ctx.get('pluginDataDir')
    if not isinstance(raw,str) or not isinstance(resource,str):
        return {}, ['需要正式 pluginConfigPath 与 pluginDataDir，不使用 QQ 或共享配置兜底']
    config,scope=Path(raw),Path(resource)
    root=Path(str(ctx.get('root') or ''))
    expected=root.parent/'bots'/owner/'infinity/plugins/core-infinity-wavesuid'
    if (not root.is_absolute() or not config.is_absolute() or not scope.is_absolute()
            or path_key(config)!=path_key(expected/'config.json')
            or path_key(scope)!=path_key(expected/'runtime')):
        return {}, ['正式插件独立路径与 bot_id 不匹配']
    if path_key(scope.resolve())!=path_key(scope.absolute()) or path_key(config.resolve())!=path_key(config.absolute()):
        return {}, ['Bot 配置路径不允许符号链接或目录重定向']
    result, problems = {}, []
    def pairs(items):
        obj={}
        for key,value in items:
            if key in obj:raise ValueError('配置存在重复键')
            obj[key]=value
        return obj
    for path, required in [(package / 'config.example.json', False), (config, True)]:
        if not path.is_file():
            if required:
                problems.append('缺少正式独立插件 config.json；不会使用插件目录或其他 Bot 配置')
            continue
        if path.is_symlink():
            problems.append('不允许链接配置文件')
            continue
        try:
            with path.open('rb') as stream:raw=stream.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:raise ValueError('配置超过大小限制')
            one = json.loads(raw.decode('utf-8-sig'),object_pairs_hook=pairs)
            if not isinstance(one, dict):
                raise ValueError('必须为 JSON 对象')
            result = _merged(result, one)
        except (OSError, ValueError) as exc:
            problems.append(path.name + ' 配置错误：' + str(exc))
    result['_scopeDir'] = str(scope.resolve())
    return result, problems


def _require_host_runtime():
    # Same sticky denial used by the current official QQ plugin. No override,
    # invented capability, alternative loader or unrestricted fallback.
    from telyincore.plugins import _require_unconfined_lifecycle
    _require_unconfined_lifecycle()



def _login_kind(text, prefixes):
    """Return (login, attached credentials) using the dispatch prefix set."""
    word = text.strip()
    for prefix in sorted(set(prefixes), key=len, reverse=True):
        if prefix and word.startswith(prefix):
            word = word[len(prefix):].strip()
            break
    word = word.lower()
    words = ("登录", "登陆", "登入", "登龙", "login", "dl", "邮箱登录", "国际服登录",
             "抽卡登录", "抽卡登陆", "抽卡登入", "云鸣潮登录", "云鸣潮登陆", "云登陆", "云登录", "云登入")
    login = any(word.startswith(w) for w in words)
    return login, login and word not in words


def create(ctx):
    _require_host_runtime()
    # File-loaded Infinity entries have no package import context.
    import importlib.util
    runtime_spec = importlib.util.spec_from_file_location(
        "waves_scoped_runtime", Path(__file__).with_name("scoped_runtime.py"))
    runtime_module = importlib.util.module_from_spec(runtime_spec)
    runtime_spec.loader.exec_module(runtime_module)
    folder = Path(ctx["packageDir"])
    note = ctx.get("log")
    def log(line):
        if callable(note):
            note(line)
    runtime = runtime_module.ScopedRuntime(ctx, lambda: _scoped_config(ctx, folder), log)
    from telyincore.async_tools import OwnedBlockingIO, WorkOwner, current_invocation
    status_io = OwnedBlockingIO()
    def config():
        return runtime.settings or _scoped_config(ctx, folder)[0]
    def base():
        return runtime.settings["base"] if runtime.settings else ""

    def caller(user=""):
        from telyincore.session_view import event_scope
        active = ctx.get("active_events")
        events = tuple(active() or ()) if callable(active) else ()
        outside = [e for e in events if getattr(e, "origin", "") == "external"]
        if len(outside) != 1 or getattr(outside[0], "source", "") != "qq":
            raise ValueError("请由当前 QQ 会话发起命令；无法确认用户时不使用默认账号。")
        event = outside[0]; sender = str(getattr(event, "sender_key", ""))
        if not sender.startswith("qq:") or not sender[3:].isdigit():
            raise ValueError("当前 QQ 用户身份不完整；未执行命令。")
        uid = sender[3:]; scope = event_scope(event)
        mid = (getattr(event, "meta", {}) or {}).get("message_id")
        if not scope or not mid or (str(user or "").strip() and str(user).strip() != uid):
            raise ValueError("命令用户与当前 QQ 会话不一致，或缺少消息编号；未执行命令。")
        return uid, scope, str(mid)

    async def task_status(job_id: str, cursor: int = 0, **rest) -> str:
        _require_host_runtime()
        invocation = current_invocation.get()
        if invocation is None or invocation.name != 'waves_task_status':
            raise RuntimeError('waves_task_status requires an owned dispatcher invocation')
        try:
            identity = caller()
        except ValueError as exc:
            return str(exc)
        try:
            frozen = runtime.prepare_command()
            snapshot, _, _, fmt = frozen
            uid, audience, mid = identity
            payload = {'job_id': job_id, 'cursor': cursor, 'user_id': uid,
                       'scene': 'group' if audience.startswith('group:') else 'direct',
                       'audience': audience}
            encoded = json.dumps(payload, ensure_ascii=False)
            owner = WorkOwner(snapshot[2], snapshot[3], invocation.call_id, invocation.name)

            def execute(cancelled):
                answer = runtime.receipt_request_for(
                    frozen, identity, '/api/command-receipt', encoded, 8, cancelled)
                return _receipt(answer, snapshot[4], fmt)

            return await status_io.run(owner, execute)
        except Exception as exc:
            return f"任务状态未确认：{_why(exc)}；没有重新执行命令。"

    async def ask(command: str = "", user: str = "", images: list | None = None,
                  **rest) -> str:
        _require_host_runtime()
        invocation = current_invocation.get()
        if invocation is None or invocation.name != 'waves_command':
            raise RuntimeError('waves_command requires an owned dispatcher invocation')
        text = str(command or "").strip()
        if not text:
            return "没有给命令；先用 waves_help 查一条可用的命令词。"
        try:
            identity = caller(user)
        except ValueError as exc:
            return str(exc)
        uid, audience, mid = identity
        prepare_notice=ctx.get('prepare_waves_login_notification')
        prepared_notice=prepare_notice() if callable(prepare_notice) else None
        import hashlib
        request_id = hashlib.sha256(json.dumps([audience, uid, mid, text], ensure_ascii=False).encode()).hexdigest()
        payload = {'command': text, 'scene': 'group' if audience.startswith('group:') else 'direct',
                   'user_id': uid, 'audience': audience,
                   'bridge_receipts': True, 'request_id': request_id}
        if isinstance(images, list):
            usable = [str(one).strip() for one in images if str(one or '').strip()]
            if usable:
                payload['images'] = usable
        try:
            # All caller/model values are detached on the owner loop before the
            # first await. The worker never reads active_events or a Bot selector.
            frozen = runtime.prepare_command()
            snapshot, _, _, fmt = frozen
            encoded = json.dumps(payload, ensure_ascii=False)
            owner = WorkOwner(snapshot[2], snapshot[3], invocation.call_id, invocation.name)

            def execute(cancelled):
                health = runtime.receipt_request_for(
                    frozen, identity, '/api/health', 'null', 5, cancelled)
                if not isinstance(health, dict) or health.get('command_receipts') != 1:
                    return "当前服务尚未加载分阶段回执协议；本次没有执行命令，请通过原启动器正常重启 IRIS 后再验收。"
                prefixes = health.get('prefixes')
                if not isinstance(prefixes, list) or not all(isinstance(p, str) for p in prefixes):
                    return "服务未提供有效命令前缀；本次未执行命令。"
                login, credentials = _login_kind(text, prefixes)
                if login and prepared_notice is not None and health.get('login_notifications')!=1:
                    return '当前服务尚未加载登录结果通知协议，本次没有执行登录。请先完成统一服务版本切换。'
                group_login = audience.startswith('group:') and login
                if group_login and credentials:
                    return "群聊可以发起网页登录；账号、密码或验证码请在登录网页填写，不在群消息中提交。本次未执行带凭证的登录命令。"
                answer = runtime.receipt_request_for(
                    frozen, identity, '/api/chat', encoded, 12, cancelled)
                receipt = _receipt(answer, snapshot[4], fmt)
                if group_login:
                    receipt = ("这是当前发起者的临时网页登录入口；只在网页完成授权，不要转发入口或在群里发送凭证。"
                               "群聊可正常发起，不要求转私聊。若在 QQ 内打开，请通过右上角“在浏览器打开”完成授权。\n" + receipt)
                if login and answer.get('job_id') and answer.get('pending') is True:
                    return {'receipt':receipt,'notice_job':answer['job_id']}
                return receipt

            result=await status_io.run(owner, execute)
            if isinstance(result,dict) and 'notice_job' in result:
                if prepared_notice is not None:
                    try:armed=prepared_notice(result['notice_job'],frozen,identity)
                    except Exception:
                        return result['receipt']+'\n自动结果通知登记未确认；请保留此任务，勿重新登录，需要时查询原任务。'
                    return result['receipt']+ ('\n登录结束后将向原发起会话反馈；不要重新执行登录命令。' if armed else
                        '\n此任务已有通知记录，未安排重复通知；需要时只查询原任务。')
                return result['receipt']+'\n当前宿主未提供自动登录结果通知，需用户询问后查询同一任务。'
            return result
        except Exception as exc:
            return (f"鸣潮命令回执未确认：{_why(exc)}。"
                    "结果未确认；不要自动重试绑定、登录、签到或导入。服务在线不代表账号操作成功。")

    def help_query(topic, card_only, request, render_base, options):
        word = str(topic or "").strip()
        # 没给主题时，直接借上游那张「ww帮助」卡片 —— 那是人写过的目录，
        # 比自己拼一遍原始的触发器表可读得多。
        if not word:
            try:
                got = request("/api/chat",
                            {"command": "ww帮助",
                             "scene": str(options.get("scene") or "group")},
                            timeout=12)
                if isinstance(got, dict) and got.get("ok") is True:
                    if card_only is True:
                        # Preserve factual links, not thousands of words already on the card.
                        links = []
                        for segment in got.get("segments") or []:
                            if isinstance(segment, dict) and segment.get("type") in ("html", "image"):
                                url = _media_url(render_base, str(segment.get("url") or ""),
                                                 str(options.get("mediaFormat") or "png"))
                                if url and url not in links:
                                    links.append(url)
                        if links:
                            return ("帮助卡片地址（尚未发送）：\n" + "\n".join(links[:8])
                                    + "\n使用当前平台图片工具投递；若要解释具体命令，再查询对应 topic。")
                    return ("命令总览（来自上游「ww帮助」）：\n"
                            + _receipt(got, render_base, str(options.get("mediaFormat") or "png"))
                            + "\n\n以上是查询资料和卡片地址，不代表图片已发送。"
                              "按用户需要概括或使用当前平台的图片发送工具，保持命令与数据准确。"
                              "需要细分类目时可给 waves_help 一个 topic。")
            except Exception:  # noqa: BLE001 —— 卡片这条路不通就退回查命令表
                pass

        try:
            got = request("/api/commands", None, 15)
        except Exception as exc:  # noqa: BLE001
            return f"鸣潮服务不可用：{_why(exc)}。"

        triggers: list[str] = []
        for row in got.get("commands") or []:
            if not isinstance(row, dict):
                continue
            key = str(row.get("trigger") or "").strip()
            if not key or any(ch in _REGEX_CHARS for ch in key):
                continue
            if word and word not in key:
                continue
            if key not in triggers:
                triggers.append(key)

        if not triggers:
            return (f"没有匹配「{word}」的普通命令词。可以不带 topic 调一次，"
                    "看完整目录；上游的正则写法也仍然可用，只是这里不列。"
                    if word else "服务没有返回命令表。")

        head = (f"共 {len(triggers)} 条命令" if not word else
                f"与「{word}」有关的命令 {len(triggers)} 条")
        body = ["· " + one for one in triggers[:40]]
        if len(triggers) > 40:
            body.append(f"…另有 {len(triggers) - 40} 条，把 topic 写细一点。")
        return "\n".join([head + "：", *body])

    async def help_command(topic: str = "", card_only: bool = False, **rest) -> str:
        _require_host_runtime()
        invocation = current_invocation.get()
        if invocation is None or invocation.name != 'waves_help':
            raise RuntimeError('waves_help requires an owned dispatcher invocation')
        try:
            frozen = runtime.prepare_public()
            snapshot, scene, timeout, fmt = frozen
            owner = WorkOwner(snapshot[2], snapshot[3], invocation.call_id, invocation.name)
            word, card = str(topic or '').strip(), card_only is True
            options = {'scene': scene, 'commandTimeout': timeout, 'mediaFormat': fmt}

            def query(cancelled):
                def request(path, payload=None, timeout=None):
                    return runtime.public_request_for(frozen, path, payload, timeout, cancelled)
                return help_query(word, card, request, snapshot[4], options)

            return await status_io.run(owner, query)
        except Exception as exc:
            return f"鸣潮服务不可用：{_why(exc)}。"

    async def status(**rest):
        _require_host_runtime()
        invocation = current_invocation.get()
        if invocation is None or invocation.name != 'waves_status':
            raise RuntimeError('waves_status requires an owned dispatcher invocation')
        try:
            snapshot = runtime.prepare_status()
            if isinstance(snapshot, str):
                return snapshot
            owner = WorkOwner(snapshot[2], snapshot[3], invocation.call_id, invocation.name)
            return await status_io.run(owner,
                lambda cancelled: runtime.status_for(snapshot, cancelled))
        except Exception as exc:
            return f"鸣潮服务不可用：{_why(exc)}。"

    # Static, public, read-only command intent; no account data or side effects.
    help_command.__readonly_prefetch__ = {
        "version": 1, "source": "qq", "text": "ww帮助", "public": True,
        "arguments": {"topic": "", "card_only": True},
    }

    class Waves:
        # Nested I/O ownership is explicit; no worker is started by create().
        _status_io = status_io
        _tool_io = status_io

        def event_requires_isolation(self, event):
            if (getattr(event, 'origin', None) != 'external'
                    or getattr(event, 'source', None) != 'qq'):
                return False
            meta = getattr(event, 'meta', None)
            if not isinstance(meta, dict):
                return False
            text = meta.get('plain_text', '')
            if not isinstance(text, str):
                return False
            word = text.strip()
            # Use the exact current owned service's prefix set. Never HTTP here.
            return bool(word and any(word.startswith(prefix)
                                     for prefix in runtime.command_prefixes))

        def start(self):
            _require_host_runtime()
            try:
                runtime.start()
            except Exception as exc:
                runtime.last_error = str(exc)
                raise
        def stop(self):
            _require_host_runtime()
            status_io.assert_idle()
            runtime.stop()

        def system_hooks(self):
            from telyincore.system_calls import SystemHook
            return {'waves_login_result':SystemHook(self._prepare_login_result)}

        def _prepare_login_result(self,job_id,frozen,identity):
            from telyincore.system_calls import PreparedSystemCall,current_system_call
            from telyincore.async_tools import OwnedBlockingIO,WorkOwner
            import asyncio,time
            snapshot=frozen[0]
            io=OwnedBlockingIO()
            uid,audience,_=identity
            encoded=json.dumps({'job_id':job_id,'cursor':0,'user_id':uid,
                'scene':'group' if audience.startswith('group:') else 'direct','audience':audience})
            async def body():
                ticket=current_system_call.get()
                if ticket is None or ticket.instance is not self or ticket.operation!='waves_login_result':
                    raise RuntimeError('login result requires registered host system call')
                owner=WorkOwner(snapshot[2],snapshot[3],ticket.call_id,ticket.operation)
                def read(cancelled):
                    return runtime.receipt_request_for(frozen,identity,'/api/command-receipt',encoded,8,cancelled)
                answer=await io.run_system(ticket,owner,read)
                outcome=answer.get('login_outcome') if isinstance(answer,dict) else None
                if isinstance(outcome,dict) and outcome.get('state') in ('success','failure','timeout','unknown'):
                    if outcome['state']=='success' and outcome.get('saved') is not True:
                        return {'state':'unknown'}
                    result = {'state':outcome['state']}
                    if outcome.get('reason') in ('public_service_unavailable','public_session_expired','public_page_unconfirmed','public_result_timeout'):
                        result['reason'] = outcome['reason']
                    return result
                return {'state':'pending' if answer.get('pending') else 'unknown'}
            return PreparedSystemCall(snapshot[2],snapshot[3],body)

        def tools(self):
            _require_host_runtime()
            from telyincore.tools import ToolDef, ToolTag

            return [ToolDef(
                name="waves_command",
                description="执行一条鸣潮（Wuthering Waves）命令，例如「ww角色面板 椿」"
                            "「ww深渊」「ww抽卡记录」「ww体力」「ww日历」。"
                            "命令词不确定时先用 waves_help 查。"
                            "账号类功能第一次要先绑定：`ww绑定<9位UID>`；"
                            "查体力/签到还要先 `ww登录`。"
                            "本工具既能查，也能执行绑定、签到、导入抽卡记录这类"
                            "**会真实改变账号状态**的操作——做这些之前先跟用户确认。"
                            "返回正文、卡片文字和链接；长任务先返回已有回执或处理中。群聊或私聊均可发起网页登录，凭证只在网页填写；pending不代表成功。",
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "完整命令，可带或不带 ww 前缀；不要自己编命令词",
                        },
                        "user": {
                            "type": "string",
                            "description": "可选：核对当前发起者QQ号；留空取宿主当前QQ用户，不使用默认账号，不得填他人",
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
                            "当前消息已有host_read_prefetch回执时可直接使用其中结果，无需重复查询。"
                            "card_only=true仅精简工具回执中的目录资料，图片配文仍可自然组织。"
                            "不给 topic 时返回上游那张帮助目录；给 topic（如「深渊」「抽卡」"
                            "「排行」「体力」「面板」）则列出相关的普通命令词。",
                parameters={
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "关键词；留空列出全部命令",
                        },
                        "card_only": {
                            "type": "boolean",
                            "description": "精简回执：返回图片地址并省略完整目录文字，不限制图片配文；有topic时仍按主题查询",
                        },
                    },
                },
                tags=(ToolTag.READ,),
                handler=help_command,
            ), ToolDef(
                name="waves_task_status",
                description="只读查询当前 QQ 用户本会话的鸣潮长任务回执，不重新执行命令。"
                            "先把登录入口或处理中回执回复用户；用户完成操作或询问后再查，不能用服务在线代替账号结果。",
                parameters={"type": "object", "properties": {
                    "job_id": {"type": "string"}, "cursor": {"type": "integer", "minimum": 0}},
                    "required": ["job_id"], "additionalProperties": False},
                tags=(ToolTag.READ,), handler=task_status,
            ), ToolDef(
                name="waves_status",
                description="查鸣潮服务是否在线、加载了多少命令、是否由本插件启动。"
                            "命令报「服务不可用」时先调它。",
                parameters={"type": "object", "properties": {}},
                tags=(ToolTag.READ,),
                handler=status,
            )]

    return Waves()
