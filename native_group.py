"""Native GsCore wire protocol for the current Bot's QQ group connection."""
import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import threading
import time
from datetime import datetime
from collections import OrderedDict
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from urllib.parse import quote, urlsplit


def message_receive(event, parts, image_url):
    content = []
    if not isinstance(parts, list) or not str(event.get("self_id", "")).isdecimal():
        raise ValueError("QQ 账号或消息格式无效")
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("QQ 消息段无效")
        kind, data = part.get("type"), part.get("data")
        if not isinstance(data, dict):
            raise ValueError("QQ 消息段无效")
        if kind == "text":
            value = data.get("text")
        elif kind == "at":
            value = str(data.get("qq", ""))
        elif kind == "reply":
            kind, value = "reply_id", str(data.get("id", ""))
        elif kind == "image":
            value = image_url(data.get("url"))
        else:
            raise ValueError("鸣潮命令含未支持的 QQ 消息段")
        if not isinstance(value, str) or not value:
            raise ValueError("QQ 消息段缺少内容")
        content.append({"type": kind, "data": value})
    sender = event.get("sender") or {}
    return {"bot_id": "onebot", "bot_self_id": str(event["self_id"]),
            "group_id": str(event["group_id"]), "user_type": "group",
            "user_id": str(event["user_id"]), "msg_id": str(event["message_id"]),
            "sender": {k: sender[k] for k in ("nickname", "card", "role") if k in sender},
            "user_pm": {"owner": 2, "admin": 3}.get(sender.get("role"), 6),
            "content": content}


def onebot_segments(content, group, depth=0):
    if not isinstance(content, list) or depth > 3:
        raise ValueError("无效的 GsCore 消息段")
    result = []
    for part in content:
        kind, data = part["type"], part.get("data")
        if kind == "group":
            if str(data) != group:
                raise ValueError("群标记与目标不一致")
            continue
        if kind == "image_size":
            continue
        if kind == "node":
            result.extend(onebot_segments(data, group, depth + 1))
            continue
        if kind in ("text", "markdown") and isinstance(data, str):
            wire = {"type": "text", "data": {"text": data}}
        elif kind == "image" and isinstance(data, str) and data.startswith("base64://"):
            base64.b64decode(data[9:], validate=True)
            wire = {"type": "image", "data": {"file": data}}
        elif kind == "at" and str(data).isdecimal():
            wire = {"type": "at", "data": {"qq": str(data)}}
        elif kind in ("reply", "reply_id") and str(data).lstrip("-").isdecimal():
            wire = {"type": "reply", "data": {"id": str(data)}}
        elif kind == "buttons":
            buttons = [b for row in data for b in (row if isinstance(row, list) else [row])]
            labels = [str(b.get("data") or b.get("text")) for b in buttons if isinstance(b, dict)]
            wire = {"type": "text", "data": {"text": "\n" + " / ".join(labels)}}
        else:
            raise ValueError("当前 OneBot 群连接不支持此 GsCore 消息段")
        result.append(wire)
    return result


_READ_QUERY = re.compile(r"ww(?:帮助|体力|(?:练度排行|群练度排行|练度群排行榜?|练度排名|群练度排名|练度群排名|ldph|ldpm)(?: *(?:ss|s|a)? *[0-9]{0,2})?)", re.IGNORECASE)


_RANK_INPUT = re.compile(r"ww[\u3400-\u9fff·]{1,24}(?:排行榜?|排名)", re.IGNORECASE)
_FUZZY_SCORE = re.compile(r"^\[鸣潮\] 你可能想查询【ww([\u3400-\u9fff·]{1,16})评分排行】，已执行该指令$", re.MULTILINE)


