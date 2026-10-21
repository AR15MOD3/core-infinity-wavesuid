# 设计：从 XutheringWavesUID 到 TelyinCore

依据：TelyinCore《Infinity 插件开发规范 v1.2》与两份源码的实测统计（2026-10-22）。

## 1. 为什么是"桥接"，不是"重写"

| 事实 | 数字 |
|---|---|
| 上游代码量 | 254 个 `.py`，69,516 行，30 个功能模块 |
| 命令面 | 86 个 `SV(...)`，106 个触发器（21 command / 43 fullmatch / 38 regex / 3 prefix / 1 file） |
| 框架耦合 | 185 个文件、502 条 `import gsuid_core` |
| 运行时依赖 | SQLAlchemy、APScheduler、FastAPI、Playwright、OpenCV… |
| 现有资产 | 同作者已把 gsuid_core 替换成 `rover`（约 11.7k 行），对外是本地 FastAPI |
| 素材 | 游戏数据从 cnb.cool 镜像下载，不在仓库里 |

把 502 条 import 逐个改成 TelyinCore 的原生工具，等于重写一个产品；而 `rover` 已经把"去 gsuid_core"
这件事做完。桥接层因此只需要承担**协议转换**：命令 ↔ 工具、segments ↔ 回执。

## 2. 边界

```
TelyinCore 进程                     waves service 进程（独立 venv）
┌────────────────────────┐         ┌──────────────────────────────┐
│ extensions/            │  HTTP   │ rover（兼容层，GPL）          │
│  core-infinity-wavesuid│ ──────▶ │  └─ plugins/XutheringWavesUID │
│   index.py（本仓库）    │ ◀────── │     （上游，保留 .git）        │
└────────────────────────┘         └──────────────────────────────┘
```

- 桥接层不 import 上游，不读上游的数据文件；只认 HTTP。
- 服务只监听回环地址；账号 token 存在服务自己的目录里。
- 许可边界：本仓库 GPL-3.0-or-later；TelyinCore 本体许可不传染到插件。

## 3. 三个卡点与当前处置

| 卡点 | 现状 | 处置 |
|---|---|---|
| **出图无路** | core 的 QQ 插件只有 `qq_send_saved_image(media_id)`，而 `media_id` 只能来自"收到过的图"；Web 聊天是纯文本，不渲染图片 | 回执里给 PNG/HTML 链接（`mediaFormat` 可切）；真要发到 QQ 需内核加"出站媒体"能力，见 §5 |
| **谁在问** | 工具 handler 只收参数（`dispatch.py`），没有会话上下文 | v0.1 用服务端默认使用者；`user` 参数留给部署方 / 后续接 `active_events` 的 `sender_key` |
| **依赖与数据** | core 的 venv 没有 FastAPI/SQLAlchemy 等；首次要下载数百 MB 素材 | 独立 venv + 子进程；`autoStart` 显式开关；失败写日志并如实报 |

## 4. 分期

| 期 | 内容 | 验收 |
|---|---|---|
| P0 | 服务端安装脚本（`install.ps1` / `install.sh`，vendor `rover` + 拉上游） | `/api/health` 返回 384 条命令；全新目录可装 ✅ |
| P1 | 插件骨架：清单、配置、启停 | 停用后端口释放；连重载不双开 ✅ |
| P2 | 命令总线 + 帮助 + 状态 | 角色/深渊/抽卡/体力/帮助 smoke ✅（账号类需绑定） |
| P3 | 出图 → QQ（叠加插件，不改 core/QQ） | 卡片 739×2048 / 1.27 MB 进 QQ；名单外被挡 ✅（见 §5） |
| P4 | 账号类（绑定 / 签到 / 抽卡导入） | token 不进日志与页面；失败区分"没登录/过期/网络" ✗ |

## 5. 出图：做成了叠加插件（2026-10-22 定）

约束是：**不动 core、不改 `core-infinity-qq`、能力放在插件里**。所以既不是 A1（内核加能力），
也不是 A2（改官方 QQ 插件），而是 A4 —— 一个独立叠加插件
[`core-infinity-waves-qq-image`](https://github.com/AR15MOD3/core-infinity-waves-qq-image)：

- 从 waves 服务取 `/api/media/<卡片>/png`；
- 按 core 的 `image_policy` 压到长边 2048 / 6 MiB（超过 1600 万像素的长卡片先按比例缩一次）；
- 交给运行中的 QQ 插件实例已有的 `_send_segments`，于是允许名单、撤回、当前会话三道闸照旧生效。

代价：它依赖 core 的私有注册表 `_active_items()`（今天没有公开的"跨插件发图"宿主能力）。
只在调用那一刻解析，热加载/重载不受影响；找不到 QQ 实例时明确回"没找到"。

实测（`ww帮助` 卡片）：原图 2570×7118 / 5.2 MB → 缩放后 739×2048 / 1.27 MB PNG；
允许名单外的目标被 QQ 自己挡下，名单内通过校验后才进入发送。

## 6. 规范对照（§12 检查单）

- 入口/清单/前缀：`package.json` + `index.py`，工具前缀 `waves_`。
- 停用可收回：`stop()` 只杀自己起的子进程；外部启动的服务不动。
- 失败不伪装：服务不可达时回执明确说不可用，并指向 `waves_status`。
- 无默认密钥：token 不在插件目录，配置页不回显。
- 非官方：README / NOTICE 明确声明。
