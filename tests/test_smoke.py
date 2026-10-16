# -*- coding: utf-8 -*-
"""集成冒烟测试：stub 掉 astrbot 依赖与网络 IO，走完整命令链路。"""

import asyncio
import logging
import os
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_DIR)


# ---------- astrbot stub ----------

class _Logger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


def _install_astrbot_stub():
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event_mod = types.ModuleType("astrbot.api.event")
    star_mod = types.ModuleType("astrbot.api.star")
    components_mod = types.ModuleType("astrbot.api.message_components")
    filter_mod = types.ModuleType("astrbot.api.event.filter")

    class AstrBotConfig(dict):
        pass

    class MessageEventResult:
        def __init__(self, text):
            self.text = text

        def message(self, text):
            self.text = text
            return self

    class AstrMessageEvent:
        def __init__(self, message_str="", sender_id="u1", messages=None):
            self.message_str = message_str
            self._sender_id = sender_id
            self._messages = messages or []

        def get_sender_id(self):
            return self._sender_id

        def get_messages(self):
            return self._messages

        def plain_result(self, text):
            return MessageEventResult(text)

    class EventMessageType:
        ALL = 7

    class Plain:
        def __init__(self, text):
            self.text = text

    class Image:
        def __init__(self, url="", file=""):
            self.url = url
            self.file = file
            self.path = file

    def event_message_type(typ):
        def deco(fn):
            return fn

        return deco

    def register(name, author, desc, version):
        def deco(cls):
            return cls

        return deco

    filter_mod.EventMessageType = EventMessageType
    filter_mod.event_message_type = event_message_type
    event_mod.filter = filter_mod
    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageEventResult = MessageEventResult
    api.event = event_mod
    api.star = star_mod
    api.AstrBotConfig = AstrBotConfig
    api.logger = _Logger()
    components_mod.Image = Image
    components_mod.Plain = Plain
    astrbot.api = api
    astrbot.logger = _Logger()

    class Context:
        pass

    class Star:
        def __init__(self, context, config=None):
            self.context = context
            self.config = config or {}

    star_mod.Context = Context
    star_mod.Star = Star
    star_mod.register = register

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.event.filter"] = filter_mod
    sys.modules["astrbot.api.star"] = star_mod
    sys.modules["astrbot.api.message_components"] = components_mod


_install_astrbot_stub()


