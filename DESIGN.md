# AstrBot BlogWriter 插件设计文档

日期：2026-08-08
状态：已批准（用户 2026-08-08 确认方案 A 全项 + 高德 API 取坐标）

## 1. 目标

通过个人微信与 AstrBot 对话，更新博客（tianshihao2003/dumplingandcakeblog，Firefly Astro）的三种内容：

- 动态（moments）
- 笔记（notebooks）
- 足迹（places）

图片自动上传到 CloudFlare-ImgBed 图床，正文/元数据按博客 zod schema 生成 markdown，通过 GitHub API 提交 main 分支，触发 Actions 构建与 EdgeOne Pages 部署。

## 2. 架构

```
个人微信 → AstrBot(腾讯云) → BlogWriter 插件
  ├─ 命令解析（/动态 /笔记 /足迹 /发布 /取消）
  ├─ 会话状态机（收集正文与图片）
  ├─ 图床上传 POST {imgbed_upload_url}（multipart field=file, header API_TOKEN）
  ├─ 足迹坐标：高德地理编码 https://restapi.amap.com/v3/geocode/geo
  ├─ 生成 markdown（对齐 src/content.config.ts zod schema）
  └─ GitHub REST API 提交 contents → main → Actions 构建 → EdgeOne Pages
```

## 3. 命令协议

| 命令 | 格式 | 行为 |
|---|---|---|
| /动态 | `/动态 今天去了公园` | 创建会话，可继续发图 |
| /笔记 | `/笔记 日常随笔 标题` | 正文由后续文本消息追加，直到 /发布 |
| /足迹 | `/足迹 陕西 华阴市华山 去找宝宝了` | 省 地点 体验；坐标由高德 geocode（省+地点）获取 |
| 图片消息 | 无命令直接发 | 归入当前会话（仅在会话激活时消费） |
| /发布 | `/发布` | 图床→md→GitHub 提交→回复结果 |
| /取消 | `/取消` | 丢弃会话 |
| /状态 | `/状态` | 查看会话状态 |

规则：

- 会话 30 分钟无操作自动作废；作废前不发提醒，但下次消息会提示「上一个会话已超时作废」
- 同一用户同时只允许一个会话
- 非白名单用户一律拒绝（回复「无权限」）
- 其他命令/普通消息不消费，放行给其他插件或 AI
- 缺省规则：`/动态` 无正文 → 报错提示；`/笔记 标题`（一个参数）→ 分类用默认目录，标题=该参数；`/足迹` 参数不足 → 报错
- 图片落位：动态/笔记 → 追加到正文末尾 `![](url)`；足迹 → photos 列表

## 4. 文件生成规则（对齐现有数据）

### 动态 → `src/content/moments/YYYY-MM-DD[-N].md`

```yaml
---
published: 2026-08-08 14:30:00   # 本地时间精确到秒
author: 团子和蛋糕                 # 配置
avatar: /assets/ziyuan/tx.webp   # 配置
id: ext-1785175842726            # ext- + 毫秒时间戳
tags:
  - 日常                          # 配置 moment_tags
---

正文文字

![](https://img.tsh520.cn/file/xxx.jpg)
```

### 笔记 → `src/content/life/notebooks/<分类>/2026年8月8日[-N].md`

```yaml
---
date: 2026-08-08
name: 标题
---

正文
```

分类来自 `/笔记` 第一个参数；默认 `日常随笔`（配置 default_note_dir）。

### 足迹 → `src/content/life/places/YYYY-MM-DD[-N].md`

```yaml
---
date: 2026-08-08
province: 陕西
city: 华阴市华山
experience: 去找宝宝了
visitCount: 1
lat: 34.477861
lng: 110.084789
photos:
  - "https://img.tsh520.cn/file/places/xxx.jpg"
tags:
  - 旅游
  - "2026"
---
```

lat/lng 由高德 geocode（address = 省+地点）获取；解析失败则中止发布并报错（绝不发布无坐标足迹）。

## 5. GitHub 提交

