# AGENTS.md

AstrBot BlogWriter 插件：通过微信（weixin_oc）/ QQ（aiocqhttp）对话把内容发布到静态博客仓库 `tianshihao2003/dumplingandcakeblog`（Firefly Astro）。内容类型：动态（moments）、笔记（notebooks）、足迹（places）、友链（friends）、相册（album）、影视/书籍（bangumi）、导航（daohang）、转载文章（posts）。账单/日程已于 2026-10-16 移除（见下方变更条目）。流程：图片上传 CloudFlare-ImgBed → 生成 markdown（对齐博客 zod schema）→ GitHub API 提交 main 分支 → Actions 构建 + EdgeOne Pages 部署。

**2026-10-16 变更：账单/日程 功能整体移除（对齐博客删集合）**：博客端同日摘除了 `bills`/`schedules`/`apps` 三个集合及其空目录，`/bills`、`/schedules` 路由与导航入口更早就已删除。插件这边的记账/日程仍在「提示发布成功 → 文件写进已删目录 → 回复链接 404」地静默失效，故一并摘除：① core 删除整个「账单 / 日程」段（含 `parse_bill`/`parse_bills_batch`/`parse_schedule`/`parse_schedules_batch`/`parse_anniversary`/`build_bill_md`/`build_schedule_md`/`schedule_filename`/各类 `_detect_*`/`_parse_*`，共 885 行），`COMMANDS` 去掉 `账单/日程/生日/纪念日`，常量 `BILL_CATEGORIES`/`BILL_ACCOUNTS`/`SCHEDULE_PRIORITIES`/`BILL_WIZARD_CREDIT_ACCOUNTS` 删除；② main.py 删掉 `/账单`、`/日程`、`/生日`、`/纪念日` 四个命令入口、`_start_*`/`_apply_*_defaults`/`_bill_*` 辅助方法、账单与日程的会话向导分支、四个 `_publish` 分支与发布后的日程提醒调度（共 597 行），命令列表与帮助文案同步；③ `_conf_schema.json` 删除 `bill_default_account`/`bill_default_category`/`schedule_default_priority`（保留 `schedule_remind_before`）；④ 测试删除 51 个相关用例（198 → 147），`TestBillSchedule` 类改名为 `TestMediaFlow` 只保留影视全流程（它原本就挂在这个类里）；⑤ README/metadata.yaml 文案同步。⑥ 站长确认后**连带删除提醒子系统**（`/提醒` 不是独立功能——它的唯一创建入口就是日程发布，删日程后必然一起走）：`/提醒` 命令与 `_handle_remind`、`_schedule_remind`、`_send_remind`/`_send_remind_sync`、`_load_reminders`/`_save_reminders`/`_restore_reminders`、`_resolve_reminder_file`、`REMINDER_FILE`、`self._reminders`/`self._reminder_file`/`self._scheduler`/`self._loop`、APScheduler 依赖与 `HAS_APSCHEDULER`、`_proactive_supported`（摘掉日程后已无调用者）、`terminate()` 里的调度器清理、帮助手册 `/提醒` 两行、`_conf_schema.json` 的 `schedule_remind_before`（**至此插件不再依赖 apscheduler**）；测试再删 8 例（147 → 139）。**教训**：批量按行号删代码时，`@staticmethod` 这类装饰器常常在被删函数的**前一行**，只删 `def` 起始行会把装饰器留给下一个函数——本次就误把 `@staticmethod` 挂到了 `_unauthorized_hint_text` 上，靠测试才抓出来。删完务必跑测试 + grep 被删符号名。

