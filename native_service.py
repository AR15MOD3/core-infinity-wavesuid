"""Start upstream GsCore in the owning Bot's private installation."""
import asyncio
import ipaddress
import os
import re
import socket
from contextlib import nullcontext
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit


def has_group_login_url(value):
    if not isinstance(value, str) or any(c.isspace() or not c.isprintable() for c in value):
        return False
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower().rstrip(".")
        if (url.scheme not in ("http", "https") or not host or url.port == 0
                or url.username is not None or "?" in value or "#" in value or "\\" in value
                or "%" in host):
            return False
        if host == "localhost" or host.endswith(".localhost"):
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # Single-label numbers and abbreviated IPv4 addresses can be
            # interpreted by browsers as loopback (e.g. 2130706433, 127.1).
            labels = host.split(".")
            return not all(label.isdigit() or label.startswith("0x") for label in labels)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return not (address.is_loopback or address.is_unspecified)
    except ValueError:
        return False


class InstanceSocket:
    def __init__(self, app, token, proof, hosted_status=None):
        self.app, self.token, self.proof = app, token, proof
        self.hosted_status = hosted_status

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/_telyin/waves/group-login":
            from starlette.responses import Response
            token = dict(scope["headers"]).get(b"x-waves-token", b"")
            if scope["method"] != "GET" or not secrets.compare_digest(self.token, token):
                status = 403
            elif scope.get("query_string") == b"hosted":
                status = self.hosted_status() if self.hosted_status else 409
            else:
                from gsuid_core.utils.plugins_config.gs_config import all_config_list
                config = all_config_list.get("XutheringWavesUID")
                value = config.get_config("WavesLoginUrl").data if config else None
                status = 204 if has_group_login_url(value) else 409
            return await Response(status_code=status, headers={"Cache-Control": "no-store"})(scope, receive, send)
        if scope["type"] != "websocket":
            return await self.app(scope, receive, send)
        token = dict(scope["headers"]).get(b"x-waves-token", b"")
        if not secrets.compare_digest(self.token, token):
            await send({"type": "websocket.close", "code": 1008})
            return

        from gsuid_core.sv import SL
        services = (SL.lst.get(name) for name in ("ww练度排行", "waves查询体力"))
        if not all(sv and sv.TL and sv.self_plugin_name == "XutheringWavesUID" for sv in services):
            await send({"type": "websocket.close", "code": 1013})
            return

        async def accept(message):
            if message["type"] == "websocket.accept":
                message = dict(message, headers=[*message.get("headers", []),
                                                  (b"x-waves-proof", self.proof)])
            await send(message)
        await self.app(scope, receive, accept)


class GroupLoginPage:
    """The public-facing listener accepts only the two ordinary group login routes."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        from starlette.responses import Response
        if scope["type"] != "http":
            return
        method, path = scope["method"], scope["path"]
        headers = {b"cache-control": b"no-store", b"referrer-policy": b"no-referrer",
                   b"x-frame-options": b"DENY", b"x-content-type-options": b"nosniff"}
        if method == "GET" and path == "/waves/login-status":
            code = os.environ.get("TELYIN_WAVES_SITE_CODE", "")
            response = Response(f"鸣潮网页登录入口已连通。入口校验码 {code}。请回到 QQ 群发送 ww登录。",
                                media_type="text/plain", headers={"Cache-Control": "no-store"})
            return await response(scope, receive, send)
        error = 0
        if method == "GET" and re.fullmatch(r"/waves/i/[A-Za-z0-9_-]{22}", path):
            pass
        elif method == "POST" and path == "/waves/login":
            fields = dict(scope["headers"])
            if fields.get(b"content-type", b"").split(b";", 1)[0].strip().lower() != b"application/json":
                error = 415
            else:
                data = bytearray()
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    data.extend(message.get("body", b""))
                    if len(data) > 4096:
                        error = 413
                        break
                    if not message.get("more_body"):
                        break

                async def read_body():
                    return {"type":"http.request", "body":bytes(data), "more_body":False}
                receive = read_body
        else:
            error = 404
        if error:
            return await Response(status_code=error)(scope, receive, send)

        async def reply(message):
            if message["type"] == "http.response.start":
                message = dict(message, headers=[(k,v) for k,v in message["headers"] if k not in headers]
                               + list(headers.items()))
            await send(message)
        await self.app(scope, receive, reply)


async def main():
    browser_dir = Path.cwd() / "browsers"
    if browser_dir.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_dir)
    from gsuid_core.core import serve
    from gsuid_core.app_life import app
    login_port = int(os.environ.pop("TELYIN_WAVES_LOGIN_PORT", "0"))
    login_url = os.environ.get("TELYIN_WAVES_LOGIN_URL", "")
    server = task = None

    def hosted_status():
        if not login_url:
            return 409
        return 204 if server and server.started and task and not task.done() else 503

    app.add_middleware(InstanceSocket, token=os.environ.pop("TELYIN_WAVES_TOKEN").encode(),
                       proof=os.environ.pop("TELYIN_WAVES_PROOF").encode(), hosted_status=hosted_status)
    if not login_port:
        await serve()
        return
    import uvicorn

    class LoginServer(uvicorn.Server):
        # GsCore owns process signals and the ASGI application lifespan.
        def capture_signals(self):
            return nullcontext()

        def install_signal_handlers(self):
            pass

    try:
        listener = socket.create_server(("127.0.0.1", login_port))
    except OSError as exc:
        print(f"鸣潮网页登录端口 {login_port} 无法监听：{exc}", file=sys.stderr, flush=True)
        await serve()
        return
    with listener:
        server = LoginServer(uvicorn.Config(GroupLoginPage(app), lifespan="off", ws="none",
                             proxy_headers=False, access_log=False, log_config=None,
                             limit_concurrency=24, timeout_keep_alive=5, timeout_graceful_shutdown=3))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            await serve()
        finally:
            server.should_exit = True
            await task


if __name__ == "__main__":
    from native_registration import ensure_registration_root
    ensure_registration_root(Path.cwd())
    sys.path.insert(0, str(Path.cwd()))
    from gsuid_core.supervisor import Supervisor, is_supervised
    if is_supervised():
        asyncio.run(main())
    else:
        # Upstream refreshes compiled resources and requests a restart with
        # exit code 51. Re-enter this adapter on each new native worker.
        command = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
        sys.exit(Supervisor(command, os.environ).run())
