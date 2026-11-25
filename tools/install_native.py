"""Install the pinned native backend in one Bot's Waves runtime (Python 3.11+)."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path
from urllib.request import urlopen

CORE = ("Genshin-bots/gsuid_core", "96150e640e89c441b8c8c7953b47ce3b4a4f8f36")
WAVES = ("Loping151/XutheringWavesUID", "def9c3de4744948b815aa8caa5d6627aedbf8ebe")


def install_source(destination, source):
    repo, commit = source
    marker = destination / ".telyin-upstream"
    if destination.exists():
        if marker.is_file() and marker.read_text().strip() == commit:
            return
        raise ValueError(f"现有目录不属于本版本，请保留并核对 {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        archive = Path(temp) / "source.tgz"
        with urlopen(f"https://codeload.github.com/{repo}/tar.gz/{commit}", timeout=120) as response:
            with archive.open("wb") as output:
                shutil.copyfileobj(response, output)
        with tarfile.open(archive) as stream:
            stream.extractall(temp, filter="data")
        checkout = Path(temp) / (repo.split("/")[-1] + "-" + commit)
        (checkout / marker.name).write_text(commit + "\n")
        checkout.rename(destination)


def install_runtime(home, uv, progress=print):
    # Keep an existing interpreter and its compiled Waves resources on repair.
    venv = home / ".venv/pyvenv.cfg"
    python_version = "3.11" if os.name == "nt" else "3.12"
    if venv.is_file():
        for line in venv.read_text(encoding="utf8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() in ("version", "version_info"):
                python_version = ".".join(value.strip().split(".")[:2])
                break
    if python_version == f"{sys.version_info.major}.{sys.version_info.minor}":
        python_version = sys.executable
    # uv may inherit another project's environment from the Core launcher.
    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(home / ".venv"))
    progress("下载 GsCore")
    install_source(home, CORE)
    plugin = home / "gsuid_core/plugins/XutheringWavesUID"
    progress("下载 XutheringWavesUID")
    install_source(plugin, WAVES)
    progress("安装 Python 与 GsCore 依赖")
    subprocess.run([uv, "sync", "--python", python_version, "--frozen", "--no-dev"], cwd=home, check=True, env=env)
    python = home / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    deps = tomllib.loads((plugin / "pyproject.toml").read_text(encoding="utf8"))["project"]["dependencies"]
    progress("安装鸣潮依赖")
    subprocess.run([uv, "pip", "install", "--python", str(python), *deps], cwd=home, check=True)
    progress("安装 Chromium")
    subprocess.run([str(python), "-m", "playwright", "install", "chromium", "--only-shell"],
                   cwd=home, check=True, env=dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(home / "browsers")))


def install(config, port, uv):
    config = config.absolute()
    scope = config.parent
    if (config.name != "config.json" or scope.name != "core-infinity-wavesuid"
            or scope.parent.name != "plugins" or scope.parent.parent.name != "infinity"
            or scope.parents[3].name != "bots" or config.resolve() != config):
        raise ValueError("需要 bots/<Bot>/infinity/plugins/core-infinity-wavesuid/config.json")
    if not 1024 <= port <= 65535:
        raise ValueError("端口必须在 1024 至 65535 之间")
    owner = scope.parents[2].name
    home = scope / "runtime/gscore"
    if home.resolve() != home:
        raise ValueError("原生环境须属于本 Bot")
    before = config.read_bytes() if config.exists() else None
    settings = json.loads(before.decode("utf-8-sig")) if before else {"ownerBot": owner}
    if settings.get("ownerBot") != owner:
        raise ValueError("配置与 Bot 不匹配")
    install_runtime(home, uv)
    settings.update(backend="gscore", nativePort=port)
    if (config.read_bytes() if config.exists() else None) != before:
        raise RuntimeError("安装期间配置已改变，已保留新配置")
    with tempfile.NamedTemporaryFile(dir=scope, mode="w", encoding="utf8", delete=False) as tmp:
        json.dump(settings, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
    os.replace(tmp.name, config)
    print(f"{owner} 原生鸣潮已安装在 {home}，端口 {port}。启用插件后受理 QQ 群命令。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--uv", default="uv")
    args = parser.parse_args()
    install(args.config, args.port, args.uv)
