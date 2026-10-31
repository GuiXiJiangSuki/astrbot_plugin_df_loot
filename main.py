"""AstrBot 三角洲出货播报插件。

监控 delta-force-overlay 的对局数据，出红/房卡时推送到 QQ 群。
"""

from __future__ import annotations

import asyncio
import base64
import inspect
from typing import Any

try:
    import aiohttp
except ImportError:  # 理论上 AstrBot 环境自带；缺失时相关功能降级
    aiohttp = None  # type: ignore[assignment]

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.api.message_components import Image, Plain

try:
    # 作为包的一部分被加载时（AstrBot 插件目录）
    from .df_loot_core import (
        LootBattle,
        LootTracker,
        OverlayClient,
        parse_battle,
    )
    from .df_loot_card import (
        build_brief_text,
        build_daily_text,
        build_text_message,
        render_card,
        render_daily_card,
    )
    from .df_loot_daily import fetch_daily_summary
    from . import df_loot_xb
    from .df_loot_zb_card import render_zhanbei
    from .df_loot_rank_card import render_rank_daily
    from .df_loot_report import parse_report
    from .df_loot_sample import random_battle
except ImportError:
    # 直接以脚本方式运行 / 测试
    from df_loot_core import (
        LootBattle,
        LootTracker,
        OverlayClient,
        parse_battle,
    )
    from df_loot_card import (
        build_brief_text,
        build_daily_text,
        build_text_message,
        render_card,
        render_daily_card,
    )
    from df_loot_daily import fetch_daily_summary
    import df_loot_xb
    from df_loot_zb_card import render_zhanbei
    from df_loot_rank_card import render_rank_daily
    from df_loot_report import parse_report
    from df_loot_sample import random_battle

# 统一使用 astrbot.api 的 logger（自带 plugin_tag 等字段）


