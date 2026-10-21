# 服务端（waves service）说明

`service/` 是一个独立进程：上游 XutheringWavesUID + `rover` 兼容层，对外只有本地 HTTP。

## 一键装

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

默认装 HTML 卡片（浏览器能看）。要 PNG 卡片加 `-WithPlaywright`（多装 Chromium，约 150 MB）。

## 手动装（脚本失败时照这个排）

```powershell
cd <插件仓库>
uv venv --python 3.11 service\.venv
uv pip install --python service\.venv\Scripts\python.exe -r service\pyproject.toml
service\.venv\Scripts\python.exe service\tools\update_plugins.py
```

然后在插件配置里填：

- `python` = `<插件仓库>\service\.venv\Scripts\python.exe`
- `serviceDir` = `<插件仓库>\service`
- `autoStart` = `true`

## 目录

```
service/
  rover/            兼容层（第三方，GPL-3.0，勿改）
  tools/            拉取/升级上游的脚本
  plugins/          上游仓库（git clone 来的，不入库）
  data/             运行数据：config.json、数据库、素材、日志、渲染产物
  .venv/            独立虚拟环境
```

账号 token、绑定关系、抽卡记录都在 `service/data/`，不在插件目录里，也不会回显到页面。

## 常遇到的

| 现象 | 原因 / 处理 |
|---|---|
| `waves_status` 说没起来 | 先手工跑一次服务看日志：`service\.venv\Scripts\python.exe -m rover.main`，工作目录是 `service` |
| 端口被占 | 改插件 `port`，或找出占用者：`netstat -ano | findstr :9777` |
| 命令回“未匹配” | 命令词不对。`waves_help` 带 topic 查一次；带正则触发器的命令不在帮助里列出 |
| 命令回“未绑定特征码” | 先 `ww绑定<9位UID>` |
| 命令回“暂无有效 token” | 先 `ww登录`（库街区） |
| 卡片链接 500 / 打不开 | `mediaFormat` 设成 `html`；要 PNG 就装 Playwright 与 Chromium |
| 首次命令很慢 | 服务在后台下载游戏素材（数百 MB），日志里有进度；下完就正常 |
| 上游更新 | `service\.venv\Scripts\python.exe service\tools\update_plugins.py`，然后重载插件 |

## 许可

`rover/` 是 GPL-3.0，`tools/update_plugins.py` 同源；本仓库其余部分 GPL-3.0-or-later。
