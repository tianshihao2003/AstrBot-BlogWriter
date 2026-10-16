# -*- coding: utf-8 -*-
"""
BlogWriter 纯逻辑核心：命令解析、markdown 生成、请求构造、响应解析。
不依赖 AstrBot，可独立单元测试。
"""

import base64
import json
import mimetypes
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

SHANGHAI_TZ = timezone(timedelta(hours=8))


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI_TZ).replace(tzinfo=None)


SESSION_TIMEOUT = timedelta(minutes=30)
MAX_PATH_SUFFIX = 10

COMMANDS = ("动态", "笔记", "足迹", "友链", "相册", "影视", "书籍", "导航", "转载", "发布", "取消", "状态", "帮助")

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
      对齐博客 地点/笔记本 等最新文件风格（PagesCMS 生成格式）。
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
    """对齐最新 notebooks 格式（2026-09-27）：图片进 frontmatter images 数组，正文只留文字，对齐 moments。"""
    day = day or now_shanghai()
    fm: Dict[str, Any] = {"date": day.strftime("%Y-%m-%d"), "name": name}
    if image_urls:
        fm["images"] = list(image_urls)
    body = content.strip()
    return _dump_yaml(fm) + "\n\n" + body + "\n"


def build_notebook_index_json(name: str) -> str:
    """新建笔记本时生成 _index.json（博客书架/归档页靠它识别笔记本，缺了整个文件夹不可见）。
    对齐现有文件风格：tab 缩进、中文字符不转义；封面留空由博客显示默认 emoji，用户可后续在 CMS 补。"""
    return json.dumps({"name": name}, ensure_ascii=False, indent="\t") + "\n"


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
    """对齐博客最新足迹格式（2026-08-22 参照 src/content/地点/）：
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

_MIME_MAP = {
    "webp": "image/webp",
    "heic": "image/heic",
    "heif": "image/heif",
    "ico": "image/x-icon",
    "svg": "image/svg+xml",
    "m4v": "video/x-m4v",
    "mov": "video/quicktime",
}


def _mime_for_filename(name: str) -> str:
    ext = (name.rsplit(".", 1)[-1].lower() if "." in name else "")
    if ext in _MIME_MAP:
        return _MIME_MAP[ext]
    guessed, _ = mimetypes.guess_type(name)
    if guessed and (guessed.startswith("image/") or guessed.startswith("video/")):
        return guessed
    return "image/jpeg"


def build_imgbed_upload(url: str, filename: str, data: bytes) -> Dict[str, Any]:
    """构造 multipart 上传请求（手写 boundary，不依赖 requests）。"""
    boundary = "----BlogWriterBoundary" + str(int(time.time() * 1000))
    lines = []
    lines.append("--" + boundary)
    lines.append('Content-Disposition: form-data; name="file"; filename="{}"'.format(filename))
    lines.append("Content-Type: {}".format(_mime_for_filename(filename)))
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
    score: Optional[int],
    tags: List[str],
    comment: str,
    name_cn: str = "",
    subcategory: str = "",
    category: str = "anime",
    now: datetime = None,
) -> str:
    """生成影视/书籍/游戏条目 md，对齐博客 bangumi 格式：
    title/name_cn/category/subcategory/status/image/score/tags/published + 评论正文。
    影视：category=anime + subcategory(movie/tv/anime/documentary)，放 anime/ 子目录（对齐现有数据）；
    游戏：category=game、无 subcategory，放 game/ 子目录（对齐现有 game/ 条目）；
    书籍：category=book、无 subcategory，放 book/ 根目录（手动书籍，区别于 weread 同步的分类子目录）。
    """
    now = now or now_shanghai()
    fm: Dict[str, Any] = {"title": title}
    if name_cn and name_cn != title:
        fm["name_cn"] = name_cn
    fm["category"] = category if category in ("anime", "book", "music", "game", "real") else "anime"
    if subcategory in ("movie", "tv", "anime", "documentary"):
        fm["subcategory"] = subcategory
    fm["status"] = 2  # 看过
    fm["image"] = image_url
    if score is not None:
        fm["score"] = score
    if tags:
        fm["tags"] = tags
    fm["published"] = now.strftime("%Y-%m-%d")
    body = (comment or "").strip()
    return _dump_yaml(fm) + "\n\n" + body + ("\n" if body else "")


_MEDIA_SUBCATEGORY_KEYWORDS = {"电影": "movie", "剧集": "tv", "电视剧": "tv", "电视": "tv", "movie": "movie", "tv": "tv"}


def parse_media_fields(text: str) -> Dict[str, str]:
    """解析影视/书籍会话内的键值对：名称: xxx（改标题）、类型: 电影|剧集（影视 subcategory）。

    返回 dict，可含 title / subcategory 键；非键值对返回空 dict。
    """
    result: Dict[str, str] = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        m = re.match(r"^(名称|名字|标题|片名|书名)\s*[:：]\s*(.+)$", line)
        if m:
            result["title"] = m.group(2).strip()
            continue
        m2 = re.match(r"^(类型|分类名)\s*[:：]\s*(.+)$", line)
        if m2:
            token = m2.group(2).strip().lower()
            if token in _MEDIA_SUBCATEGORY_KEYWORDS:
                result["subcategory"] = _MEDIA_SUBCATEGORY_KEYWORDS[token]
    return result


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

    别名：名称/名字/网站名/name/title→name；分类/类别/category→category（现为「文件夹名」）；
    描述/简介/介绍/description/desc→description。
    注：2026-09-21 博客导航精简为四字段（name/url/icon/description），color 已删除不再解析；
    同日 tags 又回归为可选字段，标签改走 #标签 收集，不经本函数。
    """
    aliases = {
        "name": ["名称", "名字", "网站名称", "网站名", "站点名称", "站点名", "标题", "name", "title"],
        "category": ["分类", "类别", "类目", "category"],
        "description": ["描述", "简介", "介绍", "描述信息", "description", "desc", "descr"],
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
    icon_url: str = "",
    description: str = "",
    body: str = "",
    tags: Optional[List[str]] = None,
) -> str:
    """生成导航条目 md，对齐博客最新格式（2026-09-21 v1.40.3 + tags 回归）：
    条目写 name/url + 可选 icon/description/tags（category 由文件夹决定，
    color/featured/order/image 已从 schema 删除，不再写入）。
    tags 非空时按块状列表输出（与 src/content/导航 下现有条目一致）：
        tags:
          - API
    """
    fm: Dict[str, Any] = {"name": name, "url": url}
    if icon_url:
        fm["icon"] = icon_url
    if description:
        fm["description"] = description
    clean_tags = [str(t).strip() for t in (tags or []) if str(t).strip()]
    if clean_tags:
        fm["tags"] = clean_tags
    return _dump_yaml(fm) + "\n\n" + (body or "").strip() + ("\n" if (body or "").strip() else "")


