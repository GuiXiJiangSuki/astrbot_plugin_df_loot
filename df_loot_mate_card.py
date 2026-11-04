"""队友出货排行卡片渲染（PIL 直接绘制）。

参照软件「排行榜」页面的样式（与 df_loot_rank_card 同款）：
  - 每人一个卡片：左侧头像 + 名次 + 名字 + 统计，右侧总带出/净收益
  - 下方横向排列该队友的高价值物品（图 + 名称 + 价值）
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageDraw

try:
    from .df_loot_card import _font, _fmt_amount
    from .df_loot_rank_card import (
        BG_TOP, BG_BOT, CARD_BG, CARD_BORDER, ITEM_BG,
        GOLD, GOLD2, SILVER, BRONZE, TEXT, SUB, DIM, RED, BLUE, GREEN,
        _grad_bg, _circle_avatar,
    )
except ImportError:
    from df_loot_card import _font, _fmt_amount
    from df_loot_rank_card import (
        BG_TOP, BG_BOT, CARD_BG, CARD_BORDER, ITEM_BG,
        GOLD, GOLD2, SILVER, BRONZE, TEXT, SUB, DIM, RED, BLUE, GREEN,
        _grad_bg, _circle_avatar,
    )


def _fmt_duration(sec: int) -> str:
    """秒 → 「X 分钟」"""
    n = int(sec or 0)
    if n <= 0:
        return "--"
    if n < 60:
        return f"{n} 秒"
    m = n / 60
    return f"{round(m)} 分钟" if m >= 10 else f"{m:.1f} 分钟"


def render_mate_rank(
    *,
    date_label: str,
    players: list[dict[str, Any]],
    scale: int = 2,
    avatars: dict[str, bytes] | None = None,
    item_pics: dict[str, bytes] | None = None,
    max_players: int = 10,
    items_per_player: int = 4,
) -> bytes:
    """渲染队友出货排行卡片。

    players: [{name, avatar, battles, escapes, escapeRate, gained, profit,
               kills, duration, items:[{name, pic, total, num}]}]
    avatars: 名字 → 头像 bytes
    item_pics: 物品名 → 图标 bytes
    """
    players = players[:max_players]
    avs = avatars or {}
    pics = item_pics or {}

    W = 960
    PAD = 20
    HEAD_H = 100
    ROW_HEAD = 96          # 卡片上半部分（头像+名字+统计+右侧数值）
    ITEM_H = 66            # 物品行高度
    ROW_GAP = 12

    inner_w = W - PAD * 2 - 32

    def S(v):
        return int(round(v * scale))

    # ── 计算高度 ──
    heights = []
    for p in players:
        its = (p.get("items") or [])[:items_per_player]
        body = ROW_HEAD
        if its:
            body += ITEM_H + 8
        heights.append(body)

    H = HEAD_H + sum(heights) + ROW_GAP * max(0, len(players) - 1) + 46
    img = _grad_bg(S(W), S(H))
    d = ImageDraw.Draw(img)

    # ── 标题 ──
    title = "今日队友出货排行"
    if date_label:
        title = f"{date_label} 队友出货排行"
    d.text((S(PAD), S(24)), title, font=_font(S(30), True), fill=GOLD2)
    d.text((S(PAD), S(64)), f"共 {len(players)} 名队友 · 按净收益排序",
           font=_font(S(16), False), fill=SUB)

    # 右上角 TOP N
    cnt = f"TOP {len(players)}"
    cw = d.textlength(cnt, font=_font(S(26), True))
    d.text((S(W - PAD) - cw, S(26)), cnt, font=_font(S(26), True), fill=GOLD)

    d.line([(S(PAD), S(HEAD_H - 12)), (S(W - PAD), S(HEAD_H - 12))],
           fill=(38, 44, 56), width=S(1))

    # ── 逐人绘制 ──
    y = HEAD_H
    for idx, p in enumerate(players):
        rank = p.get("rank") or (idx + 1)
        row_h = heights[idx]

        # 卡片背景
        d.rounded_rectangle(
            [S(PAD), S(y), S(W - PAD), S(y + row_h)],
            radius=S(12), fill=CARD_BG, outline=CARD_BORDER, width=S(1),
        )

        # 名次徽章
        badge_col = GOLD if rank == 1 else (SILVER if rank == 2 else (BRONZE if rank == 3 else DIM))
        d.text((S(PAD + 18), S(y + 24)), str(rank),
               font=_font(S(28), True), fill=badge_col)

        # 头像
        name = str(p.get("name") or "未知")
        av_size = 44
        av_x = PAD + 56
        _circle_avatar(img, avs.get(name), S(av_size), (S(av_x), S(y + 18)))

        # 名字
        nx = av_x + av_size + 12
        d.text((S(nx), S(y + 20)), name, font=_font(S(22), True), fill=TEXT)

        # 统计行
        battles = int(p.get("battles") or 0)
        escapes = int(p.get("escapes") or 0)
        rate = p.get("escapeRate")
        if rate is None:
            rate = round(escapes / battles * 100) if battles else 0
        kills = int(p.get("kills") or 0)
        dur = _fmt_duration(p.get("duration") or 0)

        meta = f"{battles} 局 · 撤离 {escapes} · 撤离率 {rate}% · {kills} 杀 · {dur}"
        d.text((S(nx), S(y + 52)), meta, font=_font(S(15), False), fill=SUB)

        # 右侧：总带出（大字）+ 净收益（绿/红小字）
        gained = int(p.get("gained") or 0)
        profit = int(p.get("profit") or 0)

        gs = _fmt_amount(gained)
        gw = d.textlength(gs, font=_font(S(26), True))
        d.text((S(W - PAD - 20) - gw, S(y + 18)), gs, font=_font(S(26), True), fill=GOLD)
        lw = d.textlength("带出", font=_font(S(12), False))
        d.text((S(W - PAD - 20) - lw, S(y + 50)), "带出",
               font=_font(S(12), False), fill=DIM)

        ps = ("净收益 " if profit >= 0 else "净亏损 ") + _fmt_amount(abs(profit))
        pw = d.textlength(ps, font=_font(S(14), True))
        d.text((S(W - PAD - 20) - pw, S(y + 68)), ps,
               font=_font(S(14), True), fill=GREEN if profit >= 0 else RED)

        # ── 物品行 ──
        its = (p.get("items") or [])[:items_per_player]
        if its:
            iy = y + ROW_HEAD
            ix = PAD + 20
            card_w = min(200, int((inner_w - 12 * (len(its) - 1)) / max(1, len(its))))
            for it in its:
                iname = str(it.get("name") or "")
                itotal = int(it.get("total") or 0)
                inum = int(it.get("num") or 1)

                d.rounded_rectangle(
                    [S(ix), S(iy), S(ix + card_w), S(iy + ITEM_H - 8)],
                    radius=S(8), fill=ITEM_BG, outline=(34, 40, 50), width=S(1),
                )
                # 图标
                ic = pics.get(iname)
                icon_size = 34
                iox = ix + 10
                ioy = iy + (ITEM_H - 8 - icon_size) // 2
                if ic:
                    try:
                        im = Image.open(io.BytesIO(ic)).convert("RGBA")
                        im = im.resize((S(icon_size), S(icon_size)), Image.LANCZOS)
                        img.paste(im, (S(iox), S(ioy)), im)
                    except Exception:
                        pass
                else:
                    d.rounded_rectangle(
                        [S(iox), S(ioy), S(iox + icon_size), S(ioy + icon_size)],
                        radius=S(5), fill=(26, 30, 38),
                    )

                # 名称（截断）
                tx = iox + icon_size + 8
                max_chars = max(4, int((card_w - icon_size - 30) / 7.2))
                disp = iname if len(iname) <= max_chars else iname[:max_chars - 1] + "…"
                d.text((S(tx), S(iy + 10)), disp, font=_font(S(13), True), fill=TEXT)

                # 价值
                val = _fmt_amount(itotal) + (f" ×{inum}" if inum > 1 else "")
                d.text((S(tx), S(iy + 30)), val, font=_font(S(13), True), fill=GOLD)

                ix += card_w + 12

        y += row_h + ROW_GAP

    # ── 底部 ──
    d.text((S(PAD), S(H - 30)), "数据来自各玩家软件自动上传 · 仅统计有队友的对局",
           font=_font(S(13), False), fill=DIM)
    foot = "astrbot_plugin_df_loot"
    fw = d.textlength(foot, font=_font(S(13), False))
    d.text((S(W - PAD) - fw, S(H - 30)), foot, font=_font(S(13), False), fill=DIM)

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
