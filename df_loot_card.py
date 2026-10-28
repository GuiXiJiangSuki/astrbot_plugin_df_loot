"""出货播报卡片：直接用 PIL 绘制。

不用 HTML 渲染，原因是：
  1. AstrBot 的 t2i 网络渲染默认 type=jpeg, quality=40 → 图片不清晰
  2. full_page=True 会带出白色背景
  3. base64 内嵌图片在远程渲染时可能丢失
直接画可控性最高。
"""

from __future__ import annotations

import io
import os
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

try:
    from .df_loot_core import LootBattle, LootItem
except ImportError:
    from df_loot_core import LootBattle, LootItem

from astrbot.api import logger

# ── 配色 ──
BG_TOP = (28, 32, 42)
BG_BOT = (15, 17, 22)
GOLD = (245, 189, 82)
GOLD2 = (255, 220, 138)
GOLD_DIM = (168, 135, 63)
RED = (255, 96, 96)
BLUE = (126, 200, 255)
GREEN = (74, 222, 128)
TEXT = (238, 241, 246)
SUB = (154, 165, 181)
SUB_DIM = (90, 100, 114)
WHITE = (255, 255, 255)

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
]

_font_cache: dict[tuple[int, bool], Any] = {}


def _font(size: int, bold: bool = True) -> Any:
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    for path in FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            idx = 0
            if path.lower().endswith(".ttc"):
                # NotoSansCJK-Bold.ttc 第一个是 JP，SC 在后面；用 index 试
                for i in range(0, 4):
                    try:
                        f = ImageFont.truetype(path, size, index=i)
                        _font_cache[key] = f
                        return f
                    except Exception:  # noqa: BLE001
                        continue
            f = ImageFont.truetype(path, size)
            _font_cache[key] = f
            return f
        except Exception:  # noqa: BLE001
            continue
    f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def _fmt_amount(n: int) -> str:
    n = int(n or 0)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 100_000_000:
        return f"{sign}{n / 100_000_000:.2f}".rstrip("0").rstrip(".") + "亿"
    if n >= 10_000:
        return f"{sign}{n / 10_000:.1f}".rstrip("0").rstrip(".") + "万"
    return f"{sign}{n:,}"


def _fmt_price(n: int) -> str:
    n = int(n or 0)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 10_000:
        return f"{sign}{n / 10_000:.1f}".rstrip("0").rstrip(".") + "万"
    return f"{sign}{n:,}"


def _rounded(draw: Any, box: tuple, radius: int, fill: Any) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _avatar_bytes(pic: bytes | None) -> Image.Image | None:
    if not pic:
        return None
    try:
        im = Image.open(io.BytesIO(pic)).convert("RGBA")
        # 居中裁成正方形
        w, h = im.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        return im.crop((left, top, left + side, top + side))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[df_loot] 头像解码失败: %s", exc)
        return None


def _item_image(pic: bytes | None, size: int) -> Image.Image | None:
    if not pic:
        return None
    try:
        im = Image.open(io.BytesIO(pic)).convert("RGBA")
        # 等比缩放到 size
        im.thumbnail((size, size), Image.LANCZOS)
        return im
    except Exception as exc:  # noqa: BLE001
        logger.debug("[df_loot] 物品图解码失败: %s", exc)
        return None


