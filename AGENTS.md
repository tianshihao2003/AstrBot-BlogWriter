# AGENTS.md

AstrBot BlogWriter 插件：通过微信（weixin_oc）对话把内容发布到静态博客仓库 `tianshihao2003/dumplingandcakeblog`（Firefly Astro）。内容类型：动态（moments）、笔记（notebooks）、足迹（places）、友链（friends）、相册（album）、账单（bills）、日程（schedules，自然语言正则 + 微信提醒）。流程：图片上传 CloudFlare-ImgBed → 生成 markdown（对齐博客 zod schema）→ GitHub API 提交 main 分支 → Actions 构建 + EdgeOne Pages 部署。

**2026-08-23 重大变更**：移除全部 AI 自然语言抽取（LLM 调用、`/模型列表` 命令、免命令自然语言识别、`ai_*` 配置项全删），账单/日程只走本地正则；所有内容必须显式用命令创建。同日对齐博客最新文件格式（见「业务规则」各条）。

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
4. `/相册 验收相册` + 发图 + `/发布` → 博客仓库出现 `src/content/album/验收相册.md`（仅 title/date/imgbedFolder），图床出现 `blog/album/验收相册/` 目录；⚠️ 重点验证**图床中文目录**上传解码正常；Actions 构建后访问 `https://blog.tsh520.cn/album/验收相册/` 能看到照片
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
- 会话：30 分钟超时、每用户同时仅一个会话、`allow_users` 白名单（空 = 全部拒绝）；**非白名单或无关消息必须静默放行**，不得抢占其他插件/AI。
- 图床上传目录：动态/笔记/足迹照片统一用 `imgbed_upload_folder`（默认 `blog/moments`，与博客惯例一致）；友链无图片。
- 相册：`/相册 相册名` 会话只收图片；**存在性按 title/文件名判断**（列出 `src/content/album/` 解析每个文件的 title/imgbedFolder，博客文件名与 title 不一定相同，如 `xiangce1.md` 的 title 是「测试相册」）；命中则只传图不写文件、照片上传到**命中相册自己的 imgbedFolder**（不触发构建，博客详情页运行时从图床动态拉图）；未命中才创建仅含 `title`/`date`/`imgbedFolder` 的 md，不生成 `-1` 后缀。
- 动态媒体：GIF 走 Image 组件（`.gif` 已在白名单）；视频走 `Video` 组件（微信适配器下载到本地 `.mp4`），**仅动态会话接收**（`_extract_images(allow_video=...)`，笔记/足迹/相册/账单/日程仍只收图片，账单/日程为文本会话），URL 与图片一样进 `images` 数组；大小上限视频 100MB/图片 20MB；微信 raw_message type 5（video_item）兜底 ref 带 `.mp4` 后缀。
- **账单**：`/账单` 支持自然语言**正则**解析（2026-08-23 起无 AI）：`今天午餐微信花了32`/`发工资12000` → `amount/type/category/account/date/description`；金额必抓（`块/元/￥`），分类/账户白名单优先匹配未命中则新建；`午餐30晚餐45打车12` 一句多笔走 `parse_bills_batch` 批量会话；路径 `src/content/bills/YYYY-MM-DD-{slug}.md`（`slug=clean_filename_part(title or category)`），冲突 `-1..-10`；`amount` 正收入负支出，`type` 自动 `income/expense`
- **账单三类型（2026-08-23，对齐博客 bill-adapter 净资产口径）**：type 枚举 `expense/income/liability`（transfer 无数据不用）。类型判定显式优先：首词类型前缀 `支出/消费/花费→expense`、`收入→income`、`负债/借款/还款→liability`（须后跟空格，`消费30` 无空格不剥前缀）；无前缀时按关键词——收入（工资/收入/到账…）> 负债（借款/欠款/负债/花呗/白条 或 还款/偿还/还了）> 默认支出。**liability 符号特殊**：借入为正（新增负债）、还款为负（减少负债），不取绝对值（前端 `liability += amount` 累计，展示 `Math.max(0, liability)`）；liability 固定 `category: 负债`（tags 由 build_bill_md 自动 `["负债"]`），不套默认分类/账户；账户别名表 `花呗/白条/信用卡`（对齐现有 `account: 花呗` 数据，别名不参与标题清理）。回复文案带类型标签（支出/收入/负债）
- **日程三命令细分（2026-08-23，防 category 生成错）**：`/日程` 普通日程（category: schedule，含时间可提醒）；`/生日` 生日（category: birthday，复用 `parse_schedules_batch`，单条与批量同入口，每年重复全天）；`/纪念日` 纪念日（category: anniversary，`parse_anniversary` 解析「标题 日期 [@人物]」，日期支持 `1月1日`/`2026-01-01`/`农历5月20`，每年重复全天）。**防呆**：`/日程` 单条内容含「生日/生气/纪念日」关键词时不解析，提示改用对应专用命令（批量生日 ≥2 条仍兼容直出）；空会话 kind 为 `birthday`/`anniversary`，解析成功后转 `schedule` 复用发布路径（`_publish` 的 kind 判断含三种）。生日/纪念日全天 → 不调度微信提醒
- **日程**：`/日程` 支持自然语言**正则**解析（2026-08-23 起无 AI）：`明天下午3点高优在会议室A开周会 每周重复 提前15分钟` → `title/date/allDay/priority/location/repeat/remind_before`；时间基准 `now`，`allDay` 无时间则真，`priority` 映射 `高→high` 等，`repeat` 命中 `每天/每周/每月/每年`，`remind_before` 抽“提前N分钟”否则取配置 `schedule_remind_before`（默认 10，0 为准点）；**批量生日** `parse_schedules_batch` 全文扫描日期+人物映射（结束位置最靠右者优先，防“二姐”被子串“姐”抢、防上一个人的词干扰），农历生日产 `isLunar/lunarMonth/lunarDay`（不存公历 date，公历换算由博客端 lunar-javascript 完成），文件名 `lunar-M-D-{slug}.md`（`schedule_filename()`），公历生日过今年顺延明年；**提醒**：含时间且非全天的日程，`remind_at = date - remind_before`，`apscheduler` 持久化到 `data/schedules_reminder.json`，重启通过 `_restore_reminders` 恢复，到点 `weixin_oc` 私聊 `🔔 日程提醒`，支持 `/提醒 列表/取消`；全天生日不调度提醒
- **导航网站（2026-08-23）**：`/导航 网址` → xxapi 图标接口 `https://v2.xxapi.cn/api/ico?url=<编码网址>`（博客 `scripts/添加导航/index.js` 同款、无需 Key，返回 `{"code":200,"data":"图标直链"}`）→ 下载字节入会话（失败不中止，icon optional 允许无图标发布）→ 键值对补信息（`parse_daohang_text`：名称/分类/描述/颜色，`#标签`）→ `/发布` 图标上传独立目录 `daohang_upload_folder`（默认 `blog/bangumi` 同级的 `blog/daohang`，图床文件名 `{域名}-icon.{ext}` 保留点对齐 `blog.tsh520.cn-icon.webp`）→ `build_daohang_md` 生成 `src/content/daohang/{域名slug}.md`（slug=域名点转横线，对齐现有 `app-pagescms-org.md`；**不用数字编号**——现有编号按分类分段规则复杂，无编号文件大量存在）；md 格式 plain 风格：name/url/icon/description/category/tags 行内无引号/color 带引号，featured/order 不写
- **影视（2026-08-23）**：`/影视 片名` → TMDB `/search/multi`（zh-CN，跳过 person 取第一个 movie/tv）→ 立即下载海报字节入会话 images（用户不可发图，封面固定 TMDB）→ 会话文本：`评分 8`（`parse_media_score`，0-10 clamp）、`#标签`、其余为影评正文 → `/发布` 上传封面到独立目录 `bangumi_upload_folder`（默认 `blog/bangumi`）→ `build_bangumi_md` 生成 `src/content/bangumi/anime/<片名>.md`（category: anime + subcategory: movie/tv，**对齐现有影视条目放 anime 子目录的惯例**，status: 2 看过，无评分/标签不写字段）。**连通性**：TMDB 国内时通时不通，`tmdb_api_base`/`tmdb_image_base` 可配反代（默认官方）；无 key 提示申请；本机直连 2026-08 实测通（API 401/图片 404 即连接成功）
- **格式对齐（2026-08-23，改 md 生成时必须保持）**：bills/schedules/places 用博客最新风格——字符串一律双引号（date/datetime 形态除外）、tags 行内数组 `["a"]`、photos 多行带引号；places 新增 `description: "记录在{省}{市}的足迹。"`、不写 `visitCount`；schedules 空 `location/repeat/person` 不写字段、支持 `person/isLunar/lunarMonth/lunarDay`；friends 保持无引号风格但 `weight: 0` + 末尾 `group: other`；album 加 `subtitle: 记录{标题}`、`imgbedFolder` 带引号；moments/notebooks 维持原样（published/date 无引号 + tags 多行）
- **Python 3.8 兼容**：本地测试环境为 Python 3.8.6，函数注解禁止用 `str | None`（3.10+ 语法，import 即崩），一律 `Optional[str]`
- 所有失败路径返回明确中文错误信息并写日志。

## 格式验证流程（改 md 生成必做，2026-08-23 实测通过）

1. 用 core 的 build_xxx_md 生成全部类型样例（含账单三类型、日程三形态）
2. 复制进博客 `src/content/` 对应目录 → `pnpm build`（exit 0 = zod 全过）→ grep dist 页面确认渲染 → **立即删除样例**
3. 2026-08-23 验证结论：全部 7 类内容（11 种形态）schema 通过、页面正常渲染
4. ⚠️ 教训：往博客目录放/删临时文件前**必须先确认目标已存在**（`ls` 检查），曾被 `cp` 覆盖用户已有 moments 文件、`rm -rf` 误删用户「日常随笔」目录（幸与 git HEAD 一致可 `git checkout -- <路径>` 恢复）；删除只允许逐个精确文件名，禁止按目录删

## 环境

- 开发机为 Windows + Git Bash；插件实际运行在服务器上的 AstrBot 框架内，本地无法端到端运行（无 AstrBot 运行时），验证靠单测 + 冒烟测试。
- 本仓库是插件仓库，无 CI；博客仓库的 Actions 构建在提交后由 GitHub 自动触发，无需 webhook。
