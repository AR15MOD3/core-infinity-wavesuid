# core-infinity-wavesuid 0.3.2 · 插件自带账号管理页

TelyinCore Infinity API 1 的非官方鸣潮查询桥接，使用 XutheringWavesUID / dsh-plugin-waves 服务兼容层。
不代表上游作者背书；桥接代码 GPL-3.0-or-later，原服务兼容层 GPL-3.0，合并分发按 GPL-3.0，见 NOTICE.md 与 service/UPSTREAM.md。

## 数据流

`waves_command` → 当前 Bot 的独立本机服务 → 文本与 PNG 卡片链接 → 官方 QQ 插件 `qq_send_image`。
本插件不直接发送 QQ，不提供生图，不复制其他 Bot 账号、人设、聊天或记忆。
真实绑定、登录、签到、导入等命令会改变账号状态，必须先取得用户同意。

## 0.3.1 必须明确的边界

- 需要正式 Core 插件作用域宿主，提供 `bot_id`、`pluginSettingsBot`、`pluginConfigPath`、`pluginDataDir`；不再依赖 QQ 路径。这些字段必须由实际宿主提供；不能由插件自行伪造。
- 私有配置定位到 `bots/<bot_id>/infinity/plugins/core-infinity-wavesuid/config.json`，绝不读取插件目录下的私人 config.json，也不回退到 Telyin。
- `ownerBot` 必须与宿主真实 bot_id 一致；显示名不能代替身份。运行数据 `dataDir` 必须在该插件的 `runtime` 资源目录之下。
- 服务代码使用独立的版本化目录，数据目录不在插件包/服务代码树内。不能共用已有 9777 服务，也不能复制旧 Bot 数据库。
- 必须 autoStart=true，明确 python/serviceDir/port/dataDir；只管理本插件创建的进程。手工服务模式不再自动接管。
- 数据目录首次必须为空；独占 OS 文件锁及 owner 标记避免跨 Bot/并发实例误用。
- 启动实例具有随机令牌，健康信息核对 scope/实例哈希与自有进程树。命令认证阻止端口替换时误投；不重定向、不走环境代理、不自动重试命令。
- Windows 用 suspended spawn + Job Object 管理 venv 启动器及其后代。POSIX 为独立进程组，尚未在 Linux/macOS 实机验收。
- 保留 Core 的不可逆生命周期 guard。受限宿主不能凭插件自行恢复/伪造权限，失败应交由原宿主处理。
- 本机 Python 插件与同用户进程是可信执行环境，以上误用防护不是恶意代码沙箱。

## 安装与升级

需要已安装 Python、uv、git；脚本不自动下载安装 uv，也不启动 Bot。安装依赖与上游文件可能很大，必须明确传下载开关。
先用纯计划模式核对路径（示例端口不是端口占用证明；实际启用前再核验）：

```powershell
.\install.ps1 -ServiceDir D:\TelyinCore\waves-service-0.3.1 -OwnerBot IRIS `
  -ConfigPath D:\TelyinCore\bots\IRIS\infinity\plugins\core-infinity-wavesuid\config.json `
  -DataDir D:\TelyinCore\bots\IRIS\infinity\plugins\core-infinity-wavesuid\runtime\data -Port 19778 -Plan
```

确需安装时，将 `-Plan` 换为 `-AllowDownloads -WithPlaywright`。PNG 需要 Playwright 与 Chromium。
Linux/macOS 对应参数为 `--service-dir --owner --config-path --data-dir --port --plan`，安装用 `--allow-downloads --playwright`；该分支尚未实机验证。

安装器向新版本目录复制全部服务适配文件（包括 scoped_main.py / scope_identity.py），不会覆盖不同内容的旧目录。
升级应选择新的服务版本目录，先停用自己拥有的旧插件，再人工审核独立配置中的 serviceDir/python 后切换；不自动终止其他进程。
已有独立 config.json **逐字节保留**，包括旧 serviceDir 与未知字段。脚本不会偷偷替你切到新版本；它成功完成只表示安装，不代表已经运行新版本。
市场替换插件包不会覆盖 Bot 目录下的配置和数据；缺配置或格式坏时会拒绝启用，不使用共享配置兜底。
`tools/provision.py` 可独立做不下载、不启动的计划与复制检查。计划模式不会写入目录。

## 配置与工具

参见 config.example.json / config.schema.json。ownerBot、dataDir、serviceDir、python、专属 port 与 autoStart=true 缺一不可。
现有基础字段 host、scene、commandTimeout、mediaFormat 保留。运行期间配置被冻结，修改后须正规停用/启用。

- `waves_status`：当前自有服务的实际身份与状态；不是只看任意 health=200。
- `waves_help`：命令目录与主题筛选，可精简目录回执。
- `waves_task_status`：读取原任务的后续回执，不重新执行命令。
- `waves_command(command,user,images)`：转发命令并给出文本与卡片 PNG 链接。

官方 QQ 插件负责目标白名单、图片来源限制和实际协议回执；没有 message_id 不能称发送成功，不明发送结果不自动重试。
退出/卸载前先正规停用插件，保留独立 runtime 账号资料。不要把删除服务代码目录等同于删除账号数据，也不要清理不属于自己的进程。

## 验证状态

本候选已做合成数据隔离、FastAPI 认证 middleware、官方 QQ 工具 + Mock OneBot 发送链路与配置保留测试。
**离线测试不等于完整账号功能、生产热加载或真实 QQ 图片送达验收。** 具体部署与真实验收以对应 Bot 的最新报告和回执为准。

## 0.3.1 正式宿主作用域

此 revision 声明 `telyincore.botScope=1`（仅为实现兼容版本标记，不是宿主权限）。正规 scoped loader 对旧未适配的官方 QQ/视觉/鸣潮安装包拒绝启动，不能只更新 Core 却继续运行旧共享资源插件。须协调全部共享安装包后再重启；不自动热替换正在运行的特琳插件。
需要 Core 提供 pluginSettingsBot / pluginConfigPath / pluginDataDir，与 bot_id 一致；不再从 qq_config_path 推导，不兼容旧共享宿主回退。配置保存在每个 Bot 的 infinity/plugins/core-infinity-wavesuid/config.json；dataDir 必须在对应 runtime 子目录内。旧 0.3.0 配置不自动复制（其中可能带账号数据路径）；请通过正规配置入口审核新配置并选择全新合成/独立数据根，真实账号迁移另行授权。版本健康信息必须与桥接器0.3.1匹配，否则不发布工具。

## 帮助与回执边界

waves_help 返回命令资料及可用卡片地址，不直接发送消息、不固定角色台词。需要图片时由当前平台的正式图片工具发送；有卡片URL不等于已经渲染或发送成功。长摘要优先保留卡片链接，截断会明确提示。工具失败不回传原始HTTP错误正文或异常文本，防止私有内容进入模型。

## 更新、兼容与测试

见 [0.3.1 更新说明](docs/RELEASE-0.3.1.md) 与 [服务说明](docs/SERVICE.md)。
0.3.2 已包含 config.html 与 account_admin.py，通过 pageBackend v1 通用宿主接口提供账号管理。
需要支持该接口的 Core；旧 Core 可能仍展示其历史原生页面，不能据此判断插件自带页面已验收。
见 [页面接入与分发](docs/PAGE.md)。
离线测试入口：`python -B -m unittest discover -s tests -v`（标准库，无真实登录或 QQ 外发）。

页面脚本模拟测试：`node tests/test_page.mjs`（标准库，不进行网络请求）。
包版本 0.3.2；服务/桥接协议仍为 0.3.1，两者作用不同，不需为页面更新替换服务数据库。
