"""随机出货模拟数据：用于 /df 测试命令。

物品表全部来自 delta-force-overlay 的真实接口数据（ID ↔ 名称 ↔ 单价），
保证渲染出来的图片和文字一一对应。
"""

from __future__ import annotations

import random

# ── 红（grade 6）—— 真实数据 ──
RED_ITEMS: list[tuple[str, str, int]] = [
    ("15080050030", "曼德尔超算单元", 1980976),
    ("15080050222", "浓缩铀样本", 1558671),
    ("15020010033", "火箭燃料", 1498562),
    ("15080050067", "强力吸尘器", 1406233),
    ("15209500054", "阿萨拉牌-大王", 888888),
    ("15030010012", "摄影机", 648420),
    ("15030050013", "G.T.I卫星通信天线", 471504),
    ("15010050001", "黄金瞪羚", 424260),
    ("15030050018", "军用控制终端", 381206),
    ("15080050031", "棘龙爪化石", 341803),
    ("15060080015", "奥莉薇娅香槟", 332874),
    ("15080050010", "万足金条", 325849),
    ("15070050001", "量子存储", 273884),
    ("15070040003", "实验数据", 225445),
    ("15080050014", "赛伊德的怀表", 211061),
    ("15080050042", "名贵机械表", 206842),
    ("15080050178", "超声波切割刀", 197713),
    ("15200000060", "鎏金卡牌", 192239),
    ("15080050167", "定位接收器", 180715),
    ("15080050131", "“钻石”鱼子酱", 171238),
    ("15080050004", "功绩勋章", 80001),
]

# ── 房卡（1505xx）—— 真实数据 ──
KEYCARD_ITEMS: list[tuple[str, str, int, int]] = [
    ("15050500009", "食堂洗碗间", 4, 1336813),
    ("15050200007", "西区大门", 4, 836596),
    ("15050600006", "仿星器控制室", 4, 774948),
    ("15050200012", "3号宿舍里屋", 4, 640090),
    ("15050400007", "旅店员工休息室", 4, 290619),
    ("15050400008", "西城民宅卧室", 4, 200273),
    ("15050100011", "西楼医务室", 3, 118006),
    ("15050100009", "变电站宿舍", 3, 37096),
]

PLAYER_NAMES = [
    "鬼汐酱", "雪喵Meow", "波黑软脚虾Niko", "小純喵喵",
    "三角洲老兵", "哈夫币猎人", "撤离失败专业户", "带出必成功",
]

MAP_IDS = [2202, 8902, 3902, 2201, 3901, 8102, 1902]


def random_battle(seed: int | None = None):
    """生成一局随机的出货数据。

    Returns:
        (battle, item_ids) —— battle 是 LootBattle，item_ids 是需要下载图片的物品 id。
    """
    import datetime

    try:
        from .df_loot_core import LootBattle, LootItem  # noqa: PLC0415
    except ImportError:
        from df_loot_core import LootBattle, LootItem  # noqa: PLC0415

    rng = random.Random(seed)

    # 1~3 件红
    reds = rng.sample(RED_ITEMS, k=rng.randint(1, 3))
    # 30% 概率带 1~2 张房卡
    keys = (
        rng.sample(KEYCARD_ITEMS, k=rng.randint(1, 2))
        if rng.random() < 0.3
        else []
    )

    items: list[LootItem] = []
    for oid, name, price in reds:
        items.append(
            LootItem(
                id=oid,
                name=name,
                grade=6,
                num=rng.choice([1, 1, 1, 2]),
                price=price,
                is_red=True,
                is_keycard=False,
            )
        )
    for oid, name, grade, price in keys:
        items.append(
            LootItem(
                id=oid,
                name=name,
                grade=grade,
                num=1,
                price=price,
                is_red=False,
                is_keycard=True,
            )
        )

    escape = rng.random() < 0.7
    gained = sum(it.price * it.num for it in items)
    gained = int(gained * rng.uniform(0.92, 1.12))

    now = datetime.datetime.now()
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")

    battle = LootBattle(
        room_id="test_" + str(rng.randint(100000, 999999)),
        time=time_str,
        gained=gained,
        escape=escape,
        kills=rng.randint(0, 9),
        map_id=rng.choice(MAP_IDS),
        player=rng.choice(PLAYER_NAMES),
        avatar="",
        items=items,
    )
    return battle, [it.id for it in items]
