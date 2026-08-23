# -*- coding: utf-8 -*-
"""
BlogWriter 纯逻辑核心：命令解析、markdown 生成、请求构造、响应解析。
不依赖 AstrBot，可独立单元测试。
"""

import base64
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

SHANGHAI_TZ = timezone(timedelta(hours=8))


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI_TZ).replace(tzinfo=None)


SESSION_TIMEOUT = timedelta(minutes=30)
MAX_PATH_SUFFIX = 10

COMMANDS = ("动态", "笔记", "足迹", "友链", "相册", "账单", "日程", "生日", "纪念日", "影视", "导航", "提醒", "发布", "取消", "状态", "帮助")

BILL_CATEGORIES = ["餐饮", "交通", "住房", "工资", "居家生活", "交流通讯", "食品酒水", "职业收入", "人情收礼", "其他"]
BILL_ACCOUNTS = ["微信", "支付宝", "银行卡", "现金", "其他"]
SCHEDULE_PRIORITIES = ["none", "low", "medium", "high"]

# ---------- 命令解析 ----------

Parsed = Tuple[Optional[str], List[str], str]


def parse_message(text: str) -> Parsed:
    """解析用户消息。返回 (命令名或 None, 参数列表, 原始正文)。"""
    text = (text or "").strip()
    if not text:
        return None, [], text
    if text.startswith("/"):
        text = text[1:].strip()
    parts = re.split(r"\s+", text)
    head = parts[0].lstrip("/")
    if head in COMMANDS:
        return head, parts[1:], text
    return None, [], text


def parse_dynamic(args: List[str]) -> Tuple[Optional[str], List[str]]:
    """/动态 内容 [#标签...] → (内容, 自定义标签列表)；内容为空返回 (None, [])。"""
    content = " ".join(args).strip()
    if not content:
        return None, []
    clean, tags = extract_tags(content)
    return (clean or None), tags


def parse_note(args: List[str], default_dir: str) -> Tuple[str, str]:
    """/笔记 [分类] 标题 → (分类, 标题)。一个参数时分类用默认。"""
    if len(args) >= 2:
        return args[0].strip(), " ".join(args[1:]).strip()
    if len(args) == 1:
        return default_dir, args[0].strip()
    return default_dir, ""


def parse_place(args: List[str]) -> Tuple[str, str, str, List[str]]:
    """/足迹 省 地点 体验 [#标签...] → (省, 地点, 体验, 自定义标签)。"""
    if len(args) == 0:
        return "", "", "", []
    if len(args) == 1:
        return "", args[0].strip(), "", []
    if len(args) == 2:
        return args[0].strip(), args[1].strip(), "", []
    province, city = args[0].strip(), args[1].strip()
    experience, tags = extract_tags(" ".join(args[2:]).strip())
    return province, city, experience, tags


def parse_album(args: List[str]) -> str:
    """/相册 相册名 → 相册名（args 拼接、去空白）。名字为空返回空串。"""
    return " ".join(a.strip() for a in args if a.strip()).strip()


def parse_album_frontmatter(md: str) -> Tuple[str, str]:
    """从相册 md 文本提取 (title, imgbedFolder)。行级正则解析，容错（引号可带可不带）。

    用于「按 title 判断相册是否存在」：博客相册文件名与 title 不一定相同（如
    xiangce1.md 的 title 是「测试相册」），必须读内容才能对齐。
    """
    title, folder = "", ""
    for line in (md or "").splitlines():
        s = line.strip()
        m = re.match(r"^title\s*:\s*(.*)$", s)
        if m:
            title = m.group(1).strip().strip("\"'")
        m2 = re.match(r"^imgbedFolder\s*:\s*(.*)$", s)
        if m2:
            folder = m2.group(1).strip().strip("\"'")
    return title, folder


def extract_tags(text: str) -> Tuple[str, List[str]]:
    """从文本中提取 #标签 并移除。返回 (清理后的文本, 标签列表，保持出现顺序去重)。"""
    if not text:
        return "", []
    pattern = re.compile(r"#([^\s#]+)")
    tags = []
    seen = set()
    for m in pattern.finditer(text):
        tag = m.group(1).strip()
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    clean = pattern.sub("", text)
    # 清理因移除标签产生的多余空白与行内空白
    lines = [re.sub(r"\s{2,}", " ", ln).strip() for ln in clean.split("\n")]
    clean = "\n".join(ln for ln in lines if ln)
    return clean, tags


# ---------- 文件名与 id ----------

def gen_moment_id(now: datetime = None) -> str:
    now = now or now_shanghai()
    return "ext-" + str(int(now.timestamp() * 1000))


def format_moment_published(now: datetime = None) -> str:
    now = now or now_shanghai()
    return now.strftime("%Y-%m-%d %H:%M:%S")


def moment_filename(day: datetime) -> str:
    return day.strftime("%Y-%m-%d")


def note_filename(day: datetime) -> str:
    # 对齐现有数据：2026年6月1日（无补零）
    return "{}年{}月{}日".format(day.year, day.month, day.day)


def place_filename(day: datetime) -> str:
    return day.strftime("%Y-%m-%d")


def with_suffix(name: str, ext: str, index: int) -> str:
    """冲突后缀：xxx、xxx-1、xxx-2 ..."""
    if index == 0:
        return name + ext
    return "{}-{}{}".format(name, index, ext)


# ---------- markdown 生成 ----------

def _dump_yaml(
    data: Dict[str, Any],
    quote_all: bool = False,
    quote_keys: Optional[set] = None,
    inline_keys: Optional[set] = None,
) -> str:
    """生成 YAML frontmatter（仅支持本项目用到的简单类型）。

    - quote_all=True：所有字符串值强制双引号（date/datetime 形态除外），
      对齐博客 bills/schedules/places 最新文件风格（PagesCMS 生成格式）。
    - quote_keys：仅指定 key 强制双引号（quote_all 的按 key 版本）。
    - inline_keys：指定 key 的非空列表输出行内数组（如 tags: ["a", "b"]）。
    """
    quote_keys = quote_keys or set()
    inline_keys = inline_keys or set()

    def _quoted(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return '"{}"'.format(escaped)

    def _need_quote(key: str) -> bool:
        return quote_all or key in quote_keys

    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, list):
            if not value:
                lines.append("{}: []".format(key))
            elif key in inline_keys:
                items = []
                for item in value:
                    s = str(item)
                    items.append(_quoted(s) if _need_quote(key) else _yaml_str(s))
                lines.append("{}: [{}]".format(key, ", ".join(items)))
            else:
                lines.append("{}:".format(key))
                for item in value:
                    s = str(item)
                    rendered = _quoted(s) if _need_quote(key) else _yaml_str(s)
                    lines.append("  - {}".format(rendered))
        elif isinstance(value, bool):
            lines.append("{}: {}".format(key, "true" if value else "false"))
        elif isinstance(value, (int, float)):
            lines.append("{}: {}".format(key, value))
        elif isinstance(value, str):
            if _is_datetime_str(value):
                # date/datetime 形态一律不加引号（对齐现有数据：2024-10-02 14:19:00 / 2026-08-23）
                lines.append("{}: {}".format(key, value))
            elif _need_quote(key):
                lines.append("{}: {}".format(key, _quoted(value)))
            else:
                lines.append("{}: {}".format(key, _yaml_str(value)))
        else:
            lines.append("{}: {}".format(key, value))
    lines.append("---")
    return "\n".join(lines)