**2026-09-05 重大变更（v1.0.50，对齐博客去负债）**：博客账单模型收紧为**支出/收入两类型**（`src/content.config.ts` enum 只剩 `income/expense`，PagesCMS 同步），插件同步：① `parse_bill` 删负债判定——「还款/偿还/还了/已还」记为 expense（category 固定「还款」、amount 恒为负，「还款」前缀同样映射 expense+还款分类）；「借款/欠款/欠了/负债」命中直接报错「负债类型已下线」（不再解析），borrow 关键词表里的花呗/白条移除（它们只是账户别名）；② 向导类型只剩 `1.支出 2.收入 3.直接发布 4.重说`（`parse_choice(text, 4)`），`_bill_type_prompt`/`_bill_next_step`/`_bill_step_prompt`/`_bill_confirm_text` 删 repay_split/credit_split/liability 全部分支；③ 信用购（快车道 `信用购/信用买` 首词、向导选 7、发布拆两笔、bill_pick_credit_account/bill_pick_repay_account/bill_pick_repay_fund 三个 step）整体删除——现在输入「信用购 花呗 午餐30」按普通支出解析（账户花呗）；④ `BILL_WIZARD_LIABILITY_ACCOUNTS` 更名 `BILL_WIZARD_CREDIT_ACCOUNTS`（语义=信用账户作为普通账户选项，不再关联负债类型）；⑤ `_apply_bill_defaults` 删「负债不套默认账户」特判；⑥ 发布逻辑删还债/信用购拆双笔段（`/发布` 只写单笔）；⑦ 测试改写：test_core 3 个负债用例改为「借款报错 + 还款记支出 + 还款前缀」，test_smoke 6 个用例改写（含 `test_start_bill_borrow_rejected`、`test_bill_credit_option_removed` 回归）。全部 169 个测试通过。

**2026-09-05 补充（v1.0.51，文案清理）**：站长反馈 `/账单` 帮助文案不应出现「还款」字样——用户可见文案一律不宣传还款：`_start_bill` 空会话帮助删「还款：花呗还款2000」行、向导重说提示删「花呗还款200」示例、`_help_text` 使用手册删还款命令行。**解析兜底逻辑保留**（内容提到还款仍静默按支出、分类「还款」解析，不再单独提示），AGENTS.md/README 仅在开发者文档记录该行为，聊天文案零出现。

**2026-08-23 重大变更**：移除全部 AI 自然语言抽取（LLM 调用、`/模型列表` 命令、免命令自然语言识别、`ai_*` 配置项全删），账单/日程只走本地正则；所有内容必须显式用命令创建。同日对齐博客最新文件格式（见「业务规则」各条）。

