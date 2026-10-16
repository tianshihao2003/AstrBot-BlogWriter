# -*- coding: utf-8 -*-
"""
AstrBot BlogWriter 插件
通过微信对话更新博客的动态（moments）、笔记（notebooks）、足迹（places）。

用法：
  /动态 今天去了公园            # 创建动态会话，可继续发图片/GIF/视频
  /笔记 日常随笔 标题            # 创建笔记会话，正文由后续文本消息追加
  /足迹 陕西 华阴市华山 去找宝宝了  # 创建足迹会话（坐标由高德地理编码获取）
  /相册 情侣头像                # 创建相册会话，图片传到 blog/album/<相册名>
  /发布                      # 结束会话：上传图床 → 生成 md → 提交 GitHub
  /取消                      # 丢弃当前会话
  /状态                      # 查看会话状态

API 依据官方文档（https://docs.astrbot.app/dev/star/）：
- 消息监听：@filter.event_message_type(filter.EventMessageType.ALL)
- 插件配置：_conf_schema.json + __init__(self, context, config)
- 网络请求：httpx（AstrBot 内置依赖）
"""

import asyncio
import base64
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

SHANGHAI_TZ = timezone(timedelta(hours=8))


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI_TZ).replace(tzinfo=None)


def now_shanghai_tz() -> datetime:
    return datetime.now(SHANGHAI_TZ)


from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.message_components import Image, Plain

try:
    from astrbot.api.message_components import Video
except ImportError:  # 兼容极老版本（无 Video 组件）
    Video = None
from astrbot.api.star import Context, Star, register

try:
    from .blog_writer_core import (
        COMMANDS,
        build_album_md,
        build_amap_url,
        build_bangumi_md,
        build_article_md,
        extract_wx_article,
        extract_wx_url,
        assemble_article_body,
        build_daohang_md,
        build_tmdb_search_url,
        build_xxapi_ico_url,
        build_github_put_body,
        build_imgbed_upload,
        build_moment_md,
        build_note_md,
        build_notebook_index_json,
        build_place_md,
        build_upload_url,
        build_friend_md,
        clean_filename_part,
        extract_tags,
        github_base,
        github_put_url,
        moment_filename,
        next_friend_index,
        note_filename,
        parse_amap_response,
        parse_album,
        parse_album_frontmatter,
        parse_media_score,
        parse_media_fields,
        parse_tmdb_search_response,
        parse_xxapi_ico_response,
        parse_daohang_text,
        parse_dynamic,
        parse_friend_text,
        parse_github_put_response,
        parse_imgbed_response,
        parse_message,
        parse_note,
        parse_place,
        place_filename,
        daohang_slug,
        site_host,
        tmdb_poster_url,
        upload_base_host,
        validate_friend_data,
        with_suffix,
        Session,
        format_choices,
        parse_choice,
        is_wizard_cancel,
        is_wizard_skip,
        MEDIA_WIZARD_CATEGORIES,
    )
except ImportError:  # 兼容非包形式加载
    from blog_writer_core import (
        COMMANDS,
        build_album_md,
        build_amap_url,
        build_bangumi_md,
        build_article_md,
        extract_wx_article,
        extract_wx_url,
        assemble_article_body,
        build_daohang_md,
        build_tmdb_search_url,
        build_xxapi_ico_url,
        build_github_put_body,
        build_imgbed_upload,
        build_moment_md,
        build_note_md,
        build_notebook_index_json,
        build_place_md,
        build_upload_url,
        build_friend_md,
        clean_filename_part,
        extract_tags,
        github_base,
        github_put_url,
        moment_filename,
        next_friend_index,
        note_filename,
        parse_amap_response,
        parse_album,
        parse_album_frontmatter,
        parse_media_score,
        parse_media_fields,
        parse_tmdb_search_response,
        parse_xxapi_ico_response,
        parse_daohang_text,
        parse_dynamic,
        parse_friend_text,
        parse_github_put_response,
        parse_imgbed_response,
        parse_message,
        parse_note,
        parse_place,
        place_filename,
        daohang_slug,
        site_host,
        tmdb_poster_url,
        upload_base_host,
        validate_friend_data,
        with_suffix,
        Session,
        format_choices,
        parse_choice,
        is_wizard_cancel,
        is_wizard_skip,
        MEDIA_WIZARD_CATEGORIES,
    )

CONNECT_TIMEOUT = 15
READ_TIMEOUT = 30
MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB
MAX_VIDEO_SIZE = 100 * 1024 * 1024  # 100MB（微信视频常超 20MB）
RETRY_COUNT = 2
RETRY_DELAYS = (1.0, 3.0)


