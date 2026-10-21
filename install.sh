#!/usr/bin/env bash
# Install the waves service for core-infinity-wavesuid (Linux / macOS).
#
#   ./install.sh                 # HTML cards, text
#   ./install.sh --playwright    # also PNG cards (adds Chromium, ~150 MB)
#   ./install.sh --no-config     # don't write the plugin config.json
#
# It never touches your game account and never starts anything in the background.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE="$ROOT/service"
VENV="$SERVICE/.venv"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"
PLAYWRIGHT=0
WRITE_CONFIG=1

for arg in "$@"; do
  case "$arg" in
    --playwright) PLAYWRIGHT=1 ;;
    --no-config) WRITE_CONFIG=0 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "未知参数：$arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
fail() { printf '\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

[ -f "$SERVICE/rover/main.py" ] || fail "找不到 $SERVICE/rover/main.py —— 请在插件仓库根目录运行。"

# ---- 1. uv + git ------------------------------------------------------------
step "检查 uv 与 git"
if ! command -v uv >/dev/null 2>&1; then
  echo "没有 uv，尝试安装（官方脚本）..."
  curl -LsSf https://astral.sh/uv/install.sh | sh || fail "uv 安装失败，请手工装：curl -LsSf https://astral.sh/uv/install.sh | sh"
  export PATH="$HOME/.local/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || fail "uv 仍然不可用，请重开终端再试。"
command -v git >/dev/null 2>&1 || fail "缺少 git，请先安装（apt/dnf/brew install git）。"
uv --version

# ---- 2. venv ----------------------------------------------------------------
step "创建虚拟环境（Python $PYTHON_VERSION）"
if [ ! -x "$VENV/bin/python" ]; then
  uv venv --python "$PYTHON_VERSION" "$VENV" || fail "虚拟环境创建失败。可试：uv python install $PYTHON_VERSION"
fi
"$VENV/bin/python" -c 'import sys; print("python", sys.version.split()[0])'

# ---- 3. dependencies --------------------------------------------------------
step "安装服务依赖（第一次要下载几百 MB，耐心等）"
uv pip install --python "$VENV/bin/python" -r "$SERVICE/pyproject.toml" || fail "依赖安装失败，见上面的输出。"

# ---- 4. upstream plugins ----------------------------------------------------
step "拉取上游插件（XutheringWavesUID / RoverSign / ScoreEcho）"
"$VENV/bin/python" "$SERVICE/tools/update_plugins.py" || fail "上游插件拉取失败（网络或 GitHub 限流）。稍后重跑即可续上。"

# ---- 5. optional playwright -------------------------------------------------
if [ "$PLAYWRIGHT" = "1" ]; then
  step "安装 Playwright 与 Chromium（只有 PNG 卡片需要）"
  uv pip install --python "$VENV/bin/python" playwright || true
  "$VENV/bin/python" -m playwright install chromium || echo "Playwright 安装失败；不影响文字与 HTML 卡片。"
fi

# ---- 6. plugin config -------------------------------------------------------
if [ "$WRITE_CONFIG" = "1" ]; then
  CONFIG="$ROOT/config.json"
  if [ -f "$CONFIG" ]; then
    step "已有 config.json，未改动"
  else
    step "写入插件 config.json"
    "$VENV/bin/python" - "$ROOT" "$VENV/bin/python" "$SERVICE" <<'PY'
import json, pathlib, sys
root, python, service = sys.argv[1], sys.argv[2], sys.argv[3]
base = json.loads((pathlib.Path(root) / "config.example.json").read_text(encoding="utf-8"))
base.update({"python": python, "serviceDir": service, "autoStart": True})
(pathlib.Path(root) / "config.json").write_text(
    json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("  已写入 config.json（autoStart = true）")
PY
  fi
fi

step "完成"
cat <<EOF
插件配置（Config 页面里也能看到）：
  python      = $VENV/bin/python
  serviceDir  = $SERVICE
  autoStart   = true
  mediaFormat = html    （要 PNG 卡片就加 --playwright 再改成 png）

下一步：在 TelyinCore 的插件页启用「鸣潮查询」，调一次 waves_status 看是否在线。
卸载：停用插件 → 删掉 $SERVICE 整个目录即可（账号数据在 service/data 里）。
EOF
