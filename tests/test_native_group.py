"""Group isolation, multi-command and proactive delivery at the native wire boundary."""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("waves_group_test", ROOT / "native_group.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
service_spec = importlib.util.spec_from_file_location("waves_service_test", ROOT / "native_service.py")
service = importlib.util.module_from_spec(service_spec)
service_spec.loader.exec_module(service)


class GroupProtocol(unittest.TestCase):
    def setUp(self):
        self.transport = Mock(return_value="7543")
        self.patch = patch.dict(sys.modules, {"telyincore.qq_group_route": Mock(deliver_group=self.transport)})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.bridge = native.NativeGroup({"bot_id": "TEST"}, {"ownerBot": "TEST", "_scopeDir": "/tmp"},
                                         Mock(), lambda *args: (False, False), Mock())
        self.socket = self.bridge.ws = Mock()

    def send(self, group, mid, command, *extra):
        event = {"self_id": 71, "group_id": group, "message_id": mid, "user_id": 23,
                 "sender": {"card": "测试", "role": "member"}}
        parts = [{"type": "text", "data": {"text": command}}, *extra]
        self.bridge.receive(event, parts, str)

    def reply(self, group, mid, content, echo=None):
        self.bridge.deliver(self.socket, json.dumps({"bot_id": "onebot", "bot_self_id": "71",
                            "target_type": "group", "target_id": str(group), "msg_id": str(mid),
                            "echo": echo, "content": content}))

    def test_separate_groups_and_commands(self):
        self.send(42, 11, "ww群排行")
        self.send(42, 12, "ww体力")
        self.send(42, 11, "ww群排行")
        self.send(43, 11, "ww群排行")
        frames = [json.loads(call.args[0]) for call in self.socket.send.call_args_list]
        self.assertEqual([(r["group_id"], r["msg_id"]) for r in frames],
                         [("42", "11"), ("42", "12"), ("43", "11")])
        self.reply(42, 12, [{"type": "text", "data": "体力"}])
        self.reply(43, 11, [{"type": "text", "data": "排行"}])
        self.assertEqual([c.args[2] for c in self.transport.call_args_list], ["42", "43"])
        with self.assertRaises(ValueError):
            self.reply(43, 12, [{"type": "text", "data": "错误群"}])

    def test_only_delivered_facts_reach_the_matching_normal_conversation(self):
        def event(sender="23", group="42", quote="", account="71"):
            return SimpleNamespace(source="qq", origin="external", sender_key="qq:" + sender,
                                   ts=datetime.now(timezone.utc).isoformat(),
                                   meta={"conversation": "group:" + group, "self_id": account,
                                         "reply_to_message_id": quote})
        self.send(42, 11, "ww练度群排行")
        query = "[鸣潮] 群【42】暂无练度排行数据\n请使用【ww刷新面板】后再使用此功能！"
        self.assertEqual(self.bridge.followup_context([event()]), "")
        self.reply(42, 11, [{"type":"text","data":query}])
        self.assertEqual(self.transport.call_args.args[3][0]["data"]["text"], query)
        result = self.bridge.followup_context([event()])
        self.assertIn("ww刷新面板", result)
        self.assertIn('"排行记录": 0', result)
        self.assertEqual(self.bridge.followup_context([event(sender="24")]), "")
        self.assertEqual(self.bridge.followup_context([event(sender="24", quote="7543")]), result)
        self.assertEqual(self.bridge.followup_context([event(quote="99")]), "")
        self.assertEqual(self.bridge.followup_context([event(group="43"), event()]), "")
        self.assertEqual(self.bridge.followup_context([event(account="72")]), "")
        self.send(42, 12, "ww登录")
        self.reply(42, 12, [{"type":"text","data":"SENSITIVE_LOGIN_LINK"}])
        self.assertEqual(self.bridge.followup_context([event()]), result)
        self.send(42, 13, "ww帮助")
        self.transport.side_effect = RuntimeError("failed")
        with self.assertRaises(RuntimeError):
            self.reply(42, 13, [{"type":"image","data":"base64://YWJj"}])
        self.assertEqual(self.bridge.followup_context([event()]), result)
        with patch.object(native.time, "monotonic", return_value=native.time.monotonic()+305):
            self.assertEqual(self.bridge.followup_context([event()]), "")
        self.assertFalse(self.bridge.results)

    def test_fuzzy_score_receipt_survives_for_a_followup_without_mention(self):
        self.send(42, 11, "ww连读群排行")
        event = SimpleNamespace(source="qq", origin="external", sender_key="qq:23",
                                ts=datetime.now(timezone.utc).isoformat(), text="怎么还没上榜？",
                                meta={"conversation": "group:42", "self_id": 71})
        reply = "[鸣潮] 你可能想查询【ww嘉贝莉娜评分排行】，已执行该指令"
        self.reply(42, 11, [{"type": "text", "data": reply + "\n[鸣潮] 群【42】暂无【嘉贝莉娜】面板"}])
        context = self.bridge.followup_context([event])
        self.assertIn("ww嘉贝莉娜评分排行", context)
        self.assertIn("ww练度群排行", context)
        self.assertIn('"本群缺少面板的角色": "嘉贝莉娜"', context)
        self.assertNotIn("排行记录", context)
        self.assertNotIn("连读", context)
        self.send(42, 12, "ww未知排行")
        self.reply(42, 12, [{"type": "text", "data": "任意上游文字"}])
        self.assertEqual(self.bridge.followup_context([event]), context)
        self.reply(42, 12, [{"type": "text", "data": reply},
                            {"type": "image", "data": "base64://YWJj"}])
        self.assertIn('"回执": "图片"', self.bridge.followup_context([event]))
        self.assertNotIn("base64", self.bridge.followup_context([event]))

    def test_push_and_receipt(self):
        self.reply(43, "", [{"type": "text", "data": "公告"}], echo="8")
        self.assertEqual(self.transport.call_args.args[1:3], ("71", "43"))
        ack = json.loads(self.socket.send.call_args.args[0])["content"][0]
        self.assertEqual(ack, {"type": "recall_message_id", "data": {"echo": "8", "id": "7543"}})

    def test_group_login_uses_running_bots_configuration(self):
        self.bridge.login_kind = lambda text, _: (text.startswith("ww登录"), " " in text)
        with patch.object(native, "HTTPConnection") as client:
            conn = client.return_value
            conn.getresponse.return_value.status = 409
            self.send(42, 11, "ww登录")
            self.socket.send.assert_not_called()
            notice = self.transport.call_args.args
            self.assertEqual((notice[2], notice[4]), ("42", "11"))
            self.assertIn("联系机器人管理员", notice[3][0]["data"]["text"])
            conn.request.assert_called_once_with("GET", "/_telyin/waves/group-login",
                                                 headers={"X-Waves-Token": self.bridge.token})
            conn.getresponse.return_value.status = 204
            self.send(42, 12, "ww登录")
            self.assertEqual(json.loads(self.socket.send.call_args.args[0])["msg_id"], "12")
            self.send(42, 13, "ww登录 SYNTHETIC")
            self.send(42, 14, "ww体力")
            self.assertEqual(conn.request.call_count, 2)
            self.assertEqual(self.socket.send.call_count, 2)
            conn.request.side_effect = TimeoutError()
            self.send(42, 15, "ww登录")
            self.assertIn("稍后再试", self.transport.call_args.args[3][0]["data"]["text"])
            self.assertEqual(conn.close.call_count, 3)

    def test_hosted_ordinary_login_checks_its_listener_without_changing_other_flows(self):
        self.bridge.login_port = 18801
        self.bridge.login_kind = lambda *_: (True, False)
        with patch.object(native, "HTTPConnection") as client:
            conn = client.return_value
            conn.getresponse.return_value.status = 409
            self.send(42, 11, "ww登录")
            self.socket.send.assert_not_called()
            self.assertEqual(conn.request.call_args.args[1], "/_telyin/waves/group-login?hosted")
            conn.getresponse.return_value.status = 204
            self.send(42, 12, "ww登录")
            self.assertEqual(json.loads(self.socket.send.call_args.args[0])["msg_id"], "12")
            self.send(42, 13, "ww邮箱登录")
            self.assertEqual(conn.request.call_args.args[1], "/_telyin/waves/group-login")

    def test_hosted_config_requires_a_separate_port_and_https_origin(self):
        for port, url in ((18765, ""), (0, "https://login.example.org"),
                          (18766, "http://login.example.org"), (18766, "https://login.example.org/path")):
            with self.subTest(port=port, url=url), self.assertRaises(ValueError):
                native.NativeGroup({"bot_id":"Example"}, {"ownerBot":"Example", "_scopeDir":"/tmp",
                                   "nativeLoginPort":port, "nativeLoginUrl":url}, Mock(), Mock(), Mock())

    def test_login_url_accepts_configured_origin_and_rejects_device_local_addresses(self):
        for url in ("https://waves.example.org", "https://waves.example.org/login/",
                    "http://192.168.10.2:18765", "https://[2001:db8::8]"):
            with self.subTest(url=url):
                self.assertTrue(service.has_group_login_url(url))
        for url in ("", None, "http://localhost:18765", "http://host.localhost.",
                    "http://127.1", "http://127.0.0.2", "http://2130706433", "http://[::1]",
                    "http://[::ffff:127.0.0.1]", "http://0.0.0.0", "http://[::]",
                    "https://waves.example.org:bogus", "https://user@host", "file:///tmp",
                    "http://%6cocalhost", "https://waves.example.org?", "https://waves.example.org/#"):
            with self.subTest(url=url):
                self.assertFalse(service.has_group_login_url(url))

    def test_image_reply_and_mention(self):
        self.send(42, 11, "ww评分", {"type": "image", "data": {"url": "https://a.qpic.cn/x"}},
                  {"type": "at", "data": {"qq": 23}}, {"type": "reply", "data": {"id": 99}})
        frame = json.loads(self.socket.send.call_args.args[0])
        self.assertEqual(frame["content"][1:], [{"type": "image", "data": "https://a.qpic.cn/x"},
                         {"type": "at", "data": "23"}, {"type": "reply_id", "data": "99"}])
        self.reply(42, 11, [{"type": "image", "data": "base64://YWJj"}, {"type": "group", "data": "42"}])
        self.assertEqual(self.transport.call_args.args[3], [{"type": "image", "data": {"file": "base64://YWJj"}}])
