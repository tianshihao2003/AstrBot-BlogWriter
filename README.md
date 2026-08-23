# AstrBot BlogWriter

通过微信（个人微信 / QQ 等 IM 平台）对话，直接发布博客内容到静态博客仓库（Firefly Astro）：

- **动态（moments）** —— 随手一条消息，图片自动上链
- **笔记（notebooks）** —— 分类 + 标题 + 正文，多段自动拼接
- **足迹（places）** —— 输入地名，坐标由高德地理编码自动获取
- **友链（friends）** —— 键值对文本自动识别字段，一键上链
- **相册（album）** —— 报个相册名直接发图，图片进图床 `blog/album/<相册名>`，博客动态加载

图片自动上传 CloudFlare-ImgBed 图床，内容生成 markdown（严格对齐博客 zod schema）后通过 GitHub API 提交 main 分支，触发 Actions 构建与 EdgeOne Pages 自动部署，全链路零人工干预。

## 功能特性

- ✅ 微信对话直接发布动态 / 笔记 / 足迹 / 友链 / 相册 / 账单 / 日程（全部命令式，2026-08 起移除 AI 抽取，纯正则解析零外部 AI 依赖）
- ✅ **自然语言正则记账 + 三类型显式区分**（2026-08-23）：`/账单 今天午餐微信花了32`（支出）/ `/账单 发工资12000 银行卡`（收入）/ `/账单 花呗借款5000`（负债借入，正数）/ `/账单 花呗还款2000`（负债还款，负数）；首词可显式指定类型 `支出/收入/负债` 和分类白名单名（如 `/账单 食品酒水 买了箱牛奶45`、组合 `/账单 支出 餐饮 午餐30`）；分类自动识别靠关键词表（午餐→餐饮、打车→交通、奖金→职业收入…），识别不到可发 `分类:人情收礼`、`账户:现金` 在发布前手动纠正；支持一句多笔 `午餐30晚餐45打车12`；负债 category 固定「负债」、自动识别花呗/白条/信用卡账户，与博客净资产卡片联动
- ✅ **日程细分三命令**（2026-08-23）：`/日程` 普通日程（如 `明天下午3点高优在会议室A开周会 每周 提前15分钟`）、`/生日` 生日（category: birthday，支持一句多人+农历，农历按 `isLunar/lunarMonth/lunarDay` 存储、文件名 `lunar-M-D` 前缀）、`/纪念日` 纪念日（category: anniversary，每年重复，支持 `@人物`）；用错命令会自动提示纠正，杜绝 category 生成错
- ✅ **日程微信提醒**：到点前 `N` 分钟（可口语“提前15分钟”或设置默认）微信私聊 `🔔 提醒`，`apscheduler` 持久化，重启不丢，支持 `/提醒 列表/取消`
- ✅ 动态支持图片、动图 GIF 与视频：微信视频自动下载、上传图床，URL 进动态 `images` 数组（视频仅动态支持）
- ✅ 相册发布：新相册自动建 `src/content/album/<名>.md`（含 `subtitle`/`imgbedFolder`，对齐博客现有格式）触发构建；已有相册（按标题/文件名判断）只传图不写文件，照片进命中相册自己的图床目录
- ✅ 图片自动上传图床（统一上传到 `imgbed_upload_folder`，默认 `blog/moments`，与博客图床目录惯例一致）
- ✅ 足迹坐标自动获取（高德地理编码 API）；足迹格式对齐博客最新（含 `description` 固定句式）
- ✅ 自定义标签：`/动态 内容 #日常 #2026`，自动合并默认标签（纯数字标签自动加引号防 schema 校验失败）
- ✅ 友链字段智能识别：兼容中英文键名（站点名称/名称/name/博客名…）、冒号/单空格分隔、乱序、缺字段提示；`weight: 0` + `group: other` 对齐现有文件
- ✅ 会话式图片收集：微信图片不能图文同发，先发命令再发图，`/发布` 统一提交
- ✅ 文件名自动冲突处理：同日多条自动追加 `-1`、`-2` 后缀
- ✅ **影视记录**（2026-08-23）：`/影视 侏罗纪世界` 自动从 TMDB 搜索中文片名并获取封面，封面上传图床独立目录（默认 `blog/bangumi`）；随后可发 `评分 8`、`#标签`、一句话影评，生成 `src/content/bangumi/anime/片名.md`（category: anime + subcategory: movie/tv）；**自己发图可替换封面**；TMDB 搜不到自动转手动模式（自己发图 + `名称:`/`类型:` 修正）；TMDB 连不上可在配置填反代地址
- ✅ **书籍记录**（2026-08-23）：`/书籍 书名` 自己发封面图（必须），评分/标签/书评同影视，生成 `src/content/bangumi/book/书名.md`（category: book）
- ✅ **导航网站**（2026-08-23）：`/导航 https://example.com` 自动通过 xxapi（v2.xxapi.cn，无需 Key）获取网站图标，图标上传图床独立目录（默认 `blog/daohang`）；随后键值对补 `名称/分类/描述/颜色` + `#标签`，生成 `src/content/daohang/域名-slug.md`（文件名对齐现有 app-pagescms-org.md 命名）
- ✅ 白名单控制：非白名单用户静默忽略，绝不抢占其他插件/AI 的消息处理
- ✅ 微信图片下载兜底：适配器 aiohttp 被微信 CDN TLS 风控拒绝时，自动改用 curl 下载 + AES 解密

