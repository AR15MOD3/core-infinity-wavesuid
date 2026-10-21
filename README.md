# core-infinity-wavesuid

TelyinCore 的鸣潮（Wuthering Waves）查询插件：把 [XutheringWavesUID](https://github.com/Loping151/XutheringWavesUID)
的能力接进 [TelyinCore](https://github.com/AR15MOD3/TelyinCore) 的 Infinity 插件体系。

> 非官方移植。不是 Telyin 的官方内置插件，也不代表上游作者对本项目背书。

- 版本：0.1.0（桥接骨架）
- 适用：TelyinCore Infinity API 1
- 许可：GPL-3.0-or-later（上游是 GPL-3.0-or-later，桥接层与之一致，见 [LICENSE](LICENSE)）

## 它怎么工作

```
模型 ──waves_command("ww椿面板")──▶ index.py ──HTTP 127.0.0.1:9777──▶ waves service
                                        ◀── summary / segments ───────┘
模型 ◀── 回执：正文 + 卡片文字 + 图片链接
```

- **桥接层**（本仓库）：一个 TelyinCore 插件，只做三件事——拉起服务、转发命令、把结果折成回执。
  它只用标准库，不 import 上游代码。
- **waves service**（独立进程、独立虚拟环境）：上游 XutheringWavesUID + 一层把 `gsuid_core`
  换成 `rover` 的兼容层，对外是本地 FastAPI（`/api/chat`、`/api/commands`、`/api/health`）。

这样切开的理由：上游要 SQLAlchemy / FastAPI / Playwright 这一整套，塞进 TelyinCore 的进程会污染它的
依赖树；分进程后服务挂了只影响这一个插件，许可边界也干净。

## 现在的状态

- ✅ 插件清单、配置表单、`waves_command` / `waves_help` / `waves_status` 三个工具
- ✅ 服务随插件启停（`autoStart`），停用会收掉子进程
- ⏳ 服务端安装脚本（把上游 + rover 装到独立 venv）还没进本仓库——见「准备服务」
- ⏳ 图片发到 QQ：TelyinCore 目前没有"插件产图 → 会话"的通道，回执里给的是可点的本地链接
- ⏳ 账号类命令（绑定 / 签到 / 抽卡导入）尚未逐条验收

## 安装

1. 把本仓库放进 TelyinCore 的插件目录：

   ```text
   <TelyinCore>/extensions/core-infinity-wavesuid/
   ```

2. 准备 waves service（二选一）：
   - **借现成的**：把 [dsh-plugin-waves](https://github.com/Loping151/dsh-plugin-waves) 的 `service/`
     目录克隆下来，按它的 README 建好虚拟环境、拉上游插件；
   - **等本仓库的安装脚本**（P0 阶段补上，会把 XutheringWavesUID / RoverSign / ScoreEcho
     拉到 `service/plugins/` 并装依赖）。

3. 复制配置并填写：

   ```sh
   cp config.example.json config.json
   ```

4. 打开插件配置页，至少填这两项：
   - `python`：waves-service 虚拟环境里的解释器绝对路径（如 `D:\TelyinCore\waves-service\.venv\Scripts\python.exe`）
   - `serviceDir`：包含 `rover/main.py` 的目录
   - 想让插件自己起服务，就把 `autoStart` 打开

5. 启用插件。模型那边会多出 `waves_command` / `waves_help` / `waves_status` 三个工具。

## 配置

| 字段 | 说明 | 默认 |
|---|---|---|
| `host` | 服务监听地址，只允许本机 | `127.0.0.1` |
| `port` | 服务端口 | `9777` |
| `autoStart` | 由插件拉起服务，停用时一并结束 | `false` |
| `python` | 服务用解释器；留空用 TelyinCore 自己的（通常没有服务依赖） | 空 |
| `serviceDir` | 包含 `rover/main.py` 的目录；留空用插件目录下 `service/` | 空 |
| `mediaFormat` | 卡片链接给 `png` 还是 `html`；png 需要服务端装好 Playwright + Chromium | `png` |
| `scene` | `group` / `direct`，少数命令需要 direct | `group` |
| `commandTimeout` | 单条命令超时（秒） | `90` |

## 工具

| 工具 | 说明 |
|---|---|
| `waves_command` | 执行一条鸣潮命令，回执含正文、卡片文字与链接。绑定/签到/导入会真实改变账号状态，调用前应先与用户确认 |
| `waves_help` | 查命令词，可按关键词过滤 |
| `waves_status` | 服务是否在线、命令条数、是否由本插件启动 |

为什么不把 86 条命令拆成 86 个工具：上游接口本身就是命令式的（前缀 + 别名 + 模糊匹配），
拆开等于重写一遍解析逻辑，还会把工具表撑大、增加模型选错的机会。一个命令总线能跟着上游升级。

## 已知限制

- **图片到不了 QQ**：TelyinCore 的 QQ 通道目前只支持回发"收到过的图"。回执里给的是
  `http://127.0.0.1:9777/api/media/...` 链接，浏览器能打开；要真发图需要内核增加出站媒体能力（见 `docs/DESIGN.md`）。
- **服务要自己装**：首次运行要拉上游仓库、装 30 多个依赖、下载游戏素材（数百 MB）。仓库里不塞这些东西。
- **账号类命令**需要库街区 token；token 由服务存在它自己的目录里，不进插件目录、不回显。
- **不做安全隔离**：插件与上游都在本机以当前用户身份运行，启用前请自行确认来源（规范 §1 同款提醒）。

## 许可与署名

- 本仓库（桥接层）以 **GPL-3.0-or-later** 发布。
- 运行时依赖的 [XutheringWavesUID](https://github.com/Loping151/XutheringWavesUID) 同样为
  GPL-3.0-or-later，作者 tyql688 / Loping151；`rover` 兼容层来自
  [dsh-plugin-waves](https://github.com/Loping151/dsh-plugin-waves)。
- 详细来源与改动说明见 [NOTICE.md](NOTICE.md)。
- TelyinCore 本体另有其许可；本插件是独立附属插件，不包含 TelyinCore 的官方代码。