def query_result(command, segments, group):
    body = "\n".join(s["data"]["text"] for s in segments if s["type"] == "text")
    fuzzy = _FUZZY_SCORE.search(body.replace("\r\n", "\n"))
    if fuzzy:
        character = fuzzy[1]
        result = {"上游模糊匹配后执行的命令": f"ww{character}评分排行",
                  "练度群排行的命令": "ww练度群排行"}
        if f"[鸣潮] 群【{group}】暂无【{character}】面板" in body.splitlines():
            result["本群缺少面板的角色"] = character
    elif command:
        result = {"命令": command}
    else:
        return None
    result["回执"] = "图片" if any(s["type"] == "image" for s in segments) else "文字"
    # Only public, verified result fields enter conversational context. Never
    # copy arbitrary upstream text, links, user names or credentials into it.
    if f"群【{group}】暂无练度排行数据" in body:
        result["排行记录"] = 0
    if "您暂无有效token，无法查询该数据" in body:
        result["查询状态"] = "缺少有效登录凭证"
    if "当前排行开启了登录验证" in body:
        result["排行要求登录"] = True
    page = re.search(r"本群排行第(\d{1,2})页暂无数据，当前共(\d{1,2})页", body)
    if page:
        result.update(请求页=int(page[1]), 总页数=int(page[2]))
    actions = [c for c in ("ww刷新面板", "ww登录", "ww绑定uid") if "【" + c + "】" in body]
    if actions:
        result["回执中的操作"] = actions
    return result