## 安装

1. 将 `main.py`、`blog_writer_core.py`、`metadata.yaml`、`README.md`、`_conf_schema.json` 打包为 `AstrBot-BlogWriter-vX.Y.Z.zip`（打包规范见 `AGENTS.md`）
2. AstrBot 后台 → 插件管理 → 安装插件 → 上传 zip
3. 在 WebUI 插件的「配置」面板中填写配置项（见下）

> 要求 AstrBot >= v4.22.0（`metadata.yaml` 中已声明 `astrbot_version: ">=4.22.0"`）

## 配置

| 配置项 | 说明 |
|---|---|
| github_token | GitHub fine-grained PAT（Contents 读写，仅限博客仓库） |
| github_repo | 博客仓库，默认 `tianshihao2003/dumplingandcakeblog` |
| github_branch | 目标分支，默认 `main` |
| imgbed_upload_url | 图床上传地址，默认 `https://img.tsh520.cn/upload` |
| imgbed_token | 图床 API Token（`Authorization: Bearer` 认证） |
| imgbed_upload_folder | 图片上传目录（动态/笔记/足迹照片统一），默认 `blog/moments` |
| album_folder_prefix | 相册图片目录前缀，默认 `blog/album`（实际目录 = 前缀 + 相册名） |
| wx_cdn_base_url | 微信媒体 CDN 地址（图片下载兜底用），默认 `https://novac2c.cdn.weixin.qq.com/c2c` |
| amap_key | 高德 Web 服务 Key（足迹坐标用）；留空读环境变量 `AMAP_KEY` |
| tmdb_api_key | TMDB API Key（影视封面用）；themoviedb.org 免费注册申请 v3 Key |
| tmdb_api_base / tmdb_image_base | TMDB API/图片地址（服务器连不上官方时填自建反代；默认官方地址） |
| bangumi_upload_folder | 影视封面上传图床目录（独立于动态图片目录），默认 `blog/bangumi` |
| daohang_upload_folder | 导航网站图标上传图床目录，默认 `blog/daohang` |
| moment_tags / place_tags | 动态/足迹默认标签，默认 `["日常"]` / `["旅游"]` |
| default_note_dir | 笔记默认分类目录，默认 `日常随笔` |
| friend_default_avatar | 友链默认头像（未提供头像链接时），默认 `/assets/ziyuan/tx.webp` |
| friend_tags | 友链默认标签，默认 `["Blog"]` |
| allow_users | 允许使用的用户 ID 列表（个人微信为 `xxx@im.wechat` 格式）；空列表 = 全部拒绝 |
| bill_default_account / bill_default_category | 账单默认账户/分类（解析为「其他」时替换） |
| schedule_default_priority / schedule_remind_before | 日程默认优先级/默认提前提醒分钟（0=准点） |

`allow_users` 怎么拿自己的 ID：发一条消息，在 AstrBot 控制台日志里看发送者 ID，填进去。

## 使用

