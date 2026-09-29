# -*- coding: utf-8 -*-
"""Expasy PeptideMass parameter handling - URL builder and form data builder

字段与取值以 2026-09-02 抓取的 Expasy PeptideMass 真实页面为准：
  https://web.expasy.org/peptide_mass/
表单字段:
  enzyme      select(25)         酶名(option 文本即提交值)
  MC          select(0..5)       missed cleavages
  minmass     select(0/500/750/1000/1250/1500/1750)
  maxmass     select(3000..8000/unlimited)
  mplus       radio: mh/m/mminus/mh2/mh3
  masses      radio: monoisotopic/average
  order       radio: mass/chronologic
  reagents    select: nothing (in reduced form)/Iodoacetic acid/Iodoacetamide/4-vinyl pyridene
  acrylamide  checkbox value=acrylamide
  methionine  checkbox value=oxidize
  modification/conflict/variant/varsplic  checkbox (无 value, checked 时提交空串)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import EXPSY_DEFAULTS

EXPSY_BASE = "https://web.expasy.org/cgi-bin/peptide_mass/peptide-mass.pl"
EXPSY_PAGE = "https://web.expasy.org/peptide_mass/"

# ---- Enzyme: 与 Expasy 页面 option 文本逐字对齐 ----
ENZymes = [
    "Trypsin",
    "Trypsin (C-term to K/R, even before P)",
    "Trypsin (higher specificity)",
    "Trypsin/CNBr",
    "Lys C",
    "Lys N",
    "CNBr",
    "Arg C",
    "Asp N",
    "Asp N + N-terminal Glu",
    "Asp N / Lys C",
    "Asp N + N-terminal Glu / Lys C",
    "Asp N / Glu C (bicarbonate)",
    "Glu C (bicarbonate)",
    "Glu C (phosphate)",
    "Glu C (phosphate) + Lys C",
    "Microwave-assisted formic acid hydrolysis (C-term to D)",
    "Chymotrypsin (C-term to F/Y/W/M/L, not before P)",
    "Chymotrypsin (C-term to F/Y/W, not before P)",
    "Trypsin/Chymotrypsin (C-term to K/R/F/Y/W, not before P)",
    "Pepsin (pH 1.3)",
    "Pepsin (pH > 2)",
    "Proteinase K",
    "Thermolysin",
    "No cutting",
]

# 旧版/别名 -> 当前页面文本 (兼容历史 run_log / 旧代码传值)
ENZYME_ALIASES = {
    "Trypsin/P": "Trypsin (C-term to K/R, even before P)",
    "Trypsin(higher specificity)": "Trypsin (higher specificity)",
    "Lys-C": "Lys C",
    "Lys-N": "Lys N",
    "Arg-C": "Arg C",
    "Asp-N": "Asp N",
    "Asp-N+Lys-C": "Asp N / Lys C",
    "Asp-N+N-terminal Glu": "Asp N + N-terminal Glu",
    "Asp-N+Glu-C(bicarbonate)": "Asp N / Glu C (bicarbonate)",
    "Glu-C(bicarbonate)": "Glu C (bicarbonate)",
    "Glu-C(phosphate)": "Glu C (phosphate)",
    "Glu-C(phosphate)+Lys-C": "Glu C (phosphate) + Lys C",
    "Chymotrypsin(C-term to F/Y/W/M/L)": "Chymotrypsin (C-term to F/Y/W/M/L, not before P)",
    "Chymotrypsin(C-term to F/Y/W/M/L, not before P)": "Chymotrypsin (C-term to F/Y/W/M/L, not before P)",
    "Chymotrypsin(C-term to F/Y/W)": "Chymotrypsin (C-term to F/Y/W, not before P)",
    "Trypsin/Chymotrypsin(C-term to K/R/F/Y/W)": "Trypsin/Chymotrypsin (C-term to K/R/F/Y/W, not before P)",
    "Pepsin(pH 1.3)": "Pepsin (pH 1.3)",
    "Pepsin(pH > 2)": "Pepsin (pH > 2)",
    "Microwave-assisted formic acid hydrolysis(C-term to D)":
        "Microwave-assisted formic acid hydrolysis (C-term to D)",
}

# ---- CYS: reagents select 与 acrylamide checkbox 相互独立（对齐 Expasy） ----
CYS_REAGENT_OPTIONS = [
    "nothing (in reduced form)",
    "Iodoacetic acid",
    "Iodoacetamide",
    "4-vinyl pyridene",
]

# 旧 GUI 值 / 旧 internal 值 -> (reagent 文本, 是否勾选 acrylamide)
_CYS_LEGACY_MAP = {
    "nothing": ("nothing (in reduced form)", False),
    "iodoacetic_acid": ("Iodoacetic acid", False),
    "iodoacetic acid": ("Iodoacetic acid", False),
    "iodoacetamide": ("Iodoacetamide", False),
    "vinyl_pyridine": ("4-vinyl pyridene", False),
    "vinyl pyridine": ("4-vinyl pyridene", False),
    "acrylamide": ("nothing (in reduced form)", True),
    "acrylamide adducts": ("nothing (in reduced form)", True),
    "with acrylamide adducts": ("nothing (in reduced form)", True),
}


def normalize_cys_value(cys_value, acrylamide_flag=None):
    """把任意历史取值归一化为 (reagents 文本, acrylamide bool)。

    接受: 新页面 reagent 文本 / 旧 GUI 文案 / 旧 internal 值。
    若显式给出 acrylamide_flag 则以其为准。
    """
    v = cys_value
    if isinstance(v, str):
        v = v.strip()
    if v in CYS_REAGENT_OPTIONS:
        reagent, legacy_acryl = v, False
    else:
        reagent, legacy_acryl = _CYS_LEGACY_MAP.get(
            v, ("nothing (in reduced form)", False))
    if acrylamide_flag is None:
        acrylamide_flag = legacy_acryl
    return reagent, bool(acrylamide_flag)


CYS_OPTIONS = list(CYS_REAGENT_OPTIONS)

# ---- Ion type: GUI 显示 -> Expasy 提交值 ----
ION_GUI_TO_INTERNAL = {
    "[M+H]+": "mh",
    "[M]": "m",
    "[M-H]-": "mminus",
    "[M+2H]2+": "mh2",
    "[M+3H]3+": "mh3",
}
INTERNAL_TO_ION_GUI = {v: k for k, v in ION_GUI_TO_INTERNAL.items()}

# 旧 internal 错误值兼容 (mh-/m2h/m3h 曾误用)
_ION_LEGACY_FIX = {"mh-": "mminus", "m2h": "mh2", "m3h": "mh3"}


def normalize_mplus(v):
    """归一化离子类型为 Expasy 可识别的 mplus 值。"""
    if v in ("mh", "m", "mminus", "mh2", "mh3"):
        return v
    if v in ION_GUI_TO_INTERNAL:
        return ION_GUI_TO_INTERNAL[v]
    return _ION_LEGACY_FIX.get(v, v)


MASS_TYPES = ["monoisotopic", "average"]

# internal sort 值 (Expasy order 字段值)
SORT_OPTIONS = ["mass", "chronologic"]
_SORT_LEGACY_FIX = {"chronological": "chronologic", "chronological order in the protein": "chronologic",
                    "peptide masses": "mass"}

MIN_MASS_OPTIONS = ["0", "500", "750", "1000", "1250", "1500", "1750"]
MAX_MASS_OPTIONS = ["3000", "4000", "5000", "6000", "7000", "8000", "unlimited"]


class ExpasyParams:
    def __init__(self, **kwargs):
        defaults = EXPSY_DEFAULTS.copy()
        defaults.update(kwargs)
        # enzyme 归一化
        self.enzyme = ENZYME_ALIASES.get(str(defaults["enzyme"]).strip(), str(defaults["enzyme"]).strip())
        self.missed_cleavages = int(defaults["missed_cleavages"] or 0)
        self.min_mass = defaults["min_mass"]
        self.max_mass = defaults["max_mass"]
        # sort 归一化
        raw_sort = defaults["sort_by"]
        self.sort_by = _SORT_LEGACY_FIX.get(str(raw_sort), str(raw_sort))
        # mplus 归一化
        self.mplus = normalize_mplus(defaults["mplus"])
        self.masses = str(defaults["masses"])
        # CYS: 新旧字段兼容
        if "cys_reagent" in defaults and str(defaults.get("cys_reagent") or "").strip():
            reagent, acryl = normalize_cys_value(defaults["cys_reagent"])
        else:
            reagent, acryl = normalize_cys_value(defaults.get("cys_treatment", "nothing"))
        if "cys_acrylamide" in defaults and defaults["cys_acrylamide"] is not None:
            acryl = bool(defaults["cys_acrylamide"])
        self.cys_reagent = reagent
        self.cys_acrylamide = acryl
        # met
        self.met_oxidized = bool(defaults["met_oxidized"])
        # 显示范围勾选
        self.show_ptm = bool(defaults["show_ptm"])
        self.show_conflict = bool(defaults["show_conflict"])
        self.show_variant = bool(defaults["show_variant"])
        self.show_varsplice = bool(defaults["show_varsplice"])

    def _mass_range(self):
        """min/max 归一化到 Expasy select 的合法取值。
        Expasy 只接受固定选项：minmass 0/500/750/1000/1250/1500/1750,
        maxmass 3000/4000/5000/6000/7000/8000/unlimited。
        输入不在列表内时向上就近取整，避免被 CGI 静默忽略。
        """
        opts = [int(x) for x in MIN_MASS_OPTIONS]
        minv = int(self.min_mass or 500)
        best = min(opts, key=lambda x: (abs(x - minv), -x))
        if best < minv:  # 不允许比期望值小
            larger = [x for x in opts if x >= minv]
            best = min(larger) if larger else opts[-1]
        maxv = self.max_mass
        if maxv is None or maxv == "unlimited":
            return str(best), "unlimited"
        maxv = int(maxv)
        mopts = [int(x) for x in MAX_MASS_OPTIONS if x != "unlimited"]
        larger = [x for x in mopts if x >= maxv]
        return str(best), str(min(larger) if larger else max(mopts))

    def to_url_params(self):
        """Build URL query string for GET request (Expasy CGI 字段)。"""
        minmass, maxmass = self._mass_range()
        params = {
            "enzyme": self.enzyme,
            "MC": str(self.missed_cleavages),
            "minmass": minmass,
            "maxmass": maxmass,
            "mplus": self.mplus,
            "masses": self.masses,
            "order": self.sort_by,
            "reagents": self.cys_reagent,
        }
        if self.cys_acrylamide:
            params["acrylamide"] = "acrylamide"
        if self.met_oxidized:
            params["methionine"] = "oxidize"
        # Expasy 的 4 个“显示范围”checkbox 无 value：勾选时提交空串即可
        if self.show_ptm:
            params["modification"] = ""
        if self.show_conflict:
            params["conflict"] = ""
        if self.show_variant:
            params["variant"] = ""
        if self.show_varsplice:
            params["varsplic"] = ""
        return params

    def to_form_data(self, protein_id):
        """Build form data dict for POST request"""
        data = {"protein": protein_id}
        data.update(self.to_url_params())
        return data

    def fingerprint(self):
        """缓存参数指纹：同指纹的同蛋白可用同一份缓存结果。"""
        parts = [self.enzyme, str(self.missed_cleavages),
                 self._mass_range()[0], self._mass_range()[1],
                 self.sort_by, self.cys_reagent,
                 "A" if self.cys_acrylamide else "-",
                 "M" if self.met_oxidized else "-",
                 self.mplus, self.masses,
                 "P" if self.show_ptm else "-",
                 "C" if self.show_conflict else "-",
                 "V" if self.show_variant else "-",
                 "S" if self.show_varsplice else "-"]
        return "|".join(parts)

    def to_dict(self):
        return {
            "enzyme": self.enzyme, "missed_cleavages": self.missed_cleavages,
            "min_mass": self.min_mass, "max_mass": self.max_mass,
            "sort_by": self.sort_by,
            "cys_treatment": self.cys_reagent,
            "cys_reagent": self.cys_reagent,
            "cys_acrylamide": self.cys_acrylamide,
            "met_oxidized": self.met_oxidized, "mplus": self.mplus,
            "masses": self.masses,
            "show_ptm": self.show_ptm, "show_conflict": self.show_conflict,
            "show_variant": self.show_variant, "show_varsplice": self.show_varsplice,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in d.items() if k in EXPSY_DEFAULTS or
                      k in ("cys_reagent", "cys_acrylamide")})

    @classmethod
    def from_gui_values(cls, enzyme, missed_cl, min_m, max_m, sort, cys_gui, met_ox, ion_gui, mass_type, show_ptm, show_conf, show_var, show_vs):
        # Convert GUI values to internal values
        internal_ion = ION_GUI_TO_INTERNAL.get(ion_gui, ion_gui)
        return cls(
            enzyme=enzyme, missed_cleavages=int(missed_cl),
            min_mass=int(min_m) if min_m else 0,
            max_mass=None if max_m == "" or max_m is None else int(max_m),
            sort_by=sort, cys_treatment=cys_gui, met_oxidized=bool(met_ox),
            mplus=internal_ion, masses=mass_type,
            show_ptm=bool(show_ptm), show_conflict=bool(show_conf),
            show_variant=bool(show_var), show_varsplice=bool(show_vs),
        )


if __name__ == "__main__":
    p = ExpasyParams()
    print("fingerprint:", p.fingerprint())
    print("URL params:", p.to_url_params())
    print("Form data:", p.to_form_data("P02649"))
    # 兼容旧值验证
    q = ExpasyParams(cys_treatment="acrylamide adducts", mplus="mh-", sort_by="chronological",
                     enzyme="Glu-C(bicarbonate)")
    print("legacy normalized:", q.to_url_params())
