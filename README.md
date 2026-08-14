# ocr-fanqie-novel

番茄读书群入群验证插件：新成员入群后要求发送**自己发布的番茄小说书评详情页截图**，
通过 PaddleOCR（PP-OCRv6）云端 API 识别书评信息，校验「我」徽章、书名、作者后
自动放行；验证失败则私信管理员决定通过或踢出。

## 功能

- **FR1 入群触发**：监听 OneBot V11 `group_increase` 事件，对新成员发送验证提示并开启超时计时
- **FR2 图片识别**：接收新成员发送的图片，调用 PaddleOCR 云端 API 识别文本
- **FR3 信息提取**：检测「我」徽章（书评为本人发布）、提取读者名、发布日期、评分、阅读时长、书评正文、书名与作者
- **FR4 综合判断**：本人书评 + 书名/作者合理性校验 + 可配置的放行策略（按群配置作者白名单）
- **FR5 通过放行**：发送欢迎消息
- **FR6 拒绝处理**：不执行禁言，结束会话并私信通知管理员决定通过或踢出
- **FR7/FR8 超时/重试**：超时未响应或连续识别失败达到上限，按验证失败处理并通知管理员决策
- **FR9 管理员命令**：`/kick`、`/keep` 决定踢出或保留
- **运行时重载**：`重载番茄OCR配置` 命令热更新放行策略
- **边界守卫**：监听退群/管理员变动/禁言事件，操作前查询群成员状态，避免对已退出成员执行无效动作

## 如何开始

