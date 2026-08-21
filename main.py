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
        parse_bill,
        parse_dynamic,
        parse_friend_text,
        parse_github_put_response,
        parse_imgbed_response,
        parse_message,
        parse_note,
        parse_place,
        parse_schedule,
        place_filename,
        upload_base_host,
        upload_url_with_return_format,
        validate_friend_data,
        with_suffix,
        Session,
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
        parse_bill,
        parse_dynamic,
        parse_friend_text,
        parse_github_put_response,
        parse_imgbed_response,
        parse_message,
        parse_note,
        parse_place,
        parse_schedule,
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
MAX_VIDEO_SIZE = 100 * 1024 * 1024  # 100MB（微信视频常超 20MB）
RETRY_COUNT = 2
RETRY_DELAYS = (1.0, 3.0)

# ---------- LLM 抽取 Prompt（Task 3） ----------
BILL_CATEGORIES_STR = "、".join(BILL_CATEGORIES) if "BILL_CATEGORIES" in globals() else "餐饮、交通、住房、工资、居家生活、交流通讯、食品酒水、职业收入、人情收礼、其他"
BILL_ACCOUNTS_STR = "、".join(BILL_ACCOUNTS) if "BILL_ACCOUNTS" in globals() else "微信、支付宝、银行卡、现金、其他"
BILL_PROMPT = (
    "你是账单信息抽取助手，只输出 JSON，不要解释。\n"
    "字段：title(标题/简短描述), amount(数字,支出为负收入为正), type(expense/income), category(白名单分类), account(白名单账户), date(YYYY-MM-DD), description(描述)\n"
    "分类白名单: " + BILL_CATEGORIES_STR + "\n"
    "账户白名单: " + BILL_ACCOUNTS_STR + "\n"
    "示例输入：今天午餐微信花了32\n"
    '示例输出：{"title":"午餐","amount":-32,"type":"expense","category":"餐饮","account":"微信","date":"2026-08-21","description":"午餐"}\n'
    "示例输入：发工资12000 银行卡\n"
    '示例输出：{"title":"工资","amount":12000,"type":"income","category":"工资","account":"银行卡","date":"2026-08-21","description":"工资"}\n'
    "未提及的字段用默认值：category=其他, account=微信, date=当天\n"
    "只输出 JSON 对象。"
)
SCHEDULE_PROMPT = (
    "你是日程信息抽取助手，只输出 JSON，不要解释。时区为 Asia/Shanghai。\n"
    "字段：title(标题), date(YYYY-MM-DD HH:MM:SS 或 YYYY-MM-DD), allDay(bool), priority(none/low/medium/high), location(地点), repeat(每天/每周/每月/每年或空), remind_before(整数分钟)\n"
    "优先级白名单: none、low、medium、high（高优=high，中优=medium，低优=low）\n"
    "时间基准：{now}（当前时间，请以此为基准精确计算相对时间，不要加减12小时）\n"
    "相对时间规则：'X分钟后' = 基准+ X分钟，'半小时后'=基准+30分钟，'X小时后'=基准+X小时，保持日期与基准同一天除非跨天\n"
    "示例输入：明天下午3点高优在会议室A开周会 每周重复 提前15分钟\n"
    '示例输出：{"title":"周会","date":"2026-08-22 15:00:00","allDay":false,"priority":"high","location":"会议室A","repeat":"每周","remind_before":15}\n'
    "示例输入：2分钟后在会议室A开周会 高优 提前1分钟（若基准是 {now}，2分钟后就是 {now_plus_2m}）\n"
    '示例输出：{"title":"周会","date":"{now_plus_2m}","allDay":false,"priority":"high","location":"会议室A","repeat":"","remind_before":1}\n'
    "示例输入：明天上午9点开会\n"
    '示例输出：{"title":"开会","date":"2026-08-22 09:00:00","allDay":false,"priority":"none","location":"","repeat":"","remind_before":10}\n'
    "未提及时间则 allDay=true，priority 默认 none，remind_before 默认 10。\n"
    "只输出 JSON 对象。"
)


