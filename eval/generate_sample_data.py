"""生成阶段 1 测试用的模拟电商销售数据。

运行: python demo_agent/generate_sample_data.py
输出: data/ecommerce_sales.csv（约 5000 行）

数据是模拟的，并故意埋入少量脏数据，用于验证 Agent 的数据质量检查能力：
- ~1.5% 的 region 缺失
- ~1% 的 amount 缺失
- 10 个非法/空日期
- ~0.5% 的负 quantity
- ~0.5% 的完全重复行

业务规律（供分析验证）：整体销量逐月增长，双11/618 大促月有尖峰，
不同品类的客单价差异大。
"""

import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

random.seed(42)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
N_ROWS = 5000

REGIONS = ["华东", "华南", "华北", "西南", "东北", "华中"]
REGION_WEIGHTS = [0.28, 0.22, 0.18, 0.13, 0.09, 0.10]

# 品类 -> (单价下限, 单价上限, 商品示例)
CATEGORIES = {
    "手机数码": (800, 12000, ["iPhone 17 Pro", "华为 Mate 80", "小米 16", "OPPO Find X9"]),
    "家用电器": (300, 8000, ["美的空调", "海尔冰箱", "戴森吸尘器", "小米电视"]),
    "服饰鞋包": (50, 800, ["优衣库卫衣", "耐克跑鞋", "李宁羽绒服"]),
    "美妆个护": (30, 500, ["兰蔻小黑瓶", "SK-II 神仙水", "完美日记口红"]),
    "食品生鲜": (10, 200, ["三只松鼠坚果", "阳澄湖大闸蟹", "五常大米"]),
    "图书文娱": (20, 150, ["《三体》全集", "switch 游戏卡带", "儿童绘本套装"]),
}

CHANNELS = ["天猫", "京东", "抖音", "拼多多", "线下门店"]
CHANNEL_WEIGHTS = [0.30, 0.25, 0.20, 0.15, 0.10]

AGE_GROUPS = ["18-25", "26-35", "36-45", "46+"]

START, END = date(2025, 1, 1), date(2026, 7, 31)

# 大促月订单量放大倍数（618、双11 等）
PROMO_BOOST = {
    (2025, 6): 1.6,
    (2025, 11): 2.2,
    (2025, 12): 1.4,
    (2026, 1): 1.3,
    (2026, 6): 2.0,
}


def _month_weight(d: date) -> float:
    """订单量随月份变化：整体逐月增长 + 大促月放大。"""
    w = 1.0 + 0.02 * ((d.year - 2025) * 12 + (d.month - 1))
    return w * PROMO_BOOST.get((d.year, d.month), 1.0)


_MAX_WEIGHT = max(
    _month_weight(date(y, m, 1)) for y in (2025, 2026) for m in range(1, 13)
)


def random_date() -> date:
    """按月度权重抽样（拒绝采样，权重越大被选中的概率越高）。"""
    while True:
        d = START + timedelta(days=random.randint(0, (END - START).days))
        if random.random() < _month_weight(d) / _MAX_WEIGHT:
            return d


def generate_rows(n: int) -> list[dict]:
    rows = []
    for i in range(n):
        category = random.choice(list(CATEGORIES))
        low, high, products = CATEGORIES[category]
        quantity = random.randint(1, 5)
        unit_price = round(random.uniform(low, high), 2)
        rows.append({
            "order_id": f"ORD{20250001 + i:07d}",
            "order_date": random_date().isoformat(),
            "region": random.choices(REGIONS, REGION_WEIGHTS)[0],
            "category": category,
            "product": random.choice(products),
            "channel": random.choices(CHANNELS, CHANNEL_WEIGHTS)[0],
            "customer_age_group": random.choice(AGE_GROUPS),
            "quantity": quantity,
            "unit_price": unit_price,
            "amount": round(quantity * unit_price, 2),
            "is_returned": 1 if random.random() < 0.045 else 0,
        })
    return rows


def add_dirt(df: pd.DataFrame) -> pd.DataFrame:
    """埋入脏数据，并打印埋点清单（便于对照 Agent 是否全部发现）。"""
    n = len(df)
    dirt = []

    # 1. region 缺失 ~1.5%
    idx = random.sample(range(n), int(n * 0.015))
    df.loc[idx, "region"] = None
    dirt.append(f"region 缺失 {len(idx)} 个")

    # 2. amount 缺失 ~1%
    idx = random.sample(range(n), int(n * 0.01))
    df.loc[idx, "amount"] = None
    dirt.append(f"amount 缺失 {len(idx)} 个")

    # 3. 非法日期：5 个 2025-13-01（不存在的月份），5 个空值
    idx = random.sample(range(n), 5)
    df.loc[idx, "order_date"] = "2025-13-01"
    idx = random.sample(range(n), 5)
    df.loc[idx, "order_date"] = ""
    dirt.append("非法日期 10 个（5 个 2025-13-01，5 个空值）")

    # 4. 负 quantity ~0.5%
    idx = random.sample(range(n), int(n * 0.005))
    df.loc[idx, "quantity"] = [-random.randint(1, 3) for _ in idx]
    dirt.append(f"负 quantity {len(idx)} 个")

    # 5. 完全重复行 ~0.5%（把该行替换成另一行的拷贝）
    idx = random.sample(range(n), int(n * 0.005))
    for i in idx:
        j = random.randint(0, n - 1)
        df.iloc[i] = df.iloc[j]
    dirt.append(f"完全重复行 {len(idx)} 个")

    print("已埋入脏数据：")
    for d in dirt:
        print(f"  - {d}")
    return df


def main():
    df = pd.DataFrame(generate_rows(N_ROWS))
    df = add_dirt(df)
    DATA_DIR.mkdir(exist_ok=True)
    out = DATA_DIR / "ecommerce_sales.csv"
    df.to_csv(out, index=False)
    print(f"已生成 {len(df)} 行 × {len(df.columns)} 列 -> {out}")


if __name__ == "__main__":
    main()
