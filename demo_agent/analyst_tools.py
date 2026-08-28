"""阶段 1：业务数据分析 Agent 的工具集。

设计原则：
- 返回紧凑：所有工具结果截断（最多 20 行预览 + 总行数），避免撑爆 LLM 上下文
- 只读：工具只查询数据，不修改数据文件
- 护栏：query_data 用 sqlglot 语法树校验，只放行单条 SELECT/WITH，
  表名必须等于数据集名，无 LIMIT 时自动注入
"""

from pathlib import Path

import duckdb
import pandas as pd
import sqlglot
import yaml
from sqlglot import exp

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
METADATA_FILE = DATA_DIR / "datasets.yaml"

MAX_ROWS = 10000       # 单次查询返回上限
PREVIEW_ROWS = 20      # 返回给 LLM 的预览行数

# 数据集名 -> DataFrame（进程内缓存，避免每次查询重复读盘）
_df_cache: dict[str, pd.DataFrame] = {}


# ---------- 内部工具函数 ----------

def _load_metadata() -> dict:
    if METADATA_FILE.exists():
        with open(METADATA_FILE, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _get_df(name: str) -> pd.DataFrame:
    if name not in _df_cache:
        path = DATA_DIR / f"{name}.csv"
        if not path.exists():
            raise ValueError(f"数据集 {name} 不存在，请先用 list_datasets 查看可用数据集")
        _df_cache[name] = pd.read_csv(path)
    return _df_cache[name]


def _count_rows(path: Path) -> int:
    with open(path, "rb") as f:
        return sum(1 for _ in f) - 1  # 减去表头


def _validate_sql(name: str, sql: str) -> tuple[bool, str]:
    """sqlglot 护栏（白名单策略）：返回 (是否通过, 安全SQL 或 错误信息)。

    - 只放行单条 SELECT/WITH/UNION 查询（exp.Select 及其子类），其余语句一律拒绝
    - 表名必须等于数据集名
    - 无 LIMIT 时自动注入
    """
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError as e:
        return False, f"SQL 语法错误，请修正: {e}"

    # 用 parse() 拿全部语句、len 检查语句数：只依赖"返回语句列表"这个稳定契约，
    # 不依赖"多语句会被解析成什么节点"这种版本敏感的行为（parse_one 在部分版本
    # 只解析第一条，Block 节点也不是所有版本都有）
    # 注意：parse("") 返回 [None]（列表长度 1 但元素为空），必须先过滤掉 None
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return False, f"只允许单条 SQL 语句（检测到 {len(statements)} 条）"

    stmt = statements[0]

    # 白名单：只放行查询语句（默认拒绝一切非查询操作）
    if not isinstance(stmt, exp.Select):
        return False, f"只允许查询语句（SELECT/WITH），收到 {stmt.key} 语句"

    tables = [t.name for t in stmt.find_all(exp.Table)]
    if any(t != name for t in tables):
        return False, f"表名不合法: {tables}，只能查询数据集 {name}"

    if not stmt.find_all(exp.Limit):
        stmt = stmt.limit(MAX_ROWS)
    return True, stmt.sql(dialect="duckdb")


def _format_result(df: pd.DataFrame) -> str:
    if df.empty:
        return "查询结果为空（0 行）"
    shown = min(PREVIEW_ROWS, len(df))
    preview = df.head(shown).to_string(index=False)
    return (
        f"查询结果: {len(df)} 行 × {len(df.columns)} 列\n"
        f"{preview}\n"
        f"（仅显示前 {shown} 行，完整结果请用 GROUP BY/聚合进一步汇总）"
    )


# ---------- 对外工具 ----------

def list_datasets() -> str:
    """列出 data/ 目录下所有可分析的数据集：名称、描述、规模。"""
    meta = _load_metadata()
    lines = ["可用数据集："]
    for path in sorted(DATA_DIR.glob("*.csv")):
        name = path.stem
        desc = meta.get(name, {}).get("description", "（无描述）")
        lines.append(f"- {name} | {_count_rows(path)} 行 | {desc}")
    if len(lines) == 1:
        lines.append("（暂无数据，请把 CSV 文件放入 data/ 目录）")
    return "\n".join(lines)


def get_dataset_schema(name: str) -> str:
    """返回数据集的列结构：列名、类型、非空率、示例值、业务含义。

    Args:
        name: 数据集名
    """
    df = _get_df(name)
    meta = _load_metadata().get(name, {}).get("columns", {})
    lines = [f"数据集 {name}：{len(df)} 行 × {len(df.columns)} 列"]
    for col in df.columns:
        sample = df[col].dropna().head(3).tolist()
        biz = meta.get(col, "")
        lines.append(
            f"- {col} | {df[col].dtype} | 非空率 {(df[col].notna().mean()):.1%} "
            f"| 示例: {sample} | {biz}"
        )
    return "\n".join(lines)


def get_data_profile(name: str) -> str:
    """生成数据画像：规模、缺失率、重复率、数值列统计、异常值、分类列分布、日期范围。

    分析任何问题之前都应先调用本工具做数据质量检查。

    Args:
        name: 数据集名
    """
    df = _get_df(name)
    out = [f"数据画像：{name}", f"规模: {len(df)} 行 × {len(df.columns)} 列"]

    # 缺失率
    missing = df.isna().mean().round(3)
    missing_cols = [f"{c} {v:.1%}" for c, v in missing.items() if v > 0]
    out.append("缺失率: " + (", ".join(missing_cols) or "无缺失"))

    # 重复
    out.append(f"完全重复行: {df.duplicated().sum()} ({df.duplicated().mean():.1%})")

    # 数值列统计 + 异常值
    num = df.select_dtypes(include="number")
    if not num.empty:
        stats = num.describe().T[["min", "mean", "50%", "max"]].round(2)
        out.append("数值列统计:\n" + stats.to_string())
        neg = [f"{c} {(df[c] < 0).sum()} 个" for c in num.columns if (df[c] < 0).any()]
        out.append("负值异常: " + (", ".join(neg) or "无"))

    # 分类列基数与 Top 取值
    for col in df.select_dtypes(include="object"):
        top = df[col].value_counts().head(5).index.tolist()
        out.append(f"分类列 {col}: {df[col].nunique()} 个取值, 最多: {top}")

    # 日期列：有效范围 + 非法值数量
    for col in df.columns:
        if "date" in col.lower() or "日期" in col:
            parsed = pd.to_datetime(df[col], errors="coerce")
            invalid = df[col].notna() & parsed.isna()
            out.append(
                f"日期列 {col}: 有效范围 {parsed.min().date()} ~ {parsed.max().date()}, "
                f"非法值 {invalid.sum()} 个"
            )
    return "\n".join(out)


def get_metric_definitions(name: str, keyword: str = "") -> str:
    """查询数据集的指标口径定义——指标如何计算的权威标准。

    计算任何业务指标（销售额、退货率、客单价等）之前必须先调用本工具，
    指标公式以本工具返回的定义和参考 SQL 为准，禁止自行发明口径。

    Args:
        name: 数据集名
        keyword: 可选，按关键词过滤指标（如 "退货"），为空返回全部指标
    """
    all_metrics = _load_metadata().get(name, {}).get("metrics", {})
    if not all_metrics:
        return f"数据集 {name} 尚未定义指标口径，请向用户确认口径后再计算"
    if keyword:
        metrics = {
            k: v for k, v in all_metrics.items()
            if keyword in k or keyword in v.get("定义", "")
        }
        if not metrics:
            return (
                f"未找到包含关键词 {keyword!r} 的指标，"
                f"现有指标: {list(all_metrics.keys())}"
            )
    else:
        metrics = all_metrics
    lines = [f"数据集 {name} 的指标口径（计算指标必须以以下定义为准）："]
    for metric, spec in metrics.items():
        lines.append(f"- {metric}: {spec.get('定义', '')}")
        if spec.get("参考SQL"):
            lines.append(f"  参考SQL: {spec['参考SQL']}")
    return "\n".join(lines)


def query_data(name: str, sql: str) -> str:
    """对数据集执行 SQL 查询（duckdb 方言）。

    安全约束：只允许单条 SELECT/WITH 语句；表名必须等于数据集名；
    自动注入 LIMIT；结果截断为前 20 行预览。

    Args:
        name: 数据集名（SQL 中作为表名使用，列名见 get_dataset_schema）
        sql: 查询语句，例如 "SELECT category, SUM(amount) AS total FROM ecommerce_sales GROUP BY category ORDER BY total DESC"
    """
    ok, result = _validate_sql(name, sql)
    if not ok:
        return f"[被护栏拦截] {result}"
    conn = duckdb.connect()
    try:
        conn.register(name, _get_df(name))
        df = conn.execute(result).df()
    except Exception as e:
        return f"查询执行失败: {e}\n请检查 SQL 或先用 get_dataset_schema 确认列名"
    finally:
        conn.close()
    return _format_result(df)
