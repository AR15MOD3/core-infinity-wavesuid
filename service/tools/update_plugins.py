#!/usr/bin/env python3
"""拉取/更新上游插件。

插件目录保持上游原样(带 .git), 更新就是 git pull。本地差异全在 rover/hooks.py,
不改插件源码, 所以更新不会冲突。
GitHub 不可达时自动改走 cnb.cool 镜像。
"""

import argparse
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Optional

PLUGINS_DIR = Path(__file__).resolve().parents[1] / "plugins"

REPOS = {
    "XutheringWavesUID": {
        "github": "https://github.com/Loping151/XutheringWavesUID.git",
        "cnb": "https://cnb.cool/gscore-mirror/XutheringWavesUID.git",
    },
    "RoverSign": {
        "github": "https://github.com/Loping151/RoverSign.git",
        "cnb": "https://cnb.cool/gscore-mirror/RoverSign.git",
    },
    "ScoreEcho": {
        "github": "https://github.com/Loping151/ScoreEcho.git",
        "cnb": "https://cnb.cool/gscore-mirror/ScoreEcho.git",
    },
}

# clone/pull 卡住太久也算不可达, 好切镜像
CLONE_TIMEOUT = 90
PULL_TIMEOUT = 60

_UNREACHABLE = (
    "could not resolve host",
    "failed to connect",
    "connection timed out",
    "operation timed out",
    "network is unreachable",
    "no route to host",
    "unable to access",
    "connection reset",
    "connection refused",
    "the remote end hung up unexpectedly",
    "early eof",
    "rpc failed",
    "gnutlsrecv error",
    "openssl ssl_connect",
    "ssl_connect",
    "tls connect error",
    "couldn't connect to server",
    "empty reply from server",
    "proxy connect aborted",
    "failed to connect to github.com",
)


def run(args, cwd=None, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        return subprocess.CompletedProcess(
            args, 124, out, (err + "\nconnection timed out").strip()
        )


def is_unreachable(result: subprocess.CompletedProcess) -> bool:
    if result.returncode == 0:
        return False
    text = f"{result.stderr} {result.stdout}".lower()
    return any(token in text for token in _UNREACHABLE)


class SyncError(RuntimeError):
    """A failed or unverified upstream operation; no successful install receipt."""


def checked(args, cwd=None, timeout=None):
    result = run(args, cwd=cwd, timeout=timeout)
    if result.returncode:
        raise SyncError("upstream command failed; output withheld")
    return result


def validate_tree(name, dest):
    if dest.is_symlink() or not (dest / ".git").is_dir():
        raise SyncError("upstream repository missing")
    package = dest / name / "__init__.py"
    if not package.is_file() or package.is_symlink():
        raise SyncError("upstream package missing")
    checked(["git", "rev-parse", "--verify", "HEAD"], cwd=dest, timeout=PULL_TIMEOUT)


def clone_with_fallback(name: str, dest: Path, github: str, cnb: str) -> str:
    if dest.exists() or dest.is_symlink():
        raise SyncError("clone destination already exists")
    for source, url in (("github", github), ("cnb", cnb)):
        # Each attempt owns a fresh sibling. Failures are preserved, never deleted.
        stage = dest.with_name(dest.name + ".clone-" + secrets.token_hex(16))
        result = run(["git", "clone", "--depth", "1", url, str(stage)], timeout=CLONE_TIMEOUT)
        if result.returncode:
            if source == "github" and is_unreachable(result):
                continue
            raise SyncError("upstream clone failed; staging retained")
        validate_tree(name, stage)
        if dest.exists() or dest.is_symlink():
            raise SyncError("clone destination changed; staging retained")
        stage.rename(dest)
        return f"{name}: 已克隆 ({source})"
    raise SyncError("upstream sources unavailable")


def _other_remote(origin: str, github: str, cnb: str):
    """当前 origin 不可达时该换去哪一边; 第三方 origin 不擅自改。"""
    if "github.com" in origin:
        return cnb, "cnb"
    if "cnb.cool" in origin:
        return github, "GitHub"
    return None, ""


def pull_with_fallback(name: str, dest: Path, github: str, cnb: str) -> Optional[str]:
    """origin 不可达时切到另一个源再拉一次(两边互为备份)。失败返回错误文案。"""
    result = run(["git", "pull", "--ff-only"], cwd=dest, timeout=PULL_TIMEOUT)
    if result.returncode == 0:
        return None
    if not is_unreachable(result):
        return f"{name}: 拉取失败 {result.stderr.strip()[:160]}"
    origin = run(["git", "remote", "get-url", "origin"], cwd=dest).stdout.strip()
    other, label = _other_remote(origin, github, cnb)
    if not other:
        return f"{name}: 拉取失败 {result.stderr.strip()[:160]}"
    print(f"{name}: 当前源不可达, origin 改为 {label}")
    setu = run(["git", "remote", "set-url", "origin", other], cwd=dest)
    if setu.returncode:
        return f"{name}: 无法改 origin {setu.stderr.strip()[:120]}"
    result = run(["git", "pull", "--ff-only"], cwd=dest, timeout=PULL_TIMEOUT)
    if result.returncode:
        return f"{name}: {label} 拉取也失败 {result.stderr.strip()[:160]}"
    return None


def sync_one(name: str, urls: dict) -> str:
    dest = PLUGINS_DIR / name
    if not dest.exists():
        return clone_with_fallback(name, dest, urls["github"], urls["cnb"])
    validate_tree(name, dest)
    dirty = checked(["git", "status", "--porcelain"], cwd=dest, timeout=PULL_TIMEOUT).stdout
    if any(not line.startswith("??") for line in dirty.splitlines()):
        raise SyncError("local changes present; upstream update not performed")
    before = checked(["git", "rev-parse", "--short", "HEAD"], cwd=dest, timeout=PULL_TIMEOUT).stdout.strip()
    error = pull_with_fallback(name, dest, urls["github"], urls["cnb"])
    if error:
        raise SyncError("upstream pull failed")
    validate_tree(name, dest)
    after = checked(["git", "rev-parse", "--short", "HEAD"], cwd=dest, timeout=PULL_TIMEOUT).stdout.strip()
    return f"{name}: 已是最新" if before == after else f"{name}: 已更新"


def check_compat() -> int:
    """更新后自检: 上游有没有用到本运行时还没提供的模块。"""
    for name in REPOS:
        validate_tree(name, PLUGINS_DIR / name)
    sys.path.insert(0, str(PLUGINS_DIR.parent))
    from rover import compat

    missing = compat.missing_imports(PLUGINS_DIR)
    if missing:
        print("\n运行时缺少这些上游模块, 需要在 rover/compat.py 补映射:")
        for name in missing:
            print(f"  {name}")
        return 1
    print("\n兼容层自检通过")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true", help="只做兼容层自检, 不拉取")
    args = parser.parse_args()

    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    (PLUGINS_DIR / "__init__.py").touch()

    try:
        if not args.check_only:
            for name, urls in REPOS.items():
                print(sync_one(name, urls))
        code = check_compat()
    except (SyncError, OSError):
        print("Upstream update incomplete; retained staging requires review.", file=sys.stderr)
        code = 1
    if code == 0 and not args.check_only:
        print("Upstream update verified; review before restarting service.")
    sys.exit(code)


if __name__ == "__main__":
    main()
