"""小黑盒卡战备数据直连模块。

从小黑盒 API 直接拉取卡战备配装数据，不依赖 delta-force-overlay。

接口：
  GET https://api.xiaoheihe.cn/game/deltaforce/fake_battle_value/config_list
  参数：fake_level（档位）、_time、hkey、nonce、version

签名算法（hkey）：
  1. 路径标准化为 /game/deltaforce/fake_battle_value/config_list/
  2. 用 XB_AN 字符表对 time/nonce 做替换
  3. 交错拼接 → md5 → 取后 6 位做位运算 → 取模 100 → 拼成 7 位 hkey

数据解密（xbLg）：
  服务端返回的 items 里部分字段经过 GF(2^8) 风格的混淆，
  用 4 字节分组做异或矩阵还原。

档位映射（fake_level → 目标价值）：
  0 → 11.25万
  1 → 18.75万
  2 → 55万
  3 → 60万
  5 → 78万   （注意：没有 4）
"""

from __future__ import annotations

import hashlib
import random
import time
from typing import Any

try:
    import aiohttp
except ImportError:
    aiohttp = None  # type: ignore[assignment]

# ── 常量（与 delta-force-overlay 保持一致）──
XB_AN = "AB45STUVWZEFGJ6CH01D237IXYPQRKLMN89"
XB_FP = "heyboxweb"
XB_VERSION = "999.0.4"
XB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)
XB_API_LEVEL = "/game/deltaforce/fake_battle_value/config_list"

# 档位：fake_level → (目标价值, 显示名)
XB_LEVELS = [
    (0, 112500, "11.25万"),
    (1, 187500, "18.75万"),
    (2, 550000, "55万"),
    (3, 600000, "60万"),
    (5, 780000, "78万"),
]


# ── 签名算法 ──
def _b1(e: int) -> int:
    """GF(2^8) 乘 2（模多项式 0x1B）。"""
    return ((e << 1) ^ 27) & 255 if e & 128 else (e << 1) & 255


def _nr(e: int) -> int:
    return _b1(e) ^ e


def _gr(e: int) -> int:
    return _nr(_b1(e))


def _jr(e: int) -> int:
    return _gr(_nr(_b1(e)))


def _cn(e: int) -> int:
    return _jr(e) ^ _gr(e) ^ _nr(e)


def _lg(e: list[int]) -> list[int]:
    """4 字节分组异或矩阵还原。"""
    t = list(e)
    t[0] = _cn(e[0]) ^ _jr(e[1]) ^ _gr(e[2]) ^ _nr(e[3])
    t[1] = _nr(e[0]) ^ _cn(e[1]) ^ _jr(e[2]) ^ _gr(e[3])
    t[2] = _gr(e[0]) ^ _nr(e[1]) ^ _cn(e[2]) ^ _jr(e[3])
    t[3] = _jr(e[0]) ^ _gr(e[1]) ^ _nr(e[2]) ^ _cn(e[3])
    return t


def _sub(s: str, table: str) -> str:
    """按字符表做替换。"""
    if not table:
        return s
    return "".join(table[ord(ch) % len(table)] for ch in s)


def _dg(arr: list[str]) -> str:
    """交错拼接多个字符串。"""
    if not arr:
        return ""
    out = []
    maxlen = max(len(x) for x in arr)
    for i in range(maxlen):
        for s in arr:
            if i < len(s):
                out.append(s[i])
    return "".join(out)


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _hkey(path: str, t: int, nonce: str) -> str:
    """计算 hkey 签名。"""
    norm = "/" + "/".join([p for p in path.split("/") if p]) + "/"
    o = _sub(str(t), XB_AN[:-2])
    c = _dg([o, _sub(norm, XB_AN), _sub(nonce, XB_AN)])[:20]
    h = _md5(c)
    g = _lg([ord(ch) for ch in h[-6:]])
    a = str(sum(g) % 100).zfill(2)
    return _sub(h[:5], XB_AN[:-4]) + a


def make_sign(path: str) -> dict[str, Any]:
    """生成签名参数。"""
    ms = int(time.time() * 1000)
    sec = ms // 1000
    nonce = _md5(f"{sec}{ms}{random.random()}{XB_FP}{XB_FP}").upper()
    return {
        "_time": sec,
        "hkey": _hkey(path, sec + 1, nonce),
        "nonce": nonce,
    }


