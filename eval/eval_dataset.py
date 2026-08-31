"""评估题目加载与校验（从 eval_retrieval.py 拆出）。

职责：读 data/eval_cases.yaml、校验必填字段、执行 SQL（golden 与 Agent
SQL 共用的执行器）。
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import yaml

from analyst_tools import _get_df

CASES_FILE = Path(__file__).resolve().parent.parent / "data" / "eval_cases.yaml"
DEFAULT_DATASET = "京东"

_REQUIRED_FIELDS = {"id", "type", "question"}


def run_sql(sql: str, name: str = DEFAULT_DATASET) -> pd.DataFrame:
    """在同一个 duckdb 引擎、同一份注册数据上执行 SQL（golden 与 Agent SQL 共用）。"""
    conn = duckdb.connect()
    try:
        conn.register(name, _get_df(name))
        return conn.execute(sql).df()
    finally:
        conn.close()


def load_cases() -> list[dict]:
    with open(CASES_FILE, encoding="utf-8") as f:
        cases = yaml.safe_load(f)
    for c in cases:
        missing = _REQUIRED_FIELDS - set(c)
        if missing:
            raise ValueError(f"题目 {c.get('id')} 缺少必填字段: {missing}")
        if c["type"] == "sql":
            if not c.get("golden_sql"):
                raise ValueError(f"sql 题 {c['id']} 缺少 golden_sql")
            # 加载时校验 golden 可执行：跑不通的金子进不了题集
            run_sql(c["golden_sql"], c.get("dataset", DEFAULT_DATASET))
        elif c["type"] in ("clarify", "clarify_small", "negative"):
            if not c.get("expected_behavior"):
                raise ValueError(f"行为题 {c['id']} 缺少 expected_behavior")
    return cases
