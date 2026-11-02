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


def _find_core_box(src: Any, grid: int = 24) -> tuple[float, float]:
    """找出地图「核心区」的相对位置（0~1），供外部调试查看。

    返回核心窗口的中心点归一化坐标；窗口宽度按图宽 1/4 估。
    """
    try:
        sw, sh = src.size
        if sw <= 0 or sh <= 0:
            return (0.5, 0.5)
        # 用 1/4 尺寸的窗口滑一遍，取窗口中心
        win_w = max(1, sw // 4)
        win_h = max(1, sh // 4)
        ox = _best_window_offset(_density_profile(src, "x"), sw, win_w)
        oy = _best_window_offset(_density_profile(src, "y"), sh, win_h)
        return ((ox + win_w / 2) / sw, (oy + win_h / 2) / sh)
    except Exception:  # noqa: BLE001
        return (0.5, 0.5)


def _density_profile(src: Any, axis: str, bins: int = 64) -> list[float]:
    """把地图按轴切成一维密度曲线（边缘强度沿该轴累加）。

    axis='y' → 第 i 个值是第 i 条水平带的细节量（用于上下裁时选窗口）
    axis='x' → 同上，按垂直带（用于左右裁时选窗口）

    为什么要一维曲线：直接用「全图重心」会被整片地形拉平（实测各图重心
    都接近 0.50），等于没偏。改成窗口滑动找最密的一段才有意义。
    """
    try:
        from PIL import Image, ImageFilter

        SZ = 256
        small = src.convert("L").resize((SZ, SZ), Image.BILINEAR)
        edges = small.filter(ImageFilter.FIND_EDGES)
        px = edges.load()

        out: list[float] = []
        step = SZ / bins
        for i in range(bins):
            a0 = int(i * step)
            a1 = max(a0 + 1, int((i + 1) * step))
            s = 0.0
            if axis == "y":
                for y in range(a0, min(a1, SZ)):
                    for x in range(0, SZ, 2):     # 横抽样，够用且快
                        s += px[x, y]
            else:
                for x in range(a0, min(a1, SZ)):
                    for y in range(0, SZ, 2):
                        s += px[x, y]
            out.append(s)
        return out
    except Exception:  # noqa: BLE001
        return []


def _best_window_offset(profile: list[float], total_size: int, window: int) -> int:
    """在一维密度曲线上滑动窗口，返回细节总量最大的起始偏移。

    找不到合适窗口（图不需要裁 / 无数据）时返回居中偏移。
    """
    need = total_size - window
    if need <= 0 or not profile:
        return max(0, need // 2)

    n = len(profile)
    bin_px = total_size / n
    win_bins = max(1, int(round(window / bin_px)))

    pref = [0.0]
    for v in profile:
        pref.append(pref[-1] + v)

    best_start = 0
    best_sum = -1.0
    last = max(0, n - win_bins)
    for b in range(0, last + 1):
        s = pref[min(n, b + win_bins)] - pref[b]
        if s > best_sum:
            best_sum = s
            best_start = b

    off = int(round(best_start * bin_px))
    return max(0, min(need, off))


def _draw_map_bg(
    bg: Any, map_img: Any, w: int, h: int, scale_fn: Any
) -> None:
    """把地图缩略图铺到卡片头部当背景。

    处理要点
    ────────
    1. 等比裁切铺满（cover），不拉伸变形；
    2. 裁切窗口偏向「核心区」（建筑/路网密集处），而不是死板取正中；
    3. 整体压暗，保证白字可读；
    4. 右侧渐隐到卡片底色，让「带出 585.9万」区域干净；
    5. 底部轻微渐隐，和下方的物品区自然衔接。
    """
    from PIL import Image

    if map_img is None or w <= 0 or h <= 0:
        return

    src = map_img.convert("RGB")
    sw, sh = src.size
    if sw <= 0 or sh <= 0:
        return

    # cover 裁切：保持比例填满目标区域
    target_ratio = w / h
    src_ratio = sw / sh
    if src_ratio > target_ratio:
        # 源更宽 → 按高度缩放，左右裁
        new_h = h
        new_w = int(round(sw * h / sh)) if sh else w
        resized = src.resize((new_w, new_h), Image.LANCZOS)
        # 滑动窗口找细节最密集的一段（而不是取正中）
        prof = _density_profile(src, "x")
        left = _best_window_offset(prof, new_w, w)
        crop = resized.crop((left, 0, left + w, h))
    else:
        # 源更高 → 按宽度缩放，上下裁
        new_w = w
        new_h = int(round(sh * w / sw)) if sw else h
        resized = src.resize((new_w, new_h), Image.LANCZOS)
        prof = _density_profile(src, "y")
        top = _best_window_offset(prof, new_h, h)
        crop = resized.crop((0, top, w, top + h))

    # 压暗（乘 0.62 —— 太暗就看不出是哪张图了，0.62 仍能保证白字可读）
    dark = crop.point(lambda v: int(v * 0.62))
    bg.paste(dark, (0, 0))

    # 右侧 + 底部渐隐到卡片底色，让文字区域干净
    from PIL import ImageDraw as _ID

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = _ID.Draw(overlay, "RGBA")
    # 右侧 74% 才开始渐隐：尽量多露地图，只保住「带出 xxx万」那块
    fade_start = int(w * 0.74)
    for x in range(fade_start, w):
        t = (x - fade_start) / max(1, w - fade_start)
        a = int(230 * (t ** 1.3))
        od.line([(x, 0), (x, h)], fill=BG_BOT + (a,))
    # 底部 26% 渐隐（和物品区自然衔接）
    fade_h = int(h * 0.26)
    for y in range(h - fade_h, h):
        t = (y - (h - fade_h)) / max(1, fade_h)
        a = int(185 * (t ** 1.6))
        od.line([(0, y), (w, y)], fill=BG_BOT + (a,))

    bg.paste(Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB"), (0, 0))


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
    map_bg: "Image.Image | None" = None,
    item_layout: str = "grid",
) -> bytes:
    """渲染出货卡片，返回 PNG 字节。

    scale=2 表示 2 倍超采样，输出更清晰。
    show_avatar 默认关闭（不显示账号头像）。
    map_bg 为对应对局的官方地图缩略图（PIL Image），有则铺在头部当背景。
    item_layout:
        'grid' = 网格（一行 3 个，紧凑，物品多时友好）
        'list' = 列表（每行一个，图标更大、名称完整）
    """
    item_pics = item_pics or {}
    layout = "list" if str(item_layout).lower() == "list" else "grid"

    # ── 布局参数（逻辑像素）──
    PAD = 18
    HEAD_H = 96          # 头部高度（不含左侧圆形区）

    reds = battle.reds
    keys = battle.keycards
    show_reds = sorted(reds, key=lambda x: -x.total) if show_items else []
    show_keys = sorted(keys, key=lambda x: -x.total) if show_items else []

    if layout == "list":
        # 列表：每行一个物品，卡片窄一些、图标大一些
        W = 560
        COLS = 1
        CELL_GAP = 8
        CELL_H = 66
        ICON = 52
    else:
        # 网格：一行 3 个（和官方战绩页一致）
        W = 640
        COLS = 3
        CELL_GAP = 9
        CELL_H = 88
        ICON = 44

    # 分区块高度：红一组 + 房卡一组
    def _group_h(n: int) -> int:
        if n <= 0:
            return 0
        rows = (n + COLS - 1) // COLS
        return 22 + rows * (CELL_H + CELL_GAP)   # 22 = 小标题行

    body_h = _group_h(len(show_reds)) + _group_h(len(show_keys))
    H = HEAD_H + 10 + body_h + 26

    S = scale
    Wp, Hp = W * S, H * S

    def S_(v: float) -> int:
        return int(round(v * S))

    # ── 背景 ──
    bg = Image.new("RGB", (Wp, Hp), BG_BOT)
    d = ImageDraw.Draw(bg, "RGBA")
    # 竖向渐变（打底）
    for y in range(Hp):
        t = y / max(1, Hp - 1)
        c = (
            int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t),
            int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t),
            int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t),
        )
        d.line([(0, y), (Wp, y)], fill=c)

    # ── 地图底图（头部区域）──
    # 用对应对局的官方地图缩略图铺在头部，压暗 + 右侧淡出，
    # 让文字可读的同时一眼看出是哪张图。
    head_h_px = S_(HEAD_H)
    if map_bg is not None:
        try:
            _draw_map_bg(bg, map_bg, Wp, head_h_px, S_)
        except Exception:  # noqa: BLE001
            pass  # 底图失败不影响卡片

    # ── 边框 ──
    d.rounded_rectangle(
        [1, 1, Wp - 2, Hp - 2], radius=S_(16), outline=GOLD + (140,), width=S_(2)
    )

    # ── 头部金色渐变条（叠在地图上；有地图时减淡，否则会盖掉地图）──
    head_h = head_h_px
    gold_boost = 1.0 if map_bg is None else 0.42
    for x in range(Wp):
        t = x / max(1, Wp - 1)
        a = int((46 * (1 - t) + 10 * t) * gold_boost)
        d.line([(x, 0), (x, head_h)], fill=GOLD + (a,))
    d.line([(0, head_h), (Wp, head_h)], fill=GOLD + (55,), width=S_(1))

    # ── 玩家名 / 状态（无左侧圆形区，文字直接靠左）──
    tx = S_(PAD + 4)
    f_name = _font(S_(24))
    f_meta = _font(S_(16))

    # 有地图底图时给文字加阴影：地图亮部会吃掉白色文字
    shadow_off = S_(2) if map_bg is not None else 0

    def _txt(xy, s, font, fill):
        if shadow_off:
            d.text((xy[0] + shadow_off, xy[1] + shadow_off), s, font=font, fill=(0, 0, 0, 210))
        d.text(xy, s, font=font, fill=fill)

    _txt((tx, S_(PAD + 6)), battle.player or "未知", f_name, TEXT)

    meta_y = S_(PAD + 42)
    meta_x = tx
    if battle.escape:
        _txt((meta_x, meta_y), "撤离成功", f_meta, GREEN)
        meta_x += d.textlength("撤离成功", font=f_meta) + S_(12)
    else:
        _txt((meta_x, meta_y), "撤离失败", f_meta, RED)
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

    # ── 物品网格（图二风格：多列小图标 + 汇总标题）──
    # 分两组展示：红一组、房卡一组，各自带「N 种 · M 件 · X万」汇总行
    grid_y = S_(HEAD_H + 8)
    left_x = S_(PAD)
    avail_w = Wp - S_(PAD) * 2
    cell_w = (avail_w - S_(CELL_GAP) * (COLS - 1)) // COLS if COLS > 1 else avail_w

    def _draw_group(items: list, is_red_group: bool) -> int:
        """画一组物品，返回占用后新的 y 坐标（设备像素）。"""
        nonlocal grid_y
        if not items:
            return grid_y
        accent = RED if is_red_group else BLUE
        label = "红" if is_red_group else "房卡"
        f_hd = _font(S_(12))
        f_hd_b = _font(S_(13))

        # 汇总行：「红  10 种 · 15 件 · 942.4万」
        kinds = len(items)
        pieces = sum(max(1, it.num) for it in items)
        total_v = sum(it.total for it in items)

        tag = label
        tw = d.textlength(tag, font=f_hd_b) + S_(16)
        _rounded(d, [left_x, grid_y, left_x + tw, grid_y + S_(19)], S_(5), accent + (52,))
        d.text((left_x + S_(8), grid_y + S_(2)), tag, font=f_hd_b, fill=accent)

        # 汇总文字
        summ = f"{kinds} 种 · {pieces} 件"
        if total_v > 0:
            summ += f" · {_fmt_price(total_v)}"
        d.text((left_x + tw + S_(10), grid_y + S_(3)), summ, font=f_hd, fill=SUB)

        grid_y += S_(22)

        # ── 列表模式：每行一个，图标左、文字右 ──
        if layout == "list":
            for it in items:
                row_top = grid_y
                _rounded(
                    d,
                    [left_x, row_top, left_x + avail_w, row_top + S_(CELL_H)],
                    S_(9), accent + (22,),
                )
                d.rounded_rectangle(
                    [left_x, row_top, left_x + avail_w, row_top + S_(CELL_H)],
                    radius=S_(9), outline=accent + (58,), width=S_(1),
                )

                # 图标（左）
                pic = _item_image(item_pics.get(it.id), S_(ICON))
                ix = left_x + S_(8)
                iy = row_top + (S_(CELL_H) - S_(ICON)) // 2
                if pic is not None:
                    px = ix + (S_(ICON) - pic.width) // 2
                    py = iy + (S_(ICON) - pic.height) // 2
                    bg.paste(pic, (px, py), pic)
                else:
                    _rounded(
                        d,
                        [ix, iy, ix + S_(ICON), iy + S_(ICON)],
                        S_(6), (255, 255, 255, 18),
                    )

                # 名称 + 品质标签（右上）
                nx = ix + S_(ICON) + S_(11)
                ny = row_top + S_(11)
                f_nm = _font(S_(16))
                nm = it.name or "未知"
                max_w = left_x + avail_w - nx - S_(86)
                while nm and d.textlength(nm, font=f_nm) > max_w:
                    nm = nm[:-1]
                if nm != (it.name or "未知") and nm:
                    nm = nm[:-1] + "…"
                d.text((nx, ny), nm, font=f_nm, fill=TEXT)
                nw = d.textlength(nm, font=f_nm)

                # 品质标签跟在名称后
                if it.is_red:
                    _rounded(d, [nx + nw + S_(7), ny + S_(1), nx + nw + S_(38), ny + S_(22)], S_(4), RED + (58,))
                    d.text((nx + nw + S_(11), ny + S_(4)), "红", font=_font(S_(12)), fill=RED)
                elif it.is_keycard:
                    _rounded(d, [nx + nw + S_(7), ny + S_(1), nx + nw + S_(52), ny + S_(22)], S_(4), BLUE + (52,))
                    d.text((nx + nw + S_(11), ny + S_(4)), "房卡", font=_font(S_(12)), fill=BLUE)

                # 数量 + 总价（第二行）
                f_sub = _font(S_(13))
                parts = []
                if it.num > 1:
                    parts.append(f"×{it.num}")
                if it.total > 0:
                    parts.append(f"{_fmt_price(it.total)} 哈夫币")
                if parts:
                    d.text((nx, row_top + S_(37)), "  ".join(parts), font=f_sub, fill=SUB)

                grid_y += S_(CELL_H) + S_(CELL_GAP)

            return grid_y

        # ── 网格模式：一行 COLS 个 ──
        for idx, it in enumerate(items):
            col = idx % COLS
            row = idx // COLS
            cx = left_x + col * (cell_w + S_(CELL_GAP))
            cy = grid_y + row * (S_(CELL_H) + S_(CELL_GAP))

            # 单元底
            _rounded(
                d,
                [cx, cy, cx + cell_w, cy + S_(CELL_H)],
                S_(8), accent + (22,),
            )

            # 图标（居中，垂直位置留出上下文字空间）
            pic = _item_image(item_pics.get(it.id), S_(ICON))
            icon_cx = cx + (cell_w - S_(ICON)) // 2
            icon_cy = cy + S_(7)
            if pic is not None:
                # 居中贴图
                px = icon_cx + (S_(ICON) - pic.width) // 2
                py = icon_cy + (S_(ICON) - pic.height) // 2
                bg.paste(pic, (px, py), pic)
            else:
                _rounded(
                    d,
                    [icon_cx, icon_cy, icon_cx + S_(ICON), icon_cy + S_(ICON)],
                    S_(5), (255, 255, 255, 16),
                )

            # 数量角标（右上，>1 才显示）
            if it.num > 1:
                f_q = _font(S_(10))
                q = f"×{it.num}"
                qw = d.textlength(q, font=f_q) + S_(8)
                qx = cx + cell_w - qw - S_(4)
                qy = cy + S_(5)
                _rounded(d, [qx, qy, qx + qw, qy + S_(15)], S_(3), (0, 0, 0, 170))
                d.text((qx + S_(4), qy + S_(1)), q, font=f_q, fill=GOLD2)

            # 名称（可能截断）—— 图标下方
            f_nm = _font(S_(11))
            nm = it.name or "未知"
            max_w = cell_w - S_(10)
            while nm and d.textlength(nm, font=f_nm) > max_w:
                nm = nm[:-1]
            if nm != (it.name or "未知") and nm:
                nm = nm[:-1] + "…"
            nw = d.textlength(nm, font=f_nm)
            d.text(
                (cx + (cell_w - nw) / 2, cy + S_(57)),
                nm, font=f_nm, fill=TEXT,
            )

            # 价格 —— 名称下方
            f_pr = _font(S_(10))
            pr = _fmt_price(it.total) if it.total > 0 else ""
            if pr:
                pw = d.textlength(pr, font=f_pr)
                d.text(
                    (cx + (cell_w - pw) / 2, cy + S_(72)),
                    pr, font=f_pr, fill=accent,
                )

        grid_y += ((len(items) + COLS - 1) // COLS) * (S_(CELL_H) + S_(CELL_GAP))
        return grid_y

    grid_y = _draw_group(show_reds, True)
    if show_reds and show_keys:
        grid_y += S_(2)
    grid_y = _draw_group(show_keys, False)


    # ── 页脚 ──
    f_foot = _font(S_(12))
    d.text((S_(PAD), Hp - S_(24)), battle.time or "", font=f_foot, fill=SUB_DIM)
    foot_r = "astrbot_plugin_df_loot"
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

