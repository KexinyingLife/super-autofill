# -*- coding: utf-8 -*-
"""Generic form field schema, option library, and preset engine.

All content is placeholder. Replace FIELD_DEFS / LIB with your actual
form structure to adapt this framework to any domain.
"""
from __future__ import annotations

from typing import Any

# ── Field definitions ───────────────────────────────────────────────
# (key, display_label, widget_type)
# Widget: text | dd_<libkey> | cb | txt

FIELD_DEFS: list[tuple[str, str, str]] = [
    ("uid",     "条目ID",     "text"),
    ("name",    "条目名称",   "text"),
    ("cat",     "类别",       "dd_cat"),
    ("f1",      "字段A(1-5)", "dd_s5"),
    ("f2",      "字段B(1-5)", "dd_s5"),
    ("f3",      "字段C(1-5)", "dd_s5"),
    ("g1",      "子项-1",     "dd_grade"),
    ("g2",      "子项-2",     "dd_grade"),
    ("g3",      "子项-3",     "dd_grade"),
    ("rec",     "是否推荐",   "dd_yn"),
    ("recNote", "推荐理由(≥20字)", "txt"),
    ("flag1",   "标记A(勾=是)", "cb"),
    ("flag2",   "标记B(勾=是)", "cb"),
    ("sub1",    "标签1",      "dd_tag"),
    ("sub2",    "标签2",      "dd_tag"),
    ("total",   "总分(1-10)", "dd_s10"),
    ("w1",      "意愿1(勾=是)", "cb"),
    ("w2",      "意愿2(勾=是)", "cb"),
    ("w3",      "意愿3",       "dd_yn"),
    ("status",  "状态",       "text"),
    ("ts",      "时间戳",     "text"),
]

FIELD_KEYS  = [f[0] for f in FIELD_DEFS]
FIELD_HEADS = [f[1] for f in FIELD_DEFS]
FIELD_TYPES = {f[0]: f[2] for f in FIELD_DEFS}

# ── Defaults ────────────────────────────────────────────────────────

CB_DEFAULT: dict[str, bool] = {
    "flag1": False, "flag2": False,
    "w1": True, "w2": True,
}

DD_DEFAULT: dict[str, str] = {
    "cat": "", "f1": "3", "f2": "3", "f3": "3",
    "g1": "3:一般", "g2": "3:一般", "g3": "3:一般",
    "rec": "不一定", "sub1": "", "sub2": "",
    "total": "5", "w3": "不一定",
}

# ── Option library ──────────────────────────────────────────────────

LIB: dict[str, list[str]] = {
    "cat":    ["类型A", "类型B", "类型C", "类型D"],
    "s5":     ["1", "2", "3", "4", "5"],
    "s10":    [str(i) for i in range(1, 11)],
    "grade":  ["5:优秀", "4:良好", "3:一般", "2:欠佳", "1:差"],
    "yn":     ["愿意", "不愿意", "不一定"],
    "tag":    ["标签X", "标签Y", "标签Z", "标签W"],
}

# ── Category-based presets ──────────────────────────────────────────

CAT_PRESETS: dict[str, dict[str, str]] = {
    "类型A": {"sub1": "标签X", "sub2": "标签Y", "f1": "4"},
    "类型B": {"sub1": "标签Y", "sub2": "标签Z", "f2": "4"},
    "类型C": {"sub1": "标签Z", "sub2": "标签W", "f3": "4"},
}

CAT_FOLLOWUP: dict[str, str] = {
    "类型A": "fq_a",
    "类型B": "fq_b",
}


def default_cells(idx: int, uid: str, name: str, cat: str = "") -> dict[str, Any]:
    """Build a row of default values for one item."""
    cells: dict[str, Any] = {
        "uid": str(uid), "name": name,
        "cat": cat, "status": "待处理", "ts": "",
        "recNote": "",
    }
    for k, v in CB_DEFAULT.items():
        cells[k] = v
    for k, v in DD_DEFAULT.items():
        cells[k] = v
    for k, v in CAT_PRESETS.get(cat, {}).items():
        cells[k] = v
    return cells
