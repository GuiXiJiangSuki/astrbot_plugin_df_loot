"""排行榜日报卡片渲染（PIL 直接绘制）。

参照软件「排行榜」页面样式：
  - 每人一个卡片，左侧头像 + 名次 + 名字 + 统计，右侧带出价值
  - 红装一行横向排列（每行最多 3 个，带图 + 名称 + 价值）
  - 房卡一行横向排列
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageDraw

try:
    from .df_loot_card import _font, _fmt_amount
except ImportError:
    from df_loot_card import _font, _fmt_amount

# ── 配色 ──
BG_TOP = (24, 27, 35)
BG_BOT = (13, 15, 20)
CARD_BG = (22, 26, 34)
CARD_BORDER = (42, 48, 60)
ITEM_BG = (17, 20, 26)
GOLD = (245, 189, 82)
GOLD2 = (255, 220, 138)
SILVER = (200, 208, 220)
BRONZE = (198, 133, 80)
TEXT = (238, 241, 246)
SUB = (154, 165, 181)
DIM = (95, 104, 117)
RED = (255, 110, 110)
BLUE = (126, 200, 255)
GREEN = (74, 222, 128)


def _grad_bg(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h), BG_BOT)
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t)
        g = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t)
        b = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t)
        d.line([(0, y), (w, y)], fill=(r, g, b))
    return img


def _circle_avatar(img: Image.Image, data: bytes | None, size: int, xy: tuple) -> None:
    """贴圆形头像。"""
    x, y = xy
    if data:
        try:
            av = Image.open(io.BytesIO(data)).convert("RGBA")
            w, h = av.size
            s = min(w, h)
            av = av.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))
            av = av.resize((size, size), Image.LANCZOS)
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
            img.paste(av, (x, y), mask)
            return
        except Exception:  # noqa: BLE001
            pass
    d = ImageDraw.Draw(img)
    d.ellipse((x, y, x + size, y + size), fill=(48, 54, 66))


def _draw_item_row(
    img: Image.Image,
    d: ImageDraw.ImageDraw,
    items: list[dict],
    *,
    x0: int,
    y0: int,
    avail_w: int,
    pics: dict[str, bytes],
    S,
    per_row: int = 3,
    row_h: int = 64,
    gap: int = 8,
) -> int:
    """横向绘制物品行，每行最多 per_row 个。返回结束 y。"""
    if not items:
        return y0

    card_w = (avail_w - gap * (per_row - 1)) // per_row
    # card_w 是每张卡的逻辑宽度；文字区 = card_w - 图标(34) - 左右间距
    # per_row=3 时约 231px，15pt 中文可放约 15 字

    for idx, it in enumerate(items):
        col = idx % per_row
        row = idx // per_row
        cx = x0 + col * (card_w + gap)
        cy = y0 + row * (row_h + gap)

        d.rounded_rectangle(
            [S(cx), S(cy), S(cx + card_w), S(cy + row_h)],
            radius=S(8), fill=ITEM_BG, outline=(34, 40, 50), width=S(1),
        )

        # 图标：等比缩放保持比例，居中
        icon_sz = 34
        icon_x = cx + 8
        icon_y = cy + (row_h - icon_sz) // 2
        pic = str(it.get("pic") or "")
        drawn = False
        if pic and pic in pics:
            try:
                im2 = Image.open(io.BytesIO(pics[pic])).convert("RGBA")
                im2.thumbnail((S(icon_sz), S(icon_sz)), Image.LANCZOS)
                pw, ph = im2.size
                px = S(icon_x) + (S(icon_sz) - pw) // 2
                py = S(icon_y) + (S(icon_sz) - ph) // 2
                img.paste(im2, (px, py), im2)
                drawn = True
            except Exception:  # noqa: BLE001
                pass
        if not drawn:
            d.rounded_rectangle(
                [S(icon_x), S(icon_y), S(icon_x + icon_sz), S(icon_y + icon_sz)],
                radius=S(6), fill=(26, 30, 38),
            )

        tx = icon_x + icon_sz + 8
        maxw = card_w - (tx - cx) - 6   # 右侧只留 6，文字区最大化
        f_nm = _font(S(15), False)
        nm = str(it.get("name") or "?")
        num = int(it.get("num") or 1)
        # 名称按可用宽度动态截断（不硬编码字符数，卡宽多少就显示多少）
        disp = nm
        if d.textlength(disp, font=f_nm) > S(maxw):
            # 逐字回退，直到能放下
            cut = len(disp)
            while cut > 1 and d.textlength(disp[:cut] + "…", font=f_nm) > S(maxw):
                cut -= 1
            disp = disp[:cut] + "…"
        d.text((S(tx), S(cy + 8)), disp, font=f_nm, fill=TEXT)
        if num > 1:
            d.text((S(tx + d.textlength(disp, font=f_nm) + 5), S(cy + 10)),
                   f"×{num}", font=_font(S(14), False), fill=SUB)

        tot = int(it.get("total") or 0)
        ps = _fmt_amount(tot)
        d.text((S(tx), S(cy + 34)), ps, font=_font(S(16)), fill=GOLD2)

    rows = (len(items) + per_row - 1) // per_row
    return y0 + rows * (row_h + gap)


def render_rank_daily(
    *,
    date: str,
    players: list[dict[str, Any]],
    scale: int = 2,
    item_pics: dict[str, bytes] | None = None,
    avatars: dict[str, bytes] | None = None,
    logo: bytes | None = None,
    max_players: int = 5,
    per_row: int = 3,
) -> bytes:
    """渲染排行榜日报图。"""
    players = players[:max_players]
    pics = item_pics or {}
    avs = avatars or {}

    W = 960
    PAD = 20
    HEAD_H = 96
    ROW_HEAD = 104
    GROUP_LABEL_H = 32
    ROW_H = 64
    ROW_GAP = 8
    PLAYER_GAP = 14

    inner_w = W - PAD * 2 - 32

    heights = []
    for p in players:
        reds = p.get("redItems") or []
        keys = p.get("keyItems") or []
        h = ROW_HEAD
        if reds:
            rows = (len(reds) + per_row - 1) // per_row
            h += GROUP_LABEL_H + rows * (ROW_H + ROW_GAP)
        if keys:
            rows = (len(keys) + per_row - 1) // per_row
            h += GROUP_LABEL_H + rows * (ROW_H + ROW_GAP)
        h += 10
        heights.append(h)

    body_h = sum(heights) + PLAYER_GAP * max(0, len(players) - 1)
    total_h = HEAD_H + body_h + PAD + 34

    img = _grad_bg(W * scale, total_h * scale)
    d = ImageDraw.Draw(img)

    def S(v: float) -> int:
        return int(v * scale)

    # ── 头部（logo 与两行文字整体垂直居中）──
    f_title = _font(S(32))
    f_sub = _font(S(15), False)

    # 两行文字高度（全部用逻辑单位，贴图时再 S()）
    title_h = 38         # 标题 32pt 行高
    sub_h = 18           # 副标题 15pt 行高
    text_gap = 4
    text_block_h = title_h + text_gap + sub_h   # 60

    title_y = 14
    lsz = 44             # logo 逻辑尺寸
    logo_y = title_y + (text_block_h - lsz) // 2   # 14 + 8 = 22

    title_x = PAD
    if logo:
        try:
            lg = Image.open(io.BytesIO(logo)).convert("RGBA")
            lg = lg.resize((S(lsz), S(lsz)), Image.LANCZOS)
            img.paste(lg, (S(PAD), S(logo_y)), lg)
            title_x = PAD + lsz + 12
        except Exception:  # noqa: BLE001
            title_x = PAD

    d.text((S(title_x), S(title_y)), "今日出货排行", font=f_title, fill=GOLD2)
    d.text((S(title_x), S(title_y + title_h + text_gap)),
           f"{date} · 全服玩家出货榜", font=f_sub, fill=SUB)

    cnt = f"TOP {len(players)}"
    cw = d.textlength(cnt, font=_font(S(28)))
    d.text((S(W - PAD) - cw, S(22)), cnt, font=_font(S(28)), fill=GOLD)

    d.line([(S(PAD), S(HEAD_H - 10)), (S(W - PAD), S(HEAD_H - 10))],
           fill=(38, 44, 56), width=S(1))

    # ── 每个玩家 ──
    y = HEAD_H
    for idx, p in enumerate(players):
        rank = int(p.get("rank") or (idx + 1))
        name = str(p.get("name") or "未知玩家")
        gained = int(p.get("gained") or 0)
        profit = int(p.get("profit") or 0)
        battles = int(p.get("battles") or 0)
        escapes = int(p.get("escapes") or 0)
        red_n = int(p.get("redCount") or 0)
        key_n = int(p.get("keyCount") or 0)
        reds = (p.get("redItems") or [])
        keys = (p.get("keyItems") or [])
        pid = str(p.get("playerId") or "")

        ph = heights[idx]
        d.rounded_rectangle([S(PAD), S(y), S(W - PAD), S(y + ph)],
                            radius=S(12), fill=CARD_BG,
                            outline=CARD_BORDER, width=S(1))

        rank_col = GOLD if rank == 1 else (SILVER if rank == 2 else (BRONZE if rank == 3 else DIM))

        rank_s = str(rank)
        rw = d.textlength(rank_s, font=_font(S(26)))
        d.text((S(PAD + 14), S(y + 30)), rank_s, font=_font(S(26)), fill=rank_col)

        # 头像
        av_x = PAD + 14 + max(int(rw), 26) + 12
        av_sz = 56
        _circle_avatar(img, avs.get(pid), S(av_sz), (S(av_x), S(y + 18)))

        nx = av_x + av_sz + 12
        d.text((S(nx), S(y + 22)), name, font=_font(S(20)), fill=TEXT)

        esc_pct = round(escapes / battles * 100) if battles else 0
        meta = f"{battles} 局 · 撤离率 {esc_pct}%"
        if red_n:
            meta += f" · 红 {red_n}"
        if key_n:
            meta += f" · 卡 {key_n}"
        d.text((S(nx), S(y + 52)), meta, font=_font(S(15), False), fill=SUB)

        # 右侧：带出价值（大字）
        gs = _fmt_amount(gained)
        gw = d.textlength(gs, font=_font(S(27)))
        d.text((S(W - PAD - 18) - gw, S(y + 16)), gs, font=_font(S(27)), fill=GOLD)
        lw = d.textlength("带出", font=_font(S(12), False))
        d.text((S(W - PAD - 18) - lw, S(y + 48)), "带出", font=_font(S(12), False), fill=DIM)

        # 右侧：净收益（绿色小字，在带出下方）
        if profit:
            ps = "净收益 " + _fmt_amount(profit)
            pw = d.textlength(ps, font=_font(S(13)))
            d.text((S(W - PAD - 18) - pw, S(y + 66)), ps,
                   font=_font(S(13)), fill=GREEN)

        # ── 物品区 ──
        iy = y + ROW_HEAD
        inner_x = PAD + 16

        def draw_group(items: list, label: str, col) -> int:
            nonlocal iy
            if not items:
                return iy
            tag_w = d.textlength(label, font=_font(S(14)))
            d.rounded_rectangle(
                [S(inner_x), S(iy), S(inner_x + tag_w + 22), S(iy + 26)],
                radius=S(6), fill=(18, 20, 26), outline=col, width=S(1),
            )
            d.text((S(inner_x + 11), S(iy + 4)), label, font=_font(S(14)), fill=col)
            iy += GROUP_LABEL_H
            iy = _draw_item_row(img, d, items, x0=inner_x, y0=iy,
                                avail_w=inner_w, pics=pics, S=S,
                                per_row=per_row, row_h=ROW_H, gap=ROW_GAP)
            return iy

        iy = draw_group(reds, f"红 {red_n} 种 · {len(reds)} 件", RED)
        iy = draw_group(keys, f"房卡 {key_n} 种 · {len(keys)} 件", BLUE)

        y += ph + PLAYER_GAP

    # ── 底部 ──
    foot_y = total_h - 26
    d.text((S(PAD), S(foot_y)), "数据来自各玩家软件自动上传",
           font=_font(S(12), False), fill=DIM)
    right = "astrbot_plugin_df_loot"
    rw2 = d.textlength(right, font=_font(S(12), False))
    d.text((S(W - PAD) - rw2, S(foot_y)), right, font=_font(S(12), False), fill=DIM)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