def render_card(
    battle: LootBattle,
    *,
    avatar_data: bytes | None = None,
    item_pics: dict[str, bytes] | None = None,
    show_avatar: bool = False,
    show_items: bool = True,
    max_items: int = 6,
    scale: int = 2,
) -> bytes:
    """渲染出货卡片，返回 PNG 字节。

    scale=2 表示 2 倍超采样，输出更清晰。
    show_avatar 默认关闭（不显示账号头像）。
    """
    item_pics = item_pics or {}

    # ── 布局参数（逻辑像素）──
    PAD = 18
    HEAD_H = 96          # 头部高度（不含左侧圆形区）
    ICON = 52
    ITEM_H = 66
    W = 520

    reds = battle.reds
    keys = battle.keycards
    # 只展示红和房卡
    show_list = reds + keys
    show_list = sorted(show_list, key=lambda x: (0 if x.is_red else 1, -x.total))

    # 角标高度（没有角标时为 0）
    BADGE_H = 32 if (reds or keys) else 0
    body_rows = show_list[:max_items] if show_items else []
    body_h = len(body_rows) * (ITEM_H + 8) + (10 if body_rows else 0)
    H = HEAD_H + BADGE_H + body_h + 26

    S = scale
    Wp, Hp = W * S, H * S

    # ── 背景 ──
    bg = Image.new("RGB", (Wp, Hp), BG_BOT)
    d = ImageDraw.Draw(bg, "RGBA")
    # 竖向渐变
    for y in range(Hp):
        t = y / max(1, Hp - 1)
        c = (
            int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t),
            int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t),
            int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t),
        )
        d.line([(0, y), (Wp, y)], fill=c)

    def S_(v: float) -> int:
        return int(round(v * S))

    # ── 边框 ──
    d.rounded_rectangle(
        [1, 1, Wp - 2, Hp - 2], radius=S_(16), outline=GOLD + (140,), width=S_(2)
    )

    # ── 头部背景（淡金渐变条）──
    head_h = S_(HEAD_H)
    for x in range(Wp):
        t = x / max(1, Wp - 1)
        a = int(46 * (1 - t) + 10 * t)
        d.line([(x, 0), (x, head_h)], fill=GOLD + (a,))
    d.line([(0, head_h), (Wp, head_h)], fill=GOLD + (55,), width=S_(1))

    # ── 玩家名 / 状态（无左侧圆形区，文字直接靠左）──
    tx = S_(PAD + 4)
    f_name = _font(S_(24))
    f_meta = _font(S_(16))
    d.text((tx, S_(PAD + 6)), battle.player or "未知", font=f_name, fill=TEXT)

    meta_y = S_(PAD + 42)
    meta_x = tx
    if battle.escape:
        d.text((meta_x, meta_y), "撤离成功", font=f_meta, fill=GREEN)
        meta_x += d.textlength("撤离成功", font=f_meta) + S_(12)
    else:
        d.text((meta_x, meta_y), "撤离失败", font=f_meta, fill=RED)
        meta_x += d.textlength("撤离失败", font=f_meta) + S_(12)

    # 地图名
    try:
        from .df_loot_maps import map_name as _map_name
    except ImportError:
        from df_loot_maps import map_name as _map_name

    where = _map_name(battle.map_id)
    if where:
        d.text((meta_x, meta_y), where, font=f_meta, fill=SUB)
        meta_x += d.textlength(where, font=f_meta) + S_(12)

    if battle.kills:
        d.text((meta_x, meta_y), f"{battle.kills} 杀", font=f_meta, fill=GOLD2)

    # ── 右侧带出 ──
    f_gl = _font(S_(13))
    f_gv = _font(S_(30))
    gl = "带出"
    gb = d.textbbox((0, 0), gl, font=f_gl)
    d.text((Wp - S_(PAD) - (gb[2] - gb[0]), S_(PAD + 4)), gl, font=f_gl, fill=SUB)
    amount = _fmt_amount(battle.gained)
    gb2 = d.textbbox((0, 0), amount, font=f_gv)
    aw = gb2[2] - gb2[0]
    d.text(
        (Wp - S_(PAD) - aw - S_(30), S_(PAD + 24)),
        amount, font=f_gv, fill=GOLD,
    )
    d.text(
        (Wp - S_(PAD) - S_(30), S_(PAD + 40)),
        "哈夫币", font=f_gl, fill=GOLD_DIM,
    )

    # ── 角标：红/房卡 ──
    by = S_(HEAD_H + 6)
    bx = S_(PAD)
    if reds:
        tag = f"红 {len(reds)}"
        tw = d.textlength(tag, font=_font(S_(14))) + S_(22)
        _rounded(d, [bx, by, bx + tw, by + S_(25)], S_(7), RED + (42,))
        d.text((bx + S_(11), by + S_(4)), tag, font=_font(S_(14)), fill=RED)
        bx += tw + S_(10)
    if keys:
        tag = f"房卡 {len(keys)}"
        tw = d.textlength(tag, font=_font(S_(14))) + S_(22)
        _rounded(d, [bx, by, bx + tw, by + S_(25)], S_(7), BLUE + (42,))
        d.text((bx + S_(11), by + S_(4)), tag, font=_font(S_(14)), fill=BLUE)
        bx += tw + S_(10)

    # ── 物品列表（只显示红 + 房卡）──
    if body_rows:
        ry = S_(HEAD_H + BADGE_H + 6)
        for it in body_rows:
            # 行底
            row_bg = RED + (26,) if it.is_red else BLUE + (24,)
            row_bd = RED + (70,) if it.is_red else BLUE + (60,)
            _rounded(d, [S_(PAD), ry, Wp - S_(PAD), ry + S_(ITEM_H)], S_(10), row_bg)
            d.rounded_rectangle(
                [S_(PAD), ry, Wp - S_(PAD), ry + S_(ITEM_H)],
                radius=S_(10), outline=row_bd, width=S_(1),
            )

            # 物品图
            pic = _item_image(item_pics.get(it.id), S_(ICON))
            ix = S_(PAD + 9)
            iy = ry + (S_(ITEM_H) - S_(ICON)) // 2
            if pic is not None:
                bg.paste(pic, (ix, iy), pic)
            else:
                _rounded(
                    d,
                    [ix, iy, ix + S_(ICON), iy + S_(ICON)],
                    S_(7), (255, 255, 255, 18),
                )
                # 占位文字
                f_ph = _font(S_(11))
                t = "图"
                tb = d.textbbox((0, 0), t, font=f_ph)
                d.text(
                    (ix + (S_(ICON) - (tb[2] - tb[0])) / 2 - tb[0],
                     iy + (S_(ICON) - (tb[3] - tb[1])) / 2 - tb[1]),
                    t, font=f_ph, fill=SUB_DIM,
                )

            # 名称 + 标签
            nx = ix + S_(ICON) + S_(10)
            ny = ry + S_(11)
            f_nm = _font(S_(16))
            nm = it.name
            d.text((nx, ny), nm, font=f_nm, fill=TEXT)
            nw = d.textlength(nm, font=f_nm)

            # 品质标签
            if it.is_red:
                _rounded(d, [nx + nw + S_(7), ny, nx + nw + S_(38), ny + S_(21)], S_(4), RED + (58,))
                d.text((nx + nw + S_(11), ny + S_(3)), "红", font=_font(S_(12)), fill=RED)
            elif it.is_keycard:
                _rounded(d, [nx + nw + S_(7), ny, nx + nw + S_(52), ny + S_(21)], S_(4), BLUE + (52,))
                d.text((nx + nw + S_(11), ny + S_(3)), "房卡", font=_font(S_(12)), fill=BLUE)

            # 数量 + 价格
            f_sub = _font(S_(13))
            sy = ny + S_(26)
            parts = []
            if it.num > 1:
                parts.append(f"×{it.num}")
            if it.total > 0:
                parts.append(f"{_fmt_price(it.total)} 哈夫币")
            if parts:
                d.text((nx, sy), "  ".join(parts), font=f_sub, fill=SUB)

            ry += S_(ITEM_H + 8)

    # ── 页脚 ──
    f_foot = _font(S_(12))
    d.text((S_(PAD), Hp - S_(24)), battle.time or "", font=f_foot, fill=SUB_DIM)
    foot_r = "三角洲行动 · 出货播报"
    fb = d.textbbox((0, 0), foot_r, font=f_foot)
    d.text((Wp - S_(PAD) - (fb[2] - fb[0]), Hp - S_(24)), foot_r, font=f_foot, fill=SUB_DIM)

    # ── 输出 PNG（透明背景，锐利）──
    out = io.BytesIO()
    bg.save(out, format="PNG", optimize=True)
    return out.getvalue()


