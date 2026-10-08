# -*- coding: utf-8 -*-
# 基于质谱峰的蛋白质匹配软件 V1.0
# Copyright (c) 2026 张葛阳
# 本软件为独立开发，未使用第三方开源代码
"""
DSH 自定义 OpenAI 兼容网关路由配置

给 ~/.dsh/settings.yaml 合并 `llm-pi-ai.providers.<route>` 分节，
把 API Key 明文只写入 ~/.dsh/.credentials.yaml 的 refs.<引用名>。

路由 profile 结构（对齐 @deepseek-ai/dsh-llm-pi-ai）：
    llm-pi-ai:
      providers:
        <route>:
          displayName?: 厂商显示名（可选）
          apiKeyEnv: <大写引用名>_API_KEY   # 凭据引用，明文不落 settings
          api: openai-completions            # 本板块固定 OpenAI 兼容协议
          baseURL: https://...
          models: [{id, ...}]                # 必填非空

写盘前先备份 settings.yaml / .credentials.yaml 到指定备份目录；
settings.yaml 其它顶层分节（ui-onboarding / agent-default-model 等）原样保留，
仅增改目标分节。
"""
import datetime
import json
import os
import re
import shutil
import urllib.error
import urllib.request

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


# ============================================================
#  常量 / 小工具
# ============================================================
ROUTE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_KEEP_RE = re.compile(r"[^a-z0-9]+")

SETTINGS_FILE = "settings.yaml"
CREDENTIALS_FILE = ".credentials.yaml"

# 备份目录：调用方（web_app.py）启动时设置；为空则跳过备份（仅供测试用）
BACKUP_DIR = ""


def set_backup_dir(path):
    global BACKUP_DIR
    BACKUP_DIR = (path or "").strip()


def _home():
    return os.path.expanduser("~/.dsh")


def settings_path(home=None):
    return os.path.join(home or _home(), SETTINGS_FILE)


def credentials_path(home=None):
    return os.path.join(home or _home(), CREDENTIALS_FILE)


def _backup_once(file_path):
    """写盘前备份原文件到 BACKUP_DIR（带时间戳，不覆盖已有备份）。"""
    if not BACKUP_DIR or not file_path or not os.path.isfile(file_path):
        return None
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        base = os.path.basename(file_path)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        target = os.path.join(BACKUP_DIR, f"{base.replace('.', '_')}_{stamp}.yaml")
        shutil.copy2(file_path, target)
        return target
    except Exception:
        return None


# ============================================================
#  YAML 读写（保序、不丢注释以外的结构）
# ============================================================
def _read_yaml(path, default=None):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        return doc if isinstance(doc, dict) else default
    except Exception:
        return default


def _write_yaml(path, doc):
    if yaml is None:
        raise RuntimeError("PyYAML 未安装：pip install pyyaml")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # newline='\n'：保持与 dsh(Node/yaml) 原样一致的 LF 行尾，避免 Windows 文本模式转 CRLF 造成字节漂移
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False,
                       default_flow_style=False, width=120)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