前置要求：Python 3.13、[uv](https://docs.astral.sh/uv/)、一个 OneBot V11 实现（如
[NapCat](https://napneko.github.io/) / [go-cqhttp](https://docs.go-cqhttp.org/)）。

1. 安装依赖：

   ```bash
   uv sync --all-groups
   ```

2. 配置环境变量：复制 `.env.example` 为 `.env` 并填写：

   ```bash
   cp .env.example .env
   ```

   至少需要配置：
   - `FANQIE_OCR_API_TOKEN`：PaddleOCR 云端 API 令牌
   - `SUPERUSERS` / `FANQIE_ADMIN_IDS`：管理员 QQ 号（`/kick`、`/keep`、
     `重载番茄OCR配置` 命令权限，NoneBot 要求 SUPERUSER）
   - 群范围：放行策略 TOML 中配置群节点（配置了节点的群才验证）

3. 配置 OneBot V11 反向 WebSocket：

   在 NapCat / go-cqhttp 中添加反向 WebSocket 连接，地址指向本插件的
   `ws://<HOST>:<PORT>/onebot/v11/ws`（默认 `ws://127.0.0.1:8080/onebot/v11/ws`）。
   HOST / PORT 由 `.env` 中的 `HOST` / `PORT` 决定。

4. 首次运行生成放行策略配置，然后按需编辑（见下文）：

   ```bash
   uv run nb run
   ```

5. 运行机器人（开发热重载）：

   ```bash
   uv run nb run --reload
   ```

## 验证目标与判定

新成员需发送**自己发布的番茄小说书评详情页截图**。页面关键标识：

- 「**我**」徽章：书评为自己发布（他人书评无此徽章，将被拒绝）
- 读者名、发布日期、评分（★）、阅读时长、书评正文
- 书名（如 `综漫：吉他雇佣兵无法找到归宿？`）、作者

判定标准：**必须检测到「我」徽章 + 提取到书名与作者**。

## 配置

### 环境变量

以下变量通过 `.env`（或 `.env.dev` / `.env.prod`）配置，环境变量名大小写不敏感。

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `FANQIE_VERIFY_GROUPS` | 需要验证的群号集合，`[]` 表示全部群（与策略群节点取交集） | `[]` |
| `FANQIE_ADMIN_IDS` | 管理员 QQ 号集合，可接收通知并执行 `/kick` `/keep` | `[]` |
| `FANQIE_WELCOME_MESSAGE` | 入群提示消息 | 内置默认文案 |
| `FANQIE_RESPONSE_TIMEOUT` | 等待新成员发送截图的超时（秒），超时按验证失败处理并通知管理员决策 | `300` |
| `FANQIE_MAX_ATTEMPTS` | 识别失败允许尝试次数，达上限按验证失败处理并通知管理员决策 | `3` |
| `FANQIE_NOTIFY_ADMIN` | 验证失败时是否私信通知管理员决定通过或踢出 | `true` |
| `FANQIE_BOOK_NAME_MAX_LEN` | FR4 有效书名最大字符数 | `100` |
| `FANQIE_OCR_API_URL` | OCR 任务提交地址，留空用官方默认 | 官方默认 |
| `FANQIE_OCR_API_TOKEN` | OCR API 认证令牌（必填） | 空 |
| `FANQIE_OCR_TIMEOUT` | 单次 HTTP 请求超时（秒） | `15` |
| `FANQIE_OCR_POLL_TIMEOUT` | 等待 OCR 任务完成总超时（秒） | `120` |
| `FANQIE_OCR_MODEL` | PaddleOCR 模型名 | `PP-OCRv6` |
| `FANQIE_VERIFICATION_POLICY_PATH` | 放行策略 TOML 路径，空用 localstore 配置目录 | 空 |
| `FANQIE_MESSAGE_STORE_ENABLED` | 是否启用消息/审计存储 | `true` |
| `FANQIE_MESSAGE_STORE_SUMMARY_LIMIT` | 文本摘要最大字符数 | `500` |
| `FANQIE_MESSAGE_STORE_CLEANUP_ENABLED` | 关闭时是否清理过期记录 | `true` |
| `FANQIE_MESSAGE_STORE_RETENTION_DAYS` | 记录保留天数 | `30` |
| `FANQIE_MESSAGE_STORE_RECORD_API_CALLS` | 是否记录平台 API 调用审计 | `false` |

完整模板见 `.env.example`。

### 放行策略 TOML（FR4）

首次运行后生成在 `config/nonebot_plugin_ocr_fanqie_novel/verification_policy.toml`。
策略**以群为节点**组织：每个群下可配置多个作者，每个作者下可配置多个作品（书名）。

```toml
[verification]
require_all = false          # true 要求全部受支持元素匹配；false 按 required_elements
required_elements = ["book_name", "author"]  # require_all=false 时必配元素

# 群节点：群号 → 作者列表。配置了节点的群才执行验证。
[verification.groups]

[[verification.groups.868258211.authors]]
name = "阿百川大鬼"
books = ["综漫：吉他雇佣兵无法找到归宿？", "另一部作品"]

[[verification.groups.868258211.authors]]
name = "刘慈欣"
books = ["三体", "球状闪电"]
```

判定规则（按群）：
- **群未配置任何作者节点**：放行（宽松模式，仅校验必配元素存在）。
- **群配置了作者节点**：提取到的**作者必须命中其中一个作者名**（只校验作者，
  `books` 作品列表仅作配置参考与审计展示，不参与判定）。
- 群节点本身即**监控范围**：只有配置了节点的群才会执行入群验证。

受支持的元素名：`reader_name`、`publish_time`、`rating`、`read_duration`、
`book_name`、`author`、`review_text`。

## 管理员命令

| 命令 | 权限 | 说明 |
| --- | --- | --- |
| `/kick <user_id>` | 超级用户 / 配置管理员 | 将指定成员移出群聊 |
| `/keep <user_id>` | 超级用户 / 配置管理员 | 通过并保留成员 |
| `重载番茄OCR配置` | 超级用户 / 配置管理员 | 热重载放行策略 TOML |

`<user_id>` 也可用 `@成员` 代替。命令前缀由 `COMMAND_START` 决定（默认 `/`）。

## 验证流程

1. 新成员入群 → 机器人 @ 成员发送验证提示并开始超时计时
2. 成员发送**自己发布的书评详情页截图** → OCR 识别 → 提取「我」徽章、书名、作者、读者名等
3. 综合判断：本人书评（「我」徽章）+ 信息足够 + 放行策略满足 + 合理性通过 → **放行**（欢迎）
4. 否则（他人书评 / 缺少书名作者 / 白名单不命中 / 超时未发图 / 识别失败达上限）→ 结束会话并私信通知管理员，管理员用 `/kick` 或 `/keep` 决策

## 开发

```bash
task check   # ruff + pyright
task test    # pytest
task fix     # 自动修复 lint/格式
```

插件源码位于 `src/plugins/nonebot_plugin_ocr_fanqie_novel/`。

## 文档

- [NoneBot2 文档](https://nonebot.dev/)
- [PaddleOCR / PP-OCRv6](https://www.paddleocr.ai/)
