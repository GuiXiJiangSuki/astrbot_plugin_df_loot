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


def parse_battle_time(raw: str) -> float | None:
    """把对局时间字符串解析成 Unix 时间戳（秒）。

    支持的格式（overlay 与上报都可能出现）：
      · "2026-11-02 21:13:18"
      · "2026-11-02T21:13:18"
      · "2026-11-02T21:13:18.000Z"       （ISO，UTC）
      · "2026/11/02 21:13:18"
      · 纯数字（秒级或毫秒级时间戳）
    解析失败返回 None。
    """
    s = str(raw or "").strip()
    if not s:
        return None

    # 纯数字时间戳
    if s.isdigit():
        v = int(s)
        # 13 位按毫秒处理
        return v / 1000.0 if v > 10_000_000_000 else float(v)

    # ISO 带时区（...Z 或 +08:00）
    iso = s.replace("Z", "+00:00")
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(iso)
        # 无时区信息时按本地时间处理（overlay 给的是本地时间）
        return dt.timestamp()
    except (TypeError, ValueError):
        pass

    # 常见分隔符归一化后重试
    norm = s.replace("/", "-").replace("T", " ")
    # 去掉毫秒后缀
    if "." in norm:
        norm = norm.split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m-%d %H:%M:%S"):
        try:
            from datetime import datetime

            dt = datetime.strptime(norm, fmt)
            if fmt.startswith("%m"):
                # 补上当年
                dt = dt.replace(year=datetime.now().year)
            return dt.timestamp()
        except (TypeError, ValueError):
            continue
    return None


def battle_age_seconds(battle: "LootBattle") -> float | None:
    """对局距今多少秒；时间字段缺失/解析失败返回 None。"""
    ts = parse_battle_time(getattr(battle, "time", ""))
    if ts is None:
        return None
    return max(0.0, time.time() - ts)


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
    # 真实图标地址（软件/接口可能带上，形如
    # https://playerhub.df.qq.com/playerhub/60004/object/gun/ammo/300BLK.png）
    pic: str = ""

    @property
    def total(self) -> int:
        return self.price * self.num

    @property
    def pic_url(self) -> str:
        """默认猜测的图标地址（部分物品这个路径是 404）。"""
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

    async def fetch_item_pic(self, item_id: str, pic: str = "") -> bytes | None:
        """下载物品图标。

        图标地址有两种情况（实测）：
          · 普通物品：playerhub.df.qq.com/.../object/<id>.png     → 200
          · 弹药等：  playerhub.df.qq.com/.../object/gun/ammo/300BLK.png
                     按 <id>.png 拼会 404，必须用数据里带的 pic 字段

        所以按优先级尝试：
          1. pic（数据里给的真实地址）
          2. <id>.png 直接拼
          3. 走 overlay 的 /itemurl/?u=<pic> 代理（软件侧有缓存，最可靠）
        """
        if not item_id or aiohttp is None:
            return None

        candidates: list[str] = []
        if pic and pic.startswith("http"):
            candidates.append(pic)
        candidates.append(
            f"https://playerhub.df.qq.com/playerhub/60004/object/{item_id}.png"
        )
        # 软件代理：把真实地址交给 overlay 下载（它有 md5 缓存，第二次开始秒开）
        if pic and pic.startswith("http") and self.base_url:
            from urllib.parse import quote

            candidates.append(f"{self.base_url}/itemurl/?u={quote(pic, safe='')}")

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        for url in candidates:
            try:
                async with aiohttp.ClientSession(timeout=timeout) as sess:
                    async with sess.get(url) as resp:
                        if resp.status != 200:
                            continue
                        body = await resp.read()
                        # 404 的 CDN 会返回一小段 XML，按大小过滤掉
                        if len(body) > 500:
                            return body
            except Exception as exc:  # noqa: BLE001
                logger.debug("[df_loot] 物品图下载失败 %s (%s): %s", item_id, url, exc)
                continue
        logger.debug("[df_loot] 物品图全部候选失败：%s", item_id)
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
                    # overlay 的 items 里可能带真实图标地址
                    pic=str(it.get("pic") or it.get("picUrl") or it.get("icon") or ""),
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

    两级去重：
      1. roomId —— 同一局只播报一次（首选，最准确）
      2. 内容指纹 —— 时间+玩家+带出值+物品集合的哈希。
         roomId 为空、或软件重复上报时 roomId 变了但内容没变，
         用指纹再拦一道，避免同一次出货播报两遍。
    """

    def __init__(self, max_keep: int = 500) -> None:
        self._seen: dict[str, float] = {}
        self._seen_fp: dict[str, float] = {}
        self._max_keep = max_keep

    def _gc(self) -> None:
        for store in (self._seen, self._seen_fp):
            if len(store) <= self._max_keep:
                continue
            # 按时间排序，丢掉最旧的一半
            ordered = sorted(store.items(), key=lambda kv: kv[1])
            for key, _ in ordered[: len(ordered) // 2]:
                store.pop(key, None)

    @staticmethod
    def fingerprint(battle: "LootBattle") -> str:
        """内容指纹：同一局无论从哪条路径来，指纹都应一致。"""
        parts = [
            str(getattr(battle, "time", "") or ""),
            str(getattr(battle, "player", "") or ""),
            str(getattr(battle, "gained", "") or ""),
            str(getattr(battle, "map_id", "") or ""),
            str(getattr(battle, "escape", "") or ""),
        ]
        # 物品按 id 排序后拼进去，避免顺序差异导致指纹不同
        ids = sorted(str(getattr(x, "id", "") or "") for x in (getattr(battle, "items", None) or []))
        parts.append(",".join(ids))
        raw = "|".join(parts)
        # 用 hashlib 而不是内置 hash()：后者在不同进程间不稳定
        try:
            import hashlib

            return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:16]
        except Exception:  # noqa: BLE001
            return raw[:64]

    def is_new(self, room_id: str, battle: "LootBattle | None" = None) -> bool:
        """是否是新对局。有 roomId 就查 roomId，同时用指纹兜底。"""
        fp = self.fingerprint(battle) if battle is not None else ""
        if fp and fp in self._seen_fp:
            return False
        if room_id and room_id in self._seen:
            return False
        # roomId 为空时，只能靠指纹判断
        if not room_id:
            return bool(fp)
        return True

    def mark(self, room_id: str, battle: "LootBattle | None" = None) -> None:
        now = time.time()
        if room_id:
            self._seen[room_id] = now
        if battle is not None:
            fp = self.fingerprint(battle)
            if fp:
                self._seen_fp[fp] = now
        self._gc()

    def mark_many(self, room_ids: list[str]) -> None:
        for rid in room_ids:
            self.mark(rid)

    @property
    def count(self) -> int:
        return len(self._seen)
