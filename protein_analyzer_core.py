# -*- coding: utf-8 -*-
"""Step 4: Protein analysis and formatting with coverage/peptide count stats"""
import pandas as pd, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import FILENAME_PROTEIN_ANALYSIS, MIN_PEPTIDE_MATCH, MIN_COVERAGE_PERCENT

def analyze_proteins(input_file, output_file):
    """
    Analyze peptide match results and generate summary report.
    Calculates: peptide match count, coverage %, detection frequency across samples.
    """
    df = pd.read_excel(input_file)
    print(f"Loaded {len(df)} peptide match records")
    
    # Calculate coverage per protein per file
    results = []
    for pid in df["Protein_ID"].unique():
        pid_data = df[df["Protein_ID"] == pid]
        
        # Peptide match count
        peptide_count = len(pid_data)
        
        # Coverage: position range / protein length estimate
        positions = []
        for pos in pid_data["position"].dropna():
            pos_str = str(pos)
            if "-" in pos_str:
                try:
                    start, end = pos_str.split("-")
                    positions.extend(range(int(start), int(end) + 1))
                except: pass
            else:
                try: positions.append(int(pos_str))
                except: pass
        
        coverage = 0.0
        if positions:
            coverage = (max(positions) - min(positions) + 1)
        
        # Detection count across experiment files
        detection_count = pid_data["Experiment_File"].nunique()
        total_files = df["Experiment_File"].nunique() if len(df) > 0 else 1
        
        # Confidence rating
        if peptide_count >= 5 and coverage >= 30:
            confidence = "★★★★★"
        elif peptide_count >= 3 and coverage >= 20:
            confidence = "★★★★☆"
        elif peptide_count >= 3 or coverage >= MIN_COVERAGE_PERCENT:
            confidence = "★★★☆☆"
        else:
            confidence = "★☆☆☆☆"
        
        results.append({
            "Protein_ID": pid,
            "Peptide_Count": peptide_count,
            "Coverage_%": round(coverage, 1),
            "Detected_In_Files": detection_count,
            "Total_Files": total_files,
            "Confidence": confidence,
        })
    
    result_df = pd.DataFrame(results)
    result_df = result_df.sort_values("Peptide_Count", ascending=False)
    result_df.to_excel(output_file, index=False)
    print(f"Saved: {output_file} ({len(result_df)} proteins)")
    return output_file, result_df


def build_repeat_matrix(input_file, output_file):
    """
    平移旧版 create_correct_format：按实验组统计每个蛋白的肽段重复情况。
    输入为 Step3 肽段匹配结果（列含 Experiment_File / Protein_ID / mass 或
    Theoretical_mass / m/z / position / peptide_sequence）。
    每个蛋白输出 5 行：[蛋白ID行(含组重复计数), mass行, m/z行, position行,
    peptide_sequence行]，实验组每 3 个一批后跟"重复次数"汇总列。
    """
    df = pd.read_excel(input_file)
    if "mass" not in df.columns and "Theoretical_mass" in df.columns:
        df["mass"] = df["Theoretical_mass"]
    required = ["Experiment_File", "Protein_ID", "mass", "m/z", "position", "peptide_sequence"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        # 与旧版 create_correct_format 一致：缺列时抛错（由调用方捕获并如实上报），
        # 不再静默返回 None 吞掉问题。
        raise ValueError(f"Repeat matrix: missing column(s) {missing} in {input_file}")

    experiment_files = sorted([f for f in df["Experiment_File"].dropna().unique()])
    group_names = [str(f).replace(".xlsx", "") for f in experiment_files]
    protein_ids = [p for p in df["Protein_ID"].dropna().unique()]
    if not experiment_files or not protein_ids:
        print("Repeat matrix: no experiment group / protein, skipped")
        return None, 0

    batches = [list(range(i, min(i + 3, len(experiment_files))))
               for i in range(0, len(experiment_files), 3)]

    # 预取每个 (protein, file) 的子表，避免大循环重复过滤
    per = {}
    for pid in protein_ids:
        for f in experiment_files:
            per[(pid, f)] = df[(df["Protein_ID"] == pid) & (df["Experiment_File"] == f)]

    counts = {pid: sum(len(per[(pid, f)]) for f in experiment_files) for pid in protein_ids}
    sorted_proteins = sorted(protein_ids, key=lambda p: counts[p], reverse=True)

    def fmt(vals):
        return ",".join(str(v) for v in vals) if vals else ""

    rows = []
    header = ["Protein_ID", ""]
    for b in batches:
        for gi in b:
            header.append(group_names[gi])
        header.append("重复次数")
    header.append("总重复 次数")
    rows.append(header)

    for pid in sorted_proteins:
        row1 = [pid, ""]
        for b in batches:
            for _ in b:
                row1.append("")
            row1.append(sum(len(per[(pid, experiment_files[gi])]) for gi in b))
        row1.append(counts[pid])
        rows.append(row1)

        for field, label in [("mass", "mass"), ("m/z", "m/z"),
                             ("position", "position"), ("peptide_sequence", "peptide_sequence")]:
            r = ["", label]
            for b in batches:
                for gi in b:
                    r.append(fmt(per[(pid, experiment_files[gi])][field].tolist()))
                r.append("")
            r.append("")
            rows.append(r)

    result_df = pd.DataFrame(rows)
    result_df.to_excel(output_file, index=False, header=False)
    print(f"Saved repeat matrix: {output_file} "
          f"({len(sorted_proteins)} proteins x {len(experiment_files)} groups)")
    return output_file, len(rows) - 1


if __name__ == "__main__":
    # 命令行自测入口（不依赖任何本机路径，默认输出到工程内 runtime/output）
    # 用法： python protein_analyzer_core.py --crawler 爬取结果.xlsx
    import argparse
    import logging
    import os

    import app_paths
    import config as CFG

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Step4 蛋白分析（命令行自测）")
    parser.add_argument("--crawler", required=True, help="Step2 爬取结果 Excel 路径")
    parser.add_argument("--out", default=os.path.join(app_paths.OUTPUT_DIR, CFG.FILENAME_PROTEIN_ANALYSIS),
                        help="输出 Excel（默认 runtime/output/protein_analysis_result.xlsx）")
    args = parser.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    analyze_proteins(args.crawler, args.out)