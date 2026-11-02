"""地图底图：从官方地图工具抓瓦片，拼成缩略图当卡片背景。

数据来源
────────
https://df.qq.com/cp/a20240729directory/index.html（三角洲行动官方地图工具）

官方把每张地图切成瓦片，放在 ossweb-img CDN 上，路径规则：
    https://ossweb-img.qq.com/images/dfm/cp/a20240729directory/img/<图层>/<z>_<x>_<y>.jpg

每个「图层」对应一张地图（或某个地图的某个楼层）。顶层地图图层与地图名的对应
关系是从官方 main.js 的 mapConfigs 里提取的（2026-11 实测）：

    零号大坝   map_db      z=0   （只有 1 张瓦片，直接就是全图）
    长弓溪谷   map_yc      z=2
    航天基地   map_htjd    z=2
    巴克什     map_bks2    z=2
    潮汐监狱   map_cxjy    z=1
    AZ3        map_az3     z=1

不同地图可用层级不一样，所以这里用「按 z 从 0 往上试，命中就用」的策略，
第一次抓到时把「地图 → (图层, z, 网格范围)」记进缓存，后续直接命中。

缓存
────
拼好的缩略图存 <插件目录>/.cache/maps/<map_key>.jpg，
同时把拼装参数存 <插件目录>/.cache/maps/index.json。
这样只需要联网一次，之后离线也能用。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

try:
    from astrbot.api import logger
except ImportError:  # pragma: no cover - 单测/脚本环境
    import logging

    logger = logging.getLogger(__name__)

# 插件目录下的缓存目录（与插件源码同级，随插件一起分发/清理）
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "maps")
_INDEX_FILE = os.path.join(_CACHE_DIR, "index.json")

_CDN = "https://ossweb-img.qq.com/images/dfm/cp/a20240729directory/img"

# 地图 ID 段 → (显示名, 图层目录, 提示层级)
# 按 mapId 的前缀判断（mapId 末位是难度：0=常规 1=机密 2=绝密）
MAP_ID_PREFIX: list[tuple[str, str, str, int]] = [
    # (mapId 前缀, 地图名, 图层目录, 建议起始 z)
    ("22", "零号大坝", "map_db", 0),
    ("19", "长弓溪谷", "map_yc", 2),
    ("39", "航天基地", "map_htjd", 2),
    ("81", "巴克什", "map_bks2", 2),
    ("88", "潮汐监狱", "map_cxjy", 1),
    ("89", "AZ3", "map_az3", 1),
]

_TILE_SIZE = 256


def map_key(map_id: int | str) -> str:
    """mapId → 图层标识（如 8901 → 'map_az3'）。

    未知地图返回空串（调用方跳过背景）。
    """
    try:
        mid = str(int(map_id or 0))
    except (TypeError, ValueError):
        return ""
    if not mid or mid == "0":
        return ""
    for prefix, _name, layer, _z in MAP_ID_PREFIX:
        if mid.startswith(prefix):
            return layer
    return ""


def map_display_name(map_id: int | str) -> str:
    try:
        mid = str(int(map_id or 0))
    except (TypeError, ValueError):
        return ""
    for prefix, name, _layer, _z in MAP_ID_PREFIX:
        if mid.startswith(prefix):
            return name
    return ""


def _suggest_z(layer: str) -> int:
    for _p, _n, lay, z in MAP_ID_PREFIX:
        if lay == layer:
            return z
    return 2


def _index_load() -> dict[str, Any]:
    try:
        with open(_INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def _index_save(data: dict[str, Any]) -> None:
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[df_loot] 地图缓存索引写入失败: %s", exc)


def cached_map_path(map_id: int | str) -> str | None:
    """已缓存的地图缩略图路径；没有则 None。"""
    layer = map_key(map_id)
    if not layer:
        return None
    p = os.path.join(_CACHE_DIR, f"{layer}.jpg")
    return p if os.path.isfile(p) and os.path.getsize(p) > 2000 else None


async def _fetch(session: Any, url: str) -> bytes | None:
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            body = await resp.read()
            # 404 时 CDN 返回一小段 XML，按大小过滤
            return body if len(body) > 1000 else None
    except Exception:  # noqa: BLE001
        return None


async def build_map_thumb(map_id: int | str, *, timeout: float = 20.0) -> str | None:
    """抓取并拼接地图缩略图，返回本地文件路径；失败返回 None。

    已缓存时直接返回缓存路径（不会重复下载）。
    """
    layer = map_key(map_id)
    if not layer:
        return None

    hit = cached_map_path(map_id)
    if hit:
        return hit

    try:
        import aiohttp
    except ImportError:
        logger.debug("[df_loot] 没有 aiohttp，无法下载地图底图")
        return None

    os.makedirs(_CACHE_DIR, exist_ok=True)
    out_path = os.path.join(_CACHE_DIR, f"{layer}.jpg")

    index = _index_load()
    meta = index.get(layer) or {}
    tm = aiohttp.ClientTimeout(total=timeout)

    # 层级选择策略
    # ─────────────
    # 之前固定「从建议层级开始试，命中就用」，导致零号大坝用了 z=0
    # （只有 1 张 256px 瓦片）被拉伸到卡片宽度 → 非常糊。
    # 正确做法：从低到高遍历，选**拼接后分辨率刚好够用**的那一档：
    #   目标 ~1400px（卡片头部 520 逻辑像素 × 2 倍超采样 = 1040，
    #   再留一点余量），太小会糊，太大浪费流量。
    TARGET_PX = 1400
    MAX_TILES = 64          # 单次最多下 64 张（8×8），防止失控
    candidates = [0, 1, 2, 3, 4, 5]
    if meta.get("z") is not None:
        # 已缓存过：直接用记下的层级（并允许往上跳一档以提升清晰度）
        cached_z = int(meta["z"])
        candidates = [cached_z] + [z for z in candidates if z > cached_z]

    async with aiohttp.ClientSession(timeout=tm) as sess:
        # 依次尝试，记录每档的拼接尺寸，最后挑最合适的一档
        best: dict[str, Any] | None = None

        for z in candidates:
            tiles: dict[tuple[int, int], bytes] = {}
            max_n = 8
            for y in range(max_n):
                got_row = False
                for x in range(max_n):
                    url = f"{_CDN}/{layer}/{z}_{x}_{y}.jpg"
                    body = await _fetch(sess, url)
                    if body:
                        tiles[(x, y)] = body
                        got_row = True
                # 某一行一个都没拿到 → 认为到底了
                if not got_row and y > 0:
                    break
                if len(tiles) > MAX_TILES:
                    break

            if not tiles:
                continue

            cols = max(x for x, _ in tiles) + 1
            rows = max(y for _, y in tiles) + 1
            px_w = cols * _TILE_SIZE
            px_h = rows * _TILE_SIZE

            # 这一档已经够清晰：用它，并停止往上试（省流量）
            if px_w >= TARGET_PX:
                best = {"z": z, "tiles": tiles, "cols": cols, "rows": rows}
                break
            # 不够清晰：先记下（作为兜底），继续试更高层级
            if best is None or px_w > best["cols"] * _TILE_SIZE:
                best = {"z": z, "tiles": tiles, "cols": cols, "rows": rows}

        if not best:
            logger.debug("[df_loot] 地图底图抓取失败：%s", layer)
            return None

        # 拼图
        try:
            from PIL import Image
            import io

            z = best["z"]
            tiles = best["tiles"]
            cols, rows = best["cols"], best["rows"]
            canvas = Image.new(
                "RGB", (cols * _TILE_SIZE, rows * _TILE_SIZE), (12, 14, 18)
            )
            for (x, y), body in tiles.items():
                try:
                    im = Image.open(io.BytesIO(body)).convert("RGB")
                    if im.size != (_TILE_SIZE, _TILE_SIZE):
                        im = im.resize((_TILE_SIZE, _TILE_SIZE))
                    canvas.paste(im, (x * _TILE_SIZE, y * _TILE_SIZE))
                except Exception:  # noqa: BLE001
                    continue
            # 统一缩到目标宽度（保持比例）
            if canvas.width != TARGET_PX:
                ratio = TARGET_PX / canvas.width
                canvas = canvas.resize(
                    (TARGET_PX, max(1, int(canvas.height * ratio))), Image.LANCZOS
                )
            canvas.save(out_path, "JPEG", quality=86)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[df_loot] 地图拼接失败 %s: %s", layer, exc)
            return None

        index[layer] = {
            "z": z,
            "cols": cols,
            "rows": rows,
            "tiles": len(tiles),
        }
        _index_save(index)
        logger.info(
            "[df_loot] 地图底图已缓存：%s（z=%s %dx%d，%d 张瓦片，%dpx 宽）",
            layer, z, cols, rows, len(tiles), TARGET_PX,
        )
        return out_path


async def ensure_maps(map_ids: list[int | str], *, timeout: float = 20.0) -> dict[str, str]:
    """批量准备：返回 {图层: 本地路径}。

    只处理还没有缓存的；有缓存的直接命中，不发请求。
    """
    out: dict[str, str] = {}
    todo: list[str] = []
    for mid in map_ids:
        layer = map_key(mid)
        if not layer or layer in out:
            continue
        hit = cached_map_path(mid)
        if hit:
            out[layer] = hit
        elif layer not in todo:
            todo.append(layer)

    for layer in todo:
        # 反查一个 mapId 来复用 build（简单起见直接按 layer 找建议信息）
        mid_guess = None
        for prefix, _n, lay, _z in MAP_ID_PREFIX:
            if lay == layer:
                mid_guess = int(prefix + "01")
                break
        if mid_guess is None:
            continue
        p = await build_map_thumb(mid_guess, timeout=timeout)
        if p:
            out[layer] = p
    return out


def thumb_for(map_id: int | str) -> str | None:
    """同步接口：取已缓存的地图缩略图（卡片渲染时用）。"""
    return cached_map_path(map_id)
