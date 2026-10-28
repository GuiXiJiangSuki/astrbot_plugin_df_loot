"""每日汇总：从 overlay 拉取今天的全部对局，统计出货。

用于「每天晚上 11:50 播报今天一共出了什么货」。

数据来源：overlay 的 `GET /api/state` 里的 `today` 段，字段实测如下：
    date, gain, profit, equip, count, escapes, kills, hasToday,
    red, redValue, redItems[], redGrouped[],
    keys, keysValue, keyItems[], keyGrouped[]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    import aiohttp
except ImportError:  # 本地测试
    aiohttp = None  # type: ignore[assignment]

try:
    from astrbot.api import logger
except ImportError:
    import logging

    logger = logging.getLogger("astrbot.df_loot")


@dataclass
class LootLine:
    """汇总表里的一行物品。"""

    id: str
    name: str
    num: int
    total: int
    price: int


@dataclass
class DailySummary:
    """一天的出货汇总。"""

    date: str = ""
    player: str = ""
    map_id: int = 0

    games: int = 0
    wins: int = 0
    losses: int = 0
    kills: int = 0

    gained: int = 0      # 今日带出总额
    profit: int = 0      # 今日净收益
    equip: int = 0       # 今日装备投入

    red_count: int = 0
    red_value: int = 0
    key_count: int = 0
    key_value: int = 0

    reds: list[LootLine] = field(default_factory=list)
    keys: list[LootLine] = field(default_factory=list)

    @property
    def has_loot(self) -> bool:
        return bool(self.red_count or self.key_count)

    @property
    def total_loot_value(self) -> int:
        return self.red_value + self.key_value


def _parse_grouped(raw: Any) -> list[LootLine]:
    """解析 overlay 的 redGrouped / keyGrouped。"""
    out: list[LootLine] = []
    if not isinstance(raw, list):
        return out
    for x in raw:
        if not isinstance(x, dict):
            continue
        try:
            out.append(
                LootLine(
                    id=str(x.get("id") or ""),
                    name=str(x.get("name") or "未知"),
                    num=int(x.get("num") or 1),
                    total=int(x.get("total") or 0),
                    price=int(x.get("price") or 0),
                )
            )
        except (TypeError, ValueError):
            continue
    # 按总价值降序
    out.sort(key=lambda i: (-i.total, -i.num))
    return out


async def fetch_daily_summary(
    overlay_url: str,
    *,
    timeout: float = 20.0,
) -> DailySummary | None:
    """从 overlay 拉取今日汇总。失败返回 None。"""
    if aiohttp is None:
        logger.error("[df_loot] 缺少 aiohttp，无法拉取日报数据")
        return None

    base = (overlay_url or "").rstrip("/")
    if not base:
        return None

    tmo = aiohttp.ClientTimeout(total=timeout)
    try:
        async with aiohttp.ClientSession(timeout=tmo) as sess:
            async with sess.get(f"{base}/api/state") as resp:
                if resp.status != 200:
                    logger.warning("[df_loot] 日报：overlay 返回 %s", resp.status)
                    return None
                state = await resp.json(content_type=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[df_loot] 日报：拉取 overlay 失败 %s", exc)
        return None

    if not isinstance(state, dict) or not state.get("ok"):
        logger.warning(
            "[df_loot] 日报：overlay 数据不可用（%s）",
            (state or {}).get("error") if isinstance(state, dict) else "无响应",
        )
        return None

    today = state.get("today") or {}
    player = state.get("player") or {}

    s = DailySummary(
        date=str(today.get("date") or ""),
        player=str(player.get("characName") or ""),
        games=int(today.get("count") or 0),
        wins=int(today.get("escapes") or 0),
        kills=int(today.get("kills") or 0),
        gained=int(today.get("gain") or 0),
        profit=int(today.get("profit") or 0),
        equip=int(today.get("equip") or 0),
        red_count=int(today.get("red") or 0),
        red_value=int(today.get("redValue") or 0),
        key_count=int(today.get("keys") or 0),
        key_value=int(today.get("keysValue") or 0),
    )
    s.losses = max(0, s.games - s.wins)

    s.reds = _parse_grouped(today.get("redGrouped"))
    s.keys = _parse_grouped(today.get("keyGrouped"))

    return s
