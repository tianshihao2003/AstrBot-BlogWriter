# -*- coding: utf-8 -*-
"""
BlogWriter 纯逻辑核心：命令解析、markdown 生成、请求构造、响应解析。
不依赖 AstrBot，可独立单元测试。
"""

import base64
import json
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

SESSION_TIMEOUT = timedelta(minutes=30)
MAX_PATH_SUFFIX = 10

COMMANDS = ("动态", "笔记", "足迹", "友链", "发布", "取消", "状态", "帮助")

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
    now = now or datetime.now()
    return "ext-" + str(int(now.timestamp() * 1000))


def format_moment_published(now: datetime = None) -> str:
    now = now or datetime.now()
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

def _dump_yaml(data: Dict[str, Any]) -> str:
    """生成 YAML frontmatter（仅支持本项目用到的简单类型）。"""
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, list):
            if not value:
                lines.append("{}: []".format(key))
            else:
                lines.append("{}:".format(key))
                for item in value:
                    lines.append("  - {}".format(_yaml_str(str(item))))
        elif isinstance(value, bool):
            lines.append("{}: {}".format(key, "true" if value else "false"))
        elif isinstance(value, (int, float)):
            lines.append("{}: {}".format(key, value))
        elif isinstance(value, str):
            if _is_datetime_str(value):
                # 对齐现有数据：published 等时间字段不加引号（如 2024-10-02 14:19:00）
                lines.append("{}: {}".format(key, value))
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
    author: str,
    avatar: str,
    tags: List[str],
    now: datetime = None,
) -> str:
    """对齐现有 moments 格式：图片在 frontmatter 的 images 数组，正文只放文字。"""
    now = now or datetime.now()
    fm = {
        "published": format_moment_published(now),
        "author": author,
        "avatar": avatar,
        "tags": tags,
    }
    if image_urls:
        fm["images"] = list(image_urls)
    return _dump_yaml(fm) + "\n\n" + content.strip() + "\n"


def build_note_md(name: str, content: str, image_urls: List[str], day: datetime = None) -> str:
    day = day or datetime.now()
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
    day = day or datetime.now()
    fm = {
        "date": day.strftime("%Y-%m-%d"),
        "province": province,
        "city": city,
    }
    if experience:
        fm["experience"] = experience
    fm["visitCount"] = 1
    fm["lat"] = round(lat, 6)
    fm["lng"] = round(lng, 6)
    fm["photos"] = list(photos)
    fm["tags"] = tags
    return _dump_yaml(fm) + "\n\n\n"


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
    weight: int = 10,
    enabled: bool = True,
) -> str:
    fm = {
        "title": title,
        "imgurl": imgurl,
        "desc": desc,
        "siteurl": siteurl,
        "tags": tags,
        "weight": weight,
        "enabled": enabled,
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
        self.kind = kind  # moment | note | place
        self.meta = meta
        self.text_parts: List[str] = []
        # 元素为 (来源引用, 图片字节)。图片在收到消息时立即读取，避免临时文件被清理。
        self.images: List[Tuple[str, bytes]] = []
        self.created_at = created_at or datetime.now()
        self.last_active = self.created_at

    def touch(self, now: datetime = None) -> None:
        self.last_active = now or datetime.now()

    def expired(self, now: datetime = None, timeout: timedelta = None) -> bool:
        now = now or datetime.now()
        return now - self.last_active > (timeout or SESSION_TIMEOUT)

    def add_text(self, text: str) -> None:
        self.text_parts.append(text)

    def add_image(self, ref: str, data: bytes) -> None:
        self.images.append((ref, data))

    def full_text(self) -> str:
        return "\n".join(p.strip() for p in self.text_parts if p.strip())
