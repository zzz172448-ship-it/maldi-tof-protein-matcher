# -*- coding: utf-8 -*-
# 基于质谱峰的蛋白质匹配软件 V1.0
# Copyright (c) 2026 张葛阳
# 本软件为独立开发，未使用第三方开源代码
"""UniProt REST API Helper"""
import json, requests, urllib3, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import UNIPROT_BASE, UNIPROT_TIMEOUT
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class UniProtHelper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})

    def get_protein_info(self, protein_id):
        try:
            resp = self.session.get(f"{UNIPROT_BASE}/uniprotkb/{protein_id}.json", timeout=UNIPROT_TIMEOUT, verify=False)
            if resp.status_code != 200: return None
            data = resp.json()
            info = {"entry": data.get("primaryAccession", protein_id), "entry_name": data.get("uniProtkbId", ""),
                    "organism": data.get("organism", {}).get("scientificName", ""),
                    "taxon_id": data.get("organism", {}).get("taxonId", ""),
                    "sequence_length": data.get("sequence", {}).get("length", 0),
                    "uni_parc_id": data.get("extraAttributes", {}).get("uniParcId", ""),
                    "has_conflict": False, "has_variant": False, "has_ptm": False, "has_varsplice": False}
            for feat in data.get("features", []):
                t = feat.get("type", "")
                if t == "Variant": info["has_variant"] = True
                elif t == "Modified residue": info["has_ptm"] = True
            for c in data.get("comments", []):
                for tx in c.get("texts", []):
                    if "conflict" in str(tx.get("value", "")).lower(): info["has_conflict"] = True
            return info
        except Exception: return None

    def get_sequence(self, protein_id):
        try:
            resp = self.session.get(f"{UNIPROT_BASE}/uniprotkb/{protein_id}.fasta", timeout=UNIPROT_TIMEOUT, verify=False)
            if resp.status_code != 200: return protein_id, None
            lines = resp.text.strip().split("\n")
            if len(lines) >= 2: return protein_id, "".join(lines[1:]).replace("\n", "").replace(" ", "")
            return protein_id, None
        except Exception: return protein_id, None

    def get_uniparc_sequence(self, protein_id):
        """UniParc 归档序列兜底。

        UniProtKB 已删除(DELETED / Inactive)条目的主库 fasta 为空，
        但 uniprotkb/{id}.json 仍返回 extraAttributes.uniParcId，
        可从 uniparc/{id}.fasta 取到删除前的历史序列。

        返回约定：
          - 序列字符串：归档序列可用
          - None：确定性无效（UniParc 确无归档序列，或序列不可用）
          - 抛 RuntimeError：瞬时故障（网络错误 / HTTP 非 200 / 响应损坏），
            调用方应重试而非判死
        """
        try:
            resp = self.session.get(
                f"{UNIPROT_BASE}/uniprotkb/{protein_id}.json",
                timeout=UNIPROT_TIMEOUT, verify=False)
        except Exception as e:
            raise RuntimeError(f"uniprotkb/{protein_id} network error: {e}") from e
        if resp.status_code != 200:
            raise RuntimeError(f"uniprotkb/{protein_id} HTTP {resp.status_code}")
        try:
            data = resp.json()
        except Exception as e:
            raise RuntimeError(f"uniprotkb/{protein_id} bad json: {e}") from e
        upid = (data.get("extraAttributes") or {}).get("uniParcId")
        if not upid:
            # JSON 正常返回但无归档 ID：该条目确实没有 UniParc 记录，属确定性无效
            return None
        try:
            fasta_resp = self.session.get(
                f"{UNIPROT_BASE}/uniparc/{upid}.fasta",
                timeout=UNIPROT_TIMEOUT, verify=False)
        except Exception as e:
            raise RuntimeError(f"uniparc/{upid} network error: {e}") from e
        if fasta_resp.status_code != 200:
            raise RuntimeError(f"uniparc/{upid} HTTP {fasta_resp.status_code}")
        if not fasta_resp.text.strip().startswith(">"):
            # 200 但内容不是 FASTA：该归档序列不可用，属确定性无效
            return None
        lines = fasta_resp.text.strip().split("\n")
        seq = "".join(lines[1:]).replace("\n", "").replace(" ", "")
        valid_chars = set("ACDEFGHIKLMNPQRSTVWY")
        if len(seq) < 20:
            return None
        seq_upper = seq.upper()
        valid_ratio = sum(1 for c in seq_upper if c in valid_chars) / len(seq_upper)
        return seq if valid_ratio >= 0.9 else None

    def search_by_taxon(self, taxon_id, size=100):
        try:
            resp = self.session.get(f"{UNIPROT_BASE}/uniprotkb/search", params={"query": f"organism_id:{taxon_id}", "format": "json", "size": size, "fields": "accession,entry_name,organism"}, timeout=UNIPROT_TIMEOUT, verify=False)
            if resp.status_code != 200: return []
            results = []
            for e in resp.json().get("results", []):
                org = e.get("organism", {})
                results.append({"accession": e.get("primaryAccession",""), "entry_name": e.get("uniProtkbId",""), "scientific_name": org.get("scientificName","")})
            return results
        except Exception: return []

if __name__ == "__main__":
    h = UniProtHelper()
    print(json.dumps(h.get_protein_info("P02649"), indent=2, ensure_ascii=False))