class TestFlow(unittest.TestCase):
    def setUp(self):
        self.config = {
            "github_token": "tok",
            "github_repo": "tianshihao2003/dumplingandcakeblog",
            "github_branch": "main",
            # 旧版本配置残留键（author/avatar 已废弃）：必须被静默忽略，不影响运行
            "author": "团子和蛋糕",
            "avatar": "/assets/ziyuan/tx.webp",
            "moment_tags": ["日常"],
            "place_tags": ["旅游"],
            "default_note_dir": "日常随笔",
            "allow_users": ["u1"],
        }
        import main as plugin_main

        class Stubbed(plugin_main.BlogWriter):
            async def _upload_images(self, stored, folder=""):
                # stored: [(ref, bytes)]
                self.last_upload_folder = folder
                return ["https://img.tsh520.cn/file/" + os.path.basename(ref) for ref, _ in stored]

            async def _download_wx_media(self, enc, aes_hex, aes_b64):
                return b"fake-image-bytes"

            async def _geocode(self, address):
                return (34.477861, 110.084789)

            async def _list_notebook_names(self, repo, branch, token):
                return ["日常随笔"]

            async def _commit_md(self, path, md, now):
                self.committed.append((path, md))
                return True, path, ""

            async def _album_index(self, repo, branch, token):
                # 相册追加/新建分流用；默认空索引，各测试可覆盖 self.album_index_result
                return self.album_index_result

            async def terminate(self):
                pass

        self.plugin = Stubbed(context=types.SimpleNamespace(), config=dict(self.config))
        self.plugin.committed = []
        self.plugin.last_upload_folder = None
        self.plugin.album_index_result = {"titles": {}, "files": {}}

    async def _send(self, text, messages=None):
        ev = types.SimpleNamespace(
            message_str=text,
            get_sender_id=lambda: "u1",
            get_messages=lambda: messages or [],
            plain_result=lambda t: types.SimpleNamespace(text=t),
        )
        out = []
        async for r in self.plugin.on_message(ev):
            out.append(r)
        return [o.text for o in out]

    def test_moment_full_flow(self):
        from astrbot.api.message_components import Image

        tmp = Path(os.environ.get("TEMP", ".")) / "blogwriter_test.png"
        tmp.write_bytes(b"fake-image-bytes")

        asyncio.get_event_loop().run_until_complete(self._send("/动态 今天去了公园"))
        self.assertEqual(len(self.plugin._sessions), 1)
        replies = asyncio.get_event_loop().run_until_complete(
            self._send("", messages=[Image(file=str(tmp))])
        )
        self.assertIn("已收到 1 张图片", replies[0])
        replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
        self.assertIn("发布成功", replies[0])
        self.assertEqual(len(self.plugin.committed), 1)
        path, md = self.plugin.committed[0]
        self.assertTrue(path.startswith("src/content/动态/"))
        self.assertIn("今天去了公园", md)
        self.assertIn("blogwriter_test.png", md)  # 图片进 frontmatter images 数组
        self.assertNotIn("id: ext-", md)
        # 2026-08-13 起不再按条写 author/avatar；图片统一上传到 imgbed_upload_folder（默认 blog/moments）
        self.assertNotIn("author:", md)
        self.assertNotIn("avatar:", md)
        self.assertEqual(self.plugin.last_upload_folder, "blog/moments")
        self.assertEqual(len(self.plugin._sessions), 0)
        tmp.unlink(missing_ok=True)

    def test_note_full_flow(self):
        asyncio.get_event_loop().run_until_complete(self._send("/笔记 日常随笔 标题"))
        asyncio.get_event_loop().run_until_complete(self._send("第一段正文"))
        asyncio.get_event_loop().run_until_complete(self._send("第二段正文"))
        replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
        self.assertIn("发布成功", replies[0])
        path, md = self.plugin.committed[0]
        self.assertTrue(path.startswith("src/content/笔记本/日常随笔/"))
        self.assertIn("name: 标题", md)
        self.assertIn("第一段正文\n第二段正文", md)

    def test_moment_full_flow_with_tags(self):
        """自定义标签：默认标签 + # 标签合并去重。"""
        replies = asyncio.get_event_loop().run_until_complete(self._send("/动态 今天去了公园 #日常 #2026"))
        self.assertIn("标签：#日常 #2026", replies[0])
        replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
        self.assertIn("发布成功", replies[0])
        path, md = self.plugin.committed[0]
        self.assertIn("  - 日常", md)
        self.assertIn('  - "2026"', md)

    def test_wx_raw_media_fallback_flow(self):
        """适配器无 Image 组件时，从 raw_message 提取媒体参数走 curl 兜底。"""
        raw_message = {
            "item_list": [
                {"type": 1, "text_item": {"text": "普通文本"}},
                {
                    "type": 2,
                    "image_item": {
                        "aeskey": "11" * 16,
                        "media": {"encrypt_query_param": "ENCPARAM123"},
                    },
                },
            ]
        }

        async def run():
            ev = types.SimpleNamespace(
                message_str="",
                get_sender_id=lambda: "u1",
                get_sender_name=lambda: "用户",
                get_messages=lambda: [types.SimpleNamespace(type="text", text="普通文本")],
                plain_result=lambda t: types.SimpleNamespace(text=t),
                message_obj=types.SimpleNamespace(raw_message=raw_message),
            )
            out = []
            async for r in self.plugin.on_message(ev):
                out.append(r)
            return out

        asyncio.get_event_loop().run_until_complete(self._send("/动态 兜底测试"))
        replies = asyncio.get_event_loop().run_until_complete(run())
        self.assertEqual(len(replies), 1)
        self.assertIn("已收到 1 张图片", replies[0].text)
        replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
        self.assertIn("发布成功", replies[0])
        path, md = self.plugin.committed[0]
        self.assertIn("images:", md)
        self.assertIn("https://img.tsh520.cn/file/wxraw_ENCPARAM123", md)

    def test_moment_video_flow(self):
        """动态可发视频：Video 组件（本地 .mp4）→ 上传图床 → URL 进 images 数组。"""
        tmp = Path(os.environ.get("TEMP", ".")) / "blogwriter_video.mp4"
        tmp.write_bytes(b"fake-video-bytes")

        video_comp = types.SimpleNamespace(
            type="Video", url="", file="file://" + str(tmp), path=str(tmp)
        )
        asyncio.get_event_loop().run_until_complete(self._send("/动态 视频测试"))
        replies = asyncio.get_event_loop().run_until_complete(
            self._send("", messages=[video_comp])
        )
        self.assertIn("已收到 1 张图片", replies[0])
        replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
        self.assertIn("发布成功", replies[0])
        path, md = self.plugin.committed[0]
        self.assertIn("  - https://img.tsh520.cn/file/blogwriter_video.mp4", md)
        self.assertEqual(self.plugin.last_upload_folder, "blog/moments")
        tmp.unlink(missing_ok=True)

    def test_moment_gif_flow(self):
        """动态发 GIF 动图：Image 组件 .gif → images 数组保留 .gif。"""
        from astrbot.api.message_components import Image

        tmp = Path(os.environ.get("TEMP", ".")) / "blogwriter_gif.gif"
        tmp.write_bytes(b"GIF89a-fake")

        asyncio.get_event_loop().run_until_complete(self._send("/动态 动图测试"))
        replies = asyncio.get_event_loop().run_until_complete(
            self._send("", messages=[Image(file=str(tmp))])
        )
        self.assertIn("已收到 1 张图片", replies[0])
        replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
        self.assertIn("发布成功", replies[0])
        path, md = self.plugin.committed[0]
        self.assertIn("  - https://img.tsh520.cn/file/blogwriter_gif.gif", md)
        tmp.unlink(missing_ok=True)

    def test_note_rejects_video(self):
        """视频仅动态支持：笔记会话收到视频组件应被忽略（不误收）。"""
        tmp = Path(os.environ.get("TEMP", ".")) / "blogwriter_video2.mp4"
        tmp.write_bytes(b"fake-video-bytes")

        video_comp = types.SimpleNamespace(
            type="Video", url="", file="file://" + str(tmp), path=str(tmp)
        )
        asyncio.get_event_loop().run_until_complete(self._send("/笔记 日常随笔 标题"))
        replies = asyncio.get_event_loop().run_until_complete(
            self._send("", messages=[video_comp])
        )
        self.assertEqual(len(replies), 0)  # 未识别为媒体，放行
        self.assertEqual(len(self.plugin._sessions["u1"].images), 0)
        tmp.unlink(missing_ok=True)

    def test_wx_raw_video_fallback_flow(self):
        """动态会话中 raw_message 视频（type 5）走 curl 兜底，ref 带 .mp4 后缀。"""
        raw_message = {
            "item_list": [
                {"type": 1, "text_item": {"text": "普通文本"}},
                {
                    "type": 5,
                    "video_item": {
                        "media": {"encrypt_query_param": "VIDPARAM456", "aes_key": "22" * 16},
                        "video_size": 12345,
                    },
                },
            ]
        }

        async def run():
            ev = types.SimpleNamespace(
                message_str="",
                get_sender_id=lambda: "u1",
                get_sender_name=lambda: "用户",
                get_messages=lambda: [types.SimpleNamespace(type="text", text="普通文本")],
                plain_result=lambda t: types.SimpleNamespace(text=t),
                message_obj=types.SimpleNamespace(raw_message=raw_message),
            )
            out = []
            async for r in self.plugin.on_message(ev):
                out.append(r)
            return out

        asyncio.get_event_loop().run_until_complete(self._send("/动态 视频兜底测试"))
        replies = asyncio.get_event_loop().run_until_complete(run())
        self.assertEqual(len(replies), 1)
        self.assertIn("已收到 1 张图片", replies[0].text)
        replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
        self.assertIn("发布成功", replies[0])
        path, md = self.plugin.committed[0]
        self.assertIn("https://img.tsh520.cn/file/wxraw_VIDPARAM456.mp4", md)

    def test_wx_aes_decrypt_roundtrip(self):
        """AES-ECB 解密逻辑与微信适配器一致（加密→解密往返验证）。"""
        from Crypto.Cipher import AES as _AES

        key_hex = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
        key = bytes.fromhex(key_hex)
        raw = b"hello wechat image data"
        pad_len = 16 - (len(raw) % 16)
        padded = raw + bytes([pad_len]) * pad_len
        encrypted = _AES.new(key, _AES.MODE_ECB).encrypt(padded)
        plain = self.plugin._decrypt_wx_media(encrypted, key_hex, "")
        self.assertEqual(plain, raw)
        # b64 key 形式（parse_media_aes_key 兼容路径）
        import base64 as _b64

        plain2 = self.plugin._decrypt_wx_media(encrypted, "", _b64.b64encode(key).decode())
        self.assertEqual(plain2, raw)

    def test_image_magic_check(self):
        self.assertTrue(self.plugin._has_image_magic(b"\xff\xd8\xff\xe0rest"))
        self.assertTrue(self.plugin._has_image_magic(b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(self.plugin._has_image_magic(b"GIF89a"))
        self.assertTrue(self.plugin._has_image_magic(b"RIFF\x00\x00\x00\x00WEBP"))
        self.assertFalse(self.plugin._has_image_magic(b"not an image"))
        self.assertFalse(self.plugin._has_image_magic(b""))

    def test_friend_full_flow(self):
        """友链：/友链 → 发送键值对 → 发布 → 检查文件与路径编号。"""
        self.plugin._github_list_dir_index = self.plugin._github_list_dir_index  # 保留真实逻辑
        # 替换为假实现：模拟现有 05 个文件
        async def fake_list_dir(dir_path, repo, branch, token):
            return 6

        self.plugin._github_list_dir_index = fake_list_dir
        replies = asyncio.get_event_loop().run_until_complete(self._send("/友链"))
        self.assertIn("友链会话已创建", replies[0])
        replies = asyncio.get_event_loop().run_until_complete(
            self._send("站点名称: 测试友链\n站点描述：欢迎来玩\n站点链接 https://test.com\n头像链接: /assets/ziyuan/tx.webp")
        )
        self.assertIn("已识别", replies[0])
        replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
        self.assertIn("发布成功", replies[0])
        path, md = self.plugin.committed[0]
        self.assertEqual(path, "src/content/友链/06-测试友链.md")
        self.assertIn("title: 测试友链", md)
        self.assertIn("siteurl: https://test.com", md)
        self.assertIn("enabled: true", md)

    def test_friend_missing_fields(self):
        """友链缺字段发布时提示。"""
        asyncio.get_event_loop().run_until_complete(self._send("/友链"))
        asyncio.get_event_loop().run_until_complete(
            self._send("站点名称: 只有名称")
        )
        replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
        self.assertIn("站点链接", replies[0])
        self.assertEqual(len(self.plugin.committed), 0)

    def test_place_full_flow(self):
        from astrbot.api.message_components import Image

        tmp = Path(os.environ.get("TEMP", ".")) / "blogwriter_place.png"
        tmp.write_bytes(b"fake-image-bytes")

        asyncio.get_event_loop().run_until_complete(self._send("/足迹 陕西 华阴市华山 去找宝宝了"))
        asyncio.get_event_loop().run_until_complete(
            self._send("", messages=[Image(file=str(tmp))])
        )
        replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
        self.assertIn("发布成功", replies[0])
        path, md = self.plugin.committed[0]
        self.assertTrue(path.startswith("src/content/足迹/"))
        self.assertIn('province: "陕西"', md)
        self.assertIn("lat: 34.477861", md)
        self.assertIn("lng: 110.084789", md)
        self.assertIn('experience: "去找宝宝了"', md)
        # 足迹照片不再单独放 places 目录，统一用 imgbed_upload_folder（默认 blog/moments）
        self.assertEqual(self.plugin.last_upload_folder, "blog/moments")
        tmp.unlink(missing_ok=True)

    def test_album_new_flow(self):
        """新建相册：图片上传 blog/album/<相册名>，创建 src/content/相册/<相册名>.md。"""
        from astrbot.api.message_components import Image

        tmp = Path(os.environ.get("TEMP", ".")) / "blogwriter_album.png"
        tmp.write_bytes(b"fake-image-bytes")

        asyncio.get_event_loop().run_until_complete(self._send("/相册 情侣头像"))
        replies = asyncio.get_event_loop().run_until_complete(
            self._send("", messages=[Image(file=str(tmp))])
        )
        self.assertIn("已收到 1 张图片", replies[0])
        replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
        self.assertIn("发布成功", replies[0])
        self.assertEqual(self.plugin.last_upload_folder, "blog/album/情侣头像")
        self.assertEqual(len(self.plugin.committed), 1)
        path, md = self.plugin.committed[0]
        self.assertEqual(path, "src/content/相册/情侣头像.md")
        self.assertIn("title: 情侣头像", md)
        self.assertIn('imgbedFolder: "blog/album/情侣头像"', md)
        self.assertNotIn("photos:", md)
        self.assertEqual(len(self.plugin._sessions), 0)
        tmp.unlink(missing_ok=True)

    def test_album_append_flow(self):
        """相册已存在（按文件名命中）：只传图不写文件，回复「已添加」。"""
        from astrbot.api.message_components import Image

        self.plugin.album_index_result = {
            "titles": {"Telegram武侠风": "blog/album/武侠风"},
            "files": {"武侠风.md": "blog/album/武侠风"},
        }
        tmp = Path(os.environ.get("TEMP", ".")) / "blogwriter_album2.png"
        tmp.write_bytes(b"fake-image-bytes")

        asyncio.get_event_loop().run_until_complete(self._send("/相册 武侠风"))
        asyncio.get_event_loop().run_until_complete(
            self._send("", messages=[Image(file=str(tmp))])
        )
        replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
        self.assertIn("已添加到相册", replies[0])
        self.assertEqual(self.plugin.last_upload_folder, "blog/album/武侠风")
        self.assertEqual(len(self.plugin.committed), 0)  # 不写文件、不触发构建
        self.assertEqual(len(self.plugin._sessions), 0)
        tmp.unlink(missing_ok=True)

    def test_album_append_by_title_different_filename(self):
        """按 title 命中已有相册（文件名不同）：追加到它自己的 imgbedFolder，不新建文件。

        回归场景：博客里「测试相册」在 xiangce1.md（imgbedFolder=blog/album/相册1），
        插件不能因为找不到 测试相册.md 就新建重复相册。
        """
        from astrbot.api.message_components import Image

        self.plugin.album_index_result = {
            "titles": {"测试相册": "blog/album/相册1"},
            "files": {"xiangce1.md": "blog/album/相册1"},
        }
        tmp = Path(os.environ.get("TEMP", ".")) / "blogwriter_album3.png"
        tmp.write_bytes(b"fake-image-bytes")

        asyncio.get_event_loop().run_until_complete(self._send("/相册 测试相册"))
        asyncio.get_event_loop().run_until_complete(
            self._send("", messages=[Image(file=str(tmp))])
        )
        replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
        self.assertIn("已添加到相册", replies[0])
        self.assertEqual(self.plugin.last_upload_folder, "blog/album/相册1")
        self.assertEqual(len(self.plugin.committed), 0)
        self.assertEqual(len(self.plugin._sessions), 0)
        tmp.unlink(missing_ok=True)

    def test_album_text_rejected(self):
        """相册会话只收图片：发文字给提示。"""
        asyncio.get_event_loop().run_until_complete(self._send("/相册 情侣头像"))
        replies = asyncio.get_event_loop().run_until_complete(self._send("这是副标题吗"))
        self.assertIn("只接收图片", replies[0])
        self.assertEqual(len(self.plugin.committed), 0)

    def test_album_no_images(self):
        """相册没有图片不能发布。"""
        asyncio.get_event_loop().run_until_complete(self._send("/相册 情侣头像"))
        replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
        self.assertIn("至少需要一张图片", replies[0])
        self.assertEqual(len(self.plugin.committed), 0)

    def test_permission_denied_silent(self):
        """非白名单用户：群聊与非命令一律静默放行（不抢其他插件/AI）；
        私聊发明确命令时只回一条含其 ID 的提示（2026-09-30 v1.0.57 起，
        便于跨平台把 ID 加进 allow_users，可用 unauthorized_hint 关闭）。"""

        async def run(ev):
            out = []
            async for r in self.plugin.on_message(ev):
                out.append(r.text)
            return out

        def ev(text, origin=""):
            return types.SimpleNamespace(
                message_str=text,
                get_sender_id=lambda: "hacker",
                get_sender_name=lambda: "黑客",
                get_messages=lambda: [],
                plain_result=lambda t: types.SimpleNamespace(text=t),
                unified_msg_origin=origin,
            )

        # 群聊：完全静默（含命令）
        replies = asyncio.get_event_loop().run_until_complete(
            run(ev("/动态 x", origin="aiocqhttp:GroupMessage:123456"))
        )
        self.assertEqual(len(replies), 0)
        # 私聊非命令：静默
        replies = asyncio.get_event_loop().run_until_complete(run(ev("随便聊聊")))
        self.assertEqual(len(replies), 0)
        # 私聊命令：只回 ID 提示，且不建会话
        replies = asyncio.get_event_loop().run_until_complete(run(ev("/动态 x")))
        self.assertEqual(len(replies), 1)
        self.assertIn("hacker", replies[0])
        self.assertIn("allow_users", replies[0])
        self.assertNotIn("hacker", self.plugin._sessions)

    def test_permission_denied_image_no_hint(self):
        """回归（2026-09-04 群聊回人 bug）：非白名单用户无会话发图片，
        绝不回复「无会话媒体提示」（白名单检查必须前置）。"""
        from astrbot.api.message_components import Image
        from pathlib import Path

        tmp = Path(os.environ.get("TEMP", ".")) / "blogwriter_hacker.png"
        tmp.write_bytes(b"fake")
        try:
            ev = types.SimpleNamespace(
                message_str="",
                get_sender_id=lambda: "hacker",
                get_sender_name=lambda: "黑客",
                get_messages=lambda: [Image(file=str(tmp))],
                plain_result=lambda t: types.SimpleNamespace(text=t),
                message_obj=types.SimpleNamespace(raw_message={}),
            )

            async def run():
                out = []
                async for r in self.plugin.on_message(ev):
                    out.append(r)
                return out

            replies = asyncio.get_event_loop().run_until_complete(run())
            self.assertEqual(len(replies), 0)
        finally:
            tmp.unlink(missing_ok=True)

    def test_whitelisted_image_without_session_hint(self):
        """白名单用户**私聊**无会话发图片才提示（媒体不被静默丢弃）。"""
        from astrbot.api.message_components import Image
        from pathlib import Path

        tmp = Path(os.environ.get("TEMP", ".")) / "blogwriter_owner.png"
        tmp.write_bytes(b"fake")
        try:
            replies = asyncio.get_event_loop().run_until_complete(
                self._send("", messages=[Image(file=str(tmp))])
            )
            self.assertEqual(len(replies), 1)
            self.assertIn("没有进行中的会话", replies[0])
        finally:
            tmp.unlink(missing_ok=True)

    def test_group_image_no_hint(self):
        """回归（2026-09-15）：白名单用户在群里无会话发图片 → 不插嘴（提示仅私聊）。"""
        from astrbot.api.message_components import Image
        from pathlib import Path

        tmp = Path(os.environ.get("TEMP", ".")) / "blogwriter_group.png"
        tmp.write_bytes(b"fake")
        try:
            ev = types.SimpleNamespace(
                message_str="",
                get_sender_id=lambda: "u1",
                get_sender_name=lambda: "用户",
                get_messages=lambda: [Image(file=str(tmp))],
                plain_result=lambda t: types.SimpleNamespace(text=t),
                message_obj=types.SimpleNamespace(raw_message={}),
                unified_msg_origin="aiocqhttp:GroupMessage:238954530",
                get_group_id=lambda: "238954530",
            )

            async def run():
                out = []
                async for r in self.plugin.on_message(ev):
                    out.append(r)
                return out

            replies = asyncio.get_event_loop().run_until_complete(run())
            self.assertEqual(len(replies), 0)
        finally:
            tmp.unlink(missing_ok=True)

    def test_allow_groups_blocks_unlisted_group(self):
        """allow_groups 非空：列表外的群完全静默（含命令）。"""
        self.plugin.config["allow_groups"] = ["111111"]
        ev = types.SimpleNamespace(
            message_str="/动态 测试",
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "用户",
            get_messages=lambda: [],
            plain_result=lambda t: types.SimpleNamespace(text=t),
            unified_msg_origin="aiocqhttp:GroupMessage:238954530",
            get_group_id=lambda: "238954530",
        )

        async def run():
            out = []
            async for r in self.plugin.on_message(ev):
                out.append(r)
            return out

        replies = asyncio.get_event_loop().run_until_complete(run())
        self.assertEqual(len(replies), 0)
        self.assertNotIn("u1", self.plugin._sessions)

    def test_allow_groups_allows_listed_group(self):
        """allow_groups 非空：列表内的群正常交互，私聊不受影响。"""
        self.plugin.config["allow_groups"] = ["238954530"]
        ev = types.SimpleNamespace(
            message_str="/动态 测试",
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "用户",
            get_messages=lambda: [],
            plain_result=lambda t: types.SimpleNamespace(text=t),
            unified_msg_origin="aiocqhttp:GroupMessage:238954530",
            get_group_id=lambda: "238954530",
        )

        async def run():
            out = []
            async for r in self.plugin.on_message(ev):
                out.append(r.text)
            return out

        replies = asyncio.get_event_loop().run_until_complete(run())
        self.assertEqual(len(replies), 1)
        self.assertIn("动态已创建", replies[0])
        # 私聊不受 allow_groups 限制
        replies2 = asyncio.get_event_loop().run_until_complete(self._send("/取消"))
        self.assertTrue(any("已取消" in r for r in replies2))

    def test_command_stops_event(self):
        """官方规范：命令被插件完整消费后必须 stop_event，阻断 LLM 阶段重复回复。"""
        stops = []
        ev = types.SimpleNamespace(
            message_str="/动态 测试",
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "用户",
            get_messages=lambda: [],
            plain_result=lambda t: types.SimpleNamespace(text=t),
            stop_event=lambda: stops.append(1),
        )

        async def run():
            out = []
            async for r in self.plugin.on_message(ev):
                out.append(r)
            return out

        replies = asyncio.get_event_loop().run_until_complete(run())
        self.assertEqual(len(replies), 1)
        self.assertEqual(len(stops), 1)

    def test_unrelated_message_no_stop(self):
        """无关消息放行时不阻断（事件继续传播给其他插件/LLM）。"""
        stops = []
        ev = types.SimpleNamespace(
            message_str="今天天气不错",
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "用户",
            get_messages=lambda: [],
            plain_result=lambda t: types.SimpleNamespace(text=t),
            stop_event=lambda: stops.append(1),
        )

        async def run():
            out = []
            async for r in self.plugin.on_message(ev):
                out.append(r)
            return out

        replies = asyncio.get_event_loop().run_until_complete(run())
        self.assertEqual(len(replies), 0)
        self.assertEqual(len(stops), 0)


    def test_unrelated_message_passthrough(self):
        """普通聊天消息（非命令、无会话）一律放行：白名单用户也不回复。"""
        replies = asyncio.get_event_loop().run_until_complete(self._send("今天天气不错"))
        self.assertEqual(len(replies), 0)
        # 非白名单用户的普通消息同样放行
        ev = types.SimpleNamespace(
            message_str="你好",
            get_sender_id=lambda: "hacker",
            get_sender_name=lambda: "黑客",
            get_messages=lambda: [],
            plain_result=lambda t: types.SimpleNamespace(text=t),
        )

        async def run():
            out = []
            async for r in self.plugin.on_message(ev):
                out.append(r)
            return out

        replies = asyncio.get_event_loop().run_until_complete(run())
        self.assertEqual(len(replies), 0)

    def test_no_token(self):
        self.plugin.config["github_token"] = ""
        replies = asyncio.get_event_loop().run_until_complete(self._send("/动态 x"))
        self.assertIn("GitHub Token", replies[0])

    def test_cancel(self):
        asyncio.get_event_loop().run_until_complete(self._send("/动态 内容"))
        self.assertEqual(len(self.plugin._sessions), 1)
        replies = asyncio.get_event_loop().run_until_complete(self._send("/取消"))
        self.assertIn("已取消", replies[0])
        self.assertEqual(len(self.plugin._sessions), 0)


class TestMediaFlow(unittest.TestCase):
    """粘合层影视流程（TMDB 搜索 → 评分/标签/影评 → 发布）+ GitHub 提交"""

    def setUp(self):
        self.config = {
            "github_token": "tok",
            "github_repo": "tianshihao2003/dumplingandcakeblog",
            "github_branch": "main",
            "allow_users": ["u1"],
        }

        import main as plugin_main

        class Stubbed(plugin_main.BlogWriter):
            async def _upload_images(self, stored, folder=""):
                self.last_upload_folder = folder
                return ["https://img.tsh520.cn/file/" + os.path.basename(ref) for ref, _ in stored]

            async def _commit_md(self, path, md, now):
                self.committed.append((path, md))
                return True, path, ""

            async def _album_index(self, repo, branch, token):
                return self.album_index_result

            async def terminate(self):
                pass

        self.plugin = Stubbed(context=types.SimpleNamespace(), config=dict(self.config))
        self.plugin.committed = []
        self.plugin.last_upload_folder = None
        self.plugin.album_index_result = {"titles": {}, "files": {}}

    async def _send(self, text, messages=None, sender="u1"):
        ev = types.SimpleNamespace(
            message_str=text,
            get_sender_id=lambda: sender,
            get_sender_name=lambda: "用户",
            get_messages=lambda: messages or [],
            plain_result=lambda t: types.SimpleNamespace(text=t),
            message_obj=types.SimpleNamespace(raw_message={}),
        )
        out = []
        async for r in self.plugin.on_message(ev):
            out.append(r)
        return [o.text for o in out]

    def test_media_full_flow(self):
        """影视全流程：TMDB 搜索（stub）→ 评分/标签/影评 → 发布到 bangumi/anime/。"""
        import json as _json

        class FakeResp:
            status_code = 200
            text = _json.dumps(
                {"results": [{
                    "media_type": "movie", "title": "侏罗纪世界", "original_title": "Jurassic World",
                    "release_date": "2015-06-10", "overview": "恐龙主题公园",
                    "vote_average": 6.9, "poster_path": "/abc123.jpg",
                }]},
                ensure_ascii=False,
            )

        class FakeClient:
            async def get(self, url):
                return FakeResp()

        async def fake_download(url):
            return b"fake-poster-bytes"

        orig_client = self.plugin._get_client
        orig_download = self.plugin._download_http
        self.plugin._get_client = lambda: FakeClient()
        self.plugin._download_http = fake_download
        self.plugin.config["tmdb_api_key"] = "test-key"
        try:
            replies = asyncio.get_event_loop().run_until_complete(self._send("/影视 侏罗纪世界"))
            self.assertTrue(any("已找到《侏罗纪世界》" in r for r in replies))
            sess = self.plugin._sessions.get("u1")
            self.assertEqual(sess.kind, "media")
            self.assertEqual(sess.meta.get("subcategory"), "movie")
            self.assertEqual(len(sess.images), 1)  # 封面字节已入会话
            # 评分
            replies = asyncio.get_event_loop().run_until_complete(self._send("评分 8"))
            self.assertTrue(any("评分：8" in r for r in replies))
            # 标签 + 影评
            replies = asyncio.get_event_loop().run_until_complete(self._send("#科幻 好看"))
            self.assertEqual(sess.meta.get("tags"), ["科幻"])
            # 发布
            replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
            self.assertTrue(any("发布成功" in r for r in replies))
            path, md = self.plugin.committed[0]
            self.assertEqual(path, "src/content/影视/anime/侏罗纪世界.md")
            self.assertIn("category: anime", md)
            self.assertIn("subcategory: movie", md)
            self.assertIn("score: 8", md)
            self.assertIn("- 科幻", md)
            self.assertIn("image: https://img.tsh520.cn/file/侏罗纪世界.jpg", md)
            self.assertIn("好看", md)
            # 封面上传到独立目录
            self.assertEqual(self.plugin.last_upload_folder, "blog/bangumi")
        finally:
            self.plugin._get_client = orig_client
            self.plugin._download_http = orig_download

    def test_media_no_key_hint(self):
        self.plugin.config["tmdb_api_key"] = ""
        replies = asyncio.get_event_loop().run_until_complete(self._send("/影视 侏罗纪世界"))
        self.assertTrue(any("tmdb_api_key" in r for r in replies))

    def _make_image_msg(self, name="book.png"):
        from astrbot.api.message_components import Image
        from pathlib import Path as _P
        import os as _os

        tmp = _P(_os.environ.get("TEMP", ".")) / ("blogwriter_" + name)
        tmp.write_bytes(b"fake-cover-bytes")
        return [Image(file=str(tmp))], tmp

    def test_book_full_flow(self):
        """书籍全流程：/书籍 → 发封面（必须）→ 评分/标签/书评 → 发布 bangumi/book/。"""
        asyncio.get_event_loop().run_until_complete(self._send("/书籍 认知觉醒"))
        sess = self.plugin._sessions.get("u1")
        self.assertEqual(sess.kind, "book")
        self.assertEqual(sess.meta.get("title"), "认知觉醒")
        # 直接发布应被拦（缺封面）
        replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
        self.assertFalse(any("发布成功" in r for r in replies))
        self.assertTrue(any("封面" in r for r in replies))
        # 发封面图
        msgs, tmp = self._make_image_msg()
        try:
            replies = asyncio.get_event_loop().run_until_complete(self._send("", messages=msgs))
            self.assertTrue(any("封面已更新" in r for r in replies))
            self.assertEqual(len(sess.images), 1)
            # 评分 + 标签 + 书评
            asyncio.get_event_loop().run_until_complete(self._send("评分 9"))
            asyncio.get_event_loop().run_until_complete(self._send("#心理 很有启发"))
            replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
            self.assertTrue(any("发布成功" in r for r in replies))
            path, md = self.plugin.committed[0]
            self.assertEqual(path, "src/content/影视/book/认知觉醒.md")
            self.assertIn("category: book", md)
            self.assertNotIn("subcategory", md)
            self.assertIn("score: 9", md)
            self.assertIn("- 心理", md)
            self.assertIn("很有启发", md)
            self.assertEqual(self.plugin.last_upload_folder, "blog/bangumi")
        finally:
            tmp.unlink(missing_ok=True)

    def test_media_manual_mode_when_not_found(self):
        """TMDB 搜不到 → 手动模式：发图补封面 → 发布。"""
        import json as _json

        class FakeResp:
            status_code = 200
            text = _json.dumps({"results": []})

        class FakeClient:
            async def get(self, url):
                return FakeResp()

        orig_client = self.plugin._get_client
        self.plugin._get_client = lambda: FakeClient()
        self.plugin.config["tmdb_api_key"] = "k"
        try:
            replies = asyncio.get_event_loop().run_until_complete(self._send("/影视 冷门老片"))
            self.assertTrue(any("手动模式" in r for r in replies))
            sess = self.plugin._sessions.get("u1")
            self.assertEqual(sess.meta.get("title"), "冷门老片")
            # 改名称与类型（向导下兼容 名称:/类型: 文本）
            asyncio.get_event_loop().run_until_complete(self._send("名称: 正确片名"))
            self.assertEqual(sess.meta.get("title"), "正确片名")
            # 发封面后需通过向导选类型
            msgs, tmp = self._make_image_msg("media.png")
            try:
                asyncio.get_event_loop().run_until_complete(self._send("", messages=msgs))
                sess2 = self.plugin._sessions.get("u1")
                # 发图后进入类型选择向导，选 2 = 电视剧 tv
                replies = asyncio.get_event_loop().run_until_complete(self._send("2"))
                self.assertTrue(any("评分" in r or "类型" in r for r in replies))
                # 跳过评分标签
                asyncio.get_event_loop().run_until_complete(self._send("跳过"))
                asyncio.get_event_loop().run_until_complete(self._send("跳过"))
                replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
                self.assertTrue(any("发布成功" in r for r in replies))
                path, md = self.plugin.committed[0]
                self.assertEqual(path, "src/content/影视/anime/正确片名.md")
                self.assertIn("subcategory: tv", md)
            finally:
                tmp.unlink(missing_ok=True)
        finally:
            self.plugin._get_client = orig_client

    def test_media_replace_cover_with_user_image(self):
        """TMDB 命中后用户发图 → 替换封面（仍只有一张）。"""
        import json as _json

        class FakeResp:
            status_code = 200
            text = _json.dumps({"results": [{
                "media_type": "movie", "title": "侏罗纪世界", "original_title": "Jurassic World",
                "release_date": "2015-06-10", "vote_average": 6.9, "poster_path": "/abc.jpg",
            }]})

        class FakeClient:
            async def get(self, url):
                return FakeResp()

        async def fake_download(url):
            return b"tmdb-poster-bytes"

        orig_client = self.plugin._get_client
        orig_download = self.plugin._download_http
        self.plugin._get_client = lambda: FakeClient()
        self.plugin._download_http = fake_download
        self.plugin.config["tmdb_api_key"] = "k"
        try:
            asyncio.get_event_loop().run_until_complete(self._send("/影视 侏罗纪世界"))
            sess = self.plugin._sessions.get("u1")
            self.assertEqual(len(sess.images), 1)
            msgs, tmp = self._make_image_msg("replace.png")
            try:
                replies = asyncio.get_event_loop().run_until_complete(self._send("", messages=msgs))
                self.assertTrue(any("封面已更新" in r for r in replies))
                self.assertEqual(len(sess.images), 1)  # 替换而非追加
                self.assertEqual(sess.images[0][1], b"fake-cover-bytes")
            finally:
                tmp.unlink(missing_ok=True)
        finally:
            self.plugin._get_client = orig_client
            self.plugin._download_http = orig_download

    def test_daohang_full_flow(self):
        """导航全流程（2026-09-21 对齐博客）：xxapi 图标（stub）→ 向导选分类文件夹
        → 键值对补名称/描述 + #标签 → 发布到 daohang/<分类>/（文件名=域名 slug，无 category）。"""
        import json as _json

        class FakeResp:
            status_code = 200
            text = _json.dumps({"code": 200, "data": "https://api.iowen.cn/favicon/x.png"})

        class FakeClient:
            async def get(self, url, headers=None):
                return FakeResp()

        async def fake_download(url, referer=""):
            return b"fake-icon-bytes"

        async def fake_folders(repo, branch, token):
            return ["学习网站", "工具网站"]

        orig_client = self.plugin._get_client
        orig_download = self.plugin._download_http
        orig_folders = self.plugin._list_daohang_folders
        self.plugin._get_client = lambda: FakeClient()
        self.plugin._download_http = fake_download
        self.plugin._list_daohang_folders = fake_folders
        try:
            replies = asyncio.get_event_loop().run_until_complete(self._send("/导航 https://app.pagescms.org"))
            self.assertTrue(any("图标已就绪" in r for r in replies))
            self.assertTrue(any("请选择分类" in r for r in replies))
            sess = self.plugin._sessions.get("u1")
            self.assertEqual(sess.kind, "daohang")
            self.assertEqual(sess.meta.get("name"), "app.pagescms.org")  # 默认名称=域名
            self.assertEqual(len(sess.images), 1)
            # 向导选分类文件夹（1 = 学习网站）
            replies = asyncio.get_event_loop().run_until_complete(self._send("1"))
            self.assertTrue(any("已选分类「学习网站」" in r for r in replies))
            self.assertEqual(sess.meta.get("category"), "学习网站")
            self.assertIsNone(sess.wizard)
            # 键值对补信息（分类为文件夹）+ #标签（2026-09-21 起 tags 回归，可选）
            replies = asyncio.get_event_loop().run_until_complete(
                self._send("名称: PagesCMS\n描述: 后台管理 #工具 #建站")
            )
            self.assertTrue(any("已记录" in r for r in replies))
            self.assertEqual(sess.meta.get("name"), "PagesCMS")
            # #标签 从描述里摘走，不粘在描述上
            self.assertEqual(sess.meta.get("description"), "后台管理")
            self.assertEqual(sess.meta.get("tags"), ["工具", "建站"])
            # 发布
            replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
            self.assertTrue(any("发布成功" in r for r in replies))
            path, md = self.plugin.committed[0]
            self.assertEqual(path, "src/content/导航/学习网站/app-pagescms-org.md")
            self.assertIn("name: PagesCMS", md)
            self.assertIn("url: https://app.pagescms.org", md)
            self.assertIn("description: 后台管理", md)
            self.assertIn("icon: https://img.tsh520.cn/file/app.pagescms.org-icon.png", md)
            self.assertIn("tags:\n  - 工具\n  - 建站", md)
            # category 由文件夹决定，不再写进 frontmatter
            self.assertNotIn("category:", md)
            self.assertNotIn("color:", md)
            # 图标上传独立目录
            self.assertEqual(self.plugin.last_upload_folder, "blog/daohang")
        finally:
            self.plugin._get_client = orig_client
            self.plugin._download_http = orig_download
            self.plugin._list_daohang_folders = orig_folders

    def test_daohang_new_category_by_name(self):
        """导航向导：直接回复分类名 → 命中现有文件夹用原名（忽略空格/大小写），未命中则新建。"""
        import json as _json

        class FakeResp:
            status_code = 200
            text = _json.dumps({"code": 200, "data": "https://api.iowen.cn/favicon/x.png"})

        class FakeClient:
            async def get(self, url, headers=None):
                return FakeResp()

        async def fake_download(url, referer=""):
            return b"fake-icon-bytes"

        async def fake_folders(repo, branch, token):
            return ["AI 工具", "学习网站"]

        orig_client = self.plugin._get_client
        orig_download = self.plugin._download_http
        orig_folders = self.plugin._list_daohang_folders
        self.plugin._get_client = lambda: FakeClient()
        self.plugin._download_http = fake_download
        self.plugin._list_daohang_folders = fake_folders
        try:
            # 命中现有文件夹（输入无空格小写形式，应还原为「AI 工具」）
            asyncio.get_event_loop().run_until_complete(self._send("/导航 https://example.com"))
            replies = asyncio.get_event_loop().run_until_complete(self._send("ai工具"))
            sess = self.plugin._sessions.get("u1")
            self.assertTrue(any("已选分类「AI 工具」" in r for r in replies))
            self.assertEqual(sess.meta.get("category"), "AI 工具")
            # 未命中 → 新建分类
            asyncio.get_event_loop().run_until_complete(self._send("/取消"))
            asyncio.get_event_loop().run_until_complete(self._send("/导航 https://example2.com"))
            replies = asyncio.get_event_loop().run_until_complete(self._send("摄影网站"))
            sess = self.plugin._sessions.get("u1")
            self.assertTrue(any("已新建分类「摄影网站」" in r for r in replies))
            self.assertEqual(sess.meta.get("category"), "摄影网站")
        finally:
            self.plugin._get_client = orig_client
            self.plugin._download_http = orig_download
            self.plugin._list_daohang_folders = orig_folders













class TestWizardFlow(unittest.TestCase):
    """向导冒烟：空参进向导→数字选择→完成"""

    def setUp(self):
        self.config = {
            "github_token": "tok",
            "github_repo": "tianshihao2003/dumplingandcakeblog",
            "github_branch": "main",
            "allow_users": ["u1"],
            "default_note_dir": "日常随笔",
        }
        import main as plugin_main

        class Stubbed(plugin_main.BlogWriter):
            async def _upload_images(self, stored, folder=""):
                self.last_upload_folder = folder
                return ["https://img.tsh520.cn/file/" + os.path.basename(ref) for ref, _ in stored]

            async def _commit_md(self, path, md, now):
                self.committed.append((path, md))
                return True, path, ""

            async def _album_index(self, repo, branch, token):
                return self.album_index_result

            async def _list_notebook_names(self, repo, branch, token):
                return ["日常随笔", "喜马拉雅"]

            async def _geocode(self, address):
                return (34.477, 110.084)

            async def terminate(self):
                pass

        self.plugin = Stubbed(context=types.SimpleNamespace(), config=dict(self.config))
        self.plugin.committed = []
        self.plugin.album_index_result = {"titles": {"情侣头像": "blog/album/情侣头像"}, "files": {"2026-spring.md": "blog/album/情侣头像"}}
        import main as pm
        self.pm = pm

    async def _send(self, text, sender="u1", messages=None):
        import types as _types
        ev = _types.SimpleNamespace(
            message_str=text,
            get_sender_id=lambda: sender,
            get_sender_name=lambda: "用户",
            get_messages=lambda: messages or [],
            plain_result=lambda t: _types.SimpleNamespace(text=t),
            message_obj=_types.SimpleNamespace(raw_message={}),
        )
        out = []
        async for r in self.plugin.on_message(ev):
            out.append(r)
        return [o.text for o in out]

    def test_note_wizard_pick_and_title(self):
        # /笔记 空参 → 选笔记本 → 输标题 → 发正文 → 发布
        replies = asyncio.get_event_loop().run_until_complete(self._send("/笔记"))
        self.assertTrue(any("请选择笔记本" in r for r in replies))
        replies = asyncio.get_event_loop().run_until_complete(self._send("1"))
        self.assertTrue(any("标题" in r for r in replies))
        replies = asyncio.get_event_loop().run_until_complete(self._send("我的新标题"))
        self.assertTrue(any("笔记已创建" in r for r in replies))
        sess = self.plugin._sessions.get("u1")
        self.assertEqual(sess.meta.get("note_dir"), "日常随笔")
        self.assertEqual(sess.meta.get("title"), "我的新标题")

    def test_note_wizard_cancel(self):
        asyncio.get_event_loop().run_until_complete(self._send("/笔记"))
        replies = asyncio.get_event_loop().run_until_complete(self._send("取消"))
        self.assertTrue(any("已取消" in r for r in replies))
        self.assertNotIn("u1", self.plugin._sessions)

    def test_album_wizard_pick_existing(self):
        self.plugin.album_index_result = {
            "titles": {"武侠风": "blog/album/武侠风", "情侣头像": "blog/album/情侣头像"},
            "files": {"wuxia.md": "blog/album/武侠风", "2026-spring.md": "blog/album/情侣头像"},
            # entries 顺序故意与字母序不同：验证展示与映射同源排序，选 1 必须是“情侣头像”
            "entries": [
                ("武侠风", "wuxia.md", "blog/album/武侠风"),
                ("情侣头像", "2026-spring.md", "blog/album/情侣头像"),
            ],
        }
        replies = asyncio.get_event_loop().run_until_complete(self._send("/相册"))
        self.assertTrue(any("请选择相册" in r for r in replies))
        replies = asyncio.get_event_loop().run_until_complete(self._send("1"))
        sess = self.plugin._sessions.get("u1")
        self.assertIsNotNone(sess)
        # 排序后第 1 项是 情侣头像（回归：展示与映射必须同源，防选项错位）
        self.assertEqual(sess.meta.get("name"), "情侣头像")









    def test_media_wizard_game_publish(self):
        # 影视向导选 5 游戏 → 发布到 bangumi/game/，category game、无 subcategory
        from blog_writer_core import Session
        sess = Session("media", {"title": "星露谷物语", "subcategory": "", "manual": True})
        sess.wizard = {"step": "media_pick_category"}
        self.plugin._sessions["u1"] = sess
        from astrbot.api.message_components import Image
        from pathlib import Path
        tmp = Path(os.environ.get("TEMP", ".")) / "blogwriter_game.png"
        tmp.write_bytes(b"fake")
        try:
            asyncio.get_event_loop().run_until_complete(self._send("", messages=[Image(file=str(tmp))]))
            asyncio.get_event_loop().run_until_complete(self._send("5"))
            asyncio.get_event_loop().run_until_complete(self._send("跳过"))
            asyncio.get_event_loop().run_until_complete(self._send("跳过"))
            replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
            self.assertTrue(any("发布成功" in r for r in replies))
            path, md = self.plugin.committed[0]
            self.assertTrue(path.startswith("src/content/影视/game/"))
            self.assertIn("category: game", md)
            self.assertNotIn("subcategory", md)
        finally:
            tmp.unlink(missing_ok=True)

    def test_media_wizard_anime_subcategory(self):
        # 影视向导选 3 动漫 → subcategory: anime 写入（schema 允许）
        from blog_writer_core import Session
        sess = Session("media", {"title": "测试动漫", "subcategory": "", "manual": True})
        sess.wizard = {"step": "media_pick_category"}
        self.plugin._sessions["u1"] = sess
        replies = asyncio.get_event_loop().run_until_complete(self._send("3"))
        self.assertTrue(any("评分" in r for r in replies))
        asyncio.get_event_loop().run_until_complete(self._send("跳过"))
        asyncio.get_event_loop().run_until_complete(self._send("跳过"))
        from astrbot.api.message_components import Image
        from pathlib import Path
        tmp = Path(os.environ.get("TEMP", ".")) / "blogwriter_anime.png"
        tmp.write_bytes(b"fake")
        try:
            asyncio.get_event_loop().run_until_complete(self._send("", messages=[Image(file=str(tmp))]))
            replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
            self.assertTrue(any("发布成功" in r for r in replies))
            path, md = self.plugin.committed[0]
            self.assertTrue(path.startswith("src/content/影视/anime/"))
            self.assertIn("subcategory: anime", md)
        finally:
            tmp.unlink(missing_ok=True)

    def test_note_wizard_cleans_dir_name(self):
        # 笔记向导输入带路径符号的名称 → 存清洗后的名字（防路径注入）
        asyncio.get_event_loop().run_until_complete(self._send("/笔记"))
        replies = asyncio.get_event_loop().run_until_complete(self._send("../坏/名字"))
        self.assertTrue(any("标题" in r for r in replies))
        sess = self.plugin._sessions.get("u1")
        self.assertNotIn("..", sess.meta.get("note_dir", ""))
        self.assertNotIn("/", sess.meta.get("note_dir", ""))

    def test_note_publish_creates_index_for_new_notebook(self):
        # 全新笔记本发布：先补建 _index.json 再发笔记，成功提示含“已新建笔记本”
        puts = []

        async def fake_exists(repo, path, branch, token):
            return False  # 目标不存在 → 允许创建

        async def fake_put(repo, path, md, token, branch):
            puts.append((path, md))
            return True, ""

        self.plugin._github_exists = fake_exists
        self.plugin._github_put = fake_put

        asyncio.get_event_loop().run_until_complete(self._send("/笔记"))
        asyncio.get_event_loop().run_until_complete(self._send("全新笔记本"))
        asyncio.get_event_loop().run_until_complete(self._send("标题一"))
        asyncio.get_event_loop().run_until_complete(self._send("正文内容"))
        replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
        self.assertTrue(any("发布成功" in r for r in replies))
        self.assertTrue(any("已新建笔记本" in r for r in replies))
        # 索引先于笔记提交，路径与内容正确
        self.assertEqual(len(puts), 1)
        idx_path, idx_md = puts[0]
        self.assertEqual(idx_path, "src/content/笔记本/全新笔记本/_index.json")
        self.assertIn("全新笔记本", idx_md)
        self.assertEqual(len(self.plugin.committed), 1)
        note_path, note_md = self.plugin.committed[0]
        self.assertTrue(note_path.startswith("src/content/笔记本/全新笔记本/"))
        self.assertIn("正文内容", note_md)

    def test_note_publish_existing_notebook_no_index(self):
        # 已有笔记本发布：不补建索引、成功提示不含“已新建笔记本”
        puts = []

        async def fake_put(repo, path, md, token, branch):
            puts.append((path, md))
            return True, ""

        self.plugin._github_put = fake_put
        asyncio.get_event_loop().run_until_complete(self._send("/笔记"))
        asyncio.get_event_loop().run_until_complete(self._send("1"))  # 日常随笔（stub 列表已含）
        asyncio.get_event_loop().run_until_complete(self._send("标题二"))
        asyncio.get_event_loop().run_until_complete(self._send("正文二"))
        replies = asyncio.get_event_loop().run_until_complete(self._send("/发布"))
        self.assertTrue(any("发布成功" in r for r in replies))
        self.assertFalse(any("已新建笔记本" in r for r in replies))
        self.assertEqual(len(puts), 0)  # 无索引提交
        self.assertEqual(len(self.plugin.committed), 1)

    def test_media_wizard_category(self):
        # 模拟手动模式已发图后进入选类型
        from blog_writer_core import Session
        sess = Session("media", {"title": "测试片", "subcategory": "", "manual": True})
        sess.wizard = {"step": "media_pick_category"}
        self.plugin._sessions["u1"] = sess
        replies = asyncio.get_event_loop().run_until_complete(self._send("1"))
        self.assertTrue(any("评分" in r for r in replies))
        self.assertEqual(sess.meta.get("subcategory"), "movie")
        replies = asyncio.get_event_loop().run_until_complete(self._send("8"))
        self.assertTrue(any("标签" in r for r in replies))
        self.assertEqual(sess.meta.get("score"), 8)
        replies = asyncio.get_event_loop().run_until_complete(self._send("#科幻"))
        sess2 = self.plugin._sessions.get("u1")
        self.assertIn("科幻", sess2.meta.get("tags", []))

    def test_fast_track_still_works(self):
        # 带参快车道不受影响
        replies = asyncio.get_event_loop().run_until_complete(self._send("/笔记 日常随笔 快捷标题"))
        self.assertTrue(any("笔记已创建" in r for r in replies))
        self.assertIsNone(self.plugin._sessions["u1"].wizard)


try:
    from tests.test_core import WX_ARTICLE_HTML
except ImportError:  # unittest discover -s tests 模式
    from test_core import WX_ARTICLE_HTML


class TestArticleFlow(unittest.TestCase):
    """微信公众号转载：/转载 链接 → 抓取 → 图片转存图床 → 生成 posts 文章 → 提交。"""

    def setUp(self):
        self.config = {
            "github_token": "tok",
            "github_repo": "tianshihao2003/dumplingandcakeblog",
            "github_branch": "main",
            "allow_users": ["u1"],
        }
        import main as plugin_main

        class Stubbed(plugin_main.BlogWriter):
            async def _upload_images(self, stored, folder=""):
                self.last_upload_folder = folder
                return ["https://img.tsh520.cn/file/" + os.path.basename(ref) for ref, _ in stored]

            async def _commit_md(self, path, md, now):
                self.committed.append((path, md))
                return True, path, ""

            async def terminate(self):
                pass

        self.plugin = Stubbed(context=types.SimpleNamespace(), config=dict(self.config))
        self.plugin.committed = []
        self.plugin.last_upload_folder = None

    async def _send(self, text):
        ev = types.SimpleNamespace(
            message_str=text,
            get_sender_id=lambda: "u1",
            get_sender_name=lambda: "用户",
            get_messages=lambda: [],
            plain_result=lambda t: types.SimpleNamespace(text=t),
            message_obj=types.SimpleNamespace(raw_message={}),
        )
        out = []
        async for r in self.plugin.on_message(ev):
            out.append(r)
        return [o.text for o in out]

    def _install_net(self, html, image_bytes=b"\xff\xd8\xff fake-jpeg"):
        class FakeResp:
            status_code = 200

            def __init__(self, text):
                self.text = text

        class FakeClient:
            async def get(self, url, headers=None):
                return FakeResp(html)

        orig_client = self.plugin._get_client
        orig_dl = self.plugin._download_http
        self.plugin._get_client = lambda: FakeClient()

        async def fake_dl(url, referer=""):
            return image_bytes

        self.plugin._download_http = fake_dl
        return orig_client, orig_dl

    def _restore(self, orig):
        self.plugin._get_client, self.plugin._download_http = orig

    def test_full_flow(self):
        orig = self._install_net(WX_ARTICLE_HTML)
        try:
            replies = asyncio.get_event_loop().run_until_complete(
                self._send("/转载 https://mp.weixin.qq.com/s/abc")
            )
            self.assertTrue(any("转载成功" in r for r in replies))
            self.assertEqual(len(self.plugin.committed), 1)
            path, md = self.plugin.committed[0]
            self.assertEqual(path, "src/content/文章/技术分享/测试文章标题.md")
            self.assertIn("sourceLink: https://mp.weixin.qq.com/s/abc", md)
            self.assertIn("tags:\n  - 转载", md)
            self.assertIn("本文转载自微信公众号「测试公众号」", md)
            self.assertIn("https://img.tsh520.cn/file/article-1.jpg", md)
            self.assertNotIn("{{IMG_", md)
            self.assertEqual(self.plugin.last_upload_folder, "blog/article")
        finally:
            self._restore(orig)

    def test_verify_page_aborts(self):
        orig = self._install_net("<html><body>当前环境异常，完成验证后即可继续访问。</body></html>")
        try:
            replies = asyncio.get_event_loop().run_until_complete(
                self._send("/转载 https://mp.weixin.qq.com/s/abc")
            )
            self.assertTrue(any("验证" in r for r in replies))
            self.assertEqual(len(self.plugin.committed), 0)
        finally:
            self._restore(orig)

    def test_bad_url_rejected(self):
        replies = asyncio.get_event_loop().run_until_complete(
            self._send("/转载 https://example.com/x")
        )
        self.assertTrue(any("格式" in r for r in replies))
        self.assertEqual(len(self.plugin.committed), 0)

    def test_image_download_fail_aborts(self):
        orig = self._install_net(WX_ARTICLE_HTML, image_bytes=None)
        try:
            replies = asyncio.get_event_loop().run_until_complete(
                self._send("/转载 https://mp.weixin.qq.com/s/abc")
            )
            self.assertTrue(any("图片下载失败" in r for r in replies))
            self.assertEqual(len(self.plugin.committed), 0)
        finally:
            self._restore(orig)


class TestPlatformCompat(unittest.TestCase):
    """企业微信 / QQ 官方机器人平台适配（2026-09-30 v1.0.57）：

    - allow_users 与 AstrBot 全局 admins_id（event.is_admin）双白名单
    - 非白名单用户私聊发命令 → 回一条含其 ID 的提示（可关、带冷却）；群聊与非命令仍静默
    - 平台能力经 platform_meta 读取（如 support_proactive_message，企业微信为 False）
    """

    def setUp(self):
        self.config = {
            "github_token": "tok",
            "github_repo": "tianshihao2003/dumplingandcakeblog",
            "github_branch": "main",
            "allow_users": ["u1"],
        }
        import main as plugin_main

        class Stubbed(plugin_main.BlogWriter):
            async def _commit_md(self, path, md, now):
                self.committed.append((path, md))
                return True, path, ""

            async def terminate(self):
                pass

        self.plugin = Stubbed(context=types.SimpleNamespace(), config=dict(self.config))
        self.plugin.committed = []

    @staticmethod
    def _ev(text, sender="u1", platform="qq_official", group=False, admin=False, proactive=True):
        """平台化事件桩：qq_official / wecom 的私聊与群聊、是否全局管理员、是否支持主动推送。"""
        origin = "{}:{}:{}".format(
            platform, "GroupMessage" if group else "FriendMessage", "sess-1"
        )
        return types.SimpleNamespace(
            message_str=text,
            get_sender_id=lambda: sender,
            get_sender_name=lambda: "用户",
            get_messages=lambda: [],
            plain_result=lambda t: types.SimpleNamespace(text=t),
            message_obj=types.SimpleNamespace(raw_message={}),
            unified_msg_origin=origin,
            get_platform_name=lambda: platform,
            is_admin=lambda: admin,
            platform_meta=types.SimpleNamespace(support_proactive_message=proactive),
        )

    async def _send_ev(self, ev):
        out = []
        async for r in self.plugin.on_message(ev):
            out.append(r)
        return [o.text for o in out]

    def _run(self, ev):
        return asyncio.get_event_loop().run_until_complete(self._send_ev(ev))

    # ---------- 双白名单 ----------

    def test_admin_id_bypasses_allow_users(self):
        # AstrBot 全局 admins_id（event.is_admin()）里的用户在任意平台都能用，无需写进 allow_users
        self.plugin.config["allow_users"] = []
        replies = self._run(self._ev("/帮助", sender="wxid_admin", platform="wecom", admin=True))
        self.assertEqual(len(replies), 1)
        self.assertIn("BlogWriter", replies[0])
        # 同一平台的非管理员（不在任何白名单）仍被拒
        replies = self._run(self._ev("/帮助", sender="wxid_other", platform="wecom"))
        self.assertIn("allow_users", replies[0])

    def test_whitelisted_on_new_platform(self):
        # 把新平台的 ID 写进 allow_users 后，命令正常执行
        self.plugin.config["allow_users"] = ["u1", "openid-abc"]
        replies = self._run(self._ev("/帮助", sender="openid-abc", platform="qq_official"))
        self.assertEqual(len(replies), 1)
        self.assertIn("BlogWriter", replies[0])

    # ---------- 未授权 ID 提示 ----------

    def test_unauthorized_hint_private_command(self):
        replies = self._run(self._ev("/帮助", sender="openid-abc", platform="qq_official"))
        self.assertEqual(len(replies), 1)
        self.assertIn("openid-abc", replies[0])
        self.assertIn("allow_users", replies[0])
        self.assertIn("QQ 官方机器人", replies[0])
        # 冷却期内再发命令不重复提示
        replies = self._run(self._ev("/帮助", sender="openid-abc", platform="qq_official"))
        self.assertEqual(len(replies), 0)

    def test_unauthorized_hint_wecom_label(self):
        replies = self._run(self._ev("/动态 测试", sender="zhangsan", platform="wecom"))
        self.assertEqual(len(replies), 1)
        self.assertIn("zhangsan", replies[0])
        self.assertIn("企业微信", replies[0])

    def test_unauthorized_hint_group_silent(self):
        # 群聊一律静默，不抢其他插件/AI 的消息
        replies = self._run(
            self._ev("/帮助", sender="openid-abc", platform="qq_official", group=True)
        )
        self.assertEqual(len(replies), 0)

    def test_unauthorized_hint_only_for_commands(self):
        replies = self._run(self._ev("今天天气不错", sender="openid-abc", platform="qq_official"))
        self.assertEqual(len(replies), 0)

    def test_unauthorized_hint_can_be_disabled(self):
        self.plugin.config["unauthorized_hint"] = False
        replies = self._run(self._ev("/帮助", sender="openid-abc", platform="qq_official"))
        self.assertEqual(len(replies), 0)
