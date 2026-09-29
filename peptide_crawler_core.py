# -*- coding: utf-8 -*-
"""Peptide data crawler - Expasy PeptideMass with URL+POST fallback

修复要点(对照旧版 protein_crawler_core.py 与 Expasy 真实页面):
  A1 单蛋白级重试: URL/POST 各自 attempt 2 次, 失败 sleep(2) 后重试
  A2 每蛋白请求前随机延迟恢复 0-5s
  A3 错误页三词识别: error / not found / problem
  A4 POST 降频: 主页 form 模板只 GET 一次并复用(失败才刷新重试一次)
  A5 调试日志: 状态码 / 响应长度 / 错误页片段 / 解析条数
  D1 缓存命中: 蛋白级 + 参数指纹(params_fp), 同参数不重复爬
  D2 增量落盘: 每轮结束保存一次 cache
  早停: 连续 3 轮失败集合无任何改善则提前结束(避免 50 轮空转 14 分钟)
"""
import pandas as pd, requests, re, time, random, urllib3, logging
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from expasy_params import ExpasyParams, EXPSY_BASE, EXPSY_PAGE
from uniprot_api_helper import UniProtHelper

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)

ERROR_MARKERS = ("error", "not found", "problem")
INVALID_MARKER = "not a valid uniprotkb identifier"
INVALID_RESPONSE = "INVALID_ID"

CACHE_COLS = ["Protein ID", "mass", "position", "#MC", "modifications",
              "peptide sequence", "params_fp"]


def _looks_like_error_page(text, probe_chars=3000):
    """错误页识别: 响应头部/正文是否含 error / not found / problem 标记。"""
    if not text:
        return True
    head = text[:probe_chars].lower()
    return any(m in head for m in ERROR_MARKERS)


def _looks_invalid(text):
    """无效 UniProtKB ID 识别: Expasy 错误页会把 UniProtKB 包进 <a> 链接,
    必须先剥离 HTML 标签再匹配标记(否则 'not a valid <a>uniprotkb</a> identifier' 断链失配)。"""
    if not text:
        return False
    clean = re.sub(r"<[^>]+>", "", text).lower()
    return INVALID_MARKER in clean


