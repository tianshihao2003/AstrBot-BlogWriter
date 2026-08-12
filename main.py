# -*- coding: utf-8 -*-
"""
AstrBot BlogWriter 插件
通过微信对话更新博客的动态（moments）、笔记（notebooks）、足迹（places）。

用法：
  /动态 今天去了公园            # 创建动态会话，可继续发图片
  /笔记 日常随笔 标题            # 创建笔记会话，正文由后续文本消息追加
  /足迹 陕西 华阴市华山 去找宝宝了  # 创建足迹会话（坐标由高德地理编码获取）
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
import os
import re
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import httpx

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register

try:
    from .blog_writer_core import (
        COMMANDS,
        SESSION_TIMEOUT,
        build_amap_url,
        build_github_put_body,
        build_imgbed_upload,
        build_moment_md,
        build_note_md,
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
        parse_dynamic,
        parse_friend_text,
        parse_github_put_response,
        parse_imgbed_response,
        parse_message,
        parse_note,
        parse_place,
        place_filename,
        upload_base_host,
        upload_url_with_return_format,
        validate_friend_data,
        with_suffix,
        Session,
    )
except ImportError:  # 兼容非包形式加载
    from blog_writer_core import (
        COMMANDS,
        SESSION_TIMEOUT,
        build_amap_url,
        build_github_put_body,
        build_imgbed_upload,
        build_moment_md,
        build_note_md,
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
        parse_dynamic,
        parse_friend_text,
        parse_github_put_response,
        parse_imgbed_response,
        parse_message,
        parse_note,
        parse_place,
        place_filename,
        upload_base_host,
        upload_url_with_return_format,
        validate_friend_data,
        with_suffix,
        Session,
    )

CONNECT_TIMEOUT = 15
READ_TIMEOUT = 30
MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB
RETRY_COUNT = 2
RETRY_DELAYS = (1.0, 3.0)


@register("blog_writer", "tianshihao2003", "通过微信对话更新博客的动态、笔记、足迹", "v1.0.0")
class BlogWriter(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self._sessions: Dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._client = None

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
        now = datetime.now()
        expired = [uid for uid, s in self._sessions.items() if s.expired(now)]
        for uid in expired:
            del self._sessions[uid]

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
                "BlogWriter: 收到消息 user=%s cmd=%s has_session=%s msg_str=%r",
                user_id, cmd, session is not None, (event.message_str or "")[:60],
            )

            # 与博客无关的消息（非命令、无进行中会话）一律放行，绝不回复
            if cmd not in COMMANDS and not session:
                # 例外：无会话时收到图片 → 提示，避免图片被静默丢弃
                if self._extract_images(event):
                    logger.info("BlogWriter: 用户 %s 无会话时发送图片，已提示", user_id)
                    yield event.plain_result(
                        "当前没有进行中的会话，图片未接收。请先发 /动态、/笔记 或 /足迹。"
                    )
                return

            # 非白名单用户：静默忽略，不回复（避免抢占其他插件/AI 的消息处理）
            if not self._allowed(user_id):
                logger.info(
                    "BlogWriter: 用户 %s（id=%s）无权限，已忽略。如需使用，请将 ID 加入 allow_users 配置。",
                    event.get_sender_name() or "?",
                    user_id,
                )
                return

            if not self._cfg("github_token"):
                yield event.plain_result("插件未配置 GitHub Token，请在插件设置中填写后再使用。")
                return

            if cmd == "动态":
                yield self._start_moment(event, user_id, args)
                return
            if cmd == "笔记":
                yield self._start_note(event, user_id, args)
                return
            if cmd == "足迹":
                yield self._start_place(event, user_id, args)
                return
            if cmd == "友链":
                yield self._start_friend(event, user_id, raw)
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
                images = self._extract_images(event)
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
                    raw_media = self._extract_wx_raw_media(event)
                    if raw_media:
                        logger.info("BlogWriter: 尝试 curl 兜底下载 %d 张微信媒体", len(raw_media))
                        stored = []
                        for enc, aes_hex, aes_b64 in raw_media:
                            data = await self._download_wx_media(enc, aes_hex, aes_b64)
                            if data is None:
                                yield event.plain_result(
                                    "微信图片下载失败（CDN 握手异常且 curl 兜底失败），请重发。"
                                )
                                return
                            stored.append(("wxraw_{}".format(enc[:16]), data))
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
            "动态已创建：{}{}\n\n可以直接发图片（可多发），发完说 /发布。".format(content, tag_hint)
        )

    def _start_note(self, event: AstrMessageEvent, user_id: str, args: List[str]):
        default_dir = self._cfg("default_note_dir") or "日常随笔"
        note_dir, title = parse_note(args, default_dir)
        if not title:
            return event.plain_result("格式：/笔记 [分类] 标题（例如：/笔记 日常随笔 标题）")
        self._sessions[user_id] = Session("note", {"note_dir": note_dir, "title": title})
        return event.plain_result(
            "笔记已创建：分类「{}」标题「{}」。\n\n接下来直接发正文（可多条，自动拼接），也可以发图片，发完说 /发布。".format(
                note_dir, title
            )
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

    # ---------- 发布 ----------

    async def _publish(self, event: AstrMessageEvent, user_id: str) -> MessageEventResult:
        session = self._session(user_id)
        if not session:
            return event.plain_result("当前没有进行中的会话，请先发 /动态、/笔记、/足迹 或 /友链。")
        repo = self._cfg("github_repo") or "tianshihao2003/dumplingandcakeblog"
        branch = self._cfg("github_branch") or "main"
        token = (self._cfg("github_token") or "").strip()
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

        # 1. 图片处理（失败即中止，不写 md）
        image_urls = []
        if session.images:
            # 足迹照片与现有数据一致放 places 目录；动态/笔记用配置的上传目录
            folder = "places" if session.kind == "place" else (self._cfg("imgbed_upload_folder") or "blog/moments")
            result = await self._upload_images(session.images, folder)
            if isinstance(result, str):
                return event.plain_result("图片上传失败，已中止发布：{}".format(result))
            image_urls = result

        # 2. 生成 markdown
        try:
            now = datetime.now()
            if session.kind == "moment":
                md = build_moment_md(
                    content,
                    image_urls,
                    self._cfg("author", "团子和蛋糕"),
                    self._cfg("avatar", "/assets/ziyuan/tx.webp"),
                    self._merge_tags(self._cfg("moment_tags", ["日常"]), session.meta.get("tags")),
                    now,
                )
                path = "src/content/moments/{}.md".format(moment_filename(now))
                link = "/moments/{}".format(moment_filename(now))
            elif session.kind == "note":
                md = build_note_md(
                    session.meta.get("title", ""),
                    session.full_text(),
                    image_urls,
                    now,
                )
                note_dir = session.meta.get("note_dir", "日常随笔")
                path = "src/content/life/notebooks/{}/{}.md".format(note_dir, note_filename(now))
                link = "/life/notebooks/{}/{}".format(note_dir, note_filename(now))
            elif session.kind == "friend":
                friend_index = await self._github_list_dir_index(
                    "src/content/friends", repo, branch, token
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
                path = "src/content/friends/{:02d}-{}.md".format(
                    friend_index, clean_filename_part(title)
                )
                link = "/friends"
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
                path = "src/content/life/places/{}.md".format(place_filename(now))
                link = "/life/places/{}".format(place_filename(now))
        except Exception as e:
            logger.error("BlogWriter 生成 markdown 失败: {}".format(e))
            return event.plain_result("生成内容失败：{}".format(e))

        # 3. GitHub 提交（带冲突后缀重试）
        ok, final_path, err = await self._commit_md(path, md, now)
        if not ok:
            return event.plain_result("GitHub 提交失败：{}".format(err))

        self._sessions.pop(user_id, None)
        return event.plain_result(
            "发布成功 ✅\n\n文件：{}\n博客：https://blog.tsh520.cn{}".format(final_path, link)
        )

    # ---------- 图片 ----------

    _IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic")

    def _extract_images(self, event: AstrMessageEvent) -> List[str]:
        """提取图片。优先远程 URL，否则用本地文件路径（个人微信适配器会下载到 data/temp）。

        防御式匹配：不依赖组件具体类型，兼容 Image 组件及带图片特征字段的其他组件。
        """
        urls = []
        try:
            for comp in event.get_messages():
                comp_type = str(getattr(comp, "type", "") or "").lower()
                is_image_type = isinstance(comp, Image) or comp_type in ("image", "img")
                candidates = []
                for attr in ("url", "file", "path", "src"):
                    v = str(getattr(comp, attr, "") or "").strip()
                    if v:
                        candidates.append(v)
                if not candidates:
                    continue
                picked = candidates[0]
                if picked.startswith("http"):
                    if is_image_type or any(picked.lower().endswith(e) for e in self._IMAGE_EXTS):
                        urls.append(picked)
                        continue
                elif is_image_type or any(picked.lower().endswith(e) for e in self._IMAGE_EXTS):
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
            if p.stat().st_size > MAX_IMAGE_SIZE:
                logger.warning("BlogWriter 图片超过 20MB，跳过: {}".format(ref))
                return None
            return p.read_bytes()
        except Exception as e:
            logger.warning("BlogWriter 读取本地图片失败: {} ({})".format(ref, e))
            return None

    async def _download_http(self, url: str) -> Optional[bytes]:
        for attempt in range(RETRY_COUNT + 1):
            try:
                resp = await self._get_client().get(url)
                if resp.status_code >= 400:
                    logger.warning("BlogWriter 下载图片 HTTP {}: {}".format(resp.status_code, url))
                    return None
                if len(resp.content) > MAX_IMAGE_SIZE:
                    logger.warning("BlogWriter 图片超过 20MB，跳过: {}".format(url))
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

    def _extract_wx_raw_media(self, event: AstrMessageEvent) -> List[Tuple[str, str, str]]:
        """从 event.message_obj.raw_message 提取 (encrypt_query_param, aeskey_hex, aes_key_b64)。

        结构对齐 astrbot/core/platform/sources/weixin_oc/weixin_oc_adapter.py 的
        _resolve_inbound_media_component：item.type==2 为图片，参数在 image_item.media 中。
        """
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if not isinstance(raw, dict):
            return []
        out = []
        for item in raw.get("item_list") or []:
            try:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type")) != self._WX_IMAGE_ITEM_TYPE:
                    continue
                image_item = item.get("image_item") or {}
                media = image_item.get("media") or {}
                enc = str(media.get("encrypt_query_param") or "").strip()
                aes_hex = str(image_item.get("aeskey") or "").strip()
                aes_b64 = str(media.get("aes_key") or "").strip()
                if enc and (aes_hex or aes_b64):
                    out.append((enc, aes_hex, aes_b64))
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
        """图片魔数校验：JPEG/PNG/GIF/WebP/BMP。"""
        if not data:
            return False
        return any(
            data.startswith(m)
            for m in (b"\xff\xd8", b"\x89PNG", b"GIF8", b"RIFF", b"BM")
        )

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

    # ---------- 其他 ----------

    def _help_text(self) -> str:
        return (
            "BlogWriter 使用说明：\n"
            "/动态 内容 #标签 —— 发动态（可附图片、自定义标签）\n"
            "/笔记 [分类] 标题 —— 发笔记，正文随后发\n"
            "/足迹 省 地点 体验 #标签 —— 发足迹，坐标自动获取\n"
            "/友链 —— 发友链（站点名称/描述/链接/头像链接，逐行发送自动识别）\n"
            "/发布 —— 结束并提交当前会话\n"
            "/取消 —— 放弃当前会话\n"
            "/状态 —— 查看当前会话"
        )

    async def terminate(self):
        self._sessions.clear()
        if self._client is not None:
            await self._client.aclose()
            self._client = None