@register(
    "blog_writer",
    "tianshihao2003",
    "通过微信/QQ对话更新博客的动态、笔记、足迹、相册、友链、影视、书籍、导航（交互式向导）",
    "v1.0.0",
)
class BlogWriter(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self._sessions: Dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._client = None
        self._hint_at: Dict[str, float] = {}  # 非白名单 ID 提示的冷却时间戳（monotonic）

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
                follow_redirects=True,
                headers={"User-Agent": "AstrBot-BlogWriter/1.0"},
            )
        return self._client

    # ---------- 配置 ----------

    def _cfg(self, key: str, default=None):
        try:
            value = self.config.get(key)
            return value if value is not None else default
        except Exception:
            return default

    def _amap_key(self) -> str:
        return (self._cfg("amap_key") or "").strip() or os.environ.get("AMAP_KEY", "").strip()

    def _allowed(self, user_id: str) -> bool:
        allow = self._cfg("allow_users") or []
        if not allow:
            return False
        return user_id in [str(x).strip() for x in allow]

    # ---------- 平台兼容（2026-09-30 v1.0.57：企业微信 / QQ 官方机器人） ----------
    # 背景：不同平台的 sender_id 完全不同（NapCat=QQ 号、微信=wxid、QQ 官方=openid、
    # 企业微信=企业成员 UserID / 客服 external_userid），而 allow_users 是全局一份，
    # 所以新平台接入时要么把它的 ID 加进 allow_users，要么用 AstrBot 全局 admins_id。

    # 适配器类型名 → 人类可读平台名（提示文案与日志用）
    _PLATFORM_LABELS = {
        "aiocqhttp": "QQ（NapCat）",
        "qq_official": "QQ 官方机器人",
        "qq_official_webhook": "QQ 官方机器人（Webhook）",
        "wecom": "企业微信（含微信客服）",
        "wecom_ai_bot": "企业微信智能机器人",
        "weixin_oc": "微信",
        "weixin_official_account": "微信公众号",
    }
    # 非白名单 ID 提示的冷却（秒）：同一用户 5 分钟只提示一次，避免刷屏
    _HINT_COOLDOWN = 300

    @staticmethod
    def _platform_name(event: AstrMessageEvent) -> str:
        """事件所属平台的适配器类型名（aiocqhttp / qq_official / wecom / weixin_oc ...）。

        优先用官方 API get_platform_name()；老版本框架没有该方法时回退解析
        unified_msg_origin 的 platform 段（格式 platform_id:message_type:session_id）。
        """
        try:
            getter = getattr(event, "get_platform_name", None)
            if callable(getter):
                name = str(getter() or "").strip()
                if name:
                    return name
        except Exception:
            pass
        try:
            origin = str(getattr(event, "unified_msg_origin", "") or "")
            if ":" in origin:
                return origin.split(":", 1)[0].strip()
        except Exception:
            pass
        return ""

    def _platform_label(self, event: AstrMessageEvent) -> str:
        """平台中文名，用在给用户看的提示里（未登记的平台名原样返回）。"""
        name = self._platform_name(event)
        return self._PLATFORM_LABELS.get(name, name or "未知平台")

    @staticmethod
    def _is_admin_event(event: AstrMessageEvent) -> bool:
        """发送者是否是 AstrBot 全局 admins_id 里的管理员。

        框架在唤醒检查阶段按全局配置 admins_id 打 role=admin（event.is_admin()），
        这里只读不写；拿不到（老版本框架/测试桩）返回 False。
        """
        try:
            checker = getattr(event, "is_admin", None)
            if callable(checker):
                return bool(checker())
        except Exception:
            pass
        return False

    def _unauthorized_hint_text(self, event: AstrMessageEvent, user_id: str, cmd: Optional[str]) -> str:
        """非白名单用户发命令时的一次性「ID 提示」（跨平台加白名单用）。

        只在「开关开启 + 明确命令 + 私聊 + 冷却已过」时返回文本；群聊与非命令返回 ""，
        保持既有「非白名单静默放行、不抢其他插件/AI」的语义。
        """
        if not self._cfg("unauthorized_hint", True):
            return ""
        if cmd not in COMMANDS:
            return ""
        if self._is_group_message(event):
            return ""
        now_mono = time.monotonic()
        if now_mono - self._hint_at.get(user_id, 0.0) < self._HINT_COOLDOWN:
            return ""
        self._hint_at[user_id] = now_mono
        if len(self._hint_at) > 500:  # 防内存膨胀：只留最近有活动的 200 条
            keep = sorted(self._hint_at.items(), key=lambda kv: kv[1], reverse=True)[:200]
            self._hint_at = dict(keep)
        return (
            "⚠ BlogWriter：你还不在白名单里（平台：{}）。\n"
            "把这个 ID 加进插件配置 allow_users 就能用了：\n"
            "{}\n"
            "（加进 AstrBot 全局配置的 admins_id 同样生效）".format(
                self._platform_label(event), user_id
            )
        )

    @staticmethod
    def _is_group_message(event: AstrMessageEvent) -> bool:
        """判断消息是否来自群聊。

        优先解析 unified_msg_origin 类型段（如 aiocqhttp:GroupMessage:123456），
        其次探测 get_group_id()。识别不出时按私聊处理（宁可提示也不丢消息）。
        """
        try:
            origin = str(getattr(event, "unified_msg_origin", "") or "")
            if "GroupMessage" in origin:
                return True
            if "FriendMessage" in origin or "PrivateMessage" in origin:
                return False
            getter = getattr(event, "get_group_id", None)
            if callable(getter):
                if str(getter() or "").strip():
                    return True
        except Exception:
            pass
        return False

    def _group_allowed(self, event: AstrMessageEvent) -> bool:
        """群聊白名单：allow_groups 为空 → 不限制（保持既有行为）；
        非空 → 仅列表内的群可交互，其余群完全静默（含命令）。"""
        allow = self._cfg("allow_groups") or []
        if not allow:
            return True
        allowed = [str(x).strip() for x in allow if str(x).strip()]
        gid = ""
        try:
            getter = getattr(event, "get_group_id", None)
            if callable(getter):
                gid = str(getter() or "").strip()
        except Exception:
            gid = ""
        if not gid:
            origin = str(getattr(event, "unified_msg_origin", "") or "")
            parts = origin.split(":")
            if len(parts) >= 3 and "GroupMessage" in origin:
                gid = parts[-1].strip()
        return bool(gid) and gid in allowed

    @staticmethod
    def _merge_tags(default_tags, extra) -> List[str]:
        """默认标签 + 自定义标签，保持顺序并去重。"""
        result = [str(t).strip() for t in (default_tags or []) if str(t).strip()]
        seen = set(result)
        for t in (extra or []):
            t = str(t).strip()
            if t and t not in seen:
                seen.add(t)
                result.append(t)
        return result

    def _sweep(self) -> None:
        now = now_shanghai()
        expired = [uid for uid, s in self._sessions.items() if s.expired(now)]
        for uid in expired:
            del self._sessions[uid]

    # ---------- 向导辅助 ----------

    async def _list_notebook_names(self, repo: str, branch: str, token: str) -> Optional[List[str]]:
        """列出 src/content/笔记本 下的一级目录名（笔记本名）。

        返回排序后的目录名列表；**请求失败返回 None**（区别于空仓库的 []，
        发布笔记时依赖该区分决定是否补建 _index.json）。"""
        url = "https://api.github.com/repos/{}/contents/src/content/笔记本?ref={}".format(
            repo, urllib.parse.quote(branch)
        )
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            resp = await self._get_client().get(url, headers=headers)
            if resp.status_code != 200:
                return None
            import json as _json
            items = _json.loads(resp.text)
            names = []
            for it in items:
                if isinstance(it, dict) and it.get("type") == "dir":
                    n = str(it.get("name") or "").strip()
                    if n and not n.startswith("."):
                        names.append(n)
            return sorted(names)
        except Exception as e:
            logger.warning("BlogWriter: 列笔记本失败: %s", e)
            return None

    async def _ensure_notebook_index(self, note_dir: str, repo: str, branch: str, token: str) -> Tuple[bool, str]:
        """笔记本不存在时补建 _index.json（博客书架/归档页靠它识别，缺了文件夹不可见）。

        返回 (是否新建了索引, 错误信息)。目录列表拉取失败时不动作（返回 False, ""），
        宁可沿用旧行为也不误判已有笔记本。"""
        names = await self._list_notebook_names(repo, branch, token)
        if names is None or note_dir in names:
            return False, ""
        idx_path = "src/content/笔记本/{}/_index.json".format(note_dir)
        exists = await self._github_exists(repo, idx_path, branch, token)
        if exists is not False:  # True（已存在）或 None（查询失败）都不再建
            return False, ""
        idx_md = build_notebook_index_json(note_dir)
        ok, err = await self._github_put(repo, idx_path, idx_md, token, branch)
        if not ok:
            return False, err or "GitHub 提交失败"
        logger.info("BlogWriter: 已补建笔记本索引 %s", idx_path)
        return True, ""

    # ---------- 会话创建 ----------

    async def _list_daohang_folders(self, repo: str, branch: str, token: str) -> Optional[List[str]]:
        """列出 src/content/导航 下的一级分类文件夹名。

        返回排序后的文件夹名列表；请求失败返回 None（区别于空目录的 []）。
        """
        url = "https://api.github.com/repos/{}/contents/src/content/导航?ref={}".format(
            repo, urllib.parse.quote(branch)
        )
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            resp = await self._get_client().get(url, headers=headers)
            if resp.status_code != 200:
                return None
            import json as _json
            items = _json.loads(resp.text)
            names = []
            for it in items:
                if isinstance(it, dict) and it.get("type") == "dir":
                    n = str(it.get("name") or "").strip()
                    if n and not n.startswith("."):
                        names.append(n)
            return sorted(names)
        except Exception as e:
            logger.warning("BlogWriter: 列导航分类失败: %s", e)
            return None

    async def _start_daohang(self, event: AstrMessageEvent, user_id: str, args: List[str]):
        """/导航 网址 —— xxapi 取网站图标，向导选分类文件夹（2026-09-21 起分类=文件夹），
        键值对补名称/描述、#标签 补标签；/发布 上传图标到 blog/daohang 并提交 src/content/导航/<分类>/。"""
        url = " ".join(args).strip()
        if not url:
            return event.plain_result("格式：/导航 网址（如：/导航 https://example.com），图标自动获取。")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        host = site_host(url)
        if "." not in host:
            return event.plain_result("网址无效，请检查（如：/导航 https://example.com）。")
        # xxapi 取图标（博客 scripts/添加导航 同款 API，无需 key）
        icon_url = ""
        try:
            resp = await self._get_client().get(build_xxapi_ico_url(url))
            icon_url, _err = parse_xxapi_ico_response(resp.status_code, resp.text)
        except Exception as e:
            logger.warning("BlogWriter: 图标接口请求失败: %s", e)
        icon_bytes = None
        if icon_url:
            icon_bytes = await self._download_http(icon_url)
        session = Session("daohang", {
            "url": url,
            "name": host,  # 默认名称=域名，可键值对改
            "category": "",  # 分类文件夹，待向导选择
            "description": "",
        })
        if icon_bytes:
            ext = (icon_url.rsplit(".", 1)[-1].split("?")[0].lower() or "png")
            if ext not in ("jpg", "jpeg", "png", "webp", "gif", "ico", "svg", "bmp"):
                ext = "png"
            session.add_image("{}-icon.{}".format(host, ext), icon_bytes)  # 对齐现有图床命名 blog.tsh520.com-icon.webp
            icon_hint = "图标已就绪"
        else:
            icon_hint = "⚠ 图标获取失败（将无图标发布，不影响其他字段）"
        self._sessions[user_id] = session
        # 向导：选分类文件夹（博客导航分类=文件夹，如 学习网站/AI 工具/未分类）
        repo = self._cfg("github_repo") or "tianshihao2003/dumplingandcakeblog"
        branch = self._cfg("github_branch") or "main"
        token = (self._cfg("github_token") or "").strip()
        folders = await self._list_daohang_folders(repo, branch, token)
        if not folders:
            # 列表拉取失败/为空：用配置默认分类，直接进入键值对
            session.meta["category"] = clean_filename_part(
                self._cfg("daohang_default_category", "未分类") or "未分类", fallback="未分类"
            )
            return event.plain_result(
                "{}：{}\n\n"
                "分类：{}（未能读取分类列表，可在配置 daohang_default_category 调整）\n"
                "可继续发（键值对，可多行）：名称: xxx / 描述: xxx / #标签\n"
                "发 /发布 提交，/取消 放弃。".format(icon_hint, host, session.meta["category"])
            )
        session.wizard = {"step": "daohang_pick_category", "folders": folders}
        return event.plain_result(
            "{}：{}\n\n".format(icon_hint, host)
            + format_choices("请选择分类（文件夹）：", folders, extra=["新建分类"], with_skip_cancel=False)
            + "\n也可直接回复分类名（如：学习网站）"
        )

    def _start_book(self, event: AstrMessageEvent, user_id: str, args: List[str]):
        """/书籍 书名 —— 封面用户自己发（无 API），评分/标签/书评同影视。"""
        name = " ".join(args).strip()
        if not name:
            return event.plain_result("格式：/书籍 书名（如：/书籍 认知觉醒），随后发封面图。")
        session = Session("book", {"title": name, "score": None, "tags": []})
        self._sessions[user_id] = session
        return event.plain_result(
            "《{}》书籍会话已创建。请直接发一张封面图（必须），\n"
            "然后可发：评分 8、#标签、书评文字（多条自动拼接），\n"
            "发 /发布 提交，/取消 放弃。".format(name)
        )

    def _media_type_prompt(self) -> str:
        items = [f"{label}（{val}）" for label, val in MEDIA_WIZARD_CATEGORIES]
        return format_choices("请选择类型：", items, with_skip_cancel=False)

    async def _start_media(self, event: AstrMessageEvent, user_id: str, args: List[str]):
        """/影视 片名 —— TMDB 搜索取中文片名+封面（字节立即下载入会话，/发布 时上传图床独立目录）。"""
        name = " ".join(args).strip()
        if not name:
            return event.plain_result("格式：/影视 片名（如：/影视 侏罗纪世界），封面自动从 TMDB 获取。")
        api_key = (self._cfg("tmdb_api_key") or "").strip()
        if not api_key:
            return event.plain_result(
                "未配置 tmdb_api_key。请到 themoviedb.org 免费注册 → 设置 → API 申请 v3 Key，填到插件配置。"
            )
        url = build_tmdb_search_url(name, api_key, self._cfg("tmdb_api_base"))
        try:
            resp = await self._get_client().get(url)
        except Exception as e:
            logger.warning("BlogWriter: TMDB 请求失败: %s", e)
            return event.plain_result("TMDB 请求失败（网络不通？可在配置 tmdb_api_base 填反代地址）。")
        info, err = parse_tmdb_search_response(resp.status_code, resp.text)
        if info is None:
            # TMDB 搜不到 → 手动模式：等用户发封面图，类型用向导选
            session = Session("media", {"title": name, "subcategory": "", "score": None, "tags": [], "manual": True})
            session.wizard = {"step": "media_need_cover"}
            self._sessions[user_id] = session
            return event.plain_result(
                "TMDB 未找到「{}」，已转手动模式：\n"
                "请直接发一张封面图（必须）。\n发图后我会让你选类型/评分/标签。".format(name)
            )
        # 下载封面字节（立即，避免发布时再等网络）
        poster_bytes = None
        if info["poster_path"]:
            poster_url = tmdb_poster_url(info["poster_path"], self._cfg("tmdb_image_base"))
            poster_bytes = await self._download_http(poster_url)
        ext = (info["poster_path"].rsplit(".", 1)[-1].lower() if "." in info["poster_path"] else "jpg")
        if ext not in ("jpg", "jpeg", "png", "webp"):
            ext = "jpg"
        filename = "{}.{}".format(clean_filename_part(info["title"]), ext)
        session = Session("media", {
            "title": info["title"],
            "subcategory": "movie" if info["media_type"] == "movie" else "tv",
            "score": None,
            "tags": [],
            "year": info["year"],
        })
        if poster_bytes:
            session.add_image(filename, poster_bytes)
        else:
            # 封面下载失败（无海报/CDN 不通）→ 手动模式，发图后走类型向导
            session.meta["manual"] = True
            session.wizard = {"step": "media_need_cover"}
            self._sessions[user_id] = session
            return event.plain_result(
                "已找到《{}》（{}）但封面下载失败，请直接发一张封面图。\n发图后我会让你选类型/评分/标签。".format(info["title"], info["year"] or "未知年份")
            )
        self._sessions[user_id] = session
        vote = info.get("vote_average")
        vote_hint = " TMDB评分{}".format(vote) if isinstance(vote, (int, float)) and vote else ""
        sub_label = "电影" if info["media_type"] == "movie" else "剧集"
        return event.plain_result(
            "已找到《{}》（{} {}）{}，封面已就绪。\n\n"
            "接下来可发：\n"
            "评分 8　→ 打分（0-10）\n"
            "#科幻 #冒险　→ 标签\n"
            "直接发文字　→ 一句话影评\n"
            "发 /发布 提交，/取消 放弃。".format(info["title"], info["year"] or "未知年份", sub_label, vote_hint)
        )

    # ---------- 消息入口 ----------

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id() or "")
        if not user_id:
            return

        async with self._lock:
            self._sweep()
            cmd, args, raw = parse_message(event.message_str)
            session = self._session(user_id)
            logger.info(
                "BlogWriter: 收到消息 platform=%s user=%s cmd=%s has_session=%s msg_str=%r",
                self._platform_name(event) or "?",
                user_id, cmd, session is not None, (event.message_str or "")[:60],
            )

            # 白名单：allow_users 列表，或 AstrBot 全局 admins_id 里的管理员（跨平台通用）。
            # 非白名单用户：私聊发明确命令时回一条含其 ID 的提示（便于加白名单，可关），
            # 其余情况（群聊 / 非命令）一律静默忽略，不回复、不抢其他插件/AI 的消息处理
            if not self._allowed(user_id) and not self._is_admin_event(event):
                hint = self._unauthorized_hint_text(event, user_id, cmd)
                if hint:
                    logger.info(
                        "BlogWriter: 非白名单用户发命令 %s（platform=%s id=%s），已回复 ID 提示",
                        cmd, self._platform_name(event) or "?", user_id,
                    )
                    # 本条消息已被插件完整消费，阻断传播（否则会落到框架 LLM 阶段报错）
                    _stop = getattr(event, "stop_event", None)
                    if callable(_stop):
                        _stop()
                    yield event.plain_result(hint)
                    return
                _name_getter = getattr(event, "get_sender_name", None)
                logger.info(
                    "BlogWriter: 用户 %s（id=%s，platform=%s）无权限，已忽略。"
                    "如需使用，请把该 ID 加入插件 allow_users 或 AstrBot 全局 admins_id。",
                    (_name_getter() if callable(_name_getter) else "") or "?",
                    user_id,
                    self._platform_name(event) or "?",
                )
                return

            # 群聊白名单：配置了 allow_groups 后，列表外的群完全静默（含命令）
            if self._is_group_message(event) and not self._group_allowed(event):
                logger.info("BlogWriter: 群消息不在 allow_groups 白名单，已忽略 user=%s origin=%s",
                            user_id, getattr(event, "unified_msg_origin", ""))
                return

            # 与博客无关的消息（非命令、无进行中会话）一律放行，绝不回复
            if cmd not in COMMANDS and not session:
                # 例外：白名单用户**私聊**无会话时收到图片/视频 → 提示，避免媒体被静默丢弃；
                # 群聊里一律不插嘴（2026-09-15：群里随手发图被提示刷屏）
                if not self._is_group_message(event) and self._extract_images(event, allow_video=True):
                    logger.info("BlogWriter: 用户 %s 私聊无会话发送媒体，已提示", user_id)
                    # 本条消息已被插件完整消费，阻断传播（否则 LLM 阶段还会再收到）
                    _stop = getattr(event, "stop_event", None)
                    if callable(_stop):
                        _stop()
                    yield event.plain_result(
                        "当前没有进行中的会话，图片/视频未接收。请先发 /动态、/笔记、/足迹、/相册 等命令。"
                    )
                    return
                return

            # 命令或已有会话的消息由本插件完整消费：按官方规范 stop_event 阻断传播，
            # 否则 yield 结果后事件仍会进入 LLM 阶段（/ 前缀或 @ 唤醒时 LLM 会再答一次）
            _stop = getattr(event, "stop_event", None)
            if callable(_stop):
                _stop()

            if not self._cfg("github_token"):
                yield event.plain_result("插件未配置 GitHub Token，请在插件设置中填写后再使用。")
                return

            if cmd == "动态":
                yield self._start_moment(event, user_id, args)
                return
            if cmd == "笔记":
                yield await self._start_note(event, user_id, args)
                return
            if cmd == "足迹":
                yield self._start_place(event, user_id, args)
                return
            if cmd == "友链":
                yield self._start_friend(event, user_id, raw)
                return
            if cmd == "相册":
                yield await self._start_album(event, user_id, args)
                return
            if cmd == "影视":
                yield await self._start_media(event, user_id, args)
                return
            if cmd == "书籍":
                yield self._start_book(event, user_id, args)
                return
            if cmd == "导航":
                yield await self._start_daohang(event, user_id, args)
                return
            if cmd == "转载":
                yield await self._handle_article(event, user_id, args)
                return
            if cmd == "发布":
                yield await self._publish(event, user_id)
                return
            if cmd == "取消":
                self._sessions.pop(user_id, None)
                yield event.plain_result("已取消当前会话。")
                return
            if cmd == "状态":
                if session:
                    yield event.plain_result(
                        "当前会话：{}（文本 {} 条、图片 {} 张）。".format(
                            session.kind, len(session.text_parts), len(session.images)
                        )
                    )
                else:
                    yield event.plain_result("当前没有进行中的会话。")
                return
            if cmd == "帮助":
                yield event.plain_result(self._help_text())
                return

            # 非命令消息：归入会话
            if session:
                if session.expired():
                    self._sessions.pop(user_id, None)
                    yield event.plain_result("上一个会话已超时作废，请重新发指令。")
                    return
                # 影视/书籍：先一次性探测封面图（避免向导拦截图片、避免重复提取日志）
                allow_video = session.kind == "moment"  # 视频仅动态支持；笔记/足迹/相册仍只收图片
                cover_payload = None
                if session.kind in ("media", "book"):
                    cover_refs = self._extract_images(event, allow_video=False)
                    if cover_refs:
                        cover_data = await self._read_image_bytes(cover_refs[-1])
                        if cover_data is not None:
                            cover_payload = (cover_refs[-1], cover_data)
                    else:
                        # 兜底：微信 raw_message 加密媒体 curl 下载（下载的字节直接作封面）
                        for enc, aes_hex, aes_b64, _kind in self._extract_wx_raw_media(event, allow_video=False):
                            dl = await self._download_wx_media(enc, aes_hex, aes_b64)
                            if dl is not None:
                                cover_payload = ("wxraw_{}.jpg".format(enc[:16]), dl)
                                break
                if cover_payload is not None:
                    ref, data = cover_payload
                    filename = ref.rsplit("/", 1)[-1].split("?")[0].split("#")[0] or "cover.jpg"
                    if "." not in filename:
                        filename += ".jpg"
                    base_title = session.meta.get("title") or "cover"
                    filename = "{}{}".format(
                        clean_filename_part(base_title),
                        filename[filename.rfind("."):] if "." in filename else ".jpg",
                    )
                    session.images.clear()
                    session.add_image(filename, data)
                    session.touch()
                    # 手动模式下发图后进入类型向导
                    if getattr(session, "wizard", None) and session.wizard.get("step") == "media_need_cover":
                        session.wizard = {"step": "media_pick_category"}
                        yield event.plain_result(
                            "封面已更新。\n" + self._media_type_prompt()
                        )
                        return
                    yield event.plain_result(
                        "封面已更新（发布时上传图床）。继续发评分/#标签/影评，或 /发布 提交。"
                    )
                    return
                # 无图：向导分发（如有）
                if getattr(session, "wizard", None):
                    res = await self._handle_wizard(event, user_id, raw)
                    if res is not None:
                        yield res
                        return
                images = self._extract_images(event, allow_video=allow_video)
                if images:
                    logger.info("BlogWriter: 提取到 %d 张图片引用: %s", len(images), images[:3])
                    stored = []
                    for ref in images:
                        # 立即读取字节：data/temp 临时文件在消息处理完后会被清理
                        data = await self._read_image_bytes(ref)
                        if data is None:
                            logger.warning("BlogWriter: 图片读取失败: %s", ref)
                            yield event.plain_result(
                                "图片读取失败（{}），请重发。".format(ref[:60])
                            )
                            return
                        stored.append((ref, data))
                else:
                    # 兜底：适配器下载媒体失败（如微信 CDN TLS 握手被拒）时，
                    # 从 raw_message 提取加密参数，用 curl 下载 + AES 解密。
                    raw_media = self._extract_wx_raw_media(event, allow_video=allow_video)
                    if raw_media:
                        logger.info("BlogWriter: 尝试 curl 兜底下载 %d 张微信媒体", len(raw_media))
                        stored = []
                        for enc, aes_hex, aes_b64, kind in raw_media:
                            data = await self._download_wx_media(enc, aes_hex, aes_b64)
                            if data is None:
                                yield event.plain_result(
                                    "微信媒体下载失败（CDN 握手异常且 curl 兜底失败），请重发。"
                                )
                                return
                            suffix = ".mp4" if kind == "video" else ""
                            stored.append(("wxraw_{}{}".format(enc[:16], suffix), data))
                    else:
                        stored = []
                if stored:
                    session.touch()
                    for ref, data in stored:
                        session.add_image(ref, data)
                    logger.info("BlogWriter: 图片已入会话，共 %d 张", len(session.images))
                    yield event.plain_result(
                        "已收到 {} 张图片（共 {} 张）。发 /发布 结束，发 /取消 放弃。".format(
                            len(stored), len(session.images)
                        )
                    )
                    return
                logger.info("BlogWriter: 消息链中未发现图片组件（共 %d 个组件）", len(event.get_messages()))
                text = raw.strip()
                if text:
                    session.touch()
                    if session.kind == "friend":
                        data = parse_friend_text(text)
                        if data:
                            session.meta.update(data)
                            summary = "，".join(
                                "{}：{}".format(k, v[:40]) for k, v in data.items()
                            )
                            yield event.plain_result(
                                "已识别：{}\n\n发 /发布 提交，发 /取消 放弃。".format(summary)
                            )
                        else:
                            yield event.plain_result(
                                "未能识别有效字段，请按「站点名称: xxx」这样的格式发送。"
                            )
                    elif session.kind == "album":
                        # 相册会话只收图片（博客相册由图床目录动态加载，md 只需标题/日期）
                        yield event.plain_result(
                            "相册只接收图片。请直接发图片，或发 /发布 提交、/取消 放弃。"
                        )
                    elif session.kind == "daohang":
                        # 导航会话：键值对（名称/描述；分类=文件夹，发「分类: xxx」可改），#标签 追加标签
                        # 先摘 #标签 再解析键值对，避免标签粘在描述里（tags 2026-09-21 起回归为可选字段）
                        kv_text, tags = extract_tags(text)
                        kv = parse_daohang_text(kv_text)
                        if kv:
                            for k, v in kv.items():
                                if k == "category":
                                    # 分类改为文件夹名：清洗后写入（对齐博客 2026-09-21 文件夹分类）
                                    v = clean_filename_part(v, fallback="")
                                    if not v:
                                        continue
                                session.meta[k] = v
                        if tags:
                            existing = session.meta.setdefault("tags", [])
                            for t in tags:
                                if t not in existing:
                                    existing.append(t)
                        session.touch()
                        summary = "，".join(
                            "{}：{}".format(label, str(session.meta.get(key))[:30])
                            for key, label in (("name", "名称"), ("category", "分类"), ("description", "描述"))
                            if session.meta.get(key)
                        )
                        tag_hint = (
                            "，标签：{}".format(" ".join("#" + t for t in session.meta.get("tags", [])))
                            if session.meta.get("tags")
                            else ""
                        )
                        yield event.plain_result(
                            "已记录：{}{}。发 /发布 提交，发 /取消 放弃。".format(summary, tag_hint)
                        )
                        return
                    elif session.kind in ("media", "book"):
                        # 影视/书籍会话：键值对（名称/类型）→ 评分行 → #标签 → 其余为正文
                        fields = parse_media_fields(text)
                        if fields:
                            if fields.get("title"):
                                session.meta["title"] = fields["title"]
                            if fields.get("subcategory"):
                                session.meta["subcategory"] = fields["subcategory"]
                            session.touch()
                            yield event.plain_result(
                                "已修改：{}。发 /发布 提交，发 /取消 放弃。".format(
                                    "，".join("{}={}".format(k, v) for k, v in fields.items())
                                )
                            )
                            return
                        score = parse_media_score(text)
                        if score is not None:
                            session.meta["score"] = score
                            session.touch()
                            yield event.plain_result("已记录评分：{}。发 /发布 提交，发 /取消 放弃。".format(score))
                            return
                        clean, tags = extract_tags(text)
                        if tags:
                            existing = session.meta.setdefault("tags", [])
                            for t in tags:
                                if t not in existing:
                                    existing.append(t)
                        if clean:
                            session.add_text(clean)
                        session.touch()
                        tag_hint = "，标签：{}".format(" ".join("#" + t for t in session.meta.get("tags", []))) if session.meta.get("tags") else ""
                        score_hint = "，评分：{}".format(session.meta.get("score")) if session.meta.get("score") is not None else ""
                        yield event.plain_result("已记录{}{}。发 /发布 提交，发 /取消 放弃。".format(score_hint, tag_hint))
                        return
                    else:
                        session.add_text(text)
                        yield event.plain_result("内容已追加。发 /发布 提交，发 /取消 放弃。")
                    return
            # 其他消息一律放行（不 yield 结果即放行）
            return

    # ---------- 会话创建 ----------

    def _session(self, user_id: str) -> Optional[Session]:
        return self._sessions.get(user_id)

    def _start_moment(self, event: AstrMessageEvent, user_id: str, args: List[str]):
        content, tags = parse_dynamic(args)
        if not content:
            return event.plain_result("格式：/动态 内容 [#标签]（例如：/动态 今天去了公园 #日常）")
        self._sessions[user_id] = Session("moment", {"content": content, "tags": tags})
        tag_hint = "，标签：{}".format(" ".join("#" + t for t in tags)) if tags else ""
        return event.plain_result(
            "动态已创建：{}{}\n\n可以直接发图片、动图 GIF 或视频（可多发），发完说 /发布。".format(content, tag_hint)
        )

    async def _start_note(self, event: AstrMessageEvent, user_id: str, args: List[str]):
        default_dir = self._cfg("default_note_dir") or "日常随笔"
        # 带参快车道：/笔记 分类 标题（分类名清洗，防路径注入）
        if args:
            note_dir, title = parse_note(args, default_dir)
            if title:
                note_dir = clean_filename_part(note_dir, fallback=default_dir)
                self._sessions[user_id] = Session("note", {"note_dir": note_dir, "title": title})
                return event.plain_result(
                    "笔记已创建：分类「{}」标题「{}」。\n\n接下来直接发正文（可多条，自动拼接），也可以发图片，发完说 /发布。".format(
                        note_dir, title
                    )
                )
        # 空参进向导：先选笔记本
        repo = self._cfg("github_repo") or "tianshihao2003/dumplingandcakeblog"
        branch = self._cfg("github_branch") or "main"
        token = (self._cfg("github_token") or "").strip()
        names = await self._list_notebook_names(repo, branch, token)
        if not names:
            names = [default_dir]
        # 去重并把默认放首位
        uniq = []
        seen = set()
        for n in [default_dir] + names:
            if n not in seen:
                seen.add(n)
                uniq.append(n)
        sess = Session("note", {"note_dir": "", "title": ""})
        sess.wizard = {"step": "note_pick_notebook", "notebooks": uniq, "default_dir": default_dir}
        self._sessions[user_id] = sess
        return event.plain_result(
            format_choices("请选择笔记本：", uniq, extra=["新建笔记本"], with_skip_cancel=False)
            + "\n\n也可直接回复笔记本名称新建。"
        )

    def _start_friend(self, event: AstrMessageEvent, user_id: str, raw: str):
        """/友链 [键值对文本]。后续文本消息继续追加解析。"""
        rest = re.sub(r"^/?(友链)\s*", "", raw or "", count=1).strip()
        session = Session("friend", {})
        if rest:
            data = parse_friend_text(rest)
            session.meta.update(data)
        self._sessions[user_id] = session
        lines = [
            "友链会话已创建。请发送友链信息（每行一条，格式随意，我会自动识别）：",
            "站点名称: 名称",
            "站点描述: 一句话介绍",
            "站点链接: https://xxx.com",
            "头像链接: https://头像图片地址 或 /相对路径",
            "发 /发布 提交，发 /取消 放弃。",
        ]
        if rest:
            lines.insert(0, "已识别到内容，还可在下一条消息补充或修改。")
        return event.plain_result("\n".join(lines))

    def _start_place(self, event: AstrMessageEvent, user_id: str, args: List[str]):
        province, city, experience, tags = parse_place(args)
        if not province or not city:
            return event.plain_result("格式：/足迹 省 地点 体验 [#标签]（例如：/足迹 陕西 华阴市华山 去找宝宝了 #旅游 #2026）")
        self._sessions[user_id] = Session(
            "place",
            {"province": province, "city": city, "experience": experience, "tags": tags},
        )
        tag_hint = "，标签：{}".format(" ".join("#" + t for t in tags)) if tags else ""
        return event.plain_result(
            "足迹已创建：{} {}{}（坐标将用高德地理编码获取）。\n\n可以直接发照片（可多发），发完说 /发布。".format(
                province, city, tag_hint
            )
        )

    async def _start_album(self, event: AstrMessageEvent, user_id: str, args: List[str]):
        # 带参快车道
        name = parse_album(args)
        if name:
            self._sessions[user_id] = Session("album", {"name": name})
            return event.plain_result(
                "相册「{}」已创建。\n\n请直接发图片（可多发），发完说 /发布。".format(name)
            )
        # 空参进向导：列出现有相册（一次拉取，排序后同时生成展示与映射，保证选项不错位）
        repo = self._cfg("github_repo") or "tianshihao2003/dumplingandcakeblog"
        branch = self._cfg("github_branch") or "main"
        token = (self._cfg("github_token") or "").strip()
        idx = await self._album_index(repo, branch, token)
        if idx is None:
            return event.plain_result("GitHub 查询失败（网络或 Token 问题），请稍后重试或直接发 /相册 相册名 快捷创建。")
        entries = list(idx.get("entries") or [])
        entries.sort(key=lambda e: e[0])  # 按 title 排序
        titles = [t for t, _f, _fd in entries]
        display = ["{}（{}）".format(t, f) for t, f, _fd in entries]
        sess = Session("album", {"name": ""})
        sess.wizard = {"step": "album_pick", "display": display, "titles": titles}
        self._sessions[user_id] = sess
        if not display:
            return event.plain_result("当前还没有相册，直接回复新相册名称即可创建。\n回复“取消”退出。")
        return event.plain_result(
            format_choices("请选择相册：", display, extra=["新建相册"], with_skip_cancel=False)
        )

    _WX_ARTICLE_UA = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.44(0x18002c2d) NetType/WIFI Language/zh_CN"
    )

    @staticmethod
    def _wx_image_ext(url: str, data: bytes) -> str:
        """推断微信文章图片扩展名：wx_fmt 参数 → URL 后缀 → 魔数兜底。"""
        m = re.search(r"[?&]wx_fmt=([a-zA-Z0-9]+)", url or "")
        fmt = (m.group(1).lower() if m else "")
        mapping = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "gif": "gif", "webp": "webp", "bmp": "bmp"}
        if fmt in mapping:
            return mapping[fmt]
        base = (url or "").split("?")[0].rsplit(".", 1)
        if len(base) == 2 and base[1].lower() in mapping:
            return mapping[base[1].lower()]
        if data[:3] == b"\xff\xd8\xff":
            return "jpg"
        if data[:8].startswith(b"\x89PNG"):
            return "png"
        if data[:4] == b"GIF8":
            return "gif"
        if data[:4] == b"RIFF":
            return "webp"
        return "jpg"

    async def _handle_article(self, event: AstrMessageEvent, user_id: str, args: List[str]):
        """/转载 公众号文章链接 —— 抓取 → 解析 → 图片转存图床 → 生成 posts 文章 → 提交（直发，不进会话）。

        实测（2026-09-16 本地验证）：直连抓取正常（HTTP 200，无验证页）；
        微信对部分服务器 IP 会返回「环境异常」验证页 → extract_wx_article 检测后明确报错；
        图片 mmbiz.qpic.cn 无 Referer 可下载，失败再带 Referer 重试（防盗链兜底）。
        原子性：标题解析失败 / 任一张图下载或上传失败 → 中止发布，绝不半成品提交。
        """
        url = extract_wx_url(" ".join(args).strip())
        if not url:
            return event.plain_result("格式：/转载 公众号文章链接（如：/转载 https://mp.weixin.qq.com/s/xxxx）")
        # 1) 抓取（MicroMessenger UA + Referer）
        headers = {
            "User-Agent": self._WX_ARTICLE_UA,
            "Referer": "https://mp.weixin.qq.com/",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        html = ""
        for attempt in range(RETRY_COUNT + 1):
            try:
                resp = await self._get_client().get(url, headers=headers)
                if resp.status_code >= 400:
                    return event.plain_result("转载中止：抓取失败 HTTP {}（请确认链接在微信里可正常打开）。".format(resp.status_code))
                html = resp.text
                break
            except Exception as e:
                logger.warning("BlogWriter: 转载抓取失败(第%d次): %s", attempt + 1, e)
                if attempt < RETRY_COUNT:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
        if not html:
            return event.plain_result("转载中止：网络错误，请稍后重试。")
        data, err = extract_wx_article(html)
        if data is None:
            return event.plain_result("转载中止：{}。".format(err))
        # 2) 图片：下载 → 转存图床（任一失败即中止）
        image_urls: List[str] = []
        if data["image_urls"]:
            stored = []
            for idx, src in enumerate(data["image_urls"]):
                img = await self._download_http(src)
                if img is None:
                    img = await self._download_http(src, referer="https://mp.weixin.qq.com/")
                if img is None:
                    return event.plain_result("转载中止：第 {} 张图片下载失败，请稍后重试。".format(idx + 1))
                stored.append(("article-{}.{}".format(idx + 1, self._wx_image_ext(src, img)), img))
            result = await self._upload_images(stored, self._cfg("article_upload_folder") or "blog/article")
            if isinstance(result, str):
                return event.plain_result("转载中止：图片转存图床失败：{}".format(result))
            image_urls = result
        # 3) 生成 md 并提交
        now = now_shanghai()
        body_md = assemble_article_body(data["body_md"], image_urls)
        md = build_article_md(
            data["title"],
            body_md,
            image_urls,
            self._merge_tags(self._cfg("article_tags", ["转载"]), []),
            description=data["digest"],
            source_link=url,
            author=data["author"],
            publish_date=data["publish_date"],
            now=now,
        )
        article_dir = clean_filename_part(self._cfg("article_default_dir") or "技术分享", fallback="技术分享")
        file_name = clean_filename_part(data["title"], fallback="转载文章")
        path = "src/content/文章/{}/{}.md".format(article_dir, file_name)
        ok, final_path, err2 = await self._commit_md(path, md, now)
        if not ok:
            return event.plain_result("转载提交失败：{}".format(err2))
        slug = final_path[len("src/content/文章/"):-len(".md")]
        return event.plain_result(
            "转载成功 ✅\n\n标题：{}\n公众号：{}\n文件：{}\n博客：https://blog.tsh520.cn/posts/{}".format(
                data["title"], data["author"] or "未知", final_path, slug)
        )

    # ---------- 发布 ----------

    async def _publish(self, event: AstrMessageEvent, user_id: str) -> MessageEventResult:
        session = self._session(user_id)
        if not session:
            return event.plain_result("当前没有进行中的会话，请先发 /动态、/笔记、/足迹、/友链、/相册、/影视、/书籍 或 /导航。")
        repo = self._cfg("github_repo") or "tianshihao2003/dumplingandcakeblog"
        branch = self._cfg("github_branch") or "main"
        token = (self._cfg("github_token") or "").strip()
        album_folder = None
        album_exists = False
        if session.kind == "moment":
            content = ((session.meta.get("content") or "") + "\n\n" + session.full_text()).strip()
            if not content:
                return event.plain_result("动态内容为空，无法发布。")
        elif session.kind == "note" and not session.full_text():
            return event.plain_result("笔记正文为空，无法发布。")
        elif session.kind == "friend":
            ok, err = validate_friend_data(session.meta)
            if not ok:
                return event.plain_result("友链信息不完整：{}。可继续发送补充（格式：站点名称: xxx），或发 /取消。".format(err))
        elif session.kind == "album":
            album_name = (session.meta.get("name") or "").strip()
            if not album_name:
                return event.plain_result("相册名为空，无法发布。")
            if not session.images:
                return event.plain_result("相册至少需要一张图片，无法发布。")
            # 按 title/文件名判断相册是否已存在：博客相册文件名与 title 不一定相同
            # （如 xiangce1.md 的 title 是「测试相册」），必须读文件内容才能对齐。
            album_index = await self._album_index(repo, branch, token)
            if album_index is None:
                return event.plain_result("GitHub 查询失败（网络或 Token 问题），已中止发布。")
            fname = clean_filename_part(album_name)
            default_folder = "{}/{}".format(
                (self._cfg("album_folder_prefix") or "blog/album").strip().strip("/"),
                fname,
            )
            matched_folder = album_index["titles"].get(album_name)
            if matched_folder is not None:
                # title 命中已有相册：追加到它自己的 imgbedFolder
                album_exists = True
                album_folder = matched_folder or default_folder
            elif (fname + ".md") in album_index["files"]:
                # 文件名命中（title 不同）：同样追加
                album_exists = True
                album_folder = album_index["files"][fname + ".md"] or default_folder
            else:
                album_folder = default_folder

        # 1. 图片处理（失败即中止，不写 md）
        image_urls = []
        if session.images:
            if session.kind == "album":
                folder = album_folder
            elif session.kind in ("media", "book"):
                # 影视/书籍封面独立目录（对齐博客图床惯例 blog/bangumi）
                folder = self._cfg("bangumi_upload_folder") or "blog/bangumi"
            elif session.kind == "daohang":
                # 导航图标独立目录（对齐博客图床惯例 blog/daohang）
                folder = self._cfg("daohang_upload_folder") or "blog/daohang"
            else:
                # 博客图床目录已统一（2026-08-13）：插件上传的图片全部进 imgbed_upload_folder（默认 blog/moments）
                folder = self._cfg("imgbed_upload_folder") or "blog/moments"
            result = await self._upload_images(session.images, folder)
            if isinstance(result, str):
                return event.plain_result("图片上传失败，已中止发布：{}".format(result))
            image_urls = result

        # 2. 生成 markdown
        try:
            now = now_shanghai()
            if session.kind == "moment":
                md = build_moment_md(
                    content,
                    image_urls,
                    self._merge_tags(self._cfg("moment_tags", ["日常"]), session.meta.get("tags")),
                    now,
                )
                path = "src/content/动态/{}.md".format(moment_filename(now))
                link = "/moments/{}".format(moment_filename(now))
            elif session.kind == "note":
                md = build_note_md(
                    session.meta.get("title", ""),
                    session.full_text(),
                    image_urls,
                    now,
                )
                note_dir = clean_filename_part(session.meta.get("note_dir") or "日常随笔", fallback="日常随笔")
                # 新笔记本先补 _index.json（博客书架/归档靠它识别，缺了文件夹不可见）
                index_created = False
                ok_idx, err_idx = await self._ensure_notebook_index(note_dir, repo, branch, token)
                if not ok_idx and err_idx:
                    return event.plain_result("新笔记本 _index.json 创建失败：{}，已中止发布（避免出现不可见笔记）。".format(err_idx))
                index_created = ok_idx
                path = "src/content/笔记本/{}/{}.md".format(note_dir, note_filename(now))
                link = "/笔记本/{}/{}".format(note_dir, note_filename(now))
            elif session.kind == "friend":
                friend_index = await self._github_list_dir_index(
                    "src/content/友链", repo, branch, token
                )
                title = session.meta.get("title", "").strip()
                md = build_friend_md(
                    title,
                    (session.meta.get("desc") or "").strip(),
                    session.meta.get("siteurl", "").strip(),
                    (session.meta.get("imgurl") or "").strip()
                    or (self._cfg("friend_default_avatar") or "/assets/ziyuan/tx.webp"),
                    self._cfg("friend_tags") or ["Blog"],
                )
                path = "src/content/友链/{:02d}-{}.md".format(
                    friend_index, clean_filename_part(title)
                )
                link = "/friends"
            elif session.kind == "album":
                album_name = (session.meta.get("name") or "").strip()
                clean_name = clean_filename_part(album_name)
                if album_exists:
                    # 追加模式：相册已存在（按 title/文件名命中），只传图不写文件。
                    # 博客详情页运行时从图床目录动态拉图，照片即时可见；列表页预览下次构建后更新。
                    self._sessions.pop(user_id, None)
                    return event.plain_result(
                        "已添加到相册「{}」：{} 张照片。\n照片在详情页即时可见（列表页预览将在下次构建后更新）。".format(
                            album_name, len(session.images)
                        )
                    )
                md = build_album_md(album_name, album_folder, now)
                path = "src/content/相册/{}.md".format(clean_name)
                link = "/album/{}".format(clean_name)
            elif session.kind == "daohang":
                # 导航条目（2026-09-21 对齐博客）：分类=文件夹，
                # 条目写 name/url/icon/description + 可选 tags（color/image/featured/order 已从 schema 删除）
                icon_url = image_urls[0] if image_urls else ""
                daohang_cat = clean_filename_part(
                    session.meta.get("category") or self._cfg("daohang_default_category", "未分类") or "未分类",
                    fallback="未分类",
                )
                md = build_daohang_md(
                    session.meta.get("name", ""),
                    session.meta.get("url", ""),
                    icon_url,
                    session.meta.get("description", ""),
                    body=session.full_text(),
                    tags=session.meta.get("tags") or [],
                )
                path = "src/content/导航/{}/{}.md".format(
                    daohang_cat, daohang_slug(session.meta.get("url", ""))
                )
                link = "/projects"
            elif session.kind == "book":
                # 书籍条目：封面用户上传（必须），标准 bangumi 格式 category: book，放 book/ 根目录
                if not image_urls:
                    return event.plain_result("书籍缺少封面图，请先发一张封面图再 /发布（或 /取消 放弃）。")
                book_title = str(session.meta.get("title") or "").strip()
                md = build_bangumi_md(
                    book_title,
                    image_urls[0],
                    score=session.meta.get("score"),
                    tags=session.meta.get("tags") or [],
                    comment=session.full_text(),
                    category="book",
                    now=now,
                )
                path = "src/content/追番/book/{}.md".format(clean_filename_part(book_title))
                link = "/books"
            elif session.kind == "media":
                # 影视/游戏条目：category 由向导决定（anime/game），落盘目录随之分流
                if not image_urls:
                    return event.plain_result("封面缺失，无法发布（请发一张封面图，或 /取消 放弃）。")
                media_title = str(session.meta.get("title") or "").strip()
                media_category = str(session.meta.get("category") or "anime")
                if media_category not in ("anime", "game"):
                    media_category = "anime"
                if media_category == "game":
                    # 游戏：category game、无 subcategory，放 bangumi/game/（对齐现有条目）
                    media_sub = ""
                    media_dir = "game"
                else:
                    media_sub = str(session.meta.get("subcategory") or "movie")
                    if media_sub not in ("movie", "tv", "anime", "documentary"):
                        media_sub = "movie"
                    media_dir = "anime"
                md = build_bangumi_md(
                    media_title,
                    image_urls[0],
                    score=session.meta.get("score"),
                    tags=session.meta.get("tags") or [],
                    comment=session.full_text(),
                    subcategory=media_sub,
                    category=media_category,
                    now=now,
                )
                path = "src/content/追番/{}/{}.md".format(media_dir, clean_filename_part(media_title))
                link = "/movies-games" if media_category == "game" else "/bangumi"
            else:
                lat, lng = await self._geocode(
                    session.meta.get("province", "") + session.meta.get("city", "")
                )
                if lat is None:
                    return event.plain_result(
                        "高德地理编码失败，无法获取坐标，已中止发布。请检查地点名称。"
                    )
                md = build_place_md(
                    session.meta.get("province", ""),
                    session.meta.get("city", ""),
                    session.meta.get("experience", ""),
                    image_urls,
                    lat,
                    lng,
                    self._merge_tags(self._cfg("place_tags", ["旅游"]), session.meta.get("tags")),
                    now,
                )
                path = "src/content/地点/{}.md".format(place_filename(now))
                link = "/地点/{}".format(place_filename(now))
        except Exception as e:
            logger.error("BlogWriter 生成 markdown 失败: {}".format(e))
            return event.plain_result("生成内容失败：{}".format(e))

        # 3. GitHub 提交（带冲突后缀重试）
        ok, final_path, err = await self._commit_md(path, md, now)
        if not ok:
            return event.plain_result("GitHub 提交失败：{}".format(err))

        self._sessions.pop(user_id, None)
        created_hint = "\n🎉 已新建笔记本「{}」（含 _index.json）".format(
            clean_filename_part(session.meta.get("note_dir") or "日常随笔", fallback="日常随笔")
        ) if session.kind == "note" and index_created else ""
        return event.plain_result(
            "发布成功 ✅\n\n文件：{}\n博客：https://blog.tsh520.cn{}{}".format(
                final_path, link, created_hint
            )
        )

    # ---------- 图片/视频 ----------

    _IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic")
    _VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm")
    _MEDIA_EXTS = _IMAGE_EXTS + _VIDEO_EXTS

    @staticmethod
    def _media_size_limit(ref: str) -> int:
        """按扩展名选大小上限：视频 100MB，图片 20MB。"""
        return MAX_VIDEO_SIZE if ref.lower().split("?")[0].endswith((".mp4", ".mov", ".m4v", ".webm")) else MAX_IMAGE_SIZE

    def _extract_images(self, event: AstrMessageEvent, allow_video: bool = False) -> List[str]:
        """提取图片（allow_video 时含视频）。优先远程 URL，否则用本地文件路径
        （个人微信适配器会把图片/视频下载到 data/temp）。

        防御式匹配：不依赖组件具体类型，兼容 Image/Video 组件及带媒体特征字段的其他组件。
        """
        exts = self._MEDIA_EXTS if allow_video else self._IMAGE_EXTS
        urls = []
        try:
            for comp in event.get_messages():
                comp_type = str(getattr(comp, "type", "") or "").lower()
                is_image_type = isinstance(comp, Image) or comp_type in ("image", "img")
                is_video_type = allow_video and (
                    (Video is not None and isinstance(comp, Video)) or comp_type in ("video",)
                )
                is_media_type = is_image_type or is_video_type
                candidates = []
                for attr in ("url", "file", "path", "src"):
                    v = str(getattr(comp, attr, "") or "").strip()
                    if v:
                        candidates.append(v)
                if not candidates:
                    continue
                picked = candidates[0]
                if picked.startswith("http"):
                    if is_media_type or any(picked.lower().endswith(e) for e in exts):
                        urls.append(picked)
                        continue
                elif is_media_type or any(picked.lower().endswith(e) for e in exts):
                    urls.append(picked)
        except Exception as e:
            logger.warning("BlogWriter 提取图片失败: {}".format(e))
        logger.info("BlogWriter: 图片提取结果 %d 个引用: %s", len(urls), urls[:3])
        return urls
    async def _read_image_bytes(self, ref: str) -> Optional[bytes]:
        """从 URL 或本地路径读取图片字节。"""
        if ref.startswith(("http://", "https://")):
            return await self._download_http(ref)
        try:
            path = ref[len("file://") :] if ref.startswith("file://") else ref
            path = urllib.parse.unquote(path)
            p = Path(path)
            if not p.is_file():
                logger.warning("BlogWriter 本地图片不存在: {}".format(ref))
                return None
            if p.stat().st_size > self._media_size_limit(ref):
                logger.warning("BlogWriter 本地媒体超过大小上限，跳过: {}".format(ref))
                return None
            return p.read_bytes()
        except Exception as e:
            logger.warning("BlogWriter 读取本地图片失败: {} ({})".format(ref, e))
            return None

    # 平台媒体多为外链 CDN（QQ 官方 multimedia.nt.qq.com.cn、企业微信 wework.qpic.cn、
    # 微信 mmbiz 等），部分 CDN 对 python-httpx 默认 UA 直接 403，统一带浏览器 UA
    # （2026-09-30 v1.0.57 适配企业微信/QQ 官方机器人时加）
    _DOWNLOAD_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    async def _download_http(self, url: str, referer: str = "") -> Optional[bytes]:
        headers = {"User-Agent": self._DOWNLOAD_UA}
        if referer:
            headers["Referer"] = referer
        for attempt in range(RETRY_COUNT + 1):
            try:
                resp = await self._get_client().get(url, headers=headers)
                if resp.status_code >= 400:
                    logger.warning("BlogWriter 下载图片 HTTP {}: {}".format(resp.status_code, url))
                    return None
                if len(resp.content) > self._media_size_limit(url):
                    logger.warning("BlogWriter 媒体超过大小上限，跳过: {}".format(url))
                    return None
                return resp.content
            except Exception as e:
                logger.warning("BlogWriter 下载图片失败(第{}次): {}".format(attempt + 1, e))
                if attempt < RETRY_COUNT:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
        return None

    async def _upload_images(self, stored: List[Tuple[str, bytes]], folder: str = "") -> object:
        """stored: [(来源引用, 字节)]。并发上传，返回 List[str]（图床 URL）或 str（错误信息）。"""
        results: List[Optional[str]] = [None] * len(stored)
        failures: List[str] = []

        async def upload_one(index: int, ref: str, data: bytes) -> None:
            filename = ref.rsplit("/", 1)[-1].split("?")[0].split("#")[0] or "image.png"
            if "." not in filename:
                filename = "image.png"
            ok, value = await self._imgbed_upload(filename, data, folder)
            if ok:
                results[index] = value
            else:
                failures.append("{}（{}）".format(value, filename))

        tasks = [upload_one(i, ref, data) for i, (ref, data) in enumerate(stored)]
        await asyncio.gather(*tasks)
        if failures:
            return "；".join(failures[:3])
        return [r for r in results if r is not None]

    async def _imgbed_upload(self, filename: str, data: bytes, folder: str = "") -> Tuple[bool, str]:
        upload_url = (self._cfg("imgbed_upload_url") or "https://img.tsh520.cn/upload").strip()
        token = (self._cfg("imgbed_token") or "").strip()
        base_host = upload_base_host(upload_url)
        req_data = build_imgbed_upload(upload_url, filename, data)
        for attempt in range(RETRY_COUNT + 1):
            try:
                headers = dict(req_data["headers"])
                if token:
                    headers["Authorization"] = "Bearer " + token
                resp = await self._get_client().post(
                    build_upload_url(upload_url, folder),
                    content=req_data["body"],
                    headers=headers,
                )
                ok, value = parse_imgbed_response(resp.status_code, resp.text, base_host)
                if not ok and resp.status_code == 401:
                    return False, "图床认证失败（HTTP 401）：请检查图床 Token；也可在地址后加 ?authCode=上传认证码"
                return ok, value
            except Exception as e:
                logger.warning("BlogWriter 图床上传失败(第{}次): {}".format(attempt + 1, e))
                if attempt < RETRY_COUNT:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
        return False, "图床上传网络错误"

    # ---------- 高德 ----------

    async def _geocode(self, address: str) -> Tuple[Optional[float], Optional[float]]:
        key = self._amap_key()
        if not key:
            logger.error("BlogWriter 未配置高德 Key（配置项 amap_key 或环境变量 AMAP_KEY）")
            return None, None
        url = build_amap_url(address, key)
        for attempt in range(RETRY_COUNT + 1):
            try:
                resp = await self._get_client().get(url)
                ok, coords = parse_amap_response(resp.status_code, resp.text)
                if ok:
                    return coords
                logger.warning("BlogWriter 高德解析失败: {}".format(resp.text[:120]))
                return None, None
            except Exception as e:
                logger.warning("BlogWriter 高德请求失败(第{}次): {}".format(attempt + 1, e))
                if attempt < RETRY_COUNT:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
        return None, None

    # ---------- GitHub ----------

    async def _commit_md(self, path: str, md: str, now: datetime) -> Tuple[bool, str, str]:
        repo = self._cfg("github_repo") or "tianshihao2003/dumplingandcakeblog"
        branch = self._cfg("github_branch") or "main"
        token = (self._cfg("github_token") or "").strip()
        base_name = path.rsplit(".", 1)[0]
        for index in range(11):
            candidate = with_suffix(base_name, ".md", index)
            exists = await self._github_exists(repo, candidate, branch, token)
            if exists is None:
                return False, "", "GitHub 查询失败（网络或 Token 问题）"
            if exists:
                continue
            ok, err = await self._github_put(repo, candidate, md, token, branch)
            if not ok:
                if err == "CONFLICT":
                    continue  # 并发冲突，换后缀
                return False, "", err
            return True, candidate, ""
        return False, "", "文件名冲突过多，无法找到可用文件名"

    async def _github_exists(self, repo: str, path: str, branch: str, token: str) -> Optional[bool]:
        url = github_base(repo, path, branch)
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        for attempt in range(RETRY_COUNT + 1):
            try:
                resp = await self._get_client().get(url, headers=headers)
                if resp.status_code == 200:
                    return True
                if resp.status_code == 404:
                    return False
                logger.warning("BlogWriter GitHub 查询 HTTP {}: {}".format(resp.status_code, path))
            except Exception as e:
                logger.warning("BlogWriter GitHub 查询失败(第{}次): {}".format(attempt + 1, e))
            if attempt < RETRY_COUNT:
                await asyncio.sleep(RETRY_DELAYS[attempt])
        return None

    async def _github_list_dir_index(self, dir_path: str, repo: str, branch: str, token: str) -> int:
        """列出目录文件名，计算下一个数字前缀编号（友链文件名 NN-name.md）。失败返回 1。"""
        import json as _json

        url = "https://api.github.com/repos/{}/contents/{}?ref={}".format(
            repo, urllib.parse.quote(dir_path), urllib.parse.quote(branch)
        )
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            resp = await self._get_client().get(url, headers=headers)
            if resp.status_code != 200:
                logger.warning("BlogWriter: 列目录失败 HTTP %s: %s", resp.status_code, dir_path)
                return 1
            data = _json.loads(resp.text)
            names = [str(item.get("name", "")) for item in data if isinstance(item, dict)]
            return next_friend_index(names)
        except Exception as e:
            logger.warning("BlogWriter: 列目录异常: %s", e)
            return 1

    async def _github_file_content(self, repo: str, path: str, branch: str, token: str) -> Optional[str]:
        """读取仓库文件文本内容（Accept: raw）。失败返回 None。"""
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github.raw",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            resp = await self._get_client().get(github_base(repo, path, branch), headers=headers)
            if resp.status_code == 200:
                return resp.text
            logger.warning("BlogWriter 读取文件失败 HTTP %s: %s", resp.status_code, path)
        except Exception as e:
            logger.warning("BlogWriter 读取文件异常: %s (%s)", e, path)
        return None

    async def _album_index(self, repo: str, branch: str, token: str) -> Optional[Dict[str, object]]:
        """列出 src/content/相册 目录并解析每个文件的 title/imgbedFolder。

        返回 {"titles": {title: folder}, "files": {文件名: folder},
              "entries": [(title, 文件名, folder), ...]}（entries 供向导一次性展示，避免二次拉取）；
        目录不存在（404）→ 空索引；网络失败 → None。
        """
        import json as _json

        url = "https://api.github.com/repos/{}/contents/src/content/相册?ref={}".format(
            repo, urllib.parse.quote(branch)
        )
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            resp = await self._get_client().get(url, headers=headers)
            if resp.status_code == 404:
                return {"titles": {}, "files": {}, "entries": []}
            if resp.status_code != 200:
                logger.warning("BlogWriter: 相册目录列出失败 HTTP %s", resp.status_code)
                return None
            items = _json.loads(resp.text)
            titles: Dict[str, str] = {}
            files: Dict[str, str] = {}
            entries: List[Tuple[str, str, str]] = []
            for item in items:
                if not isinstance(item, dict) or item.get("type") != "file":
                    continue
                name = str(item.get("name") or "")
                if not name.lower().endswith((".md", ".mdx")):
                    continue
                content = await self._github_file_content(
                    repo, "src/content/相册/" + name, branch, token
                )
                if content is None:
                    continue
                title, folder = parse_album_frontmatter(content)
                title = (title or name.rsplit(".", 1)[0]).strip()
                if title:
                    titles[title] = folder.strip()
                files[name] = folder.strip()
                entries.append((title, name, folder.strip()))
            return {"titles": titles, "files": files, "entries": entries}
        except Exception as e:
            logger.warning("BlogWriter: 相册目录解析异常: %s", e)
            return None

    async def _github_put(self, repo: str, path: str, md: str, token: str, branch: str) -> Tuple[bool, str]:
        body = build_github_put_body("blog: 通过 AstrBot 发布 {}".format(path), md.encode("utf-8"), branch)
        url = github_put_url(repo, path)
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        }
        for attempt in range(RETRY_COUNT + 1):
            try:
                resp = await self._get_client().put(url, content=body.encode("utf-8"), headers=headers)
                if resp.status_code in (409, 422):
                    return False, "CONFLICT"
                return parse_github_put_response(resp.status_code, resp.text)
            except Exception as e:
                logger.warning("BlogWriter GitHub 提交失败(第{}次): {}".format(attempt + 1, e))
                if attempt < RETRY_COUNT:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
        return False, "GitHub 提交网络错误"

    # ---------- 微信媒体兜底下载（绕开适配器 aiohttp 的 CDN TLS 握手失败） ----------

    _WX_IMAGE_ITEM_TYPE = "2"
    _WX_VIDEO_ITEM_TYPE = "5"

    def _extract_wx_raw_media(self, event: AstrMessageEvent, allow_video: bool = False) -> List[Tuple[str, str, str, str]]:
        """从 event.message_obj.raw_message 提取 (encrypt_query_param, aeskey_hex, aes_key_b64, kind)。

        结构对齐 astrbot/core/platform/sources/weixin_oc/weixin_oc_adapter.py 的
        _resolve_inbound_media_component：item.type==2 为图片（image_item），
        type==5 为视频（video_item，只有 media.aes_key、无 aeskey 字段）。
        kind ∈ {"image", "video"}。
        """
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if not isinstance(raw, dict):
            return []
        out = []
        for item in raw.get("item_list") or []:
            try:
                if not isinstance(item, dict):
                    continue
                itype = str(item.get("type"))
                if itype == self._WX_IMAGE_ITEM_TYPE:
                    key, kind = "image_item", "image"
                elif itype == self._WX_VIDEO_ITEM_TYPE and allow_video:
                    key, kind = "video_item", "video"
                else:
                    continue
                media_item = item.get(key) or {}
                media = media_item.get("media") or {}
                enc = str(media.get("encrypt_query_param") or "").strip()
                aes_hex = str(media_item.get("aeskey") or "").strip()
                aes_b64 = str(media.get("aes_key") or "").strip()
                if enc and (aes_hex or aes_b64):
                    out.append((enc, aes_hex, aes_b64, kind))
            except Exception as e:
                logger.warning("BlogWriter: 解析 raw_message 媒体项失败: %s", e)
        return out

    async def _download_wx_media(self, enc: str, aes_hex: str, aes_b64: str) -> Optional[bytes]:
        """curl 下载微信 CDN 加密媒体（curl 的 TLS 指纹可被微信 CDN 接受），再 AES-ECB 解密。

        已知现象：微信 CDN keep-alive 连接不主动关闭，curl 收满 Content-Length 后仍等待连接
        收尾而触发 --max-time 超时（returncode 28），但数据已完整。此时只要解密后的图片
        魔数校验通过，即视为成功。
        """
        import asyncio.subprocess

        import urllib.parse

        cdn = (self._cfg("wx_cdn_base_url") or "https://novac2c.cdn.weixin.qq.com/c2c").strip().rstrip("/")
        url = "{}/download?encrypted_query_param={}".format(cdn, urllib.parse.quote(enc))
        for attempt in range(RETRY_COUNT + 1):
            try:
                proc = await asyncio.subprocess.create_subprocess_exec(
                    "curl",
                    "-sS",
                    "--connect-timeout",
                    "8",
                    "--max-time",
                    "10",
                    "-H",
                    "Connection: close",
                    url,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode == 0 and stdout:
                    plain = self._decrypt_wx_media(stdout, aes_hex, aes_b64)
                    if plain is not None:
                        return plain
                elif stdout:
                    # 超时（28）但字节可能已完整：解密 + 魔数校验兜底
                    plain = self._decrypt_wx_media(stdout, aes_hex, aes_b64)
                    if plain is not None and self._has_image_magic(plain):
                        logger.info(
                            "BlogWriter: curl 超时但数据完整（%d 字节），魔数校验通过", len(plain)
                        )
                        return plain
                    logger.warning(
                        "BlogWriter: curl 下载微信媒体失败(第%d次): %s",
                        attempt + 1,
                        (stderr or b"").decode("utf-8", "replace")[:120],
                    )
                else:
                    logger.warning(
                        "BlogWriter: curl 下载微信媒体失败(第%d次): %s",
                        attempt + 1,
                        (stderr or b"").decode("utf-8", "replace")[:120],
                    )
                if attempt < RETRY_COUNT:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
            except Exception as e:
                logger.warning("BlogWriter: curl 下载异常(第%d次): %s", attempt + 1, e)
                if attempt < RETRY_COUNT:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
        return None

    @staticmethod
    def _has_image_magic(data: bytes) -> bool:
        """图片魔数校验：JPEG/PNG/GIF/WebP/BMP；顺带兼容 mp4（偏移 4 处 ftyp）。"""
        if not data:
            return False
        return any(
            data.startswith(m)
            for m in (b"\xff\xd8", b"\x89PNG", b"GIF8", b"RIFF", b"BM")
        ) or data[4:8] == b"ftyp"

    @staticmethod
    def _decrypt_wx_media(data: bytes, aes_hex: str, aes_b64: str) -> Optional[bytes]:
        """AES-ECB + PKCS7 解密，逻辑对齐 weixin_oc_client.parse_media_aes_key / pkcs7_unpad。"""
        try:
            from Crypto.Cipher import AES
        except ImportError:
            logger.error("BlogWriter: 缺少 pycryptodome（Crypto），无法解密微信媒体")
            return None
        try:
            if aes_hex:
                key = bytes.fromhex(aes_hex)
            else:
                padded = aes_b64.strip() + "=" * (-len(aes_b64.strip()) % 4)
                key = base64.b64decode(padded)
                if len(key) == 32:
                    key = bytes.fromhex(key.decode("ascii", errors="ignore"))
            if len(key) not in (16, 24, 32):
                logger.warning("BlogWriter: 微信媒体 AES key 长度异常: %d", len(key))
                return None
            plain = AES.new(key, AES.MODE_ECB).decrypt(data)
            pad_len = plain[-1] if plain else 0
            if 0 < pad_len <= 16 and plain[-pad_len:] == bytes([pad_len]) * pad_len:
                plain = plain[:-pad_len]
            return plain
        except Exception as e:
            logger.warning("BlogWriter: AES 解密失败: %s", e)
            return None

    # ---------- 向导分发 ----------

    async def _handle_wizard(self, event: AstrMessageEvent, user_id: str, raw: str):
        sess = self._sessions.get(user_id)
        if not sess or not getattr(sess, "wizard", None):
            return None
        step = sess.wizard.get("step", "")
        text = (raw or "").strip()
        # 空消息（微信心跳/系统事件）直接放行，不回复，避免刷屏
        if not text:
            return None

        # 通用取消
        if is_wizard_cancel(text):
            self._sessions.pop(user_id, None)
            return event.plain_result("已取消当前会话。")

        # ---- 笔记向导 ----
        if step == "note_pick_notebook":
            notebooks = sess.wizard.get("notebooks", [])
            n = len(notebooks)
            total = n + 1  # 含新建
            choice = parse_choice(text, total)
            if choice is not None:
                if choice == total:  # 新建
                    sess.wizard = {"step": "note_input_new_notebook", "notebooks": notebooks, "default_dir": sess.wizard.get("default_dir")}
                    return event.plain_result("请输入新笔记本名称（将作为文件夹名，非法字符会自动替换为 -）：")
                else:
                    picked = notebooks[choice - 1]
                    sess.meta["note_dir"] = picked
                    sess.wizard = {"step": "note_input_title", "note_dir": picked}
                    sess.touch()
                    return event.plain_result(f"已选笔记本「{picked}」。\n请输入笔记标题：")
            # 非数字：当作新建名直接处理（存清洗后的名字，防路径注入）
            if text and len(text) <= 30:
                chk = clean_filename_part(text, fallback="")
                if not chk:
                    return event.plain_result("名称无效，请重发（1-30字，避免特殊符号）。或回复数字选择。")
                if chk in notebooks:
                    sess.meta["note_dir"] = chk
                    sess.wizard = {"step": "note_input_title", "note_dir": chk}
                    sess.touch()
                    return event.plain_result(f"已选笔记本「{chk}」。\n请输入笔记标题：")
                sess.meta["note_dir"] = chk
                sess.wizard = {"step": "note_input_title", "note_dir": chk}
                sess.touch()
                return event.plain_result(f"已创建笔记本「{chk}」。\n请输入笔记标题：")
            return event.plain_result("请回复数字选择，或直接发新笔记本名称。\n" + format_choices("请选择笔记本：", notebooks, extra=["新建笔记本"], with_skip_cancel=False))

        if step == "note_input_new_notebook":
            chk = clean_filename_part(text, fallback="")
            if not chk:
                return event.plain_result("名称无效，请重发。")
            sess.meta["note_dir"] = chk
            sess.wizard = {"step": "note_input_title", "note_dir": chk}
            sess.touch()
            return event.plain_result(f"已选笔记本「{chk}」。\n请输入笔记标题：")

        if step == "note_input_title":
            if not text:
                return event.plain_result("标题不能为空，请重发笔记标题：")
            sess.meta["title"] = text.strip()
            sess.wizard = None
            sess.touch()
            return event.plain_result(
                f"笔记已创建：分类「{sess.meta['note_dir']}」标题「{text.strip()}」。\n\n接下来直接发正文（可多条，自动拼接），也可以发图片，发完说 /发布。"
            )

        # ---- 相册向导 ----
        if step == "album_pick":
            titles = sess.wizard.get("titles", [])
            display = sess.wizard.get("display", [])
            total = len(display) + 1
            choice = parse_choice(text, total)
            if choice is not None:
                if choice == total:  # 新建
                    sess.wizard = {"step": "album_input_new", "titles": titles, "display": display}
                    return event.plain_result("请输入新相册名称：")
                picked = titles[choice - 1]
                sess.meta["name"] = picked
                sess.wizard = None
                sess.touch()
                return event.plain_result(f"已选相册「{picked}」\n请直接发图片（可多发），发完说 /发布。")
            if text and len(text) <= 40:
                chk = clean_filename_part(text, fallback="")
                if chk:
                    sess.meta["name"] = chk
                    sess.wizard = None
                    sess.touch()
                    return event.plain_result(f"相册「{chk}」已创建。\n请直接发图片（可多发），发完说 /发布。")
            return event.plain_result("请回复数字选择，或直接发新相册名称。\n" + format_choices("请选择相册：", display, extra=["新建相册"], with_skip_cancel=False))

        if step == "album_input_new":
            chk = clean_filename_part(text, fallback="")
            if not chk:
                return event.plain_result("相册名无效，请重发。")
            sess.meta["name"] = chk
            sess.wizard = None
            sess.touch()
            return event.plain_result(f"相册「{chk}」已创建。\n请直接发图片（可多发），发完说 /发布。")

        # ---- 影视/书籍向导 ----
        if step == "media_need_cover":
            # 兼容旧路径：允许发 名称:/类型: 键值对修正标题/类型
            fields = parse_media_fields(text)
            if fields:
                if fields.get("title"):
                    sess.meta["title"] = fields["title"]
                if fields.get("subcategory"):
                    sess.meta["subcategory"] = fields["subcategory"]
                sess.touch()
                return event.plain_result("已修改：{}。请发封面图后继续。".format("，".join(f"{k}={v}" for k,v in fields.items())))
            return event.plain_result("请先发一张封面图（必须），发图后我会让你选类型。\n回复“取消”退出。")

        if step == "media_pick_category":
            choice = parse_choice(text, len(MEDIA_WIZARD_CATEGORIES))
            if choice is not None:
                label, val = MEDIA_WIZARD_CATEGORIES[choice - 1]
                if val == "game":
                    sess.meta["category"] = "game"
                    sess.meta.pop("subcategory", None)
                elif val in ("movie", "tv"):
                    sess.meta["category"] = "anime"
                    sess.meta["subcategory"] = val
                else:  # anime / documentary
                    sess.meta["category"] = "anime"
                    sess.meta["subcategory"] = val
                sess.wizard = {"step": "media_score"}
                sess.touch()
                return event.plain_result(f"已选类型「{label}」。\n请发评分 0-10（整数），或回复“跳过”：")
            if is_wizard_skip(text):
                sess.meta["subcategory"] = sess.meta.get("subcategory") or "movie"
                sess.wizard = {"step": "media_score"}
                sess.touch()
                return event.plain_result("已跳过类型（默认 电影）。\n请发评分 0-10，或回复“跳过”：")
            return event.plain_result("请回复数字选择类型：\n" + self._media_type_prompt())

        if step == "media_score":
            if is_wizard_skip(text):
                sess.meta["score"] = None
                sess.wizard = {"step": "media_tags"}
                sess.touch()
                return event.plain_result("已跳过评分。\n请发标签（#科幻 #冒险 形式，多标签空格分隔），或回复“跳过”：")
            score = parse_media_score(text)
            if score is not None:
                sess.meta["score"] = score
                sess.wizard = {"step": "media_tags"}
                sess.touch()
                return event.plain_result(f"已记录评分：{score}。\n请发标签（#科幻 形式），或回复“跳过”：")
            # 兼容纯数字
            if text.strip().isdigit():
                try:
                    v = max(0, min(10, int(text.strip())))
                    sess.meta["score"] = v
                    sess.wizard = {"step": "media_tags"}
                    sess.touch()
                    return event.plain_result(f"已记录评分：{v}。\n请发标签（#科幻 形式），或回复“跳过”：")
                except Exception:
                    pass
            return event.plain_result("评分格式不对，请发 0-10 的数字（如：8、评分 8），或回复“跳过”。")

        if step == "media_tags":
            if is_wizard_skip(text):
                sess.wizard = None
                sess.touch()
                return event.plain_result("已跳过标签。发 /发布 提交，或继续发影评文字。")
            clean, tags = extract_tags(text)
            if tags:
                existing = sess.meta.setdefault("tags", [])
                for t in tags:
                    if t not in existing:
                        existing.append(t)
            if clean:
                sess.add_text(clean)
            sess.wizard = None
            sess.touch()
            tag_hint = f"标签：{' '.join('#'+t for t in sess.meta.get('tags',[]))}" if sess.meta.get("tags") else "无标签"
            return event.plain_result(f"已记录，{tag_hint}。发 /发布 提交，或继续发影评。")

        # ---- 导航向导（分类=文件夹，2026-09-21 对齐博客） ----
        if step == "daohang_pick_category":
            folders = sess.wizard.get("folders", [])
            total = len(folders) + 1  # 含新建
            choice = parse_choice(text, total)
            if choice is not None:
                if choice == total:  # 新建分类
                    sess.wizard = {"step": "daohang_input_new_category", "folders": folders}
                    return event.plain_result("请输入新分类名（将作为文件夹名，如：摄影网站）：")
                picked = folders[choice - 1]
                sess.meta["category"] = picked
                sess.wizard = None
                sess.touch()
                return event.plain_result(
                    "已选分类「{}」。\n可继续发键值对：名称: xxx / 描述: xxx，可加 #标签\n发 /发布 提交，/取消 放弃。".format(picked)
                )
            if is_wizard_skip(text):
                default_cat = clean_filename_part(
                    self._cfg("daohang_default_category", "未分类") or "未分类", fallback="未分类"
                )
                sess.meta["category"] = default_cat
                sess.wizard = None
                sess.touch()
                return event.plain_result(
                    "已用默认分类「{}」。\n可继续发键值对：名称: xxx / 描述: xxx，可加 #标签\n发 /发布 提交，/取消 放弃。".format(default_cat)
                )
            if text and len(text) <= 20:
                # 直接回复分类名：命中现有文件夹（忽略大小写/空格差异）则用原名，否则作为新分类
                norm = re.sub(r"\s+", "", text).lower()
                matched = next((f for f in folders if re.sub(r"\s+", "", f).lower() == norm), "")
                picked = matched or clean_filename_part(text, fallback="")
                if picked:
                    sess.meta["category"] = picked
                    sess.wizard = None
                    sess.touch()
                    hint = "已选分类「{}」".format(picked) if matched else "已新建分类「{}」".format(picked)
                    return event.plain_result(
                        "{hint}。\n可继续发键值对：名称: xxx / 描述: xxx，可加 #标签\n发 /发布 提交，/取消 放弃。".format(hint=hint)
                    )
            return event.plain_result(
                "请回复数字选择分类，或直接发分类名：\n"
                + format_choices("请选择分类（文件夹）：", folders, extra=["新建分类"], with_skip_cancel=False)
            )

        if step == "daohang_input_new_category":
            chk = clean_filename_part(text, fallback="")
            if not chk:
                return event.plain_result("分类名无效，请重发（1-20 字，避免特殊符号）：")
            sess.meta["category"] = chk
            sess.wizard = None
            sess.touch()
            return event.plain_result(
                "已新建分类「{}」。\n可继续发键值对：名称: xxx / 描述: xxx，可加 #标签\n发 /发布 提交，/取消 放弃。".format(chk)
            )

        return None

    # ---------- 其他 ----------

    def _help_text(self) -> str:
        return (
            "📖 BlogWriter 使用手册\n"
            "\n"
            "———— ✍ 发内容 ————\n"
            "/动态 内容 #标签\n"
            "　可发图片、GIF、视频\n"
            "/笔记 [分类] 标题\n"
            "　空参会让你选笔记本/输标题\n"
            "/足迹 省 地点 体验\n"
            "　坐标自动获取，可发照片\n"
            "/友链\n"
            "　逐行发：名称/描述/链接/头像\n"
            "/相册 相册名\n"
            "　空参会列出相册让你选或新建\n"
            "\n"
            "———— 💰 记生活 ————\n"
            "/影视 侏罗纪世界\n"
            "　封面自动从 TMDB 获取\n"
            "　搜不到会让你选类型/评分/标签\n"
            "/书籍 认知觉醒\n"
            "　封面自己发一张，评分/标签/书评\n"
            "/导航 https://example.com\n"
            "　图标自动获取，向导选分类（文件夹），\n"
            "　随后发键值对：名称/描述，可加 #标签\n"
            "/转载 公众号文章链接\n"
            "　抓取正文图片转存图床，生成博客文章\n"
            "\n"
            "———— 🔧 管会话 ————\n"
            "/发布　提交当前会话\n"
            "/取消　放弃当前会话\n"
            "/状态　查看当前会话\n"
            "\n"
            "发 /帮助 随时查看本手册"
        )

    async def terminate(self):
        self._sessions.clear()
        if self._client is not None:
            await self._client.aclose()
            self._client = None