**2026-08-25 重大变更（v1.0.41-45）**：① 交互式向导（`Session.wizard` 状态机 + core 的 `parse_choice/format_choices/is_wizard_*`）覆盖 /笔记 /相册 /影视 /账单，回数字选择，`取消` 退出、`跳过` 走默认、已识别字段自动跳过不重复问；带参快捷写法全部保留。② 账单新增**信用购**（`/账单 信用购 花呗 午餐30` 或向导选 7）：`/发布` 一次原子写入两笔——支出 `-N`（账户=信用账户）+ 负债 `+N`（title=`{账户}消费`、category 负债），`meta["credit_split"]=True` 标记；批量账单不支持。③ 图床上传按扩展名声明真实 MIME（`_mime_for_filename`，修复 octet-stream 导致浏览器下载）。④ 笔记图片写 frontmatter `images:` 数组（对齐博客 2026-09-27 归档卡片），不再正文 `![](url)`。⑤ 用户输入的笔记本/相册名经 `clean_filename_part` 清洗后才入 meta（防路径注入），`_publish` 笔记分支再兜底清洗一次。⑥ 向导/会话对空消息（`msg_str=''`，QQ 心跳/重试）一律放行不回复（防刷屏）。⑦ 相册向导列表与数字映射必须同源同序（`_album_index` 返回 `entries`，排序后同时派生 display 与 titles——曾因排序不同步导致选 1 拿到别的相册）。⑧ 影视 game 落 `bangumi/game/`（category game 无 subcategory），anime/documentary 写对应 subcategory（`build_bangumi_md` 白名单已扩）。⑨ 提醒 job id 统一 `{user}_{title}_{time}`（restore 与 schedule 同格式）。⑩ 发布笔记到**不存在的笔记本**时自动补建 `_index.json`（`_ensure_notebook_index`：先拉目录列表，确认目录缺失且目标不存在才提交；列表拉取失败时不动作沿用旧行为；`_list_notebook_names` 失败返回 None 区分空仓库，成功消息带「🎉 已新建笔记本」）。⑪ **白名单检查前置**（2026-09-04 群聊 bug）：`on_message` 里 `_allowed` 判断必须在「无会话媒体提示」之前——非白名单用户在群聊发图片也必须零回复，否则会抢占群聊并暴露插件存在；回归测试 `test_permission_denied_image_no_hint`。⑫ **合规对齐官方规范**（2026-09-04 对照 docs.astrbot.app/dev/star）：a) 命令/会话消息被插件消费后必须 `event.stop_event()` 阻断传播（yield 后事件默认继续进入 LLM 阶段，`/` 前缀或 @ 唤醒时 LLM 会重复回复）；无关消息放行时**不得** stop_event；b) 提醒持久化路径 `_resolve_reminder_file()`：识别 AstrBot 根（cwd 下有 data/plugins 或 data/config）则写 `data/plugin_data/blog_writer/schedules_reminder.json`（重装插件不丢），否则回退插件目录；读取时新路径优先、旧插件目录路径兜底迁移；c) `terminate()` 除关闭 httpx 外必须 `scheduler.shutdown(wait=False)`，防重载后调度器残留双发提醒；d) `_conf_schema.json` 与 `_cfg()` 读取双向一致（审计脚本核对过零差异）；e) 禁止裸 `except:`、禁止 PEP604 语法（3.8 兼容）。⑬ **群聊静默策略**（2026-09-15）：a）「无会话收到媒体→提示」**仅私聊生效**（`_is_group_message` 基于 unified_msg_origin 的 `GroupMessage` 段判断，识别不出按私聊处理）——白名单用户在群里随手发图不再被提示刷屏；b）新增 `allow_groups` 配置（群号白名单）：**留空 = 不限制群**（保持既有行为），**非空 = 仅列表内的群可交互**，列表外群一律静默（含命令）。回归测试 `test_group_image_no_hint` / `test_allow_groups_blocks_unlisted_group` / `test_allow_groups_allows_listed_group`。⑭ **公众号文章转载 /转载**（2026-09-16，本机实测验证可行）：`/转载 <mp.weixin.qq.com 链接>` 一条命令直发（不进会话）：MicroMessenger 手机 UA + Referer 抓取 → core `extract_wx_article` 解析（标题 activity-name/公众号 js_name/日期 var ct 时间戳兑底/摘要首段兑底）→ `html_to_markdown`（**段落靠 </section> 分隔——实测结论，不能只看 <p>**；data-src 惰加载优先；过滤页脚二维码/头像；http mmbiz 升 https）→ 图片下载转存图床（mmbiz 无 Referer 可下，失败带 Referer 重试）→ `build_article_md` 生成 posts 文章（title/published/tags/description/image/sourceLink + 转载声明引用块，**不写 category**，分类=目录）→ commit 到 `src/content/文章/{article_default_dir}/`（默认 技术分享）。防止环境畸异：检测微信「环境异常/完成验证」验证页→明确报错；原子性：任一图片下载/上传失败→中止发布。新配置：article_default_dir / article_upload_folder（blog/article）/ article_tags（["转载"]）。回归测试：TestWxArticle（9 例）+ TestArticleFlow（4 例）。⑮ **导航对齐博客文件夹分类**（2026-09-21，对齐博客 v1.40.0/v1.40.3）：博客导航条目精简为四字段（name/url/icon/description，tags/color/featured/order/image 已从 schema 删除），分类改为**文件夹即分类**；插件同步：`build_daohang_md` 只写四字段；`/导航` 后向导列出现有分类文件夹（`_list_daohang_folders`，GitHub API）供选择或新建（直接回复分类名会忽略大小写/空格差异匹配现有文件夹，未命中则清洗后新建），发布路径 `src/content/导航/<分类>/<域名slug>.md`，博客链接修正为 /projects（原 /daohang 已失效）；新配置 `daohang_default_category`（默认 未分类）；回归测试更新 test_daohang_full_flow + 新增 test_daohang_new_category_by_name。同日顺手修复：未安装 apscheduler 时 `_restore_reminders()` 从未被调用（重启后 /提醒 列表为空），已在 else 分支补调用。⑯ **导航 tags 回归**（2026-09-21，v1.0.56）：博客同日把 `tags` 加回 daohang schema（可选，`array(string).default([])`，导航页分类标签行渲染并支持筛选，现有 50 个条目全带 tags），插件同步恢复支持：`build_daohang_md` 新增 `tags` 参数（非空写块状列表 `tags:\n  - xx`，与现有条目一致；空则不写字段）、会话内 `#标签` 收集（daohang 分支先 `extract_tags` 再 `parse_daohang_text`，避免标签粘在描述里）、向导与 `/帮助` 文案补 `#标签`；`parse_daohang_text` 注释修正（color 仍不解析、tags 走 #标签）。回归测试：新增 test_build_daohang_md_tags、test_daohang_full_flow 增加 #标签 断言（189 全绿）。⑰ **平台兼容：企业微信 + QQ 官方机器人**（2026-09-30，v1.0.57）：站点新增「企业微信(含微信客服)」`wecom` 与「QQ 官方机器人(Websocket)」`qq_official` 两个平台后，在这两个平台发插件命令会收到框架的「LLM 请求失败：未找到任何可用的对话模型（提供商）」——根因是**白名单**：新平台的 sender_id 形态与 NapCat(QQ 号)/微信(wxid) 完全不同（QQ 官方=openid/member_openid，企业微信=企业成员 UserID，客服=external_userid），`allow_users` 里没有 → 插件静默放行 → 事件落到框架 LLM 阶段（该 AstrBot 未配 chat provider，于是回错误）。适配：① 白名单双来源——`allow_users` ∪ AstrBot 全局 `admins_id`（读 `event.is_admin()`，框架 waking_check 按全局配置打 role），管理员跨平台免配置；② 新增 `unauthorized_hint`（默认 true）：非白名单用户**私聊发明确命令**时回一条含其 ID + 平台中文名的提示（带 300s 冷却、可关），**群聊与非命令仍完全静默**（保持既有「不抢其他插件/AI」语义），并 `stop_event` 阻断；③ 平台识别工具 `_platform_name(event)`（优先官方 `get_platform_name()`，回退 UOM 前缀）与中文名映射 `_PLATFORM_LABELS`；④ 提醒平台能力 `_proactive_supported(event)`（读 `platform_meta.support_proactive_message`）：企业微信为 False，含时间日程发布成功时在回复里加「当前平台不支持主动推送，提醒可能收不到」；⑤ `_download_http` 统一带浏览器 UA（QQ 官方/企业微信图片是外链 CDN，python-httpx 默认 UA 会被 403）；⑥ 入口/权限日志全部带 `platform=` 字段便于排查。回归测试：TestPlatformCompat 9 例（双白名单、提示与冷却、群聊静默、可关、提醒提示），并把 test_permission_denied_silent / test_bill_non_whitelist_silent 改成「群聊静默 + 私聊命令回 ID 提示」的新语义（198 全绿）。