def _is_datetime_str(value: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?$", value.strip()))


def _is_url(value: str) -> bool:
    return bool(re.match(r"^https?://", value.strip()))


def _yaml_str(value: str) -> str:
    value = value.strip()
    if not value:
        return '""'
    # 纯数字字符串（如 "2026"）必须加引号，否则 YAML 解析成 int 导致 zod string 校验失败
    if re.match(r"^-?\d+$", value):
        return '"{}"'.format(value)
    if _is_datetime_str(value):
        return value
    if _is_url(value):
        # URL 可裸写（对齐现有数据风格，如 - https://img.tsh520.cn/file/places/xxx.jpg）
        return value
    if re.search(r"[:#\"'\[\]\{\},&*!|>%@`]|\s|^[-?]", value) or value in ("true", "false", "null", "~"):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return '"{}"'.format(escaped)
    return value


def build_moment_md(
    content: str,
    image_urls: List[str],
    tags: List[str],
    now: datetime = None,
) -> str:
    """对齐现有 moments 格式：图片在 frontmatter 的 images 数组，正文只放文字。

    2026-08-13 起博客不再按条写 author/avatar（content.config.ts 已提供 schema 默认值，
    全部旧文件已清理），故这里不再生成这两个字段。
    """
    now = now or now_shanghai()
    fm = {
        "published": format_moment_published(now),
        "tags": tags,
    }
    if image_urls:
        fm["images"] = list(image_urls)
    return _dump_yaml(fm) + "\n\n" + content.strip() + "\n"


def build_note_md(name: str, content: str, image_urls: List[str], day: datetime = None) -> str:
    day = day or now_shanghai()
    fm = {"date": day.strftime("%Y-%m-%d"), "name": name}
    body = content.strip()
    for url in image_urls:
        body = body + "\n\n![{}]({})".format(_image_alt(url), url)
    return _dump_yaml(fm) + "\n\n" + body + "\n"


def build_place_md(
    province: str,
    city: str,
    experience: str,
    photos: List[str],
    lat: float,
    lng: float,
    tags: List[str],
    day: datetime = None,
) -> str:
    """对齐博客最新足迹格式（2026-08-22 参照 src/content/life/places/）：
    新增 description 固定句式、不再写 visitCount（schema 默认 1）、
    字符串带引号、tags 行内数组、photos 多行带引号。
    """
    day = day or now_shanghai()
    fm: Dict[str, Any] = {
        "description": "记录在{}{}的足迹。".format(province, city),
        "province": province,
        "city": city,
    }
    if experience:
        fm["experience"] = experience
    fm["date"] = day.strftime("%Y-%m-%d")
    fm["lat"] = round(lat, 6)
    fm["lng"] = round(lng, 6)
    fm["tags"] = tags
    if photos:
        fm["photos"] = list(photos)
    return _dump_yaml(fm, quote_all=True, inline_keys={"tags"}) + "\n\n\n"


def build_album_md(title: str, folder: str, day: datetime = None) -> str:
    """对齐现有相册格式（2026-08-13 图床化惯例 + subtitle）：
    title/subtitle/date + imgbedFolder（带引号，对齐现有文件如 2026-spring.md）。
    照片不进 md：博客详情页运行时从图床目录动态拉图。
    """
    day = day or now_shanghai()
    fm = {
        "title": title,
        "subtitle": "记录{}".format(title),
        "date": day.strftime("%Y-%m-%d"),
        "imgbedFolder": folder,
    }
    return _dump_yaml(fm, quote_keys={"imgbedFolder"}) + "\n\n\n"


def _image_alt(url: str) -> str:
    return url.rsplit("/", 1)[-1][:40]


# ---------- GitHub 路径 ----------

def github_base(repo: str, path: str, branch: str) -> str:
    return "https://api.github.com/repos/{}/contents/{}?ref={}".format(repo, _quote_path(path), branch)


def github_put_url(repo: str, path: str) -> str:
    return "https://api.github.com/repos/{}/contents/{}".format(repo, _quote_path(path))


def _quote_path(path: str) -> str:
    import urllib.parse

    return urllib.parse.quote(path)


def build_github_put_body(message: str, content_bytes: bytes, branch: str) -> str:
    return json.dumps(
        {
            "message": message,
            "content": base64.b64encode(content_bytes).decode("ascii"),
            "branch": branch,
        }
    )


def parse_github_put_response(status: int, body: str) -> Tuple[bool, str]:
    """返回 (成功, 错误信息)。status 201/200 → 成功。"""
    if status in (200, 201):
        return True, ""
    if status == 409 or status == 422:
        return False, "CONFLICT"
    try:
        data = json.loads(body)
        msg = data.get("message", body[:200])
    except (ValueError, AttributeError):
        msg = body[:200]
    return False, msg


# ---------- 图床 ----------

def build_imgbed_upload(url: str, filename: str, data: bytes) -> Dict[str, Any]:
    """构造 multipart 上传请求（手写 boundary，不依赖 requests）。"""
    boundary = "----BlogWriterBoundary" + str(int(time.time() * 1000))
    lines = []
    lines.append("--" + boundary)
    lines.append('Content-Disposition: form-data; name="file"; filename="{}"'.format(filename))
    lines.append("Content-Type: application/octet-stream")
    lines.append("")
    body = ("\r\n".join(lines)).encode("utf-8") + b"\r\n" + data + b"\r\n"
    body += ("--" + boundary + "--\r\n").encode("utf-8")
    return {
        "url": url,
        "headers": {"Content-Type": "multipart/form-data; boundary=" + boundary},
        "body": body,
    }


def upload_url_with_return_format(url: str) -> str:
    """在 /upload 地址上追加 returnFormat=full，让服务端直接返回完整链接。"""
    url = (url or "").strip()
    sep = "&" if "?" in url else "?"
    return url + sep + "returnFormat=full"


def build_upload_url(url: str, folder: str = "") -> str:
    """构造上传地址：追加 returnFormat=full 与 uploadFolder（已存在的参数不重复追加）。"""
    import urllib.parse

    url = (url or "").strip()
    params = []
    if not re.search(r"[?&]returnFormat=", url):
        params.append("returnFormat=full")
    folder = (folder or "").strip().strip("/")
    if folder and not re.search(r"[?&]uploadFolder=", url):
        # 斜杠不编码，与官方文档示例 uploadFolder=img/test 一致
        params.append("uploadFolder=" + urllib.parse.quote(folder, safe="/"))
    if params:
        sep = "&" if "?" in url else "?"
        url = url + sep + "&".join(params)
    return url


def upload_base_host(url: str) -> str:
    """从上传地址提取主机名，用于把 /file/xxx 拼成完整链接。"""
    import urllib.parse

    return urllib.parse.urlparse(url).netloc


def parse_imgbed_response(status: int, body: str, base_host: str = "") -> Tuple[bool, str]:
    """解析图床上传响应。返回 (成功, 图片 URL 或错误信息)。

    官方文档（src/api/upload.md）：成功响应为数组，如
    [{"src": "/file/abc123_image.jpg", "publicUrl": "https://img.example.com/..."}]
    """
    if status >= 400:
        return False, "图床返回 HTTP {}".format(status)
    try:
        data = json.loads(body)
    except ValueError:
        return False, "图床响应不是 JSON：{}".format(body[:120])
    # 数组格式（新版）
    if isinstance(data, list) and data:
        item = data[0]
        if isinstance(item, dict):
            src = str(item.get("src") or item.get("publicUrl") or "").strip()
            if src:
                if src.startswith("http"):
                    return True, src
                if src.startswith("/") and base_host:
                    return True, "https://{}{}".format(base_host, src)
                if base_host:
                    return True, "https://{}/{}".format(base_host, src.lstrip("/"))
                return True, src
    # 兼容旧格式 {code, data:{url}}
    if isinstance(data, dict):
        code = data.get("code")
        if code is not None and code != 200:
            return False, "图床返回 code={}：{}".format(code, data.get("message", ""))
        d = data.get("data")
        if isinstance(d, str):
            return True, d
        if isinstance(d, dict):
            url = d.get("url") or d.get("link") or d.get("src")
            if url:
                return True, str(url)
    return False, "图床响应缺少文件地址：{}".format(body[:120])


# ---------- 高德 ----------

def build_amap_url(address: str, key: str) -> str:
    import urllib.parse

    return "https://restapi.amap.com/v3/geocode/geo?address={}&key={}".format(
        urllib.parse.quote(address), urllib.parse.quote(key)
    )


def parse_amap_response(status: int, body: str) -> Tuple[bool, Tuple[float, float]]:
    """返回 (成功, (lat, lng))。"""
    if status >= 400:
        return False, None
    try:
        data = json.loads(body)
    except ValueError:
        return False, None
    if data.get("status") != "1" or data.get("count", "0") == "0":
        return False, None
    geocodes = data.get("geocodes") or []
    if not geocodes:
        return False, None
    location = (geocodes[0].get("location") or "").strip()
    if not location:
        return False, None
    parts = location.split(",")
    if len(parts) != 2:
        return False, None
    try:
        lng, lat = float(parts[0]), float(parts[1])
    except ValueError:
        return False, None
    return True, (lat, lng)


# ---------- TMDB 影视 ----------

TMDB_API_BASE_DEFAULT = "https://api.themoviedb.org"
TMDB_IMAGE_BASE_DEFAULT = "https://image.tmdb.org/t/p"


def build_tmdb_search_url(query: str, api_key: str, api_base: str = "") -> str:
    """构造 TMDB /search/multi 搜索地址（电影+剧集一起搜，中文 zh-CN）。"""
    import urllib.parse

    base = (api_base or TMDB_API_BASE_DEFAULT).strip().rstrip("/")
    return "{}/3/search/multi?api_key={}&query={}&language=zh-CN&include_adult=false".format(
        base, urllib.parse.quote(api_key), urllib.parse.quote(query)
    )


def tmdb_poster_url(poster_path: str, image_base: str = "", size: str = "w500") -> str:
    """海报完整 URL：image_base 默认 https://image.tmdb.org/t/p，尺寸 w500。"""
    base = (image_base or TMDB_IMAGE_BASE_DEFAULT).strip().rstrip("/")
    path = (poster_path or "").strip()
    if not path.startswith("/"):
        path = "/" + path
    return "{}/{}/{}".format(base, size, path.lstrip("/")) if path != "/" else ""


def parse_tmdb_search_response(status: int, body: str) -> Tuple[Optional[Dict], str]:
    """解析 TMDB /search/multi 响应，取第一个 movie/tv 条目（跳过 person 等）。

    返回 (data, err)。data：
      media_type(movie/tv)、title(中文名，无则原名)、original_title、year、
      overview(截断)、vote_average、poster_path
    subcategory 映射：movie→movie、tv→tv。
    """
    if status == 401:
        return None, "TMDB 认证失败（HTTP 401）：请检查 tmdb_api_key"
    if status >= 400:
        return None, "TMDB 返回 HTTP {}".format(status)
    try:
        data = json.loads(body)
    except ValueError:
        return None, "TMDB 响应不是 JSON：{}".format((body or "")[:120])
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        media_type = str(item.get("media_type") or "")
        if media_type not in ("movie", "tv"):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        original = str(item.get("original_title") or item.get("original_name") or "").strip()
        date_str = str(item.get("release_date") or item.get("first_air_date") or "").strip()
        if not title and not original:
            continue
        return {
            "media_type": media_type,
            "title": title or original,
            "original_title": original,
            "year": date_str[:4],
            "overview": str(item.get("overview") or "").strip()[:60],
            "vote_average": item.get("vote_average"),
            "poster_path": str(item.get("poster_path") or "").strip(),
        }, ""
    return None, "TMDB 未找到该片（换个片名试试，或检查网络）"


def parse_media_score(text: str) -> Optional[int]:
    """解析会话内评分行：评分 8 / 打分 9 / 8分 / 十分制 8.5 → int(0-10)。非评分行返回 None。"""
    m = re.match(r"^(?:评分|打分|分数|得分)\s*[:：]?\s*(\d{1,2})(?:\.\d)?\s*(?:分|片|星)?\s*$", (text or "").strip())
    if not m:
        m = re.match(r"^(\d{1,2})(?:\.\d)?\s*分\s*$", (text or "").strip())
    if not m:
        return None
    try:
        score = int(m.group(1))
    except ValueError:
        return None
    return max(0, min(10, score))


def build_bangumi_md(
    title: str,
    image_url: str,
    subcategory: str,
    score: Optional[int],
    tags: List[str],
    comment: str,
    name_cn: str = "",
    now: datetime = None,
) -> str:
    """生成影视条目 md，对齐博客现有 src/content/bangumi/anime/ 文件格式：
    title/name_cn/category/subcategory/status/image/score/tags/published + 评论正文。
    影视与动画同放 anime 子目录、category: anime（对齐现有数据，如 侏罗纪世界.md）。
    """
    now = now or now_shanghai()
    fm: Dict[str, Any] = {"title": title}
    if name_cn and name_cn != title:
        fm["name_cn"] = name_cn
    fm["category"] = "anime"
    fm["subcategory"] = subcategory if subcategory in ("movie", "tv") else "movie"
    fm["status"] = 2  # 看过
    fm["image"] = image_url
    if score is not None:
        fm["score"] = score
    if tags:
        fm["tags"] = tags
    fm["published"] = now.strftime("%Y-%m-%d")
    body = (comment or "").strip()
    return _dump_yaml(fm) + "\n\n" + body + ("\n" if body else "")


# ---------- 导航网站（daohang）----------

XXAPI_ICO_BASE = "https://v2.xxapi.cn/api/ico"


def build_xxapi_ico_url(site_url: str) -> str:
    """构造 xxapi 图标获取地址（博客 scripts/添加导航/index.js 同款 API，无需 key）。"""
    import urllib.parse

    return "{}/api/ico?url={}".format(
        XXAPI_ICO_BASE.rsplit("/api/", 1)[0], urllib.parse.quote(site_url, safe="")
    )


def parse_xxapi_ico_response(status: int, body: str) -> Tuple[Optional[str], str]:
    """解析 xxapi ico 响应：{"code":200,"data":"<图标直链>"}。返回 (图标 URL, err)。"""
    if status >= 400:
        return None, "图标接口 HTTP {}".format(status)
    try:
        data = json.loads(body)
    except ValueError:
        return None, "图标接口响应异常"
    if data.get("code") == 200 and data.get("data"):
        icon_url = str(data["data"]).strip()
        if icon_url.startswith("http"):
            return icon_url, ""
        return None, "图标接口返回的不是链接"
    return None, "未获取到图标"


def site_host(url: str) -> str:
    """从网址提取域名：https://app.pagescms.org/x → app.pagescms.org"""
    u = (url or "").strip()
    m = re.match(r"^https?://([^/?#]+)", u)
    if m:
        return m.group(1).lower()
    # 无 scheme：取第一个 / 前的部分
    return u.split("/", 1)[0].lower()


def daohang_slug(url: str) -> str:
    """导航文件名：域名点转横线（对齐现有 app-pagescms-org.md / xxapi-cn.md 命名）。"""
    host = site_host(url)
    slug = host.replace(".", "-")
    return clean_filename_part(slug, fallback="site")


def parse_daohang_text(text: str) -> Dict[str, str]:
    """解析导航键值对文本（会话内逐行/多行发送）。

    别名：名称/名字/网站名/name/title→name；分类/类别/category→category；
    描述/简介/介绍/description/desc→description；颜色/色彩/color→color。
    """
    aliases = {
        "name": ["名称", "名字", "网站名称", "网站名", "站点名称", "站点名", "标题", "name", "title"],
        "category": ["分类", "类别", "类目", "category"],
        "description": ["描述", "简介", "介绍", "描述信息", "description", "desc", "descr"],
        "color": ["颜色", "色彩", "色值", "color"],
    }
    result: Dict[str, str] = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = re.match(r"^([^:：\s]{1,12})\s*[:：]\s*(.+)$", line)
        if not m:
            continue
        key_raw = m.group(1).strip().lower()
        value = m.group(2).strip()
        for field, keys in aliases.items():
            if key_raw in keys or key_raw in [k.lower() for k in keys]:
                if value:
                    result[field] = value
                break
    return result


def build_daohang_md(
    name: str,
    url: str,
    category: str,
    icon_url: str = "",
    description: str = "",
    tags: Optional[List[str]] = None,
    color: str = "",
    body: str = "",
) -> str:
    """生成导航条目 md，对齐现有 src/content/daohang/ 文件格式：
    name/url/icon/description/category/tags(行内无引号)/color(带引号)。featured/order 用户不指定不写。
    """
    fm: Dict[str, Any] = {"name": name, "url": url}
    if icon_url:
        fm["icon"] = icon_url
    if description:
        fm["description"] = description
    fm["category"] = category or "未分类"
    if tags:
        fm["tags"] = list(tags)
    if color:
        fm["color"] = color
    return _dump_yaml(fm, inline_keys={"tags"}) + "\n\n" + (body or "").strip() + ("\n" if (body or "").strip() else "")


# ---------- 友链解析 ----------

FRIEND_KEY_ALIASES = {
    "title": [
        # 中文
        "站点名称", "网站名称", "名称", "名字", "昵称", "站点名", "网站名", "博客名",
        "博客名称", "博主", "博主名", "博主名称", "网站名字", "站点名字", "网站昵称",
        # 英文（Butterfly 等主题格式）
        "name", "title", "blogname", "blog_name", "site_name", "nickname",
    ],
    "desc": [
        # 中文
        "描述", "简介", "站点描述", "网站描述", "介绍", "站点介绍", "网站介绍",
        "一句话介绍", "签名", "标语", "说明", "备注", "博主简介", "博客简介", "描述文字",
        # 英文
        "desc", "descr", "description", "intro", "introduction", "slogan", "about",
    ],
    "siteurl": [
        # 中文
        "链接", "地址", "网址", "站点链接", "网站链接", "站点地址", "网站地址",
        "博客链接", "博客地址", "主页", "主页链接", "域名", "网站", "站点",
        # 英文
        "link", "url", "siteurl", "site_url", "website", "homepage", "blog_url", "href",
    ],
    "imgurl": [
        # 中文
        "头像", "头像链接", "头像地址", "头像图片", "图标", "图标链接", "图片",
        "图片链接", "头像url", "头像图片链接", "头像地址链接",
        # 英文
        "avatar", "imgurl", "img_url", "icon", "logo", "image", "picture", "avatar_url",
    ],
}

_IMAGE_URL_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def parse_friend_text(text: str) -> Dict[str, str]:
    """解析友链键值对文本（兼容多种键名、多种分隔符、乱序、缺字段）。

    支持：
      站点名称: 团子和蛋糕
      站点描述：如果你喜欢那么欢迎来到我的世界！
      站点链接 https://blog.tsh520.cn
    也支持无键名行：纯 URL 行按 链接/头像 自动归类。
    """
    result: Dict[str, str] = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip().rstrip("，,")
        if not line:
            continue

        def lookup(key: str) -> Optional[str]:
            kl = key.lower()
            for f, aliases in FRIEND_KEY_ALIASES.items():
                if kl in aliases or kl in [a.lower() for a in aliases]:
                    return f
            return None

        field, value = None, ""
        # 形式 1：key: value / key：value
        m = re.match(r"^([^:\s：]{1,24})\s*[:：]\s*(.+)$", line)
        if m:
            field = lookup(m.group(1).strip())
            value = m.group(2).strip()
        else:
            # 形式 2：key value（单空格分隔，仅当 key 命中别名表时）
            m2 = re.match(r"^([^\s]{1,24})\s+(.+)$", line)
            if m2:
                field = lookup(m2.group(1).strip())
                if field:
                    value = m2.group(2).strip()
        if field and value:
            result[field] = value
            continue
        # 形式 3：未知键名但冒号后是完整 URL —— 按值特征归类（键名别名未收录也能兜住）
        m3 = re.match(r"^[^:\s：]{1,24}\s*[:：]\s*(.+)$", line)
        if m3:
            v3 = m3.group(1).strip()
            if v3.startswith(("http://", "https://")):
                line = v3
        # 无键名行：按内容特征归类
        if line.startswith(("http://", "https://")):
            if any(line.lower().endswith(e) for e in _IMAGE_URL_EXTS):
                result.setdefault("imgurl", line)
            else:
                result.setdefault("siteurl", line)
        elif line.startswith("/"):
            result.setdefault("imgurl", line)
        else:
            result.setdefault("title", line)
    return result


def validate_friend_data(data: Dict[str, str]) -> Tuple[bool, str]:
    """校验友链字段。返回 (是否通过, 缺失字段提示)。"""
    title = (data.get("title") or "").strip()
    siteurl = (data.get("siteurl") or "").strip()
    if not title:
        return False, "缺少「站点名称」"
    if not siteurl:
        return False, "缺少「站点链接」（https:// 开头的地址）"
    if not siteurl.startswith(("http://", "https://")):
        return False, "站点链接必须以 http:// 或 https:// 开头"
    return True, ""


def build_friend_md(
    title: str,
    desc: str,
    siteurl: str,
    imgurl: str,
    tags: List[str],
    weight: int = 0,
    enabled: bool = True,
    added: Optional[str] = None,
) -> str:
    """对齐现有友链文件格式：weight 0、末尾 group 字段（schema enum friend/other，默认 other）。
    字符串不加引号（对齐现有 friends 文件风格）。"""
    if added is None:
        added = now_shanghai().strftime("%Y-%m-%d")
    fm = {
        "title": title,
        "imgurl": imgurl,
        "desc": desc,
        "siteurl": siteurl,
        "tags": tags,
        "weight": weight,
        "enabled": enabled,
        "added": added,
        "group": "other",
    }
    return _dump_yaml(fm) + "\n\n\n"


def clean_filename_part(name: str, fallback: str = "friend") -> str:
    """清洗用于文件名的名称：去掉非法字符与首尾空白。"""
    name = (name or "").strip()
    name = re.sub(r'[\\/:*?"<>|\s]+', "-", name)
    name = name.strip("-.")
    return name or fallback


def next_friend_index(existing_names: List[str]) -> int:
    """根据现有友链文件名（NN-name.md）计算下一个编号。"""
    max_index = 0
    for name in existing_names or []:
        m = re.match(r"^(\d+)", str(name))
        if m:
            max_index = max(max_index, int(m.group(1)))
    return max_index + 1


# ---------- 会话 ----------

class Session:
    def __init__(
        self,
        kind: str,
        meta: Dict[str, Any],
        created_at: datetime = None,
    ):
        self.kind = kind  # moment | note | place | friend | album
        self.meta = meta
        self.text_parts: List[str] = []
        # 元素为 (来源引用, 图片字节)。图片在收到消息时立即读取，避免临时文件被清理。
        self.images: List[Tuple[str, bytes]] = []
        self.created_at = created_at or now_shanghai()
        self.last_active = self.created_at

    def touch(self, now: datetime = None) -> None:
        self.last_active = now or now_shanghai()

    def expired(self, now: datetime = None, timeout: timedelta = None) -> bool:
        now = now or now_shanghai()
        return now - self.last_active > (timeout or SESSION_TIMEOUT)

    def add_text(self, text: str) -> None:
        self.text_parts.append(text)

    def add_image(self, ref: str, data: bytes) -> None:
        self.images.append((ref, data))

    def full_text(self) -> str:
        return "\n".join(p.strip() for p in self.text_parts if p.strip())


# ---------- 账单 / 日程 ----------

# 分类关键词映射（用于白名单分类匹配）
_BILL_CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "餐饮": ["餐饮", "午餐", "晚餐", "早餐", "早饭", "午饭", "晚饭", "夜宵", "吃饭", "外卖", "食堂", "餐厅", "饮食", "聚餐"],
    "交通": ["交通", "打车", "地铁", "公交", "出行", "机票", "火车", "高铁", "出租", "滴滴", "通勤", "车费", "路费"],
    "住房": ["住房", "房租", "房贷", "物业", "水电", "房费", "住房"],
    "工资": ["工资"],
    "居家生活": ["居家", "家用", "日用", "家居", "生活费", "居家生活"],
    "交流通讯": ["交流", "通讯", "话费", "流量", "宽带", "手机费"],
    "食品酒水": ["食品", "酒水", "零食", "饮料", "酒", "水果", "买菜", "超市"],
    "职业收入": ["职业收入", "奖金", "绩效", "提成", "收入", "兼职"],
    "人情收礼": ["人情", "收礼", "红包", "礼金", "请客", "随礼", "送礼"],
    "其他": [],
}

