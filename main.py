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


try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.date import DateTrigger

    HAS_APSCHEDULER = True
except ImportError:
    BackgroundScheduler = None  # type: ignore
    DateTrigger = None  # type: ignore
    HAS_APSCHEDULER = False

REMINDER_FILE = Path(__file__).parent / "data" / "schedules_reminder.json"

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
        BILL_ACCOUNTS,
        BILL_CATEGORIES,
        COMMANDS,
        SCHEDULE_PRIORITIES,
        SESSION_TIMEOUT,
        build_album_md,
        build_amap_url,
        build_bangumi_md,
        build_daohang_md,
        build_tmdb_search_url,
        build_xxapi_ico_url,
        build_bill_md,
        build_github_put_body,
        build_imgbed_upload,
        build_moment_md,
        build_note_md,
        build_place_md,
        build_schedule_md,
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
        parse_anniversary,
        parse_media_score,
        parse_media_fields,
        parse_tmdb_search_response,
        parse_xxapi_ico_response,
        parse_bill,
        parse_bills_batch,
        parse_daohang_text,
        parse_dynamic,
        parse_friend_text,
        parse_github_put_response,
        parse_imgbed_response,
        parse_message,
        parse_note,
        parse_place,
        parse_schedule,
        parse_schedules_batch,
        place_filename,
        schedule_filename,
        daohang_slug,
        site_host,
        tmdb_poster_url,
        upload_base_host,
        upload_url_with_return_format,
        validate_friend_data,
        with_suffix,
        Session,
        format_choices,
        parse_choice,
        is_wizard_cancel,
        is_wizard_skip,
        is_wizard_new,
        MEDIA_WIZARD_CATEGORIES,
        BILL_WIZARD_LIABILITY_ACCOUNTS,
        WIZARD_CANCEL_KEYWORDS,
        WIZARD_SKIP_KEYWORDS,
    )
