"""卡战备配装卡片渲染（PIL 直接绘制）。

参照 delta-force-overlay 的卡战备页面布局：
  顶部标题 + 目标价值
  每组方案一个卡片：方案名 / 总览 / 物品列表

不用 HTML 渲染的原因同 df_loot_card：
  AstrBot 的 t2i 默认 jpeg 低质量、full_page 带白底、base64 图可能丢。
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageDraw

try:
    from .df_loot_card import _font, _fmt_amount
except ImportError:
    from df_loot_card import _font, _fmt_amount

# ── 配色（和软件卡战备页一致）──
BG_TOP = (24, 27, 35)
BG_BOT = (13, 15, 20)
CARD_BG = (22, 26, 34)
CARD_BORDER = (42, 48, 60)
GOLD = (245, 189, 82)
GOLD2 = (255, 220, 138)
TEXT = (238, 241, 246)
SUB = (154, 165, 181)
DIM = (95, 104, 117)
GREEN = (74, 222, 128)
RED = (255, 110, 110)


def dedup_items(items: list[dict]) -> list[dict]:
    """小黑盒原始数据里有同名重复项，按名称去重。"""
    seen = set()
    out = []
    for it in items:
        nm = str(it.get("name") or "")
        if not nm or nm in seen:
            continue
        seen.add(nm)
        out.append(it)
    return out


def _grad_bg(w: int, h: int) -> Image.Image:
    """垂直渐变背景。"""
    img = Image.new("RGB", (w, h), BG_BOT)
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t)
        g = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t)
        b = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t)
        d.line([(0, y), (w, y)], fill=(r, g, b))
    return img


def _rrect(d: ImageDraw.ImageDraw, box, r: int, fill=None, outline=None, width: int = 1):
    """圆角矩形。"""
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def render_zhanbei(
    *,
    label: str,
    target: int,
    schemes: list[dict[str, Any]],
    source: str = "xb",
    scale: int = 2,
    max_items: int = 10,
    item_pics: dict[str, bytes] | None = None,
    logo: bytes | None = None,
) -> bytes:
    """渲染卡战备配装图，返回 PNG 字节。

    schemes 每项：
      { schemeName, totalCost, currentValue, items: [{name, cost, value, diff}] }
    """
    # 横向多列布局（和软件卡战备页一致）：每组方案一列
    COLS = max(1, min(len(schemes), 3))
    COL_W = 340          # 每列宽度
    COL_GAP = 12
    PAD = 20
    HEAD_H = 88
    ITEM_H = 46
    CARD_HEAD = 74

    W = PAD * 2 + COL_W * COLS + COL_GAP * (COLS - 1)

    # 每列物品数（取各列最大值决定高度）
    col_items = []
    for sc in schemes:
        items = dedup_items(sc.get("items") or [])[:max_items]
        col_items.append(items)
    max_rows = max((len(x) for x in col_items), default=1)
    body_h = CARD_HEAD + max_rows * ITEM_H + 14
    total_h = HEAD_H + body_h + PAD + 34

    img = _grad_bg(W * scale, total_h * scale)
    d = ImageDraw.Draw(img)

    def S(v: float) -> int:
        return int(v * scale)

    # ── 头部（含软件 logo）──
    title_x = PAD
    if logo:
        try:
            lg = Image.open(io.BytesIO(logo)).convert("RGBA")
            lsz = 46
            lg = lg.resize((S(lsz), S(lsz)), Image.LANCZOS)
            img.paste(lg, (S(PAD), S(16)), lg)
            title_x = PAD + lsz + 12
        except Exception:  # noqa: BLE001
            title_x = PAD

    d.text((S(title_x), S(18)), "卡战备", font=_font(S(30)), fill=GOLD2)
    sub = f"目标 {label} · 实时配装方案"
    d.text((S(title_x), S(56)), sub, font=_font(S(13), False), fill=SUB)

    # 右上角目标价值
    tv = _fmt_amount(target)
    tw = d.textlength(tv, font=_font(S(26)))
    d.text((S(W - PAD) - tw, S(20)), tv, font=_font(S(26)), fill=GOLD)
    lw = d.textlength("目标价值", font=_font(S(11), False))
    d.text((S(W - PAD) - lw, S(52)), "目标价值", font=_font(S(11), False), fill=DIM)

    # 分隔线
    d.line([(S(PAD), S(HEAD_H - 10)), (S(W - PAD), S(HEAD_H - 10))], fill=(38, 44, 56), width=S(1))

    # ── 方案卡片（横向多列）──
    y0 = HEAD_H
    ch = body_h - 4
    for idx, sc in enumerate(schemes[:COLS]):
        items = col_items[idx]
        x0 = PAD + idx * (COL_W + COL_GAP)
        box = [S(x0), S(y0), S(x0 + COL_W), S(y0 + ch)]
        _rrect(d, box, S(12), fill=CARD_BG, outline=CARD_BORDER, width=S(1))

        # 卡片标题
        name = str(sc.get("schemeName") or f"方案{idx + 1}")
        f_title = _font(S(15))
        disp_name = name
        while d.textlength(disp_name, font=f_title) > S(COL_W - 32) and len(disp_name) > 4:
            disp_name = disp_name[:-2]
        if disp_name != name:
            disp_name = disp_name[:-1] + "…"
        d.text((S(x0 + 14), S(y0 + 12)), disp_name, font=f_title, fill=GOLD2)

        # 成本 / 价值 两行
        cost = int(sc.get("totalCost") or 0)
        cur = int(sc.get("currentValue") or 0)
        d.text((S(x0 + 14), S(y0 + 36)), "成本", font=_font(S(10), False), fill=DIM)
        cs = _fmt_amount(cost)
        cw = d.textlength(cs, font=_font(S(13)))
        d.text((S(x0 + 44), S(y0 + 34)), cs, font=_font(S(13)), fill=GOLD)

        d.text((S(x0 + 14), S(y0 + 54)), "价值", font=_font(S(10), False), fill=DIM)
        vs = _fmt_amount(cur)
        vw = d.textlength(vs, font=_font(S(13)))
        d.text((S(x0 + 44), S(y0 + 52)), vs, font=_font(S(13)), fill=GREEN)

        # 物品列表
        iy = y0 + CARD_HEAD
        f_nm = _font(S(12), False)
        ICON_SZ = 30
        pics = item_pics or {}
        for k, it in enumerate(items):
            nm = str(it.get("name") or "?")
            ic = int(it.get("cost") or 0)
            pic = str(it.get("pic") or "")

            # 物品图标（有图就贴，没有留空）
            icon_x = x0 + 14
            text_x = icon_x + ICON_SZ + 8
            if pic and pic in pics:
                try:
                    im2 = Image.open(io.BytesIO(pics[pic])).convert("RGBA")
                    im2 = im2.resize((S(ICON_SZ), S(ICON_SZ)), Image.LANCZOS)
                    img.paste(im2, (S(icon_x), S(iy + 8)), im2)
                except Exception:  # noqa: BLE001
                    pass

            # 物品名（超长截断，留出价格位置）
            maxw = COL_W - (text_x - x0) - 14 - 62
            disp = nm
            while d.textlength(disp, font=f_nm) > S(maxw) and len(disp) > 3:
                disp = disp[:-2]
            if disp != nm:
                disp = disp[:-1] + "…"
            d.text((S(text_x), S(iy + 15)), disp, font=f_nm, fill=TEXT)

            # 价格（右对齐）
            ps = _fmt_amount(ic)
            pw = d.textlength(ps, font=_font(S(12)))
            d.text((S(x0 + COL_W - 14) - pw, S(iy + 15)), ps, font=_font(S(12)), fill=GOLD2)

            # 分隔线
            if k < len(items) - 1:
                ly = iy + ITEM_H - 1
                d.line([(S(x0 + 14), S(ly)), (S(x0 + COL_W - 14), S(ly))], fill=(30, 35, 45), width=S(1))

            iy += ITEM_H

    # ── 底部 ──
    foot_y = total_h - 26
    # 左下角：按数据源标注
    if source == "overlay":
        src_text = "由 delta-force-overlay 服务端提供数据源"
    else:
        src_text = "由小黑盒直接提供数据源"
    d.text((S(PAD), S(foot_y)), src_text, font=_font(S(11), False), fill=DIM)
    # 右下角：插件来源
    right = "astrbot_plugin_df_loot"
    rw = d.textlength(right, font=_font(S(11), False))
    d.text((S(W - PAD) - rw, S(foot_y)), right, font=_font(S(11), False), fill=DIM)

    # 直接输出 2 倍超采样图（不缩回 1x，否则文字和图标会糊）
    # 1084x506 逻辑尺寸 → 2168x1012 实际输出，QQ 显示时按屏幕缩放依然清晰
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