def build_brief_text(battle: LootBattle) -> str:
    """一行式文字摘要，配合卡片一起发。

    格式：鬼汐酱 在 零号大坝 12:18 撤离成功，出 2 红 / 1 卡：物品A×1、物品B×2（带出 66.5万）
    """
    try:
        from .df_loot_maps import map_name
    except ImportError:
        from df_loot_maps import map_name

    who = battle.player or "未知"
    where = map_name(battle.map_id) or "未知地图"

    t = ""
    s = str(battle.time or "")
    if len(s) >= 16:
        t = s[11:16]

    status = "撤离成功" if battle.escape else "撤离失败"

    bits = []
    n_red = len(battle.reds)
    n_key = len(battle.keycards)
    if n_red:
        bits.append(f"{n_red} 红")
    if n_key:
        bits.append(f"{n_key} 卡")
    loot = " / ".join(bits) if bits else "无出货"

    head = f"{who} 在 {where}"
    if t:
        head += f" {t}"
    head += f" {status}"

    line = f"{head}，出 {loot}"

    names = [f"{x.name}×{x.num}" for x in battle.reds]
    names += [f"{x.name}×{x.num}" for x in battle.keycards]
    if names:
        line += "：" + "、".join(names[:6])
        if len(names) > 6:
            line += f" 等 {len(names)} 件"

    line += f"（带出 {_fmt_amount(battle.gained)}）"
    return line


