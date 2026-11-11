#!/usr/bin/env bash
# 天枢 fork 自用构建：打免签名 dir 包，产物仅本机使用。
# 前置：uv 已装 Python 3.12，pnpm 已装依赖。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "== 校验 fork 版本 =="
node -e 'const p=require("./package.json");console.log("version:",p.version,"build:",p.release.build_number)'

echo "== 构建 core (PyInstaller) =="
# 若本机 stock CPython 不广告 dir_fd，access.py 的运行时探测会兜底；
# 用 python.org 3.12 构建 则静态集合本身就为真。
uv run --directory apps/core --python 3.12 pyinstaller --noconfirm one-memory.spec

echo "== 构建 web =="
pnpm --filter @one-memory/web build

echo "== 构建 desktop =="
pnpm --filter @one-memory/desktop build

echo "== 打 dir 包（免签名）=="
bash scripts/run-desktop-package-mac-dir.sh

echo
echo "== 产物 =="
ls -d apps/desktop/dist/*.app 2>/dev/null || echo "(见 scripts/run-desktop-package-mac-dir.sh 输出路径)"
echo
echo "替换 managed install 见 scripts/tianshu/README.md"