先读文档：`DESIGN.md`（已批准的完整设计，含协议与规则）、`README.md`（安装/配置/使用）。

> **规范维护（元规则）**：开发过程中新形成的任何约定（打包、发布、命名、目录、流程、限制……）必须**当场**补进本文件，并随代码一起提交推送；不允许只停留在对话里或口头约定，防止后续开发遗忘。

## 博客仓库（本机克隆）

- **`E:\GithubProgect\MyRunProject\dumplingandcakeblog`** —— 插件发布的目标仓库本机克隆，改插件前先看它有没有新改动。
- 插件本仓库（当前目录）位于博客工作树内的 `plug-in/AstrBot/AstrBot BlogWriter/`（`plug-in/` 下按框架分组，未来可放其他插件），是**独立 git 仓库**；博客 `.gitignore` 已忽略 `/plug-in/`，两边互不提交。
- 博客的权威约定在博客仓库内：`src/content.config.ts`（各 collection 的 zod schema）、`CLAUDE.md`（工程规范）、`AGENTS.md`（入口）。插件生成的 frontmatter 必须与 zod schema 对齐。
- 博客图床目录惯例（2026-08-13 统一）：插件上传的图片全部进 `blog/moments`；相册 `blog/album/<相册名>`；bangumi `blog/bangumi`。
- 博客已去掉动态每条的自定义 `author`/`avatar`（schema 提供默认值 `团子和蛋糕`、`/assets/ziyuan/tx.webp`）——插件不得再写这两个字段；`_conf_schema.json` 中已移除对应配置项（旧配置残留键会被静默忽略）。