# ---------- 微信公众号文章转载 ----------

WX_ARTICLE_HOST = "mp.weixin.qq.com"
WX_IMG_PLACEHOLDER = "{{IMG_%d}}"
# 正文里需要排除的非内容图片（二维码/头像等页脚元素）
_WX_IMG_SKIP_MARKERS = ("qr_code", "qrcode", "jump_author_avatar", "js_pc_weapp_code")


def extract_wx_url(text: str) -> Optional[str]:
    """从消息文本提取微信公众号文章链接；非 mp.weixin.qq.com 的链接返回 None。"""
    m = re.search(r"https?://mp\.weixin\.qq\.com/[^\s]+", text or "")
    if not m:
        return None
    return m.group(0).rstrip(".,;:!?，。；：！？)）]】>\"'")


def _html_unescape(text: str) -> str:
    import html as _html

    return _html.unescape(text or "")


def _strip_tags(fragment: str) -> str:
    """剥掉剩余标签（含 script/style）并解码实体，压缩空白。"""
    s = re.sub(r"<script[\s\S]*?</script>", "", fragment or "", flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>", "", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html_unescape(s)
    s = re.sub(r"[ \t\u00a0]+", " ", s)
    return s.strip()


def _wx_content_img_src(tag: str) -> str:
    """从 <img> 标签提取正文图片 URL（data-src 懒加载优先）；页脚二维码/头像返回空串。"""
    low = (tag or "").lower()
    if any(marker in low for marker in _WX_IMG_SKIP_MARKERS):
        return ""
    src = ""
    m = re.search(r'data-src=["\']([^"\']+)["\']', tag, flags=re.I)
    if m:
        src = m.group(1)
    else:
        m = re.search(r'\ssrc=["\']([^"\']+)["\']', tag, flags=re.I)
        if m:
            src = m.group(1)
    src = _html_unescape(src).strip()
    if not src or src.startswith("data:") or not src.lower().startswith(("http://", "https://")):
        return ""
    # mmbiz 图片统一升级 https（博客混合内容安全）
    if src.startswith("http://mmbiz.qpic.cn"):
        src = "https://" + src[len("http://"):]
    return src


def _img_tag_to_md(tag: str) -> str:
    """<img ...> → Markdown 图片（先落 __WXIMG__ 标记，稍后统一换占位符）。"""
    src = _wx_content_img_src(tag)
    if not src:
        return ""
    return "\n\n[__WXIMG__]({})\n\n".format(src)


def html_to_markdown(fragment: str) -> str:
    """微信正文 HTML 片段 → Markdown。

    实测（2026-09-16）：微信正文段落靠 <section> 分隔（不是 <p>），
    文字在 <span leaf> 里；因此 </section> 必须作为段落边界，否则整篇挤成一行。
    另覆盖 img(data-src 优先)/br/h1-h4/blockquote/li/strong/em/a 与实体解码。
    """
    if not fragment:
        return ""
    s = re.sub(r"<!--[\s\S]*?-->", "", fragment)
    s = re.sub(r"<script[\s\S]*?</script>", "", s, flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>", "", s, flags=re.I)
    s = re.sub(r"<img[^>]*?>", lambda m: _img_tag_to_md(m.group(0)), s, flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    for level in (1, 2, 3, 4):
        s = re.sub(
            r"<h%d[^>]*>([\s\S]*?)</h%d>" % (level, level),
            lambda m, lv=level: "\n\n" + "#" * lv + " " + _strip_tags(m.group(1)) + "\n\n",
            s,
            flags=re.I,
        )
    s = re.sub(
        r"<blockquote[^>]*>([\s\S]*?)</blockquote>",
        lambda m: "\n\n> " + _strip_tags(m.group(1)).replace("\n", "\n> ") + "\n\n",
        s,
        flags=re.I,
    )
    s = re.sub(
        r"<li[^>]*>([\s\S]*?)</li>",
        lambda m: "- " + _strip_tags(m.group(1)) + "\n",
        s,
        flags=re.I,
    )
    s = re.sub(
        r"<(?:strong|b)[^>]*>([\s\S]*?)</(?:strong|b)>",
        lambda m: ("**" + _strip_tags(m.group(1)) + "**") if _strip_tags(m.group(1)) else "",
        s,
        flags=re.I,
    )
    s = re.sub(
        r"<(?:em|i)[^>]*>([\s\S]*?)</(?:em|i)>",
        lambda m: ("*" + _strip_tags(m.group(1)) + "*") if _strip_tags(m.group(1)) else "",
        s,
        flags=re.I,
    )
    s = re.sub(
        r"<a[^>]*href=[\"']([^\"']+)[\"'][^>]*>([\s\S]*?)</a>",
        lambda m: "[{}]({})".format(_strip_tags(m.group(2)) or m.group(1), _html_unescape(m.group(1))),
        s,
        flags=re.I,
    )
    # 段落边界：</section> 与 <p> 都算（实测微信主体用 section）
    s = re.sub(r"</section>", "\n\n", s, flags=re.I)
    s = re.sub(r"<section[^>]*>", "", s, flags=re.I)
    s = re.sub(r"<p[^>]*>", "\n\n", s, flags=re.I)
    s = re.sub(r"</p>", "\n\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html_unescape(s)
    lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in s.split("\n")]
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _wx_publish_date(html: str, shanghai_tz) -> str:
    """解析原文发布日期（YYYY-MM-DD）：优先 publish_time 文本，其次 var ct 时间戳（实测页面用 ct）。"""
    m = re.search(r'id="publish_time"[^>]*>\s*([^<]+?)\s*<', html or "")
    if m:
        t = m.group(1).strip()
        m2 = re.search(r"(\d{4})\D{1,2}(\d{1,2})\D{1,2}(\d{1,2})", t)
        if m2:
            return "{}-{:02d}-{:02d}".format(m2.group(1), int(m2.group(2)), int(m2.group(3)))
    m3 = re.search(r'var\s+(?:ct|create_time)\s*=\s*"(\d{9,12})"', html or "")
    if m3:
        try:
            ts = int(m3.group(1))
            return datetime.fromtimestamp(ts, shanghai_tz).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return ""
    return ""


def extract_wx_article(html: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """解析微信公众号文章页 HTML。

    返回 (data, err)：data = {title, author, publish_date, digest, image_urls, body_md}
    （body_md 中图片为 {{IMG_i}} 占位符，由 assemble_article_body 替换为图床 URL）。
    检测微信「环境异常/完成验证」验证页并返回明确错误。
    """
    if not html:
        return None, "抓取内容为空"
    if ("环境异常" in html) or ("完成验证" in html and "js_content" not in html):
        return None, "微信要求环境验证（服务器 IP 可能被风控拦截），请稍后重试或更换文章"
    m = re.search(r'id="js_content"', html)
    if not m:
        return None, "未找到文章正文（可能不是图文消息，或页面结构已变化）"
    # 正文范围：js_content 起，到工具条/推荐区/脚本前的最近边界为止（实测 <script 边界最靠前）
    tail = html[m.end():]
    end_idx = len(tail)
    for marker in ('<div class="rich_media_tool"', 'id="js_sg_bar"', "rich_media_area_extra", "<script"):
        pos = tail.find(marker)
        if pos != -1 and pos < end_idx:
            end_idx = pos
    content_html = tail[:end_idx]
    gt = content_html.find(">")
    if gt != -1:
        content_html = content_html[gt + 1:]
    content_html = re.sub(r"</div>\s*$", "", content_html.strip(), count=1)

    # 标题：h1#activity-name → og:title → var msg_title
    title = ""
    m_title = re.search(r'id="activity-name"[^>]*>([\s\S]*?)</h1>', html)
    if m_title:
        title = _strip_tags(m_title.group(1))
    if not title:
        m_og = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
        if m_og:
            title = _html_unescape(m_og.group(1)).strip()
    if not title:
        m_var = re.search(r'var\s+msg_title\s*=\s*"([^"]+)"', html)
        if m_var:
            title = _html_unescape(m_var.group(1)).strip()
    if not title:
        return None, "未解析到文章标题"

    # 公众号名：js_name 锚文本 → var nickname
    author = ""
    m_auth = re.search(r'id="js_name"[^>]*>([\s\S]*?)</a>', html)
    if m_auth:
        author = _strip_tags(m_auth.group(1))
    if not author:
        m_var2 = re.search(r'var\s+nickname\s*=\s*(?:htmlDecode\()?"([^"]+)"', html)
        if m_var2:
            author = _html_unescape(m_var2.group(1)).strip()

    digest = ""
    m_desc = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]*)"', html)
    if m_desc:
        digest = _html_unescape(m_desc.group(1)).strip()

    image_urls: List[str] = []
    for m_img in re.finditer(r"<img[^>]*?>", content_html, flags=re.I):
        src = _wx_content_img_src(m_img.group(0))
        if src:
            image_urls.append(src)

    body_md = html_to_markdown(content_html)
    img_iter = iter(range(len(image_urls)))

    def _ph(_m):
        return "\n\n{}\n\n".format(WX_IMG_PLACEHOLDER % next(img_iter, 0))

    body_md = re.sub(r"\[__WXIMG__\]\([^)]+\)", _ph, body_md)
    body_md = re.sub(r"\n{3,}", "\n\n", body_md).strip()
    text_only = re.sub(r"\{\{IMG_\d+\}\}", "", body_md).strip()
    if len(text_only) < 10 and not image_urls:
        return None, "正文内容为空或过短（可能不是可直接转载的图文消息）"
    if not digest:
        # 实测：部分文章 description meta 为空 → 用正文首段截取兜底
        digest = re.sub(r"\s+", " ", text_only)[:60]

    data = {
        "title": title,
        "author": author,
        "publish_date": _wx_publish_date(html, SHANGHAI_TZ),
        "digest": digest,
        "image_urls": image_urls,
        "body_md": body_md,
    }
    return data, ""


def assemble_article_body(body_md: str, uploaded_urls: List[str]) -> str:
    """把正文中的 {{IMG_i}} 占位符按序替换为图床 URL；未解析到的占位符直接清除。"""
    out = body_md or ""
    for idx, url in enumerate(uploaded_urls or []):
        out = out.replace(WX_IMG_PLACEHOLDER % idx, url)
    out = re.sub(r"\{\{IMG_\d+\}\}", "", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def build_article_md(
    title: str,
    body_md: str,
    image_urls: List[str],
    tags: List[str],
    description: str = "",
    source_link: str = "",
    author: str = "",
    publish_date: str = "",
    now: datetime = None,
) -> str:
    """生成转载文章（posts 集合）markdown。

    对齐博客 posts schema（src/content.config.ts）：title/published/tags/description/
    image/sourceLink；不写已废弃的 category（分类=目录路径）。
    正文开头自动加转载声明引用块（对齐现有转载文惯例）。
    """
    now = now or now_shanghai()
    fm: Dict[str, Any] = {
        "title": title,
        "published": now.strftime("%Y-%m-%d"),
    }
    if description:
        fm["description"] = description
    if image_urls:
        fm["image"] = image_urls[0]
    fm["tags"] = list(tags or [])
    if source_link:
        fm["sourceLink"] = source_link

    if author:
        credit = "> 本文转载自微信公众号「{}」".format(author)
    else:
        credit = "> 本文转载自微信公众号文章"
    if publish_date:
        credit += "（原文发布于 {}）".format(publish_date)
    if source_link:
        credit += "\n> 原文链接：{}".format(source_link)

    body = (body_md or "").strip()
    return _dump_yaml(fm) + "\n\n" + credit + "\n\n" + body + "\n"


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


# ---------- 向导常量 ----------

WIZARD_CANCEL_KEYWORDS = ("取消", "cancel", "退出", "exit", "q")
WIZARD_SKIP_KEYWORDS = ("跳过", "skip", "无", "不要", "none")
WIZARD_NEW_KEYWORDS = ("新建", "new", "创建")

MEDIA_WIZARD_CATEGORIES = [
    ("电影", "movie"),
    ("电视剧", "tv"),
    ("动漫", "anime"),
    ("纪录片", "documentary"),
    ("游戏", "game"),
]


def is_wizard_cancel(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in WIZARD_CANCEL_KEYWORDS


def is_wizard_skip(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in WIZARD_SKIP_KEYWORDS


def is_wizard_new(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in WIZARD_NEW_KEYWORDS


def parse_choice(text: str, n: int) -> Optional[int]:
    """解析数字选择 1..n，支持 '1' / '1.' / '1、' / '1。'。非数字返回 None。"""
    t = (text or "").strip()
    if not t:
        return None
    m = re.match(r"^(\d+)\s*[.、。]?\s*$", t)
    if not m:
        m2 = re.match(r"^(\d+)\b", t)
        if not m2:
            return None
        m = m2
    try:
        v = int(m.group(1))
    except ValueError:
        return None
    if 1 <= v <= n:
        return v
    return None


def format_choices(title: str, items: List[str], extra: Optional[List[str]] = None, with_skip_cancel: bool = True) -> str:
    """生成编号选择文本。items 为选项列表，extra 追加到末尾（如 ['新建笔记本']）。"""
    lines = [title]
    for idx, item in enumerate(items, 1):
        lines.append(f"{idx}. {item}")
    if extra:
        start = len(items) + 1
        for j, e in enumerate(extra):
            lines.append(f"{start + j}. {e}")
    if with_skip_cancel:
        lines.append("")
        lines.append("回复数字选择，回复“取消”退出" + ("、“跳过”跳过" if with_skip_cancel else ""))
    return "\n".join(lines)


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
        self.wizard: Optional[Dict[str, Any]] = None  # 向导状态 {step, data}
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