# 账单类型关键词
_BILL_EXPENSE_KEYWORDS = ["花了", "支出", "花费", "付款", "消费", "支付", "扣款", "买"]
_BILL_INCOME_KEYWORDS = ["工资", "收入", "到账", "收款", "入账", "奖金", "发工资"]
# 负债关键词（对齐博客 bill-adapter：liability 正数=借入新增，负数=还款减少）
_BILL_LIABILITY_BORROW_KEYWORDS = ["借款", "欠款", "欠了", "负债", "花呗", "白条"]
_BILL_LIABILITY_REPAY_KEYWORDS = ["还款", "偿还", "还了", "已还"]
# 账户别名（不在白名单但博客已有数据使用，如 liability 的 account: 花呗）
_BILL_ACCOUNT_ALIASES = ["花呗", "白条", "信用卡"]
# 显式类型前缀（/账单 支出|收入|负债 <内容>）
_BILL_TYPE_PREFIX = {
    "支出": "expense",
    "消费": "expense",
    "花费": "expense",
    "收入": "income",
    "负债": "liability",
    "借款": "liability",
    "还款": "liability",
}


def _parse_bill_date(text: str, now: datetime) -> datetime:
    """从文本中提取日期关键词，返回对应的 datetime（00:00:00）"""
    m = re.search(r"(今天|明天|昨天|后天|\d{4}-\d{2}-\d{2}|\d{1,2}月\d{1,2}日)", text)
    if not m:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    token = m.group(1)
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if token == "今天":
        return base
    if token == "明天":
        return base + timedelta(days=1)
    if token == "昨天":
        return base - timedelta(days=1)
    if token == "后天":
        return base + timedelta(days=2)
    if re.match(r"^\d{4}-\d{2}-\d{2}$", token):
        try:
            dt = datetime.strptime(token, "%Y-%m-%d")
            return dt.replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            return base
    # m月d日
    m2 = re.match(r"^(\d{1,2})月(\d{1,2})日$", token)
    if m2:
        try:
            month = int(m2.group(1))
            day = int(m2.group(2))
            return datetime(now.year, month, day)
        except ValueError:
            return base
    return base