## 文件与分层（硬性边界）

- `blog_writer_core.py` —— **纯逻辑层**：命令解析、markdown/YAML 生成、请求构造、响应解析。**禁止 import astrbot**，必须可独立单测。
- `main.py` —— AstrBot 粘合层：`BlogWriter(Star)` 类、`on_message` 路由、会话状态机、所有外部网络调用（图床/高德/GitHub/微信图片下载）。
- `_conf_schema.json` —— WebUI 配置面板 schema；新配置项必须同时在此声明并在 `main.py` 的 `_cfg()` 中读取（配置经 `__init__(self, context, config)` 注入）。
- `metadata.yaml` —— 插件元数据：`name` 必须带 `astrbot_plugin_` 前缀，`version` 带 `v` 前缀，`astrbot_version: ">=4.22.0"`。
- `tests/test_core.py`（纯逻辑单测）+ `tests/test_smoke.py`（stub astrbot 模块的集成冒烟测试，`sys.path.insert` 后 import main）。

## 构建 / 测试

无构建步骤；无 lint/format 配置。

```bash
pip install httpx pycryptodome
python -m unittest discover -s tests
```

修改逻辑层时跑 test_core；改动 main.py 后必须跑 test_smoke（内部 stub `astrbot`，不含真实网络）。

## 发版流程与打包规范（每次发版必做，按顺序执行）

1. **全量测试**：`python -m unittest discover -s tests` 必须全部通过（含新增功能用例）。
2. **打包 zip**：按下方细则输出到本插件目录的 `打包/`，命名 `AstrBot-BlogWriter-vX.Y.Z.zip`（版本号在上一版基础上 +1）。
3. **推送到 GitHub（必须）**：发版后必须 `git add` 全部改动并提交、推送到 origin main —— 远端仓库 `https://github.com/tianshihao2003/AstrBot-BlogWriter`（公开仓库）。提交信息格式：`feat: AstrBot BlogWriter 插件 vX.Y.Z`（纯文档改动用 `docs: ...`）。推送后 `git ls-remote origin HEAD` 校验远端已是最新提交，`git status` 确认工作区干净。
4. **交付**：告知用户 zip 路径，并提醒按下方「发布前验收清单」在服务器实测。

### 打包细则

- **只打包 5 个文件**：`main.py`、`blog_writer_core.py`、`metadata.yaml`、`README.md`、`_conf_schema.json`。不含 `tests/`、`AGENTS.md`、`DESIGN.md`。
- **输出位置**：插件目录内的 `打包/` 子目录（`E:\GithubProgect\MyRunProject\dumplingandcakeblog\plug-in\AstrBot\AstrBot BlogWriter\打包\`）。压缩包一律收进本插件的 `打包/`，不散放在 `plug-in/` 根下。
- **zip 数量上限**：`打包/` 目录最多保留 **10 个**；每次打出新 zip 后，立即删除最旧的一个（旧版本号靠前的最先删）。
- **不改内部版本字段**：`metadata.yaml` 与 `main.py` 的 `@register` 里的版本历史上一直保持 `v1.0.0`，只有 zip 文件名与 git 提交信息递增，打包时不要动它们。
- 打包用 Python（Windows Git Bash 无 zip 命令），把版本号替换成新版：

```python
python - <<'EOF'
import zipfile, os
src = os.getcwd()
out_dir = os.path.join(src, "打包")
os.makedirs(out_dir, exist_ok=True)
out = os.path.join(out_dir, "AstrBot-BlogWriter-v1.0.19.zip")
files = ["main.py", "blog_writer_core.py", "metadata.yaml", "README.md", "_conf_schema.json"]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for f in files:
        z.write(os.path.join(src, f), f)
