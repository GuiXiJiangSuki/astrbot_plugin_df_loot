"""三角洲地图 ID → 地图名映射。

数据来源
────────
与 delta-force-overlay 的 `public/settings.html` 里的 `MAP_ALIAS` 表保持一致
（该表由作者抓包实测确认，2026-10 更新）。

官方接口没有地图名字段，只有 `mapId`；难度（常规/机密/绝密）也编码在 mapId 里，
同一张图连续编号：常规 → 机密 → 绝密 依次 +1。

早期的确认依据（已合并进上表）：
  3902 → 航天基地·绝密   截图 10/21 11:54 带出 6,656,143 ↔ 接口 12:21 mapId=3902
  2202 → 零号大坝·机密   截图 10/21 09:26 带出   600,725 ↔ 接口 09:36 mapId=2202
  8902 → AZ3核电站·机密  截图 10/20 01:18 带出 3,718,601 ↔ 接口 01:46 mapId=8902

新增地图时在这里补一行即可。
"""

from __future__ import annotations

# 完整地图对照表（与 overlay 同步）
MAP_NAMES: dict[int, str] = {
    # ── 长弓溪谷 ──
    1901: "长弓溪谷·常规",
    1902: "长弓溪谷·机密",
    # ── 零号大坝（白天：常规/机密；黑夜大坝：永夜已实测）──
    2201: "零号大坝·常规",
    2202: "零号大坝·机密",
    2232: "零号大坝·永夜",
    # ── 航天基地 ──
    3901: "航天基地·机密",
    3902: "航天基地·绝密",
    # ── 巴克什 ──
    8101: "巴克什·常规",
    8102: "巴克什·机密",
    8103: "巴克什·绝密",
    # ── 潮汐监狱 ──
    8801: "潮汐监狱·常规",
    8802: "潮汐监狱·机密",
    8803: "潮汐监狱·绝密",
    # ── AZ3 核电站 ──
    8901: "AZ3核电站·常规",
    8902: "AZ3核电站·机密",
    8903: "AZ3核电站·绝密",
}


def map_name(map_id: int | str, *, with_difficulty: bool = True) -> str:
    """把 mapId 转成地图名。

    Args:
        map_id: 接口返回的 mapId
        with_difficulty: True 返回「航天基地·绝密」，False 只返回「航天基地」

    Returns:
        地图名；未知 mapId 返回「地图<id>」；空值返回空串。
    """
    try:
        mid = int(map_id or 0)
    except (TypeError, ValueError):
        return ""
    if not mid:
        return ""

    name = MAP_NAMES.get(mid)
    if name is None:
        return f"地图{mid}"

    if not with_difficulty and "·" in name:
        return name.split("·", 1)[0]
    return name