def build_text_message(battle: LootBattle) -> str:
    """纯文字播报（渲染失败时的兜底）。"""
    try:
        from .df_loot_maps import map_name
    except ImportError:
        from df_loot_maps import map_name

    lines = []
    status = "撤离成功" if battle.escape else "撤离失败"
    where = map_name(battle.map_id)
    where_txt = f"在 {where} " if where else ""
    lines.append(f"【三角洲出货】{battle.player or '未知'} {where_txt}{status}")
    lines.append(f"带出：{_fmt_amount(battle.gained)} 哈夫币")
    if battle.kills:
        lines.append(f"击杀：{battle.kills} 人")

    if battle.reds:
        names = "、".join(f"{x.name}×{x.num}" for x in battle.reds)
        lines.append(f"🔴 红：{names}")
    if battle.keycards:
        names = "、".join(f"{x.name}×{x.num}" for x in battle.keycards)
        lines.append(f"🗝️ 房卡：{names}")

    if battle.time:
        lines.append(f"时间：{battle.time}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 每日汇总
# ═══════════════════════════════════════════════════════════════


def build_daily_text(summary) -> str:
    """每日汇总的文字版。"""
    s = summary
    lines = [f"【三角洲日报】{s.date}"]
    if s.player:
        lines.append(f"玩家：{s.player}")
    lines.append(
        f"今日 {s.games} 局 · 撤离成功 {s.wins} · 失败 {s.losses}"
    )
    if s.kills:
        lines.append(f"干员击杀：{s.kills} 人")
    lines.append(
        f"带出 {_fmt_amount(s.gained)} · 净收益 {_fmt_amount(s.profit)}"
    )

    if s.red_count:
        names = "、".join(f"{x.name}×{x.num}" for x in s.reds[:8])
        more = f" 等 {len(s.reds)} 种" if len(s.reds) > 8 else ""
        lines.append(f"🔴 红 {s.red_count} 件（{_fmt_amount(s.red_value)}）：{names}{more}")
    else:
        lines.append("红：今日无")

    if s.key_count:
        names = "、".join(f"{x.name}×{x.num}" for x in s.keys[:8])
        more = f" 等 {len(s.keys)} 种" if len(s.keys) > 8 else ""
        lines.append(f"🗝️ 房卡 {s.key_count} 张（{_fmt_amount(s.key_value)}）：{names}{more}")
    else:
        lines.append("房卡：今日无")

    return "\n".join(lines)


def render_daily_card(summary, *, item_pics: dict[str, bytes] | None = None, scale: int = 2) -> bytes:
    """渲染每日汇总卡片（PIL）。

    布局：
        ┌──────────────────────────────┐
        │ 三角洲日报  2026-10-23  鬼汐酱│
        │ 34局 · 撤离8 · 击杀15         │
        ├──────────────────────────────┤
        │ 带出 2228万   净收益 1023万   │
        ├──────────────────────────────┤
        │ 红 15 件 · 845万              │
        │  [图] 绝密服务器 ×1  213.4万  │
        │  ...                          │
        │ 房卡 3 张 · 53.6万            │
        │  [图] 浮力室医务间 ×1  30万   │
        └──────────────────────────────┘
    """
    s = summary
    item_pics = item_pics or {}

    PAD = 18
    W = 560
    HEAD_H = 92
    STAT_H = 74
    ICON = 46           # 行内物品图尺寸
    ROW_H = ICON + 20   # 行高（图上下各留 10）
    SEC_GAP = 12

    # 控制条目数，避免图太长
    red_rows = s.reds[:6]
    key_rows = s.keys[:4]
    red_more = max(0, len(s.reds) - len(red_rows))
    key_more = max(0, len(s.keys) - len(key_rows))

    body = 0
    if s.red_count:
        body += 30 + len(red_rows) * ROW_H + (18 if red_more else 0)
    else:
        body += 26
    if s.key_count:
        body += 30 + len(key_rows) * ROW_H + (18 if key_more else 0)
    else:
        body += 26

    H = HEAD_H + STAT_H + body + 56

    S = scale
    Wp, Hp = W * S, H * S

    def S_(v: float) -> int:
        return int(round(v * S))

    # 背景渐变
    bg = Image.new("RGB", (Wp, Hp), BG_BOT)
    d = ImageDraw.Draw(bg, "RGBA")
    for y in range(Hp):
        t = y / max(1, Hp - 1)
        c = (
            int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t),
            int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t),
            int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t),
        )
        d.line([(0, y), (Wp, y)], fill=c)

    # 外边框
    d.rounded_rectangle(
        [1, 1, Wp - 2, Hp - 2], radius=S_(16), outline=GOLD + (140,), width=S_(2)
    )

    # ── 头部 ──
    head_h = S_(HEAD_H)
    for x in range(Wp):
        t = x / max(1, Wp - 1)
        a = int(52 * (1 - t) + 12 * t)
        d.line([(x, 0), (x, head_h)], fill=GOLD + (a,))
    d.line([(0, head_h), (Wp, head_h)], fill=GOLD + (60,), width=S_(1))

    tx = S_(PAD + 4)
    d.text((tx, S_(PAD)), "三角洲日报", font=_font(S_(26)), fill=GOLD)
    if s.date:
        d.text(
            (tx + d.textlength("三角洲日报", font=_font(S_(26))) + S_(12), S_(PAD + 6)),
            s.date,
            font=_font(S_(17)),
            fill=SUB,
        )
    if s.player:
        d.text((tx, S_(PAD + 40)), s.player, font=_font(S_(18)), fill=TEXT)

    # 右上角：局数
    stat = f"{s.games} 局"
    f_stat = _font(S_(22))
    d.text(
        (Wp - S_(PAD) - d.textlength(stat, font=f_stat), S_(PAD + 2)),
        stat,
        font=f_stat,
        fill=GOLD2,
    )

    # ── 战绩条 ──
    sy = head_h + S_(2)
    stat_h = S_(STAT_H)
    d.rectangle([0, sy, Wp, sy + stat_h], fill=(255, 255, 255, 8))

    f_lb = _font(S_(13))
    f_v = _font(S_(24))
    col_w = Wp // 4
    cols = [
        ("撤离", str(s.wins), GREEN),
        ("失败", str(s.losses), RED),
        ("击杀", str(s.kills), GOLD2),
    ]
    for i, (lb, v, color) in enumerate(cols):
        cx = S_(PAD) + i * (S_(110))
        d.text((cx, sy + S_(10)), lb, font=_font(S_(12)), fill=SUB)
        d.text((cx, sy + S_(28)), v, font=_font(S_(22)), fill=color)

    # 右侧：带出 / 净收益
    rx = Wp - S_(PAD)
    pv = _fmt_amount(s.profit)
    pcolor = GREEN if s.profit >= 0 else RED
    pw = d.textlength(pv, font=f_v)
    d.text((rx - pw, sy + S_(26)), pv, font=f_v, fill=pcolor)
    d.text((rx - d.textlength("净收益", font=f_lb), sy + S_(10)),
           "净收益", font=f_lb, fill=SUB)

    gy = sy + stat_h
    d.line([(0, gy), (Wp, gy)], fill=GOLD + (40,), width=S_(1))

    # ── 出货明细 ──
    cy = gy + S_(SEC_GAP)
    left = S_(PAD)
    right = Wp - S_(PAD)

    def section(title: str, count: int, value: int, color, rows, more: int, y: int) -> int:
        # 小节标题
        d.text((left, y), title, font=_font(S_(16)), fill=color)
        info = f"{count} 件 · {_fmt_amount(value)}"
        d.text(
            (right - d.textlength(info, font=_font(S_(14)), ), y + S_(2)),
            info,
            font=_font(S_(14)),
            fill=SUB,
        )
        y += S_(28)

        tx0 = left + S_(10)          # 行内文字起点（图标右侧）
        for it in rows:
            _rounded(d, [left, y, right, y + S_(ROW_H - 8)], S_(8),
                     (255, 255, 255, 12))

            # ── 左侧物品图 ──
            pic = _item_image(item_pics.get(it.id), S_(ICON))
            ix = left + S_(10)
            iy = y + (S_(ROW_H - 8) - S_(ICON)) // 2
            if pic is not None:
                bg.paste(pic, (ix, iy), pic)
            else:
                # 占位框
                _rounded(d, [ix, iy, ix + S_(ICON), iy + S_(ICON)],
                         S_(7), (255, 255, 255, 18))
                f_ph = _font(S_(11))
                ph = "?"
                pb = d.textbbox((0, 0), ph, font=f_ph)
                d.text(
                    (ix + (S_(ICON) - (pb[2] - pb[0])) / 2 - pb[0],
                     iy + (S_(ICON) - (pb[3] - pb[1])) / 2 - pb[1]),
                    ph, font=f_ph, fill=SUB_DIM,
                )

            # ── 文字（图标右侧）──
            nx = ix + S_(ICON) + S_(10)
            # 物品名
            d.text((nx, y + S_(8)), it.name[:12], font=_font(S_(16)), fill=TEXT)
            # 数量 + 价值
            sub = f"×{it.num}  {_fmt_amount(it.total)}"
            d.text((nx, y + S_(32)), sub, font=_font(S_(13)), fill=SUB)
            y += S_(ROW_H)

        if more:
            d.text((left + S_(4), y + S_(2)), f"… 还有 {more} 种",
                   font=_font(S_(13)), fill=SUB_DIM)
            y += S_(18)
        return y + S_(16)

    if s.red_count:
        cy = section("红", s.red_count, s.red_value, RED,
                     red_rows, red_more, cy)
    else:
        d.text((left, cy), "红：今日无", font=_font(S_(15)), fill=SUB_DIM)
        cy += S_(30)

    if s.key_count:
        cy = section("房卡", s.key_count, s.key_value, BLUE,
                     key_rows, key_more, cy)
    else:
        d.text((left, cy), "房卡：今日无", font=_font(S_(15)), fill=SUB_DIM)
        cy += S_(30)

    # ── 页脚 ──
    foot = f"带出 {_fmt_amount(s.gained)}  ·  装备投入 {_fmt_amount(s.equip)}"
    d.text((left, Hp - S_(26)), foot, font=_font(S_(12)), fill=SUB_DIM)
    ft = "三角洲行动 · 每日汇总"
    d.text(
        (right - d.textlength(ft, font=_font(S_(12))), Hp - S_(26)),
        ft, font=_font(S_(12)), fill=SUB_DIM,
    )

    out = io.BytesIO()
    bg.save(out, format="PNG", optimize=True)
    return out.getvalue()

