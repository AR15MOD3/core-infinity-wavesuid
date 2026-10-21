# 来源与署名

## 本项目（桥接层）

`index.py` / `install.ps1` / `install.sh` / `config.*` / `README.md` / `docs/` 为原创代码，
作者 AR15MOD3，以 **GPL-3.0-or-later** 发布。

它通过本机 HTTP（默认 `127.0.0.1:9777`）调用一个独立进程，不 import 上游业务代码。

## 仓库内附带的第三方代码

### `service/rover/` 与 `service/tools/`

- 来源：https://github.com/Loping151/dsh-plugin-waves 的 `service/rover/`、`service/tools/update_plugins.py`
- 作者：Loping151
- 许可：**GPL-3.0**（见 `service/rover/LICENSE`；注意是 3.0，不是 "or later"）
- 固定版本：commit `1f11a24c394f808d23dd410e99bb8ba2785084f9`
- 作用：把上游依赖的 `gsuid_core`（命令注册、Event/Bot、渲染、数据库、资源下载）替换成独立的
  FastAPI 服务，使上游能在非 gsuid_core 环境里运行
- 本仓库未修改其代码；同步方法见 [`service/UPSTREAM.md`](service/UPSTREAM.md)

因为这一部分是 "GPL-3.0"（不含 or later），**整仓库一起分发时按 GPL-3.0 理解**；
只有 `rover/` 之外、本仓库原创的部分才带 "or later"。

## 运行时拉取的上游（不在本仓库内）

### XutheringWavesUID

- 仓库：https://github.com/Loping151/XutheringWavesUID
- 原作者：tyql688；构建与维护：Loping151
- 许可：GPL-3.0-or-later

### RoverSign（签到）、ScoreEcho（声骸评分）

- 仓库：https://github.com/Loping151/RoverSign 、https://github.com/Loping151/ScoreEcho
- 作者：Loping151，GPL-3.0

### TelyinCore

- 仓库：https://github.com/AR15MOD3/TelyinCore
- 本体与官方内置插件适用 `LicenseRef-TelyinCore-NonCommercial-1.0`；本仓库不包含其代码

## 声明

- 本项目是非官方移植，不代表 Telyin、tyql688 或 Loping151 背书。
- 随仓库分发的第三方代码保留其原始许可与版权声明。
