# -*- coding: utf-8 -*-
"""
Protein Data Processing System - Local Web UI (Flask)
Reuses the same shared core modules of the four-step pipeline:
  Step1 protein_matcher_core / Step2 peptide_crawler_core /
  Step3 peptide_matcher_core / Step4 protein_analyzer_core
Run:  python web_app.py            (auto-picks a free port & opens the browser)
      python web_app.py --port 8000
      python web_app.py --no-browser
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from collections import deque
from datetime import datetime

import pandas as pd
from flask import Flask, jsonify, render_template, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import app_paths as AP
import config as CFG
from run_logger import RunLogger
from protein_matcher_core import match_proteins
from peptide_crawler_core import PeptideCrawlerCore
from peptide_matcher_core import match_peptides
from protein_analyzer_core import analyze_proteins, build_repeat_matrix
import dsh_provider_config as DSCFG

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

OUTPUT_EXTENSIONS = (".xlsx", ".xls", ".xlsm")

# 运行时目录（备份 / 输出 / 日志 / 临时 / 会话）统一由 app_paths 提供：
# 默认落在工程内 runtime/ 下，首次运行自动创建；可用 config.local.yaml
# （Web 界面「设置」页）或 DSH_* 环境变量覆盖。
AP.ensure_runtime_dirs()

# DSH 模型配置备份目录：保存/删除/恢复默认等写操作前，
# settings.yaml / .credentials.yaml 会先备份到此目录。
DSH_CFG_BACKUP_DIR = AP.BACKUPS_DIR
DSCFG.set_backup_dir(DSH_CFG_BACKUP_DIR)


# ============================================================
#  Run context (single concurrent pipeline; Stop is best-effort)
# ============================================================
class RunContext:
    def __init__(self):
        self.lock = threading.Lock()
        self.logs = deque(maxlen=4000)   # [{ts, msg}]
        self.running = False
        self.stop_requested = False
        self.current_step = 0            # 0 idle, 1..4 busy, 5 finished
        self.step_name = ""
        self.progress = 0
        self.error = None
        self.output_dir = ""
        self.result_files = {}           # step-key -> abs xlsx path
        self.step_stats = {}             # step-key -> human summary
        self.finished_at = None
        self.crawler = None              # live PeptideCrawlerCore for Stop

    # ---- thread-safe log buffer ----
    def append_log(self, msg):
        now = datetime.now()
        ts = f"{now.hour:02d}:{now.minute:02d}:{now.second:02d}"
        with self.lock:
            self.logs.append({"ts": ts, "msg": msg})

    def snapshot_logs(self, since=0):
        with self.lock:
            items = list(self.logs)
        return items[since:], len(items)

    def reset(self, output_dir):
        with self.lock:
            self.logs.clear()
            self.running = True
            self.stop_requested = False
            self.current_step = 0
            self.step_name = ""
            self.progress = 0
            self.error = None
            self.output_dir = output_dir
            self.result_files = {}
            self.step_stats = {}
            self.finished_at = None

    def finish(self):
        with self.lock:
            self.running = False
            self.current_step = 5
            self.finished_at = datetime.now().isoformat(timespec="seconds")

    def status_dict(self):
        with self.lock:
            return {
                "running": self.running,
                "stop_requested": self.stop_requested,
                "current_step": self.current_step,
                "step_name": self.step_name,
                "progress": self.progress,
                "error": self.error,
                "output_dir": self.output_dir,
                "result_files": dict(self.result_files),
                "step_stats": dict(self.step_stats),
                "finished_at": self.finished_at,
            }


RUN_CTX = RunContext()


def log(msg):
    RUN_CTX.append_log(msg)


# ============================================================
#  Pipeline (mirrors legacy protein_processor._run_all_steps)
# ============================================================
CYS_GUI_TO_INTERNAL_LEGACY = {
    "nothing (in reduced form)": "nothing",
    "Iodoacetic acid": "iodoacetic_acid",
    "Iodoacetamide": "iodoacetamide",
    "4-vinyl pyridene": "vinyl_pyridine",
}


def _gui_str(v):
    return "" if v is None else str(v)


# sort GUI labels -> internal form values accepted by Expasy
SORT_GUI_TO_INTERNAL = {
    "peptide masses": "mass",
    "chronological order in the protein": "chronologic",
}


def build_expasy_params(p):
    """Reassemble ExpasyParams from web params.

    cys_reagent = Expasy reagents select 文本(nothing/Iodoacetic acid/Iodoacetamide/4-vinyl pyridene);
    cys_acrylamide = 与 reagents 平级的独立勾选;
    兼容旧字段 cys_treatment(GUI/内部值在 expasy_params 内自动归一化)。
    """
    from expasy_params import normalize_cys_value, ExpasyParams

    def to_bool(v, default):
        if isinstance(v, bool):
            return v
        if v in (1, "1", "true", "True", "on"):
            return True
        if v in (0, "0", "false", "False", "off"):
            return False
        return default

    cys_raw = _gui_str(p.get("cys_reagent") or p.get("cys_treatment")
                       or "nothing (in reduced form)")
    reagent, acryl = normalize_cys_value(cys_raw)
    if p.get("cys_acrylamide") is not None and _gui_str(p.get("cys_acrylamide")) != "":
        acryl = to_bool(p.get("cys_acrylamide"), acryl)

    min_raw = _gui_str(p.get("min_mass", "500"))
    min_mass = int(min_raw) if min_raw.isdigit() else 500

    max_raw = _gui_str(p.get("max_mass", "unlimited"))
    if max_raw == "unlimited" or not max_raw:
        max_mass = None
    elif max_raw.isdigit():
        max_mass = int(max_raw)
    else:
        max_mass = None

    sort_raw = _gui_str(p.get("sort_by", "peptide masses"))

    return ExpasyParams(
        enzyme=_gui_str(p.get("enzyme", CFG.EXPSY_DEFAULTS["enzyme"])),
        missed_cleavages=int(_gui_str(p.get("missed_cleavages", "3")) or 0),
        min_mass=min_mass,
        max_mass=max_mass,
        sort_by=sort_raw,
        cys_reagent=reagent,
        cys_acrylamide=acryl,
        met_oxidized=to_bool(p.get("met_oxidized"), False),
        mplus=_gui_str(p.get("ion_type", "[M+H]+")),
        masses=_gui_str(p.get("mass_type", "monoisotopic")),
        show_ptm=to_bool(p.get("show_ptm"), True),
        show_conflict=to_bool(p.get("show_conflict"), False),
        show_variant=to_bool(p.get("show_variant"), False),
        show_varsplice=to_bool(p.get("show_varsplice"), False),
    )


def extract_protein_ids(step1_file):
    """Extract protein IDs from Step-1 result sheet (legacy logic)."""
    step1_df = pd.read_excel(step1_file)
    id_col = None
    for col in step1_df.columns:
        if "Entry" in str(col) or "Protein" in str(col) or "ID" in str(col):
            id_col = col
            break
    if id_col is None:
        id_col = step1_df.columns[0]
    ids = step1_df[id_col].dropna().astype(str).str.strip().tolist()
    return [p for p in ids if p and p != "nan"]


def _check_step_interrupt():
    return RUN_CTX.stop_requested


def execute_pipeline(p):
    """Run the four processing steps on a background thread."""
    RUN_CTX.reset(p["output_folder"])
    output_folder = p["output_folder"]
    try:
        os.makedirs(output_folder, exist_ok=True)
        logger = RunLogger(output_folder)

        # Input params recorded for run_log.json / AI analysis
        expasy_rec = p.get("expasy", {})
        rec = {
            "reference_file": p.get("reference_file", ""),
            "protein_folder": p.get("protein_folder", ""),
            "peptide_folder": p.get("peptide_folder", ""),
            "output_folder": output_folder,
            "tolerance": p.get("tolerance", CFG.DEFAULT_TOLERANCE),
            "tolerance_unit": p.get("tolerance_unit", CFG.DEFAULT_TOLERANCE_UNIT),
            "expasy_params": {
                "enzyme": expasy_rec.get("enzyme"),
                "missed_cleavages": expasy_rec.get("missed_cleavages"),
                "min_mass": expasy_rec.get("min_mass"),
                "max_mass": expasy_rec.get("max_mass"),
                "cys_reagent": expasy_rec.get("cys_reagent") or expasy_rec.get("cys_treatment"),
                "cys_acrylamide": expasy_rec.get("cys_acrylamide"),
                "cys_treatment": expasy_rec.get("cys_treatment"),
                "met_oxidized": expasy_rec.get("met_oxidized"),
                "ion_type": expasy_rec.get("ion_type"),
                "mass_type": expasy_rec.get("mass_type"),
                "sort_by": expasy_rec.get("sort_by"),
                "show_ptm": expasy_rec.get("show_ptm"),
                "show_conflict": expasy_rec.get("show_conflict"),
                "show_variant": expasy_rec.get("show_variant"),
                "show_varsplice": expasy_rec.get("show_varsplice"),
            },
        }
        logger.set_input_params(rec)

        tolerance = float(p.get("tolerance", CFG.DEFAULT_TOLERANCE))
        tolerance_unit = p.get("tolerance_unit", CFG.DEFAULT_TOLERANCE_UNIT)

        reference_file = p.get("reference_file", "").strip()
        if not reference_file or not os.path.isfile(reference_file):
            raise RuntimeError(f"Reference file not found: {reference_file}")
        log(f"Using reference file: {os.path.basename(reference_file)}")

        # ===== Step 1: Protein Initial Matching =====
        if _check_step_interrupt():
            return
        with RUN_CTX.lock:
            RUN_CTX.current_step = 1
            RUN_CTX.step_name = "蛋白初筛 (Protein Initial Matching)"
            RUN_CTX.progress = 5
        log("Step 1/4 蛋白初筛：参考 Mass 匹配实验 m/z ...")
        t0 = time.time()
        step1_file, match_count = match_proteins(
            reference_file,
            p["protein_folder"], output_folder, tolerance, tolerance_unit)
        step1_path = os.path.join(output_folder, CFG.FILENAME_PROTEIN_MATCH)
        logger.log_step(1, "Protein Initial Matching",
                        input_data={"reference": reference_file,
                                    "experiment_folder": p["protein_folder"]},
                        output_data={"file": step1_path, "matches": match_count},
                        duration_sec=time.time() - t0)
        if os.path.exists(step1_path):
            with RUN_CTX.lock:
                RUN_CTX.result_files["step1"] = step1_path
                RUN_CTX.step_stats["step1"] = f"匹配成功 {match_count} 条"
                RUN_CTX.progress = 25
            log(f"Step 1 完成：{match_count} 条匹配 -> {CFG.FILENAME_PROTEIN_MATCH}")
        else:
            log("Step 1 未产生匹配结果，中止流程")
            logger.log_step(1, "Protein Initial Matching", success=False, duration_sec=time.time() - t0,
                            output_data={"matches": 0})
            raise RuntimeError("Step 1 failed: no matches found")

        # ===== Step 2: Peptide Data Crawling =====
        if _check_step_interrupt():
            return
        with RUN_CTX.lock:
            RUN_CTX.current_step = 2
            RUN_CTX.step_name = "肽段爬取 (Expasy Peptide Crawling)"
            RUN_CTX.progress = 30
        log("Step 2/4 肽段爬取：读取 Step1 蛋白 ID，请求 Expasy PeptideMass ...")
        t1 = time.time()

        protein_ids = extract_protein_ids(step1_path)
        log(f"Extracted {len(protein_ids)} protein IDs from Step 1 results")

        expasy_params = build_expasy_params(expasy_rec)

        crawler = PeptideCrawlerCore(
            output_folder=output_folder,
            log_callback=lambda m: log(f"[Step2] {m}"),
        )
        with RUN_CTX.lock:
            RUN_CTX.crawler = crawler
        crawl_file = os.path.join(output_folder, CFG.FILENAME_PEPTIDE_CRAWL)
        crawl_success, failed_ids = crawler.process_protein_list(
            protein_ids, crawl_file, expasy_params, max_workers=5)

        records = 0
        if os.path.exists(crawl_file):
            records = len(pd.read_excel(crawl_file))
        with RUN_CTX.lock:
            RUN_CTX.result_files["step2"] = crawl_file
            RUN_CTX.step_stats["step2"] = f"抓取记录 {records} 条 / 失败蛋白 {len(failed_ids)} 个"
            RUN_CTX.progress = 50
        logger.log_step(2, "Peptide Data Crawling",
                        input_data={"protein_ids": len(protein_ids),
                                    "enzyme": expasy_rec.get("enzyme")},
                        output_data={"success": crawl_success, "failed_count": len(failed_ids),
                                     "records": records},
                        duration_sec=time.time() - t1, success=crawl_success)
        if not os.path.exists(crawl_file) or records == 0:
            raise RuntimeError("Step 2 failed: no peptide records crawled")
        if not crawl_success:
            log(f"Step 2 完成但有 {len(failed_ids)} 个蛋白抓取失败，继续后续步骤")

        # ===== Step 3: Peptide-Level Matching =====
        if _check_step_interrupt():
            return
        with RUN_CTX.lock:
            RUN_CTX.current_step = 3
            RUN_CTX.step_name = "肽段匹配 (Peptide-Level Matching)"
            RUN_CTX.progress = 55
        log("Step 3/4 肽段匹配：理论肽段质量 vs 实验 m/z ...")
        t2 = time.time()
        step3_file, match_count3, _ = match_peptides(
            crawl_file, p["peptide_folder"],
            os.path.join(output_folder, CFG.FILENAME_PEPTIDE_MATCH),
            tolerance, tolerance_unit)
        logger.log_step(3, "Peptide-Level Matching",
                        input_data={"theoretical_file": CFG.FILENAME_PEPTIDE_CRAWL,
                                    "experiment_folder": p["peptide_folder"]},
                        output_data={"file": step3_file, "matches": match_count3},
                        duration_sec=time.time() - t2)
        if step3_file is None or not os.path.exists(step3_file):
            log("Step 3 未产生匹配结果，中止流程")
            raise RuntimeError("Step 3 failed: no peptide matches found")
        with RUN_CTX.lock:
            RUN_CTX.result_files["step3"] = step3_file
            RUN_CTX.step_stats["step3"] = f"匹配成功 {match_count3} 条"
            RUN_CTX.progress = 75
        log(f"Step 3 完成：{match_count3} 条匹配 -> {CFG.FILENAME_PEPTIDE_MATCH}")

        # ===== Step 4: Protein Analysis =====
        if _check_step_interrupt():
            return
        with RUN_CTX.lock:
            RUN_CTX.current_step = 4
            RUN_CTX.step_name = "蛋白分析 (Protein Analysis)"
            RUN_CTX.progress = 80
        log("Step 4/4 蛋白分析：覆盖率 / 置信度星级统计 ...")
        t3 = time.time()
        analysis_file, summary_df = analyze_proteins(
            step3_file, os.path.join(output_folder, CFG.FILENAME_PROTEIN_ANALYSIS))

        total_proteins = len(summary_df) if summary_df is not None else 0
        high_conf = 0
        if summary_df is not None and "Confidence" in summary_df.columns:
            high_conf = int(summary_df["Confidence"].astype(str).str.contains("★★★★").sum())

        repeat_file = None
        repeat_rows = 0
        repeat_error = ""
        try:
            repeat_file, repeat_rows = build_repeat_matrix(
                step3_file, os.path.join(output_folder, CFG.FILENAME_PROTEIN_REPEAT))
        except Exception as e:
            repeat_error = f"{type(e).__name__}: {e}"
            log(f"Step 4 蛋白重复矩阵生成失败: {repeat_error}")
            log(traceback.format_exc())

        with RUN_CTX.lock:
            RUN_CTX.result_files["step4"] = analysis_file
            RUN_CTX.step_stats["step4"] = f"识别蛋白 {total_proteins} 个 / 高置信 {high_conf} 个"
            if repeat_file:
                RUN_CTX.result_files["step4_repeat"] = repeat_file
                RUN_CTX.step_stats["step4_repeat"] = f"重复矩阵 {repeat_rows // 5} 蛋白 / {repeat_rows} 行"
            else:
                RUN_CTX.step_stats["step4_repeat"] = f"未生成（{repeat_error or '未返回文件'}）"
            RUN_CTX.progress = 100
        logger.log_step(4, "Protein Analysis",
                        input_data={"input_file": CFG.FILENAME_PEPTIDE_MATCH},
                        output_data={"file": analysis_file, "proteins": total_proteins,
                                     "high_confidence": high_conf},
                        duration_sec=time.time() - t3)
        logger.set_summary({
            "total_proteins": total_proteins,
            "high_confidence": high_conf,
            "total_peptide_matches": match_count3,
            "run_id": logger.run_id,
        })

        log("=" * 50)
        log("ALL STEPS COMPLETED SUCCESSFULLY!")
        log(f"Output folder: {output_folder}")
        log(f"Run log: {logger.get_log_path()}")
        log("=" * 50)

    except Exception as e:
        log(f"ERROR: {e}")
        log(traceback.format_exc())
        with RUN_CTX.lock:
            RUN_CTX.error = str(e)
    finally:
        if RUN_CTX.stop_requested:
            log("任务已由用户停止")
            with RUN_CTX.lock:
                RUN_CTX.error = "Stopped by user"
        RUN_CTX.finish()


# ============================================================
#  Small helpers
# ============================================================
def is_hidden_dir(path):
    """True if the directory has HIDDEN or SYSTEM attribute (best effort)."""
    try:
        import ctypes
        attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
        if attrs == -1:
            return False
        return bool(attrs & 0x2) or bool(attrs & 0x4)  # FILE_ATTRIBUTE_HIDDEN / SYSTEM
    except Exception:
        return False


def list_subdirs(path):
    out = []
    try:
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            try:
                if os.path.isdir(full) and not is_hidden_dir(full):
                    out.append(name)
            except OSError:
                continue
    except OSError as e:
        raise RuntimeError(str(e))
    return out


def free_port(start=5000):
    for port in range(start, start + 200):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 0


def df_to_table(path, status_logic=None, max_rows=200):
    """Read one xlsx and produce {exists, columns, rows, total, truncated, status logic}."""
    if not path or not os.path.exists(path):
        return {"exists": False, "path": path}
    try:
        df = pd.read_excel(path, header=0)
    except Exception as e:
        return {"exists": False, "path": path, "error": str(e)}
    # first row may have been data if header detection failed -> try header=1 fallback
    if df.columns.tolist() and all(str(c).startswith("Unnamed") for c in df.columns[:2]):
        try:
            df2 = pd.read_excel(path, header=1)
            if df2.columns.tolist() and any(not str(c).startswith("Unnamed") for c in df2.columns):
                df = df2
        except Exception:
            pass
    total = len(df)
    df = df.head(max_rows).copy()

    # status tag decoration
    statuses = None
    if status_logic == "step4":
        col = None
        for c in df.columns:
            if str(c).strip().lower() == "confidence":
                col = c
                break
        if col is not None:
            statuses = []
            for v in df[col].astype(str):
                stars = v.count("★")
                if stars >= 5:
                    statuses.append(("高置信度", "ok"))
                elif stars == 4:
                    statuses.append(("较高置信", "ok"))
                elif stars == 3:
                    statuses.append(("中等置信", "warn"))
                else:
                    statuses.append(("低置信度", "err"))
    elif status_logic == "step2":
        statuses = [("抓取成功", "ok")] * len(df)
    elif status_logic in ("step1", "step3"):
        statuses = [("匹配成功", "ok")] * len(df)

    rows = []
    for i, (_, row) in enumerate(df.iterrows()):
        rec = {}
        for c in df.columns:
            v = row[c]
            if pd.isna(v):
                rec[str(c)] = ""
            elif isinstance(v, (float,)):
                if v.is_integer():
                    rec[str(c)] = int(v)
                else:
                    rec[str(c)] = round(v, 6)
            elif isinstance(v, (int, str, bool)):
                rec[str(c)] = v
            else:
                rec[str(c)] = str(v)
        if statuses:
            rec["_status"], rec["_status_level"] = statuses[i]
        rows.append(rec)

    return {
        "exists": True,
        "path": path,
        "columns": [str(c) for c in df.columns] + (["状态"] if statuses else []),
        "rows": rows,
        "total": int(total),
        "truncated": total > max_rows,
    }


# ============================================================
#  Routes
# ============================================================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/meta")
def api_meta():
    """Expasy dropdown lists + defaults for rendering the parameter panel."""
    try:
        from expasy_params import ENZymes, CYS_OPTIONS, MASS_TYPES, ION_GUI_TO_INTERNAL
    except Exception:
        ENZymes = []
        CYS_OPTIONS = ["nothing (in reduced form)", "Iodoacetic acid", "Iodoacetamide", "4-vinyl pyridene"]
        ION_GUI_TO_INTERNAL = {"[M+H]+": "mh", "[M]": "m", "[M-H]-": "mminus", "[M+2H]2+": "mh2", "[M+3H]3+": "mh3"}
        MASS_TYPES = ["monoisotopic", "average"]
    ION_OPTIONS = list(ION_GUI_TO_INTERNAL)
    sort_options = ["peptide masses", "chronological order in the protein"]
    d = CFG.EXPSY_DEFAULTS
    return jsonify({
        "enzymes": ENZymes,
        "cys_options": CYS_OPTIONS,
        "ion_options": ION_OPTIONS,
        "mass_types": MASS_TYPES,
        "sort_options": sort_options,
        "min_mass_options": ["0", "500", "750", "1000", "1250", "1500", "1750"],
        "max_mass_options": ["3000", "4000", "5000", "6000", "7000", "8000", "unlimited"],
        "defaults": {
            "enzyme": d.get("enzyme"),
            "missed_cleavages": d.get("missed_cleavages"),
            "min_mass": d.get("min_mass", 500),
            "max_mass": d.get("max_mass") or "unlimited",
            "sort_by": "peptide masses",
            "cys_reagent": "nothing (in reduced form)",
            "cys_acrylamide": False,
            "cys_treatment": "nothing (in reduced form)",
            "met_oxidized": d.get("met_oxidized", False),
            "ion_type": d.get("mplus", "[M+H]+"),
            "mass_type": d.get("masses", "monoisotopic"),
            "show_ptm": d.get("show_ptm", True),
            "show_conflict": d.get("show_conflict", False),
            "show_variant": d.get("show_variant", False),
            "show_varsplice": d.get("show_varsplice", False),
        },
        "tolerance": CFG.DEFAULT_TOLERANCE,
        "tolerance_unit": CFG.DEFAULT_TOLERANCE_UNIT,
        "default_output_dir": AP.default_output_dir(),
        "runtime": AP.as_dict(),
        "filenames": {
            "step1": CFG.FILENAME_PROTEIN_MATCH,
            "step2": CFG.FILENAME_PEPTIDE_CRAWL,
            "step3": CFG.FILENAME_PEPTIDE_MATCH,
            "step4": CFG.FILENAME_PROTEIN_ANALYSIS,
        },
    })


@app.route("/api/drives")
def api_drives():
    """List existing drive roots (Windows)."""
    import string
    drives = []
    for letter in string.ascii_uppercase:
        root = letter + ":\\"
        if os.path.exists(root):
            drives.append(root)
    return jsonify({"drives": drives})


@app.route("/api/browse")
def api_browse():
    """List subdirectories of the given path (empty path -> drive list).
    With files=1, also list .xlsx files in the current directory."""
    list_files = (request.args.get("files") or "").strip().lower() in ("1", "true", "yes")
    path = (request.args.get("path") or "").strip()
    if not path or path in ("\\", "/"):
        drives = []
        import string
        for letter in string.ascii_uppercase:
            root = letter + ":\\"
            if os.path.exists(root):
                drives.append(root)
        return jsonify({"path": "", "parent": None, "dirs": drives, "files": [],
                        "is_drive_view": True})
    path = os.path.normpath(path)
    parent = os.path.dirname(path.rstrip("\\"))
    if os.path.isdir(path):
        try:
            dirs = list_subdirs(path)
            files = []
            if list_files:
                files = [n for n in sorted(os.listdir(path))
                         if n.lower().endswith(OUTPUT_EXTENSIONS) and not n.startswith("~$")]
            return jsonify({"path": path, "parent": parent, "dirs": dirs, "files": files,
                            "is_drive_view": False})
        except Exception as e:
            return jsonify({"path": path, "parent": parent, "dirs": [], "files": [],
                            "error": str(e)}), 500
    return jsonify({"path": path, "parent": None, "dirs": [], "files": [],
                    "error": "Not a directory"}), 404


@app.route("/api/run/status")
def api_run_status():
    since = request.args.get("since", 0, type=int)
    logs, total = RUN_CTX.snapshot_logs(since)
    status = RUN_CTX.status_dict()
    return jsonify({"status": status, "logs": logs, "log_total": total})


@app.route("/api/run/stop", methods=["POST"])
def api_run_stop():
    with RUN_CTX.lock:
        RUN_CTX.stop_requested = True
        crawler = RUN_CTX.crawler
    if crawler is not None:
        try:
            crawler.is_running = False
        except Exception:
            pass
    log("用户请求停止任务 ...")
    return jsonify({"ok": True})


@app.route("/api/run", methods=["POST"])
def api_run():
    if RUN_CTX.running:
        return jsonify({"ok": False, "error": "已有任务在运行中"}), 409
    data = request.get_json(force=True, silent=True) or {}
    params = data.get("params", {})
    # validate reference file + three folders
    required = ["reference_file", "protein_folder", "peptide_folder", "output_folder"]
    missing = [k for k in required if not str(params.get(k, "")).strip()]
    if missing:
        return jsonify({"ok": False, "error": "缺少参数: " + ", ".join(missing)}), 400
    ref_file = str(params.get("reference_file") or "").strip()
    if not os.path.isfile(ref_file):
        return jsonify({"ok": False, "error": f"参考文件不存在: {ref_file}"}), 400
    if not ref_file.lower().endswith(OUTPUT_EXTENSIONS):
        return jsonify({"ok": False, "error": f"参考文件必须是 .xlsx 格式: {ref_file}"}), 400
    for k in ["protein_folder", "peptide_folder", "output_folder"]:
        if not os.path.isdir(str(params.get(k))):
            return jsonify({"ok": False, "error": f"目录不存在: {params.get(k)}"}), 400
    try:
        tolerance = float(params.get("tolerance", CFG.DEFAULT_TOLERANCE))
        if tolerance <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "容差必须为正数"}), 400

    t = threading.Thread(target=execute_pipeline, args=(params,), daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/table")
def api_table():
    """Render one result Excel as JSON rows (limit 200)."""
    path = (request.args.get("file") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "missing file"}), 400
    logic = request.args.get("status_logic") or ""
    # security: only allow paths under a run output dir we know, or existing files
    if not os.path.exists(path):
        return jsonify({"ok": False, "error": "文件不存在（尚未生成或已被移动）"}), 404
    data = df_to_table(path, status_logic=logic if logic in ("step1", "step2", "step3", "step4") else None)
    return jsonify({"ok": True, **data})


@app.route("/api/runs")
def api_runs():
    """Run-record summary: given output_dir, read run_log.json (latest run)."""
    out_dir = (request.args.get("output_dir") or "").strip()
    if not out_dir or not os.path.isdir(out_dir):
        return jsonify({"ok": False, "error": "输出目录不存在"}), 404
    log_file = os.path.join(out_dir, CFG.FILENAME_RUN_LOG)
    if not os.path.exists(log_file):
        return jsonify({"ok": True, "run": None, "output_dir": out_dir})
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            run = json.load(f)
        return jsonify({"ok": True, "run": run, "output_dir": out_dir})
    except Exception as e:
        return jsonify({"ok": False, "error": f"读取 run_log 失败: {e}"}), 500


@app.route("/api/open_output", methods=["POST"])
def api_open_output():
    data = request.get_json(force=True, silent=True) or {}
    folder = (data.get("output_dir") or "").strip()
    if folder and os.path.isdir(folder):
        try:
            os.startfile(folder)  # noqa
            return jsonify({"ok": True, "message": folder})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": False, "error": "输出文件夹不存在"}), 400


# ============================================================
#  DSH (DeepSeek Harness) AI 拉起：静默启动 + 自动开浏览器 + 数据指路
# ============================================================
# DSH 端口 / 权限模式 / 产物位置均可配置（config.default.yaml 的 dsh.* 分节，
# 亦可在 Web 界面「设置」页修改，写入 config.local.yaml）
DSH_DEFAULT_PERMISSION_MODE = "workspace-write"
DSH_CONTEXT_FILENAME = "DSH_CONTEXT.md"
DSH_WEB_LOG_FILENAME = "dsh_web.log"


def _dsh_port():
    return CFG.normalize_port(CFG.get("dsh.port", 3080), 3080) or 3080


def _dsh_permission_mode():
    return CFG.normalize_permission_mode(CFG.get("dsh.permission_mode", DSH_DEFAULT_PERMISSION_MODE))


def _dsh_context_path():
    return os.path.join(AP.RUNTIME_DIR, DSH_CONTEXT_FILENAME)


def _dsh_log_path():
    return os.path.join(AP.LOGS_DIR, DSH_WEB_LOG_FILENAME)


def _dsh_url(port=None):
    return "http://127.0.0.1:%d" % (port or _dsh_port())


def _port_open(port, timeout=0.6):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _find_dsh_cmd():
    dsh_cmd = shutil.which("dsh") or shutil.which("dsh.cmd")
    if dsh_cmd:
        return dsh_cmd
    # fallback: npm global root + @deepseek-ai/dsh
    npm_exe = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm_exe:
        return ""
    try:
        proc = subprocess.run(["cmd", "/c", f'"{npm_exe}" root -g'],
                              capture_output=True, text=True, timeout=20)
        if proc.returncode == 0:
            global_root = (proc.stdout or "").strip()
            if global_root:
                cand = os.path.join(global_root, "dsh.cmd")
                if os.path.isfile(cand):
                    return cand
    except Exception:
        pass
    return ""


def _write_dsh_context_md(output_dir_hint=""):
    """把『项目代码 + 本轮运行数据』的位置写成一个 markdown 指路牌，
    放在工程目录（DSH 工作区）内，DSH 的 AI 可直接读取。"""
    try:
        out_dir = (output_dir_hint or RUN_CTX.output_dir or "").strip()
        lines = [
            "# DSH 蛋白质鉴定系统 - 当前会话上下文（自动生成，供 AI 审查/问答定位材料）",
            "",
            f"- 项目根目录（工作区）: {BASE_DIR}",
            f"- 最近运行输出目录: {out_dir if out_dir else '（未检测到运行记录，可能尚未运行四步流水线）'}",
        ]
        run_log = os.path.join(out_dir, CFG.FILENAME_RUN_LOG) if out_dir else ""
        if run_log and not os.path.isfile(run_log):
            run_log = ""
        lines.append(f"- run_log.json: {run_log if run_log else '（不存在）'}")
        lines.append("")
        lines.append("## 四步结果文件（若输出目录存在）")
        if out_dir and os.path.isdir(out_dir):
            steps = [
                ("Step1 蛋白初筛", CFG.FILENAME_PROTEIN_MATCH),
                ("Step2 肽段爬取", CFG.FILENAME_PEPTIDE_CRAWL),
                ("Step3 肽段匹配", CFG.FILENAME_PEPTIDE_MATCH),
                ("Step4 蛋白分析", CFG.FILENAME_PROTEIN_ANALYSIS),
                ("蛋白重复矩阵", CFG.FILENAME_PROTEIN_REPEAT),
            ]
            for label, fname in steps:
                p = os.path.join(out_dir, fname)
                lines.append(f"- [{label}] {p} {'（存在）' if os.path.isfile(p) else '（不存在）'}")
        else:
            lines.append("- （无）")
        lines.append("")
        lines.append("## 给 AI 的执行提示")
        lines.append("- 用户要求『代码审查/读代码』：阅读项目根目录下的 web_app.py、config.py、")
        lines.append("  protein_matcher_core.py / peptide_crawler_core.py / peptide_matcher_core.py /")
        lines.append("  protein_analyzer_core.py、static/js/app.js、templates/index.html 等。")
        lines.append("- 用户要求『数据问答/结果总结』：优先读取 run_log.json 以及上面标记为（存在）的结果 Excel。")
        lines.append("- 本文件在点击「AI 分析」时自动生成；若输出目录为空，说明还没有本轮运行数据，请如实告知用户。")
        md_path = _dsh_context_path()
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return md_path
    except Exception:
        return ""


def _append_dsh_log(text):
    try:
        with open(_dsh_log_path(), "a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


@app.route("/api/ai", methods=["POST"])
def api_ai():
    """拉起 DSH Web：
    1) 检测 node/npm/dsh；
    2) 已在运行 -> 复用并自动打开浏览器；
    3) 未运行 -> 静默后台启动（工作目录=工程目录，开放文件访问权限），
       写 DSH_CONTEXT.md 指路牌，等待端口就绪后返回 URL 由前端 window.open；
    4) 失败 -> 返回明确错误（页面可见）。"""
    data = request.get_json(silent=True) or {}
    output_dir_hint = (data.get("output_dir") or "").strip()

    node_exe = shutil.which("node") or shutil.which("node.exe")
    npm_exe = shutil.which("npm") or shutil.which("npm.cmd")
    if not node_exe or not npm_exe:
        return jsonify({
            "ok": False, "code": "NO_NODE",
            "message": ("未检测到 Node.js / npm。DeepSeek Harness (DSH) 需要 Node.js 运行环境。\n"
                        "请先安装 Node.js LTS：\n"
                        "1. 从 https://nodejs.org 下载 LTS 版本并安装（保持默认选项）\n"
                        "2. 重启本服务后再次点击「AI 分析」"),
            "open_url": "https://nodejs.org",
        })

    dsh_cmd = _find_dsh_cmd()
    if not dsh_cmd:
        return jsonify({
            "ok": False, "code": "NO_DSH",
            "message": ("未检测到 DeepSeek Harness (DSH)。\n"
                        "请打开一个新的「命令提示符」窗口执行：\n\n"
                        "  npm install -g @deepseek-ai/dsh\n\n"
                        "安装完成后重新点击「AI 分析」即可拉起 DSH Web 界面。"),
        })

    url = _dsh_url()

    # 1) 已在运行 -> 直接复用，不再重复启动
    if _port_open(_dsh_port()):
        _write_dsh_context_md(output_dir_hint)
        log("检测到 DSH Web 已在运行，直接打开 " + url)
        return jsonify({"ok": True, "first": False, "url": url,
                        "message": "检测到 DSH Web 已在运行，直接为您打开。"})

    # 2) 生成数据指路牌 + 静默后台启动
    try:
        md = _write_dsh_context_md(output_dir_hint)
        env = dict(os.environ)
        # 文件访问权限可配置，默认收紧为 workspace-write；
        # 若需让 DSH 读取输出目录之外的文件，可在 Web 界面「设置」页调整。
        env["DSH_PERMISSION_MODE"] = _dsh_permission_mode()
        # 子进程统一 UTF-8，避免中文与全角路径乱码
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        log_path = _dsh_log_path()
        _append_dsh_log(f"\n===== {datetime.now().isoformat(timespec='seconds')} launch: {dsh_cmd} web (cwd={BASE_DIR}) =====\n")
        stdout_f = open(log_path, "a", encoding="utf-8")
        # 列表参数直接调用，去掉 shell=True 下的引号拼接写法；
        # Windows 上 dsh 一般是 dsh.cmd，需要经 cmd /c 执行。
        if dsh_cmd.lower().endswith((".cmd", ".bat")):
            launch_cmd = ["cmd", "/c", dsh_cmd, "web"]
        else:
            launch_cmd = [dsh_cmd, "web"]
        proc = subprocess.Popen(
            launch_cmd,
            shell=False,
            cwd=BASE_DIR,
            env=env,
            stdout=stdout_f,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        # 让 stdout_f 在子进程退出后自然关闭
        threading.Timer(1.0, lambda f=stdout_f: f.flush()).start()
    except Exception as e:
        log(f"启动 DSH 失败: {e}")
        return jsonify({"ok": False, "code": "LAUNCH_FAIL",
                        "error": f"启动 DSH 失败：{e}"}), 500

    # 3) 等端口就绪（最多约 25 秒）
    deadline = time.time() + 25
    while time.time() < deadline:
        if _port_open(_dsh_port()):
            log(f"DSH Web 已启动: {url}" + (f"，上下文指路牌: {md}" if md else ""))
            return jsonify({"ok": True, "first": True, "url": url,
                            "message": "DSH Web 已启动，正在为您打开。"})
        if proc.poll() is not None:
            break
        time.sleep(0.5)

    # 4) 启动失败/超时 -> 把日志尾部带回给前端
    tail = ""
    try:
        with open(_dsh_log_path(), "r", encoding="utf-8") as rf:
            tail = (rf.read() or "")[-1200:]
    except Exception:
        pass
    log(f"DSH 启动失败，日志尾部: {tail[-300:]}")
    return jsonify({"ok": False, "code": "LAUNCH_FAIL",
                    "error": f"DSH 启动超时或进程提前退出（详见 {_dsh_log_path()}）",
                    "detail": tail}), 500


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    log("收到退出请求，服务即将关闭 ...")

    def _do_shutdown():
        time.sleep(0.5)
        try:
            os._exit(0)
        except Exception:
            pass

    threading.Thread(target=_do_shutdown, daemon=True).start()
    return jsonify({"ok": True, "message": "服务正在关闭"})


# ============================================================
#  DSH 模型配置（自定义 OpenAI 兼容网关 -> llm-pi-ai.providers）
# ============================================================
@app.route("/api/dsh/providers", methods=["GET"])
def api_dsh_providers():
    """列出已配置路由（含密钥存在性，不含密钥值）与当前默认模型。"""
    try:
        return jsonify({
            "ok": True,
            "providers": DSCFG.list_providers(),
            "default": DSCFG.get_default_model(),
            "settingsPath": DSCFG.settings_path(),
            "credentialsPath": DSCFG.credentials_path(),
        })
    except Exception as e:
        log(f"DSH 模型配置列表失败: {e}")
        return jsonify({"ok": False, "error": f"读取配置失败: {e}"}), 500


@app.route("/api/dsh/discover", methods=["POST"])
def api_dsh_discover():
    """连通性 / 模型发现：GET {baseURL}/models + Bearer，返回候选模型。"""
    body = request.get_json(silent=True) or {}
    base_url = (body.get("base_url") or "").strip()
    api_key = (body.get("api_key") or "").strip()
    result = DSCFG.discover_models(base_url, api_key)
    log(f"DSH 模型发现: {base_url} -> {result.get('ok')}")
    return jsonify(result), 200 if result.get("ok") else 400


@app.route("/api/dsh/providers/save", methods=["POST"])
def api_dsh_provider_save():
    """保存一条路由：合并 llm-pi-ai.providers.<route>；明文 Key 只写 .credentials.yaml。"""
    body = request.get_json(silent=True) or {}
    try:
        result = DSCFG.save_provider(body)
        log(f"DSH 模型配置已保存: {result.get('route')}")
        return jsonify(result)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        log(f"DSH 模型配置保存失败: {e}")
        return jsonify({"ok": False, "error": f"保存失败: {e}"}), 500


@app.route("/api/dsh/providers/delete", methods=["POST"])
def api_dsh_provider_delete():
    """删除一条用户新增路由（含对应凭据引用）。"""
    body = request.get_json(silent=True) or {}
    route = (body.get("route") or "").strip()
    if not route:
        return jsonify({"ok": False, "error": "缺少路由键 route"}), 400
    try:
        result = DSCFG.delete_provider(route)
        log(f"DSH 模型配置已删除: {route}")
        return jsonify(result)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        log(f"DSH 模型配置删除失败: {e}")
        return jsonify({"ok": False, "error": f"删除失败: {e}"}), 500


@app.route("/api/dsh/providers/default", methods=["POST"])
def api_dsh_provider_default():
    """把 agent-default-model 指向某条已配置路由。"""
    body = request.get_json(silent=True) or {}
    route = (body.get("route") or "").strip()
    model = (body.get("model") or "").strip()
    if not route or not model:
        return jsonify({"ok": False, "error": "缺少路由键或模型 id"}), 400
    try:
        result = DSCFG.set_default(route, model)
        log(f"DSH 默认模型已设为: {route} / {model}")
        return jsonify(result)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        log(f"设置默认模型失败: {e}")
        return jsonify({"ok": False, "error": f"设置失败: {e}"}), 500


@app.route("/api/dsh/providers/restore_default", methods=["POST"])
def api_dsh_provider_restore():
    """恢复 DeepSeek 默认：清空用户层 llm-pi-ai.providers 与对应 refs。"""
    try:
        result = DSCFG.restore_default()
        log("DSH 模型配置已恢复 DeepSeek 默认")
        return jsonify(result)
    except Exception as e:
        log(f"恢复默认失败: {e}")
        return jsonify({"ok": False, "error": f"恢复失败: {e}"}), 500


# ============================================================
#  Entry point
# ============================================================
# ============================================================
#  系统设置（Web 界面「设置」页）：读写 config.local.yaml
# ============================================================
PATH_SETTING_KEYS = ("runtime_dir", "data_dir", "output_dir", "logs_dir",
                     "temp_dir", "sessions_dir", "backups_dir")


@app.route("/api/settings", methods=["GET"])
def api_settings():
    """当前生效配置 + 运行时路径 + 可选项，供「设置」页渲染。"""
    return jsonify({
        "ok": True,
        "settings": {
            "web": {
                "port": CFG.normalize_port(CFG.get("web.port", 0), 0),
                "host": CFG.get("web.host", "127.0.0.1"),
                "open_browser": bool(CFG.get("web.open_browser", True)),
            },
            "dsh": {
                "port": CFG.normalize_port(CFG.get("dsh.port", 3080), 3080),
                "permission_mode": CFG.normalize_permission_mode(
                    CFG.get("dsh.permission_mode", DSH_DEFAULT_PERMISSION_MODE)),
            },
        },
        "runtime": AP.as_dict(),
        "runtime_root": AP.RUNTIME_DIR,
        "permission_modes": list(CFG.DSH_PERMISSION_MODES),
        "local_config_file": AP.LOCAL_CONFIG_FILE,
        "default_config_file": AP.DEFAULT_CONFIG_FILE,
        "python": sys.version.split()[0],
    })


@app.route("/api/settings/save", methods=["POST"])
def api_settings_save():
    """保存设置到 config.local.yaml（不改动 config.default.yaml）。"""
    data = request.get_json(silent=True) or {}
    errors = []

    web_port = CFG.normalize_port(data.get("web_port", ""), None)
    if web_port is None:
        errors.append("Web 端口需为 0~65535 的整数（0 = 自动选择）")

    dsh_port = CFG.normalize_port(data.get("dsh_port", ""), None)
    if not dsh_port:
        errors.append("DSH 端口需为 1~65535 的整数")

    mode = str(data.get("dsh_permission_mode", "")).strip()
    if mode not in CFG.DSH_PERMISSION_MODES:
        errors.append("DSH 权限模式不合法")

    paths = {}
    for key in PATH_SETTING_KEYS:
        if key in data:
            paths[key] = str(data.get(key) or "").strip()

    if errors:
        return jsonify({"ok": False, "error": "；".join(errors)}), 400

    old_web_port = CFG.normalize_port(CFG.get("web.port", 0), 0)
    updates = {
        "web": {
            "port": web_port,
            "open_browser": bool(data.get("open_browser", True)),
        },
        "dsh": {
            "port": dsh_port,
            "permission_mode": mode,
        },
    }
    if paths:
        updates["paths"] = paths

    try:
        saved_to = CFG.save_local_settings(updates)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    restart_required = bool(paths) or (web_port != old_web_port)
    msg = "设置已保存到 %s。" % saved_to
    msg += "端口与路径改动需重启服务生效。" if restart_required else "DSH 相关设置已即时生效。"
    return jsonify({"ok": True, "saved_to": saved_to,
                    "restart_required": restart_required, "message": msg})


def parse_args(argv):
    port = None
    no_browser = False
    i = 1
    while i < len(argv):
        if argv[i] == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])
            i += 2
        elif argv[i] == "--no-browser":
            no_browser = True
            i += 1
        else:
            i += 1
    return port, no_browser


def _resolve_port(preferred=0):
    """端口可配置 + 占用自动探测：配置端口可用则沿用，否则自动挑空闲端口。"""
    preferred = CFG.normalize_port(preferred, 0)
    if preferred and free_port(preferred) == preferred:
        return preferred
    if preferred:
        print(f"  端口 {preferred} 已被占用，自动改用其他端口")
    return free_port(5000)


def main():
    # 控制台输出统一 errors=replace，避免非 UTF-8 代码页下中文/全角路径报错
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(errors="replace")
        except Exception:
            pass

    CFG.load_settings()
    AP.ensure_runtime_dirs()

    port, no_browser = parse_args(sys.argv)
    if not port:
        port = _resolve_port(CFG.get("web.port", 0))
    host = str(CFG.get("web.host", "127.0.0.1") or "127.0.0.1")
    url = f"http://127.0.0.1:{port}/"

    print("=" * 56)
    print("  Protein Data Processing System - Web UI")
    print(f"  URL    : {url}")
    print(f"  Runtime: {AP.RUNTIME_DIR}")
    print("  按 Ctrl+C 停止服务，或点击页面右上角「退出服务」")
    print("=" * 56)

    if not no_browser and CFG.get("web.open_browser", True) is not False:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    # Production-ish local server: no reloader (background threads would double-run)
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