def _detect_bill_category(text: str) -> str:
    # 优先精确命中分类名本身
    for cat in BILL_CATEGORIES:
        if cat in text:
            return cat
    # 再通过关键词映射
    for cat, keywords in _BILL_CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw and kw in text:
                return cat
    return "其他"


def _detect_bill_account(text: str) -> str:
    for acc in BILL_ACCOUNTS:
        if acc in text:
            return acc
    # 别名（如负债账户：花呗/白条/信用卡），对齐博客现有数据 account: 花呗
    for alias in _BILL_ACCOUNT_ALIASES:
        if alias in text:
            return alias
    return "其他"


def parse_bills_batch(text: str, now=None) -> Tuple[List[Dict], str]:
    """批量解析多条账单（用于一句含多个金额的口语，如“午餐30晚餐45打车12”）。"""
    now = now or now_shanghai()
    raw = (text or "").strip()
    if not raw:
        return [], "内容为空"
    # 按常见分隔符拆分：， 、 。 ； 换行 以及“和/与/ plus”
    parts = re.split(r"[，。,；;、\n]+", raw)
    # 若只有一段但含多个金额，按金额切分
    expanded: List[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # 若一段含多个金额，按金额切（保留金额前的文本）
        amounts = list(re.finditer(r"-?\d+(?:\.\d+)?\s*(?:块|元|￥)?", p))
        if len(amounts) > 1:
            # 按金额位置切分
            last = 0
            for idx, m in enumerate(amounts):
                start = max(0, m.start() - 10)
                # 向前找分隔
                seg_start = last
                if idx < len(amounts) - 1:
                    seg_end = amounts[idx + 1].start()
                    seg = p[seg_start:seg_end].strip(" ，,。")
                else:
                    seg = p[seg_start:].strip(" ，,。")
                if seg:
                    expanded.append(seg)
                last = m.end()
                # 下一次从金额后开始，避免重叠
            # 若切分后仍只有1段，说明金额紧密，改按金额数量直接拆
            if len(expanded) <= 1:
                expanded = [p]
        else:
            expanded.append(p)
    # 若拆分后仍只有1段但含多个金额，尝试按“和/与/，”再拆
    if len(expanded) == 1 and len(re.findall(r"-?\d+(?:\.\d+)?\s*(?:块|元|￥)?", raw)) > 1:
        # 按金额直接拆全文
        expanded = []
        for m in re.finditer(r"([^，。,；;、\n]*?-?\d+(?:\.\d+)?\s*(?:块|元|￥)?[^，。,；;、\n]*)", raw):
            seg = m.group(1).strip(" ，,。")
            if seg:
                expanded.append(seg)
    results: List[Dict] = []
    for seg in expanded:
        seg = seg.strip()
        if not seg:
            continue
        # 必须含金额才算一条账单
        if not re.search(r"-?\d+(?:\.\d+)?\s*(?:块|元|￥)?", seg):
            continue
        data, err = parse_bill(seg, now)
        if data:
            results.append(data)
    if not results:
        return [], "未识别到账单信息"
    return results, ""


def parse_bill(text: str, now=None) -> Tuple[Optional[Dict], str]:
    """解析账单自然语言。返回 (data, err)，err 为空表示成功。

    data 包含：title, amount, type(expense/income/liability), category, account, date(datetime), description

    类型判定（显式优先）：
    1. 首词为类型前缀（支出/消费/花费/收入/负债/借款/还款）+ 空格 → 显式指定
    2. 收入关键词（工资/收入/到账…）→ income
    3. 负债关键词（借款/欠款/负债/花呗… 或 还款/偿还…）→ liability
    4. 支出关键词或默认 → expense

    liability 符号（对齐博客现有数据与 bill-adapter：liability += amount）：
    还款为负（减少负债）、借入为正（新增负债），不取绝对值；category 固定「负债」。
    """
    now = now or now_shanghai()
    raw = (text or "").strip()
    if not raw:
        return None, "内容为空"

    # 显式类型前缀：首词为类型词（支出/收入/负债/借款/还款…）且后跟空格 → 移除后继续解析
    type_hint = None
    first_token = re.split(r"\s+", raw, 1)[0]
    if first_token in _BILL_TYPE_PREFIX and re.search(r"\s", raw):
        type_hint = _BILL_TYPE_PREFIX[first_token]
        raw = re.sub(r"^\S+\s+", "", raw, count=1).strip()
    if not raw:
        return None, "内容为空"

    # 金额提取：(-?\d+(\.\d+)?)\s*(块|元|￥)?
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:块|元|￥)?", raw)
    if not m:
        return None, "未识别到金额"
    try:
        amount_val = float(m.group(1))
    except ValueError:
        return None, "金额解析失败"

    # 类型判断（显式前缀 > 收入 > 负债 > 支出/默认）
    has_income = any(kw in raw for kw in _BILL_INCOME_KEYWORDS)
    has_repay = any(kw in raw for kw in _BILL_LIABILITY_REPAY_KEYWORDS)
    has_borrow = any(kw in raw for kw in _BILL_LIABILITY_BORROW_KEYWORDS)
    if type_hint:
        type_ = type_hint
    elif has_income:
        type_ = "income"
    elif has_repay or has_borrow:
        type_ = "liability"
    else:
        # 无显式关键词时默认 expense
        type_ = "expense"

    if type_ == "expense":
        amount = -abs(amount_val)
    elif type_ == "income":
        amount = abs(amount_val)
    else:  # liability：还款为负（减负债）、借入为正（增负债），不取绝对值
        amount = -abs(amount_val) if has_repay else abs(amount_val)
    # 保持整数类型
    if isinstance(amount, float) and amount == int(amount):
        amount = int(amount)

    if type_ == "liability":
        # 对齐博客现有负债数据：category 固定「负债」（tags 由 build_bill_md 按 category 自动生成）
        category = "负债"
    else:
        category = _detect_bill_category(raw)
    account = _detect_bill_account(raw)
    date_val = _parse_bill_date(raw, now)

    # 标题/描述提取：去除已知片段后剩余文本
    cleaned = raw
    # 去除日期词
    cleaned = re.sub(r"(今天|明天|昨天|后天|\d{4}-\d{2}-\d{2}|\d{1,2}月\d{1,2}日)", "", cleaned)
    # 去除账户
    for acc in BILL_ACCOUNTS:
        cleaned = cleaned.replace(acc, "")
    # 去除金额（含可选单位）
    cleaned = re.sub(r"(-?\d+(?:\.\d+)?)\s*(?:块|元|￥)?", "", cleaned)
    # 去除类型动词（保留类别名词如 工资 餐饮 午餐 等，避免误删标题）
    for kw in ["花了", "支出", "花费", "付款", "消费", "支付", "扣款"]:
        cleaned = cleaned.replace(kw, "")
    # 单独的 "发" 常见于 "发工资"
    cleaned = re.sub(r"\b发\b", "", cleaned)
    # 去除多余空白与标点
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，,。")
    cleaned = cleaned.strip()
    # 若清理后剩余为空或过短（单字），回退到类别或描述
    if not cleaned or len(cleaned) <= 1:
        if category != "其他":
            cleaned = category
        else:
            # 尝试从原文提取名词片段（去除数字与账户后的首个词）
            cleaned = raw
            cleaned = re.sub(r"(-?\d+(?:\.\d+)?)\s*(?:块|元|￥)?", "", cleaned)
            for acc in BILL_ACCOUNTS:
                cleaned = cleaned.replace(acc, "")
            cleaned = cleaned.replace("今天", "").replace("明天", "").replace("昨天", "").replace("后天", "")
            for kw in ["花了", "支出", "花费", "付款", "消费", "支付", "扣款", "发"]:
                cleaned = cleaned.replace(kw, "")
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，,。")
            if not cleaned:
                cleaned = category if category != "其他" else "账单"

    # 标题与描述：标题取清理后前 20 字符，描述取清理后全文
    title = cleaned[:20].strip() if cleaned else (category if category != "其他" else "账单")
    description = cleaned if cleaned else title

    data: Dict[str, Any] = {
        "title": title,
        "amount": amount,
        "type": type_,
        "category": category,
        "account": account,
        "date": date_val,
        "description": description,
    }
    return data, ""