EOF
```

## 发布前验收清单（自动化测试无法覆盖真实外部依赖，发版后需在服务器人工验收）

本地测试桩掉了图床/GitHub/高德/微信适配器，只验证逻辑与流程。真实环境验收用白名单账号依次发送，逐项确认：

1. `/动态 测试` + 发图 + `/发布` → 博客出现动态：**无 `author`/`avatar` 字段**，图片 URL 为 `blog/moments`
2. `/足迹 陕西 华山 测试` + 发图 + `/发布` → 足迹照片 URL 为 **`blog/moments`**（不再单独 `places`）
3. `/笔记 测试 标题` + 正文 + `/发布`、`/友链` → 键值对 → `/发布` → 正常发布
4. `/相册 验收相册` + 发图 + `/发布` → 博客仓库出现 `src/content/相册/验收相册.md`（仅 title/date/imgbedFolder），图床出现 `blog/album/验收相册/` 目录；⚠️ 重点验证**图床中文目录**上传解码正常；Actions 构建后访问 `https://blog.tsh520.cn/album/验收相册/` 能看到照片
5. 再次 `/相册 验收相册` + 新图 + `/发布` → **不产生新的 md 提交**（追加模式），详情页新图即时可见
6. `/相册 测试相册` + 发图 + `/发布` → 按 **title 判定**追加到已有「测试相册」（xiangce1.md）自己的目录 `blog/album/相册1`，且不新建文件（重复相册 bug 的回归验证）
7. `/动态 视频测试` + 发一段视频 + `/发布` → 动态 md 的 `images:` 出现 `.mp4` 链接；`/动态 动图测试` + 发 GIF → 出现 `.gif` 链接

任何一步不符合预期，看 AstrBot 控制台日志（插件全程写 logger 日志）定位排查。

## AstrBot API 约束（改 main.py 前必看）

- 消息监听：`@filter.event_message_type(filter.EventMessageType.ALL)`；旧 API `on_decorating_message_type(MessageType.ALL)` 已移除，勿用。
- 配置：`__init__(self, context, config)` + `AstrBotConfig`；`get_config()` 已过时。
- 网络：用 `httpx`（AstrBot 内置），**不要用 requests**；超时 15s 连接 + 30s 读取。
- 回复：`yield event.plain_result(...)`；日志用 `astrbot.api.logger`。
- `main.py` 对 core 的 import 有双路径兼容：先相对导入 `.blog_writer_core`，失败（非包形式加载）回退顶层导入 `blog_writer_core` —— 新增 core 符号时两处 import 列表都要加。
- 平台能力探测用 `hasattr`（如 `event.get_file_url()`），不可直接调用假定存在。

## 业务规则与陷阱（对博客仓库有真实影响）

