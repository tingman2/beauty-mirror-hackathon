#!/bin/bash
# 「妆镜」Web 原型启动器（Mac）
# 双击本文件：自动找 Python 3.10+ → 建/复用根目录 .venv → 装依赖 → 起服务并打开浏览器。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PRODUCTS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$PRODUCTS_DIR/.." && pwd)"
VENV="$ROOT_DIR/.venv"
PORT="${BM_WEB_PORT:-8765}"

find_python() {
  local cand maj min
  for cand in \
      "$HOME/.local/bin/python3.12" "$HOME/.local/bin/python3.11" "$HOME/.local/bin/python3.10" \
      "/opt/homebrew/bin/python3.12" "/opt/homebrew/bin/python3.11" "/opt/homebrew/bin/python3" \
      "/usr/local/bin/python3.12" "/usr/local/bin/python3.11" "/usr/local/bin/python3" \
      python3.12 python3.11 python3.10 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
      maj=$("$cand" -c 'import sys; print(sys.version_info.major)' 2>/dev/null || echo 0)
      min=$("$cand" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo 0)
      if [ "$maj" -gt 3 ] || { [ "$maj" -eq 3 ] && [ "$min" -ge 10 ]; }; then
        echo "$cand"; return 0
      fi
    fi
  done
  return 1
}

echo "==> 检查 Python ..."
PY="$(find_python)"
if [ -z "$PY" ]; then
  echo "❌ 未找到 Python 3.10+。请先安装：https://www.python.org/downloads/"
  read -r -p "按回车关闭 ..."; exit 1
fi
echo "✅ $("$PY" --version)"

if [ ! -x "$VENV/bin/python" ]; then
  echo "==> 首次初始化虚拟环境 ..."
  "$PY" -m venv "$VENV"
fi

echo "==> 安装/校验依赖 ..."
"$VENV/bin/python" -m pip install --upgrade pip -q
"$VENV/bin/python" -m pip install -q -r "$ROOT_DIR/requirements.txt" -r "$SCRIPT_DIR/requirements.txt"

if [ ! -f "$SCRIPT_DIR/.env" ]; then
  cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
  echo "==> 已生成 .env（可在里面填视觉段密钥；不填也能离线演示）"
fi

echo "==> 启动妆镜 Web（端口 $PORT）..."
cd "$PRODUCTS_DIR"
exec "$VENV/bin/python" -m beauty_mirror.web.server --open --port "$PORT"