# 日程相关常量
_SCHEDULE_PRIORITY_KEYWORDS: Dict[str, str] = {
    "高优": "high",
    "高优先级": "high",
    "高": "high",
    "紧急": "high",
    "重要": "high",
    "中优": "medium",
    "中优先级": "medium",
    "中": "medium",
    "低优": "low",
    "低优先级": "low",
    "低": "low",
}

_SCHEDULE_REPEATS = ["每天", "每日", "每周", "每月", "每年"]


def _parse_schedule_date(text: str, now: datetime) -> datetime:
    # 相对时间“2分钟后/3小时后”等视为今天，避免被误判为全天
    if re.search(r"(\d+\s*(?:分钟|分|小时|时|天|周)\s*后|半小时后)", text):
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    # 支持 今天/明天/后天/2026-08-24/8月24日/8月24/8.24/8-24
    m = re.search(r"(今天|明天|昨天|后天|\d{4}-\d{2}-\d{2}|\d{1,2}月\d{1,2}日?|\d{1,2}[.\-]\d{1,2})", text)
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if not m:
        return base
    token = m.group(1)
    if token == "今天":
        return base
    if token == "明天":
        return base + timedelta(days=1)
    if token == "昨天":
        return base - timedelta(days=1)
    if token == "后天":
        return base + timedelta(days=2)
    if re.match(r"^\d{4}-\d{2}-\d{2}$", token):
        try:
            dt = datetime.strptime(token, "%Y-%m-%d")
            return dt.replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            return base
    m2 = re.match(r"^(\d{1,2})月(\d{1,2})日$", token)
    if m2:
        try:
            month = int(m2.group(1))
            day = int(m2.group(2))
            return datetime(now.year, month, day)
        except ValueError:
            return base
    m3 = re.match(r"^(\d{1,2})月(\d{1,2})$", token)
    if m3:
        try:
            month = int(m3.group(1))
            day = int(m3.group(2))
            return datetime(now.year, month, day)
        except ValueError:
            return base
    m4 = re.match(r"^(\d{1,2})[.\-](\d{1,2})$", token)
    if m4:
        try:
            month = int(m4.group(1))
            day = int(m4.group(2))
            return datetime(now.year, month, day)
        except ValueError:
            return base
    return base