class NativeGroup:
    def __init__(self, ctx, config, child_type, login_kind, log):
        self.owner = ctx["bot_id"]
        if config.get("ownerBot") != self.owner:
            raise ValueError("原生鸣潮配置与 Bot 不匹配")
        self.home = Path(config["_scopeDir"]) / "gscore"
        self.python = self.home / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.port = config.get("nativePort", 18765)
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise ValueError("原生服务端口无效")
        self.login_port = config.get("nativeLoginPort", 0)
        if type(self.login_port) is not int or (self.login_port and
                (not 1024 <= self.login_port <= 65535 or self.login_port == self.port)):
            raise ValueError("网页登录端口需与原生服务端口不同")
        self.login_url = config.get("nativeLoginUrl", "")
        if not isinstance(self.login_url, str):
            raise ValueError("公网登录地址须为文本")
        if self.login_url:
            url = urlsplit(self.login_url)
            if (not self.login_port or url.scheme != "https" or not url.hostname
                    or not re.fullmatch(r"(?:[A-Za-z0-9-]+\.)+[A-Za-z][A-Za-z0-9-]*", url.hostname)
                    or url.hostname.endswith(".localhost") or url.path not in ("", "/")
                    or url.username is not None or url.port == 0 or "?" in self.login_url
                    or "#" in self.login_url or any(c.isspace() for c in self.login_url)):
                raise ValueError("请填写 HTTPS 登录域名（不含路径）并指定本机登录端口")
            self.login_url = self.login_url.rstrip("/")
        self.host_start = ctx.get("start_background_thread")
        self.child_type = child_type
        self.login_kind = login_kind
        self.log = log
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.seen = OrderedDict()
        self.results = OrderedDict()
        self.token = secrets.token_urlsafe(32)
        self.proof = secrets.token_hex(32)
        self.ws = self.child = self.thread = self.unbind = None

    def start(self):
        from telyincore.qq_group_route import bind_waves
        if not self.python.is_file() or not (self.home / "gsuid_core/plugins/XutheringWavesUID").is_dir():
            raise RuntimeError("缺少本 Bot 的 GsCore 环境，请先运行 install_native.py")
        launcher = Path(__file__).with_name("native_service.py")
        env = dict(os.environ, TELYIN_WAVES_TOKEN=self.token, TELYIN_WAVES_PROOF=self.proof)
        env["TELYIN_WAVES_SITE_CODE"] = hashlib.sha256(str(self.home.resolve()).encode()).hexdigest()[:10]
        env["TELYIN_WAVES_LOGIN_PORT"] = str(self.login_port)
        env["TELYIN_WAVES_LOGIN_URL"] = self.login_url
        with (self.home / "native-service.log").open("ab") as output:
            self.child = self.child_type([str(self.python), str(launcher), "--host", "127.0.0.1",
                                          "--port", str(self.port)], cwd=str(self.home), env=env,
                                         stdin=subprocess.DEVNULL, stdout=output, stderr=output)
        try:
            self.unbind = bind_waves(self.owner, self.receive)
            self.thread = threading.Thread(target=self.run, name="waves-gscore-ws", daemon=True)
            if self.host_start:
                self.host_start(self.thread, stop=self.signal_stop)
            else:
                self.thread.start()
        except BaseException:
            self.stop()
            raise

    def check_login_page(self, hosted=False):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            path = "/_telyin/waves/group-login" + ("?hosted" if hosted else "")
            conn.request("GET", path, headers={"X-Waves-Token": self.token})
            status = conn.getresponse().status
        except (HTTPException, OSError):
            status = None
        finally:
            conn.close()
        if status == 409:
            raise ValueError("鸣潮登录页尚未配置为群成员可打开的地址，请联系机器人管理员。请勿在群内发送登录凭证。")
        if status != 204:
            raise ValueError("暂时无法确认鸣潮登录页配置，请稍后再试。")

    def receive(self, event, parts, image_url):
        from telyincore.qq_group_route import deliver_group
        if not str(event.get("self_id", "")).isdecimal():
            raise ValueError("QQ 事件缺少机器人账号")
        key = tuple(str(event[k]) for k in ("self_id", "group_id", "message_id"))
        with self.lock:
            if self.stop_event.is_set() or key in self.seen:
                return
            self.seen[key] = None
            if len(self.seen) > 4096:
                self.seen.popitem(last=False)
            ws = self.ws
        try:
            msg = message_receive(event, parts, image_url)
            text = "".join(seg["data"] for seg in msg["content"] if seg["type"] == "text").strip()
            for seg in msg["content"]:
                if seg["type"] == "text" and seg["data"].strip():
                    word = seg["data"].lstrip()
                    if word[:2].lower() == "ww":
                        seg["data"] = "ww" + word[2:]
                    break
            login, credentials = self.login_kind(text.lower(), ["ww"])
            if credentials:
                raise ValueError("登录凭证请在网页登录页填写")
            if ws is None:
                raise ValueError("鸣潮服务尚未就绪，请稍后查询")
            if login:
                ordinary = text[2:].strip().lower() in ("登录", "登陆", "登入", "登龙", "login", "dl")
                self.check_login_page(hosted=bool(self.login_port and ordinary))
            command = text.lower() if len(text) <= 40 and _READ_QUERY.fullmatch(text) else None
            if command or _RANK_INPUT.fullmatch(text):
                with self.lock:
                    self.seen[key] = (msg["user_id"], command, time.monotonic(),
                                      event.get("time") if type(event.get("time")) in (int, float) else int(time.time()))
            ws.send(json.dumps(msg, ensure_ascii=False).encode())
            return
        except ValueError as exc:
            notice = str(exc)
        except Exception:
            notice = "鸣潮连接已断开，命令结果未确认"
        deliver_group(self.owner, key[0], key[1], [{"type": "text", "data": {"text": notice}}], key[2])

    def deliver(self, ws, raw):
        from telyincore.qq_group_route import deliver_group
        frame = json.loads(raw)
        group, account = frame.get("target_id"), frame.get("bot_self_id")
        if (frame.get("target_type") != "group" or frame.get("bot_id") != "onebot"
                or not isinstance(group, str) or not group.isdecimal()
                or not isinstance(account, str) or not account.isdecimal()):
            raise ValueError("非本适配器的 QQ 群消息")
        original = frame.get("msg_id") or ""
        if original:
            with self.lock:
                if (account, group, str(original)) not in self.seen:
                    raise ValueError("未匹配到原群消息")
        segments = onebot_segments(frame.get("content"), group)
        if not segments or self.stop_event.is_set():
            return
        mid = deliver_group(self.owner, account, group, segments, str(original))
        if original:
            self.remember_result((account, group, str(original)), mid, segments)
        if frame.get("echo") is not None:
            ws.send(json.dumps({"bot_id": "onebot", "bot_self_id": account, "group_id": group,
                                "content": [{"type": "recall_message_id",
                                             "data": {"echo": frame["echo"], "id": mid}}]}).encode())

    def remember_result(self, key, reply_id, segments):
        with self.lock:
            request = self.seen.get(key)
            if not request or self.stop_event.is_set():
                return
            sender, command, issued, event_time = request
            facts = query_result(command, segments, key[1])
            if facts is None:
                return
            self.results[key] = (sender, str(reply_id), issued, event_time, facts)
            self.results.move_to_end(key)
            if len(self.results) > 256:
                self.results.popitem(last=False)

    def followup_context(self, batch):
        if not batch:
            return ""
        room = None
        eligible = []
        for event in batch:
            if getattr(event, "origin", "") != "external":
                continue
            meta = getattr(event, "meta", None) or {}
            conversation = meta.get("conversation", "")
            if (getattr(event, "source", "") != "qq" or not conversation.startswith("group:")
                    or not getattr(event, "sender_key", "").startswith("qq:")):
                return ""
            route = (str(meta.get("self_id", "")), conversation[6:])
            if room is not None and route != room:
                return ""
            room = route
            try:
                stamp = datetime.fromisoformat(event.ts).timestamp()
            except (AttributeError, ValueError, TypeError, OSError):
                continue
            eligible.append((event.sender_key[3:], str(meta.get("reply_to_message_id") or ""), stamp))
        found = {}
        now = time.monotonic()
        with self.lock:
            if self.stop_event.is_set():
                return ""
            for key in list(self.results):
                if now - self.results[key][2] > 300:
                    del self.results[key]
            for key, (sender, reply, issued, event_time, facts) in self.results.items():
                if key[:2] != room:
                    continue
                for member, quote, stamp in eligible:
                    if stamp < event_time or stamp - event_time > 300:
                        continue
                    if (quote and quote in (key[2], reply)) or (not quote and member == sender):
                        previous = found.get((member, quote))
                        if previous is None or issued > previous[0]:
                            found[(member, quote)] = (issued, key[2], facts)
        records = {mid: facts for _, mid, facts in sorted(found.values(), key=lambda item: item[0])[-4:]}
        return ("〔本轮发言人刚在本群向你查询或引用的鸣潮回执〕" + json.dumps(list(records.values()), ensure_ascii=False) + "\n"
                if records else "")

    def run(self):
        from websockets.sync.client import connect
        endpoint = f"ws://127.0.0.1:{self.port}/ws/Telyin-{quote(self.owner, safe='')}"
        while not self.stop_event.is_set():
            if self.child.poll() is not None:
                self.log("原生 GsCore 已退出，请查看本 Bot 的 native-service.log")
                return
            try:
                with connect(endpoint, additional_headers={"X-Waves-Token": self.token},
                             proxy=None, open_timeout=3, close_timeout=1, max_size=32 << 20) as ws:
                    if not secrets.compare_digest(ws.response.headers.get("X-Waves-Proof", ""), self.proof):
                        raise ValueError("原生服务实例不匹配")
                    with self.lock:
                        self.ws = ws
                    self.log("原生鸣潮群连接已就绪")
                    while not self.stop_event.is_set():
                        try:
                            raw = ws.recv(timeout=0.3)
                        except TimeoutError:
                            continue
                        try:
                            self.deliver(ws, raw)
                        except Exception as exc:
                            self.log("鸣潮群回执未送达：" + type(exc).__name__)
            except (OSError, TimeoutError, ValueError) as exc:
                if isinstance(exc, ValueError):
                    self.log("拒绝连接到其他服务实例")
            except Exception as exc:
                self.log("鸣潮连接断开：" + type(exc).__name__)
            finally:
                with self.lock:
                    self.ws = None
            self.stop_event.wait(1)

    def signal_stop(self):
        self.stop_event.set()

    def stop(self):
        self.signal_stop()
        if self.unbind:
            self.unbind()
            self.unbind = None
        if self.child:
            self.child.terminate()
            try:
                self.child.wait(8)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(5)
        if self.thread and self.thread.ident is not None:
            self.thread.join(50)
            if self.thread.is_alive():
                raise RuntimeError("原生群回执尚未停止")
        self.child = None
