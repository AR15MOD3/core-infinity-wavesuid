"""Apply verified path and group-scoping fixes to the two pinned upstream trees."""
import hashlib
import os
import tempfile
from pathlib import Path

# The initial IRIS repair changed only SV registration. Recognize that exact
# source too so existing installations can adopt the shared path resolver.
_ORIGINAL = "            parts = path.parts\n"
_FIRST_FIX = (
    "            core_root = Path(__file__).resolve().parent\n"
    "            path = path.resolve()\n"
    "            parts = path.relative_to(core_root).parts if path.is_relative_to(core_root) else path.parts\n"
)
_IMPORT = "from gsuid_core._telyin_plugin_paths import plugin_parts"
_WAVES_MODELS = "plugins/XutheringWavesUID/XutheringWavesUID/utils/database/models.py"
_LOGIN_MODULE = 'plugins/XutheringWavesUID/XutheringWavesUID/wutheringwaves_login/login.py'
_PINS = {
    _LOGIN_MODULE: "2274e8fb75e24d0df83dc7d0070d7392f543edde001c1169d6087ee895ff9246",
    _WAVES_MODELS: "b945e8380b88db686eb070f477d2dc3c725fb0af0b1792b2c18b72e5a39b8d9e",
    "sv.py": "cc9a209b9990ddb6f119644daaf8b2c4c23962bbe7ad653d26d2b6c3fd35dcae",
    "logger.py": "f7544992b3b4b0b7d17e050e96481193144fc5b582ce3c702f69245800eea303",
    "ai_core/agent_node/registry.py": "72398bf95ccac9409568255bf2dff7f2f4be03d43e88eb8efab2d24e2d2e1046",
    "ai_core/command_aside/plugin_read.py": "1017fb6c2269f2185ee09e5970f842d781e6c46075537b5ab185646f3d79cfd3",
    "plugins/XutheringWavesUID/XutheringWavesUID/utils/plugin_checker.py":
        "d4e35d53a89efcbbea6fd417053fa30ffd5f255ee97736b406c7cb426f819d6a",
}

_PATCHED = {
    _LOGIN_MODULE: "6580cec6ccd26069ef838666a02f5494c7178d5f6715b240f8812f2796319294",
    _WAVES_MODELS: "e5d66fbc6e1a7eae9833dfeb7ea88479fd37cedbffd739b5336a57793f5b8d30",
    'sv.py': "66cde70faec9004cbeff6b4b9aeee4f8ec2d68b89fe28a23b74a53eab299e8f4",
    'logger.py': "8a8ebb02a9c90579b016e59ee3a9fdf40d8cabadfdec8b0de82c3484a175871b",
    'ai_core/agent_node/registry.py': "0b87464790c770f53ca28641102ca21690a1915753decb3703aa562421706575",
    'ai_core/command_aside/plugin_read.py': "fedcfc8fe46b55b7840280ff8ae3181cd9cf1782f9e911c76ca1dfb1efec6e8f",
    'plugins/XutheringWavesUID/XutheringWavesUID/utils/plugin_checker.py': "83f2c4bab13c08f4671ae24031c5015206ea92d8e7607ac20c008418e38f21fa",
}


def _rewrite(path, source):
    if path == _LOGIN_MODULE:
        source = source.replace("await get_url()", "await group_page_url()")
        helper = (
            'async def group_page_url() -> tuple[str, bool]:\n'
            '    origin = os.environ.get("TELYIN_WAVES_LOGIN_URL")\n'
            '    if origin:\n'
            '        return origin, True\n'
            '    return await get_url()\n'
            '\n'
            '\n'
        )
        return source.replace('async def get_url() -> tuple[str, bool]:', helper + 'async def get_url() -> tuple[str, bool]:')
    if path == _WAVES_MODELS:
        # Upstream stores a member's group IDs joined by underscores.
        return source.replace(
            '        result = await session.scalars(select(cls).where(col(cls.group_id).contains(group_id)))',
            '        if not group_id:\n'
            '            return []\n'
            '        from sqlalchemy import literal\n'
            '        groups = literal("_") + col(cls.group_id) + "_"\n'
            '        result = await session.scalars(select(cls).where(groups.contains(f"_{group_id}_", autoescape=True)))',
        )
    if path == "sv.py":
        return source.replace(_ORIGINAL, f"            {_IMPORT}\n            parts = plugin_parts(path) or (path.name,)\n")
    if path == "logger.py":
        return source.replace("        parts = Path(pathname).parts", f"        {_IMPORT}\n        parts = plugin_parts(pathname)")
    if path == "ai_core/agent_node/registry.py":
        start = source.index("    try:\n", source.index("def _plugin_name_from_path"))
        end = source.index("    for i, part", start)
        return source[:start] + f"    {_IMPORT}\n    parts = plugin_parts(path)\n" + source[end:]
    if path == "ai_core/command_aside/plugin_read.py":
        source = source.replace("    parts = path.parts", f"    {_IMPORT}, CORE_ROOT\n    parts = plugin_parts(path)")
        return source.replace("root = Path(*parts[: index + 2])", "root = CORE_ROOT.joinpath(*parts[: index + 2])")
    start = source.index("            # 记录调用栈")
    end = source.index("            frame = frame.f_back", start)
    source = source[:start] + (
        "            parts = plugin_parts(file_path)\n"
        "            if parts and parts[0] == 'plugins' and Path(file_path).name not in skip_files:\n"
        "                all_plugins.append(parts[1])\n\n"
    ) + source[end:]
    return source.replace("import inspect\n", f"import inspect\nfrom pathlib import Path\n{_IMPORT}\n")


def _write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        pending = Path(f.name)
    try:
        pending.write_bytes(data)
        if path.exists():
            pending.chmod(path.stat().st_mode)
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def ensure_registration_root(home):
    core = Path(home) / "gsuid_core"
    helper = core / "_telyin_plugin_paths.py"
    helper_data = Path(__file__).with_name("native_plugin_paths.py").read_bytes()
    changes = []
    if helper.is_file():
        if helper.read_bytes().replace(b"\r\n", b"\n") != helper_data.replace(b"\r\n", b"\n"):
            raise RuntimeError("本 Bot 的 GsCore 路径修复模块与安装版本不符")
    else:
        changes.append((helper, None, helper_data))
    # Validate the entire installation before touching any file. A restart can
    # then finish an interrupted update where some entries are already patched.
    for name, original_hash in _PINS.items():
        path = core / name
        raw = path.read_bytes()
        normalized = raw.replace(b"\r\n", b"\n")
        digest = hashlib.sha256(normalized).hexdigest()
        if digest == _PATCHED[name]:
            continue
        source = normalized.decode("utf-8")
        if name == "sv.py":
            source = source.replace(_FIRST_FIX, _ORIGINAL, 1)
        if hashlib.sha256(source.encode()).hexdigest() != original_hash:
            raise RuntimeError(f"原生插件源码与锁定版本不符：{name}")
        result = _rewrite(name, source).encode()
        if hashlib.sha256(result).hexdigest() != _PATCHED[name]:
            raise RuntimeError(f"原生插件归属修复校验失败：{name}")
        if b"\r\n" in raw:
            result = result.replace(b"\n", b"\r\n")
        changes.append((path, raw, result))
    for path, before, result in changes:
        current = path.read_bytes() if path.exists() else None
        if current == result:
            continue
        if current != before:
            raise RuntimeError("GsCore 源码在检查期间已改变")
        _write(path, result)
