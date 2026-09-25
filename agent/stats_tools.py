"""统计专用工具集（4 个）：时序趋势 / 多维下钻 / 分布对比 / 相关性分析。

设计要点（铁律 2 延伸）：
- LLM 只传结构化参数，SQL 在本模块内部生成——统计场景下 LLM 没有机会写 SQL，
  护栏理念从"校验 LLM 写的 SQL"升级为"LLM 拿不到 SQL"
- 过滤条件是结构化白名单（op 枚举），值经 duckdb 占位符（?）参数绑定，
  不做字符串拼接——值里出现引号/中文也安全
- 时间列用 TRY_CAST 过滤非法日期（脏数据行不静默计入趋势）
- 结果格式与 analyst_tools 一致（紧凑预览）；Redis 缓存用 stat: 前缀，
  与 sql:/rag: 分桶计数（效率评估各自独立报命中率）
- 复杂自定义统计仍走 query_data 的 sqlglot 白名单护栏——两条路并存
"""

import json

import duckdb
import pandas as pd

import redis_cache
from analyst_tools import _dataset_not_found, _format_result, _get_df

_GRANULARITY_UNITS = {"day": "day", "week": "week", "month": "month"}  # date_trunc 单位白名单
_AGG_FUNCS = {"SUM", "AVG", "COUNT", "MIN", "MAX"}                      # 聚合函数白名单
_FILTER_OPS = {"=", "!=", ">", ">=", "<", "<=", "BETWEEN", "IN"}       # 过滤操作符白名单
_YOY_LAG = {"day": 365, "week": 52, "month": 12}                       # 同比回溯周期数


def _quote(col: str) -> str:
    return f'"{col}"'


def _run_sql_df(name: str, sql: str, params: list = ()) -> pd.DataFrame | str:
    """执行工具内部生成的 SQL（参数化绑定），返回原始 DataFrame；
    数据集不存在/执行失败时返回错误文本（调用方 isinstance(str) 判断）。"""
    try:
        df = _get_df(name)
    except ValueError:
        return _dataset_not_found(name)
    conn = duckdb.connect()
    try:
        conn.register(name, df)
        return conn.execute(sql, params).df()
    except Exception as e:
        return f"查询执行失败: {e}\n请检查参数（列名见 get_dataset_schema）"
    finally:
        conn.close()


def _stat_cache_key(tool: str, name: str, params: dict) -> str:
    """stat: 前缀缓存 key：工具名 + 数据集 + 规范化参数（sort_keys 保证确定性）。"""
    canon = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
    return f"stat:v1:{name}:{redis_cache.digest(tool, canon)}"


def _pct(cur: pd.Series, prev: pd.Series) -> pd.Series:
    """(本期-上期)/上期 百分比；无基期（NULL/首期）显示 —。"""
    pct = (cur / prev - 1) * 100
    return pct.map(lambda v: f"{v:+.1f}%" if pd.notna(v) else "—")


