# 来源与署名

## 本项目（桥接层）

`package.json` / `index.py` / `config.*` / `README.md` / `docs/` 为原创代码，
作者 AR15MOD3，以 GPL-3.0-or-later 发布。

它通过本机 HTTP（默认 `127.0.0.1:9777`）调用一个独立进程，不 import、不复制上游代码。

## 运行时依赖的上游

### XutheringWavesUID

- 仓库：https://github.com/Loping151/XutheringWavesUID
- 原作者：tyql688；构建与维护：Loping151
- 许可：GPL-3.0-or-later
- 本项目对它的使用方式：作为独立服务运行，未修改其源码

### rover 兼容层

- 来源：https://github.com/Loping151/dsh-plugin-waves 的 `service/rover/`
- 许可：GPL-3.0-or-later
- 作用：把上游依赖的 `gsuid_core`（命令注册、Event/Bot、渲染、数据库、资源下载）替换成独立的
  FastAPI 服务，使上游能在非 gsuid_core 环境里运行

### 其他

- RoverSign（签到）、ScoreEcho（声骸评分）：Loping151，GPL-3.0，作为上游扩展由服务侧按需拉取
- TelyinCore：https://github.com/AR15MOD3/TelyinCore ，其本体与官方内置插件适用
  `LicenseRef-TelyinCore-NonCommercial-1.0`；本仓库不包含其代码

## 声明

- 本项目是非官方移植，不代表 Telyin、tyql688 或 Loping151 背书。
- 若将来把 `rover` 或上游代码 vendored 进本仓库，相关目录会保留其原始许可与版权声明，
  并继续以 GPL-3.0-or-later 分发。
