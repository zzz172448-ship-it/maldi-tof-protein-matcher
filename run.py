#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""蛋白质数据处理系统 - 纯 Python 启动入口（跨平台）。

用法：
    python run.py                 # 自动选择空闲端口，并自动打开浏览器
    python run.py --port 8000     # 指定 Web 端口
    python run.py --no-browser    # 不自动打开浏览器
    python run.py --check         # 仅做环境自检（不安装依赖、不启动服务）

启动流程：
    1. 检查 Python 版本（要求 >= 3.9）
    2. 检查依赖，缺失时只安装缺失的包（缺哪个补哪个，不动其它已装好的包）
    3. 创建 runtime 运行时目录（备份 / 输出 / 日志 / 临时 / 会话）
    4. 启动本地 Web 服务（web_app.main）

Windows 用户可直接双击 run.bat，Linux / macOS 用户执行 ./run.sh，
两个脚本都只是薄壳，最终都调用本文件。
"""
import os
import re
import sys

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

MIN_PYTHON = (3, 9)

# import 名 -> PyPI 包名
REQUIRED_MODULES = {
    "flask": "flask",
    "pandas": "pandas",
    "openpyxl": "openpyxl",
    "requests": "requests",
    "urllib3": "urllib3",
    "bs4": "beautifulsoup4",
    "yaml": "PyYAML",
}


def _harden_console():
    """控制台输出统一 errors=replace，避免非 UTF-8 代码页下中文/全角路径报错。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


def check_python():
    if sys.version_info < MIN_PYTHON:
        sys.stderr.write(
            "[run] Python %d.%d+ is required, current: %s\n"
            % (MIN_PYTHON[0], MIN_PYTHON[1], sys.version.split()[0]))
        return False
    return True


def missing_modules():
    import importlib.util
    missing = []
    for mod in REQUIRED_MODULES:
        try:
            if importlib.util.find_spec(mod) is None:
                missing.append(mod)
        except Exception:
            missing.append(mod)
    return sorted(missing)


def requirement_specs(missing):
    """把缺失的 import 名映射回 requirements.txt 中的原始约束行。

    只返回缺失项对应的约束（例如 "openpyxl>=3.1"），让 pip 只处理缺失的包，
    不会因为个别模块缺失而顺带升级/降级其它已装好、可正常导入的包。
    映射失败时退化为裸包名，保证仍能装上。
    """
    req_file = os.path.join(APP_ROOT, "requirements.txt")
    raw_lines = {}
    if os.path.isfile(req_file):
        with open(req_file, "r", encoding="utf-8") as fh:
            for line in fh:
                item = line.strip()
                if not item or item.startswith("#"):
                    continue
                item = item.split(";")[0].split("#")[0].strip()
                if not item:
                    continue
                pkg = re.split(r"[<>=!~\[]", item, 1)[0].strip().lower()
                if pkg:
                    raw_lines.setdefault(pkg, item)
    specs = []
    for mod in missing:
        pkg = REQUIRED_MODULES.get(mod, mod)
        specs.append(raw_lines.get(pkg.lower(), pkg))
    return specs


def install_requirements(missing=None):
    """只安装真正缺失的依赖（缺哪个补哪个）。

    不再执行 `pip install -r requirements.txt` 整份清单重装，避免个别模块缺失
    时连带改动已经装好且当前可用的其它包（例如把 pandas 降级）。
    """
    import subprocess
    if missing is None:
        missing = missing_modules()
    if not missing:
        print("[run] Nothing to install.")
        return True
    specs = requirement_specs(missing)
    cmd = [sys.executable, "-m", "pip", "install",
           "--disable-pip-version-check", "--no-input"] + specs
    print("[run] Installing missing dependencies: " + ", ".join(missing))
    print("[run]   " + " ".join(cmd))
    try:
        return subprocess.call(cmd) == 0
    except Exception as e:
        print("[run] pip invocation failed: %s" % e)
        return False


def ensure_environment(auto_install=True):
    if not check_python():
        return False
    missing = missing_modules()
    if not missing:
        print("[run] Dependencies OK.")
        return True
    print("[run] Missing dependencies: " + ", ".join(missing))
    specs = requirement_specs(missing)
    if not auto_install:
        print("[run] Install them with: %s -m pip install %s"
              % (sys.executable, " ".join(specs)))
        return False
    if not install_requirements(missing):
        print("[run] Dependency installation failed.")
        print("[run] Please run manually: %s -m pip install %s"
              % (sys.executable, " ".join(specs)))
        return False
    still = missing_modules()
    if still:
        print("[run] Still missing after install: " + ", ".join(still))
        return False
    print("[run] Dependencies installed.")
    return True


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    _harden_console()
    os.environ.setdefault("PYTHONUTF8", "1")

    check_only = "--check" in argv
    forward = [a for a in argv if a != "--check"]

    if not ensure_environment(auto_install=not check_only):
        return 1

    import app_paths
    created = app_paths.ensure_runtime_dirs()
    if created:
        print("[run] Runtime directories created:")
        for d in created:
            print("[run]   " + d)
    else:
        print("[run] Runtime root: " + app_paths.RUNTIME_DIR)

    if check_only:
        print("[run] Python : %s" % sys.version.split()[0])
        print("[run] Environment check passed (service not started).")
        return 0

    from web_app import main as web_main
    saved_argv = sys.argv
    sys.argv = [os.path.join(APP_ROOT, "web_app.py")] + forward
    try:
        web_main()
    finally:
        sys.argv = saved_argv
    return 0


if __name__ == "__main__":
    sys.exit(main())