@register("blog_writer", "tianshihao2003", "通过微信对话更新博客的动态、笔记、足迹", "v1.0.0")
class BlogWriter(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self._sessions: Dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._client = None
        self._reminders: List[Tuple[str, str, datetime]] = []
        self._reminder_file = Path(__file__).parent / "data" / "schedules_reminder.json"
        self._scheduler = None
        if HAS_APSCHEDULER:
            try:
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
                    # 调度时用上海时区
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
        try:
            asyncio.run_coroutine_threadsafe(self._send_remind(user_id, title, origin), asyncio.get_event_loop())
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
            # 1. 优先用 origin + MessageChain（按 AstrBot 标准构造）
            if origin and hasattr(self.context, "send_message"):
                # 尝试构造 MessageChain，兼容不同版本的导入路径
                def _make_chain(text: str):
                    try:
                        from astrbot.core.message.message import MessageChain as _MC1  # type: ignore

                        try:
                            return _MC1(chain=[Plain(text)])  # type: ignore
                        except Exception:
                            try:
                                mc = _MC1()  # type: ignore
                                mc.chain = [Plain(text)]  # type: ignore
                                return mc
                            except Exception:
                                return _MC1([Plain(text)])  # type: ignore
                    except ImportError:
                        try:
                            from astrbot.core.message.components import MessageChain as _MC2  # type: ignore

                            try:
                                return _MC2(chain=[Plain(text)])  # type: ignore
                            except Exception:
                                return _MC2([Plain(text)])  # type: ignore
                        except ImportError:
                            return [Plain(text)]  # type: ignore
                    except Exception:
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

    # ---------- LLM 抽取（Task 3） ----------

    async def _try_ai_extract(self, text: str, kind: str) -> Optional[Dict]:
        """LLM 抽取账单/日程，失败返回 None。

        - 若 enable_ai_bill_schedule 关闭则直接 None
        - 通过 hasattr 探测 context.get_using_llm（兼容 get_llm 等）
        - 发 System Prompt（BILL_PROMPT/SCHEDULE_PROMPT 含白名单+示例）+ user_text
        - 解析 JSON，超时 8s 重试1次，失败返回 None
        """
        if not self._cfg("enable_ai_bill_schedule", True):
            return None
        llm = None
        try:
            if hasattr(self.context, "get_using_llm"):
                llm = self.context.get_using_llm()
            elif hasattr(self.context, "get_llm"):
                llm = self.context.get_llm()
            elif hasattr(self.context, "llm"):
                llm = getattr(self.context, "llm")
        except Exception as e:
            logger.warning("BlogWriter: 获取 LLM 失败: %s", e)
            return None
        if llm is None:
            return None
        prompt = BILL_PROMPT if kind == "bill" else SCHEDULE_PROMPT
        # 动态填充时间基准，避免 LLM 看到字面量 {now}
        if kind == "schedule":
            try:
                _now = now_shanghai()
                _now_plus_2m = (_now + timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
                prompt = prompt.format(now=_now.strftime("%Y-%m-%d %H:%M:%S"), now_plus_2m=_now_plus_2m)
            except Exception:
                pass
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": text}]
        for attempt in range(2):
            try:
                resp = None
                if hasattr(llm, "chat"):
                    # 常见：await llm.chat(messages)
                    resp = await asyncio.wait_for(llm.chat(messages), timeout=8)
                elif hasattr(llm, "ainvoke"):
                    resp = await asyncio.wait_for(llm.ainvoke(messages), timeout=8)
                elif hasattr(llm, "generate"):
                    resp = await asyncio.wait_for(llm.generate(prompt + "\n" + text), timeout=8)
                elif callable(llm):
                    resp = await asyncio.wait_for(llm(messages), timeout=8)
                else:
                    return None
                # 归一化为字符串
                if isinstance(resp, str):
                    raw = resp
                elif isinstance(resp, dict):
                    raw = resp.get("content") or resp.get("text") or resp.get("result") or str(resp)
                else:
                    raw = str(getattr(resp, "content", "") or getattr(resp, "text", "") or getattr(resp, "result", "") or resp)
                raw = raw.strip()
                if not raw:
                    raise ValueError("LLM 返回空")
                # 去除 ```json 包装
                if raw.startswith("```"):
                    raw = re.sub(r"^```(?:json)?\s*", "", raw)
                    raw = re.sub(r"\s*```$", "", raw)
                    raw = raw.strip()
                # 若含多余文字，提取 JSON 对象
                if not raw.startswith("{"):
                    m = re.search(r"\{.*\}", raw, re.DOTALL)
                    if m:
                        raw = m.group(0)
                data = json.loads(raw)
                if isinstance(data, dict):
                    return data
                return None
            except asyncio.TimeoutError:
                logger.warning("BlogWriter: LLM 抽取超时(第%s次) kind=%s", attempt + 1, kind)
                if attempt == 1:
                    return None
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning("BlogWriter: LLM 抽取失败(第%s次): %s", attempt + 1, e)
                if attempt == 1:
                    return None
                await asyncio.sleep(0.5)
        return None

    def _normalize_bill_data(self, data: Dict, original_text: str = "") -> Optional[Dict]:
        """将 LLM 返回的账单 JSON 标准化为 core 可用的 Dict（供 build_bill_md）。"""
        try:
            title = str(data.get("title") or data.get("description") or "").strip()
            if not title:
                title = (original_text or "").strip()[:20] or "账单"
            amount = data.get("amount")
            if amount is None:
                return None
            try:
                amount = float(amount)
                if amount == int(amount):
                    amount = int(amount)
            except Exception:
                return None
            type_ = str(data.get("type") or "").strip() or "expense"
            if type_ not in ("income", "expense", "transfer"):
                # 根据 amount 正负推断
                type_ = "income" if amount > 0 else "expense"
            # 确保 amount 符号与 type 一致
            if type_ == "expense":
                amount = -abs(amount)
                if isinstance(amount, float) and amount == int(amount):
                    amount = int(amount)
            else:
                amount = abs(amount)
                if isinstance(amount, float) and amount == int(amount):
                    amount = int(amount)
            category = str(data.get("category") or self._cfg("bill_default_category", "其他")).strip() or "其他"
            if category not in BILL_CATEGORIES:
                # 尝试在文本中匹配白名单
                found = None
                for cat in BILL_CATEGORIES:
                    if cat in str(data.get("category") or "") or (original_text and cat in original_text):
                        found = cat
                        break
                category = found or self._cfg("bill_default_category", "其他") or "其他"
                if category not in BILL_CATEGORIES:
                    category = "其他"
            account = str(data.get("account") or self._cfg("bill_default_account", "微信")).strip() or "其他"
            if account not in BILL_ACCOUNTS:
                found = None
                for acc in BILL_ACCOUNTS:
                    if acc in str(data.get("account") or "") or (original_text and acc in original_text):
                        found = acc
                        break
                account = found or self._cfg("bill_default_account", "微信") or "其他"
                if account not in BILL_ACCOUNTS:
                    account = "其他"
            date_val = data.get("date")
            now = now_shanghai()
            if isinstance(date_val, datetime):
                dt = date_val
            elif isinstance(date_val, str) and date_val.strip():
                ds = date_val.strip()
                # 尝试多种格式
                dt = None
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
                    try:
                        dt = datetime.strptime(ds, fmt)
                        break
                    except ValueError:
                        continue
                if dt is None:
                    dt = now
            else:
                # LLM 未返回日期，尝试从原文正则提取，否则用当天
                try:
                    from blog_writer_core import _parse_bill_date as _pbd  # type: ignore

                    dt = _pbd(original_text or title, now)
                except Exception:
                    dt = now
            # 去掉时分秒，仅保留日期（账单按天）
            dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            description = str(data.get("description") or title).strip() or title
            return {
                "title": title,
                "amount": amount,
                "type": type_,
                "category": category,
                "account": account,
                "date": dt,
                "description": description,
            }
        except Exception as e:
            logger.warning("BlogWriter: 账单数据标准化失败: %s", e)
            return None

    def _normalize_schedule_data(self, data: Dict, original_text: str = "") -> Optional[Dict]:
        """将 LLM 返回的日程 JSON 标准化为 core 可用的 Dict（供 build_schedule_md）。"""
        try:
            title = str(data.get("title") or "").strip()
            if not title:
                title = (original_text or "").strip()[:20] or "日程"
            # 清洗标题中的相对时间残留（如 LLM 返回“2分钟后周会”）
            if title and re.search(r"\d+\s*分钟后|\d+\s*小时后|半小时后|半个小时后", title):
                title = re.sub(r"\d+\s*分钟后", "", title)
                title = re.sub(r"\d+\s*小时后", "", title)
                title = re.sub(r"\d+\s*秒后", "", title)
                title = title.replace("半小时后", "").replace("半个小时后", "").strip(" ，,。")
                if not title:
                    title = "日程"
            date_val = data.get("date")
            now = now_shanghai()
            dt = None
            all_day = data.get("allDay")
            if isinstance(date_val, datetime):
                dt = date_val
            elif isinstance(date_val, str) and date_val.strip():
                ds = date_val.strip()
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
                    try:
                        dt = datetime.strptime(ds, fmt)
                        break
                    except ValueError:
                        continue
                if dt is None:
                    # 回退正则解析
                    try:
                        from blog_writer_core import _parse_schedule_date as _psd, _parse_schedule_time as _pst  # type: ignore

                        base = _psd(original_text or title, now)
                        dt, has_time = _pst(original_text or title, base, now)
                        if all_day is None:
                            all_day = not has_time
                    except Exception:
                        dt = now
            else:
                # LLM 未返回日期，用正则兜底
                try:
                    from blog_writer_core import _parse_schedule_date as _psd, _parse_schedule_time as _pst  # type: ignore

                    base = _psd(original_text or title, now)
                    dt, has_time = _pst(original_text or title, base, now)
                    if all_day is None:
                        all_day = not has_time
                except Exception:
                    dt = now
                    if all_day is None:
                        all_day = True
            if dt is None:
                dt = now
                if all_day is None:
                    all_day = True
            if all_day is None:
                all_day = dt.hour == 0 and dt.minute == 0 and dt.second == 0
            # 相对时间强制覆盖：原文含“X分钟后”等时，LLM 常有时区/12小时偏差，直接用本地正则结果覆盖
            if original_text and re.search(r"(\d+\s*(?:分钟|分|小时|时|秒)\s*后|半小时后|半个小时后)", original_text):
                try:
                    from blog_writer_core import _parse_schedule_date as _psd2, _parse_schedule_time as _pst2

                    base2 = _psd2(original_text, now)
                    dt2, has_time2 = _pst2(original_text, base2, now)
                    if has_time2:
                        # 只有当正则算出的时间与 AI 相差超过 2 分钟时才覆盖，避免误覆盖
                        if abs((dt2 - dt).total_seconds()) > 120 or (dt.hour == 0 and dt.minute == 0):
                            dt = dt2
                            all_day = False
                except Exception:
                    pass
            # 旧兜底保留：若仍为 00:00 且原文含相对时间，再算一次
            if original_text and re.search(r"(\d+\s*(?:分钟|分|小时|时|秒)\s*后|半小时后|半个小时后)", original_text):
                if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
                    try:
                        from blog_writer_core import _parse_schedule_date as _psd3, _parse_schedule_time as _pst3

                        base3 = _psd3(original_text, now)
                        dt3, has_time3 = _pst3(original_text, base3, now)
                        if has_time3:
                            dt = dt3
                            all_day = False
                    except Exception:
                        pass
            priority = str(data.get("priority") or self._cfg("schedule_default_priority", "none")).strip() or "none"
            if priority not in SCHEDULE_PRIORITIES:
                # 兼容中文
                mapping = {"高": "high", "高优": "high", "中": "medium", "中优": "medium", "低": "low", "低优": "low"}
                priority = mapping.get(priority, "none")
                if priority not in SCHEDULE_PRIORITIES:
                    priority = "none"
            location = str(data.get("location") or "").strip()
            repeat = str(data.get("repeat") or "").strip()
            if repeat and repeat not in ("每天", "每周", "每月", "每年", "每日"):
                repeat = ""
            if repeat == "每日":
                repeat = "每天"
            remind_before = data.get("remind_before")
            if remind_before is None:
                remind_before = self._cfg("schedule_remind_before", 10)
            try:
                remind_before = int(remind_before)
            except Exception:
                remind_before = 10
            return {
                "title": title,
                "date": dt,
                "allDay": bool(all_day),
                "priority": priority,
                "location": location,
                "repeat": repeat,
                "remind_before": remind_before,
                "status": str(data.get("status") or "todo").strip() or "todo",
            }
        except Exception as e:
            logger.warning("BlogWriter: 日程数据标准化失败: %s", e)
            return None

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

    # ---------- 会话创建（账单/日程/提醒） ----------

    async def _start_bill(self, event: AstrMessageEvent, user_id: str, args: List[str], raw: str):
        text = " ".join(args).strip()
        if not text:
            self._sessions[user_id] = Session("bill", {})
            return event.plain_result("账单会话已创建，请发送账单内容（如：今天午餐微信花了32），发 /发布 提交，发 /取消 放弃。")
        # 先尝试 AI 抽取
        data = await self._try_ai_extract(text, "bill")
        if data:
            normalized = self._normalize_bill_data(data, text)
            if normalized:
                self._sessions[user_id] = Session("bill", normalized)
                return event.plain_result(
                    "已识别账单：{} 金额{}，分类{}，账户{}。发 /发布 提交，发 /取消 放弃。".format(
                        normalized.get("title"), normalized.get("amount"), normalized.get("category"), normalized.get("account")
                    )
                )
        # 正则兜底
        parsed, err = parse_bill(text)
        if parsed is None:
            return event.plain_result("账单解析失败：{}，请重发或发 /取消。".format(err))
        # 应用默认值
        if parsed.get("account") == "其他":
            default_acc = self._cfg("bill_default_account", "其他")
            if default_acc in BILL_ACCOUNTS:
                parsed["account"] = default_acc
        if parsed.get("category") == "其他":
            default_cat = self._cfg("bill_default_category", "其他")
            if default_cat in BILL_CATEGORIES:
                parsed["category"] = default_cat
        self._sessions[user_id] = Session("bill", parsed)
        return event.plain_result(
            "已识别账单：{} 金额{}，分类{}，账户{}。发 /发布 提交。".format(
                parsed.get("title"), parsed.get("amount"), parsed.get("category"), parsed.get("account")
            )
        )

    async def _start_schedule(self, event: AstrMessageEvent, user_id: str, args: List[str], raw: str):
        text = " ".join(args).strip()
        if not text:
            self._sessions[user_id] = Session("schedule", {})
            return event.plain_result("日程会话已创建，请发送日程内容（如：明天下午3点在会议室A开周会），发 /发布 提交，发 /取消 放弃。")
        data = await self._try_ai_extract(text, "schedule")
        if data:
            normalized = self._normalize_schedule_data(data, text)
            if normalized:
                self._sessions[user_id] = Session("schedule", normalized)
                return event.plain_result(
                    "已识别日程：{} 时间{} 优先级{}。发 /发布 提交，发 /取消 放弃。".format(
                        normalized.get("title"),
                        normalized.get("date").strftime("%Y-%m-%d %H:%M:%S") if isinstance(normalized.get("date"), datetime) else normalized.get("date"),
                        normalized.get("priority"),
                    )
                )
        parsed, err = parse_schedule(text)
        if parsed is None:
            return event.plain_result("日程解析失败：{}，请重发或发 /取消。".format(err))
        # 默认优先级/提醒
        if parsed.get("priority") == "none":
            default_p = self._cfg("schedule_default_priority", "none")
            if default_p in SCHEDULE_PRIORITIES:
                parsed["priority"] = default_p
        if parsed.get("remind_before") is None:
            parsed["remind_before"] = self._cfg("schedule_remind_before", 10)
        self._sessions[user_id] = Session("schedule", parsed)
        return event.plain_result(
            "已识别日程：{} 时间{} 优先级{}。发 /发布 提交。".format(
                parsed.get("title"),
                parsed.get("date").strftime("%Y-%m-%d %H:%M:%S") if isinstance(parsed.get("date"), datetime) else parsed.get("date"),
                parsed.get("priority"),
            )
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
                        "当前没有进行中的会话，图片/视频未接收。请先发 /动态、/笔记 或 /足迹。"
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
            if cmd == "相册":
                yield self._start_album(event, user_id, args)
                return
            if cmd == "账单":
                yield await self._start_bill(event, user_id, args, raw)
                return
            if cmd == "日程":
                yield await self._start_schedule(event, user_id, args, raw)
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
                allow_video = session.kind == "moment"  # 视频仅动态支持；笔记/足迹/相册仍只收图片
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
                    elif session.kind == "bill":
                        # 空会话后下一句口语：尝试 AI 抽取，否则正则兜底
                        data = await self._try_ai_extract(text, "bill")
                        if data:
                            normalized = self._normalize_bill_data(data, text)
                            if normalized:
                                session.meta.update(normalized)
                                session.touch()
                                yield event.plain_result(
                                    "已识别账单：{} 金额{}。发 /发布 提交，发 /取消 放弃。".format(
                                        normalized.get("title"), normalized.get("amount")
                                    )
                                )
                                return
                        parsed, err = parse_bill(text)
                        if parsed:
                            # 应用默认值
                            if parsed.get("account") == "其他":
                                default_acc = self._cfg("bill_default_account", "其他")
                                if default_acc in BILL_ACCOUNTS:
                                    parsed["account"] = default_acc
                            if parsed.get("category") == "其他":
                                default_cat = self._cfg("bill_default_category", "其他")
                                if default_cat in BILL_CATEGORIES:
                                    parsed["category"] = default_cat
                            session.meta.update(parsed)
                            session.touch()
                            yield event.plain_result(
                                "已识别账单：{} 金额{}。发 /发布 提交，发 /取消 放弃。".format(
                                    parsed.get("title"), parsed.get("amount")
                                )
                            )
                        else:
                            yield event.plain_result("账单解析失败：{}，请重发。".format(err))
                    elif session.kind == "schedule":
                        data = await self._try_ai_extract(text, "schedule")
                        if data:
                            normalized = self._normalize_schedule_data(data, text)
                            if normalized:
                                session.meta.update(normalized)
                                session.touch()
                                yield event.plain_result(
                                    "已识别日程：{} 时间{}。发 /发布 提交，发 /取消 放弃。".format(
                                        normalized.get("title"),
                                        normalized.get("date").strftime("%Y-%m-%d %H:%M:%S") if isinstance(normalized.get("date"), datetime) else normalized.get("date"),
                                    )
                                )
                                return
                        parsed, err = parse_schedule(text)
                        if parsed:
                            session.meta.update(parsed)
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

    def _start_album(self, event: AstrMessageEvent, user_id: str, args: List[str]):
        name = parse_album(args)
        if not name:
            return event.plain_result("格式：/相册 相册名（例如：/相册 情侣头像）")
        self._sessions[user_id] = Session("album", {"name": name})
        return event.plain_result(
            "相册「{}」已创建。\n\n请直接发图片（可多发），发完说 /发布。".format(name)
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
            elif session.kind == "bill":
                if not session.meta or session.meta.get("amount") is None:
                    return event.plain_result("账单信息不完整，请先发送账单内容（如：今天午餐微信花了32）。")
                md = build_bill_md(session.meta, now)
                title = str(session.meta.get("title") or "账单").strip() or "账单"
                slug = clean_filename_part(title)
                path = "src/content/bills/{}-{}.md".format(now.strftime("%Y-%m-%d"), slug)
                link = "/bills/{}".format(slug)
            elif session.kind == "schedule":
                if not session.meta or not session.meta.get("title"):
                    return event.plain_result("日程信息不完整，请先发送日程内容。")
                md = build_schedule_md(session.meta, now)
                title = str(session.meta.get("title") or "日程").strip() or "日程"
                slug = clean_filename_part(title)
                path = "src/content/schedules/{}-{}.md".format(now.strftime("%Y-%m-%d"), slug)
                link = "/schedules/{}".format(slug)
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
        if session.kind == "schedule":
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

    # ---------- 其他 ----------

    def _help_text(self) -> str:
        return (
            "BlogWriter 使用说明：\n"
            "/动态 内容 #标签 —— 发动态（可附图片/GIF/视频、自定义标签）\n"
            "/笔记 [分类] 标题 —— 发笔记，正文随后发\n"
            "/足迹 省 地点 体验 #标签 —— 发足迹，坐标自动获取\n"
            "/友链 —— 发友链（站点名称/描述/链接/头像链接，逐行发送自动识别）\n"
            "/相册 相册名 —— 发相册照片（随后直接发图，多张可多次发送）\n"
            "/账单 内容 —— 记账（支持自然语言，如：今天午餐微信花了32，发工资12000）\n"
            "/日程 内容 —— 建日程（支持自然语言+提醒，如：明天下午3点在会议室A开周会 每周重复 提前15分钟）\n"
            "/提醒 —— 查看待提醒日程\n"
            "/发布 —— 结束并提交当前会话\n"
            "/取消 —— 放弃当前会话\n"
            "/状态 —— 查看当前会话"
        )

    async def terminate(self):
        self._sessions.clear()
        if self._client is not None:
            await self._client.aclose()
            self._client = None
