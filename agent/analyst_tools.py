"""业务数据分析 Agent 工具集（取数 5 工具 + create_chart 图表工具）。

设计原则：
- 返回紧凑：所有工具结果截断（最多 20 行预览 + 总行数），避免撑爆 LLM 上下文
- 只读：工具只查询数据，不修改数据文件
- 护栏：query_data 用 sqlglot 语法树校验，只放行单条 SELECT/WITH，
  表名必须等于数据集名，无 LIMIT 时自动注入
- create_chart：契约 v1 校验在工具内部，成功返回规范化 JSON（丢未知键）
"""

import json
from pathlib import Path

import duckdb
import pandas as pd
import sqlglot
import yaml
from sqlglot import exp

import redis_cache  # SQL 归一化结果缓存（铁律 2 延伸：缓存在工具入口内部）

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


def _dataset_not_found(name: str) -> str:
    """数据集名错误的友好提示：直接给出可用名单，省一次 list_datasets 往返。"""
    available = [p.stem for p in sorted(DATA_DIR.glob("*.csv"))]
    return (
        f"数据集 {name!r} 不存在，可用数据集: {', '.join(available)}。"
        f"请用正确的数据集名重试（SQL 表名必须等于数据集名）"
    )


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


CHART_TYPES = ("bar", "line", "pie", "table")


