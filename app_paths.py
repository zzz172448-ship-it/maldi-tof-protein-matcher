# -*- coding: utf-8 -*-
# 基于质谱峰的蛋白质匹配软件 V1.0
# Copyright (c) 2026 张葛阳
# 本软件为独立开发，未使用第三方开源代码
"""统一路径层（开源版）。

所有运行时数据（备份 / 输出 / 日志 / 临时 / 会话）默认落在工程目录下的
``runtime/`` 子目录，首次运行自动创建。

覆盖优先级（从高到低）：
    1. 环境变量   DSH_RUNTIME_DIR / DSH_DATA_DIR / DSH_OUTPUT_DIR /
                  DSH_LOGS_DIR / DSH_TEMP_DIR / DSH_SESSIONS_DIR / DSH_BACKUPS_DIR
    2. config.local.yaml 的 ``paths`` 分节（Web「系统设置」写入）
    3. 工程内默认值  <工程根>/runtime/<子目录>

本模块不依赖 config.py，可被任意模块安全导入（无循环依赖）。
"""
import os

APP_ROOT = os.path.dirname(os.path.abspath(__file__))

DEFAULT_CONFIG_FILE = os.path.join(APP_ROOT, "config.default.yaml")
LOCAL_CONFIG_FILE = os.path.join(APP_ROOT, "config.local.yaml")

# 兼容可能被注入的环境变量
_LEGACY_ENV_ALIASES = {
    "DSH_CFG_BACKUP_DIR": "DSH_BACKUPS_DIR",
}


def _load_local_paths():
    """读取 config.local.yaml 的 paths 分节；缺 pyyaml 或文件不存在时返回空。"""
    if not os.path.isfile(LOCAL_CONFIG_FILE):
        return {}
    try:
        import yaml
    except Exception:
        return {}
    try:
        with open(LOCAL_CONFIG_FILE, "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        paths = doc.get("paths") or {}
        return paths if isinstance(paths, dict) else {}
    except Exception:
        return {}


def _env(name):
    v = os.environ.get(name)
    if not v:
        alias = _LEGACY_ENV_ALIASES.get(name)
        if alias:
            v = os.environ.get(alias)
    return (v or "").strip()


def _resolve(value, default):
    """相对路径按工程根目录解析，绝对路径原样（支持 ~ 与环境变量展开）。"""
    v = (value or "").strip()
    if not v:
        return os.path.abspath(default)
    v = os.path.expanduser(os.path.expandvars(v))
    if not os.path.isabs(v):
        v = os.path.join(APP_ROOT, v)
    return os.path.abspath(v)


def _pick(env_name, local_paths, key, default):
    return _resolve(_env(env_name) or local_paths.get(key), default)


_local_paths = _load_local_paths()

RUNTIME_DIR = _pick("DSH_RUNTIME_DIR", _local_paths, "runtime_dir",
                    os.path.join(APP_ROOT, "runtime"))
DATA_DIR = _pick("DSH_DATA_DIR", _local_paths, "data_dir",
                 os.path.join(RUNTIME_DIR, "data"))
OUTPUT_DIR = _pick("DSH_OUTPUT_DIR", _local_paths, "output_dir",
                   os.path.join(RUNTIME_DIR, "output"))
LOGS_DIR = _pick("DSH_LOGS_DIR", _local_paths, "logs_dir",
                 os.path.join(RUNTIME_DIR, "logs"))
TEMP_DIR = _pick("DSH_TEMP_DIR", _local_paths, "temp_dir",
                 os.path.join(RUNTIME_DIR, "temp"))
SESSIONS_DIR = _pick("DSH_SESSIONS_DIR", _local_paths, "sessions_dir",
                     os.path.join(RUNTIME_DIR, "sessions"))
BACKUPS_DIR = _pick("DSH_BACKUPS_DIR", _local_paths, "backups_dir",
                    os.path.join(RUNTIME_DIR, "backups"))

_ALL_DIRS = (
    ("runtime", RUNTIME_DIR),
    ("data", DATA_DIR),
    ("output", OUTPUT_DIR),
    ("logs", LOGS_DIR),
    ("temp", TEMP_DIR),
    ("sessions", SESSIONS_DIR),
    ("backups", BACKUPS_DIR),
)


def ensure_runtime_dirs():
    """确保所有运行时目录存在，返回本次新建的目录列表。"""
    created = []
    for _name, d in _ALL_DIRS:
        try:
            if not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
                created.append(d)
        except OSError:
            pass
    return created


def as_dict():
    """返回全部运行时路径（供 Web「系统设置」页展示）。"""
    return dict(_ALL_DIRS)


def default_output_dir():
    """流水线默认输出目录（用户可在界面覆盖）。"""
    return OUTPUT_DIR