- 查询存在性：`GET /repos/{repo}/contents/{path}?ref={branch}` → 200 存在 / 404 不存在
- 提交：`PUT /repos/{repo}/contents/{path}`，body `{message, content(base64), branch}`，Header `Authorization: Bearer <token>`、`X-GitHub-Api-Version: 2022-11-28`
- 文件名冲突：依次尝试 `-1`、`-2` … `-10` 后缀，遇到已存在则继续
- 失败重试：网络错误/5xx 指数退避重试 2 次（1s、3s）；提交冲突（422 sha）视为已存在，换后缀
- 提交成功即触发 Actions 构建（push main），无需额外 webhook

## 6. 图片处理

- 从消息链提取 Image 组件，解析 url：
  1. `http(s)://` → 直接下载
  2. `file://` 或本地路径 → 读本地文件
  3. 平台 file id → 尝试 `event.get_file_url()`（能力探测，无则报错提示）
- 下载用标准库（urllib），不引入额外依赖
- 上传到图床：multipart `file` 字段 + `API_TOKEN` header，响应 `{"code":200,"data":{"url":"..."}}` 校验
- 任一张图上传失败 → 中止整个发布，回复错误，不写 md（绝不产生半成品提交）

## 7. 配置项（`_conf_schema.json`，WebUI 配置面板展示）

| key | 说明 | 默认 |
|---|---|---|
| github_token | fine-grained PAT（Contents 读写，仅限博客仓库） | 空 |
| github_repo | `owner/repo` | tianshihao2003/dumplingandcakeblog |
| github_branch | 目标分支 | main |
| imgbed_upload_url | 图床上传地址 | https://img.tsh520.cn/file |
| imgbed_token | 图床 API_TOKEN | 空 |
| amap_key | 高德 Web 服务 key | 空 |
| author | 动态作者 | 团子和蛋糕 |
| avatar | 动态头像 | /assets/ziyuan/tx.webp |
| moment_tags | 动态默认 tags | ["日常"] |
| place_tags | 足迹默认 tags | ["旅游"] |
| default_note_dir | 笔记默认分类目录 | 日常随笔 |
| allow_users | 允许使用的用户 ID 列表（个人微信为 wxid）；空 = 全部拒绝 | [] |

配置通过 `__init__(self, context, config)` 注入（官方新版配置系统，`get_config()` 已过时）。

## 8. API 依据（官方源码核对结果）

- 消息监听：`@filter.event_message_type(filter.EventMessageType.ALL)`（旧 API `on_decorating_message_type(MessageType.ALL)` 已移除）
- 插件配置：`_conf_schema.json` + `AstrBotConfig`（旧 `get_config()` 已过时）
- 图片：`Image` 组件字段 `url`（远程）/`file`（本地路径）/`path`；个人微信适配器（weixin_oc，>=v4.22）收图自动下载到 `data/temp`
- 回复：`yield event.plain_result(...)`；`AstrMessageEvent` 方法 `message_str`/`get_messages()`/`get_sender_id()` 均已核实
- 网络：httpx（AstrBot 内置 `httpx[socks]>=0.28.1`），官方明确要求不用 requests
- metadata.yaml：`name`（`astrbot_plugin_` 前缀）、`display_name`、`desc`、`version`（`v` 前缀）、`author`、`repo`、`support_platforms`、`astrbot_version`

## 8. 健壮性要求（用户强调「不要有 bug」）

- 纯逻辑（命令解析、md 生成、路径后缀、请求构造）与 AstrBot 粘合层分离，可单测
- 所有外部调用（图床/高德/GitHub）超时控制（默认 15s 连接 + 30s 读取）
- 所有失败路径返回明确中文错误信息，并写插件日志
- 单测覆盖：命令解析、md 生成、后缀冲突、JSON 响应解析
- 平台能力探测（get_file_url 等）使用 hasattr 防御

## 9. 交付

- `E:\GithubProgect\MyRunProject\plug-in\AstrBot\`：
  - `main.py`（AstrBot 粘合层：Star 类、命令路由、会话、外部调用）
  - `blog_writer_core.py`（纯逻辑：解析、md 生成、请求构造）
  - `_conf_schema.json`（配置 Schema，WebUI 自动渲染）
  - `metadata.yaml`（AstrBot 插件元数据）
  - `README.md`（安装、配置、使用说明）
  - `tests/test_core.py`（核心逻辑单测）、`tests/test_smoke.py`（stub astrbot 的集成冒烟测试）
- 安装：zip 打包上传 AstrBot 后台，或放 GitHub 仓库一键安装