- **发布原子性**：任一图片上传失败、或足迹高德地理编码失败 → 中止整个 `/发布`，绝不产生半成品提交。
- 文件名：动态/足迹 `YYYY-MM-DD`，笔记 `2026年8月8日`（中文、**不补零**）；冲突依次加 `-1`…`-10` 后缀。
- YAML 边界（博客 zod schema 校验，改 md 生成时必须保持）：时间不加引号、**纯数字标签必须加引号**（如 `"2026"`）、URL 裸写；动态 `id` = `ext-` + 毫秒时间戳。
- 图片：微信不能图文同发 → 会话式收集，先发命令再发图，`/发布` 统一提交；图片落位：动态/笔记追加正文 `![](url)`，足迹进 `photos` 列表。
- 微信 CDN 兜底：适配器 aiohttp 被 TLS 风控拒绝时改 curl 下载 + AES 解密，依赖服务器 `curl` 和 `pycryptodome`（延迟 import）。
- 会话：30 分钟超时、每用户同时仅一个会话、`allow_users` 白名单（空 = 全部拒绝）；**非白名单或无关消息必须静默放行**，不得抢占其他插件/AI。**例外（2026-09-30 v1.0.57）**：非白名单用户**私聊发明确命令**时回一条含其 ID 的一次性提示（`unauthorized_hint` 默认 true、300s 冷却、可关），群聊与非命令仍严格静默——这是为了新平台（企业微信/QQ 官方机器人，sender_id 是 openid/UserID 而非 QQ 号）自助加白名单。
- **平台兼容**：白名单是全局一份，但各平台 sender_id 形态不同（NapCat=QQ 号、微信=wxid、qq_official=openid、wecom=企业成员 UserID/客服 external_userid）→ 除 `allow_users` 外，**AstrBot 全局 `admins_id` 里的管理员（`event.is_admin()`）在任意平台都放行**，新平台接入优先用这条免配置。平台名一律走 `_platform_name(event)`（官方 `get_platform_name()`，回退 `unified_msg_origin` 前缀），不要硬编码解析 UOM 字符串。**平台能力用 `platform_meta` 读**（如 `support_proactive_message`：企业微信为 False，即该平台不支持主动推送），不要假定所有平台都支持主动推送。平台媒体多为外链 CDN，下载统一带浏览器 UA（`_DOWNLOAD_UA`）。
- 图床上传目录：动态/笔记/足迹照片统一用 `imgbed_upload_folder`（默认 `blog/moments`，与博客惯例一致）；友链无图片。
- 相册：`/相册 相册名` 会话只收图片；**存在性按 title/文件名判断**（列出 `src/content/相册/` 解析每个文件的 title/imgbedFolder，博客文件名与 title 不一定相同，如 `xiangce1.md` 的 title 是「测试相册」）；命中则只传图不写文件、照片上传到**命中相册自己的 imgbedFolder**（不触发构建，博客详情页运行时从图床动态拉图）；未命中才创建仅含 `title`/`date`/`imgbedFolder` 的 md，不生成 `-1` 后缀。
- 动态媒体：GIF 走 Image 组件（`.gif` 已在白名单）；视频走 `Video` 组件（微信适配器下载到本地 `.mp4`），**仅动态会话接收**（`_extract_images(allow_video=...)`，笔记/足迹/相册/账单/日程仍只收图片，账单/日程为文本会话），URL 与图片一样进 `images` 数组；大小上限视频 100MB/图片 20MB；微信 raw_message type 5（video_item）兜底 ref 带 `.mp4` 后缀。
- **账单 / 日程：已于 2026-10-16 整体移除**（博客端同时删掉了 bills/schedules 集合与 /bills、/schedules 路由）。原实现的一切约定（正则解析、时刻提取、自定义分类、支出/收入两类型、生日/纪念日/农历、发布后定时提醒调度）都不再生效，历史细节见上方 2026-10-16 变更条目与 git 历史。**不要恢复这些命令而不先恢复博客端集合**，否则又是「发布成功但站点看不到」。
- **导航网站（2026-08-23，2026-09-21 v1.0.55/56 对齐博客文件夹分类 + tags 回归）**：`/导航 网址` → xxapi 图标接口 `https://v2.xxapi.cn/api/ico?url=<编码网址>`（博客 `scripts/添加导航/index.js` 同款、无需 Key，返回 `{"code":200,"data":"图标直链"}`）→ 下载字节入会话（失败不中止，icon optional 允许无图标发布）→ 向导选分类文件夹（`_list_daohang_folders` 列 GitHub 上 `src/content/导航` 一级目录，可回数字/直接回分类名/新建，缺省 `daohang_default_category`=未分类）→ 键值对补信息（`parse_daohang_text`：名称/分类/描述）+ `#标签` → `/发布` 图标上传独立目录 `daohang_upload_folder`（默认 `blog/daohang`，图床文件名 `{域名}-icon.{ext}` 保留点对齐 `blog.tsh520.cn-icon.webp`）→ `build_daohang_md` 生成 `src/content/导航/<分类>/<域名slug>.md`（slug=域名点转横线，对齐现有 `app-pagescms-org.md`；**不用数字编号**——现有编号按分类分段规则复杂，无编号文件大量存在）；**分类由文件夹决定，不写 `category`**；md 字段 = `name/url` + 可选 `icon/description/tags`（tags 块状列表；color/featured/order/image 已从 schema 删除，一律不写）；博客链接 /projects
- **影视（2026-08-23，v1.0.40 增强）**：`/影视 片名` → TMDB `/search/multi`（zh-CN，跳过 person 取第一个 movie/tv）→ 立即下载海报字节入会话 images → 会话文本：`评分 8`（`parse_media_score`，0-10 clamp）、`#标签`、`名称: xxx`/`类型: 电影|剧集`（`parse_media_fields`）、其余为影评正文 → `/发布` 上传封面到独立目录 `bangumi_upload_folder`（默认 `blog/bangumi`）→ `build_bangumi_md` 生成 `src/content/追番/anime/<片名>.md`（category: anime + subcategory: movie/tv，**对齐现有影视条目放 anime 子目录的惯例**，status: 2 看过，无评分/标签不写字段）。**用户发图 = 替换封面**（清空重加，取最后一张）；**TMDB 搜不到/封面下载失败 → 手动模式**（manual 标记，等用户发图，发布缺封面报错）。**连通性**：TMDB 国内时通时不通，`tmdb_api_base`/`tmdb_image_base` 可配反代（默认官方）；无 key 提示申请；腾讯云实测 API 不通、图片通 → Cloudflare Worker 反代 `tmdb.0824.uk`（API+图片同代理，按路径 /t/p/ 分流，缓存 1 天）
- **书籍（2026-08-23 v1.0.40）**：`/书籍 书名` 创建 book 会话 → 用户发封面图（必须，替换语义同影视）→ 评分/#标签/书评 → `/发布` 生成 `src/content/追番/book/<书名>.md`（category: book、**无 subcategory**、标准 bangumi 格式——现有 book/ 下的分类子目录是微信读书同步专属格式 doc_type: weread-highlights-reviews，手动书放 book/ 根目录）；封面同传 `bangumi_upload_folder`
- **格式对齐（2026-08-23，改 md 生成时必须保持）**：足迹（places）用博客最新风格——字符串一律双引号（date/datetime 形态除外）、tags 行内数组 `["a"]`、photos 多行带引号；places 写 `description: "记录在{省}{市}的足迹。"`、不写 `visitCount`；friends 保持无引号风格但 `weight: 0` + 末尾 `group: other`；album 加 `subtitle: 记录{标题}`、`imgbedFolder` 带引号；moments/notebooks 维持原样（published/date 无引号 + tags 多行）。（原先还覆盖 bills/schedules，两者已于 2026-10-16 移除。）
- **Python 3.8 兼容**：本地测试环境为 Python 3.8.6，函数注解禁止用 `str | None`（3.10+ 语法，import 即崩），一律 `Optional[str]`
- 所有失败路径返回明确中文错误信息并写日志。

## 格式验证流程（改 md 生成必做，2026-08-23 实测通过）

1. 用 core 的 build_xxx_md 生成全部类型样例（动态/笔记/足迹/友链/相册/影视/书籍/导航）
2. 复制进博客 `src/content/` 对应目录 → `pnpm build`（exit 0 = zod 全过）→ grep dist 页面确认渲染 → **立即删除样例**
3. 2026-08-23 验证结论：全部 7 类内容（11 种形态）schema 通过、页面正常渲染
4. ⚠️ 教训：往博客目录放/删临时文件前**必须先确认目标已存在**（`ls` 检查），曾被 `cp` 覆盖用户已有 moments 文件、`rm -rf` 误删用户「日常随笔」目录（幸与 git HEAD 一致可 `git checkout -- <路径>` 恢复）；删除只允许逐个精确文件名，禁止按目录删

## 环境

- 开发机为 Windows + Git Bash；插件实际运行在服务器上的 AstrBot 框架内，本地无法端到端运行（无 AstrBot 运行时），验证靠单测 + 冒烟测试。
- 本仓库是插件仓库，无 CI；博客仓库的 Actions 构建在提交后由 GitHub 自动触发，无需 webhook。
