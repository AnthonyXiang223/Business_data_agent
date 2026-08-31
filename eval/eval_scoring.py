"""评估判分（从 eval_retrieval.py 拆出）。

sql 题三级判定：值包含直通（零 judge 调用）→ LLM judge → 答案数值兜底；
行为题（clarify/clarify_small/negative）：LLM judge → 关键词兜底。
judge 调用计数用 thread-local 隔离（并行尝试互不串扰）。
"""

from __future__ import annotations

import re
import threading

import numpy as np
import pandas as pd

import eval_judge
from eval_dataset import DEFAULT_DATASET, run_sql

# thread-local：并行尝试各自的 judge 计数
_tls = threading.local()


def reset_judge_calls() -> None:
    _tls.judge_calls = 0


def get_judge_calls() -> int:
    return getattr(_tls, "judge_calls", 0)


def _cell_key(v):
    """单元格归一化：数值取 6 位小数容差，其余转字符串，空值丢弃。

    必须覆盖 numpy 标量（np.integer/np.floating）——numpy int64 不是 Python
    int 的子类，只判 (int, float) 会把同一类值归一化成两种 key。
    """
    if pd.isna(v):
        return None
    if isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool):
        return round(float(v), 6)
    return str(v)


def _result_contains(golden_df: pd.DataFrame, agent_df: pd.DataFrame) -> bool:
    """golden 值集合 ⊆ Agent 结果值集合。多查列/行不扣分，只看值集合。"""
    golden_cells = {_cell_key(v) for v in golden_df.values.flat}
    golden_cells.discard(None)
    if not golden_cells:
        return False
    agent_cells = {_cell_key(v) for v in agent_df.values.flat}
    return golden_cells <= agent_cells


def _answer_number_check(final_text: str, golden_df: pd.DataFrame) -> bool:
    """多步推理兜底：最终答案文本里出现 golden 数值（容差 0.1% 且 ≥1 元）。"""
    target = float(golden_df.iloc[0, 0])
    nums = [float(x.replace(",", "")) for x in re.findall(r"-?\d[\d,]*\.?\d*", final_text)]
    return any(abs(n - target) <= max(1.0, abs(target) * 0.001) for n in nums)


def _judge_sql_case(case: dict, final_text: str, records: list) -> dict:
    """sql 题判定：值包含直通 → LLM judge → 答案数值兜底。"""
    dataset = case.get("dataset", DEFAULT_DATASET)
    golden_df = run_sql(case["golden_sql"], dataset)

    # 1) 免费直通：存在某条已执行 SQL 结果包含 golden 全部值
    for sql, _ in records:
        try:
            if _result_contains(golden_df, run_sql(sql, dataset)):
                return {
                    "passed": True, "via": "containment", "reason": "执行产物值集合包含 golden",
                    "confidence": 1.0, "issue_tags": ["correct"],
                }
        except Exception:
            continue

    # 2) LLM judge：对每个可执行产物评分，任一判过即过
    judged: list[dict] = []
    for sql, _ in records:
        try:
            agent_df = run_sql(sql, dataset)
        except Exception:
            continue
        verdict = eval_judge.judge_execution(case["question"], golden_df, agent_df)
        _tls.judge_calls += 1
        if verdict is not None:
            judged.append(verdict)
            if verdict.get("passed") is True:
                verdict.setdefault("issue_tags", ["correct"])
                return {"passed": True, "via": "judge", **verdict}
    if judged:
        return {"passed": False, "via": "judge", **judged[0]}

    # 3) judge 解析失败 / 无可用执行产物：答案数值兜底
    #    条件：无 SQL 调用，或 SQL 全部被护栏拦截（链路失败≠结果错误，
    #    模型仍交付正确答案则取数目标达成——v1 回退精神）
    if golden_df.size == 1 and (
        not records or all("[被护栏拦截]" in r for _, r in records)
    ):
        passed = _answer_number_check(final_text, golden_df)
        return {
            "passed": passed, "via": "answer_fallback",
            "reason": "无可用执行产物（无调用或全部被拦截），答案数值判定",
            "confidence": 1.0 if passed else 0.5,
        }
    return {"passed": False, "via": "no_result", "reason": "无可用执行产物且 judge 未出结论", "confidence": 0.0}


def _judge_behavior_case(case: dict, final_text: str, records: list) -> dict:
    """行为题判定：LLM judge → 关键词兜底。"""
    verdict = eval_judge.judge_behavior(
        case["question"], case["expected_behavior"], final_text
    )
    _tls.judge_calls += 1
    if verdict is not None:
        return {"passed": bool(verdict.get("passed")), "via": "judge", **verdict}
    passed = eval_judge.keyword_fallback(case["type"], final_text)
    return {
        "passed": passed, "via": "keyword_fallback",
        "reason": "judge 解析失败，关键词兜底", "confidence": 0.0,
    }


_JUDGES = {
    "sql": _judge_sql_case,
    "clarify": _judge_behavior_case,
    "clarify_small": _judge_behavior_case,
    "negative": _judge_behavior_case,
}


def judge_case(case: dict, final_text: str, records: list) -> dict:
    """按题型分派判分。"""
    return _JUDGES[case["type"]](case, final_text, records)


def judge_first_shot(sql: str, case: dict) -> bool:
    """首轮正确：第一次 query_data 的 SQL 结果即包含 golden 全部值。"""
    dataset = case.get("dataset", DEFAULT_DATASET)
    try:
        return _result_contains(
            run_sql(case["golden_sql"], dataset), run_sql(sql, dataset)
        )
    except Exception:
        return False
