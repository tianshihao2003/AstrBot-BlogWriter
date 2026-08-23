# -*- coding: utf-8 -*-
"""blog_writer_core 核心逻辑单元测试。"""

import sys
import os
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blog_writer_core import (  # noqa: E402
    BILL_ACCOUNTS,
    BILL_CATEGORIES,
    SCHEDULE_PRIORITIES,
    SESSION_TIMEOUT,
    Session,
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
    format_moment_published,
    gen_moment_id,
    moment_filename,
    next_friend_index,
    note_filename,
    parse_album,
    parse_album_frontmatter,
    parse_amap_response,
    parse_bill,
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
    upload_base_host,
    validate_friend_data,
    with_suffix,
)

try:
    import yaml
except ImportError:
    yaml = None


class TestParseMessage(unittest.TestCase):
    def test_command_with_slash(self):
        self.assertEqual(parse_message("/动态 今天去了公园")[0], "动态")
        self.assertEqual(parse_message("/动态 今天去了公园")[1], ["今天去了公园"])

    def test_command_without_slash(self):
        self.assertEqual(parse_message("发布")[0], "发布")

    def test_unknown_message(self):
        cmd, _, raw = parse_message("随便聊聊")
        self.assertIsNone(cmd)
        self.assertEqual(raw, "随便聊聊")

    def test_empty(self):
        self.assertIsNone(parse_message("")[0])
        self.assertIsNone(parse_message("   ")[0])

    def test_multi_space(self):
        cmd, args, _ = parse_message("/笔记 日常随笔 我的标题")
        self.assertEqual(cmd, "笔记")
        self.assertEqual(args, ["日常随笔", "我的标题"])

    def test_album_command(self):
        cmd, args, _ = parse_message("/相册 情侣头像")
        self.assertEqual(cmd, "相册")
        self.assertEqual(args, ["情侣头像"])

    def test_parse_album(self):
        self.assertEqual(parse_album(["情侣头像"]), "情侣头像")
        self.assertEqual(parse_album(["我的", "旅行"]), "我的 旅行")
        self.assertEqual(parse_album([]), "")
        self.assertEqual(parse_album(["  "]), "")

    def test_parse_album_frontmatter(self):
        # 常规：引号包裹的 imgbedFolder
        md = '---\ntitle: "测试相册"\nimgbedFolder: "blog/album/相册1"\n---\n'
        self.assertEqual(parse_album_frontmatter(md), ("测试相册", "blog/album/相册1"))
        # 无引号
        md2 = "---\ntitle: 情侣头像\ndate: 2026-08-13\nimgbedFolder: blog/album/情侣头像\n---\n"
        self.assertEqual(parse_album_frontmatter(md2), ("情侣头像", "blog/album/情侣头像"))
        # 缺字段
        md3 = "---\ntitle: 只有标题\n---\n"
        self.assertEqual(parse_album_frontmatter(md3), ("只有标题", ""))
        # 空/无 frontmatter
        self.assertEqual(parse_album_frontmatter(""), ("", ""))
        self.assertEqual(parse_album_frontmatter("正文无 frontmatter"), ("", ""))
        # title 值含冒号或特殊字符（键值行仅取第一个冒号后的内容）
        md4 = '---\ntitle: "A: B"\n---\n'
        self.assertEqual(parse_album_frontmatter(md4), ("A: B", ""))


class TestParseArgs(unittest.TestCase):
    def test_dynamic(self):
        content, tags = parse_dynamic(["今天", "去了公园"])
        self.assertEqual(content, "今天 去了公园")
        self.assertEqual(tags, [])

    def test_dynamic_with_tags(self):
        content, tags = parse_dynamic(["今天去了公园", "#日常", "#2026"])
        self.assertEqual(content, "今天去了公园")
        self.assertEqual(tags, ["日常", "2026"])
        # 标签去重
        content, tags = parse_dynamic(["内容", "#日常", "#日常"])
        self.assertEqual(tags, ["日常"])
        # 只有标签没有正文
        content, tags = parse_dynamic(["#日常"])
        self.assertIsNone(content)
        self.assertEqual(tags, ["日常"])

    def test_dynamic_empty(self):
        self.assertEqual(parse_dynamic([]), (None, []))
        self.assertEqual(parse_dynamic(["  "]), (None, []))

    def test_note_two_args(self):
        self.assertEqual(parse_note(["日常随笔", "标题"], "默认"), ("日常随笔", "标题"))

    def test_note_one_arg_uses_default_dir(self):
        self.assertEqual(parse_note(["标题"], "日常随笔"), ("日常随笔", "标题"))

    def test_note_multi_word_title(self):
        self.assertEqual(parse_note(["每日总结", "今天", "很好"], "默认"), ("每日总结", "今天 很好"))

    def test_note_no_args(self):
        self.assertEqual(parse_note([], "默认"), ("默认", ""))

    def test_place(self):
        self.assertEqual(parse_place(["陕西", "华阴市华山", "去找宝宝了"]), ("陕西", "华阴市华山", "去找宝宝了", []))

    def test_place_with_tags(self):
        self.assertEqual(
            parse_place(["陕西", "华阴市华山", "去找宝宝了", "#旅游", "#2026"]),
            ("陕西", "华阴市华山", "去找宝宝了", ["旅游", "2026"]),
        )

    def test_place_two_args(self):
        self.assertEqual(parse_place(["陕西", "华阴市华山"]), ("陕西", "华阴市华山", "", []))

    def test_place_one_arg(self):
        self.assertEqual(parse_place(["华山"]), ("", "华山", "", []))

    def test_place_empty(self):
        self.assertEqual(parse_place([]), ("", "", "", []))

    def test_extract_tags(self):
        clean, tags = extract_tags("今天 #日常 去公园\n第二行 #2026")
        self.assertEqual(tags, ["日常", "2026"])
        self.assertNotIn("#", clean)
        self.assertEqual(clean, "今天 去公园\n第二行")