| 命令 | 说明 |
|---|---|
| `/动态 今天去了公园 #日常` | 创建动态会话，可发图片 / GIF / 视频（可多发） |
| `/笔记 日常随笔 标题` | 创建笔记会话，正文由后续文本消息追加，可发图片 |
| `/足迹 陕西 华阴市华山 去找宝宝了 #旅游` | 创建足迹会话，坐标自动获取，可发照片 |
| `/友链` | 创建友链会话，逐行发送键值对信息自动识别 |
| `/相册 情侣头像` | 创建相册会话，直接发图片（可多发）；相册已存在（按标题/文件名判断）则只追加照片 |
| `/账单 午餐微信花了32` / `/账单` | 自然语言正则记账；首词可显式指定类型 `支出`/`收入`/`负债`（如 `/账单 负债 花呗借款5000`）；负债：借入正数、还款负数（`花呗借款5000` / `花呗还款2000`）；一句多笔自动批量 |
| `/日程 明天15点开周会 @会议室A 高优 每周 提前15分钟` / `/日程` | 普通日程（category: schedule），到点微信提醒 |
| `/生日 我的农历8.24` / `/生日` | 生日（category: birthday，每年重复全天）；一句多个：`/生日 我的农历8.24对象12.22妈8.7都是农历`；农历文件名 `lunar-M-D` |
| `/纪念日 结婚纪念日 农历5月20` / `/纪念日` | 纪念日（category: anniversary，每年重复全天），格式 `标题 日期 [@人物]`，日期支持 `1月1日` / `2026-01-01` / `农历5月20` |
| `/影视 侏罗纪世界` | 影视记录：TMDB 自动搜片取封面（上传图床 `blog/bangumi`），随后可发 `评分 8`、`#标签`、影评；自己发图可替换封面；搜不到转手动模式（发图 + `名称:`/`类型:`） |
| `/书籍 认知觉醒` | 书籍记录：自己发封面图（必须），评分/标签/书评同影视，生成 bangumi/book 条目 |
| `/导航 https://example.com` | 添加导航网站：xxapi 自动取图标（上传图床 `blog/daohang`），随后键值对发 `名称/分类/描述/颜色`、`#标签`，`/发布` 生成 daohang 条目 |
| `/提醒 列表` / `/提醒 取消 标题` | 查看/取消待提醒日程 |
| `/发布` | 结束会话：上传图床 → 生成 markdown → GitHub 提交 |
| `/取消` | 放弃当前会话 |
| `/状态` | 查看当前会话 |

### 友链示例

```
我：/友链
我：网站名称: RAGNote
    描述:Life is code. I will debug it.
    网站地址: https://ragnote.top/
    头像:https://ragnote.top/Avatar.png
我：/发布
```

支持键名：名称（站点名称/名称/名字/昵称/博客名/博主/name/title…）、描述（简介/介绍/签名/desc/descr…）、链接（网址/地址/主页/url/link/siteurl…）、头像（图标/logo/avatar/imgurl…）。键名不认识但冒号后是完整 URL 的按值自动归类。

### 会话规则

- 会话 30 分钟无操作自动作废
- 同一用户同时只允许一个会话
- 非白名单用户的消息一律静默放行，不干扰其他功能

## 注意事项

- GitHub Token 只保存在服务器 AstrBot 配置中，泄露请立即在 GitHub 撤销重建
- 足迹坐标获取失败会中止发布（避免产生无坐标数据）
- 任一图片上传失败会中止整个发布，不会产生半成品提交
- 微信图片下载兜底依赖服务器 `curl`（需可用）
- 动态的作者/头像由博客端统一配置（`content.config.ts` 的 schema 默认值），插件不再按条写入 `author`/`avatar` 字段
- 相册追加模式不触发博客构建：详情页照片即时可见，列表页预览图在下次构建后刷新
- 相册按标题判断是否存在（文件名与标题不一定相同，如 `xiangce1.md` 的标题是「测试相册」）；追加照片会上传到命中相册自己的图床目录

## 测试

```bash
pip install httpx pycryptodome
python -m unittest discover -s tests
```

139 个单元测试 + 集成冒烟测试（stub astrbot 依赖），覆盖命令解析、markdown 生成（含 YAML 边界与 2026-08 新格式）、图床/高德/GitHub/TMDB/xxapi 响应解析、友链字段识别、账单三类型、日程/生日/纪念日解析（含农历与防呆）、影视（TMDB/手动/换封面）与书籍全流程、导航全流程、消息权限与放行逻辑。

## 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) —— 多平台 AI 聊天机器人框架
- 插件 API 依据 AstrBot 官方文档：[插件开发指南](https://docs.astrbot.app/dev/star/)
