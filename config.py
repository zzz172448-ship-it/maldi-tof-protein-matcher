# -*- coding: utf-8 -*-
"""蛋白质数据处理系统 - 配置中心（开源版）。

配置层级（后者覆盖前者）：
    代码内置兜底  <  config.default.yaml  <  config.local.yaml

config.local.yaml 由 Web 界面「系统设置」写入，已被 .gitignore 忽略，
不会随仓库分发，保证开源使用者拿到的是干净默认值。

对外接口：
    CFG.get("dsh.port", 3080)      按点号路径读取配置
    CFG.load_settings()            重新载入配置
    CFG.save_local_settings({...}) 写入 config.local.yaml
    CFG.runtime_paths()            当前生效的运行时路径

同时保持旧版常量（BASE_DIR / EXPSY_DEFAULTS / FILENAME_* 等）兼容，
各 core 模块可继续按原有方式 import。
"""
import os
import threading

import app_paths

# ============================================================
#  基础常量（旧版兼容）
# ============================================================
BASE_DIR = app_paths.APP_ROOT

EXPSY_DEFAULTS = {
    "enzyme": "Trypsin",
    "missed_cleavages": 3,
    "min_mass": 500,
    "max_mass": None,
    "sort_by": "mass",
    "cys_treatment": "nothing",
    "met_oxidized": False,
    "mplus": "[M+H]+",
    "masses": "monoisotopic",
    "show_ptm": True,
    "show_conflict": False,
    "show_variant": False,
    "show_varsplice": False,
}

DEFAULT_TOLERANCE = 50
DEFAULT_TOLERANCE_UNIT = "ppm"
MIN_PEPTIDE_MATCH = 2
MIN_COVERAGE_PERCENT = 10.0
UNIPROT_BASE = "https://rest.uniprot.org"
UNIPROT_TIMEOUT = 30

# DSH 可执行入口（仅供参考；实际启动优先使用 PATH 中的 dsh / npm 全局 dsh.cmd）
DSH_CMD = ["npx", "@deepseek-ai/dsh", "web"]

FILENAME_PROTEIN_MATCH = "protein_match_result.xlsx"
FILENAME_PEPTIDE_CRAWL = "peptide_crawl_database.xlsx"
FILENAME_PEPTIDE_MATCH = "peptide_match_result.xlsx"
FILENAME_PROTEIN_ANALYSIS = "protein_analysis_result.xlsx"
FILENAME_PROTEIN_REPEAT = "protein_repeat_matrix.xlsx"
FILENAME_RUN_LOG = "run_log.json"

# DSH 文件访问权限可选值与默认值（默认收紧为 workspace-write）
DSH_PERMISSION_MODES = ("read-only", "workspace-write", "danger-full-access")
DSH_DEFAULT_PERMISSION_MODE = "workspace-write"

# ============================================================
#  可配置项默认值（与 config.default.yaml 保持一致）
# ============================================================
DEFAULT_SETTINGS = {
    "web": {
        "port": 0,              # 0 = 自动探测空闲端口
        "host": "127.0.0.1",
        "open_browser": True,
    },
    "paths": {
        "runtime_dir": "runtime",
        "data_dir": "",
        "output_dir": "",
        "logs_dir": "",
        "temp_dir": "",
        "sessions_dir": "",
        "backups_dir": "",
    },
    "dsh": {
        "port": 3080,
        "permission_mode": DSH_DEFAULT_PERMISSION_MODE,
    },
    "pipeline": {
        "default_tolerance": DEFAULT_TOLERANCE,
        "default_tolerance_unit": DEFAULT_TOLERANCE_UNIT,
        "max_workers": 5,
    },
}

_lock = threading.RLock()
_settings = {}


def _deep_merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _read_yaml(path):
    if not os.path.isfile(path):
        return {}
    try:
        import yaml
    except Exception:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def load_settings():
    """重新载入并返回合并后的完整配置。"""
    global _settings
    merged = _deep_merge(DEFAULT_SETTINGS, _read_yaml(app_paths.DEFAULT_CONFIG_FILE))
    merged = _deep_merge(merged, _read_yaml(app_paths.LOCAL_CONFIG_FILE))
    with _lock:
        _settings = merged
    return merged


def get(key, default=None):
    """按点号路径读取配置，如 get("dsh.port", 3080)。"""
    with _lock:
        node = _settings or load_settings()
    for part in str(key).split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node


def save_local_settings(updates):
    """把 updates 深合并写入 config.local.yaml（不修改 config.default.yaml）。"""
    try:
        import yaml
    except Exception as e:
        raise RuntimeError("PyYAML 未安装，无法保存设置：python -m pip install pyyaml") from e
    existing = _read_yaml(app_paths.LOCAL_CONFIG_FILE)
    merged = _deep_merge(existing, updates or {})
    try:
        with open(app_paths.LOCAL_CONFIG_FILE, "w", encoding="utf-8", newline="\n") as f:
            f.write("# 本机配置（由 Web 界面「系统设置」写入；已被 .gitignore 忽略）\n")
            yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False,
                           default_flow_style=False, width=120)
    except OSError as e:
        raise RuntimeError("无法写入 %s: %s" % (app_paths.LOCAL_CONFIG_FILE, e)) from e
    load_settings()
    return app_paths.LOCAL_CONFIG_FILE


def runtime_paths():
    """当前生效的运行时路径。"""
    return app_paths.as_dict()


def normalize_port(value, default=0):
    try:
        p = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if p < 0 or p > 65535:
        return default
    return p


def normalize_permission_mode(value):
    v = str(value or "").strip()
    return v if v in DSH_PERMISSION_MODES else DSH_DEFAULT_PERMISSION_MODE


load_settings()