except ImportError:  # 兼容非包形式加载
    from blog_writer_core import (
        BILL_ACCOUNTS,
        BILL_CATEGORIES,
        COMMANDS,
        SCHEDULE_PRIORITIES,
        SESSION_TIMEOUT,
        build_album_md,
        build_amap_url,
        build_bangumi_md,
        build_daohang_md,
        build_tmdb_search_url,
        build_xxapi_ico_url,
        build_bill_md,
        build_github_put_body,
        build_imgbed_upload,
        build_moment_md,
        build_note_md,
        build_place_md,
        build_schedule_md,
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
        parse_anniversary,
        parse_media_score,
        parse_media_fields,
        parse_tmdb_search_response,
        parse_xxapi_ico_response,
        parse_bill,
        parse_bills_batch,
        parse_daohang_text,
        parse_dynamic,
        parse_friend_text,
        parse_github_put_response,
        parse_imgbed_response,
        parse_message,
        parse_note,
        parse_place,
        parse_schedule,
        parse_schedules_batch,
        place_filename,
        schedule_filename,
        daohang_slug,
        site_host,
        tmdb_poster_url,
        upload_base_host,
        upload_url_with_return_format,
        validate_friend_data,
        with_suffix,
        Session,
        format_choices,
        parse_choice,
        is_wizard_cancel,
        is_wizard_skip,
        is_wizard_new,
        MEDIA_WIZARD_CATEGORIES,
        BILL_WIZARD_LIABILITY_ACCOUNTS,
        WIZARD_CANCEL_KEYWORDS,
        WIZARD_SKIP_KEYWORDS,
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
    "通过微信对话更新博客的动态、笔记、足迹、相册、友链、账单、日程（含微信日程提醒）",
    "v1.0.0",
)
class BlogWriter(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self._sessions: Dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._client = None
        self._reminders: List[Tuple[str, str, datetime]] = []
        self._reminder_file = Path(__file__).parent / "data" / "schedules_reminder.json"
        self._loop = None
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                self._loop = asyncio.get_event_loop()
            except Exception:
                self._loop = None
        self._scheduler = None
        if HAS_APSCHEDULER:
            try:
                # 显式指定上海时区，避免 UTC 偏差
                try:
                    self._scheduler = BackgroundScheduler(timezone=SHANGHAI_TZ)
                except Exception:
                    self._scheduler = BackgroundScheduler()
                self._scheduler.start()
                self._restore_reminders()
                logger.info("BlogWriter: APScheduler 提醒调度器已启动")
            except Exception as e:
                logger.warning("BlogWriter: APScheduler 启动失败: %s", e)
                self._scheduler = None
        else:
            logger.warning("BlogWriter: 未安装 apscheduler，提醒功能将仅内存生效（重启丢失）")

    def _load_reminders(self) -> List[Dict]:
        try:
            # 优先用模块级 REMINDER_FILE（便于测试时 mock），兼容实例级
            rf = globals().get("REMINDER_FILE", getattr(self, "_reminder_file", REMINDER_FILE))
            if rf.exists():
                return json.loads(rf.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("BlogWriter: 读取提醒文件失败: %s", e)
        return []

    def _save_reminders(self, data: List[Dict]) -> None:
        try:
            rf = globals().get("REMINDER_FILE", getattr(self, "_reminder_file", REMINDER_FILE))
            rf.parent.mkdir(parents=True, exist_ok=True)
            rf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("BlogWriter: 保存提醒文件失败: %s", e)

    def _restore_reminders(self) -> None:
        if not hasattr(self, "_reminders"):
            self._reminders = []
        if not HAS_APSCHEDULER or not self._scheduler:
            # 无调度器时仍恢复到内存列表，供列表展示
            try:
                for item in self._load_reminders():
                    try:
                        remind_at = datetime.fromisoformat(item["remind_at"])
                        if remind_at.tzinfo is not None:
                            remind_at = remind_at.astimezone(SHANGHAI_TZ).replace(tzinfo=None)
                        if remind_at <= now_shanghai():
                            continue
                        self._reminders.append((item["user_id"], item["title"], remind_at))
                    except Exception:
                        continue
            except Exception as e:
                logger.warning("BlogWriter: 恢复提醒(内存)异常: %s", e)
            return
        try:
            for item in self._load_reminders():
                try:
                    remind_at = datetime.fromisoformat(item["remind_at"])
                    if remind_at.tzinfo is not None:
                        remind_at = remind_at.astimezone(SHANGHAI_TZ).replace(tzinfo=None)
                    if remind_at <= now_shanghai():
                        continue
                    self._reminders.append((item["user_id"], item["title"], remind_at))
                    origin = item.get("origin") or ""
                    # 调度时用上海时区，存的是 naive，按上海解释
                    try:
                        remind_at_tz = remind_at.replace(tzinfo=SHANGHAI_TZ)
                    except Exception:
                        remind_at_tz = remind_at
                    self._scheduler.add_job(
                        self._send_remind_sync,
                        trigger=DateTrigger(run_date=remind_at_tz),
                        args=[item["user_id"], item["title"], origin],
                        id=item.get("id") or f"{item['user_id']}_{remind_at.isoformat()}",
                        replace_existing=True,
                    )
                except Exception as e:
                    logger.warning("BlogWriter: 恢复提醒失败: %s %s", item, e)
        except Exception as e:
            logger.warning("BlogWriter: 恢复提醒异常: %s", e)

    def _send_remind_sync(self, user_id: str, title: str, origin: str = "") -> None:
        # 使用初始化时保存的 loop，避免取到错误 loop 导致 Timeout context manager 异常
        loop = getattr(self, "_loop", None)
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
                self._loop = loop
            except RuntimeError:
                try:
                    loop = asyncio.get_event_loop()
                    self._loop = loop
                except Exception:
                    loop = None
        try:
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(self._send_remind(user_id, title, origin), loop)
            else:
                asyncio.run(self._send_remind(user_id, title, origin))
        except RuntimeError:
            # 无运行中的 loop 时创建临时 loop
            try:
                asyncio.run(self._send_remind(user_id, title, origin))
            except Exception as e:
                logger.warning("BlogWriter: 同步发送提醒失败: %s", e)

    async def _send_remind(self, user_id: str, title: str, origin: str = "") -> None:
        try:
            logger.info("BlogWriter: 到点提醒 user=%s title=%s origin=%s", user_id, title, origin)
            sent = False
            # 1. 优先用 origin + MessageChain（按 AstrBot 官方文档：MessageChain().message() + send_message(origin, chain)）
            if origin and hasattr(self.context, "send_message"):
                def _make_chain(text: str):
                    # 标准构造：优先尝试最简的 List[Plain]，再尝试 MessageChain
                    # 先试最简，避免 MessageChain().message() 内部的 timeout 陷阱
                    try:
                        from astrbot.core.message.message_event_result import MessageChain as _MC  # type: ignore

                        # 直接构造，避免调用 .message()（其内部可能用 asyncio.timeout）
                        try:
                            return _MC(chain=[Plain(text)])  # type: ignore
                        except Exception:
                            try:
                                return _MC([Plain(text)])  # type: ignore
                            except Exception:
                                pass
                    except ImportError:
                        pass
                    except Exception:
                        pass
                    try:
                        from astrbot.core.message.message import MessageChain as _MC1  # type: ignore

                        try:
                            return _MC1([Plain(text)])  # type: ignore
                        except Exception:
                            try:
                                return _MC1(chain=[Plain(text)])  # type: ignore
                            except Exception:
                                pass
                    except ImportError:
                        pass
                    except Exception:
                        pass
                    # 兜底：直接 Plain 列表，很多版本 send_message 也接受
                    return [Plain(text)]  # type: ignore

                for idx, maker in enumerate(
                    [
                        lambda: _make_chain(title),
                        lambda: [Plain(title)],  # type: ignore
                        lambda: Plain(title),  # type: ignore
                        lambda: title,  # type: ignore
                    ]
                ):
                    try:
                        chain = maker()  # type: ignore
                        await self.context.send_message(origin, chain)  # type: ignore
                        sent = True
                        logger.info("BlogWriter: 主动推送 via origin 成功 origin=%s attempt=%s", origin, idx)
                        break
                    except Exception as e:
                        logger.warning("BlogWriter: 主动推送 via origin 失败 attempt=%s err=%s", idx, e)
                        continue
                if not sent:
                    logger.warning("BlogWriter: 主动推送 via origin 失败: all attempts failed origin=%s", origin)
            # 2. 兜底：按 origin 解析平台名再试（兼容 weixin_oc / weixin_personal_bglh）
            if not sent and hasattr(self.context, "get_platform"):
                plat_names = []
                if origin and ":" in origin:
                    plat_names.append(origin.split(":")[0])
                plat_names.extend(["weixin_oc", "weixin_personal_bglh", "weixin"])

                def _plat_chain(text: str):
                    try:
                        from astrbot.core.message.message import MessageChain as _PMC1  # type: ignore

                        try:
                            return _PMC1(chain=[Plain(text)])  # type: ignore
                        except Exception:
                            try:
                                _mc = _PMC1()  # type: ignore
                                _mc.chain = [Plain(text)]  # type: ignore
                                return _mc
                            except Exception:
                                return _PMC1([Plain(text)])  # type: ignore
                    except ImportError:
                        try:
                            from astrbot.core.message.components import MessageChain as _PMC2  # type: ignore

                            return _PMC2([Plain(text)])  # type: ignore
                        except ImportError:
                            return [Plain(text)]  # type: ignore
                    except Exception:
                        return [Plain(text)]  # type: ignore

                seen = set()
                for pname in plat_names:
                    if not pname or pname in seen:
                        continue
                    seen.add(pname)
                    try:
                        plat = self.context.get_platform(pname)
                        if plat and hasattr(plat, "send_message"):
                            for idx, attempt in enumerate(
                                [
                                    lambda p=plat: p.send_message(_plat_chain(title), user_id),  # type: ignore
                                    lambda p=plat: p.send_message(Plain(title), user_id),  # type: ignore
                                    lambda p=plat: p.send_message(title, user_id),  # type: ignore
                                    lambda p=plat: p.send_message([Plain(title)], user_id),  # type: ignore
                                    lambda p=plat: p.send_message(user_id, _plat_chain(title)),  # type: ignore
                                ]
                            ):
                                try:
                                    await attempt()  # type: ignore
                                    sent = True
                                    logger.info("BlogWriter: 平台推送成功 via %s attempt=%s", pname, idx)
                                    break
                                except Exception as e:
                                    logger.warning("BlogWriter: 平台 %s 尝试 %s 失败: %s", pname, idx, e)
                                    continue
                        if sent:
                            break
                    except Exception as e:
                        logger.warning("BlogWriter: 平台 %s 获取失败: %s", pname, e)
            if not sent:
                logger.warning("BlogWriter: 未能主动推送，提醒仅记录日志 user=%s title=%s", user_id, title)
        except Exception as e:
            logger.warning("BlogWriter: 发送提醒失败: %s", e)

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
        now = now_shanghai()
        expired = [uid for uid, s in self._sessions.items() if s.expired(now)]
        for uid in expired:
            del self._sessions[uid]

    def _schedule_remind(self, user_id: str, title: str, remind_at: datetime, unified_msg_origin: str = "", remind_before: int = 10) -> None:
        """持久化调度：写入 json + APScheduler 定时，到点私聊提醒。 past 则不调度。"""
        try:
            if remind_at <= now_shanghai():
                logger.info("BlogWriter: 提醒时间已过，不调度 user=%s title=%s at=%s", user_id, title, remind_at)
                return
            # 确保 remind_at 为上海时区的 naive 时间，调度器按本地时间触发
            # APScheduler 默认使用本地时区，但容器内可能为 UTC，需显式指定
            try:
                remind_at_tz = remind_at.replace(tzinfo=SHANGHAI_TZ) if remind_at.tzinfo is None else remind_at.astimezone(SHANGHAI_TZ)
            except Exception:
                remind_at_tz = remind_at
            logger.info("BlogWriter: 调度提醒 user=%s title=%s at=%s origin=%s before=%s", user_id, title, remind_at, unified_msg_origin, remind_before)
            if not hasattr(self, "_reminders"):
                self._reminders = []
            self._reminders.append((user_id, title, remind_at))
            # 持久化
            try:
                data = self._load_reminders()
                rid = f"{user_id}_{title}_{remind_at.isoformat()}"
                data.append({"id": rid, "user_id": user_id, "title": title, "remind_at": remind_at.isoformat(), "remind_before": remind_before, "origin": unified_msg_origin})
                # 只保留未来 100 条
                data = [d for d in data if datetime.fromisoformat(d["remind_at"]) > now_shanghai() - timedelta(days=1)][-100:]
                self._save_reminders(data)
            except Exception as e:
                logger.warning("BlogWriter: 保存提醒持久化失败: %s", e)
            # 调度
            if HAS_APSCHEDULER and self._scheduler:
                try:
                    rid = f"{user_id}_{title}_{remind_at.isoformat()}"
                    # 显式指定上海时区，避免 UTC 偏差
                    trigger = DateTrigger(run_date=remind_at_tz) if HAS_APSCHEDULER else DateTrigger(run_date=remind_at)
                    self._scheduler.add_job(
                        self._send_remind_sync,
                        trigger=trigger,
                        args=[user_id, f"🔔 日程提醒：{title} 时间到了", unified_msg_origin],
                        id=rid,
                        replace_existing=True,
                    )
                except Exception as e:
                    logger.warning("BlogWriter: APScheduler 添加任务失败: %s", e)
        except Exception as e:
            logger.warning("BlogWriter: 调度提醒失败: %s", e)

    # ---------- 向导辅助 ----------

    _BILL_TYPE_LABELS = {"expense": "支出", "income": "收入", "liability": "负债", "transfer": "转账"}

    async def _list_notebook_names(self, repo: str, branch: str, token: str) -> List[str]:
        """列出 src/content/life/notebooks 下的一级目录名（笔记本名）。失败回退到默认。"""
        url = "https://api.github.com/repos/{}/contents/src/content/life/notebooks?ref={}".format(
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
                return []
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
            return []

    # ---------- 会话创建（账单/日程/提醒） ----------

    def _apply_bill_defaults(self, item: Dict) -> None:
        """正则解析出的账单项应用配置默认值（账户/分类为「其他」时替换）。

        负债不套默认账户（负债常挂在花呗/白条等信用账户，默认「微信」不对）。"""
        if item.get("account") == "其他" and item.get("type") != "liability":
            default_acc = self._cfg("bill_default_account", "其他")
            if default_acc in BILL_ACCOUNTS:
                item["account"] = default_acc
        if item.get("category") == "其他" and item.get("type") != "liability":
            default_cat = self._cfg("bill_default_category", "其他")
            if default_cat in BILL_CATEGORIES:
                item["category"] = default_cat

    async def _start_bill(self, event: AstrMessageEvent, user_id: str, args: List[str], raw: str):
        text = " ".join(args).strip()
        if not text:
            self._sessions[user_id] = Session("bill", {})
            return event.plain_result(
                "账单会话已创建，请发送账单内容，发 /发布 提交，发 /取消 放弃。\n"
                "支出：今天午餐微信花了32（或首词「支出」显式指定）\n"
                "收入：发工资12000 银行卡（或首词「收入」）\n"
                "负债：花呗借款5000 / 花呗还款2000（借入为正、还款为负；或首词「负债」）\n"
                "批量：午餐30晚餐45打车12"
            )
        # 批量正则（一句含多个金额，如“午餐30晚餐45打车12”）
        try:
            batch, _ = parse_bills_batch(text)
            if batch and len(batch) > 1:
                for item in batch:
                    self._apply_bill_defaults(item)
                self._sessions[user_id] = Session("bill_batch", {"items": batch})
                titles = "、".join(f"{x['title']}{x['amount']}" for x in batch[:5])
                more = f"等{len(batch)}笔" if len(batch) > 5 else ""
                return event.plain_result(f"已识别 {len(batch)} 笔账单：{titles}{more}，发 /发布 批量提交，发 /取消 放弃。")
        except Exception:
            pass
        # 单条正则
        parsed, err = parse_bill(text)
        if parsed is None:
            return event.plain_result("账单解析失败：{}，请重发或发 /取消。".format(err))
        self._apply_bill_defaults(parsed)
        # 进入账单向导：让用户确认类型
        t = parsed.get("type")
        label = self._BILL_TYPE_LABELS.get(t, t)
        sess = Session("bill", parsed)
        sess.wizard = {"step": "bill_confirm_type", "parsed": dict(parsed)}
        self._sessions[user_id] = sess
        return event.plain_result(
            "已识别账单：{} 金额{}（{}），分类{}，账户{}\n\n".format(
                parsed.get("title"), parsed.get("amount"), label, parsed.get("category"), parsed.get("account")
            )
            + format_choices("请确认类型：", ["支出", "收入", "负债-借入", "负债-还款"], extra=["直接发布"], with_skip_cancel=False)
            + f"\n\n当前猜测：{label}（选对应数字，或 5 直接发布）"
        )

    def _apply_schedule_defaults(self, item: Dict) -> None:
        """正则解析出的日程项应用配置默认值。"""
        if item.get("priority") == "none":
            default_p = self._cfg("schedule_default_priority", "none")
            if default_p in SCHEDULE_PRIORITIES:
                item["priority"] = default_p
        if item.get("remind_before") is None:
            item["remind_before"] = self._cfg("schedule_remind_before", 10)

    async def _start_schedule(self, event: AstrMessageEvent, user_id: str, args: List[str], raw: str):
        text = " ".join(args).strip()
        if not text:
            self._sessions[user_id] = Session("schedule", {})
            return event.plain_result("日程会话已创建，请发送日程内容（如：明天下午3点在会议室A开周会），发 /发布 提交，发 /取消 放弃。")
        # 批量正则（一句含多个生日，如“我的生日农历8.24对象12.22妈8.7都是农历”；建议用 /生日）
        try:
            batch, _ = parse_schedules_batch(text)
            if batch and len(batch) > 1:
                for item in batch:
                    self._apply_schedule_defaults(item)
                self._sessions[user_id] = Session("schedule_batch", {"items": batch})
                titles = "、".join(x["title"] for x in batch[:5])
                more = f"等{len(batch)}条" if len(batch) > 5 else ""
                return event.plain_result(f"已识别 {len(batch)} 条生日：{titles}{more}，发 /发布 批量提交，发 /取消 放弃。\n（提示：生日请优先用 /生日 命令）")
        except Exception:
            pass
        # 防呆：生日/纪念日请用专用命令，避免 category 生成错
        if "生日" in text or "生气" in text:
            return event.plain_result("检测到生日内容，请用 /生日 命令添加（如：/生日 我的农历8.24，支持一句多个：/生日 我的农历8.24对象12.22妈8.7都是农历）。")
        if "纪念日" in text:
            return event.plain_result("检测到纪念日内容，请用 /纪念日 命令添加（如：/纪念日 我和宝宝认识的纪念日 1月1日 @宝宝，支持农历：/纪念日 结婚纪念日 农历5月20）。")
        # 单条正则
        parsed, err = parse_schedule(text)
        if parsed is None:
            return event.plain_result("日程解析失败：{}，请重发或发 /取消。".format(err))
        self._apply_schedule_defaults(parsed)
        self._sessions[user_id] = Session("schedule", parsed)
        return event.plain_result(
            "已识别日程：{} 时间{} 优先级{}。发 /发布 提交，发 /取消 放弃。".format(
                parsed.get("title"),
                parsed.get("date").strftime("%Y-%m-%d %H:%M:%S") if isinstance(parsed.get("date"), datetime) else parsed.get("date"),
                parsed.get("priority"),
            )
        )

    def _fmt_birthday(self, item: Dict) -> str:
        """生日条目摘要：我生日（农历8月24，每年）"""
        if item.get("isLunar"):
            when = "农历{}月{}".format(item.get("lunarMonth"), item.get("lunarDay"))
        else:
            dt = item.get("date")
            when = dt.strftime("%m月%d日") if isinstance(dt, datetime) else str(dt)
        return "{}（{}，每年重复）".format(item.get("title"), when)

    async def _start_birthday(self, event: AstrMessageEvent, user_id: str, args: List[str], raw: str):
        """/生日 —— 专用生日命令，category 固定 birthday，杜绝文件类型生成错。"""
        text = " ".join(args).strip()
        if not text:
            self._sessions[user_id] = Session("birthday", {})
            return event.plain_result(
                "生日会话已创建，请发送生日内容（如：我的农历8.24、宝宝的 12.22），\n"
                "支持一句多个：我的农历8.24对象12.22妈8.7都是农历。\n"
                "发 /发布 提交，发 /取消 放弃。"
            )
        items, err = parse_schedules_batch(text)
        if not items:
            return event.plain_result("生日解析失败：{}，请重发（示例：我的农历8.24）或发 /取消。".format(err))
        for item in items:
            self._apply_schedule_defaults(item)
        if len(items) == 1:
            self._sessions[user_id] = Session("schedule", items[0])
            return event.plain_result("已识别生日：{}。发 /发布 提交，发 /取消 放弃。".format(self._fmt_birthday(items[0])))
        self._sessions[user_id] = Session("schedule_batch", {"items": items})
        titles = "、".join(x["title"] for x in items[:5])
        more = f"等{len(items)}条" if len(items) > 5 else ""
        return event.plain_result(f"已识别 {len(items)} 条生日：{titles}{more}，发 /发布 批量提交，发 /取消 放弃。")

    async def _start_anniversary(self, event: AstrMessageEvent, user_id: str, args: List[str], raw: str):
        """/纪念日 —— 专用纪念日命令，category 固定 anniversary、每年重复、全天。"""
        text = " ".join(args).strip()
        if not text:
            self._sessions[user_id] = Session("anniversary", {})
            return event.plain_result(
                "纪念日会话已创建，请发送纪念日内容（格式：标题 日期 [@人物]），如：\n"
                "我和宝宝认识的纪念日 1月1日 @宝宝\n"
                "结婚纪念日 农历5月20\n"
                "发 /发布 提交，发 /取消 放弃。"
            )
        parsed, err = parse_anniversary(text)
        if parsed is None:
            return event.plain_result("纪念日解析失败：{}，请重发（示例：结婚纪念日 农历5月20）或发 /取消。".format(err))
        self._apply_schedule_defaults(parsed)
        self._sessions[user_id] = Session("schedule", parsed)
        when = "农历{}月{}".format(parsed.get("lunarMonth"), parsed.get("lunarDay")) if parsed.get("isLunar") else parsed.get("date").strftime("%Y-%m-%d")
        return event.plain_result("已识别纪念日：{}（{}，每年重复）。发 /发布 提交，发 /取消 放弃。".format(parsed.get("title"), when))

    async def _start_daohang(self, event: AstrMessageEvent, user_id: str, args: List[str]):
        """/导航 网址 —— xxapi 取网站图标（字节入会话，/发布 传图床 blog/daohang），键值对补名称/分类/描述。"""
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
            "category": "未分类",
            "description": "",
            "color": "",
            "tags": [],
        })
        if icon_bytes:
            ext = (icon_url.rsplit(".", 1)[-1].split("?")[0].lower() or "png")
            if ext not in ("jpg", "jpeg", "png", "webp", "gif", "ico", "svg", "bmp"):
                ext = "png"
            session.add_image("{}-icon.{}".format(host, ext), icon_bytes)  # 对齐现有图床命名 blog.tsh520.cn-icon.webp
            icon_hint = "图标已就绪"
        else:
            icon_hint = "⚠ 图标获取失败（将无图标发布，不影响其他字段）"
        self._sessions[user_id] = session
        return event.plain_result(
            "{}：{}\n\n"
            "可继续发（键值对，可多行）：\n"
            "名称: 自定义名称\n"
            "分类: 我的网站\n"
            "描述: 一句话介绍\n"
            "颜色: #3b82f6\n"
            "#标签\n"
            "发 /发布 提交，/取消 放弃。".format(icon_hint, host)
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

    def _handle_remind(self, event: AstrMessageEvent, user_id: str, args: List[str]):
        text = " ".join(args).strip().lower()
        # 取消
        if text.startswith("取消") or text.startswith("cancel"):
            target = text.replace("取消", "").replace("cancel", "").strip()
            if not target:
                return event.plain_result("用法：/提醒 取消 <标题关键词> 或 /提醒 取消 全部")
            try:
                data = self._load_reminders()
                before = len(data)
                if target in ("全部", "all", "所有"):
                    data = [d for d in data if d["user_id"] != user_id]
                    if HAS_APSCHEDULER and self._scheduler:
                        for job in list(self._scheduler.get_jobs()):
                            if job.id.startswith(user_id):
                                self._scheduler.remove_job(job.id)
                else:
                    new_data = []
                    for d in data:
                        if d["user_id"] == user_id and target in d["title"]:
                            if HAS_APSCHEDULER and self._scheduler:
                                try:
                                    self._scheduler.remove_job(d["id"])
                                except Exception:
                                    pass
                            continue
                        new_data.append(d)
                    data = new_data
                self._save_reminders(data)
                return event.plain_result(f"已取消 {before - len(data)} 条提醒。")
            except Exception as e:
                return event.plain_result(f"取消失败：{e}")
        # 列表
        try:
            data = self._load_reminders()
            mine = [d for d in data if d["user_id"] == user_id and datetime.fromisoformat(d["remind_at"]) > now_shanghai()]
            if not mine:
                # 兼容旧内存
                if hasattr(self, "_reminders") and self._reminders:
                    lines = ["提醒列表（内存）："]
                    for uid, title, at in self._reminders[-10:]:
                        if uid == user_id:
                            lines.append(f"- {title} 于 {at.strftime('%Y-%m-%d %H:%M:%S') if isinstance(at, datetime) else at}")
                    if len(lines) > 1:
                        return event.plain_result("\n".join(lines))
                return event.plain_result("当前无待提醒日程（可通过 /日程 创建带时间的提醒，系统将提前通知。可用 /提醒 取消 标题关键词 取消）")
            lines = ["提醒列表："]
            for d in mine[-10:]:
                rb = d.get("remind_before", self._cfg('schedule_remind_before',10))
                try:
                    rb = int(rb)
                except Exception:
                    rb = 10
                # 兼容旧数据：remind_at 可能为 naive，需按上海时间显示
                at_str = d['remind_at'].replace('T',' ')[:19]
                lines.append(f"- {d['title']} 于 {at_str} (提前{rb}分)")
            return event.plain_result("\n".join(lines))
        except Exception as e:
            return event.plain_result(f"读取提醒失败：{e}")

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
                # 例外：无会话时收到图片/视频 → 提示，避免媒体被静默丢弃
                if self._extract_images(event, allow_video=True):
                    logger.info("BlogWriter: 用户 %s 无会话时发送媒体，已提示", user_id)
                    yield event.plain_result(
                        "当前没有进行中的会话，图片/视频未接收。请先发 /动态、/笔记、/足迹、/相册 等命令。"
                    )
                    return
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
            if cmd == "账单":
                yield await self._start_bill(event, user_id, args, raw)
                return
            if cmd == "日程":
                yield await self._start_schedule(event, user_id, args, raw)
                return
            if cmd == "生日":
                yield await self._start_birthday(event, user_id, args, raw)
                return
            if cmd == "纪念日":
                yield await self._start_anniversary(event, user_id, args, raw)
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
            if cmd == "提醒":
                yield self._handle_remind(event, user_id, args)
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
                # 影视/书籍图片优先：先收图，再走向导（避免 wizard 拦截图片消息）
                allow_video = session.kind == "moment"  # 视频仅动态支持；笔记/足迹/相册仍只收图片
                if session.kind in ("media", "book") and getattr(session, "wizard", None) and session.wizard.get("step") == "media_need_cover":
                    # 检查是否有图片，有则先收图
                    _pre_cover = self._extract_images(event, allow_video=False)
                    if not _pre_cover:
                        _pre_raw = self._extract_wx_raw_media(event, allow_video=False)
                        if _pre_raw:
                            _pre_cover = ["pre"]
                    if _pre_cover:
                        pass  # 有图，跳过 wizard 分发，直接走下面的收图逻辑
                    else:
                        res = await self._handle_wizard(event, user_id, raw)
                        if res is not None:
                            yield res
                            return
                elif getattr(session, "wizard", None):
                    res = await self._handle_wizard(event, user_id, raw)
                    if res is not None:
                        yield res
                        return
                    # wizard 已消费但无回复则继续走图片/文本逻辑
                if session.kind in ("media", "book"):
                    # 影视/书籍：用户发图 = 替换/提供封面（取最后一张，多次发图以最后为准）
                    cover_refs = self._extract_images(event, allow_video=False)
                    if not cover_refs:
                        raw_media = self._extract_wx_raw_media(event, allow_video=False)
                        for enc, aes_hex, aes_b64, kind in raw_media:
                            data = await self._download_wx_media(enc, aes_hex, aes_b64)
                            if data is not None:
                                cover_refs = ["wxraw_{}".format(enc[:16])]
                                break
                    if cover_refs:
                        ref = cover_refs[-1]
                        data = await self._read_image_bytes(ref)
                        if data is None:
                            yield event.plain_result("封面图读取失败，请重发。")
                            return
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
                    elif session.kind in ("bill", "bill_batch"):
                        # 手动修改：分类:餐饮 / 账户:支付宝（识别错或不识别时纠正）
                        m_cat = re.match(r"^(?:分类|类别|category)\s*[:：]?\s*(\S{1,10})\s*$", text)
                        m_acc = re.match(r"^(?:账户|账号|account)\s*[:：]?\s*(\S{1,10})\s*$", text)
                        if m_cat or m_acc:
                            if session.kind == "bill_batch":
                                yield event.plain_result("批量账单暂不支持逐条修改，请发 /取消 后重新逐条记录。")
                                return
                            if m_cat:
                                cat = m_cat.group(1)
                                if session.meta.get("type") == "liability":
                                    yield event.plain_result("负债的分类固定为「负债」，无需修改。")
                                    return
                                if cat not in BILL_CATEGORIES:
                                    yield event.plain_result(
                                        "分类「{}」不在白名单，可选：{}。".format(cat, "、".join(BILL_CATEGORIES))
                                    )
                                    return
                                session.meta["category"] = cat
                                session.touch()
                                yield event.plain_result(
                                    "已修改分类：{}。当前账单：{} 金额{}，分类{}，账户{}。发 /发布 提交。".format(
                                        cat, session.meta.get("title"), session.meta.get("amount"),
                                        session.meta.get("category"), session.meta.get("account"),
                                    )
                                )
                                return
                            if m_acc:
                                acc = m_acc.group(1)
                                session.meta["account"] = acc
                                session.touch()
                                yield event.plain_result(
                                    "已修改账户：{}。当前账单：{} 金额{}，分类{}，账户{}。发 /发布 提交。".format(
                                        acc, session.meta.get("title"), session.meta.get("amount"),
                                        session.meta.get("category"), session.meta.get("account"),
                                    )
                                )
                                return
                        # 空会话后下一句口语：正则解析（支持批量）
                        try:
                            batch, _ = parse_bills_batch(text)
                            if batch and len(batch) > 1:
                                for item in batch:
                                    self._apply_bill_defaults(item)
                                session.kind = "bill_batch"
                                session.meta = {"items": batch}
                                session.touch()
                                titles = "、".join(f"{x['title']}{x['amount']}" for x in batch[:5])
                                yield event.plain_result(f"已识别 {len(batch)} 笔账单：{titles}，发 /发布 批量提交，发 /取消 放弃。")
                                return
                        except Exception:
                            pass
                        parsed, err = parse_bill(text)
                        if parsed:
                            self._apply_bill_defaults(parsed)
                            session.meta.update(parsed)
                            session.kind = "bill"
                            session.touch()
                            yield event.plain_result(
                                "已识别账单：{} 金额{}，分类{}，账户{}。发 /发布 提交，发 /取消 放弃。".format(
                                    parsed.get("title"), parsed.get("amount"), parsed.get("category"), parsed.get("account")
                                )
                            )
                        else:
                            yield event.plain_result("账单解析失败：{}，请重发。".format(err))
                    elif session.kind == "daohang":
                        # 导航会话：键值对（名称/分类/描述/颜色）+ #标签
                        kv = parse_daohang_text(text)
                        clean, tags = extract_tags(text)
                        if kv:
                            for k, v in kv.items():
                                session.meta[k] = v
                        if tags:
                            existing = session.meta.setdefault("tags", [])
                            for t in tags:
                                if t not in existing:
                                    existing.append(t)
                        session.touch()
                        summary = "，".join(
                            "{}：{}".format(label, str(session.meta.get(key))[:30])
                            for key, label in (("name", "名称"), ("category", "分类"), ("description", "描述"), ("color", "颜色"))
                            if session.meta.get(key)
                        )
                        tag_hint = "，标签：{}".format(" ".join("#" + t for t in session.meta.get("tags", []))) if session.meta.get("tags") else ""
                        yield event.plain_result("已记录：{}{}。发 /发布 提交，发 /取消 放弃。".format(summary, tag_hint))
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
                    elif session.kind == "birthday":
                        # 生日会话：下一句口语走批量生日正则（支持单条与多个人物）
                        items, err = parse_schedules_batch(text)
                        if not items:
                            yield event.plain_result("生日解析失败：{}，请重发（示例：我的农历8.24）。".format(err))
                            return
                        for item in items:
                            self._apply_schedule_defaults(item)
                        if len(items) == 1:
                            session.meta.update(items[0])
                            session.kind = "schedule"
                        else:
                            session.kind = "schedule_batch"
                            session.meta = {"items": items}
                        session.touch()
                        titles = "、".join(x["title"] for x in items[:5])
                        more = f"等{len(items)}条" if len(items) > 5 else ""
                        yield event.plain_result(f"已识别生日：{titles}{more}。发 /发布 提交，发 /取消 放弃。")
                        return
                    elif session.kind == "anniversary":
                        # 纪念日会话：下一句口语走纪念日正则
                        parsed, err = parse_anniversary(text)
                        if parsed is None:
                            yield event.plain_result("纪念日解析失败：{}，请重发（示例：结婚纪念日 农历5月20）。".format(err))
                            return
                        self._apply_schedule_defaults(parsed)
                        session.meta.update(parsed)
                        session.kind = "schedule"
                        session.touch()
                        yield event.plain_result(
                            "已识别纪念日：{}。发 /发布 提交，发 /取消 放弃。".format(parsed.get("title"))
                        )
                        return
                    elif session.kind in ("schedule", "schedule_batch"):
                        # 空会话后下一句口语：优先批量生日正则，再单条正则
                        try:
                            batch, _ = parse_schedules_batch(text)
                            if batch and len(batch) > 1:
                                for item in batch:
                                    self._apply_schedule_defaults(item)
                                session.kind = "schedule_batch"
                                session.meta = {"items": batch}
                                session.touch()
                                titles = "、".join(x["title"] for x in batch[:5])
                                yield event.plain_result(f"已识别 {len(batch)} 条生日：{titles}，发 /发布 批量提交，发 /取消 放弃。")
                                return
                        except Exception:
                            pass
                        # 防呆：生日/纪念日走专用命令
                        if "生日" in text or "生气" in text:
                            yield event.plain_result("检测到生日内容，请取消后用 /生日 命令添加（如：/生日 我的农历8.24）。")
                            return
                        if "纪念日" in text:
                            yield event.plain_result("检测到纪念日内容，请取消后用 /纪念日 命令添加（如：/纪念日 结婚纪念日 农历5月20）。")
                            return
                        parsed, err = parse_schedule(text)
                        if parsed:
                            self._apply_schedule_defaults(parsed)
                            session.meta.update(parsed)
                            session.kind = "schedule"
                            session.touch()
                            yield event.plain_result(
                                "已识别日程：{} 时间{}。发 /发布 提交，发 /取消 放弃。".format(
                                    parsed.get("title"),
                                    parsed.get("date").strftime("%Y-%m-%d %H:%M:%S") if isinstance(parsed.get("date"), datetime) else parsed.get("date"),
                                )
                            )
                        else:
                            yield event.plain_result("日程解析失败：{}，请重发。".format(err))
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
        # 带参快车道：/笔记 分类 标题
        if args:
            note_dir, title = parse_note(args, default_dir)
            if title:
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
        # 空参进向导：列出现有相册
        repo = self._cfg("github_repo") or "tianshihao2003/dumplingandcakeblog"
        branch = self._cfg("github_branch") or "main"
        token = (self._cfg("github_token") or "").strip()
        idx = await self._album_index(repo, branch, token)
        if idx is None:
            return event.plain_result("GitHub 查询失败（网络或 Token 问题），请稍后重试或直接发 /相册 相册名 快捷创建。")
        # 去重展示：title(文件名) 形式
        seen_titles = {}
        display = []
        for fname, folder in idx.get("files", {}).items():
            try:
                content = await self._github_file_content(repo, "src/content/album/" + fname, branch, token)
                t, _f = parse_album_frontmatter(content or "")
                t = (t or fname.replace(".md", "")).strip()
            except Exception:
                t = fname.replace(".md", "")
            if t not in seen_titles:
                seen_titles[t] = fname
                display.append(f"{t}（{fname}）")
        # 按展示排序
        display.sort()
        sess = Session("album", {"name": ""})
        sess.wizard = {"step": "album_pick", "display": display, "titles": list(seen_titles.keys()), "files": seen_titles}
        self._sessions[user_id] = sess
        if not display:
            return event.plain_result("当前还没有相册，直接回复新相册名称即可创建。\n回复“取消”退出。")
        return event.plain_result(
            format_choices("请选择相册：", display, extra=["新建相册"], with_skip_cancel=False)
        )

    # ---------- 发布 ----------

    async def _publish(self, event: AstrMessageEvent, user_id: str) -> MessageEventResult:
        session = self._session(user_id)
        if not session:
            return event.plain_result("当前没有进行中的会话，请先发 /动态、/笔记、/足迹、/友链、/相册、/账单 或 /日程。")
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
                path = "src/content/album/{}.md".format(clean_name)
                link = "/album/{}".format(clean_name)
            elif session.kind == "daohang":
                # 导航条目：图标已在上方上传（无图标时 image 留空，schema optional）
                icon_url = image_urls[0] if image_urls else ""
                md = build_daohang_md(
                    session.meta.get("name", ""),
                    session.meta.get("url", ""),
                    session.meta.get("category", "未分类"),
                    icon_url,
                    session.meta.get("description", ""),
                    session.meta.get("tags") or [],
                    session.meta.get("color", ""),
                    body=session.full_text(),
                )
                path = "src/content/daohang/{}.md".format(daohang_slug(session.meta.get("url", "")))
                link = "/daohang"
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
                path = "src/content/bangumi/book/{}.md".format(clean_filename_part(book_title))
                link = "/books"
            elif session.kind == "media":
                # 影视条目：对齐博客现有 src/content/bangumi/anime/ 文件（封面已在上方上传）
                if not image_urls:
                    return event.plain_result("封面缺失，无法发布（请发一张封面图，或 /取消 放弃）。")
                media_title = str(session.meta.get("title") or "").strip()
                md = build_bangumi_md(
                    media_title,
                    image_urls[0],
                    score=session.meta.get("score"),
                    tags=session.meta.get("tags") or [],
                    comment=session.full_text(),
                    subcategory=session.meta.get("subcategory") or "movie",
                    now=now,
                )
                path = "src/content/bangumi/anime/{}.md".format(clean_filename_part(media_title))
                link = "/bangumi"
            elif session.kind == "bill":
                if not session.meta or session.meta.get("amount") is None:
                    return event.plain_result("账单信息不完整，请先发送账单内容（如：今天午餐微信花了32）。")
                md = build_bill_md(session.meta, now)
                title = str(session.meta.get("title") or "账单").strip() or "账单"
                slug = clean_filename_part(title)
                path = "src/content/bills/{}-{}.md".format(now.strftime("%Y-%m-%d"), slug)
                link = "/bills/{}".format(slug)
            elif session.kind == "bill_batch":
                items = session.meta.get("items") or []
                if not items:
                    return event.plain_result("批量账单为空，无法发布。")
                success = 0
                fails = []
                for item in items:
                    try:
                        md_item = build_bill_md(item, now)
                        title_item = str(item.get("title") or "账单").strip() or "账单"
                        slug_item = clean_filename_part(title_item)
                        path_item = "src/content/bills/{}-{}.md".format(now.strftime("%Y-%m-%d"), slug_item)
                        ok_item, final_item, err_item = await self._commit_md(path_item, md_item, now)
                        if ok_item:
                            success += 1
                        else:
                            fails.append(f"{title_item}:{err_item}")
                    except Exception as e:
                        fails.append(f"{item.get('title','?')}:{e}")
                self._sessions.pop(user_id, None)
                if fails:
                    return event.plain_result(f"批量账单发布完成：成功{success}条，失败{len(fails)}条：{'; '.join(fails[:3])}")
                return event.plain_result(f"批量账单发布成功 ✅ 共{success}条，已写入 bills。")
            elif session.kind in ("schedule", "birthday", "anniversary"):
                if not session.meta or not session.meta.get("title"):
                    return event.plain_result("日程/生日/纪念日信息不完整，请先发送内容（如：明天下午3点在会议室A开周会 / 我的农历8.24 / 结婚纪念日 农历5月20）。")
                md = build_schedule_md(session.meta, now)
                title = str(session.meta.get("title") or "日程").strip() or "日程"
                slug = clean_filename_part(title)
                # 农历日程文件名用 lunar-M-D 前缀（对齐博客 lunar-8-24-我的生日.md 惯例）
                path = "src/content/schedules/{}-{}.md".format(schedule_filename(session.meta, now), slug)
                link = "/schedules/{}".format(slug)
            elif session.kind == "schedule_batch":
                items = session.meta.get("items") or []
                if not items:
                    return event.plain_result("批量日程为空，无法发布。")
                # 批量创建：逐个生成并提交
                success = 0
                fails = []
                for item in items:
                    try:
                        md_item = build_schedule_md(item, now)
                        title_item = str(item.get("title") or "日程").strip() or "日程"
                        slug_item = clean_filename_part(title_item)
                        path_item = "src/content/schedules/{}-{}.md".format(schedule_filename(item, now), slug_item)
                        ok_item, final_item, err_item = await self._commit_md(path_item, md_item, now)
                        if ok_item:
                            success += 1
                            # 若为生日则无需提醒；若含时间则调度
                            try:
                                date_val = item.get("date")
                                dt = date_val if isinstance(date_val, datetime) else None
                                if isinstance(date_val, str):
                                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                                        try:
                                            dt = datetime.strptime(date_val.strip(), fmt)
                                            break
                                        except ValueError:
                                            continue
                                if dt and not item.get("allDay", True):
                                    all_day = item.get("allDay")
                                    if all_day is None:
                                        all_day = dt.hour == 0 and dt.minute == 0
                                    if not all_day:
                                        remind_before = item.get("remind_before", self._cfg("schedule_remind_before", 10))
                                        try:
                                            remind_before = int(remind_before)
                                        except Exception:
                                            remind_before = 10
                                        remind_at = dt - timedelta(minutes=remind_before) if remind_before > 0 else dt
                                        origin = getattr(event, "unified_msg_origin", "") or getattr(event, "session_id", "") or user_id
                                        self._schedule_remind(user_id, title_item, remind_at, origin, remind_before)
                            except Exception:
                                pass
                        else:
                            fails.append(f"{title_item}:{err_item}")
                    except Exception as e:
                        fails.append(f"{item.get('title','?')}:{e}")
                self._sessions.pop(user_id, None)
                if fails:
                    return event.plain_result(f"批量发布完成：成功{success}条，失败{len(fails)}条：{'; '.join(fails[:3])}")
                return event.plain_result(f"批量发布成功 ✅ 共{success}条，已写入 schedules。")
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

        # 日程提醒：成功后若为 schedule 且含时间则调用 _schedule_remind
        if session.kind in ("schedule", "birthday", "anniversary"):
            try:
                date_val = session.meta.get("date")
                dt = None
                if isinstance(date_val, datetime):
                    dt = date_val
                elif isinstance(date_val, str):
                    ds = date_val.strip()
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                        try:
                            dt = datetime.strptime(ds, fmt)
                            break
                        except ValueError:
                            continue
                if dt is not None:
                    all_day = session.meta.get("allDay")
                    if all_day is None:
                        all_day = dt.hour == 0 and dt.minute == 0 and dt.second == 0
                    if not all_day:
                        remind_before = session.meta.get("remind_before")
                        if remind_before is None:
                            remind_before = self._cfg("schedule_remind_before", 10)
                        try:
                            remind_before = int(remind_before)
                        except Exception:
                            remind_before = 10
                        remind_at = dt - timedelta(minutes=remind_before) if remind_before > 0 else dt
                        title = str(session.meta.get("title") or "日程").strip() or "日程"
                        origin = getattr(event, "unified_msg_origin", "") or getattr(event, "session_id", "") or user_id
                        # AstrBot 不同版本可能用 session / unified_msg_origin
                        try:
                            if not origin:
                                origin = str(getattr(event, "unified_msg_origin", "") or getattr(event, "session", "") or user_id)
                        except Exception:
                            origin = user_id
                        self._schedule_remind(user_id, title, remind_at, origin, remind_before)
            except Exception as e:
                logger.warning("BlogWriter: 调度提醒异常: %s", e)

        self._sessions.pop(user_id, None)
        return event.plain_result(
            "发布成功 ✅\n\n文件：{}\n博客：https://blog.tsh520.cn{}".format(final_path, link)
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

    async def _download_http(self, url: str) -> Optional[bytes]:
        for attempt in range(RETRY_COUNT + 1):
            try:
                resp = await self._get_client().get(url)
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

    async def _album_index(self, repo: str, branch: str, token: str) -> Optional[Dict[str, Dict[str, str]]]:
        """列出 src/content/album 目录并解析每个文件的 title/imgbedFolder。

        返回 {"titles": {title: folder}, "files": {文件名: folder}}；
        目录不存在（404）→ 空索引；网络失败 → None。
        """
        import json as _json

        url = "https://api.github.com/repos/{}/contents/src/content/album?ref={}".format(
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
                return {"titles": {}, "files": {}}
            if resp.status_code != 200:
                logger.warning("BlogWriter: 相册目录列出失败 HTTP %s", resp.status_code)
                return None
            items = _json.loads(resp.text)
            titles: Dict[str, str] = {}
            files: Dict[str, str] = {}
            for item in items:
                if not isinstance(item, dict) or item.get("type") != "file":
                    continue
                name = str(item.get("name") or "")
                if not name.lower().endswith((".md", ".mdx", ".json")):
                    continue
                content = await self._github_file_content(
                    repo, "src/content/album/" + name, branch, token
                )
                if content is None:
                    continue
                title, folder = parse_album_frontmatter(content)
                if title:
                    titles[title.strip()] = folder.strip()
                files[name] = folder.strip()
            return {"titles": titles, "files": files}
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
            # 非数字：当作新建名直接处理
            if text and len(text) <= 30:
                chk = clean_filename_part(text)
                if not chk or chk == "friend":
                    return event.plain_result("名称无效，请重发（1-30字，避免特殊符号）。或回复数字选择。")
                if text.strip() in notebooks:
                    sess.meta["note_dir"] = text.strip()
                    sess.wizard = {"step": "note_input_title", "note_dir": text.strip()}
                    sess.touch()
                    return event.plain_result(f"已选笔记本「{text.strip()}」。\n请输入笔记标题：")
                sess.meta["note_dir"] = text.strip()
                sess.wizard = {"step": "note_input_title", "note_dir": text.strip()}
                sess.touch()
                return event.plain_result(f"已创建笔记本「{text.strip()}」。\n请输入笔记标题：")
            return event.plain_result("请回复数字选择，或直接发新笔记本名称。\n" + format_choices("请选择笔记本：", notebooks, extra=["新建笔记本"], with_skip_cancel=False))

        if step == "note_input_new_notebook":
            if not text:
                return event.plain_result("名称不能为空，请重发。")
            chk = clean_filename_part(text)
            if not chk or chk == "friend":
                return event.plain_result("名称无效，请重发。")
            sess.meta["note_dir"] = text.strip()
            sess.wizard = {"step": "note_input_title", "note_dir": text.strip()}
            sess.touch()
            return event.plain_result(f"已选笔记本「{text.strip()}」。\n请输入笔记标题：")

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
                chk = clean_filename_part(text)
                if chk and chk != "friend":
                    sess.meta["name"] = text.strip()
                    sess.wizard = None
                    sess.touch()
                    return event.plain_result(f"相册「{text.strip()}」已创建。\n请直接发图片（可多发），发完说 /发布。")
            return event.plain_result("请回复数字选择，或直接发新相册名称。\n" + format_choices("请选择相册：", display, extra=["新建相册"], with_skip_cancel=False))

        if step == "album_input_new":
            if not text:
                return event.plain_result("相册名不能为空，请重发。")
            chk = clean_filename_part(text)
            if not chk or chk == "friend":
                return event.plain_result("相册名无效，请重发。")
            sess.meta["name"] = text.strip()
            sess.wizard = None
            sess.touch()
            return event.plain_result(f"相册「{text.strip()}」已创建。\n请直接发图片（可多发），发完说 /发布。")

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

        # ---- 账单向导 ----
        if step == "bill_confirm_type":
            # 兼容旧路径：会话内发 "分类:xxx / 账户:xxx" 直接修改（向导中也放行）
            m_cat_early = re.match(r"^(?:分类|类别|category)\s*[:：]?\s*(\S{1,10})\s*$", text)
            m_acc_early = re.match(r"^(?:账户|账号|account)\s*[:：]?\s*(\S{1,10})\s*$", text)
            if m_cat_early:
                cat = m_cat_early.group(1)
                if sess.meta.get("type") == "liability":
                    return event.plain_result("负债的分类固定为「负债」，无需修改。")
                if cat not in BILL_CATEGORIES:
                    return event.plain_result("分类「{}」不在白名单，可选：{}。".format(cat, "、".join(BILL_CATEGORIES)))
                sess.meta["category"] = cat
                sess.touch()
                return event.plain_result("已修改分类：{}。当前账单：{} 金额{}，分类{}，账户{}。发 /发布 提交。".format(
                    cat, sess.meta.get("title"), sess.meta.get("amount"), sess.meta.get("category"), sess.meta.get("account")))
            if m_acc_early:
                acc = m_acc_early.group(1)
                sess.meta["account"] = acc
                sess.touch()
                return event.plain_result("已修改账户：{}。当前账单：{} 金额{}，分类{}，账户{}。发 /发布 提交。".format(
                    acc, sess.meta.get("title"), sess.meta.get("amount"), sess.meta.get("category"), sess.meta.get("account")))
            choice = parse_choice(text, 5)
            if choice is not None:
                if choice == 5:  # 直接发布
                    sess.wizard = None
                    sess.touch()
                    return event.plain_result("已确认，直接发布请发 /发布。当前账单：{} 金额{}，分类{}，账户{}。".format(
                        sess.meta.get("title"), sess.meta.get("amount"), sess.meta.get("category"), sess.meta.get("account")))
                mapping = {1: "expense", 2: "income", 3: "liability_borrow", 4: "liability_repay"}
                picked = mapping.get(choice)
                if picked == "expense":
                    sess.meta["type"] = "expense"
                    sess.meta["amount"] = -abs(sess.meta.get("amount", 0))
                elif picked == "income":
                    sess.meta["type"] = "income"
                    sess.meta["amount"] = abs(sess.meta.get("amount", 0))
                elif picked == "liability_borrow":
                    sess.meta["type"] = "liability"
                    sess.meta["amount"] = abs(sess.meta.get("amount", 0))
                    sess.meta["category"] = "负债"
                elif picked == "liability_repay":
                    sess.meta["type"] = "liability"
                    sess.meta["amount"] = -abs(sess.meta.get("amount", 0))
                    sess.meta["category"] = "负债"
                    sess.wizard = {"step": "bill_pick_repay_account"}
                    sess.touch()
                    return event.plain_result(
                        format_choices("还款到哪个账户？", BILL_WIZARD_LIABILITY_ACCOUNTS, with_skip_cancel=False)
                        + "\n\n也可直接回复账户名（如：花呗）"
                    )
                # 非还款：进入分类选择（负债借入跳过分类）
                if sess.meta.get("type") == "liability":
                    sess.wizard = {"step": "bill_pick_account"}
                    sess.touch()
                    return event.plain_result(
                        format_choices("请选择账户：", BILL_ACCOUNTS, with_skip_cancel=False)
                        + "\n也可直接回复账户名"
                    )
                sess.wizard = {"step": "bill_pick_category"}
                sess.touch()
                return event.plain_result(format_choices("请选择分类：", BILL_CATEGORIES, with_skip_cancel=False))
            return event.plain_result("请回复数字 1-5 选择类型：\n" + format_choices("请确认类型：", ["支出", "收入", "负债-借入", "负债-还款"], extra=["直接发布"], with_skip_cancel=False))

        if step == "bill_pick_category":
            choice = parse_choice(text, len(BILL_CATEGORIES))
            if choice is not None:
                sess.meta["category"] = BILL_CATEGORIES[choice - 1]
                sess.wizard = {"step": "bill_pick_account"}
                sess.touch()
                return event.plain_result(format_choices("请选择账户：", BILL_ACCOUNTS, with_skip_cancel=False) + "\n也可直接回复账户名")
            if text and text.strip() in BILL_CATEGORIES:
                sess.meta["category"] = text.strip()
                sess.wizard = {"step": "bill_pick_account"}
                sess.touch()
                return event.plain_result(format_choices("请选择账户：", BILL_ACCOUNTS, with_skip_cancel=False))
            return event.plain_result("请回复数字选择分类：\n" + format_choices("请选择分类：", BILL_CATEGORIES, with_skip_cancel=False))

        if step == "bill_pick_account":
            all_accounts = BILL_ACCOUNTS + [a for a in BILL_WIZARD_LIABILITY_ACCOUNTS if a not in BILL_ACCOUNTS]
            choice = parse_choice(text, len(all_accounts))
            if choice is not None:
                sess.meta["account"] = all_accounts[choice - 1]
                sess.wizard = None
                sess.touch()
                return event.plain_result("账单已确认：{} 金额{}，分类{}，账户{}。发 /发布 提交。".format(
                    sess.meta.get("title"), sess.meta.get("amount"), sess.meta.get("category"), sess.meta.get("account")))
            if text and 1 <= len(text.strip()) <= 10:
                sess.meta["account"] = text.strip()
                sess.wizard = None
                sess.touch()
                return event.plain_result("账单已确认：{} 金额{}，分类{}，账户{}。发 /发布 提交。".format(
                    sess.meta.get("title"), sess.meta.get("amount"), sess.meta.get("category"), sess.meta.get("account")))
            return event.plain_result("请回复数字选择账户，或直接发账户名：\n" + format_choices("请选择账户：", all_accounts, with_skip_cancel=False))

        if step == "bill_pick_repay_account":
            choice = parse_choice(text, len(BILL_WIZARD_LIABILITY_ACCOUNTS))
            if choice is not None:
                sess.meta["account"] = BILL_WIZARD_LIABILITY_ACCOUNTS[choice - 1]
                sess.wizard = None
                sess.touch()
                return event.plain_result("账单已确认：{} 金额{}，分类负债，账户{}。发 /发布 提交。".format(
                    sess.meta.get("title"), sess.meta.get("amount"), sess.meta.get("account")))
            if text and 1 <= len(text.strip()) <= 10:
                sess.meta["account"] = text.strip()
                sess.wizard = None
                sess.touch()
                return event.plain_result("账单已确认：{} 金额{}，分类负债，账户{}。发 /发布 提交。".format(
                    sess.meta.get("title"), sess.meta.get("amount"), sess.meta.get("account")))
            return event.plain_result("请选择还款账户：\n" + format_choices("还款到哪个账户？", BILL_WIZARD_LIABILITY_ACCOUNTS, with_skip_cancel=False))

        # ---- 足迹确认（轻量，已禁用强制向导，仅保留显式触发） ----
        if step == "place_confirm":
            choice = parse_choice(text, 2)
            if choice == 1:
                sess.wizard = None
                sess.touch()
                return event.plain_result("已确认坐标。可以发照片后 /发布。")
            if choice == 2 or text.strip() in ("不对", "重说", "不对，我重说"):
                sess.wizard = None
                sess.touch()
                return event.plain_result("请重新发 /足迹 省 地点 体验 修正地址。已取消当前足迹会话。")
            return event.plain_result(format_choices("是否正确？", ["正确", "不对，我重说"], with_skip_cancel=False))

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
            "　会校验坐标让你确认\n"
            "/友链\n"
            "　逐行发：名称/描述/链接/头像\n"
            "/相册 相册名\n"
            "　空参会列出相册让你选或新建\n"
            "\n"
            "———— 💰 记生活 ————\n"
            "/账单 午餐微信花了32\n"
            "　会让你确认类型/分类/账户\n"
            "/日程 明天3点在A开会 每周\n"
            "　支持优先级、提前15分钟提醒\n"
            "/生日 我的农历8.24\n"
            "　可一句多人：我的8.24对象12.22\n"
            "/纪念日 结婚纪念日 农历5.20\n"
            "　格式：标题 日期 @人物\n"
            "/影视 侏罗纪世界\n"
            "　封面自动从 TMDB 获取\n"
            "　搜不到会让你选类型/评分/标签\n"
            "/书籍 认知觉醒\n"
            "　封面自己发一张，评分/标签/书评\n"
            "/导航 https://example.com\n"
            "　图标自动获取，随后发键值对：\n"
            "　名称/分类/描述/颜色/#标签\n"
            "\n"
            "———— 🔧 管会话 ————\n"
            "/发布　提交当前会话\n"
            "/取消　放弃当前会话\n"
            "/状态　查看当前会话\n"
            "/提醒 列表　看待提醒\n"
            "/提醒 取消 标题　取消提醒\n"
            "\n"
            "发 /帮助 随时查看本手册"
        )

    async def terminate(self):
        self._sessions.clear()
        if self._client is not None:
            await self._client.aclose()
            self._client = None
