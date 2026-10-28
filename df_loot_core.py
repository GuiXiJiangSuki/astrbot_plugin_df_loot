"""三角洲出货检测：从 delta-force-overlay 拉取对局，判定红/房卡。"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

# aiohttp 由 requirements.txt 声明，AstrBot 环境自带；
# 这里做软导入是为了让纯逻辑单元可单独导入，不影响运行时行为。
try:
    import aiohttp
except ImportError:
    aiohttp = None  # type: ignore[assignment]

# 统一使用 AstrBot 的 logger（带 plugin_tag 等字段）。
# 不使用 Python 内置 logging：AstrBot 的日志格式化依赖额外字段，
# 自建 logger 会因缺字段导致格式化异常。
from astrbot.api import logger

# 品质等级：红 = grade 6（实测：曼德尔超算单元、量子存储、实验数据、鎏金卡牌等）
GRADE_RED = 6
# 房卡 ID 前缀段：1505xx 整段都是门禁卡
KEYCARD_PREFIX = "1505"
# gameResult：0 = 撤离成功，1 = 撤离失败（反直觉，别搞反）
GAME_RESULT_ESCAPE = 0


def is_red_item(item: dict) -> bool:
    """红 = grade 6。"""
    try:
        return int(item.get("grade") or 0) == GRADE_RED
    except (TypeError, ValueError):
        return False


def is_keycard_item(item: dict) -> bool:
    """房卡 = ID 以 1505 开头。"""
    return str(item.get("id") or "").startswith(KEYCARD_PREFIX)


def is_escape(battle: dict) -> bool:
    """撤离成功 = gameResult 0。"""
    try:
        return int(battle.get("gameResult") or 0) == GAME_RESULT_ESCAPE
    except (TypeError, ValueError):
        return False


@dataclass
class LootItem:
    """一件带出的物品。"""

    id: str
    name: str
    grade: int
    num: int
    price: int
    is_red: bool
    is_keycard: bool

    @property
    def total(self) -> int:
        return self.price * self.num

    @property
    def pic_url(self) -> str:
        return f"https://playerhub.df.qq.com/playerhub/60004/object/{self.id}.png"


@dataclass
class LootBattle:
    """一局对战的出货信息。"""

    room_id: str
    time: str
    gained: int
    escape: bool
    kills: int
    map_id: int
    player: str
    avatar: str
    items: list[LootItem] = field(default_factory=list)

    @property
    def reds(self) -> list[LootItem]:
        return [x for x in self.items if x.is_red]

    @property
    def keycards(self) -> list[LootItem]:
        return [x for x in self.items if x.is_keycard]

    @property
    def has_loot(self) -> bool:
        return bool(self.reds or self.keycards)


class OverlayClient:
    """delta-force-overlay 的 HTTP 客户端。"""

    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout

    async def fetch_state(self) -> dict[str, Any] | None:
        """拉取 /api/state。"""
        if not self.base_url:
            return None
        if aiohttp is None:
            logger.error("[df_loot] 缺少 aiohttp 依赖，无法请求 overlay")
            return None
        url = f"{self.base_url}/api/state"
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(url) as resp:
                    if resp.status != 200:
                        logger.warning("[df_loot] overlay 返回 %s: %s", resp.status, url)
                        return None
                    return await resp.json(content_type=None)
        except asyncio.TimeoutError:
            logger.warning("[df_loot] overlay 请求超时: %s", url)
            return None
        except aiohttp.ClientError as exc:
            logger.warning("[df_loot] overlay 连接失败: %s (%s)", url, exc)
            return None
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("[df_loot] overlay 返回非 JSON: %s", exc)
            return None

    async def fetch_avatar(self, path: str) -> bytes | None:
        """下载账号头像。path 形如 /avatar/xxx。"""
        if not path or not self.base_url or aiohttp is None:
            return None
        if path.startswith("http"):
            url = path
        else:
            url = f"{self.base_url}{path}"
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(url) as resp:
                    if resp.status != 200:
                        return None
                    return await resp.read()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[df_loot] 头像下载失败: %s", exc)
            return None

    async def fetch_item_pic(self, item_id: str) -> bytes | None:
        """下载物品图标（直接走 playerhub CDN）。"""
        if not item_id or aiohttp is None:
            return None
        url = f"https://playerhub.df.qq.com/playerhub/60004/object/{item_id}.png"
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(url) as resp:
                    if resp.status != 200:
                        return None
                    return await resp.read()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[df_loot] 物品图下载失败 %s: %s", item_id, exc)
            return None


def parse_battle(state: dict[str, Any]) -> LootBattle | None:
    """把 /api/state 的 last 段解析成 LootBattle。"""
    last = state.get("last") or {}
    if not last:
        return None

    player = state.get("player") or {}
    raw_items = last.get("items") or []

    items: list[LootItem] = []
    for it in raw_items:
        try:
            items.append(
                LootItem(
                    id=str(it.get("id") or ""),
                    name=str(it.get("name") or "未知物品"),
                    grade=int(it.get("grade") or 0),
                    num=int(it.get("num") or 1),
                    price=int(it.get("price") or 0),
                    # 优先用 overlay 已算好的标记，缺失时自己判
                    is_red=bool(it.get("isRed")) if "isRed" in it else is_red_item(it),
                    is_keycard=(
                        bool(it.get("isKey"))
                        if "isKey" in it
                        else is_keycard_item(it)
                    ),
                )
            )
        except (TypeError, ValueError):
            continue

    return LootBattle(
        room_id=str(last.get("roomId") or ""),
        time=str(last.get("time") or ""),
        gained=int(last.get("gained") or 0),
        escape=bool(last.get("escape")),
        kills=int(last.get("kills") or 0),
        map_id=int(last.get("mapId") or 0),
        player=str(player.get("characName") or ""),
        avatar=str(player.get("avatar") or ""),
        items=items,
    )


class LootTracker:
    """去重 + 出货判定。

    用 roomId 做去重 key。同一局只播报一次。
    """

    def __init__(self, max_keep: int = 500) -> None:
        self._seen: dict[str, float] = {}
        self._max_keep = max_keep

    def _gc(self) -> None:
        if len(self._seen) <= self._max_keep:
            return
        # 按时间排序，丢掉最旧的一半
        ordered = sorted(self._seen.items(), key=lambda kv: kv[1])
        for key, _ in ordered[: len(ordered) // 2]:
            self._seen.pop(key, None)

    def is_new(self, room_id: str) -> bool:
        return bool(room_id) and room_id not in self._seen

    def mark(self, room_id: str) -> None:
        if not room_id:
            return
        self._seen[room_id] = time.time()
        self._gc()

    def mark_many(self, room_ids: list[str]) -> None:
        for rid in room_ids:
            self.mark(rid)

    @property
    def count(self) -> int:
        return len(self._seen)