def analyze_trend(
    name: str,
    time_column: str,
    value_column: str | None = None,
    agg: str = "SUM",
    granularity: str = "month",
    compare: str | None = None,
) -> str:
    """时序趋势分析：按时间粒度聚合指标，可选环比（vs 上一周期）与同比（vs 去年同期）。

    什么时候用：用户问"趋势/走势/变化"，或要求按月/周/日看指标并对比上一周期。

    Args:
        name: 数据集名
        time_column: 时间列名（如"购买时间"）
        value_column: 聚合的数值列（如"消费金额"）；agg="COUNT" 时可省略（统计记录数）
        agg: 聚合方式，SUM/AVG/COUNT/MIN/MAX
        granularity: 时间粒度，day/week/month
        compare: None 只要趋势；"mom" 加环比；"yoy" 加同比；"both" 都要
                  （同比需要上一年同期数据，数据集没有时对应列为 —）
    """
    unit = _GRANULARITY_UNITS.get(granularity)
    if unit is None:
        return f"granularity 参数不合法: {granularity}（只支持 day/week/month）"
    agg = (agg or "SUM").upper()
    if agg not in _AGG_FUNCS:
        return f"agg 参数不合法: {agg}（只支持 SUM/AVG/COUNT/MIN/MAX）"
    if compare not in (None, "mom", "yoy", "both"):
        return f"compare 参数不合法: {compare}（只支持 None/mom/yoy/both）"

    metric = _quote(value_column) if value_column else "*"
    period_expr = f"date_trunc('{unit}', TRY_CAST({_quote(time_column)} AS TIMESTAMP))"
    value_expr = f"{agg}({metric})"
    # 非法日期行被 WHERE 过滤，不进入任何周期桶（不静默计入）
    where = f"WHERE TRY_CAST({_quote(time_column)} AS TIMESTAMP) IS NOT NULL"
    select = f"SELECT {period_expr} AS period, {value_expr} AS value"
    if compare in ("mom", "both"):
        select += f", LAG({value_expr}) OVER (ORDER BY {period_expr}) AS prev"
    if compare in ("yoy", "both"):
        select += f", LAG({value_expr}, {_YOY_LAG[granularity]}) OVER (ORDER BY {period_expr}) AS prev_yoy"
    sql = f"{select} FROM {_quote(name)} {where} GROUP BY 1 ORDER BY 1"

    cache_key = _stat_cache_key(
        "trend", name,
        {"t": time_column, "v": value_column, "a": agg, "g": granularity, "c": compare},
    )
    cached = redis_cache.get(cache_key)
    if cached is not None:
        return cached

    df = _run_sql_df(name, sql)
    if isinstance(df, str):
        return df
    fmt = "%Y-%m" if granularity == "month" else "%Y-%m-%d"
    df["period"] = df["period"].dt.strftime(fmt)
    df["value"] = df["value"].round(2)
    if "prev" in df:
        df["环比%"] = _pct(df["value"], df["prev"])
        df = df.drop(columns=["prev"])
    if "prev_yoy" in df:
        df["同比%"] = _pct(df["value"], df["prev_yoy"])
        df = df.drop(columns=["prev_yoy"])
    out = _format_result(df)
    redis_cache.set(cache_key, out, redis_cache.SQL_TTL)
    return out


def drill_down(
    name: str,
    dimensions: list[str],
    value_column: str | None = None,
    agg: str = "SUM",
    filters: list[dict] | None = None,
    top_n: int = 10,
) -> str:
    """多维下钻：按 1-2 个维度分组聚合指标，支持结构化过滤与 Top N 排序。

    什么时候用：用户问"按 X 分组的指标"、"X 里各 Y 的表现"、逐层看明细等
    分组聚合问题——比手写 GROUP BY 更安全（过滤条件结构化，无注入面）。

    Args:
        name: 数据集名
        dimensions: 分组维度列名列表（1-2 个，如 ["商品类别"] 或 ["商品类别","用户性别"]）
        value_column: 聚合的数值列；agg="COUNT" 时可省略（统计记录数）
        agg: 聚合方式，SUM/AVG/COUNT/MIN/MAX
        filters: 结构化过滤条件列表，每项 {"column": 列名, "op": 操作符, "value": 值}；
                 op 支持 = != > >= < <= BETWEEN IN（BETWEEN 的 value 传 [下界,上界]，
                 IN 的 value 传列表）。示例：[{"column": "商品类别", "op": "=", "value": "玩具"}]
        top_n: 按聚合值降序取前 N 组（默认 10，上限 50）
    """
    agg = (agg or "SUM").upper()
    if agg not in _AGG_FUNCS:
        return f"agg 参数不合法: {agg}（只支持 SUM/AVG/COUNT/MIN/MAX）"
    if not isinstance(dimensions, list) or not (1 <= len(dimensions) <= 2):
        return f"dimensions 参数不合法: {dimensions}（需要 1-2 个维度列名）"
    top_n = max(1, min(int(top_n), 50))

    params: list = []
    preds: list[str] = []
    for f in filters or []:
        op = f.get("op")
        if op not in _FILTER_OPS or "column" not in f or "value" not in f:
            return f"filters 项不合法: {f}（需 column/op/value，op 支持 {'/'.join(sorted(_FILTER_OPS))}）"
        col = _quote(f["column"])
        if op == "BETWEEN":
            lo, hi = f["value"]
            params.extend([lo, hi])
            preds.append(f"{col} BETWEEN ? AND ?")
        elif op == "IN":
            vals = list(f["value"])
            params.extend(vals)
            preds.append(f"{col} IN ({', '.join(['?'] * len(vals))})")
        else:
            params.append(f["value"])
            preds.append(f"{col} {op} ?")
    where = (" WHERE " + " AND ".join(preds)) if preds else ""

    metric = _quote(value_column) if value_column else "*"
    group_cols = ", ".join(_quote(d) for d in dimensions)
    sql = (
        f"SELECT {group_cols}, {agg}({metric}) AS value FROM {_quote(name)}"
        f"{where} GROUP BY 1, 2 ORDER BY value DESC LIMIT {top_n}"
    )

    cache_key = _stat_cache_key(
        "drill", name,
        {"d": dimensions, "v": value_column, "a": agg, "f": filters, "n": top_n},
    )
    cached = redis_cache.get(cache_key)
    if cached is not None:
        return cached

    df = _run_sql_df(name, sql, params)
    if isinstance(df, str):
        return df
    out = _format_result(df)
    redis_cache.set(cache_key, out, redis_cache.SQL_TTL)
    return out


