# 鸣潮服务（waves service）的出处与固定版本

本目录下的 `rover/` 与 `tools/update_plugins.py`、`pyproject.toml` 来自
[dsh-plugin-waves](https://github.com/Loping151/dsh-plugin-waves) 的 `service/`，
作者 Loping151，许可 **GPL-3.0**（见 `rover/LICENSE`）。

- 固定到 commit：`1f11a24c394f808d23dd410e99bb8ba2785084f9`（"适配 dsh 0.2.1-rc.2"）
- 取用日期：2026-10-22
- 作用：把上游 XutheringWavesUID 依赖的 `gsuid_core` 替换成独立的 FastAPI 服务
  （命令注册、Event/Bot、渲染、数据库、资源下载），使上游能在非 gsuid_core 环境运行

## 同步上游

本仓库不修改 `rover/` 的任何一行。上游更新时：

```sh
# 1. 拿一份新的
git clone --depth 1 https://github.com/Loping151/dsh-plugin-waves.git /tmp/dsh-waves
# 2. 覆盖
rm -rf service/rover && cp -r /tmp/dsh-waves/service/rover service/rover
find service/rover -name __pycache__ -type d -prune -exec rm -rf {} +
cp /tmp/dsh-waves/service/tools/update_plugins.py service/tools/
cp /tmp/dsh-waves/service/pyproject.toml service/
cp /tmp/dsh-waves/LICENSE service/rover/LICENSE
# 3. 改上面的 commit 号，跑一次 install.ps1 -SkipPluginConfig 自检
```

## 许可

`rover/` 是 GPL-3.0（不是 "or later"）。本仓库自己的代码是 GPL-3.0-or-later；
两部分一起分发时，**整体按 GPL-3.0 理解**，`rover/` 部分不额外授予 "or later"。
