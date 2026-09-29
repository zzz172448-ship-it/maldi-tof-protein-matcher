# -*- coding: utf-8 -*-
"""Step 1: Protein initial matching - Reference Mass vs Experiment m/z"""
import pandas as pd, os, glob, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import FILENAME_PROTEIN_MATCH

def find_column(df, keywords):
    for col in df.columns:
        col_str = str(col)
        if any(k in col_str for k in keywords):
            return col
    return None

def read_excel_smart(file_path):
    for header_row in range(5):
        try:
            df = pd.read_excel(file_path, header=header_row)
            if find_column(df, ["Mass"]) or find_column(df, ["m/z"]):
                return df, header_row
        except: continue
    return pd.read_excel(file_path), 0

def match_proteins(reference_file, experiment_folder, output_folder, tolerance=0.1, tolerance_unit="ppm"):
    """
    Match reference Mass values against experiment m/z values.
    reference_file: Excel file with Mass column (e.g. 大黄鱼蛋白质.xlsx)
    experiment_folder: folder containing experiment Excel files with m/z column
    output_folder: where to save results
    tolerance: numeric tolerance value
    tolerance_unit: "ppm", "Da", or "%"
    """
    os.makedirs(output_folder, exist_ok=True)
    
    # Read reference data
    ref_df, hr = read_excel_smart(reference_file)
    mass_col = find_column(ref_df, ["Mass"])
    if not mass_col:
        raise ValueError(f"No Mass column found in {reference_file}. Columns: {ref_df.columns.tolist()}")
    
    ref_masses = []
    for idx, row in ref_df.iterrows():
        try:
            m = float(row[mass_col])
            if tolerance_unit == "ppm":
                tol = m * tolerance / 1e6
            elif tolerance_unit == "Da":
                tol = tolerance
            else:  # %
                tol = m * tolerance / 100
            ref_masses.append({"mass": m, "tol": tol, "row": row.to_dict()})
        except (ValueError, TypeError): continue
    
    print(f"Reference: {len(ref_masses)} mass values loaded")
    
    # Read experiment files
    exp_files = sorted(glob.glob(os.path.join(experiment_folder, "*.xlsx")))
    exp_files = [f for f in exp_files if not os.path.basename(f).startswith("~$")]
    print(f"Experiment files: {len(exp_files)}")
    
    all_results = []
    for exp_file in exp_files:
        fname = os.path.basename(exp_file)
        exp_df, ehr = read_excel_smart(exp_file)
        mz_col = find_column(exp_df, ["m/z", "mz"])
        if not mz_col:
            print(f"  Skip {fname}: no m/z column")
            continue
        
        exp_df["__mz_num__"] = pd.to_numeric(exp_df[mz_col], errors="coerce")
        valid_idx = exp_df.index[exp_df["__mz_num__"].notna()]
        print(f"  {fname}: {len(valid_idx)} m/z values")
        
        for idx in valid_idx:
            mz = float(exp_df.at[idx, "__mz_num__"])
            for ref in ref_masses:
                if abs(mz - ref["mass"]) <= ref["tol"]:
                    # 全量透传实验峰原始行（m/z/time/Intens./SN/... 等全部峰属性列动态带出）
                    matched = exp_df.loc[idx].drop(labels=["__mz_num__"]).to_dict()
                    matched["Experiment_File"] = fname
                    matched["Experiment_mz"] = mz
                    matched["Reference_Mass"] = ref["mass"]
                    matched["Tolerance"] = ref["tol"]
                    matched["Tolerance_Unit"] = tolerance_unit
                    # 参考蛋白行整行带出（保留原列名，同名冲突以参考行为准）
                    matched.update(ref["row"])
                    all_results.append(matched)
                    # 不 break：保留同一实验峰命中的所有参考蛋白（与旧版一致）
    
    if all_results:
        result_df = pd.DataFrame(all_results)
        out_path = os.path.join(output_folder, FILENAME_PROTEIN_MATCH)
        result_df.to_excel(out_path, index=False)
        print(f"Saved: {out_path} ({len(result_df)} matches)")
        return out_path, len(result_df)
    else:
        print("No matches found")
        return None, 0


if __name__ == "__main__":
    # 命令行自测入口（不依赖任何本机路径，默认输出到工程内 runtime/output）
    # 用法： python protein_matcher_core.py --protein 蛋白列表.xlsx --data 实验数据目录
    import argparse
    import logging
    import os

    import app_paths

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Step1 蛋白初筛（命令行自测）")
    parser.add_argument("--protein", required=True, help="蛋白质列表 Excel 路径")
    parser.add_argument("--data", required=True, help="实验数据目录")
    parser.add_argument("--out", default=os.path.join(app_paths.OUTPUT_DIR, "step1"),
                        help="输出目录（默认 runtime/output/step1）")
    parser.add_argument("--tolerance", type=float, default=50.0)
    parser.add_argument("--tolerance-unit", default="ppm")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    match_proteins(args.protein, args.data, args.out,
                   tolerance=args.tolerance, tolerance_unit=args.tolerance_unit)