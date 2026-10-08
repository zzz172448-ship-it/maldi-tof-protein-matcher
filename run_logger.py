# -*- coding: utf-8 -*-
# 基于质谱峰的蛋白质匹配软件 V1.0
# Copyright (c) 2026 张葛阳
# 本软件为独立开发，未使用第三方开源代码
"""Run Logger - structured JSON log for AI analysis"""
import json, os
from datetime import datetime
import sys, os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from config import FILENAME_RUN_LOG

class RunLogger:
    def __init__(self, output_folder):
        self.output_folder = output_folder
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = _os.path.join(output_folder, FILENAME_RUN_LOG)
        self.run_data = {"run_id": self.run_id, "start_time": datetime.now().isoformat(), "steps": [], "summary": {}}

    def log_step(self, step_num, step_name, input_data=None, output_data=None, api_calls=None, duration_sec=None, success=True, error=None):
        entry = {"step": step_num, "name": step_name, "timestamp": datetime.now().isoformat(), "success": success}
        if input_data is not None: entry["input"] = input_data
        if output_data is not None: entry["output"] = output_data
        if api_calls is not None: entry["api_calls"] = api_calls
        if duration_sec is not None: entry["duration_sec"] = duration_sec
        if error is not None: entry["error"] = str(error)
        self.run_data["steps"].append(entry)
        self._save()
        return entry

    def set_summary(self, d): self.run_data["summary"] = d; self._save()
    def set_input_params(self, d): self.run_data["input_params"] = d; self._save()
    def _save(self):
        try:
            os.makedirs(self.output_folder, exist_ok=True)
            with open(self.log_file, "w", encoding="utf-8") as f:
                json.dump(self.run_data, f, ensure_ascii=False, indent=2)
        except Exception as e: print(f"[RunLogger] Save failed: {e}")
    def get_log_path(self): return self.log_file

if __name__ == "__main__":
    lg = RunLogger("/tmp/test")
    lg.set_input_params({"ref": "test.xlsx"})
    lg.log_step(1, "Test Step", input_data={"a": 1}, output_data={"b": 2}, duration_sec=1.0)
    lg.set_summary({"total": 10})
    print("Log:", lg.get_log_path())