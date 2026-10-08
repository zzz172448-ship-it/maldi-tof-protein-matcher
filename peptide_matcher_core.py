# -*- coding: utf-8 -*-
# 基于质谱峰的蛋白质匹配软件 V1.0
# Copyright (c) 2026 张葛阳
# 本软件为独立开发，未使用第三方开源代码
"""Step 3: Peptide-level matching - Theoretical mass vs Experiment m/z"""
import pandas as pd, os, glob, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import FILENAME_PEPTIDE_MATCH, MIN_PEPTIDE_MATCH, MIN_COVERAGE_PERCENT

def find_column(df, keywords):
    for col in df.columns:
        if any(k in str(col) for k in keywords): return col
    return None

def read_excel_smart(file_path):
    for header_row in range(5):
        try:
            df = pd.read_excel(file_path, header=header_row)
            if find_column(df, ["m/z", "mz"]): return df, header_row
        except: continue
    return pd.read_excel(file_path), 0

def match_peptides(theoretical_file, experiment_folder, output_file, tolerance=0.1, tolerance_unit="ppm"):
    """
    Match theoretical peptide masses against experiment m/z values.
    theoretical_file: Excel with Protein ID, mass, position, peptide sequence columns
    experiment_folder: folder with experiment m/z Excel files
    output_file: where to save results
    """
    # Read theoretical data
    theo_df, _ = read_excel_smart(theoretical_file)
    theo_df = theo_df.copy()
    theo_df["mass"] = pd.to_numeric(theo_df["mass"], errors="coerce")
    theo_df = theo_df.dropna(subset=["mass"])
    
    # Calculate tolerance
    if tolerance_unit == "ppm":
        theo_df["tol"] = theo_df["mass"] * tolerance / 1e6
    elif tolerance_unit == "Da":
        theo_df["tol"] = tolerance
    else:
        theo_df["tol"] = theo_df["mass"] * tolerance / 100
    
    print(f"Theoretical peptides: {len(theo_df)}")
    
    # Read experiment files
    exp_files = sorted(glob.glob(os.path.join(experiment_folder, "*.xlsx")))
    exp_files = [f for f in exp_files if not os.path.basename(f).startswith("~$")]
    print(f"Experiment files: {len(exp_files)}")
    
    all_results = []
    for exp_file in exp_files:
        fname = os.path.basename(exp_file)
        exp_df, _ = read_excel_smart(exp_file)
        mz_col = find_column(exp_df, ["m/z", "mz"])
        if not mz_col: continue
        
        mz_vals = pd.to_numeric(exp_df[mz_col], errors="coerce").dropna()
        for mz in mz_vals:
            for _, theo in theo_df.iterrows():
                if abs(mz - theo["mass"]) <= theo["tol"]:
                    all_results.append({
                        "Experiment_File": fname,
                        "m/z": round(mz, 6),
                        "Theoretical_mass": theo["mass"],
                        "Protein_ID": theo.get("Protein ID", ""),
                        "position": theo.get("position", ""),
                        "peptide_sequence": theo.get("peptide sequence", ""),
                        "#MC": theo.get("#MC", ""),
                        "modifications": theo.get("modifications", ""),
                        "mass_tolerance_Da": round(abs(mz - theo["mass"]), 6),
                        "mass_tolerance_ppm": round(abs(mz - theo["mass"]) / theo["mass"] * 1e6, 2) if theo["mass"] else 0,
                    })
                    # 不 break：保留同一实验峰容差内命中的所有理论肽段（与其他处理模块保持一致）
    
    if all_results:
        result_df = pd.DataFrame(all_results)
        result_df.to_excel(output_file, index=False)
        print(f"Saved: {output_file} ({len(result_df)} matches)")
        return output_file, len(result_df), result_df
    else:
        print("No matches found")
        return None, 0, None


if __name__ == "__main__":
    # 命令行自测入口（不依赖任何本机路径，默认输出到工程内 runtime/output）
    # 用法： python peptide_matcher_core.py --crawler 爬取结果.xlsx --data 实验数据目录
    import argparse
    import logging
    import os

    import app_paths
    import config as CFG

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Step3 肽段匹配（命令行自测）")
    parser.add_argument("--crawler", required=True, help="Step2 爬取结果 Excel 路径")
    parser.add_argument("--data", required=True, help="实验数据目录")
    parser.add_argument("--out", default=os.path.join(app_paths.OUTPUT_DIR, CFG.FILENAME_PEPTIDE_MATCH),
                        help="输出 Excel（默认 runtime/output/peptide_match_result.xlsx）")
    parser.add_argument("--tolerance", type=float, default=50.0)
    parser.add_argument("--tolerance-unit", default="ppm")
    args = parser.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    match_peptides(args.crawler, args.data, args.out,
                   tolerance=args.tolerance, tolerance_unit=args.tolerance_unit)