def _validate_chart_spec(spec) -> tuple[bool, str]:
    """图表规格契约 v1 校验：返回 (是否通过, 错误原因)。

    只读已知键，未知键忽略（LLM 可能多传字段，输出时重建规范化 spec 丢弃）。
    """
    if not isinstance(spec, dict):
        return False, "spec 必须是 JSON 对象"
    title = spec.get("title")
    if not isinstance(title, str) or not title.strip():
        return False, "title 必须是非空字符串"
    chart_type = spec.get("chart_type")
    if chart_type not in CHART_TYPES:
        return False, f"chart_type 必须是 {'|'.join(CHART_TYPES)} 之一，收到 {chart_type!r}"
    data = spec.get("data")
    if not isinstance(data, dict):
        return False, "缺少 data 对象"
    categories = data.get("categories")
    if not isinstance(categories, list) or not categories or not all(isinstance(c, str) for c in categories):
        return False, "data.categories 必须是非空字符串列表"
    series = data.get("series")
    if not isinstance(series, list) or not series:
        return False, "data.series 必须是非空列表"
    for i, s in enumerate(series):
        if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not s["name"].strip():
            return False, f"series[{i}].name 必须是非空字符串"
        values = s.get("values")
        # bool 是 int 子类，必须显式排除
        if not isinstance(values, list) or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in values
        ):
            return False, f"series[{i}]（{s['name']}）.values 必须是数值列表"
        if chart_type in ("bar", "line") and len(values) != len(categories):
            return False, (
                f"series[{i}]（{s['name']}）values 长度 {len(values)} "
                f"与 categories 长度 {len(categories)} 不一致"
            )
    return True, ""


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
    try:
        df = _get_df(name)
    except ValueError:
        return _dataset_not_found(name)
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
    try:
        df = _get_df(name)
    except ValueError:
        return _dataset_not_found(name)
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
        if "date" in col.lower() or "日期" in col or "时间" in col:
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
        keyword: 可选，按关键词过滤指标（如 "退货"），为空返回全部指标。
            用户的原话表述可直接传入（如 "卖了多少钱"），工具会按指标名/
            定义/aliases 同义表述表归一匹配，无需自行翻译成指标名。
            关键词命中多个候选口径时，返回会提示必须先向用户确认再计算。
    """
    all_metrics = _load_metadata().get(name, {}).get("metrics", {})
    if not all_metrics:
        return f"数据集 {name} 尚未定义指标口径，请向用户确认口径后再计算"
    if keyword:
        metrics = {
            k: v for k, v in all_metrics.items()
            if keyword in k
            or keyword in v.get("定义", "")
            or any(
                keyword in a or a in keyword  # 双向包含：短别名可命中长原话（"每单多少钱" in "平均每单多少钱"）
                for a in (v.get("aliases") or [])
            )
        }
        if not metrics:
            return (
                f"未找到包含关键词 {keyword!r} 的指标，"
                f"现有指标: {list(all_metrics.keys())}"
            )
    else:
        metrics = all_metrics
    lines = [f"数据集 {name} 的指标口径（计算指标必须以以下定义为准）："]
    # 防参考SQL锚定：模型曾照抄总量模板、漏掉问题中的品类条件（如"玩具"），
    # 故明确声明参考SQL只是公式模板，过滤条件必须从用户问题提取。
    lines.append(
        "注意：参考SQL仅为口径公式模板，不含任何业务过滤条件；"
        "查询时必须按用户问题自行提取全部维度条件（平台/品类/时间/地区等）加入 WHERE。"
    )
    for metric, spec in metrics.items():
        lines.append(f"- {metric}: {spec.get('定义', '')}")
        if spec.get("参考SQL"):
            lines.append(f"  参考SQL: {spec['参考SQL']}")
    # 歧义提示：关键词命中多个候选口径 = 用户表述存在歧义信号。
    # 放在工具返回里（而非只靠 prompt 规则），模型按"数据"服从，实测比规则有效。
    if keyword and len(metrics) >= 2:
        lines.append(
            f"⚠️ 关键词 {keyword!r} 匹配到 {len(metrics)} 个候选口径"
            f"（{', '.join(metrics)}）：用户未指明具体用哪个指标时，"
            f"必须先向用户确认再计算，禁止默认选择其一作答。"
        )
    return "\n".join(lines)


def query_data(name: str, sql: str) -> str:
    """对数据集执行 SQL 查询（duckdb 方言）。

    安全约束：只允许单条 SELECT/WITH 语句；表名必须等于数据集名；
    自动注入 LIMIT；结果截断为前 20 行预览。

    Args:
        name: 数据集名（SQL 中作为表名使用，列名见 get_dataset_schema）
        sql: 查询语句，例如 "SELECT category, SUM(amount) AS total FROM ecommerce_sales GROUP BY category ORDER BY total DESC"

    分桶查询（CASE WHEN 区间分组）写法要求：每个区间显式写上下界（如
    WHEN 用户年龄 > 40），禁止用 ELSE 兜底；未覆盖的取值用 WHERE 排除或反问用户。
    示例：
    SELECT CASE WHEN 用户年龄 BETWEEN 20 AND 30 THEN '20-30岁'
                WHEN 用户年龄 BETWEEN 31 AND 40 THEN '31-40岁'
                WHEN 用户年龄 > 40 THEN '40岁以上' END AS 年龄段,
           COUNT(*) AS 订单数
    FROM 淘宝 WHERE 商品类别 = '玩具' AND 用户年龄 >= 20 GROUP BY 1
    """
    ok, result = _validate_sql(name, sql)
    if not ok:
        return f"[被护栏拦截] {result}"
    # SQL 结果缓存：key 用重生成的规范 SQL（白名单已过、方言已定，
    # sqlglot 输出确定性格式化）+ 数据集名。命中直接返回——数据文件只读，
    # TTL 内结果恒定；护栏拦截不缓存（错误信息本来零成本）
    cache_key = f"sql:v1:{name}:{redis_cache.digest(result)}"
    cached = redis_cache.get(cache_key)
    if cached is not None:
        return cached
    conn = duckdb.connect()
    try:
        conn.register(name, _get_df(name))
        df = conn.execute(result).df()
    except Exception as e:
        return f"查询执行失败: {e}\n请检查 SQL 或先用 get_dataset_schema 确认列名"
    finally:
        conn.close()
    out = _format_result(df)
    redis_cache.set(cache_key, out, redis_cache.SQL_TTL)
    return out


def create_chart(spec: dict) -> str:
    """生成标准图表规格 JSON（前端据此渲染图表）。校验通过后返回规范化 JSON 字符串。

    chart spec 契约 v1：
    {
      "title": "图表标题",
      "chart_type": "bar | line | pie | table",
      "data": {
        "categories": ["维度取值", ...],
        "series": [{"name": "序列名", "values": [数值, ...]}]
      }
    }
    选型规则：时间趋势 → line；分类对比 → bar；占比 → pie；明细列表 → table。
    调用时机：仅在用户明确要求画图/图表时才调用，用户没要求时不要主动生成图表。
    约束：bar/line 的每个 series 的 values 长度必须等于 categories 长度；
    pie 通常单个 series；table 的 categories 是列头、series 是行。
    数据未确认（口径/数值未核实）前不要调用本工具。
    校验失败会返回原因，请按错误信息修正后重试。

    Args:
        spec: 图表规格对象（契约字段之外的键会被忽略）
    """
    ok, reason = _validate_chart_spec(spec)
    if not ok:
        return f"[图表校验失败] {reason}"
    # 重建规范化 spec：只保留契约字段，丢弃 LLM 多传的未知键
    normalized = {
        "title": spec["title"].strip(),
        "chart_type": spec["chart_type"],
        "data": {
            "categories": spec["data"]["categories"],
            "series": [
                {"name": s["name"].strip(), "values": s["values"]}
                for s in spec["data"]["series"]
            ],
        },
    }
    try:
        # allow_nan=False：NaN/Infinity 直接报错，让 LLM 修正而不是产出前端解析不了的数据
        return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except ValueError as e:
        return f"[图表校验失败] 数值不合法（NaN/Infinity）: {e}"
