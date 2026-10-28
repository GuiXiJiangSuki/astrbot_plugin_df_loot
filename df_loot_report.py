"""接收其他玩家的 overlay 上报（多客户端统一播报）。

场景
────
别人的电脑跑 delta-force-overlay，出货时主动 POST 到本插件暴露的 HTTP 接口，
由 NAS 上的插件统一渲染并推送到 QQ 群。

安全
────
* 需要 Token 鉴权（配置项 `df_report_token`），不匹配直接 401
* 只接受白名单字段，不执行任何用户提供的代码
* 上报的 roomId 做去重，防止重复播报

接口
────
POST /df/report
Body (JSON):
    {
      "token": "...",              # 或走 Header: X-DF-Token
      "roomId": "648526...",
      "player": "鬼汐酱",
      "mapId": 3902,
      "time": "2026-10-28 12:18:46",
      "gained": 6656143,
      "escape": true,
      "kills": 2,
      "items": [
        {"id":"15080050042","name":"名贵机械表","grade":6,"num":1,"price":207146}
      ]
    }
Response:
    {"ok": true, "accepted": true, "broadcast": true}
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

try:
    from .df_loot_core import LootBattle, LootItem
except ImportError:
    from df_loot_core import LootBattle, LootItem


def parse_report(payload: dict[str, Any]) -> LootBattle | None:
    """把上报的 JSON 转成 LootBattle。字段缺失或非法时返回 None。"""
    if not isinstance(payload, dict):
        return None

    room_id = str(payload.get("roomId") or "").strip()
    if not room_id:
        return None

    # 兼容 overlay 可能直接给整个 last 对象
    src = payload
    if isinstance(payload.get("last"), dict):
        src = payload["last"]
        room_id = str(src.get("roomId") or room_id).strip()

    items: list[LootItem] = []
    raw_items = src.get("items") or src.get("collections") or []
    if isinstance(raw_items, list):
        for x in raw_items:
            if not isinstance(x, dict):
                continue
            try:
                grade = int(x.get("grade") or 0)
                num = int(x.get("num") or 1)
                price = int(x.get("price") or 0)
                items.append(
                    LootItem(
                        id=str(x.get("id") or ""),
                        name=str(x.get("name") or "未知"),
                        grade=grade,
                        num=num,
                        price=price,
                        is_red=bool(x.get("isRed")) or grade == 6,
                        is_keycard=bool(x.get("isKey"))
                        or str(x.get("id") or "").startswith("1505"),
                    )
                )
            except (TypeError, ValueError):
                continue

    try:
        gained = int(src.get("gained") or src.get("gainedPrice") or 0)
    except (TypeError, ValueError):
        gained = 0

    try:
        kills = int(src.get("kills") or src.get("killPlayer") or 0)
    except (TypeError, ValueError):
        kills = 0

    try:
        map_id = int(src.get("mapId") or 0)
    except (TypeError, ValueError):
        map_id = 0

    escape = src.get("escape")
    if escape is None:
        # overlay 的 gameResult: 0=成功
        gr = src.get("gameResult")
        escape = (gr == 0) if gr is not None else True

    return LootBattle(
        room_id=room_id,
        time=str(src.get("time") or src.get("dtEventTime") or ""),
        gained=gained,
        escape=bool(escape),
        kills=kills,
        map_id=map_id,
        player=str(src.get("player") or src.get("characName") or "未知"),
        avatar=str(src.get("avatar") or ""),
        items=items,
    )