# ── 数据拉取 ──
async def fetch_level(level: int, timeout: float = 25.0) -> list[dict]:
    """拉取指定档位的卡战备方案。失败返回空列表。"""
    if aiohttp is None:
        raise RuntimeError("缺少 aiohttp 依赖")

    sig = make_sign(XB_API_LEVEL)
    params = {
        "app": "heybox",
        "os_type": "web",
        "x_app": "heybox",
        "x_client_type": "weboutapp",
        "x_os_type": "Windows",
        "fake_level": str(level),
        "_time": str(sig["_time"]),
        "hkey": sig["hkey"],
        "nonce": sig["nonce"],
        "version": XB_VERSION,
    }
    headers = {
        "User-Agent": XB_UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.xiaoheihe.cn/",
        "Origin": "https://www.xiaoheihe.cn",
    }
    url = "https://api.xiaoheihe.cn" + XB_API_LEVEL

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as sess:
            async with sess.get(url, params=params, headers=headers) as resp:
                if resp.status != 200:
                    return []
                j = await resp.json(content_type=None)
    except Exception:  # noqa: BLE001
        return []

    if not isinstance(j, dict) or j.get("status") != "ok":
        return []
    result = j.get("result") or {}
    lst = result.get("fake_config_list") or []
    return lst if isinstance(lst, list) else []


def to_scheme(g: dict, target_value: int, scheme_key: str) -> dict[str, Any]:
    """小黑盒组 → 统一方案结构。"""
    upd = int(g.get("config_update_time") or 0)
    items = []
    for it in (g.get("items") or []):
        real = int(it.get("real_price") or 0)
        val = int(it.get("battle_value") or 0)
        items.append({
            "name": str(it.get("name") or ""),
            "grade": int(it.get("grade") or 0),
            "pic": str(it.get("icon") or ""),
            "cost": real,
            "value": val,
            "diff": val - real,
        })
    return {
        "targetValue": target_value,
        "totalCost": int(g.get("real_price") or 0),
        "currentValue": int(g.get("battle_value") or 0),
        "schemeKey": scheme_key,
        "schemeName": str(g.get("config_name") or ""),
        "updatedAt": upd,
        "items": items,
    }


async def fetch_all() -> dict[str, Any]:
    """拉取所有档位的数据。

    返回：{ groups: [{key,name}], byLevel: {目标价值: [方案]} }
    """
    groups: list[dict] = []
    name_key: dict[str, str] = {}
    by_level: dict[int, list] = {}

    for lv, target, _label in XB_LEVELS:
        raw = await fetch_level(lv)
        schemes = []
        for g in raw:
            name = str(g.get("config_name") or f"方案{len(groups) + 1}")
            if name not in name_key:
                key = f"xb{len(name_key)}"
                name_key[name] = key
                groups.append({"key": key, "name": name})
            schemes.append(to_scheme(g, target, name_key[name]))
        by_level[target] = schemes
        # 档位之间稍作延迟，避免触发风控
        if lv != XB_LEVELS[-1][0]:
            import asyncio
            await asyncio.sleep(0.5)

    return {"groups": groups, "byLevel": by_level}


def pick_level(want: int) -> tuple[int, int, str]:
    """按目标价值选最接近的档位，返回 (fake_level, target, label)。"""
    best = XB_LEVELS[0]
    best_diff = abs(best[1] - want)
    for lv, target, label in XB_LEVELS:
        d = abs(target - want)
        if d < best_diff:
            best_diff = d
            best = (lv, target, label)
    return best


def parse_value(text: str) -> int:
    """解析用户输入的价值。

    支持：60w / 60W / 60万 / 600000 / 60,0000
    大写 W 会先转小写再判断，所以 60W 等价 60w。
    """
    s = str(text or "").strip().lower().replace(",", "")
    if not s:
        return 0
    for suffix in ("哈夫币", "万", "w"):
        s = s.replace(suffix, "")
    s = s.strip()
    try:
        v = float(s)
    except (TypeError, ValueError):
        return 0
    if not v or v < 0:
        return 0
    raw = str(text or "").lower()
    if ("万" in raw) or ("w" in raw):
        v *= 10000
    return int(v)