class TestNaming(unittest.TestCase):
    def test_moment_id_format(self):
        m = gen_moment_id()
        self.assertTrue(m.startswith("ext-"))
        self.assertTrue(m[4:].isdigit())

    def test_published_format(self):
        self.assertRegex(format_moment_published(), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_filenames(self):
        day = datetime(2026, 8, 8)
        self.assertEqual(moment_filename(day), "2026-08-08")
        self.assertEqual(note_filename(day), "2026年8月8日")
        self.assertEqual(note_filename(datetime(2026, 6, 1)), "2026年6月1日")
        self.assertEqual(place_filename(day), "2026-08-08")

    def test_with_suffix(self):
        self.assertEqual(with_suffix("a", ".md", 0), "a.md")
        self.assertEqual(with_suffix("a", ".md", 1), "a-1.md")
        self.assertEqual(with_suffix("a", ".md", 10), "a-10.md")


class TestMarkdown(unittest.TestCase):
    def test_moment_md_structure(self):
        md = build_moment_md("今天去了公园", ["https://img.tsh520.cn/file/a.jpg"], ["日常"])
        lines = md.split("\n")
        self.assertEqual(lines[0], "---")
        self.assertTrue("---" in lines)
        # 对齐现有格式：published 不加引号，无 id 字段
        self.assertIn("published: 20", md)
        self.assertNotIn('"published"', md)
        self.assertNotIn("id: ext-", md)
        # 2026-08-13 起博客不再按条写 author/avatar（schema 提供默认值）
        self.assertNotIn("author:", md)
        self.assertNotIn("avatar:", md)
        # 图片在 frontmatter 的 images 数组（URL 裸写，对齐现有风格），正文只有文字
        self.assertIn("images:", md)
        self.assertIn("  - https://img.tsh520.cn/file/a.jpg", md)
        self.assertNotIn("![a.jpg]", md)
        self.assertIn("今天去了公园", md)
        if yaml:
            fm = yaml.safe_load(md.split("---")[1])
            self.assertNotIn("author", fm)
            self.assertNotIn("avatar", fm)
            self.assertEqual(fm["tags"], ["日常"])
            self.assertEqual(fm["images"], ["https://img.tsh520.cn/file/a.jpg"])
            self.assertNotIn("id", fm)
            self.assertIn("published", fm)

    def test_moment_md_no_images(self):
        md = build_moment_md("纯文字动态", [], ["日常"])
        self.assertNotIn("images:", md)
        self.assertNotIn("author:", md)
        self.assertNotIn("avatar:", md)
        if yaml:
            fm = yaml.safe_load(md.split("---")[1])
            self.assertEqual(fm.get("images"), None)

    def test_note_md(self):
        md = build_note_md("标题", "正文第一行\n第二行", [], datetime(2026, 8, 8))
        if yaml:
            fm = yaml.safe_load(md.split("---")[1])
            self.assertEqual(fm["name"], "标题")
            # js-yaml（Astro 用）会把 2026-08-08 当字符串；PyYAML 会解析成 date 对象，两者皆可
            self.assertTrue(fm["date"] == "2026-08-08" or fm["date"] == datetime(2026, 8, 8).date())
        self.assertIn("正文第一行\n第二行", md)

    def test_place_md(self):
        md = build_place_md("陕西", "华阴市华山", "去找宝宝了", ["https://img.tsh520.cn/file/places/x.jpg"], 34.477861, 110.084789, ["旅游"], datetime(2026, 8, 8))
        # 2026-08 新格式：新增 description 固定句式，不再写 visitCount（schema 默认 1）
        self.assertIn('description: "记录在陕西华阴市华山的足迹。"', md)
        self.assertNotIn("visitCount", md)
        self.assertIn('tags: ["旅游"]', md)
        if yaml:
            fm = yaml.safe_load(md.split("---")[1])
            self.assertEqual(fm["province"], "陕西")
            self.assertEqual(fm["city"], "华阴市华山")
            self.assertEqual(fm["experience"], "去找宝宝了")
            self.assertEqual(fm["lat"], 34.477861)
            self.assertEqual(fm["lng"], 110.084789)
            self.assertNotIn("visitCount", fm)
            self.assertEqual(fm["photos"], ["https://img.tsh520.cn/file/places/x.jpg"])
            self.assertEqual(fm["tags"], ["旅游"])

    def test_place_md_no_experience(self):
        md = build_place_md("河南", "安阳", "", [], 36.1, 114.3, ["旅游"], datetime(2026, 8, 8))
        if yaml:
            fm = yaml.safe_load(md.split("---")[1])
            self.assertNotIn("experience", fm)
            self.assertNotIn("photos", fm)

    def test_album_md(self):
        # 对齐博客相册惯例（2026-08）：title/subtitle/date + imgbedFolder（带引号），无 photos 列表
        md = build_album_md("情侣头像", "blog/album/情侣头像", datetime(2026, 8, 13))
        self.assertIn("title: 情侣头像", md)
        self.assertIn("subtitle: 记录情侣头像", md)
        self.assertIn("date: 2026-08-13", md)
        self.assertNotIn('"date"', md)
        self.assertIn('imgbedFolder: "blog/album/情侣头像"', md)
        self.assertNotIn("photos:", md)
        if yaml:
            fm = yaml.safe_load(md.split("---")[1])
            self.assertEqual(fm["title"], "情侣头像")
            self.assertEqual(fm["imgbedFolder"], "blog/album/情侣头像")
            # js-yaml（Astro 用）按字符串解析；PyYAML 解析成 date 对象，两者皆可
            self.assertTrue(fm["date"] == "2026-08-13" or fm["date"] == datetime(2026, 8, 13).date())

    def test_yaml_quoting(self):
        # 含特殊字符的标题必须被正确引用，YAML 可解析
        md = build_note_md("标题: 带冒号", "正文", [], datetime(2026, 8, 8))
        if yaml:
            fm = yaml.safe_load(md.split("---")[1])
            self.assertEqual(fm["name"], "标题: 带冒号")

    def test_yaml_numeric_tag_quoted(self):
        # 纯数字字符串必须加引号，否则 YAML 解析成 int，zod z.array(z.string()) 校验失败
        # （2026-08 起 places 用行内数组：tags: ["旅游", "2026"]）
        md = build_place_md("河南", "安阳", "", [], 36.1, 114.3, ["旅游", "2026"], datetime(2026, 8, 8))
        self.assertIn('"2026"', md)
        if yaml:
            fm = yaml.safe_load(md.split("---")[1])
            self.assertEqual(fm["tags"], ["旅游", "2026"])

    def test_yaml_url_unquoted(self):
        # 动态（plain 风格）URL 仍裸写，对齐现有 moments 数据风格
        md = build_moment_md("内容", ["https://img.tsh520.cn/file/places/x.jpg"], ["日常"], datetime(2026, 8, 8))
        self.assertIn("  - https://img.tsh520.cn/file/places/x.jpg", md)
        # 足迹（quoted 风格，2026-08 对齐博客新格式）photos 带引号
        md2 = build_place_md("陕西", "华山", "", ["https://img.tsh520.cn/file/places/x.jpg"], 1.0, 2.0, ["旅游"], datetime(2026, 8, 8))
        self.assertIn('  - "https://img.tsh520.cn/file/places/x.jpg"', md2)
        if yaml:
            fm = yaml.safe_load(md2.split("---")[1])
            self.assertEqual(fm["photos"], ["https://img.tsh520.cn/file/places/x.jpg"])


class TestRequests(unittest.TestCase):
    def test_imgbed_upload_multipart(self):
        req = build_imgbed_upload("https://img.tsh520.cn/file", "a.png", b"1234")
        self.assertIn("multipart/form-data; boundary=", req["headers"]["Content-Type"])
        self.assertIn(b'name="file"; filename="a.png"', req["body"])
        self.assertTrue(req["body"].endswith(b"--\r\n") or req["body"].endswith(b"--"))
        self.assertIn(b"1234", req["body"])

    def test_imgbed_response(self):
        # 新版数组格式（官方文档 src/api/upload.md）：优先取 src（规范路径）
        ok, url = parse_imgbed_response(
            200, '[{"src": "/file/abc123_image.jpg", "publicUrl": "https://img.tsh520.cn/abc123_image.jpg"}]',
            "img.tsh520.cn",
        )
        self.assertTrue(ok)
        self.assertEqual(url, "https://img.tsh520.cn/file/abc123_image.jpg")
        # src 相对路径 → 拼主机
        ok, url = parse_imgbed_response(200, '[{"src": "/file/x.jpg"}]', "img.tsh520.cn")
        self.assertTrue(ok)
        self.assertEqual(url, "https://img.tsh520.cn/file/x.jpg")
        # 完整链接原样返回
        ok, url = parse_imgbed_response(200, '[{"src": "https://cdn.other.com/x.jpg"}]', "img.tsh520.cn")
        self.assertTrue(ok)
        self.assertEqual(url, "https://cdn.other.com/x.jpg")
        # 空数组
        ok, url = parse_imgbed_response(200, "[]", "img.tsh520.cn")
        self.assertFalse(ok)
        # 旧格式兼容
        ok, url = parse_imgbed_response(200, '{"code":200,"data":{"url":"https://x/a.jpg"}}', "")
        self.assertTrue(ok)
        self.assertEqual(url, "https://x/a.jpg")
        ok, url = parse_imgbed_response(200, '{"code":500,"message":"bad"}', "")
        self.assertFalse(ok)
        ok, url = parse_imgbed_response(200, "not json", "")
        self.assertFalse(ok)
        ok, url = parse_imgbed_response(200, '{"code":200,"data":"https://x/b.jpg"}', "")
        self.assertTrue(ok)
        self.assertEqual(url, "https://x/b.jpg")

    def test_upload_url_helpers(self):
        self.assertEqual(
            build_upload_url("https://img.tsh520.cn/upload", "blog/moments"),
            "https://img.tsh520.cn/upload?returnFormat=full&uploadFolder=blog/moments",
        )
        # 已有 authCode 时用 & 追加
        self.assertEqual(
            build_upload_url("https://img.tsh520.cn/upload?authCode=abc", "places"),
            "https://img.tsh520.cn/upload?authCode=abc&returnFormat=full&uploadFolder=places",
        )
        # 不重复追加已有参数
        self.assertEqual(
            build_upload_url("https://img.tsh520.cn/upload?uploadFolder=x&returnFormat=full", "blog"),
            "https://img.tsh520.cn/upload?uploadFolder=x&returnFormat=full",
        )
        # 空目录不加 uploadFolder
        self.assertEqual(
            build_upload_url("https://img.tsh520.cn/upload", ""),
            "https://img.tsh520.cn/upload?returnFormat=full",
        )
        self.assertEqual(upload_base_host("https://img.tsh520.cn/upload"), "img.tsh520.cn")

    def test_amap_url(self):
        url = build_amap_url("陕西省华阴市华山", "KEY123")
        self.assertIn("address=%E9%99%95%E8%A5%BF", url)
        self.assertIn("key=KEY123", url)

    def test_amap_response(self):
        ok, coords = parse_amap_response(200, '{"status":"1","count":"1","geocodes":[{"location":"110.084789,34.477861"}]}')
        self.assertTrue(ok)
        self.assertEqual(coords, (34.477861, 110.084789))
        ok, coords = parse_amap_response(200, '{"status":"0","count":"0","geocodes":[]}')
        self.assertFalse(ok)
        ok, coords = parse_amap_response(200, '{"status":"1","count":"0"}')
        self.assertFalse(ok)
        ok, coords = parse_amap_response(200, '{"status":"1","count":"1","geocodes":[{"location":"abc"}]}')
        self.assertFalse(ok)
        ok, coords = parse_amap_response(500, "oops")
        self.assertFalse(ok)

    def test_github_put_body(self):
        body = build_github_put_body("msg", "内容".encode("utf-8"), "main")
        self.assertIn('"message": "msg"', body)
        self.assertIn('"branch": "main"', body)
        self.assertIn('"content": "', body)

    def test_github_put_response(self):
        self.assertTrue(parse_github_put_response(201, "{}")[0])
        self.assertTrue(parse_github_put_response(200, "{}")[0])
        ok, err = parse_github_put_response(422, "{}")
        self.assertFalse(ok)
        self.assertEqual(err, "CONFLICT")
        ok, err = parse_github_put_response(403, '{"message":"rate limited"}')
        self.assertFalse(ok)
        self.assertIn("rate limited", err)


class TestSession(unittest.TestCase):
    def test_expiry(self):
        now = datetime.now()
        s = Session("moment", {"content": "x"}, now)
        self.assertFalse(s.expired(now))
        self.assertTrue(s.expired(now + SESSION_TIMEOUT + timedelta(seconds=1)))

    def test_touch(self):
        now = datetime.now()
        s = Session("moment", {"content": "x"}, now)
        s.touch(now + timedelta(minutes=29))
        self.assertFalse(s.expired(now + timedelta(minutes=29) + SESSION_TIMEOUT - timedelta(seconds=1)))
        self.assertTrue(s.expired(now + timedelta(minutes=29) + SESSION_TIMEOUT + timedelta(seconds=1)))

    def test_text_and_images(self):
        s = Session("note", {"title": "t"})
        s.add_text("第一段")
        s.add_text("第二段")
        s.add_image("http://x/1.jpg", b"123")
        self.assertEqual(s.full_text(), "第一段\n第二段")
        self.assertEqual(len(s.images), 1)
        self.assertEqual(s.images[0], ("http://x/1.jpg", b"123"))


class TestFriend(unittest.TestCase):
    def test_parse_full(self):
        text = (
            "站点名称: 团子和蛋糕\n"
            "站点描述：如果你喜欢那么欢迎来到我的世界！\n"
            "站点链接 https://blog.tsh520.cn\n"
            "头像链接: /assets/ziyuan/tx.webp\n"
        )
        data = parse_friend_text(text)
        self.assertEqual(data["title"], "团子和蛋糕")
        self.assertEqual(data["desc"], "如果你喜欢那么欢迎来到我的世界！")
        self.assertEqual(data["siteurl"], "https://blog.tsh520.cn")
        self.assertEqual(data["imgurl"], "/assets/ziyuan/tx.webp")

    def test_parse_english_keys_and_order(self):
        text = "siteurl: https://blog.ayeez.cn\ntitle: 阿叶\nimgurl: https://qiniu.ayeez.cn/avatar.jpg\ndesc: 记录生活"
        data = parse_friend_text(text)
        self.assertEqual(data["title"], "阿叶")
        self.assertEqual(data["siteurl"], "https://blog.ayeez.cn")
        self.assertEqual(data["imgurl"], "https://qiniu.ayeez.cn/avatar.jpg")

    def test_parse_no_key_lines(self):
        # 无键名：URL 自动归类
        data = parse_friend_text("我的博客\nhttps://blog.tsh520.cn\nhttps://img.tsh520.cn/avatar.png")
        self.assertEqual(data["title"], "我的博客")
        self.assertEqual(data["siteurl"], "https://blog.tsh520.cn")
        self.assertEqual(data["imgurl"], "https://img.tsh520.cn/avatar.png")

    def test_parse_partial(self):
        data = parse_friend_text("站点名称: 小明\n其他无关行\n")
        self.assertEqual(data["title"], "小明")
        self.assertNotIn("siteurl", data)

    def test_parse_unknown_key_with_url_value(self):
        # 用户实测格式：键名不在别名表，但值能兜底识别
        data = parse_friend_text(
            "网站名称: RAGNote\n"
            "描述:Life is code. I will debug it.\n"
            "网站地址: https://ragnote.top/\n"
            "头像:https://ragnote.top/Avatar.png\n"
        )
        self.assertEqual(data["title"], "RAGNote")
        self.assertEqual(data["desc"], "Life is code. I will debug it.")
        self.assertEqual(data["siteurl"], "https://ragnote.top/")
        self.assertEqual(data["imgurl"], "https://ragnote.top/Avatar.png")

    def test_parse_no_colon_space(self):
        # 冒号后无空格的紧凑格式
        data = parse_friend_text("站点名称:张三\n站点链接:https://z.com\n站点描述:描述文字")
        self.assertEqual(data["title"], "张三")
        self.assertEqual(data["siteurl"], "https://z.com")
        self.assertEqual(data["desc"], "描述文字")

    def test_parse_butterfly_style_english_keys(self):
        # Butterfly 主题格式：name / link / avatar / descr
        data = parse_friend_text(
            "name: Hexo\nlink: https://hexo.io/\navatar: https://hexo.io/logo.svg\ndescr: 快速强大的博客框架"
        )
        self.assertEqual(data["title"], "Hexo")
        self.assertEqual(data["siteurl"], "https://hexo.io/")
        self.assertEqual(data["imgurl"], "https://hexo.io/logo.svg")
        self.assertEqual(data["desc"], "快速强大的博客框架")

    def test_parse_chinese_variants(self):
        # 中文博客圈常见变体
        data = parse_friend_text(
            "博客名：张三的博客\n"
            "博客地址: https://blog.zhang.com\n"
            "博主头像: https://blog.zhang.com/avatar.jpg\n"
            "博主简介：记录生活\n"
        )
        self.assertEqual(data["title"], "张三的博客")
        self.assertEqual(data["siteurl"], "https://blog.zhang.com")
        self.assertEqual(data["imgurl"], "https://blog.zhang.com/avatar.jpg")
        self.assertEqual(data["desc"], "记录生活")

    def test_validate(self):
        ok, _ = validate_friend_data({"title": "a", "siteurl": "https://a.com"})
        self.assertTrue(ok)
        ok, err = validate_friend_data({"siteurl": "https://a.com"})
        self.assertFalse(ok)
        self.assertIn("名称", err)
        ok, err = validate_friend_data({"title": "a"})
        self.assertFalse(ok)
        self.assertIn("链接", err)
        ok, err = validate_friend_data({"title": "a", "siteurl": "not-a-url"})
        self.assertFalse(ok)

    def test_build_friend_md(self):
        md = build_friend_md("张三", "描述", "https://z.com", "/assets/a.png", ["Blog"])
        if yaml:
            fm = yaml.safe_load(md.split("---")[1])
            self.assertEqual(fm["title"], "张三")
            self.assertEqual(fm["siteurl"], "https://z.com")
            # 2026-08 对齐现有友链文件：weight 0、末尾 group: other
            self.assertEqual(fm["weight"], 0)
            self.assertTrue(fm["enabled"])
            self.assertEqual(fm["group"], "other")

    def test_clean_filename(self):
        self.assertEqual(clean_filename_part("张三的博客"), "张三的博客")
        self.assertEqual(clean_filename_part('a/b:c*d?'), "a-b-c-d")
        self.assertEqual(clean_filename_part("  "), "friend")

    def test_next_index(self):
        self.assertEqual(next_friend_index(["01-a.md", "02-b.md", "05-c.md"]), 6)
        self.assertEqual(next_friend_index(["01-a.md"]), 2)
        self.assertEqual(next_friend_index([]), 1)
        self.assertEqual(next_friend_index(["not-numbered.md"]), 1)


class TestBillSchedule(unittest.TestCase):
    def test_parse_bill_natural(self):
        data, err = parse_bill("今天午餐微信花了32")
        self.assertEqual(err, "")
        self.assertEqual(data["amount"], -32)
        self.assertEqual(data["category"], "餐饮")
        self.assertEqual(data["account"], "微信")

    def test_parse_bill_income(self):
        data, _ = parse_bill("发工资12000 银行卡")
        self.assertEqual(data["type"], "income")
        self.assertEqual(data["amount"], 12000)

    def test_parse_bill_liability_borrow(self):
        # 负债借入：正数（对齐博客 bill-adapter：liability += amount）
        data, err = parse_bill("花呗借款5000")
        self.assertEqual(err, "")
        self.assertEqual(data["type"], "liability")
        self.assertEqual(data["amount"], 5000)
        self.assertEqual(data["category"], "负债")
        self.assertEqual(data["account"], "花呗")

    def test_parse_bill_liability_repay(self):
        # 负债还款：负数（减少负债）
        data, _ = parse_bill("花呗还款2000")
        self.assertEqual(data["type"], "liability")
        self.assertEqual(data["amount"], -2000)
        self.assertEqual(data["account"], "花呗")

    def test_parse_bill_type_prefix(self):
        # 首词显式类型前缀
        data, _ = parse_bill("支出 午餐微信花了32")
        self.assertEqual(data["type"], "expense")
        self.assertEqual(data["amount"], -32)
        data2, _ = parse_bill("收入 兼职到手800 支付宝")
        self.assertEqual(data2["type"], "income")
        self.assertEqual(data2["amount"], 800)
        data3, _ = parse_bill("负债 白条借款300")
        self.assertEqual(data3["type"], "liability")
        self.assertEqual(data3["amount"], 300)
        self.assertEqual(data3["account"], "白条")
        # 前缀 + 还款 → 负
        data4, _ = parse_bill("负债 花呗还款2000")
        self.assertEqual(data4["type"], "liability")
        self.assertEqual(data4["amount"], -2000)

    def test_parse_bill_prefix_word_alone_not_stripped(self):
        # 「消费」后无空格内容时（如「消费30」），不作为前缀剥离，按关键词正常解析
        data, _ = parse_bill("消费30")
        self.assertEqual(data["type"], "expense")
        self.assertEqual(data["amount"], -30)

    def test_parse_bill_category_prefix(self):
        # 首词分类前缀：关键词没命中的冷门消费（如理发）可显式指定分类
        data, _ = parse_bill("食品酒水 买了箱牛奶45")
        self.assertEqual(data["category"], "食品酒水")
        self.assertEqual(data["title"], "买了箱牛奶")
        # 类型 + 分类组合前缀
        data2, _ = parse_bill("支出 餐饮 午餐30")
        self.assertEqual(data2["type"], "expense")
        self.assertEqual(data2["category"], "餐饮")
        self.assertEqual(data2["title"], "午餐")

    def test_build_bill_md(self):
        md = build_bill_md(
            {
                "title": "午餐",
                "amount": -32,
                "type": "expense",
                "category": "餐饮",
                "account": "微信",
                "date": datetime(2026, 8, 21),
                "description": "午餐",
            },
            datetime(2026, 8, 21),
        )
        # 2026-08 对齐博客最新 bills 格式：字符串带引号、tags 行内数组、date/amount 不带
        self.assertIn("amount: -32", md)
        self.assertIn('category: "餐饮"', md)
        self.assertIn('title: "午餐"', md)
        self.assertIn("date: 2026-08-21", md)
        self.assertIn('tags: ["餐饮"]', md)

    def test_parse_schedule_natural(self):
        data, _ = parse_schedule("明天下午3点高优在会议室A开周会 每周重复 提前15分钟")
        self.assertEqual(data["title"], "周会")
        self.assertEqual(data["priority"], "high")
        self.assertEqual(data["location"], "会议室A")
        self.assertEqual(data["repeat"], "每周")
        self.assertEqual(data["remind_before"], 15)

    def test_build_schedule_md(self):
        md = build_schedule_md(
            {"title": "周会", "date": datetime(2026, 8, 22, 15, 0), "priority": "high", "location": "会议室A"},
            datetime(2026, 8, 22),
        )
        # 2026-08 对齐博客最新 schedules 格式：字符串带引号
        self.assertIn('title: "周会"', md)
        self.assertIn("date: 2026-08-22 15:00:00", md)
        if yaml:
            fm = yaml.safe_load(md.split("---")[1])
            self.assertEqual(fm["title"], "周会")
            self.assertEqual(fm["location"], "会议室A")

    def test_build_schedule_md_empty_fields_omitted(self):
        # 空地点/重复不写字段（对齐现有文件：示例占位无 location/repeat）
        md = build_schedule_md({"title": "示例", "date": datetime(2026, 8, 22), "allDay": True}, datetime(2026, 8, 22))
        self.assertNotIn("location:", md)
        self.assertNotIn("repeat:", md)
        self.assertIn("date: 2026-08-22\n", md)  # 全天只写日期

    def test_schedules_batch_lunar(self):
        # 2026-08 农历生日：isLunar/lunarMonth/lunarDay，不存公历 date
        items, err = parse_schedules_batch("我的生日农历8.24对象12.22妈8.7大姐11.22爸12.14二姐4.4都是农历")
        self.assertEqual(err, "")
        self.assertEqual(len(items), 6)
        persons = [it["person"] for it in items]
        self.assertEqual(persons, ["我", "对象", "我妈", "大姐", "我爸", "二姐"])
        first = items[0]
        self.assertTrue(first["isLunar"])
        self.assertEqual(first["lunarMonth"], 8)
        self.assertEqual(first["lunarDay"], 24)
        self.assertNotIn("date", first)
        md = build_schedule_md(first)
        self.assertIn("isLunar: true", md)
        self.assertIn("lunarMonth: 8", md)
        self.assertIn("lunarDay: 24", md)
        self.assertNotIn("date:", md)
        self.assertEqual(schedule_filename(first), "lunar-8-24")

    def test_schedules_batch_solar(self):
        # 公历生日：存 date（今年已过则顺延明年）
        items, err = parse_schedules_batch("我生日3.15对象生日9.20")
        self.assertEqual(err, "")
        self.assertEqual(len(items), 2)
        self.assertFalse(items[0]["isLunar"])
        self.assertIn("date", items[0])
        self.assertEqual(schedule_filename(items[0], datetime(2026, 8, 23)), "2027-03-15")

    def test_parse_anniversary(self):
        from blog_writer_core import parse_anniversary

        # 公历月日 + @人物（对齐博客现有“我和宝宝认识的纪念日”）
        data, err = parse_anniversary("我和宝宝认识的纪念日 1月1日 @宝宝", datetime(2026, 8, 23))
        self.assertEqual(err, "")
        self.assertEqual(data["title"], "我和宝宝认识的纪念日")
        self.assertEqual(data["category"], "anniversary")
        self.assertEqual(data["repeat"], "每年")
        self.assertTrue(data["allDay"])
        self.assertEqual(data["person"], "宝宝")
        self.assertEqual(data["date"], datetime(2026, 1, 1))
        self.assertNotIn("isLunar", data)

        # 农历
        data2, _ = parse_anniversary("结婚纪念日 农历5月20", datetime(2026, 8, 23))
        self.assertTrue(data2["isLunar"])
        self.assertEqual(data2["lunarMonth"], 5)
        self.assertEqual(data2["lunarDay"], 20)
        self.assertNotIn("date", data2)
        self.assertEqual(data2["title"], "结婚纪念日")
        self.assertEqual(schedule_filename(data2), "lunar-5-20")

        # 带年公历
        data3, _ = parse_anniversary("领证纪念日 2026-05-20", datetime(2026, 8, 23))
        self.assertEqual(data3["date"], datetime(2026, 5, 20))

        # 无日期报错
        data4, err4 = parse_anniversary("结婚纪念日", datetime(2026, 8, 23))
        self.assertIsNone(data4)
        self.assertIn("日期", err4)

        # 日期非法报错
        data5, err5 = parse_anniversary("纪念日 13月40", datetime(2026, 8, 23))
        self.assertIsNone(data5)

    def test_build_anniversary_md(self):
        from blog_writer_core import build_schedule_md, parse_anniversary

        data, _ = parse_anniversary("我和宝宝认识的纪念日 1月1日 @宝宝", datetime(2026, 8, 23))
        md = build_schedule_md(data)
        # 对齐博客现有 src/content/schedules/2026-01-01-我和宝宝认识的纪念日.md
        self.assertIn('category: "anniversary"', md)
        self.assertIn('repeat: "每年"', md)
        self.assertIn('person: "宝宝"', md)
        self.assertIn("date: 2026-01-01", md)


class TestMedia(unittest.TestCase):
    def test_tmdb_search_url_and_poster(self):
        from blog_writer_core import build_tmdb_search_url, tmdb_poster_url

        url = build_tmdb_search_url("侏罗纪世界", "mykey")
        self.assertIn("api_key=mykey", url)
        self.assertIn("language=zh-CN", url)
        # 自定义反代 base
        url2 = build_tmdb_search_url("x", "k", "https://tmdb-proxy.example.com/")
        self.assertTrue(url2.startswith("https://tmdb-proxy.example.com/3/search/multi"))
        self.assertEqual(
            tmdb_poster_url("/abc.jpg"),
            "https://image.tmdb.org/t/p/w500/abc.jpg",
        )
        self.assertEqual(
            tmdb_poster_url("abc.jpg", "https://img-proxy.example.com/t/p/"),
            "https://img-proxy.example.com/t/p/w500/abc.jpg",
        )

    def test_parse_tmdb_search_response(self):
        from blog_writer_core import parse_tmdb_search_response
        import json

        body = json.dumps({
            "results": [
                {"media_type": "person", "name": "导演"},
                {
                    "media_type": "tv", "name": "测试剧", "original_name": "Test Show",
                    "first_air_date": "2024-03-01", "overview": "剧情简介", "vote_average": 8.1,
                    "poster_path": "/tv.jpg",
                },
            ]
        }, ensure_ascii=False)
        data, err = parse_tmdb_search_response(200, body)
        self.assertEqual(err, "")
        self.assertEqual(data["media_type"], "tv")
        self.assertEqual(data["title"], "测试剧")
        self.assertEqual(data["year"], "2024")
        # 未找到
        data2, err2 = parse_tmdb_search_response(200, json.dumps({"results": []}))
        self.assertIsNone(data2)
        self.assertIn("未找到", err2)
        # 401
        data3, err3 = parse_tmdb_search_response(401, "{}")
        self.assertIsNone(data3)
        self.assertIn("401", err3)

    def test_parse_media_score(self):
        from blog_writer_core import parse_media_score

        self.assertEqual(parse_media_score("评分 8"), 8)
        self.assertEqual(parse_media_score("打分：9"), 9)
        self.assertEqual(parse_media_score("10分"), 10)
        self.assertEqual(parse_media_score("99分"), 10)  # clamp 到 10
        self.assertIsNone(parse_media_score("好看"))
        self.assertIsNone(parse_media_score("评分"))  # 无数字不算

    def test_build_bangumi_md(self):
        from blog_writer_core import build_bangumi_md

        md = build_bangumi_md(
            "侏罗纪世界",
            "https://img.tsh520.cn/file/blog/bangumi/侏罗纪世界.jpg",
            score=8,
            tags=["冒险", "科幻"],
            comment="女主不该活着。",
            subcategory="movie",
            now=datetime(2026, 8, 23),
        )
        # 对齐博客现有 src/content/bangumi/anime/侏罗纪世界.md 格式
        self.assertIn("title: 侏罗纪世界", md)
        self.assertIn("category: anime", md)
        self.assertIn("subcategory: movie", md)
        self.assertIn("status: 2", md)
        self.assertIn("image: https://img.tsh520.cn/file/blog/bangumi/侏罗纪世界.jpg", md)
        self.assertIn("score: 8", md)
        self.assertIn("- 冒险", md)
        self.assertIn("published: 2026-08-23", md)
        self.assertIn("女主不该活着。", md)
        # 无评分/标签时不写字段
        md2 = build_bangumi_md("X", "https://i/x.jpg", score=None, tags=[], comment="", subcategory="tv", now=datetime(2026, 8, 23))
        self.assertNotIn("score:", md2)
        self.assertNotIn("tags:", md2)
        # 书籍：category book、无 subcategory
        md3 = build_bangumi_md(
            "认知觉醒", "https://img.tsh520.cn/file/blog/bangumi/认知觉醒.jpg",
            score=9, tags=["心理"], comment="好书", category="book", now=datetime(2026, 8, 23),
        )
        self.assertIn("category: book", md3)
        self.assertNotIn("subcategory", md3)
        self.assertIn("score: 9", md3)

    def test_parse_media_fields(self):
        from blog_writer_core import parse_media_fields

        self.assertEqual(parse_media_fields("名称: 我的阿勒泰"), {"title": "我的阿勒泰"})
        self.assertEqual(parse_media_fields("类型: 剧集"), {"subcategory": "tv"})
        self.assertEqual(parse_media_fields("类型：电影"), {"subcategory": "movie"})
        # 普通文本/评分不是键值对
        self.assertEqual(parse_media_fields("评分 8"), {})
        self.assertEqual(parse_media_fields("好看"), {})


class TestDaohang(unittest.TestCase):
    def test_xxapi_ico(self):
        from blog_writer_core import build_xxapi_ico_url, parse_xxapi_ico_response

        url = build_xxapi_ico_url("https://example.com/x?a=1")
        self.assertEqual(url, "https://v2.xxapi.cn/api/ico?url=https%3A%2F%2Fexample.com%2Fx%3Fa%3D1")
        ok_url, err = parse_xxapi_ico_response(200, '{"code":200,"data":"https://api.iowen.cn/favicon/x.png"}')
        self.assertEqual(err, "")
        self.assertEqual(ok_url, "https://api.iowen.cn/favicon/x.png")
        # 失败路径
        icon2, err2 = parse_xxapi_ico_response(200, '{"code":404}')
        self.assertIsNone(icon2)
        icon3, err3 = parse_xxapi_ico_response(200, '{"code":200,"data":"/relative.png"}')
        self.assertIsNone(icon3)

    def test_host_and_slug(self):
        from blog_writer_core import daohang_slug, site_host

        self.assertEqual(site_host("https://app.pagescms.org/inbox"), "app.pagescms.org")
        self.assertEqual(site_host("example.com/path"), "example.com")
        # 对齐现有无编号文件命名：app-pagescms-org.md / xxapi-cn.md
        self.assertEqual(daohang_slug("https://app.pagescms.org/"), "app-pagescms-org")
        self.assertEqual(daohang_slug("xxapi.cn"), "xxapi-cn")

    def test_parse_daohang_text(self):
        from blog_writer_core import parse_daohang_text

        kv = parse_daohang_text("名称: 团子的邮箱\n分类：我的网站\n描述: 邮箱服务\n颜色: #3b82f6")
        self.assertEqual(kv.get("name"), "团子的邮箱")
        self.assertEqual(kv.get("category"), "我的网站")
        self.assertEqual(kv.get("description"), "邮箱服务")
        self.assertEqual(kv.get("color"), "#3b82f6")
        # 无键值对内容
        self.assertEqual(parse_daohang_text("随便一句话"), {})

    def test_build_daohang_md(self):
        from blog_writer_core import build_daohang_md

        md = build_daohang_md(
            "团子的邮箱", "https://email.0824.uk/inbox", "我的网站",
            "https://img.tsh520.cn/file/blog/daohang/x-icon.webp",
            "个人使用的邮箱服务", ["个人网站", "实用工具"], "#3b82f6", body="正文",
        )
        # 对齐现有 src/content/daohang/01-tuanzi-email.md 格式
        self.assertIn("name: 团子的邮箱", md)
        self.assertIn("url: https://email.0824.uk/inbox", md)
        self.assertIn("icon: https://img.tsh520.cn/file/blog/daohang/x-icon.webp", md)
        self.assertIn("description: 个人使用的邮箱服务", md)
        self.assertIn("category: 我的网站", md)
        self.assertIn("tags: [个人网站, 实用工具]", md)
        self.assertIn('color: "#3b82f6"', md)
        self.assertIn("正文", md)
        # 无图标/描述/标签/颜色时不写字段
        md2 = build_daohang_md("X", "https://x.com", "")
        self.assertNotIn("icon:", md2)
        self.assertNotIn("description:", md2)
        self.assertNotIn("tags:", md2)
        self.assertNotIn("color:", md2)


if __name__ == "__main__":
    unittest.main()
