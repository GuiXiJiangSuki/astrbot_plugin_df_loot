# 三角洲出货播报 · astrbot_plugin_df_loot

> 监控《三角洲行动》对局，**出红装 / 房卡时自动推送到 QQ 群**，附带精美卡片与物品明细。

[![AstrBot](https://img.shields.io/badge/AstrBot-4.25.3%2B-blue)](https://github.com/AstrBotDevs/AstrBot)
[![Platform](https://img.shields.io/badge/平台-aiocqhttp%20%7C%20QQ官方机器人-green)]()
[![License](https://img.shields.io/badge/许可-仅供学习-orange)]()

---

## ⚠️ 重要：必须配合 delta-force-overlay 使用

**本插件本身不登录游戏、不获取任何数据。**

所有对局数据都来自另一款软件 **delta-force-overlay**（三角洲 Overlay）——它在你自己的电脑上运行，用你的 QQ 扫码登录 WeGame，读取你的战绩。

```
┌─────────────────────────┐         ┌──────────────────┐         ┌──────────┐
│  delta-force-overlay    │  HTTP   │   AstrBot 插件    │  推送   │  QQ 群    │
│  （你的电脑，扫码登录）  │ ──────> │  astrbot_plugin_  │ ──────> │          │
│  读取 WeGame 战绩数据    │  :8788  │  df_loot          │         │          │
└─────────────────────────┘         └──────────────────┘         └──────────┘
```

**使用前必须先安装并登录 delta-force-overlay：**

👉 **下载地址：[delta-force-overlay-release](https://github.com/GuiXiJiangSuki/delta-force-overlay-release/releases)**

| 步骤 | 说明 |
|---|---|
| 1 | 下载 `delta-force-overlay-x.x.x.exe`，双击运行（免安装单文件） |
| 2 | 在软件里**扫码登录 WeGame** |
| 3 | 保持软件运行（它会开一个本地服务，默认端口 8788） |
| 4 | 记下服务地址，填到插件配置的 `df_overlay_url` 里 |

> 💡 如果 AstrBot 和 overlay 不在同一台机器，需要让 overlay 监听 `0.0.0.0`，或用内网穿透/反代把 8788 暴露给 AstrBot。

---

## 功能

| 功能 | 说明 |
|---|---|
| **实时播报** | 每打一局就检查，出红或房卡立刻推送 |
| **图片卡片** | 渲染精美卡片：头像、撤离状态、击杀数、带出价值、物品图标 |
| **文字兜底** | 渲染失败自动退回纯文字，不会漏报 |
| **多群播报** | 支持多个群，可用 `/df 订阅` 动态添加 |
| **测试群分离** | 软件「测试上报」只推测试群，不打扰正式播报群 |
| **灵活过滤** | 按带出价值、是否撤离、物品类型过滤 |
| **多平台** | 支持 aiocqhttp（NapCat 等）和 QQ 官方机器人 |

## 播报效果

```
┌────────────────────────────────────────────┐
│  (头像)  鬼汐酱                    带出     │
│          撤离成功 · 2 杀          300.4万   │
├────────────────────────────────────────────┤
│  [红 2]  [房卡 1]                          │
│  🖼 克劳迪乌斯半身像  [红]        132.4万   │
│  🖼 军用控制终端      [红]         38.1万   │
│  🖼 食堂洗碗间        [房卡]      133.7万   │
│  🖼 单反相机 ×2                    27.5万   │
└────────────────────────────────────────────┘
```

---

## 安装

### 方式一：AstrBot 插件市场（推荐）

在 AstrBot 后台 → 插件市场，搜索「**三角洲出货播报**」安装。

### 方式二：手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/GuiXiJiangSuki/astrbot_plugin_df_loot.git
```

或直接下载 ZIP 解压到 `AstrBot/data/plugins/astrbot_plugin_df_loot/`。

### 依赖

```bash
pip install aiohttp
```

> AstrBot 通常已内置 `aiohttp`，缺失时才需要装。

安装后**重启 AstrBot**，在插件管理里启用「三角洲出货播报」。

---

## 配置

AstrBot 后台 → 插件管理 → 三角洲出货播报 → 配置

### 必填

| 配置项 | 说明 | 示例 |
|---|---|---|
| `df_overlay_url` | **delta-force-overlay 的服务地址** | `http://192.168.1.5:8788` |
| `df_broadcast_groups` | 正式播报群列表 | 见下方说明 |

**群标识怎么填？**

| 平台 | 填什么 | 示例 |
|---|---|---|
| `aiocqhttp`（NapCat 等） | QQ 群号（纯数字） | `123456789` |
| `qq_official`（QQ 官方机器人） | 群的 `group_openid`（32 位十六进制） | `A13C979D1EE90713E3F294C903761CED` |

> 💡 **推荐用 `/df 订阅` 命令自动添加**，不用手动查 openid。

### 常用可选项

| 配置项 | 默认 | 说明 |
|---|---|---|
| `df_enable_red` | `true` | 播报红装（grade 6） |
| `df_enable_keycard` | `true` | 播报房卡（ID 以 1505 开头） |
| `df_min_value` | `0` | 最低带出价值（哈夫币），0 不限制 |
| `df_only_escape` | `false` | 只播报撤离成功的对局 |
| `df_at_all` | `false` | 出红时 @全体成员 |
| `df_render_mode` | `card` | `card`=图片卡片 / `text`=纯文字 |
| `df_poll_interval` | `20` | 轮询间隔秒（10~600） |
| `df_show_avatar` | `false` | 卡片显示账号头像 |
| `df_show_items` | `true` | 卡片显示物品图标 |

### 软件上报（可选）

如果你有**多个人**都在用 overlay，想让他们的出货都汇总到同一个机器人播报：

| 配置项 | 说明 |
|---|---|
| `df_report_enabled` | 开启上报接收 |
| `df_report_port` | 上报服务端口（默认 8790，需在 Docker 里映射） |
| `df_report_token` | 鉴权 Token，与软件里填的一致 |

软件侧在「设置 → 出货播报 QQ 群」里填：

- **上报地址**：`http://你的地址:8790/df/report`
- **Token**：与插件配置一致
- **播报开关**：打开

---

## 命令

| 命令 | 权限 | 说明 |
|---|---|---|
| `/df` `/df 帮助` | 所有人 | 查看命令列表 |
| `/df 状态` | 调试群 | 运行状态、今日统计、最近错误 |
| `/df 订阅` | 群内任何人 | 把当前群加入**正式播报** |
| `/df 退订` | 群内任何人 | 取消当前群正式播报 |
| `/df 上报测试` | 群内任何人 | 把当前群设为**软件测试上报**推送群 |
| `/df 取消上报测试` | 群内任何人 | 取消本群测试推送 |
| `/df 测试` | 调试群 | 用当前数据渲染一张卡片预览 |

### 正式播报 vs 测试推送

| 类型 | 触发 | 发到哪 |
|---|---|---|
| 正式播报 | 真实出货 | `/df 订阅` 的群 |
| 测试推送 | 软件里点「测试上报」 | `/df 上报测试` 的群 |

这样调试时不会打扰正式播报群。

---

## 数据判定规则

字段含义全部来自 overlay 对 WeGame 接口的实测：

| 类别 | 规则 | 备注 |
|---|---|---|
| **红装** | `grade === 6` | 曼德尔超算、火箭燃料、万足金条等 |
| **房卡** | `id` 以 `1505` 开头 | 各种房间门禁卡 |
| **金装** | `grade === 5` | 不单独播报，卡片中显示 |
| **撤离成功** | `gameResult === 0` | ⚠️ 反直觉：**1 才是失败** |
| **干员击杀** | `killPlayer` | `killCnt` 含人机，不能用 |

### 去重机制

用 `roomId` 作唯一标识，同一局只播报一次。插件启动时会把当前对局标记为已见，**重启不会重复播报旧对局**。

---

## 常见问题

### 没有播报

1. 发 `/df 状态` 看最近错误
2. 浏览器打开 `{df_overlay_url}/health`，应返回 `{"ok":true}`
3. 浏览器打开 `{df_overlay_url}/api/state`，确认 `ok: true` 且 `player` 不为空（说明 overlay 已登录）
4. 确认群里发过 `/df 订阅`，或配置里填了群标识

### QQ 官方机器人报「请求的资源不存在(用户/群已注销)」

**群 openid 填错了。** 解决：

1. 在目标群里发 `/df 订阅`（插件会自动取正确的 openid）
2. 或从 AstrBot 日志里找 `qqbot:GroupMessage:xxxx_yyyy`，**`_` 后面那段**才是群 openid

> 实测 AstrBot 4.29 + qq_official：`qqbot:GroupMessage:<A>_<B>` 里 **B 才是群 openid**，A 发不出去。

### 卡片渲染失败

自动退回纯文字。想要卡片需 AstrBot 的 `html_render` 服务可用（通常需要 Chromium / Playwright）。

### 播报重复

确认只有一个 AstrBot 实例在跑此插件。去重记录是内存级的，多实例会各自去重导致重复。

### overlay 显示「未登录」

打开 delta-force-overlay，扫码登录 WeGame。登录态过期需要重新扫码。

---

## 文件结构

```
astrbot_plugin_df_loot/
├─ main.py              插件入口：轮询、播报、命令、上报服务
├─ df_loot_core.py      数据层：overlay 客户端、出货判定、去重
├─ df_loot_card.py      渲染层：卡片 HTML / 纯文字
├─ df_loot_report.py    上报接口：解析外部 POST 的出货数据
├─ df_loot_daily.py     每日汇总
├─ df_loot_maps.py      地图 ID → 名称
├─ df_loot_sample.py    随机样本（/df 测试 用）
├─ _conf_schema.json    配置页定义
├─ metadata.yaml        插件元数据
├─ requirements.txt     依赖（aiohttp）
└─ .astrbot-plugin      插件标记
```

---

## 相关项目

| 项目 | 说明 |
|---|---|
| [delta-force-overlay-release](https://github.com/GuiXiJiangSuki/delta-force-overlay-release) | **必装**，Overlay 软件本体 |
| [AstrBot](https://github.com/AstrBotDevs/AstrBot) | 机器人框架 |

---

## 致谢

- 作者：**鬼汐酱**
- B 站：[space.bilibili.com/2060418999](https://space.bilibili.com/2060418999)

---

## 许可

仅供个人学习交流使用。

使用的 WeGame 接口属于**非公开接口**，官方随时可能变更导致失效。请勿用于商业用途，使用风险自负。