def _parse_schedule_time(text: str, base_date: datetime, now: datetime = None) -> Tuple[datetime, bool]:
    """解析时间，返回 (datetime, has_time)。支持绝对时间与相对时间 2分钟后/半小时后"""
    # 相对时间优先：2分钟后 / 3小时后 / 半小时后 等，基准用传入的 now（保持测试可控）
    _now = now or now_shanghai()
    m_rel = re.search(r"(\d+)\s*分钟后", text)
    if m_rel:
        try:
            mins = int(m_rel.group(1))
            dt = _now + timedelta(minutes=mins)
            return dt.replace(second=0, microsecond=0), True
        except ValueError:
            pass
    m_rel2 = re.search(r"(\d+)\s*小时后", text)
    if m_rel2:
        try:
            hours = int(m_rel2.group(1))
            dt = _now + timedelta(hours=hours)
            return dt.replace(second=0, microsecond=0), True
        except ValueError:
            pass
    m_rel3 = re.search(r"(\d+)\s*秒后", text)
    if m_rel3:
        try:
            secs = int(m_rel3.group(1))
            dt = _now + timedelta(seconds=secs)
            return dt.replace(second=0, microsecond=0), True
        except ValueError:
            pass
    if "半小时后" in text or "半个小时后" in text:
        dt = _now + timedelta(minutes=30)
        return dt.replace(second=0, microsecond=0), True
    # 匹配 (\d{1,2}[:点]\d{0,2}) 兼容冒号与中文点
    m = re.search(r"(\d{1,2})\s*[:：点]\s*(\d{1,2})?", text)
    has_time = False
    hour = 0
    minute = 0
    if m:
        has_time = True
        try:
            hour = int(m.group(1))
        except ValueError:
            hour = 0
        if m.group(2):
            try:
                minute = int(m.group(2))
            except ValueError:
                minute = 0
        else:
            minute = 0
        # 下午/晚上 换算：12 小时制转 24
        if any(kw in text for kw in ["下午", "晚上", "傍晚", "夜间"]):
            if 1 <= hour <= 11:
                hour += 12
        # 上午/凌晨不加
        hour = max(0, min(23, hour))
        minute = max(0, min(59, minute))
    return base_date.replace(hour=hour, minute=minute, second=0, microsecond=0), has_time


def _detect_schedule_priority(text: str) -> str:
    # 按关键词长度降序匹配，避免 "高" 误抢 "高优"
    for kw in sorted(_SCHEDULE_PRIORITY_KEYWORDS.keys(), key=len, reverse=True):
        if kw in text:
            return _SCHEDULE_PRIORITY_KEYWORDS[kw]
    return "none"