def compare_distribution(name: str, group_column: str, value_column: str) -> str:
    """分布对比：按分组列比较数值列的分布（样本量/最小/四分位/中位/最大/均值/标准差）。

    什么时候用：比较不同组（品类/平台/人群）间某个数值的分布差异，
    如"各平台客单价的分布对比"。统计量并排输出，便于识别组间差异。

    Args:
        name: 数据集名
        group_column: 分组列（如"商品类别"）
        value_column: 数值列（如"消费金额"）
    """
    try:
        df = _get_df(name)
    except ValueError:
        return _dataset_not_found(name)
    if group_column not in df.columns:
        return f"分组列不存在: {group_column}（列名见 get_dataset_schema）"
    if value_column not in df.columns:
        return f"数值列不存在: {value_column}（列名见 get_dataset_schema）"

    cache_key = _stat_cache_key("dist", name, {"g": group_column, "v": value_column})
    cached = redis_cache.get(cache_key)
    if cached is not None:
        return cached

    # pandas 3.x：groupby().describe 返回组为行、统计量为列（与 2.x 相反）
    stats = df.groupby(group_column)[value_column].describe(percentiles=[0.25, 0.5, 0.75]).round(2)
    row_names = {
        "count": "样本量", "mean": "均值", "std": "标准差",
        "min": "最小", "25%": "P25", "50%": "中位数", "75%": "P75", "max": "最大",
    }
    lines = [f"分布对比：{group_column} × {value_column}（{len(stats)} 组）"]
    lines.append("组".ljust(10) + "".join(f"{cn:<10}" for cn in row_names.values()))
    for g, row in stats.iterrows():
        cells = "".join(f"{row[stat_name]:<10.10g}" for stat_name in row_names)
        lines.append(str(g).ljust(10) + cells)
    out = "\n".join(lines)
    redis_cache.set(cache_key, out, redis_cache.SQL_TTL)
    return out


def correlation_analysis(name: str, columns: list[str] | None = None) -> str:
    """相关性分析：数值列两两 Pearson 相关系数矩阵（-1~1，绝对值越接近 1 越相关）。

    什么时候用：探索指标间联动关系（如客单价与购买数量、消费金额与单价的
    相关强度）。|r| ≥ 0.5 视为强相关（矩阵中标注 *）。NaN 按对剔除（pairwise），
    不影响其他列组合。

    Args:
        name: 数据集名
        columns: 参与分析的数值列列表；缺省 = 全部数值列（最多 10 列，超出请显式指定）
    """
    try:
        df = _get_df(name)
    except ValueError:
        return _dataset_not_found(name)
    num_cols = list(df.select_dtypes(include="number").columns)
    cols = list(columns) if columns else num_cols
    missing = [c for c in cols if c not in df.columns]
    if missing:
        return f"列不存在: {missing}（列名见 get_dataset_schema）"
    if len(cols) > 10:
        return f"列数过多（{len(cols)} > 10），请显式指定 columns 参数"
    if len(cols) < 2:
        return "至少需要 2 个数值列才能计算相关性"

    cache_key = _stat_cache_key("corr", name, {"c": sorted(cols)})
    cached = redis_cache.get(cache_key)
    if cached is not None:
        return cached

    corr = df[cols].corr().round(3)
    # 手工拼矩阵：to_string 无法在单元格内加 * 标注（|r| ≥ 0.5 强相关）
    lines = ["Pearson 相关矩阵（* 表示 |r| ≥ 0.5 的强相关）："]
    lines.append("        " + "".join(f"{c:>12}" for c in cols))
    for row in cols:
        cells = []
        for col in cols:
            v = corr.loc[row, col]
            mark = "*" if abs(v) >= 0.5 else " "
            cells.append(f"{v:+.3f}{mark}".rjust(12))
        lines.append(str(row).ljust(8) + "".join(cells))
    out = "\n".join(lines)
    redis_cache.set(cache_key, out, redis_cache.SQL_TTL)
    return out
