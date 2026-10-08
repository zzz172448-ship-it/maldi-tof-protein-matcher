#!/usr/bin/env sh
# 基于质谱峰的蛋白质匹配软件 V1.0
# Copyright (c) 2026 张葛阳
# 本软件为独立开发，未使用第三方开源代码
# ============================================================
#  基于质谱峰的蛋白质匹配软件 - Linux / macOS 启动器（薄壳）
#  全部逻辑在 run.py 中；本脚本只负责调用 run.py：
#    1. 首次运行时创建工程内 .venv 虚拟环境并安装 requirements.txt
#    2. 用 .venv 解释器调用 run.py
#  用法： ./run.sh [--port 8000] [--no-browser]
# ============================================================
set -e
cd "$(dirname "$0")"

PYEXE=".venv/bin/python"

if [ ! -x "$PYEXE" ]; then
    echo "[run] Virtual environment not found, creating .venv ..."
    if command -v python3 >/dev/null 2>&1; then
        python3 -m venv .venv
    elif command -v python >/dev/null 2>&1; then
        python -m venv .venv
    else
        echo "[run] Python 3.9+ not found. Please install Python first." >&2
        exit 1
    fi
    "$PYEXE" -m pip install --upgrade pip --disable-pip-version-check
    "$PYEXE" -m pip install -r requirements.txt
fi

exec "$PYEXE" run.py "$@"