def _detect_schedule_location(text: str) -> str:
    # 优先 "在...开" 结构
    m = re.search(r"在\s*(.+?)\s*开", text)
    if m:
        loc = m.group(1).strip()
        # 去除尾部标点
        loc = loc.strip(" ，,。")
        if loc:
            return loc
    # 兜底：在 后取非空片段直到空白或标点
    m2 = re.search(r"在\s*([^\s，。,]+)", text)
    if m2:
        loc = m2.group(1).strip()
        # 若捕获中仍含 "开" 与后续，截断
        if "开" in loc:
            loc = loc.split("开")[0]
        return loc.strip(" ，,。")
    return ""


def _detect_schedule_repeat(text: str) -> str:
    for rep in _SCHEDULE_REPEATS:
        if rep in text:
            # 统一返回 "每周" 等短形式
            if rep == "每日":
                return "每天"
            return rep
    if "不重复" in text:
        return ""
    # 兼容 "每周重复" 等
    m = re.search(r"(每天|每周|每月|每年)", text)
    if m:
        return m.group(1)
    return ""


def _detect_remind_before(text: str) -> int:
    m = re.search(r"提前\s*(\d+)\s*分钟", text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    m2 = re.search(r"提前\s*(\d+)\s*分", text)
    if m2:
        try:
            return int(m2.group(1))
        except ValueError:
            pass
    return 10


def parse_schedule(text: str, now=None) -> Tuple[Optional[Dict], str]:
    """解析日程自然语言。返回 (data, err)。

    data 包含：title, date(datetime), priority, location, repeat, remind_before(int), allDay(bool)
    """
    now = now or now_shanghai()
    raw = (text or "").strip()
    if not raw:
        return None, "内容为空"
    # 兼容常见错别字
    raw = raw.replace("生气", "生日").replace("生如", "生日")

    base_date = _parse_schedule_date(raw, now)
    dt, has_time = _parse_schedule_time(raw, base_date, now)
    # 若未解析到时间且文本中无时间关键词，则视为全天事件
    all_day = not has_time

    priority = _detect_schedule_priority(raw)
    location = _detect_schedule_location(raw)
    repeat = _detect_schedule_repeat(raw)
    remind_before = _detect_remind_before(raw)

    # 标题提取：去除已识别片段
    cleaned = raw
    # 去除相对时间
    cleaned = re.sub(r"\d+\s*分钟后", "", cleaned)
    cleaned = re.sub(r"\d+\s*小时后", "", cleaned)
    cleaned = re.sub(r"\d+\s*秒后", "", cleaned)
    cleaned = re.sub(r"半小时后|半个小时后", "", cleaned)
    # 去除日期
    cleaned = re.sub(r"(今天|明天|昨天|后天|\d{4}-\d{2}-\d{2}|\d{1,2}月\d{1,2}日)", "", cleaned)
    # 去除时间段关键词下午等 + 时间本身
    cleaned = re.sub(r"(上午|下午|晚上|傍晚|凌晨|夜间)", "", cleaned)
    cleaned = re.sub(r"(\d{1,2})\s*[:：点]\s*(\d{1,2})?", "", cleaned)
    # 去除优先级词（按长度降序）
    for kw in sorted(_SCHEDULE_PRIORITY_KEYWORDS.keys(), key=len, reverse=True):
        cleaned = cleaned.replace(kw, "")
    # 去除地点片段
    if location:
        # 同时去除 "在"+location
        cleaned = cleaned.replace("在" + location, "")
        cleaned = cleaned.replace(location, "")
    else:
        # 无明确地点时仍尝试去除 "在..." 兜底
        cleaned = re.sub(r"在\s*[^\s，。,]+", "", cleaned)
    # 去除重复词
    for rep in _SCHEDULE_REPEATS + ["重复", "不重复"]:
        cleaned = cleaned.replace(rep, "")
    # 去除提醒词
    cleaned = re.sub(r"提前\s*\d+\s*分钟", "", cleaned)
    cleaned = re.sub(r"提前\s*\d+\s*分", "", cleaned)
    # 去除剩余动词与空白
    cleaned = cleaned.replace("开", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，,。")
    cleaned = cleaned.strip()
    if not cleaned:
        cleaned = "日程"
    title = cleaned[:30].strip()
    # 若标题过长含多词，取最后一段（通常为事件名）
    # 例如 "周会" 已经精简
    data: Dict[str, Any] = {
        "title": title,
        "date": dt,
        "priority": priority,
        "location": location,
        "repeat": repeat,
        "remind_before": remind_before,
        "allDay": all_day,
    }
    return data, ""


def parse_schedules_batch(text: str, now=None) -> Tuple[List[Dict], str]:
    """批量解析多条生日/日程（用于一句含多个“生日是农历X月X日”的情况）。直接扫描全文所有日期。

    对齐博客 schedules 新 schema（2026-08）：农历生日写 isLunar/lunarMonth/lunarDay
    （不存公历近似 date），公历生日存 date；农历转公历由博客端 lunar-javascript 完成。
    """
    now = now or now_shanghai()
    raw = (text or "").strip().replace("生气", "生日").replace("生如", "生日")
    if not raw:
        return [], "内容为空"
    # 关键词到 person 的映射
    person_map = {
        "我": "我",
        "我的": "我",
        "对象": "对象",
        "我对象": "对象",
        "对象是": "对象",
        "妈": "我妈",
        "妈妈": "我妈",
        "我妈": "我妈",
        "爸": "我爸",
        "爸爸": "我爸",
        "我爸": "我爸",
        "大姐": "大姐",
        "二姐": "二姐",
        "姐": "大姐",
    }
    is_lunar_all = "都是农历" in raw or "均为农历" in raw
    results: List[Dict] = []
    # 直接扫描全文所有日期，避免按逗号切分丢失“对象的是12.22”这类无“生日”关键词的条目
    for m_date in re.finditer(r"(\d{1,2})[.\-月](\d{1,2})日?", raw):
        date_start = m_date.start()
        # 向前取 20 字符找人物：取结束位置最靠近日期的词（同结束位置取更长词，
        # 避免“…12.14二姐4.4”里“姐”抢过“二姐”、“…8.24对象12.22”里“我”抢过“对象”）
        context = raw[max(0, date_start - 20) : date_start]
        person = ""
        best_end = -1
        best_len = -1
        for key, val in person_map.items():
            pos = context.rfind(key)
            if pos == -1:
                continue
            end = pos + len(key)
            if end > best_end or (end == best_end and len(key) > best_len):
                best_end = end
                best_len = len(key)
                person = val
        if not person:
            m_p = re.search(r"(\S{1,6})的是", context)
            if m_p:
                person = m_p.group(1).strip()
        try:
            month = int(m_date.group(1))
            day = int(m_date.group(2))
        except ValueError:
            continue
        is_lunar = is_lunar_all or "农历" in raw[max(0, date_start - 10) : m_date.end() + 10] or "农历" in context
        title = "{}生日".format(person) if person else "生日"
        item: Dict[str, Any] = {
            "title": title,
            "priority": "none",
            "location": "",
            "repeat": "每年",
            "remind_before": 10,
            "allDay": True,
            "category": "birthday",
            "person": person,
            "isLunar": is_lunar,
        }
        if is_lunar:
            item["lunarMonth"] = month
            item["lunarDay"] = day
        else:
            # 公历生日：已过今年则用明年
            try:
                dt = datetime(now.year, month, day)
                if dt < now.replace(hour=0, minute=0, second=0, microsecond=0):
                    dt = datetime(now.year + 1, month, day)
                item["date"] = dt
            except ValueError:
                continue
        results.append(item)
    if not results:
        return [], "未识别到生日信息"
    return results, ""


def schedule_filename(data: Dict, now: datetime = None) -> str:
    """日程文件名的日期部分：农历 → lunar-M-D（对齐 lunar-8-24-我的生日.md）；
    普通 → YYYY-MM-DD。"""
    if data.get("isLunar") and data.get("lunarMonth") and data.get("lunarDay"):
        return "lunar-{}-{}".format(data.get("lunarMonth"), data.get("lunarDay"))
    date_val = data.get("date")
    base = now or now_shanghai()
    if isinstance(date_val, datetime):
        return date_val.strftime("%Y-%m-%d")
    if isinstance(date_val, str) and date_val.strip():
        return date_val.strip()[:10]
    return base.strftime("%Y-%m-%d")


def parse_anniversary(text: str, now=None) -> Tuple[Optional[Dict], str]:
    """解析纪念日自然语言：标题 + 日期（公历/农历）+ 可选 @人物。

    示例：
      /纪念日 我和宝宝认识的纪念日 1月1日 @宝宝
      /纪念日 结婚纪念日 农历5月20
      /纪念日 领证纪念日 2026-05-20

    产出固定：repeat=每年、allDay=True、category=anniversary；
    农历日期存 isLunar/lunarMonth/lunarDay（无 date），公历存 date。
    """
    now = now or now_shanghai()
    raw = (text or "").strip()
    if not raw:
        return None, "内容为空"

    person = ""
    m_person = re.search(r"@(\S{1,10})", raw)
    if m_person:
        person = m_person.group(1).strip()
        raw = re.sub(r"@\S{1,10}", "", raw).strip()

    is_lunar = False
    has_year = False
    year = now.year
    month = day = None

    # 农历优先：农历5月20 / 农历5.20 / 农历5-20
    m_lunar = re.search(r"农历\s*(\d{1,2})\s*[.\-月]\s*(\d{1,2})\s*日?", raw)
    if m_lunar:
        is_lunar = True
        month = int(m_lunar.group(1))
        day = int(m_lunar.group(2))
    else:
        # 带年公历：2026-01-01 / 2026年1月1日 / 2026.1.1
        m_ymd = re.search(r"(\d{4})\s*[.\-年]\s*(\d{1,2})\s*[.\-月]\s*(\d{1,2})\s*日?", raw)
        if m_ymd:
            has_year = True
            year = int(m_ymd.group(1))
            month = int(m_ymd.group(2))
            day = int(m_ymd.group(3))
        else:
            # 月日公历：1月1日 / 1.1 / 1-1（年份用当前年）
            m_md = re.search(r"(\d{1,2})\s*[.\-月]\s*(\d{1,2})\s*日?", raw)
            if m_md:
                month = int(m_md.group(1))
                day = int(m_md.group(2))

    if month is None:
        return None, "未识别到日期（支持 1月1日 / 2026-01-01 / 农历5月20）"

    data: Dict[str, Any] = {
        "title": "",
        "priority": "none",
        "location": "",
        "repeat": "每年",
        "remind_before": 10,
        "allDay": True,
        "category": "anniversary",
        "person": person,
    }
    if is_lunar:
        data["isLunar"] = True
        data["lunarMonth"] = month
        data["lunarDay"] = day
        if not (1 <= month <= 12 and 1 <= day <= 30):
            return None, "农历日期超出范围（月 1-12，日 1-30）"
    else:
        try:
            data["date"] = datetime(year, month, day)
        except ValueError:
            return None, "公历日期无效：{}-{}-{}".format(year, month, day)

    # 标题 = 移除日期词、农历字样、@人物后的剩余文本
    cleaned = raw
    cleaned = re.sub(r"\d{4}\s*[.\-年]\s*\d{1,2}\s*[.\-月]\s*\d{1,2}\s*日?", "", cleaned)
    cleaned = re.sub(r"\d{1,2}\s*[.\-月]\s*\d{1,2}\s*日?", "", cleaned)
    cleaned = cleaned.replace("农历", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，,。")
    data["title"] = cleaned[:30].strip() or "纪念日"
    return data, ""


def build_bill_md(data: Dict, now=None) -> str:
    """生成账单 markdown。对齐博客最新 bills 格式（2026-08-23 参照）：
    字符串带双引号、date/amount 不带、tags 行内数组。"""
    now = now or now_shanghai()
    title = str(data.get("title") or data.get("description") or "账单").strip() or "账单"
    amount = data.get("amount", 0)
    type_ = data.get("type", "expense")
    if type_ not in ("income", "expense", "transfer", "liability"):
        type_ = "expense"
    category = str(data.get("category") or "其他").strip() or "其他"
    account = str(data.get("account") or "其他").strip() or "其他"
    date_val = data.get("date") or now
    if isinstance(date_val, datetime):
        date_str = date_val.strftime("%Y-%m-%d")
    else:
        date_str = str(date_val).strip() or now.strftime("%Y-%m-%d")
    description = str(data.get("description") or title).strip()
    tags = data.get("tags")
    if tags is None:
        tags = [category] if category else []
    # 确保 tags 为列表
    if isinstance(tags, str):
        tags = [tags]
    fm: Dict[str, Any] = {
        "title": title,
        "amount": amount,
        "type": type_,
        "category": category,
        "account": account,
        "date": date_str,
        "description": description,
        "tags": list(tags),
    }
    body = str(data.get("body") or description or title).strip()
    return _dump_yaml(fm, quote_all=True, inline_keys={"tags"}) + "\n\n" + body + "\n"


def build_schedule_md(data: Dict, now=None) -> str:
    """生成日程 markdown。对齐博客最新 schedules 格式（2026-08 参照）：

    - 字符串带双引号、date/allDay/isLunar 等不带；空 location/repeat/person 不写
    - 农历生日（isLunar）：不写 date，写 isLunar/lunarMonth/lunarDay，
      文件名用 lunar-M-D 前缀（对齐 lunar-8-24-我的生日.md）
    - 普通日程：全天只写日期，非全天写到秒
    """
    now = now or now_shanghai()
    title = str(data.get("title") or "日程").strip() or "日程"
    priority = str(data.get("priority") or "none").strip()
    if priority not in SCHEDULE_PRIORITIES:
        # 兼容中文优先级误传入
        priority = _SCHEDULE_PRIORITY_KEYWORDS.get(priority, "none")
        if priority not in SCHEDULE_PRIORITIES:
            priority = "none"
    status = str(data.get("status") or "todo").strip()
    if status not in ("todo", "done", "cancelled"):
        status = "todo"
    location = str(data.get("location") or "").strip()
    repeat = str(data.get("repeat") or "").strip()
    category = str(data.get("category") or "schedule").strip() or "schedule"
    if category not in ("schedule", "birthday", "anniversary", "holiday"):
        category = "schedule"
    person = str(data.get("person") or "").strip()
    fm: Dict[str, Any] = {
        "title": title,
    }
    is_lunar = bool(data.get("isLunar"))
    if is_lunar:
        # 农历：不写 date，由博客端 lunar-javascript 换算
        fm["allDay"] = bool(data.get("allDay", True))
    else:
        date_val = data.get("date") or now
        all_day = data.get("allDay")
        if isinstance(date_val, datetime):
            if all_day is None:
                all_day = date_val.hour == 0 and date_val.minute == 0 and date_val.second == 0
            date_str = date_val.strftime("%Y-%m-%d") if all_day else date_val.strftime("%Y-%m-%d %H:%M:%S")
        else:
            date_str = str(date_val).strip() or now.strftime("%Y-%m-%d")
            all_day = bool(data.get("allDay", False))
        fm["date"] = date_str
        fm["allDay"] = bool(all_day)
    fm["priority"] = priority
    fm["status"] = status
    if location:
        fm["location"] = location
    if repeat:
        fm["repeat"] = repeat
    fm["category"] = category
    if person:
        fm["person"] = person
    if is_lunar:
        fm["isLunar"] = True
        fm["lunarMonth"] = int(data.get("lunarMonth") or 1)
        fm["lunarDay"] = int(data.get("lunarDay") or 1)
    # 可选 endDate（仅非农历）
    if not is_lunar and data.get("endDate"):
        end_val = data.get("endDate")
        if isinstance(end_val, datetime):
            fm["endDate"] = end_val.strftime("%Y-%m-%d")
        else:
            fm["endDate"] = str(end_val)
    body = str(data.get("description") or data.get("body") or title).strip()
    return _dump_yaml(fm, quote_all=True) + "\n\n" + body + "\n"