class DfLootPlugin(Star):
    """三角洲出货播报。"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.astrbot_config = config
        self._task: asyncio.Task | None = None
        self._running = False
        self._tracker = LootTracker()
        self._client: OverlayClient | None = None
        self._last_state: dict[str, Any] | None = None
        self._poll_count = 0
        self._broadcast_count = 0
        self._last_error = ""
        self._daily_job_id: str | None = None
        self._report_server: asyncio.Task | None = None

    # ─────────────── 配置读取 ───────────────

    def _cfg(self, key: str, default: Any = None) -> Any:
        try:
            val = self.astrbot_config.get(key, default)
            return default if val is None else val
        except Exception:  # noqa: BLE001
            return default

    @property
    def daily_enabled(self) -> bool:
        """是否启用每日汇总。"""
        return bool(self._cfg("df_daily_enabled", True))

    @property
    def report_enabled(self) -> bool:
        """是否开放外部上报接口。"""
        return bool(self._cfg("df_report_enabled", False))

    @property
    def report_token(self) -> str:
        """上报接口的鉴权 Token。"""
        return str(self._cfg("df_report_token", "") or "").strip()

    @property
    def report_port(self) -> int:
        """上报服务的监听端口。"""
        try:
            v = int(self._cfg("df_report_port", 8790))
        except (TypeError, ValueError):
            v = 8790
        return max(1024, min(65535, v))

    @property
    def daily_time(self) -> tuple[int, int]:
        """每日汇总时间，返回 (时, 分)。默认 23:50。"""
        raw = str(self._cfg("df_daily_time", "23:50")).strip()
        try:
            if ":" in raw:
                hh_s, mm_s = raw.split(":", 1)
                hh, mm = int(hh_s), int(mm_s)
            else:
                hh, mm = 23, 50
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return hh, mm
        except (TypeError, ValueError):
            pass
        return 23, 50

    @property
    def daily_time_str(self) -> str:
        hh, mm = self.daily_time
        return f"{hh:02d}:{mm:02d}"


    @property
    def overlay_url(self) -> str:
        return str(self._cfg("df_overlay_url", "http://192.168.1.5:8788")).strip()

    @property
    def groups(self) -> list[str]:
        """播报目标群。

        - aiocqhttp：填 QQ 群号（纯数字）
        - qq_official：填群的 group_openid（一串字符）
        统一按字符串存，兼容两种。
        """
        raw = self._cfg("df_broadcast_groups", []) or []
        out: list[str] = []
        for item in raw:
            s = str(item).strip()
            if s and s not in out:
                out.append(s)
        return out

    @property
    def debug_groups(self) -> list[str]:
        raw = self._cfg("df_debug_groups", []) or []
        out: list[str] = []
        for item in raw:
            s = str(item).strip()
            if s and s not in out:
                out.append(s)
        return out

    @property
    def report_test_groups(self) -> list[str]:
        """软件「测试上报」专用的推送群。

        与 df_broadcast_groups（正式播报群）分开：
          - 正式出货 → df_broadcast_groups
          - 软件里点「测试上报」→ df_report_test_groups
        这样测试消息不会打扰正式播报群。
        用 /df 上报测试 在当前群添加。
        """
        raw = self._cfg("df_report_test_groups", []) or []
        out: list[str] = []
        for item in raw:
            s = str(item).strip()
            if s and s not in out:
                out.append(s)
        return out

    @property
    def poll_interval(self) -> int:
        try:
            v = int(self._cfg("df_poll_interval", 30))
        except (TypeError, ValueError):
            v = 30
        return max(10, min(600, v))

    # ─────────────── 生命周期 ───────────────

    async def initialize(self) -> None:
        self._client = OverlayClient(self.overlay_url)
        self._running = True
        self._task = asyncio.create_task(self._poll_loop(), name="df_loot_poll")

        # 每日汇总定时任务
        await self._register_daily_job()

        # 接收外部 overlay 上报
        self._register_report_api()

        logger.info(
            "[df_loot] 已启动 · overlay=%s · 间隔=%ss · 群=%s · 日报=%s · 上报=%s",
            self.overlay_url,
            self.poll_interval,
            self.groups or "(未配置)",
            self.daily_time_str if self.daily_enabled else "关闭",
            "已开启" if self.report_enabled else "关闭",
        )

    def _register_report_api(self) -> None:
        """启动独立的上报 HTTP 服务。

        为什么不用 context.register_web_api：
        ────────────────────────────────────
        AstrBot 的插件 web api 挂在 `/api/plug/<path>`，而且**要求面板登录态**
        （走 dashboard 的鉴权中间件）。别人的 overlay 不可能带着你的面板 token，
        所以必须起一个独立的、只需 Token 鉴权的小服务。
        """
        if not self.report_enabled:
            logger.info("[df_loot] 上报接口已关闭")
            return

        # ⚠️ 安全强制：Token 为空时拒绝启动。
        # 否则 handle_report 里的 `if expect and got != expect` 会整段跳过鉴权，
        # 任何能访问该端口的人都能往群里推送内容。
        if not self.report_token:
            logger.error(
                "[df_loot] 上报接口已开启但 df_report_token 为空，已拒绝启动。"
                "请在插件配置里设置一个足够长的随机 Token（建议 32 位以上），"
                "并在 overlay 设置页填入同一个值。"
            )
            return

        port = self.report_port
        try:
            self._report_server = asyncio.create_task(
                self._run_report_server(port), name="df_loot_report_srv"
            )
            logger.info("[df_loot] 上报服务启动中：0.0.0.0:%s/df/report", port)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[df_loot] 启动上报服务失败: %s", exc)

    async def _run_report_server(self, port: int) -> None:
        """极简 HTTP 服务，只处理 POST /df/report 和 GET /df/ping。"""
        from aiohttp import web

        async def handle_ping(request: web.Request) -> web.Response:
            return web.json_response({"ok": True, "service": "df_loot_report"})

        async def handle_report(request: web.Request) -> web.Response:
            # Token 鉴权（Header 或 Body 都认）
            expect = self.report_token
            got = request.headers.get("X-DF-Token", "") or ""
            body: dict = {}
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001
                body = {}
            if not isinstance(body, dict):
                body = {}
            if not got:
                got = str(body.get("token") or "")

            # 双重保险：配置异常为空时一律拒绝，绝不裸奔
            if not expect:
                logger.warning("[df_loot] 上报被拒：服务端未配置 Token")
                return web.json_response(
                    {"ok": False, "error": "server token not configured"},
                    status=503,
                )
            if got != expect:
                logger.warning("[df_loot] 上报被拒：Token 不匹配")
                return web.json_response({"ok": False, "error": "invalid token"}, status=401)

            battle = parse_report(body)
            if battle is None:
                return web.json_response({"ok": False, "error": "invalid payload"}, status=400)

            if not self._tracker.is_new(battle.room_id):
                logger.info("[df_loot] 上报：对局 %s 已播报过，跳过", battle.room_id)
                return web.json_response(
                    {"ok": True, "accepted": True, "broadcast": False}
                )

            self._tracker.mark(battle.room_id)
            # 测试判定（任一命中即视为测试，只发测试群，绝不打扰正式播报群）：
            #   1. 软件里点「测试上报」→ 带 isTest=true
            #   2. roomId 以 test_ / test- 开头（软件测试上报的固定格式）
            #   3. 显式传 isTest 字段（任意真值）
            is_test = (
                bool(body.get("isTest"))
                or str(battle.room_id).lower().startswith("test_")
                or str(battle.room_id).lower().startswith("test-")
            )
            should = True if is_test else self._should_broadcast(battle)
            logger.info(
                "[df_loot] 收到上报%s %s · %s · 红%d 卡%d · 带出%s · 播报=%s",
                "（测试）" if is_test else "",
                battle.room_id, battle.player,
                len(battle.reds), len(battle.keycards),
                battle.gained, should,
            )
            if should:
                asyncio.create_task(
                    self._broadcast(battle, test_only=is_test),
                    name="df_loot_report_bc",
                )
            return web.json_response(
                {"ok": True, "accepted": True, "broadcast": should}
            )

        app = web.Application()
        app.router.add_get("/df/ping", handle_ping)
        app.router.add_post("/df/report", handle_report)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        try:
            await site.start()
            logger.info(
                "[df_loot] 上报服务已就绪：http://0.0.0.0:%s/df/report", port
            )
            # 常驻
            while self._running:
                await asyncio.sleep(3600)
        except OSError as exc:
            logger.warning(
                "[df_loot] 上报端口 %s 绑定失败（可能被占用）: %s", port, exc
            )
        finally:
            try:
                await runner.cleanup()
            except Exception:  # noqa: BLE001
                pass


    async def _register_daily_job(self) -> None:
        """注册每天 N 点的日报任务。"""
        if not self.daily_enabled:
            logger.info("[df_loot] 日报功能已关闭")
            return

        cron_mgr = getattr(self.context, "cron_manager", None)
        if cron_mgr is None:
            logger.warning("[df_loot] 无 cron_manager，日报功能不可用")
            return

        hh, mm = self.daily_time
        expr = f"{mm} {hh} * * *"
        job_name = "df_loot_daily"

        # 先删旧的同名任务，避免重复注册
        # 注意：CronJobManager 的 list_jobs / delete_job / add_basic_job 都是协程，必须 await
        try:
            jobs = cron_mgr.list_jobs()
            if inspect.isawaitable(jobs):
                jobs = await jobs
            for job in jobs or []:
                if getattr(job, "name", "") == job_name:
                    res = cron_mgr.delete_job(job.job_id)
                    if inspect.isawaitable(res):
                        await res
                    logger.info("[df_loot] 已移除旧日报任务 %s", job.job_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[df_loot] 清理旧日报任务失败: %s", exc)

        try:
            job = cron_mgr.add_basic_job(
                name=job_name,
                cron_expression=expr,
                handler=self._daily_report,
                description="三角洲每日出货汇总",
                timezone="Asia/Shanghai",
            )
            if inspect.isawaitable(job):
                job = await job
            self._daily_job_id = getattr(job, "job_id", None)
            logger.info(
                "[df_loot] 日报任务已注册：每天 %02d:%02d（%s） job_id=%s",
                hh, mm, expr, self._daily_job_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[df_loot] 注册日报任务失败: %s", exc)

    async def _fetch_rank_top(self, limit: int = 5) -> list:
        """从排行服务拉今日 TOP N。"""
        if aiohttp is None:
            return []
        url = f"{self.rank_base}/api/rank/range"
        params = {"range": "today", "limit": str(limit)}
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(url, params=params) as resp:
                    if resp.status != 200:
                        logger.warning("[df_loot] 排行接口返回 %s", resp.status)
                        return []
                    j = await resp.json(content_type=None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[df_loot] 拉取排行失败: %s", exc)
            return []
        if not isinstance(j, dict) or not j.get("ok"):
            return []
        return j.get("list") or []

    async def _fetch_rank_pics(self, players: list) -> dict:
        """下载排行里红/卡物品图标。"""
        if aiohttp is None:
            return {}
        urls = set()
        for p in players:
            for it in (p.get("redItems") or []) + (p.get("keyItems") or []):
                pic = str(it.get("pic") or "")
                if pic.startswith("http"):
                    urls.add(pic)
        if not urls:
            return {}
        out = {}
        try:
            timeout = aiohttp.ClientTimeout(total=25)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async def one(u):
                    try:
                        async with sess.get(u) as r:
                            if r.status == 200:
                                out[u] = await r.read()
                    except Exception:  # noqa: BLE001
                        pass
                await asyncio.gather(*[one(u) for u in urls])
        except Exception:  # noqa: BLE001
            pass
        return out

    async def _fetch_rank_avatars(self, players: list) -> dict:
        """下载玩家头像（圆形显示用）。"""
        if aiohttp is None:
            return {}
        pairs = {}
        for p in players:
            pid = str(p.get("playerId") or "")
            av = str(p.get("avatar") or "")
            if pid and av.startswith("http"):
                pairs[pid] = av
        if not pairs:
            return {}
        out = {}
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async def one(pid, u):
                    try:
                        async with sess.get(u) as r:
                            if r.status == 200:
                                out[pid] = await r.read()
                    except Exception:  # noqa: BLE001
                        pass
                await asyncio.gather(*[one(k, v) for k, v in pairs.items()])
        except Exception:  # noqa: BLE001
            pass
        return out

    def _build_rank_daily_text(self, players: list, date: str) -> str:
        """排行榜汇总的文字版。"""
        if not players:
            return f"【今日出货排行 · {date}】\n\n今天还没有玩家上榜。"
        lines = [f"【今日出货排行 · {date}】", ""]
        for i, p in enumerate(players[:5]):
            rank = p.get("rank") or (i + 1)
            name = p.get("name") or "未知"
            gained = self._fmt_money(p.get("gained") or 0)
            profit = self._fmt_money(p.get("profit") or 0)
            battles = p.get("battles") or 0
            escapes = p.get("escapes") or 0
            red_n = p.get("redCount") or 0
            key_n = p.get("keyCount") or 0
            esc_pct = round(escapes / battles * 100) if battles else 0
            lines.append(
                f"#{rank} {name} — 带出 {gained} · 净收益 {profit}"
                f"（{battles} 局 · 撤离率 {esc_pct}%）"
            )
            reds = p.get("redItems") or []
            if reds:
                names = "、".join(
                    f"{it.get('name')}×{it.get('num')}" if int(it.get("num") or 1) > 1 else str(it.get("name"))
                    for it in reds[:4]
                )
                lines.append(f"   红：{names}")
            keys = p.get("keyItems") or []
            if keys:
                names = "、".join(
                    f"{it.get('name')}×{it.get('num')}" if int(it.get("num") or 1) > 1 else str(it.get("name"))
                    for it in keys[:4]
                )
                lines.append(f"   卡：{names}")
            lines.append("")
        lines.append("————————————")
        lines.append("数据来自各玩家软件自动上传")
        return "\n".join(lines)

    async def _daily_report(
        self,
        *,
        test_only: bool = False,
        target_groups: list | None = None,
    ) -> None:
        """每日排行榜出货汇总。

        群选择优先级：
          1. target_groups  —— 指定群（/df 今天排行 用：在哪个群发就回哪个群）
          2. test_only      —— 只发测试群
          3. 默认            —— 发所有播报群（cron 定时任务用）
        """
        if target_groups:
            groups = list(target_groups)
        elif test_only:
            groups = self.report_test_groups
        else:
            groups = self.groups

        if not groups:
            tag = "指定群" if target_groups else ("测试群" if test_only else "播报群")
            logger.info("[df_loot] 排行日报：没有配置%s，跳过", tag)
            return

        players = await self._fetch_rank_top(5)
        if not players:
            logger.info("[df_loot] 排行日报：今日无人上榜，跳过")
            return

        from datetime import datetime
        date = datetime.now().strftime("%Y-%m-%d")

        # 下载物品图标 + 玩家头像
        pics = await self._fetch_rank_pics(players)
        avatars = await self._fetch_rank_avatars(players)
        if pics:
            logger.info("[df_loot] 排行日报：物品图 %s 张", len(pics))
        if avatars:
            logger.info("[df_loot] 排行日报：头像 %s 个", len(avatars))

        text = self._build_rank_daily_text(players, date)

        image_comp = None
        try:
            png = render_rank_daily(
                date=date,
                players=players,
                item_pics=pics,
                avatars=avatars,
                logo=self._load_logo(),
                max_players=5,
                per_row=3,
            )
            image_comp = Image.fromBase64(base64.b64encode(png).decode("ascii"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[df_loot] 排行日报卡片渲染失败: %s", exc)

        sent = 0
        for gid in groups:
            try:
                ok = await self._send_to_group(gid, [Plain(text)])
                if ok and image_comp is not None:
                    await asyncio.sleep(1.2)
                    await self._send_to_group(gid, [image_comp])
                if ok:
                    sent += 1
                if len(groups) > 1:
                    await asyncio.sleep(0.8)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[df_loot] 排行日报发送到群 %s 失败: %s", gid, exc)

        logger.info(
            "[df_loot] 排行日报已推送 → %s 个群（TOP %d）",
            sent, len(players),
        )

    async def terminate(self) -> None:
        self._running = False

        # 关闭上报服务
        if self._report_server:
            self._report_server.cancel()
            try:
                await self._report_server
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._report_server = None

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # 移除日报任务
        if self._daily_job_id:
            try:
                cron_mgr = getattr(self.context, "cron_manager", None)
                if cron_mgr:
                    res = cron_mgr.delete_job(self._daily_job_id)
                    if inspect.isawaitable(res):
                        await res
                    logger.info("[df_loot] 已移除日报任务")
            except Exception as exc:  # noqa: BLE001
                logger.debug("[df_loot] 移除日报任务失败: %s", exc)
            self._daily_job_id = None

        logger.info("[df_loot] 已停止")

    # ─────────────── 轮询主循环 ───────────────

    async def _poll_loop(self) -> None:
        # 启动回填：先把当前 last 标记为已见，避免重启后重复播报
        await self._prime_seen()

        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                logger.exception("[df_loot] 轮询异常: %s", exc)

            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                raise

    async def _prime_seen(self) -> None:
        """启动时把已有对局标记为已见，防止重复播报。"""
        if self._client is None:
            return
        state = await self._client.fetch_state()
        if not state:
            return
        battle = parse_battle(state)
        if battle and battle.room_id:
            self._tracker.mark(battle.room_id)
            logger.info("[df_loot] 启动回填：已标记对局 %s", battle.room_id)

    async def _poll_once(self) -> None:
        """轮询一次。"""
        if self._client is None:
            self._client = OverlayClient(self.overlay_url)

        self._poll_count += 1
        state = await self._client.fetch_state()
        if not state:
            self._last_error = "无法连接 overlay"
            return

        self._last_error = ""
        self._last_state = state

        # overlay 本身异常
        if not state.get("ok"):
            err = state.get("error") or "overlay 数据不可用"
            if state.get("needLogin"):
                self._last_error = "overlay 未登录，需要扫码"
            else:
                self._last_error = str(err)
            return

        battle = parse_battle(state)
        if not battle or not battle.room_id:
            return

        # 去重：这一局已经处理过
        if not self._tracker.is_new(battle.room_id):
            return

        # 标记为已见（无论是否播报，避免重复判定）
        self._tracker.mark(battle.room_id)

        # 判定是否值得播报
        if not self._should_broadcast(battle):
            return

        await self._broadcast(battle)

    def _should_broadcast(self, battle: LootBattle) -> bool:
        """判定这局是否要播报。"""
        # 总开关（关闭后不播报任何出货）
        if not self.broadcast_enabled:
            return False

        enable_red = bool(self._cfg("df_enable_red", True))
        enable_key = bool(self._cfg("df_enable_keycard", True))
        only_escape = bool(self._cfg("df_only_escape", False))

        try:
            min_value = int(self._cfg("df_min_value", 0)) * 10_000
        except (TypeError, ValueError):
            min_value = 0

        # 只播报撤离成功
        if only_escape and not battle.escape:
            return False

        # 价值门槛
        if min_value > 0 and battle.gained < min_value:
            return False

        # 出货判定
        has_red = bool(battle.reds) and enable_red
        has_key = bool(battle.keycards) and enable_key
        return has_red or has_key

    # ─────────────── 播报 ───────────────

    async def _broadcast(self, battle: LootBattle, test_only: bool = False) -> None:
        """推送到配置的群。

        test_only=True 时只发「测试群」（df_report_test_groups），
        用于软件里点「测试上报」，避免打扰正式播报群。
        """
        groups = self.report_test_groups if test_only else self.groups
        if not groups:
            if test_only:
                logger.info(
                    "[df_loot] 测试上报：还没配置测试群，"
                    "请在群里发 /df 上报测试 添加（本次不推送正式群）"
                )
            else:
                logger.info("[df_loot] 出货了但没配置播报群，跳过：%s", battle.room_id)
            return

        # 准备图片素材
        avatar_data = None
        item_pics: dict[str, bytes] = {}
        if self._client:
            show_avatar = bool(self._cfg("df_show_avatar", False))
            if show_avatar and battle.avatar:
                avatar_data = await self._client.fetch_avatar(battle.avatar)

            # 只下载红/房卡的图，控制请求数
            wanted = [x for x in battle.items if x.is_red or x.is_keycard]
            for it in wanted[:10]:
                data = await self._client.fetch_item_pic(it.id)
                if data:
                    item_pics[it.id] = data

        # 渲染（PIL 直接画，清晰且无白边）
        image_comp = None
        text_comp = None
        # 播报样式：
        #   card → 只发图片
        #   text → 只发文字
        #   all  → 文字 + 图片都发
        render_mode = str(self._cfg("df_render_mode", "card")).strip().lower()
        if render_mode not in ("card", "text", "all"):
            render_mode = "card"
        want_image = render_mode in ("card", "all")
        want_text = render_mode in ("text", "all")

        if want_image:
            try:
                png = render_card(
                    battle,
                    avatar_data=avatar_data,
                    item_pics=item_pics,
                    show_avatar=bool(self._cfg("df_show_avatar", False)),
                    show_items=bool(self._cfg("df_show_items", True)),
                )
                image_comp = Image.fromBase64(base64.b64encode(png).decode("ascii"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[df_loot] 卡片渲染失败，退回文字: %s", exc)

        # 文字摘要（card 模式下由 df_attach_text 控制；all 模式强制带文字）
        if want_image and render_mode != "all" and self._cfg("df_attach_text", True):
            brief = build_brief_text(battle)
            if brief:
                text_comp = Plain(brief)

        # 纯文字 / all 模式：发完整文字
        if want_text:
            text_comp = Plain(build_text_message(battle))

        # 渲染失败兜底
        if image_comp is None and text_comp is None:
            text_comp = Plain(build_text_message(battle))

        # 组装：文字和图片分成两条发（QQ 官方富媒体消息的 content 常被丢弃，
        # 分开发最可靠；OneBot 平台也兼容）
        if text_comp is not None and image_comp is not None:
            send_groups = [[text_comp], [image_comp]]
        elif image_comp is not None:
            send_groups = [[image_comp]]
        else:
            send_groups = [[text_comp]]

        # 逐个群发送
        # 文字和图片分两条发；每条之间留间隔，避免被 QQ 合并成一条
        at_all = bool(self._cfg("df_at_all", False)) and bool(battle.reds)
        sent = 0
        for gid in groups:
            try:
                ok = False
                for i, chain in enumerate(send_groups):
                    # 第二条起先等 1.2 秒，让平台识别为独立消息
                    if i > 0:
                        await asyncio.sleep(1.2)
                    # @全体只加在第一条
                    ok = await self._send_to_group(
                        gid, chain, at_all=(at_all and i == 0)
                    )
                    if not ok:
                        break
                if ok:
                    sent += 1
                else:
                    logger.warning("[df_loot] 群 %s 发送未成功（可能被平台限制）", gid)
                # 群与群之间也留间隔，避免触发频率限制
                if len(groups) > 1:
                    await asyncio.sleep(0.8)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[df_loot] 发送到群 %s 失败: %s", gid, exc)

        if sent:
            self._broadcast_count += 1
            logger.info(
                "[df_loot] 已播报 %s → %s 个群（红%d 卡%d 带出%s）",
                battle.room_id,
                sent,
                len(battle.reds),
                len(battle.keycards),
                battle.gained,
            )

    async def _send_official_direct(
        self, platform: Any, group_openid: str, parts: list
    ) -> bool:
        """直接把消息推给 QQ 官方群（绕过 AstrBot 的 scene 缓存）。

        背景
        ────
        AstrBot 的 send_by_session 在群会话里会做：
            session.session_id.rsplit("_", 1)[-1]
        但我们存进配置的 group_openid 是 `84CE..._184D...` 这种带下划线的，
        截断后 session_id 变了，`_session_scene` 里查不到 → 判定为非 group →
            [QQOfficial] No cached msg_id for session ... skip send_by_session
        消息被静默丢弃。

        这里直接用 botpy 客户端发，不依赖任何缓存状态。
        """
        client = getattr(platform, "client", None)
        if client is None:
            client_holder = getattr(platform, "_client", None)
            client = client_holder
        if client is None:
            logger.debug("[df_loot] 平台无 botpy client，跳过直连")
            return False

        api = getattr(client, "api", None)
        if api is None or not hasattr(api, "post_group_message"):
            logger.debug("[df_loot] client.api 不可用，跳过直连")
            return False

        import random as _random

        # 拆分文字和图片
        plain_text = ""
        image_b64: str | None = None
        for c in parts:
            cname = type(c).__name__
            if cname == "Plain":
                plain_text += getattr(c, "text", "") or ""
            elif cname == "Image" and image_b64 is None:
                # Image 组件的 base64 内容
                raw = getattr(c, "file", "") or ""
                if isinstance(raw, str) and raw.startswith("base64://"):
                    image_b64 = raw[len("base64://"):]
                elif isinstance(raw, str) and raw.startswith("data:"):
                    # data:image/png;base64,xxx
                    idx = raw.find("base64,")
                    if idx > 0:
                        image_b64 = raw[idx + 7:]
                elif isinstance(raw, str) and raw:
                    image_b64 = raw

        if not plain_text and not image_b64:
            return False

        try:
            # 有图先上传
            media = None
            if image_b64:
                from types import SimpleNamespace

                from astrbot.core.platform.sources.qqofficial.qqofficial_message_event import (
                    QQOfficialMessageEvent as QOE,
                )

                # upload_group_and_c2c_image 内部用 self.bot，包一个最小实现
                helper = SimpleNamespace(bot=client)
                media = await QOE.upload_group_and_c2c_image(
                    helper,  # type: ignore[arg-type]
                    image_b64,
                    QOE.IMAGE_FILE_TYPE,
                    group_openid=group_openid,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[df_loot] 直连：上传图片失败 %s", exc)
            media = None

        try:
            if media is not None:
                payload: dict[str, Any] = {
                    "content": plain_text or None,
                    "msg_type": 7,
                    "media": media,
                    "msg_seq": _random.randint(1, 10000),
                }
            else:
                payload = {
                    "content": plain_text,
                    "msg_type": 0,
                    "msg_seq": _random.randint(1, 10000),
                }

            ret = await api.post_group_message(
                group_openid=group_openid, **payload
            )
            # 打印返回详情，便于排查纯文字是否被平台丢弃
            try:
                ret_id = getattr(ret, "id", None) or (ret.get("id") if isinstance(ret, dict) else None)
                ret_ts = getattr(ret, "timestamp", None) or (ret.get("timestamp") if isinstance(ret, dict) else None)
                logger.info("[df_loot] 直连发送成功 group=%s msg_type=%s id=%s ts=%s",
                            str(group_openid)[:24], payload.get("msg_type"), ret_id, ret_ts)
            except Exception:  # noqa: BLE001
                logger.info("[df_loot] 直连发送成功 group=%s msg_type=%s ret=%s",
                            str(group_openid)[:24], payload.get("msg_type"), type(ret).__name__)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[df_loot] 直连发送失败(群 %s): %s",
                           str(group_openid)[:24], exc)
            return False

    async def _send_to_group(
        self, group_id: str, chain: list, *, at_all: bool = False
    ) -> bool:
        """发送到群。

        两种平台分别处理：
          - qq_official：直接调用适配器的 send_by_session。
            context.send_message() 明确不支持该平台。
          - aiocqhttp：走 context.send_message()。
        """
        from astrbot.api.message_components import At
        from astrbot.core.message.message_event_result import MessageChain

        parts: list = []
        if at_all:
            parts.append(At(qq="all"))
        parts.extend(chain)
        message_chain = MessageChain(chain=parts)

        # ── QQ 官方机器人 ──
        # 注意：平台 meta.name 在不同 AstrBot 版本/配置下可能是
        #   "qq_official"（适配器源码里写的）或 "qqbot"（实际会话前缀）
        # 两个都要认，否则会掉进 OneBot 分支报 cannot find platform
        OFFICIAL_NAMES = ("qq_official", "qqbot", "qq_official_webhook")
        for platform in self.context.platform_manager.platform_insts:
            meta = platform.meta()
            if meta.name not in OFFICIAL_NAMES:
                continue

            # 保险：万一配置里还留着 "群openid_用户openid" 这种复合键，这里剥掉
            gid = str(group_id)
            if "_" in gid:
                head = gid.split("_")[0]
                if len(head) >= 16:
                    logger.info(
                        "[df_loot] 群标识含下划线，已剥离为纯 openid: %s -> %s",
                        gid[:24], head[:24],
                    )
                    gid = head

            kinds = [type(c).__name__ for c in parts]
            logger.info(
                "[df_loot] 发送群(platform=%s) 组件=%s group=%s",
                meta.name, kinds, gid[:24],
            )

            # 先试直连 botpy（绕过 AstrBot 的 scene 缓存）
            ok = await self._send_official_direct(platform, gid, parts)
            if ok:
                return True

            # 直连失败，退回官方 send_by_session
            try:
                from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import (
                    MessageSesion,
                    MessageType,
                )
            except ImportError:
                logger.warning("[df_loot] 无法导入 qq_official 的会话类型")
                return False

            session = MessageSesion(
                meta.id,
                MessageType.GROUP_MESSAGE,
                gid,
            )
            try:
                await platform.send_by_session(session, message_chain)
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[df_loot] send_by_session 也失败(群 %s): %s", group_id, exc
                )
                return False

        # ── OneBot 等其它平台 ──
        origin = self._resolve_group_origin(group_id)
        if not origin:
            logger.warning("[df_loot] 群 %s 找不到可用会话", group_id)
            return False
        try:
            await self.context.send_message(origin, message_chain)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[df_loot] send_message 失败(群 %s): %s", group_id, exc)
            return False

    def _resolve_group_origin(self, group_id: int | str) -> str | None:
        """构造主动发送的会话 ID。

        支持两种平台：
          - aiocqhttp（OneBot）：{meta.id}:GroupMessage:{群号}
          - qq_official：{meta.id}:GroupMessage:{group_openid}
        """
        gid = str(group_id)
        try:
            for platform in self.context.platform_manager.platform_insts:
                meta = platform.meta()
                if meta.name in ("aiocqhttp", "qq_official", "qqbot", "qq_official_webhook"):
                    return f"{meta.id}:GroupMessage:{gid}"
        except Exception as exc:  # noqa: BLE001
            logger.debug("[df_loot] 解析群会话失败: %s", exc)
        return None

    def _current_platform_name(self) -> str:
        """当前主平台名称。"""
        try:
            for platform in self.context.platform_manager.platform_insts:
                meta = platform.meta()
                if meta.name in ("aiocqhttp", "qq_official", "qqbot", "qq_official_webhook"):
                    return meta.name
        except Exception:  # noqa: BLE001
            pass
        return ""

    @property
    def is_official_bot(self) -> bool:
        """是否是 QQ 官方机器人（用 openid 而非群号）。"""
        return self._current_platform_name() in ("qq_official", "qqbot", "qq_official_webhook")

    # ─────────────── 命令 ───────────────

    def _group_key(self, event: AstrMessageEvent) -> str:
        """从事件里取群标识。

        aiocqhttp → 群号；qq_official → group_openid。
        统一返回字符串，空串表示不是群消息。

        ⚠️ QQ 官方机器人的坑（实测 2026-10）
        ────────────────────────────────
        AstrBot 的 unified_msg_origin 里群会话是：
            qqbot:GroupMessage:<A>_<B>
        A、B 都是 32 位十六进制，但哪个是群 openid 取决于 AstrBot 版本。

        实测本机（AstrBot 4.29.0 + qq_official）：
            qqbot:GroupMessage:84CE...2441_A13C...CED
            84CE...2441  ← 发不出去（HTTP 400 群已注销）
            A13C...CED  ← 能发（HTTP 200）

        即：群 openid 在下划线**后面**那段。旧代码取 parts[0] 是错的，
        导致 /df 订阅 存进去的是发不出去的 ID。
        """
        raw = ""
        try:
            origin = getattr(event, "unified_msg_origin", "") or ""
            if ":GroupMessage:" in origin:
                raw = origin.split(":GroupMessage:")[-1].strip()
        except Exception:  # noqa: BLE001
            pass
        if not raw:
            try:
                gid = event.get_group_id()
                raw = str(gid).strip() if gid else ""
            except Exception:  # noqa: BLE001
                raw = ""

        if not raw:
            return ""

        # QQ 官方：复合 key 取「最后一段」——实测这才是群 openid
        if self.is_official_bot and "_" in raw:
            parts = [p for p in raw.split("_") if p]
            if len(parts) >= 2 and len(parts[-1]) >= 16:
                return parts[-1]
            if parts and len(parts[0]) >= 16:
                return parts[0]
        return raw

    def _group_candidates(self, event: AstrMessageEvent) -> list:
        """返回群标识候选列表（qq_official 下可能有两段）。

        发送时逐个尝试，第一个发得通的就是真·群 openid。
        aiocqhttp 只有一个候选。
        """
        raw = ""
        try:
            origin = getattr(event, "unified_msg_origin", "") or ""
            if ":GroupMessage:" in origin:
                raw = origin.split(":GroupMessage:")[-1].strip()
        except Exception:  # noqa: BLE001
            pass
        if not raw:
            try:
                gid = event.get_group_id()
                raw = str(gid).strip() if gid else ""
            except Exception:  # noqa: BLE001
                raw = ""
        if not raw:
            return []

        if not (self.is_official_bot and "_" in raw):
            return [raw]

        parts = [p for p in raw.split("_") if len(p) >= 16]
        if not parts:
            return [raw]
        # 后段优先（实测能发），再退前段
        out = []
        for p in reversed(parts):
            if p not in out:
                out.append(p)
        return out

    @property
    def rank_base(self) -> str:
        """排行服务地址（用于 /df 今日战绩 查询）。"""
        return "http://df.guixijiang.cn:8799"

    async def _fetch_rank_player(self, name: str, rng: str = "today"):
        """按游戏 ID 从排行服务查询玩家数据。"""
        if aiohttp is None:
            logger.error("[df_loot] 缺少 aiohttp 依赖，无法查询排行")
            return None
        url = f"{self.rank_base}/api/player"
        params = {"name": name, "range": rng}
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(url, params=params) as resp:
                    if resp.status != 200:
                        logger.warning("[df_loot] 排行查询返回 %s", resp.status)
                        return None
                    return await resp.json(content_type=None)
        except asyncio.TimeoutError:
            logger.warning("[df_loot] 排行查询超时")
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("[df_loot] 排行查询异常: %s", exc)
            return None

    @staticmethod
    def _fmt_money(v) -> str:
        """金额格式化：1.2亿 / 3430.3万 / -451.7万 / 12,345。"""
        try:
            n = float(v or 0)
        except (TypeError, ValueError):
            return "0"
        sign = "-" if n < 0 else ""
        n = abs(n)
        if n >= 1e8:
            s = f"{n / 1e8:.2f}".rstrip("0").rstrip(".")
            return f"{sign}{s}亿"
        if n >= 1e4:
            s = f"{n / 1e4:.1f}".rstrip("0").rstrip(".")
            return f"{sign}{s}万"
        return f"{sign}{int(n):,}"

    def _build_rank_text(self, data, query: str) -> str:
        """把排行服务返回的数据渲染成文字。"""
        if not data or not data.get("ok"):
            return "查询失败，排行服务暂时不可用，请稍后再试。"

        if not data.get("found"):
            return (
                f"未找到玩家「{query}」今日的出货数据。\n\n"
                "⚠️ 只有使用 delta-force-overlay 软件并开启排行榜同步的玩家才能查询到数据。\n"
                "如需参与，请在软件「出货播报 → 今日出货排行榜」中打开同步开关。"
            )

        p = data.get("player") or {}
        name = p.get("name") or query
        gained = p.get("gained") or 0
        profit = p.get("profit") or 0
        battles = p.get("battles") or 0
        escapes = p.get("escapes") or 0
        red = p.get("redCount") or 0
        keys = p.get("keyCount") or 0
        rank = p.get("rank") or 0
        online = p.get("online")

        lines = [
            f"【{name}】今日出货战绩",
            "",
            f"带出总计：{self._fmt_money(gained)} 哈夫币",
            f"净收益：{'+' if profit >= 0 else ''}{self._fmt_money(profit)}",
            f"对局数：{battles} 局 · 撤离 {escapes} 次",
            f"红装：{red} 件 · 房卡：{keys} 件",
        ]
        if rank:
            lines.append(f"今日排名：第 {rank} 名")
        if online is not None:
            lines.append(f"在线状态：{'在线' if online else '离线'}")

        red_items = p.get("redItems") or []
        if red_items:
            lines.append("")
            lines.append("出货明细（红）：")
            for it in red_items[:5]:
                n = it.get("num") or 1
                num_txt = f" ×{n}" if n > 1 else ""
                lines.append(f"  · {it.get('name') or '?'}{num_txt} — {self._fmt_money(it.get('total'))}")

        key_items = p.get("keyItems") or []
        if key_items:
            lines.append("")
            lines.append("出货明细（房卡）：")
            for it in key_items[:5]:
                n = it.get("num") or 1
                num_txt = f" ×{n}" if n > 1 else ""
                lines.append(f"  · {it.get('name') or '?'}{num_txt} — {self._fmt_money(it.get('total'))}")

        lines.append("")
        lines.append("————————————")
        lines.append("⚠️ 数据来自 delta-force-overlay 软件上传的对局记录")
        lines.append("只有使用该软件并开启排行榜同步的玩家才能查询到数据")
        return "\n".join(lines)

    # ── 卡战备 ──
    @property
    def broadcast_enabled(self) -> bool:
        """出货播报总开关。"""
        return bool(self._cfg("df_broadcast_enabled", True))

    @property
    def zhanbei_source(self) -> str:
        """卡战备数据源：xb（直连小黑盒）或 overlay（中转）。"""
        v = str(self._cfg("df_zhanbei_source", "xb") or "xb").strip().lower()
        return "overlay" if v == "overlay" else "xb"

    @staticmethod
    def _parse_zb_value(text: str) -> int:
        """解析用户输入的价值：60w / 60万 / 600000 → 整数。"""
        return df_loot_xb.parse_value(text)

    @staticmethod
    def _pick_zb_level(value: int):
        """按目标价值选最接近的档位，返回 (fake_level, target, label)。"""
        return df_loot_xb.pick_level(value)

    async def _fetch_zhanbei(self, level: int):
        """拉取指定档位的卡战备方案。

        数据源由配置 df_zhanbei_source 决定：
          xb      → 插件直连小黑盒（不需要运行软件）
          overlay → 通过 delta-force-overlay 中转（需要软件运行）
        """
        if self.zhanbei_source == "overlay":
            return await self._fetch_zhanbei_overlay(level)
        return await self._fetch_zhanbei_direct(level)

    async def _fetch_zhanbei_direct(self, level: int):
        """直连小黑盒。"""
        try:
            raw = await df_loot_xb.fetch_level(level)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[df_loot] 小黑盒直连查询异常: %s", exc)
            return None
        if not raw:
            return None
        target = 0
        for lv, tv, _lbl in df_loot_xb.XB_LEVELS:
            if lv == level:
                target = tv
                break
        schemes = []
        for g in raw:
            schemes.append(df_loot_xb.to_scheme(g, target, "xb"))
        return {"ok": True, "schemes": schemes, "source": "xb"}

    async def _fetch_zhanbei_overlay(self, level: int):
        """通过 delta-force-overlay 中转。

        overlay 的 lv 是「ZB_TARGETS 下标」（0-4），
        而小黑盒直连用的是 fake_level（0,1,2,3,5），需要转换。
        """
        if aiohttp is None:
            logger.error("[df_loot] 缺少 aiohttp 依赖")
            return None
        base = str(self.overlay_url or "").rstrip("/")
        if not base:
            return None

        # fake_level → overlay lv 下标
        lv_map = {0: 0, 1: 1, 2: 2, 3: 3, 5: 4}
        ov_lv = lv_map.get(level, 0)

        url = f"{base}/api/zhanbei"
        params = {"src": "xb", "lv": str(ov_lv)}
        try:
            timeout = aiohttp.ClientTimeout(total=45)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(url, params=params) as resp:
                    if resp.status != 200:
                        logger.warning("[df_loot] overlay 卡战备返回 %s", resp.status)
                        return None
                    j = await resp.json(content_type=None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[df_loot] overlay 卡战备查询异常: %s", exc)
            return None

        if not isinstance(j, dict) or not j.get("ok"):
            return None

        # 转换 overlay 格式 → 统一格式
        groups = {g.get("key"): g.get("name") for g in (j.get("groups") or [])}
        schemes = []
        for sc in (j.get("schemes") or []):
            stype = sc.get("schemeType") or ""
            items = []
            for it in (sc.get("schemeItems") or []):
                items.append({
                    "name": str(it.get("objectName") or ""),
                    "grade": int(it.get("objectGrade") or 0),
                    "pic": str(it.get("objectPic") or ""),
                    "cost": int(it.get("costHafCoin") or 0),
                    "value": int(it.get("currentValue") or 0),
                    "diff": int(it.get("diffValue") or 0),
                })
            schemes.append({
                "targetValue": int(sc.get("targetValue") or 0),
                "totalCost": int(sc.get("totalHafCost") or 0),
                "currentValue": int(sc.get("currentValue") or 0),
                "schemeKey": stype,
                "schemeName": groups.get(stype) or "方案",
                "updatedAt": 0,
                "items": items,
            })
        return {"ok": True, "schemes": schemes, "source": "overlay"}

    async def _fetch_zb_pics(self, schemes: list) -> dict:
        """下载卡战备物品图标（并发，失败忽略）。"""
        if aiohttp is None:
            return {}
        urls = set()
        for sc in schemes:
            for it in (sc.get("items") or []):
                pic = str(it.get("pic") or "")
                if pic.startswith("http"):
                    urls.add(pic)
        if not urls:
            return {}

        out = {}
        try:
            timeout = aiohttp.ClientTimeout(total=25)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async def one(u):
                    try:
                        async with sess.get(u) as r:
                            if r.status == 200:
                                out[u] = await r.read()
                    except Exception:  # noqa: BLE001
                        pass
                import asyncio as _aio
                await _aio.gather(*[one(u) for u in urls])
        except Exception:  # noqa: BLE001
            pass
        return out

    def _load_logo(self):
        """读取插件目录下的 logo.png（软件图标）。"""
        try:
            import os as _os
            base = _os.path.dirname(_os.path.abspath(__file__))
            path = _os.path.join(base, "logo.png")
            if _os.path.exists(path):
                with open(path, "rb") as f:
                    return f.read()
        except Exception:  # noqa: BLE001
            pass
        return None

    def _build_zhanbei_brief(self, data, label: str) -> str:
        """图片前的简版文字摘要。"""
        schemes = (data or {}).get("schemes") or []
        if not schemes:
            return ""
        parts = [f"【卡战备 · 目标 {label}】"]
        for sc in schemes:
            nm = sc.get("schemeName") or "方案"
            cost = self._fmt_money(sc.get("totalCost") or 0)
            cur = self._fmt_money(sc.get("currentValue") or 0)
            parts.append(f"{nm}：成本 {cost} · 价值 {cur}")
        return "\n".join(parts)

    def _build_zhanbei_text(self, data, label: str, want_value: int) -> str:
        """渲染卡战备文字。"""
        if not data or not data.get("ok"):
            return "卡战备查询失败，小黑盒接口暂时不可用，请稍后再试。"

        schemes = data.get("schemes") or []
        if not schemes:
            return "暂无卡战备数据，请稍后再试。"

        lines = [
            f"【卡战备 · 目标 {label}】",
            "（数据来自小黑盒，按装备价值配装）",
            "",
        ]

        for sc in schemes:
            gname = sc.get("schemeName") or "方案"
            cost = sc.get("totalCost") or 0
            cur = sc.get("currentValue") or 0
            items = sc.get("items") or []

            lines.append(f"━━ {gname} ━━")
            lines.append(
                f"配装成本 {self._fmt_money(cost)} · 当前价值 {self._fmt_money(cur)}"
            )
            # 小黑盒原始数据可能有重复项，按名称去重
            seen = set()
            for it in items:
                nm = it.get("name") or "?"
                if nm in seen:
                    continue
                seen.add(nm)
                ic = it.get("cost") or 0
                lines.append(f"  · {nm} — {self._fmt_money(ic)}")
            lines.append("")

        lines.append("————————————")
        # 数据来源标注（与图片版保持一致）
        src_tag = (data or {}).get("source") or "xb"
        if src_tag == "overlay":
            lines.append("由 delta-force-overlay 服务端提供数据源")
        else:
            lines.append("由小黑盒直接提供数据源")
        return "\n".join(lines)

    def _is_debug_group(self, event: AstrMessageEvent) -> bool:
        """是否是调试群。"""
        gid = self._group_key(event)
        if not gid:
            return False
        return gid in set(self.debug_groups)

    @filter.command("df")
    async def cmd_df(self, event: AstrMessageEvent):
        """三角洲播报管理命令。"""
        args = (event.message_str or "").strip().split()
        # 去掉命令本身
        if args and args[0].lstrip("/") in ("df", "三角洲"):
            args = args[1:]
        sub = args[0] if args else ""

        if sub in ("", "帮助", "help"):
            yield event.plain_result(
                "【三角洲出货播报】\n"
                "/df 状态 — 查看运行状态\n"
                "/df 订阅 — 把本群加入正式播报\n"
                "/df 退订 — 本群取消正式播报\n"
                "/df 上报测试 — 把本群设为「软件测试上报」推送群\n"
                "/df 取消上报测试 — 取消本群测试推送\n"
                "/df 今日战绩 <游戏ID> — 查询该玩家今日出货战绩\n"
                "/df 今日排行 — 生成今日出货排行榜汇总\n"
                "/df 卡战备 <11/18/55/60/78w> — 查询卡战备配装（如 60w）\n"
                "/df 测试 — 用当前数据渲染一张卡片（需调试群）"
            )
            return

        if sub == "状态":
            if not self._is_debug_group(event):
                yield event.plain_result("该命令仅调试群可用")
                return
            st = self._last_state or {}
            today = st.get("today") or {}
            player = st.get("player") or {}
            lines = [
                "【运行状态】",
                f"overlay：{self.overlay_url}",
                f"轮询：每 {self.poll_interval}s，已轮询 {self._poll_count} 次",
                f"已播报：{self._broadcast_count} 次",
                f"去重记录：{self._tracker.count} 局",
                f"播报群：{len(self.groups)} 个",
            ]
            if player:
                lines.append(f"当前账号：{player.get('characName') or '未知'}")
            if today:
                lines.append(
                    f"今日：{today.get('count', 0)} 局 · "
                    f"带出 {today.get('gain', 0):,} · "
                    f"红 {today.get('red', 0)} · 卡 {today.get('keys', 0)}"
                )
            if self._last_error:
                lines.append(f"⚠️ 最近错误：{self._last_error}")
            yield event.plain_result("\n".join(lines))
            return

        if sub == "订阅":
            gid = self._group_key(event)
            if not gid:
                yield event.plain_result("请在群里使用此命令")
                return
            groups = self.groups
            if gid in groups:
                yield event.plain_result(f"本群已在播报列表（{gid}）")
                return
            groups.append(gid)
            self._save_groups(groups)
            yield event.plain_result(f"✅ 本群已加入播报，共 {len(groups)} 个群")
            return

        if sub == "退订":
            gid = self._group_key(event)
            if not gid:
                yield event.plain_result("请在群里使用此命令")
                return
            groups = [g for g in self.groups if g != gid]
            self._save_groups(groups)
            yield event.plain_result("已取消本群播报")
            return

        if sub in ("上报测试", "测试上报"):
            gid = self._group_key(event)
            if not gid:
                yield event.plain_result("请在群里使用此命令")
                return
            groups = self.report_test_groups
            if gid in groups:
                yield event.plain_result(
                    f"本群已在测试推送列表（{gid}）\n"
                    "软件里点「测试上报」会推到这里"
                )
                return
            groups.append(gid)
            self._save_report_test_groups(groups)
            yield event.plain_result(
                f"✅ 本群已加入「软件测试上报」推送，共 {len(groups)} 个群\n"
                "注意：正式出货播报仍发到 /df 订阅 的群"
            )
            return

        if sub in ("取消上报测试", "取消测试上报"):
            gid = self._group_key(event)
            if not gid:
                yield event.plain_result("请在群里使用此命令")
                return
            groups = [g for g in self.report_test_groups if g != gid]
            self._save_report_test_groups(groups)
            yield event.plain_result("已取消本群的测试上报推送")
            return

        if sub == "日报":
            if not self._is_debug_group(event):
                yield event.plain_result("该命令仅调试群可用")
                return
            yield event.plain_result("正在生成日报…")
            try:
                await self._daily_report()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[df_loot] 手动日报失败: %s", exc)
                yield event.plain_result(f"日报失败：{exc}")
            return

        if sub in ("今日排行", "今天排行", "今日榜", "今天榜",
                   "排行", "排行榜", "榜单", "今日榜单", "今天榜单"):
            # 在哪个群发命令，就只发回哪个群
            cur_gid = self._group_key(event)
            if not cur_gid:
                yield event.plain_result("无法识别当前群，请在群里使用该命令")
                return
            yield event.plain_result("正在生成今日排行榜…")
            try:
                await self._daily_report(target_groups=[cur_gid])
            except Exception as exc:  # noqa: BLE001
                logger.warning("[df_loot] 今日排行失败: %s", exc)
                yield event.plain_result(f"生成失败：{exc}")
            return

        if sub in ("卡战备", "战备"):
            arg = " ".join(args[1:]).strip()
            if not arg:
                levels = " / ".join([x[2] for x in self.ZB_LEVELS])
                yield event.plain_result(
                    "用法：/df 卡战备 <11/18/55/60/78w>\n"
                    f"可选档位：{levels}\n"
                    "例如：/df 卡战备 60w\n"
                    "支持写法：60w / 60万 / 60W / 600000\n\n"
                    "会自动匹配最接近的档位"
                )
                return
            want = self._parse_zb_value(arg)
            if not want:
                yield event.plain_result(
                    f"无法识别价值「{arg}」\n"
                    "请用 60w / 60万 / 600000 这样的格式"
                )
                return
            lv, target, label = self._pick_zb_level(want)
            yield event.plain_result(f"正在查询卡战备 {label} 配装…")
            data = await self._fetch_zhanbei(lv)
            if data is None:
                if self.zhanbei_source == "overlay":
                    yield event.plain_result(
                        "查询失败：请确认 delta-force-overlay 正在运行且地址配置正确\n"
                        "（可在插件配置里把「卡战备数据源」改成 xb 直连小黑盒）"
                    )
                else:
                    yield event.plain_result(
                        "查询失败，小黑盒接口暂时不可用，请稍后再试\n"
                        "（可在插件配置里把「卡战备数据源」改成 overlay 中转）"
                    )
                return

            # 遵循「播报样式」配置（与出货播报一致）：
            #   card → 只发图片
            #   text → 只发文字
            #   all  → 文字 + 图片都发
            mode = str(self._cfg("df_render_mode", "card")).strip().lower()
            if mode not in ("card", "text", "all"):
                mode = "card"
            want_image = mode in ("card", "all")
            want_text = mode in ("text", "all")

            png = None
            if want_image:
                try:
                    schemes = data.get("schemes") or []
                    pics = await self._fetch_zb_pics(schemes)
                    png = render_zhanbei(
                        label=label,
                        target=target,
                        schemes=schemes,
                        source=data.get("source") or "xb",
                        max_items=8,
                        item_pics=pics,
                        logo=self._load_logo(),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[df_loot] 卡战备渲染失败，退回文字: %s", exc)
                    png = None
                    want_text = True   # 渲染失败必须给文字，否则没响应

            # 文字部分
            if want_text or (want_image and png is None):
                yield event.plain_result(self._build_zhanbei_text(data, label, want))

            # 图片部分
            if want_image and png is not None:
                yield event.chain_result(
                    [Image.fromBase64(base64.b64encode(png).decode("ascii"))]
                )
            return

        if sub in ("今日战绩", "战绩", "查询"):
            target = " ".join(args[1:]).strip()
            if not target:
                yield event.plain_result(
                    "用法：/df 今日战绩 <游戏ID>\n"
                    "例如：/df 今日战绩 鬼汐酱\n\n"
                    "⚠️ 只有使用 delta-force-overlay 软件并开启排行榜同步的玩家才能查询到数据"
                )
                return
            data = await self._fetch_rank_player(target, "today")
            if data is None:
                yield event.plain_result("查询失败，排行服务暂时不可用，请稍后再试。")
                return
            yield event.plain_result(self._build_rank_text(data, target))
            return

        if sub == "测试":
            if not self._is_debug_group(event):
                yield event.plain_result("该命令仅调试群可用")
                return
            # 随机模拟一次出货，用于预览样式
            battle, item_ids = random_battle()
            item_pics: dict[str, bytes] = {}
            if self._client is None:
                self._client = OverlayClient(self.overlay_url)
            for itid in item_ids:
                data = await self._client.fetch_item_pic(itid)
                if data:
                    item_pics[itid] = data
            try:
                png = render_card(
                    battle,
                    avatar_data=None,
                    item_pics=item_pics,
                    show_avatar=False,
                    show_items=True,
                )
                # 和正式播报保持一致：先文字，后图片
                brief = build_brief_text(battle)
                if brief:
                    yield event.plain_result(brief)
                yield event.chain_result(
                    [Image.fromBase64(base64.b64encode(png).decode("ascii"))]
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[df_loot] 测试渲染失败: %s", exc)
                yield event.plain_result(build_text_message(battle))
            return

        yield event.plain_result(f"未知子命令：{sub}\n发送 /df 帮助 查看用法")

    def _save_report_test_groups(self, groups: list) -> None:
        """写回测试推送群配置。"""
        try:
            self.astrbot_config["df_report_test_groups"] = groups
            save = getattr(self.astrbot_config, "save_config", None)
            if callable(save):
                save()
            else:
                logger.info("[df_loot] 新测试群列表（需手动保存）：%s", groups)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[df_loot] 保存测试群列表失败: %s", exc)

    def _save_groups(self, groups: list[int]) -> None:
        """写回配置。"""
        try:
            self.astrbot_config["df_broadcast_groups"] = groups
            save = getattr(self.astrbot_config, "save_config", None)
            if callable(save):
                save()
            else:
                logger.info("[df_loot] 新群列表（需手动保存）：%s", groups)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[df_loot] 保存群列表失败: %s", exc)