class PeptideCrawlerCore:
    def __init__(self, output_folder=".", cache_folder=None, log_callback=None):
        self.is_running = True
        self.log_message = log_callback or (lambda m: logger.info(m))
        self.cache_folder = cache_folder or os.path.join(output_folder, "cache")
        self.cache_file = os.path.join(self.cache_folder, "protein_cache.xlsx")
        # params_fp -> {protein_id: [record, ...]}
        self.cache_by_fp = {}
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        os.makedirs(self.cache_folder, exist_ok=True)
        # POST form 模板: 从主页解析出的默认控件值, 每蛋白只复用不再重复 GET 主页
        self._form_template = None
        # UniParc 归档序列兜底助手（已删 ID 的序列回退路径）
        self.uniprot_helper = UniProtHelper()
        self._load_cache()

    # ---------- cache ----------
    def _load_cache(self):
        try:
            if os.path.exists(self.cache_file):
                df = pd.read_excel(self.cache_file)
                for _, row in df.iterrows():
                    rec = row.to_dict()
                    fp = str(rec.get("params_fp", "") or "")
                    pid = str(rec.get("Protein ID", "") or "")
                    if not pid:
                        continue
                    key = f"{pid}_{rec.get('mass', '')}_{rec.get('peptide sequence', '')}"
                    self.cache_by_fp.setdefault(fp, {}).setdefault(pid, {})[key] = rec
                total = sum(len(v) for m in self.cache_by_fp.values() for v in m.values())
                self.log_message(f"Cache loaded: {total} records across {len(self.cache_by_fp)} param-fingerprint(s)")
        except Exception as e:
            self.log_message(f"Cache load error: {e}")

    def _save_cache(self):
        try:
            rows = []
            for fp, by_pid in self.cache_by_fp.items():
                for pid, recs in by_pid.items():
                    for rec in recs.values():
                        r = dict(rec)
                        r["params_fp"] = fp
                        r["Protein ID"] = pid
                        rows.append(r)
            if not rows:
                return
            df = pd.DataFrame(rows)
            for col in CACHE_COLS:
                if col not in df.columns:
                    df[col] = ""
            df = df[CACHE_COLS]
            df.to_excel(self.cache_file, index=False)
            self.log_message(f"Cache saved: {len(df)} records")
        except Exception as e:
            self.log_message(f"Cache save error: {e}")

    def _cache_hit(self, protein_id, fp):
        """按指纹命中: 同蛋白同参数直接复用缓存。"""
        recs = self.cache_by_fp.get(fp, {}).get(protein_id)
        return list(recs.values()) if recs else None

    # ---------- parse ----------
    def parse_peptide_data(self, html_content, protein_id):
        try:
            peptides = []
            pattern = r"<!--\s*([\d.]+)\|([^|]+)\|(\d+)\|([^|]*)\|\|\|\|\|([^>]+?)\s*-->"
            for match in re.findall(pattern, html_content):
                peptides.append({
                    "Protein ID": protein_id,
                    "mass": match[0].strip(),
                    "position": match[1].strip(),
                    "#MC": match[2].strip(),
                    "modifications": "",
                    "peptide sequence": match[4].strip()
                })
            return peptides
        except Exception as e:
            self.log_message(f"Parse error for {protein_id}: {e}")
            return []

    # ---------- Expasy requests ----------
    def fetch_via_url(self, protein_id, params, attempts=2):
        """Fast path: GET request with URL parameters.
        A1: attempt 2 次, 失败 sleep(2) 退避; A5: 状态码/长度/错误片段日志"""
        url_params = params.to_url_params()
        url_params["protein"] = protein_id
        for attempt in range(1, attempts + 1):
            if not self.is_running:
                return None
            try:
                resp = self.session.get(EXPSY_BASE, params=url_params, timeout=25, verify=False)
                status = resp.status_code
                length = len(resp.text)
                low = resp.text.lower()
                if _looks_invalid(resp.text):
                    self.log_message(f"[URL#{attempt}] {protein_id}: HTTP {status}, len={length}, INVALID UniProtKB ID (try UniParc fallback)")
                    return INVALID_RESPONSE
                if status == 200 and not _looks_like_error_page(resp.text):
                    peptides = self.parse_peptide_data(resp.text, protein_id)
                    self.log_message(f"[URL#{attempt}] {protein_id}: HTTP {status}, len={length}, parsed={len(peptides)}")
                    return peptides if peptides else None
                snippet = re.sub(r"\s+", " ", resp.text[:200])
                self.log_message(f"[URL#{attempt}] {protein_id}: HTTP {status}, len={length}, suspicious head={snippet!r}")
            except Exception as e:
                self.log_message(f"[URL#{attempt}] {protein_id}: exception {e}")
            if attempt < attempts:
                time.sleep(2)
        return None

    def _ensure_form_template(self):
        """A4: 主页 form 模板只 GET 一次(默认控件值), 后续蛋白复用。"""
        if self._form_template is not None:
            return self._form_template
        try:
            resp = self.session.get(EXPSY_PAGE, timeout=20, verify=False)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            form = soup.find("form", {"method": "POST", "action": re.compile(r"peptide-mass\.pl")})
            if not form:
                self.log_message("POST form template: form not found on page")
                return None
            template = {}
            for inp in form.find_all("input"):
                n = inp.get("name")
                if not n:
                    continue
                itype = inp.get("type", "text")
                if itype == "checkbox":
                    if inp.has_attr("checked"):
                        template[n] = ""
                elif itype == "radio":
                    if inp.has_attr("checked"):
                        template[n] = inp.get("value", "")
                else:
                    template[n] = inp.get("value", "")
            for sel in form.find_all("select"):
                n = sel.get("name")
                if not n:
                    continue
                opt = sel.find("option", selected=True)
                text = opt.get_text(strip=True) if opt else None
                if text is None:
                    first = sel.find("option")
                    text = first.get_text(strip=True) if first else ""
                # 粘连 text 只取第一段 option 文本
                text = text.split("Iodoacetic")[0].split("Iodoacetamide")[0] \
                    .split("4-vinyl")[0].strip() if n == "reagents" else text
                template[n] = text
            template.pop("protein", None)
            self._form_template = template
            self.log_message(f"POST form template built: {len(template)} fields -> {template}")
            return template
        except Exception as e:
            self.log_message(f"POST form template build error: {e}")
            return None

    def _post_once(self, protein_id, params, template):
        data = dict(template)
        data.update(params.to_form_data(protein_id))
        resp = self.session.post(EXPSY_BASE, data=data, timeout=30, verify=False)
        status = resp.status_code
        length = len(resp.text)
        low = resp.text.lower()
        if _looks_invalid(resp.text):
            self.log_message(f"[POST] {protein_id}: HTTP {status}, len={length}, INVALID UniProtKB ID (try UniParc fallback)")
            return INVALID_RESPONSE
        if status == 200 and not _looks_like_error_page(resp.text):
            peptides = self.parse_peptide_data(resp.text, protein_id)
            self.log_message(f"[POST] {protein_id}: HTTP {status}, len={length}, parsed={len(peptides)}")
            return peptides if peptides else None
        snippet = re.sub(r"\s+", " ", resp.text[:200])
        self.log_message(f"[POST] {protein_id}: HTTP {status}, len={length}, suspicious head={snippet!r}")
        return None

    def fetch_via_post(self, protein_id, params, attempts=2):
        """Fallback: POST form submission (A1 重试 + A4 模板复用)。"""
        for attempt in range(1, attempts + 1):
            if not self.is_running:
                return None
            template = self._ensure_form_template()
            if template is None and attempt == 1:
                self._form_template = None  # 强制下一轮重新 GET
                time.sleep(2)
                continue
            if template is None:
                return None
            try:
                result = self._post_once(protein_id, params, template)
                if result:
                    return result
                # POST 失败怀疑模板过期 -> 清空模板强制刷新一次后重试
                if attempt == 1:
                    self._form_template = None
                    time.sleep(2)
                    continue
            except Exception as e:
                self.log_message(f"[POST#{attempt}] {protein_id}: exception {e}")
                if attempt == 1:
                    self._form_template = None
            if attempt < attempts:
                time.sleep(2)
        return None

    def _fetch_via_uniparc(self, protein_id, params):
        """UniParc 归档序列兜底（旧版 UniProtBackupCrawler 路径移植）。

        场景：Expasy 报 "not a valid uniprotkb identifier"，通常是因为该 ID
        已被 UniProtKB 删除(DELETED, 如不属于参考蛋白组的 TrEMBL 条目)，
        主库 fasta 为空但 UniParc 归档仍保留历史序列。取到序列后以
        "序列粘贴"模式重新提交 Expasy PeptideMass（该模式只做酶切、不校验
        ID），酶/漏切位点/修饰等参数仍按用户当前 UI 配置走。
        返回肽段列表；取不到归档序列或提交失败返回 None。
        """
        try:
            seq = self.uniprot_helper.get_uniparc_sequence(protein_id)
        except Exception as e:
            self.log_message(f"  UniParc lookup error for {protein_id}: {e}")
            return None
        if not seq:
            self.log_message(f"  {protein_id}: UniParc fallback failed, no archived sequence")
            return None
        self.log_message(f"  {protein_id}: UniParc fallback, archived sequence len={len(seq)}")
        for attempt in range(1, 3):
            if not self.is_running:
                return None
            template = self._ensure_form_template()
            if template is None:
                if attempt == 1:
                    self._form_template = None
                    time.sleep(2)
                    continue
                return None
            try:
                data = dict(template)
                data.update(params.to_form_data(protein_id))  # 酶/MC/修饰等按 UI 参数
                data["protein"] = seq  # 序列模式覆盖 ID
                resp = self.session.post(EXPSY_BASE, data=data, timeout=30, verify=False)
                status = resp.status_code
                length = len(resp.text)
                if status == 429:
                    # Expasy 限流: 退避后重试，避免把临时限流误判成永久无效
                    self.log_message(f"[SEQ#{attempt}] {protein_id}: HTTP 429 rate limit, backoff 8s")
                    if attempt == 1:
                        self._form_template = None
                        time.sleep(8)
                        continue
                    return None
                if status == 200 and not _looks_like_error_page(resp.text):
                    peptides = self.parse_peptide_data(resp.text, protein_id)
                    if peptides:
                        self.log_message(f"[SEQ#{attempt}] {protein_id}: HTTP {status}, len={length}, parsed={len(peptides)} (via UniParc sequence)")
                        return peptides
                    self.log_message(f"[SEQ#{attempt}] {protein_id}: HTTP {status}, len={length}, parsed=0 (sequence mode)")
                snippet = re.sub(r"\s+", " ", resp.text[:200])
                self.log_message(f"[SEQ#{attempt}] {protein_id}: HTTP {status}, len={length}, suspicious head={snippet!r}")
                if attempt == 1:
                    self._form_template = None  # 怀疑模板过期，强制刷新一次
                    time.sleep(2)
            except Exception as e:
                self.log_message(f"[SEQ#{attempt}] {protein_id}: exception {e}")
                if attempt == 1:
                    self._form_template = None
                    time.sleep(2)
        return None

    def fetch_peptides(self, protein_id, params):
        """Try URL first, then POST fallback. A2: 每蛋白 0-5s 随机延迟。
        无效 ID(Expasy 不认) 先走 UniParc 归档序列兜底；
        兜底也失败才返回 INVALID_RESPONSE 永久跳过。"""
        delay = random.uniform(0, 5)
        time.sleep(delay)
        peptides = self.fetch_via_url(protein_id, params)
        if peptides == INVALID_RESPONSE:
            # Expasy 不认该 ID（通常是 UniProtKB 已删条目）：
            # 先走 UniParc 归档序列兜底，成功返回肽段，失败才永久跳过
            fallback = self._fetch_via_uniparc(protein_id, params)
            if fallback:
                self.log_message(f"  UniParc fallback OK for {protein_id}: {len(fallback)} peptides")
                return fallback
            return INVALID_RESPONSE
        if peptides:
            self.log_message(f"  URL fetch OK for {protein_id}: {len(peptides)} peptides")
            return peptides
        self.log_message(f"  URL failed for {protein_id}, trying POST...")
        time.sleep(2)
        peptides = self.fetch_via_post(protein_id, params)
        if peptides == INVALID_RESPONSE:
            # 同上：URL 无果后 POST 也报 invalid，同样尝试 UniParc 兜底
            fallback = self._fetch_via_uniparc(protein_id, params)
            if fallback:
                self.log_message(f"  UniParc fallback OK for {protein_id}: {len(fallback)} peptides")
                return fallback
            return INVALID_RESPONSE
        if peptides:
            self.log_message(f"  POST fetch OK for {protein_id}: {len(peptides)} peptides")
            return peptides
        self.log_message(f"  Both methods failed for {protein_id}")
        return []

    # ---------- batch process ----------
    def process_protein_list(self, protein_ids, output_file, params, max_workers=5):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        # 保序去重：Step1 多候选结果会带重复蛋白 ID，重复会令缓存/爬取结果被多次扩展
        seen_pids = set()
        uniq_ids = []
        for pid in protein_ids:
            pid_s = str(pid).strip()
            if pid_s and pid_s not in seen_pids:
                seen_pids.add(pid_s)
                uniq_ids.append(pid_s)
        protein_ids = uniq_ids
        all_results = []
        fp = params.fingerprint() if hasattr(params, "fingerprint") else ""

        cache_hits = 0
        to_fetch = []
        for pid in protein_ids:
            cached = self._cache_hit(pid, fp)
            if cached:
                cache_hits += 1
                all_results.extend(cached)
            else:
                to_fetch.append(pid)
        if cache_hits:
            self.log_message(f"Cache hit: {cache_hits} proteins reused from cache (fp={fp[:40]}...)")
        if not to_fetch:
            self.log_message("All proteins served from cache; skipping network crawl")
            self._write_output(output_file, all_results)
            self._write_txt_log(protein_ids, set(), cache_hits, set(), set(), 0, all_results, output_file)
            return True, []

        self.log_message(f"Starting crawl for {len(to_fetch)} proteins (cache-hit {cache_hits}), workers={max_workers}")

        failed = set(to_fetch)
        permanently_failed = set()
        total_retries = 50
        no_progress_rounds = 0
        retry_count = 0
        successful = set()

        while failed and retry_count < total_retries:
            retry_count += 1
            current_failed = set()
            self.log_message(f"Round {retry_count}: {len(failed)} proteins remaining")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(self.fetch_peptides, pid, params): pid for pid in failed}
                for future in as_completed(futures):
                    pid = futures[future]
                    if not self.is_running:
                        break
                    try:
                        peptides = future.result()
                        if peptides == INVALID_RESPONSE:
                            permanently_failed.add(pid)
                            self.log_message(f"  {pid}: INVALID UniProtKB ID, excluded permanently")
                            continue
                        if peptides:
                            all_results.extend(peptides)
                            successful.add(pid)
                            for p in peptides:
                                key = f"{pid}_{p['mass']}_{p.get('peptide sequence', '')}"
                                self.cache_by_fp.setdefault(fp, {}).setdefault(pid, {})[key] = p
                        else:
                            current_failed.add(pid)
                    except Exception as e:
                        self.log_message(f"  Error processing {pid}: {e}")
                        current_failed.add(pid)

            self._save_cache()  # D2 每轮增量落盘
            if not current_failed:
                self.log_message("All proteins processed successfully!")
                failed = set()
                break
            if current_failed == failed:
                no_progress_rounds += 1
                self.log_message(f"Round {retry_count}: no progress ({no_progress_rounds}/3 identical failure set)")
                if no_progress_rounds >= 3:
                    self.log_message("No progress for 3 consecutive rounds; stopping early to avoid idle retries")
                    failed = current_failed
                    break
            else:
                no_progress_rounds = 0
            failed = current_failed
            if not self.is_running:
                break

        self._save_cache()
        self._write_output(output_file, all_results)
        self.log_message(f"Final: {len(successful)} succeeded (+{cache_hits} cached), {len(failed)} failed, {len(permanently_failed)} invalid-ID skipped")
        self._write_txt_log(protein_ids, successful, cache_hits, failed, permanently_failed, retry_count, all_results, output_file)
        all_failed = sorted(set(failed) | permanently_failed)
        return len(all_failed) == 0, all_failed

    def _write_txt_log(self, protein_ids, successful, cache_hits, failed, permanently_failed, retry_count, all_results, output_file):
        """平移旧版 generate_txt_log_file：生成与输出文件同目录的 *_log.txt 爬虫统计日志"""
        try:
            log_file = os.path.splitext(output_file)[0] + "_log.txt"
            with open(log_file, "w", encoding="utf-8") as f:
                f.write("=" * 50 + "\n")
                f.write("爬虫任务完成统计\n")
                f.write("=" * 50 + "\n")
                f.write(f"总共识别到的唯一蛋白质数量: {len(protein_ids)}\n")
                f.write(f"成功爬取的蛋白质数量: {len(successful) + cache_hits}\n")
                f.write(f"爬取失败的蛋白质数量: {len(failed)}\n")
                f.write(f"无效UniProtKB ID跳过(永久): {len(permanently_failed)}\n")
                f.write(f"总重试轮数: {retry_count}\n")
                f.write(f"总共获取到的肽段记录数: {len(all_results)}\n")
                if failed:
                    f.write("爬取失败的蛋白质列表(可重试):\n")
                    for i, pid in enumerate(sorted(failed), 1):
                        f.write(f"  {i}. {pid}\n")
                else:
                    f.write("所有蛋白质均已成功爬取!\n")
                if permanently_failed:
                    f.write("无效UniProtKB ID跳过列表:\n")
                    for i, pid in enumerate(sorted(permanently_failed), 1):
                        f.write(f"  {i}. {pid}\n")
                f.write("=" * 50 + "\n")
            self.log_message(f"爬虫日志文件已保存到 {log_file}")
        except Exception as e:
            self.log_message(f"生成爬虫日志文件时出错: {e}")

    def _write_output(self, output_file, all_results):
        if all_results:
            df = pd.DataFrame(all_results)
            cols = ["Protein ID", "mass", "position", "#MC", "modifications", "peptide sequence"]
            for col in cols:
                if col not in df.columns:
                    df[col] = ""
            df = df[cols]
            df.to_excel(output_file, index=False)
            self.log_message(f"Saved {len(df)} records to {output_file}")
        elif os.path.exists(output_file):
            os.remove(output_file)
            self.log_message(f"Removed empty crawl file {output_file}")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    params = ExpasyParams()
    crawler = PeptideCrawlerCore(output_folder=".")
    crawler.process_protein_list(["P02649"], "test_crawl.xlsx", params)