# ============================================================
#  路由键 / 凭据引用生成
# ============================================================
def slugify(display_name):
    """显示名 -> 小写 slug：Agnes AI -> agnes-ai；中文名 -> 空串（需手动填 route）。"""
    if not display_name:
        return ""
    s = display_name.strip().lower()
    s = SLUG_KEEP_RE.sub("-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


def validate_route(route):
    route = (route or "").strip()
    if not route:
        return "", "路由键不能为空（未填写显示名时请手动输入路由键）"
    if not ROUTE_RE.match(route):
        return route, "路由键只允许小写字母 / 数字 / 连字符（如 agnes-ai）"
    return route, ""


def ref_name_for(route):
    """route -> 凭据引用名：agnes-ai -> AGNES_AI_API_KEY"""
    up = re.sub(r"[^A-Z0-9]+", "_", (route or "").upper()).strip("_")
    return up + "_API_KEY"


# ============================================================
#  读取
# ============================================================
def _get_llm_providers(doc):
    if not isinstance(doc, dict):
        return {}
    pi = doc.get("llm-pi-ai")
    if pi is None:
        return {}
    if not isinstance(pi, dict):
        return {}
    prov = pi.get("providers")
    if prov is None:
        return {}
    if not isinstance(prov, dict):
        return {}
    return prov


def list_providers(home=None):
    """列出 settings.yaml 中用户层 llm-pi-ai.providers 的全部路由（不含密钥）。"""
    doc = _read_yaml(settings_path(home), {})
    prov = _get_llm_providers(doc)
    refs = list_credential_refs(home)

    out = []
    for route, profile in prov.items():
        if not isinstance(profile, dict):
            continue
        models = profile.get("models") or []
        model_ids = []
        for m in models:
            if isinstance(m, dict):
                if m.get("id"):
                    model_ids.append(m["id"])
            elif isinstance(m, str) and m:
                model_ids.append(m)
        api_key_env = (profile.get("apiKeyEnv") or "").strip()
        out.append({
            "route": route,
            "displayName": profile.get("displayName") or "",
            "baseURL": profile.get("baseURL") or "",
            "api": profile.get("api") or "openai-completions",
            "apiKeyEnv": api_key_env,
            "hasKey": bool(api_key_env and api_key_env in refs),
            "models": model_ids,
            "isDefault": False,
        })

    # 标注默认模型
    adm = (doc or {}).get("agent-default-model") or {}
    default_provider = (adm or {}).get("provider") or ""
    for r in out:
        if r["route"] == default_provider:
            r["isDefault"] = True
            r["defaultModel"] = adm.get("model") or ""
    return out


def list_credential_refs(home=None):
    """只返回 refs 的键名集合（绝不返回密钥值）。"""
    doc = _read_yaml(credentials_path(home), {})
    refs = doc.get("refs")
    if not isinstance(refs, dict):
        return set()
    return set(str(k) for k in refs.keys())


def get_default_model(home=None):
    doc = _read_yaml(settings_path(home), {})
    adm = doc.get("agent-default-model") or {}
    return {
        "provider": adm.get("provider") or "",
        "model": adm.get("model") or "",
        "reasoningEffort": adm.get("reasoningEffort") or "",
    }


# ============================================================
#  写：保存 / 删除 / 恢复默认
# ============================================================
def save_provider(payload, home=None, backup_dir=None):
    """保存一条 OpenAI 兼容网关路由。

    payload: {
      route?, display_name?, base_url, api_key,
      models: [str...] | [{id}...],
      set_default: bool
    }
    """
    old_backup_dir = BACKUP_DIR
    if backup_dir is not None:
        set_backup_dir(backup_dir)
    try:
        route = (payload.get("route") or "").strip()
        display_name = (payload.get("display_name") or "").strip()
        if not route:
            route = slugify(display_name)
        route, err = validate_route(route)
        if err:
            raise ValueError(err)

        base_url = (payload.get("base_url") or "").strip().rstrip("/")
        if not base_url.lower().startswith(("http://", "https://")):
            raise ValueError("BaseURL 必须是 http:// 或 https:// 开头的完整地址")

        api_key = str(payload.get("api_key") or "").strip()

        raw_models = payload.get("models") or []
        model_ids = []
        for m in raw_models:
            if isinstance(m, dict):
                mid = str(m.get("id") or "").strip()
            else:
                mid = str(m).strip()
            if mid and mid not in model_ids:
                model_ids.append(mid)
        if not model_ids:
            raise ValueError("模型 ID 列表至少需要 1 个模型")

        home = home or _home()
        # ---- settings.yaml ----
        doc = _read_yaml(settings_path(home), {})
        _backup_once(settings_path(home))
        prov = _get_llm_providers(doc)
        route_exists = route in prov
        if not isinstance(doc.get("llm-pi-ai"), dict):
            doc["llm-pi-ai"] = {}
        if not isinstance(doc["llm-pi-ai"].get("providers"), dict):
            doc["llm-pi-ai"]["providers"] = {}

        profile = {
            "apiKeyEnv": ref_name_for(route),
            "api": "openai-completions",
            "baseURL": base_url,
            "models": [{"id": mid} for mid in model_ids],
        }
        if display_name:
            profile["displayName"] = display_name
        doc["llm-pi-ai"]["providers"][route] = profile

        # 先校验 API Key，再写盘（避免 settings 写入后才报错导致残留无密钥路由）
        ref = ref_name_for(route)
        if not api_key:
            route_exists = route in prov
            cred_doc_pre = _read_yaml(credentials_path(home), {})
            pre_refs = cred_doc_pre.get("refs") if isinstance(cred_doc_pre, dict) else None
            has_old_key = bool(isinstance(pre_refs, dict) and ref in pre_refs)
            if not route_exists or not has_old_key:
                raise ValueError(
                    "API Key 不能为空（新增路由必须提供密钥；仅当该路由已存在且已保存过密钥时可留空以保留旧 Key）")

        # 可选：设为默认模型（provider=route, model=第一个模型 id；保留 reasoningEffort）
        if payload.get("set_default"):
            adm = doc.get("agent-default-model")
            if not isinstance(adm, dict):
                adm = {}
            adm["provider"] = route
            adm["model"] = model_ids[0]
            doc["agent-default-model"] = adm

        _write_yaml(settings_path(home), doc)

        # ---- .credentials.yaml（只写 refs，明文密钥绝不进 settings）----
        cred_doc = _read_yaml(credentials_path(home), {})
        if not isinstance(cred_doc, dict) or not cred_doc:
            cred_doc = {"version": 1, "refs": {}, "records": {}}
        if not isinstance(cred_doc.get("refs"), dict):
            cred_doc["refs"] = {}
        if cred_doc.get("version") is None:
            cred_doc["version"] = 1
        existing_refs = cred_doc["refs"]
        if api_key:
            _backup_once(credentials_path(home))
            existing_refs[ref] = api_key
            _write_yaml(credentials_path(home), cred_doc)

        return {
            "ok": True,
            "route": route,
            "ref": ref,
            "message": f"配置已保存：{route}（凭据引用 {ref}）",
            "backup": _last_backup_names(),
        }
    finally:
        if backup_dir is not None:
            set_backup_dir(old_backup_dir)


def delete_provider(route, home=None, backup_dir=None):
    """删除一条用户新增路由：只移除 llm-pi-ai.providers.<route> 与其 credentials ref。"""
    old_backup_dir = BACKUP_DIR
    if backup_dir is not None:
        set_backup_dir(backup_dir)
    try:
        route = (route or "").strip()
        home = home or _home()
        doc = _read_yaml(settings_path(home), {})
        prov = _get_llm_providers(doc)
        if route not in prov:
            raise ValueError(f"路由不存在：{route}")

        adm = doc.get("agent-default-model") or {}
        if isinstance(adm, dict) and adm.get("provider") == route:
            raise ValueError(f"路由 {route} 正被设为默认模型；请先修改默认设置或使用「恢复 DeepSeek 默认」")

        _backup_once(settings_path(home))
        profile = prov.pop(route)
        if not prov and isinstance(doc.get("llm-pi-ai"), dict):
            # providers 已空 -> 移除空 providers（保留 llm-pi-ai 顶层如存在其它字段）
            doc["llm-pi-ai"].pop("providers", None)
            if not doc["llm-pi-ai"]:
                doc.pop("llm-pi-ai", None)
        _write_yaml(settings_path(home), doc)

        # 删除 credentials ref（优先按 profile.apiKeyEnv，回退按路由推导）
        cred_doc = _read_yaml(credentials_path(home), {})
        refs = cred_doc.get("refs") if isinstance(cred_doc, dict) else None
        if isinstance(refs, dict):
            ref_candidates = []
            env = (profile.get("apiKeyEnv") or "").strip() if isinstance(profile, dict) else ""
            if env:
                ref_candidates.append(env)
            ref_candidates.append(ref_name_for(route))
            _backup_once(credentials_path(home))
            removed = []
            for ref in ref_candidates:
                if ref in refs:
                    del refs[ref]
                    removed.append(ref)
            _write_yaml(credentials_path(home), cred_doc)
        else:
            removed = []

        return {"ok": True, "route": route, "removed_refs": removed,
                "message": f"已删除路由 {route}" + (f"，并移除凭据引用 {', '.join(removed)}" if removed else "")}
    finally:
        if backup_dir is not None:
            set_backup_dir(old_backup_dir)


def restore_default(home=None, backup_dir=None):
    """恢复 DeepSeek 默认：清空用户层 llm-pi-ai.providers + 对应 refs；
    若默认模型指向用户路由则回退 deepseek-official / deepseek-v4-flash-vision-exp。"""
    old_backup_dir = BACKUP_DIR
    if backup_dir is not None:
        set_backup_dir(backup_dir)
    try:
        home = home or _home()
        doc = _read_yaml(settings_path(home), {})
        prov = _get_llm_providers(doc)
        removed_routes = list(prov.keys()) if isinstance(prov, dict) else []

        _backup_once(settings_path(home))
        changed = False
        if removed_routes:
            doc.get("llm-pi-ai", {}).pop("providers", None)
            if not doc.get("llm-pi-ai"):
                doc.pop("llm-pi-ai", None)
            changed = True

        adm = doc.get("agent-default-model")
        if isinstance(adm, dict) and adm.get("provider") in removed_routes:
            adm["provider"] = "deepseek-official"
            adm["model"] = "deepseek-v4-flash-vision-exp"
            changed = True
        if changed:
            _write_yaml(settings_path(home), doc)

        # 删除这些路由对应的 credentials refs
        cred_doc = _read_yaml(credentials_path(home), {})
        refs = cred_doc.get("refs") if isinstance(cred_doc, dict) else None
        removed_refs = []
        if isinstance(refs, dict) and removed_routes:
            _backup_once(credentials_path(home))
            for route in removed_routes:
                for ref in (ref_name_for(route),):
                    if ref in refs:
                        del refs[ref]
                        removed_refs.append(ref)
            _write_yaml(credentials_path(home), cred_doc)

        return {"ok": True, "removed_routes": removed_routes,
                "removed_refs": removed_refs,
                "message": "已恢复 DeepSeek 默认配置"}
    finally:
        if backup_dir is not None:
            set_backup_dir(old_backup_dir)


def set_default(route, model, home=None, backup_dir=None):
    """把 agent-default-model 指向某条已配置路由（不保存新路由）。"""
    old_backup_dir = BACKUP_DIR
    if backup_dir is not None:
        set_backup_dir(backup_dir)
    try:
        home = home or _home()
        route = (route or "").strip()
        model = (model or "").strip()
        doc = _read_yaml(settings_path(home), {})
        prov = _get_llm_providers(doc)
        if route not in prov:
            raise ValueError(f"路由不存在：{route}")
        if not model:
            raise ValueError("请选择要设为默认的模型")
        _backup_once(settings_path(home))
        adm = doc.get("agent-default-model")
        if not isinstance(adm, dict):
            adm = {}
        adm["provider"] = route
        adm["model"] = model
        doc["agent-default-model"] = adm
        _write_yaml(settings_path(home), doc)
        return {"ok": True, "route": route, "model": model,
                "message": f"默认模型已设为 {route} / {model}"}
    finally:
        if backup_dir is not None:
            set_backup_dir(old_backup_dir)


def _last_backup_names():
    """返回本进程本次写操作前记录的备份文件名（尽力而为的信息提示）。"""
    if not BACKUP_DIR:
        return []
    try:
        if os.path.isdir(BACKUP_DIR):
            names = [n for n in os.listdir(BACKUP_DIR) if n.startswith("dsh_") and n.endswith(".yaml")]
            return sorted(names)[-4:]
    except Exception:
        pass
    return []


# ============================================================
#  模型发现（GET {baseURL}/models，Bearer 认证，只读）
# ============================================================
def discover_models(base_url, api_key, timeout=15):
    """用 BaseURL + API Key 调 GET {baseURL}/models，返回候选模型列表。

    返回: {ok: True, models: [{id, name?, contextWindow?}]}
      或 {ok: False, code, message}
    """
    base_url = (base_url or "").strip().rstrip("/")
    api_key = (api_key or "").strip()
    if not base_url.lower().startswith(("http://", "https://")):
        return {"ok": False, "code": "BAD_URL", "message": "BaseURL 必须是 http(s):// 开头"}
    if not api_key:
        return {"ok": False, "code": "NO_KEY", "message": "需要 API Key 才能做模型发现"}

    url = base_url + "/models"
    req = urllib.request.Request(url, method="GET", headers={
        "Authorization": "Bearer " + api_key,
        "Accept": "application/json",
        "User-Agent": "dsh-protein-config",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False, "code": "AUTH_FAIL",
                    "message": f"API Key 无效（HTTP {e.code}），请检查凭据"}
        return {"ok": False, "code": "HTTP_ERR", "message": f"端点返回 HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"ok": False, "code": "UNREACHABLE",
                "message": f"端点不可达：{e.reason}"}
    except Exception as e:
        return {"ok": False, "code": "DISCOVERY_FAILED",
                "message": f"模型发现失败：{e}"}

    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return {"ok": False, "code": "BAD_JSON", "message": "端点未返回合法 JSON"}
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {"ok": False, "code": "NO_DATA", "message": "响应中没有 data 数组（可能不是 OpenAI 兼容 /models 端点）"}

    models = []
    for it in items:
        if not isinstance(it, dict):
            continue
        mid = str(it.get("id") or "").strip()
        if not mid:
            continue
        entry = {"id": mid}
        if it.get("name"):
            entry["name"] = str(it["name"])
        if it.get("context_window") or it.get("context_length"):
            entry["contextWindow"] = (it.get("context_window") or it.get("context_length"))
        models.append(entry)
    if not models:
        return {"ok": False, "code": "EMPTY", "message": "端点返回的模型列表为空"}
    return {"ok": True, "models": models}
