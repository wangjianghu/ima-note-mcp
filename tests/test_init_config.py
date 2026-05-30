from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ima_note_mcp.server import (
    IDEMPOTENCY_CACHE,
    IDEMPOTENCY_CACHE_LOCK,
    IMACredentials,
    IMAError,
    UPDATE_CHECK_DISABLE_ENV,
    UPDATE_CHECK_URL_ENV,
    build_cursor_mcp_config,
    check_for_updates,
    credentials_check,
    knowledge_add,
    knowledge_create_media,
    knowledge_get_info,
    knowledge_get_media_info,
    knowledge_list,
    knowledge_list_addable,
    knowledge_list_content,
    knowledge_search,
    note_append,
    note_create,
    note_get_content,
    note_search,
    normalize_business_response,
    post_ima_candidates,
    post_knowledge_api,
    read_secret_file_with_status,
    resolve_default_config_path,
    workflow_add_note_to_knowledge,
    workflow_get_knowledge_source,
    sanitize_utf8_text,
    summarize_credential_source,
    update_check,
    write_cursor_mcp_config,
)


class InitConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_build_cursor_mcp_config(self) -> None:
        config = build_cursor_mcp_config(
            server_name="ima-note-mcp",
            python_path="/tmp/venv/bin/python",
            client_id="${IMA_OPENAPI_CLIENTID}",
            api_key="${IMA_OPENAPI_APIKEY}",
            log_level="INFO",
        )
        self.assertIn("mcpServers", config)
        self.assertIn("ima-note-mcp", config["mcpServers"])
        item = config["mcpServers"]["ima-note-mcp"]
        self.assertEqual(item["command"], "/usr/bin/env")
        self.assertEqual(item["args"], ["/tmp/venv/bin/python", "-m", "ima_note_mcp.server"])
        self.assertEqual(item["env"]["IMA_OPENAPI_CLIENTID"], "${IMA_OPENAPI_CLIENTID}")

    def test_write_cursor_mcp_config_merge_existing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "mcp.json"
            existing = {
                "mcpServers": {
                    "other-server": {
                        "command": "/usr/bin/env",
                        "args": ["python", "-m", "other"],
                    }
                }
            }
            config_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
            config = build_cursor_mcp_config(
                server_name="ima-note-mcp",
                python_path="/tmp/venv/bin/python",
                client_id="cid",
                api_key="ak",
                log_level="INFO",
            )
            write_cursor_mcp_config(config_path, "ima-note-mcp", config)
            merged = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertIn("other-server", merged["mcpServers"])
            self.assertIn("ima-note-mcp", merged["mcpServers"])

    def test_resolve_default_config_path(self) -> None:
        self.assertEqual(resolve_default_config_path("cursor"), ".cursor/mcp.json")
        self.assertEqual(resolve_default_config_path("trae"), ".trae/mcp.json")
        self.assertEqual(resolve_default_config_path("codebuddy"), ".codebuddy/mcp.json")
        self.assertIn("claude", resolve_default_config_path("claude").lower())

    def test_credentials_read_from_config_file_when_env_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client_path = Path(temp_dir) / "client_id"
            api_path = Path(temp_dir) / "api_key"
            client_path.write_text("file_client\n", encoding="utf-8")
            api_path.write_text("file_api\n", encoding="utf-8")
            os.environ.pop("IMA_OPENAPI_CLIENTID", None)
            os.environ.pop("IMA_OPENAPI_APIKEY", None)
            with (
                mock.patch("ima_note_mcp.server.IMA_CONFIG_CLIENT_ID", client_path),
                mock.patch("ima_note_mcp.server.IMA_CONFIG_API_KEY", api_path),
            ):
                creds = IMACredentials.from_env()
                self.assertEqual(creds.client_id, "file_client")
                self.assertEqual(creds.api_key, "file_api")

    def test_credentials_env_overrides_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client_path = Path(temp_dir) / "client_id"
            api_path = Path(temp_dir) / "api_key"
            client_path.write_text("file_client\n", encoding="utf-8")
            api_path.write_text("file_api\n", encoding="utf-8")
            os.environ["IMA_OPENAPI_CLIENTID"] = "env_client"
            os.environ["IMA_OPENAPI_APIKEY"] = "env_api"
            with (
                mock.patch("ima_note_mcp.server.IMA_CONFIG_CLIENT_ID", client_path),
                mock.patch("ima_note_mcp.server.IMA_CONFIG_API_KEY", api_path),
            ):
                creds = IMACredentials.from_env()
                self.assertEqual(creds.client_id, "env_client")
                self.assertEqual(creds.api_key, "env_api")
                self.assertEqual(creds.client_id_source, "env")
                self.assertEqual(creds.api_key_source, "env")

    def test_credentials_check_reports_effective_file_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client_path = Path(temp_dir) / "client_id"
            api_path = Path(temp_dir) / "api_key"
            client_path.write_text("file_client\n", encoding="utf-8")
            api_path.write_text("file_api\n", encoding="utf-8")
            os.environ.pop("IMA_OPENAPI_CLIENTID", None)
            os.environ.pop("IMA_OPENAPI_APIKEY", None)
            with (
                mock.patch("ima_note_mcp.server.IMA_CONFIG_CLIENT_ID", client_path),
                mock.patch("ima_note_mcp.server.IMA_CONFIG_API_KEY", api_path),
            ):
                result = credentials_check()
                self.assertTrue(result["success"])
                source = result["data"]["credential_source"]
                self.assertEqual(source["client_id"], "file")
                self.assertEqual(source["api_key"], "file")
                self.assertEqual(source["effective"], "file")
                self.assertEqual(source["client_id_file_status"], "present")

    def test_read_secret_file_with_status_marks_unreadable(self) -> None:
        with mock.patch("pathlib.Path.read_text", side_effect=PermissionError("denied")):
            result = read_secret_file_with_status(Path("/tmp/blocked"))
        self.assertEqual(result.value, "")
        self.assertEqual(result.status, "unreadable")

    def test_missing_credentials_error_contains_status_details(self) -> None:
        os.environ.pop("IMA_OPENAPI_CLIENTID", None)
        os.environ.pop("IMA_OPENAPI_APIKEY", None)
        with (
            mock.patch("ima_note_mcp.server.IMA_CONFIG_CLIENT_ID", Path("/tmp/not-found-client")),
            mock.patch("ima_note_mcp.server.IMA_CONFIG_API_KEY", Path("/tmp/not-found-api")),
        ):
            with self.assertRaises(IMAError) as context:
                IMACredentials.from_env()
        self.assertEqual(context.exception.code, "IMA_AUTH_MISSING")
        self.assertEqual(context.exception.details["client_id_file_status"], "missing")
        self.assertEqual(context.exception.details["api_key_file_status"], "missing")

    def test_summarize_credential_source_supports_mixed(self) -> None:
        self.assertEqual(summarize_credential_source("env", "file"), "mixed")
        self.assertEqual(summarize_credential_source("env", "env"), "env")
        self.assertEqual(summarize_credential_source("file", "file"), "file")
        self.assertEqual(summarize_credential_source("missing", "file"), "missing")

    def test_safe_int_converts_string_to_int(self) -> None:
        from ima_note_mcp.server import _safe_int
        self.assertEqual(_safe_int("157"), 157)
        self.assertEqual(_safe_int(42), 42)
        self.assertEqual(_safe_int("abc"), 0)
        self.assertEqual(_safe_int(None), 0)
        self.assertEqual(_safe_int("abc", -1), -1)

    def test_normalize_knowledge_base_handles_content_count_string(self) -> None:
        from ima_note_mcp.server import normalize_knowledge_base
        item = {"kb_id": "kb_1", "kb_name": "知识库A", "content_count": "157", "member_count": "26822"}
        result = normalize_knowledge_base(item)
        self.assertEqual(result["item_count"], 157)
        self.assertEqual(result["knowledge_id"], "kb_1")
        self.assertEqual(result["name"], "知识库A")

    def test_normalize_knowledge_base_includes_creator_and_role(self) -> None:
        from ima_note_mcp.server import normalize_knowledge_base
        item = {"kb_id": "kb_x", "kb_name": "测试", "creator": "user_1", "role_type": "owner", "base_type": "personal"}
        result = normalize_knowledge_base(item)
        self.assertEqual(result["creator"], "user_1")
        self.assertEqual(result["role_type"], "owner")
        self.assertEqual(result["base_type"], "personal")

    def test_post_knowledge_api_expand_prefix_candidates(self) -> None:
        with mock.patch("ima_note_mcp.server.post_ima_candidates") as mock_post:
            mock_post.return_value = {"request_id": "r", "result": {}}
            result = post_knowledge_api(["list_knowledge_base_by_cursor"], {"cursor": "0"})
            self.assertEqual(result["request_id"], "r")
            mock_post.assert_called_once()
            called_endpoints = mock_post.call_args[0][0]
            self.assertIn("openapi/wiki/v1/list_knowledge_base_by_cursor", called_endpoints)
            self.assertIn("openapi/knowledge_base/v1/list_knowledge_base_by_cursor", called_endpoints)
            self.assertIn("openapi/knowledge/v1/list_knowledge_base_by_cursor", called_endpoints)

    def test_normalize_business_response_extracts_data_wrapper(self) -> None:
        result = normalize_business_response(
            {"code": 0, "msg": "ok", "data": {"folders": [{"folder_id": "f_1"}]}},
            "req_success",
        )
        self.assertEqual(result["folders"][0]["folder_id"], "f_1")

    def test_normalize_business_response_raises_for_note_business_error(self) -> None:
        with self.assertRaises(IMAError) as context:
            normalize_business_response(
                {"code": 1001, "msg": "参数不合法", "data": {"field": "limit"}},
                "req_note_error",
            )
        self.assertEqual(context.exception.code, "IMA_PARAM_INVALID")
        self.assertEqual(context.exception.details["upstream_code"], 1001)

    def test_normalize_business_response_raises_for_knowledge_business_error(self) -> None:
        with self.assertRaises(IMAError) as context:
            normalize_business_response(
                {"code": 404, "msg": "知识库不存在", "data": {"knowledge_id": "kb_x"}},
                "req_knowledge_error",
            )
        self.assertEqual(context.exception.code, "IMA_NOT_FOUND")
        self.assertEqual(context.exception.details["upstream_message"], "知识库不存在")


class KnowledgeToolTests(unittest.TestCase):
    def test_knowledge_list_parse_search_knowledge_base_response(self) -> None:
        mocked_result = {
            "info_list": [
                {"kb_id": "kb_1", "kb_name": "知识库A", "count": 3, "modify_time": 1700000001},
                {
                    "knowledge": {
                        "basic_info": {
                            "knowledge_id": "kb_2",
                            "name": "知识库B",
                            "item_count": 7,
                            "modify_time": 1700000002,
                        }
                    }
                },
            ],
            "next_cursor": "next_1",
            "is_end": False,
        }
        with mock.patch("ima_note_mcp.server.post_knowledge_api") as mocked_post:
            mocked_post.return_value = {"request_id": "req_1", "result": mocked_result}
            result = knowledge_list(cursor="cur_1", limit=2)

        mocked_post.assert_called_once_with(
            ["search_knowledge_base"],
            {"query": "", "cursor": "cur_1", "limit": 2},
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["request_id"], "req_1")
        self.assertEqual(result["data"]["page"]["cursor"], "cur_1")
        self.assertEqual(result["data"]["page"]["next_cursor"], "next_1")
        self.assertFalse(result["data"]["page"]["is_end"])
        self.assertEqual(result["data"]["knowledges"][0]["knowledge_id"], "kb_1")
        self.assertEqual(result["data"]["knowledges"][1]["knowledge_id"], "kb_2")
        self.assertEqual(result["data"]["knowledges"][0]["name"], "知识库A")

    def test_knowledge_list_addable_fallback_to_search_knowledge_base(self) -> None:
        mocked_result = {
            "info_list": [{"kb_id": "kb_1", "kb_name": "知识库A"}],
            "next_cursor": "next_1",
            "is_end": False,
        }
        with mock.patch("ima_note_mcp.server.post_knowledge_api") as mocked_post:
            mocked_post.side_effect = [
                IMAError("IMA_NOT_FOUND", "未匹配到可用接口路径，请升级服务端或检查账号权限"),
                {"request_id": "req_fallback", "result": mocked_result},
            ]
            result = knowledge_list_addable(cursor="cur_2", limit=1)

        self.assertEqual(mocked_post.call_count, 2)
        self.assertEqual(
            mocked_post.call_args_list[0].args,
            (["list_addable_knowledge_base_by_cursor", "list_addable_knowledge_base"], {"cursor": "cur_2", "limit": 1}),
        )
        self.assertEqual(
            mocked_post.call_args_list[1].args,
            (["search_knowledge_base"], {"query": "", "cursor": "cur_2", "limit": 1}),
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["fallback_used"], "search_knowledge_base")
        self.assertEqual(result["data"]["knowledges"][0]["knowledge_id"], "kb_1")

    def test_knowledge_list_content_parse_data_medias(self) -> None:
        mocked_result = {
            "data": {
                "medias": [
                    {
                        "media": {
                            "id": "m_1",
                            "title": "条目1",
                            "summary": "摘要1",
                            "url": "https://example.com/1",
                            "download_url": "https://example.com/download/1",
                            "file_name": "条目1.pdf",
                            "size": 1024,
                        }
                    },
                    {
                        "media": {
                            "basic_info": {
                                "media_id": "m_2",
                                "title": "条目2",
                                "doc_info": {"doc_id": "doc_2"},
                                "media_type": 11,
                            }
                        }
                    },
                ]
            },
            "next_cursor": "next_2",
            "is_end": True,
        }
        with mock.patch("ima_note_mcp.server.post_knowledge_api") as mocked_post:
            mocked_post.return_value = {"request_id": "req_2", "result": mocked_result}
            result = knowledge_list_content(knowledge_id="kb_1", cursor="", limit=20)

        mocked_post.assert_called_once_with(
            ["list_knowledge_content_by_cursor", "list_knowledge_media_by_cursor"],
            {"knowledge_id": "kb_1", "knowledge_base_id": "kb_1", "cursor": "", "limit": 20},
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["items"][0]["media_id"], "m_1")
        self.assertEqual(result["data"]["items"][1]["media_id"], "m_2")
        self.assertEqual(result["data"]["items"][0]["source_url"], "https://example.com/1")
        self.assertEqual(result["data"]["items"][0]["download_url"], "https://example.com/download/1")
        self.assertEqual(result["data"]["items"][0]["file_name"], "条目1.pdf")
        self.assertEqual(result["data"]["items"][0]["file_size"], 1024)
        self.assertEqual(result["data"]["items"][1]["note_doc_id"], "doc_2")
        self.assertTrue(result["data"]["page"]["is_end"])

    def test_knowledge_search_parse_highlight_info(self) -> None:
        mocked_result = {
            "items": [
                {
                    "media": {"id": "m_3", "title": "命中条目"},
                    "highlight_info": {"title": ["<em>命中</em>"]},
                }
            ],
            "is_end": True,
        }
        with mock.patch("ima_note_mcp.server.post_knowledge_api") as mocked_post:
            mocked_post.return_value = {"request_id": "req_3", "result": mocked_result}
            result = knowledge_search(knowledge_id="kb_1", query="命中", start=0, end=10)

        mocked_post.assert_called_once_with(
            ["search_knowledge_content", "search_knowledge"],
            {"knowledge_id": "kb_1", "knowledge_base_id": "kb_1", "query": "命中", "start": 0, "end": 10},
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["items"][0]["item"]["media_id"], "m_3")
        self.assertEqual(result["data"]["items"][0]["highlight_info"]["title"][0], "<em>命中</em>")
        self.assertTrue(result["data"]["page"]["is_end"])

    def test_knowledge_search_parses_info_list_items(self) -> None:
        mocked_result = {
            "info_list": [
                {"media_id": "m_flat_1", "title": "扁平结构条目", "media_type": 6},
            ],
            "is_end": True,
        }
        with mock.patch("ima_note_mcp.server.post_knowledge_api") as mocked_post:
            mocked_post.return_value = {"request_id": "req_flat", "result": mocked_result}
            result = knowledge_search(knowledge_id="kb_1", query="扁平", start=0, end=5)

        self.assertTrue(result["success"])
        self.assertEqual(len(result["data"]["items"]), 1)
        self.assertEqual(result["data"]["items"][0]["item"]["media_id"], "m_flat_1")
        self.assertEqual(result["data"]["items"][0]["item"]["title"], "扁平结构条目")

    def test_knowledge_get_info_fallback_to_search_knowledge_base(self) -> None:
        mocked_result = {
            "info_list": [{"kb_id": "kb_1", "kb_name": "知识库A"}],
            "next_cursor": "",
            "is_end": True,
        }
        with mock.patch("ima_note_mcp.server.post_knowledge_api") as mocked_post:
            mocked_post.side_effect = [
                IMAError("IMA_NOT_FOUND", "未匹配到可用接口路径，请升级服务端或检查账号权限"),
                {"request_id": "req_fallback", "result": mocked_result},
            ]
            result = knowledge_get_info("kb_1")

        self.assertEqual(mocked_post.call_count, 2)
        self.assertEqual(
            mocked_post.call_args_list[0].args,
            (["get_knowledge_base_info", "get_knowledge_info"], {"knowledge_id": "kb_1", "knowledge_base_id": "kb_1"}),
        )
        self.assertEqual(
            mocked_post.call_args_list[1].args,
            (["search_knowledge_base"], {"query": "", "cursor": "0", "limit": 20}),
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["knowledge"]["knowledge_id"], "kb_1")
        self.assertEqual(result["data"]["fallback_used"], "search_knowledge_base")

    def test_knowledge_list_content_fallback_to_search_knowledge(self) -> None:
        mocked_result = {
            "items": [{"media": {"id": "m_1", "knowledge_base_id": "kb_1", "title": "条目1"}}],
            "is_end": False,
        }
        with mock.patch("ima_note_mcp.server.post_knowledge_api") as mocked_post:
            mocked_post.side_effect = [
                IMAError("IMA_NOT_FOUND", "未匹配到可用接口路径，请升级服务端或检查账号权限"),
                {"request_id": "req_search", "result": mocked_result},
            ]
            result = knowledge_list_content(knowledge_id="kb_1", cursor="5", limit=3)

        self.assertEqual(mocked_post.call_count, 2)
        self.assertEqual(
            mocked_post.call_args_list[0].args,
            (
                ["list_knowledge_content_by_cursor", "list_knowledge_media_by_cursor"],
                {"knowledge_id": "kb_1", "knowledge_base_id": "kb_1", "cursor": "5", "limit": 3},
            ),
        )
        self.assertEqual(
            mocked_post.call_args_list[1].args,
            (["search_knowledge"], {"knowledge_id": "kb_1", "knowledge_base_id": "kb_1", "query": " ", "start": 5, "end": 8}),
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["items"][0]["knowledge_id"], "kb_1")
        self.assertEqual(result["data"]["page"]["next_cursor"], "8")
        self.assertEqual(result["data"]["fallback_used"], "search_knowledge")

    def test_knowledge_tool_error_mapping_from_upstream(self) -> None:
        with mock.patch("ima_note_mcp.server.post_knowledge_api") as mocked_post:
            mocked_post.side_effect = IMAError(
                "IMA_RATE_LIMITED",
                "触发上游限流",
                True,
                {"http_status": 429},
            )
            result = knowledge_get_info("kb_1")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "IMA_RATE_LIMITED")
        self.assertTrue(result["error"]["retryable"])
        self.assertEqual(result["error"]["details"]["http_status"], 429)

    def test_knowledge_get_media_info_supports_cross_module_flags(self) -> None:
        mocked_result = {
            "media_info": {
                "media_id": "m_9",
                "title": "知识库中的笔记",
                "media_type": 11,
                "doc_info": {"doc_id": "doc_9"},
                "source_url": "",
                "download_url": "",
            }
        }
        with mock.patch("ima_note_mcp.server.post_knowledge_api") as mocked_post:
            mocked_post.return_value = {"request_id": "req_9", "result": mocked_result}
            result = knowledge_get_media_info("m_9")

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["item"]["note_doc_id"], "doc_9")
        self.assertTrue(result["data"]["view_source_supported"])
        self.assertTrue(result["data"]["requires_note_module"])

    def test_knowledge_get_media_info_extracts_url_info_url(self) -> None:
        mocked_result = {
            "media_type": 6,
            "url_info": {"url": "https://mp.weixin.qq.com/s/example", "headers": {}},
        }
        with mock.patch("ima_note_mcp.server.post_knowledge_api") as mocked_post:
            mocked_post.return_value = {"request_id": "req_url", "result": mocked_result}
            result = knowledge_get_media_info("m_wechat")

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["item"]["source_url"], "https://mp.weixin.qq.com/s/example")
        self.assertTrue(result["data"]["view_source_supported"])

    def test_knowledge_add_invalid_source_url(self) -> None:
        result = knowledge_add(knowledge_id="kb_1", media_type=10, source_url="ftp://invalid-url")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "IMA_PARAM_INVALID")

    def test_knowledge_add_rejects_video_link(self) -> None:
        result = knowledge_add(knowledge_id="kb_1", media_type=10, source_url="https://www.youtube.com/watch?v=1")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "IMA_UNSUPPORTED_MEDIA")

    def test_knowledge_add_rejects_file_scheme(self) -> None:
        result = knowledge_add(knowledge_id="kb_1", media_type=10, source_url="file:///tmp/demo.pdf")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "IMA_UNSUPPORTED_MEDIA")

    def test_knowledge_create_media_returns_upload_summary_fields(self) -> None:
        mocked_result = {
            "media_id": "m_upload",
            "upload_info": {"url": "https://cos.example.com/upload", "method": "PUT"},
            "file_name": "demo.pdf",
            "file_size": 2048,
            "content_type": "application/pdf",
        }
        with mock.patch("ima_note_mcp.server.post_knowledge_api") as mocked_post:
            mocked_post.return_value = {"request_id": "req_upload", "result": mocked_result}
            result = knowledge_create_media(
                knowledge_id="kb_1",
                file_name="demo.pdf",
                file_size=2048,
                content_type="application/pdf",
            )

        mocked_post.assert_called_once_with(
            ["create_media", "create_knowledge_media"],
            {
                "knowledge_id": "kb_1",
                "knowledge_base_id": "kb_1",
                "file_name": "demo.pdf",
                "file_size": 2048,
                "content_type": "application/pdf",
            },
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["upload_url"], "https://cos.example.com/upload")
        self.assertEqual(result["data"]["upload_method"], "PUT")
        self.assertEqual(result["data"]["file_name"], "demo.pdf")

    def test_knowledge_create_media_rejects_video_extension(self) -> None:
        result = knowledge_create_media(
            knowledge_id="kb_1",
            file_name="demo.mp4",
            file_size=2048,
            content_type="application/octet-stream",
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "IMA_UNSUPPORTED_MEDIA")

    def test_knowledge_create_media_rejects_video_content_type(self) -> None:
        result = knowledge_create_media(
            knowledge_id="kb_1",
            file_name="demo.bin",
            file_size=2048,
            content_type="video/mp4",
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "IMA_UNSUPPORTED_MEDIA")

    def test_knowledge_add_returns_title_and_source_url(self) -> None:
        mocked_result = {
            "id": "ki_1",
            "media_id": "m_web",
            "title": "官网首页",
            "source_url": "https://example.com",
        }
        with mock.patch("ima_note_mcp.server.post_knowledge_api") as mocked_post:
            mocked_post.return_value = {"request_id": "req_add", "result": mocked_result}
            result = knowledge_add(
                knowledge_id="kb_1",
                media_type=10,
                media_id="m_web",
                title="官网首页",
                source_url="https://example.com",
            )

        mocked_post.assert_called_once_with(
            ["add_knowledge"],
            {
                "knowledge_id": "kb_1",
                "knowledge_base_id": "kb_1",
                "media_type": 10,
                "media_id": "m_web",
                "title": "官网首页",
                "source_url": "https://example.com",
            },
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["title"], "官网首页")
        self.assertEqual(result["data"]["source_url"], "https://example.com")

    def test_knowledge_limit_out_of_range(self) -> None:
        result_low = knowledge_list(cursor="0", limit=0)
        self.assertFalse(result_low["success"])
        self.assertEqual(result_low["error"]["code"], "IMA_PARAM_INVALID")
        result_high = knowledge_list_addable(cursor="0", limit=101)
        self.assertFalse(result_high["success"])
        self.assertEqual(result_high["error"]["code"], "IMA_PARAM_INVALID")

    def test_knowledge_search_invalid_start_end(self) -> None:
        result_equal = knowledge_search(knowledge_id="kb_1", query="x", start=5, end=5)
        self.assertFalse(result_equal["success"])
        self.assertEqual(result_equal["error"]["code"], "IMA_PARAM_INVALID")
        result_reverse = knowledge_search(knowledge_id="kb_1", query="x", start=6, end=3)
        self.assertFalse(result_reverse["success"])
        self.assertEqual(result_reverse["error"]["code"], "IMA_PARAM_INVALID")
        result_negative = knowledge_search(knowledge_id="kb_1", query="x", start=-1, end=3)
        self.assertFalse(result_negative["success"])
        self.assertEqual(result_negative["error"]["code"], "IMA_PARAM_INVALID")

    def test_knowledge_empty_knowledge_id(self) -> None:
        result_info = knowledge_get_info("")
        self.assertFalse(result_info["success"])
        self.assertEqual(result_info["error"]["code"], "IMA_PARAM_INVALID")
        result_list = knowledge_list_content(knowledge_id="", cursor="", limit=10)
        self.assertFalse(result_list["success"])
        self.assertEqual(result_list["error"]["code"], "IMA_PARAM_INVALID")
        result_media = knowledge_create_media(
            knowledge_id="",
            file_name="a.txt",
            file_size=100,
            content_type="text/plain",
        )
        self.assertFalse(result_media["success"])
        self.assertEqual(result_media["error"]["code"], "IMA_PARAM_INVALID")
        result_add = knowledge_add(knowledge_id="", media_type=11, media_id="doc_1")
        self.assertFalse(result_add["success"])
        self.assertEqual(result_add["error"]["code"], "IMA_PARAM_INVALID")

    def test_note_search_invalid_start_end(self) -> None:
        result = note_search(search_type=0, query_info={"title": "t"}, start=1, end=1)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "IMA_PARAM_INVALID")

    def test_post_ima_candidates_skip_not_found_then_success(self) -> None:
        with mock.patch("ima_note_mcp.server.post_ima") as mocked_post:
            mocked_post.side_effect = [
                IMAError("IMA_NOT_FOUND", "资源不存在", False),
                {"request_id": "req_4", "result": {"ok": True}},
            ]
            result = post_ima_candidates(["path_a", "path_b"], {"x": 1})

        self.assertEqual(result["request_id"], "req_4")
        self.assertEqual(mocked_post.call_count, 2)


class NoteWriteEncodingTests(unittest.TestCase):
    def setUp(self) -> None:
        with IDEMPOTENCY_CACHE_LOCK:
            IDEMPOTENCY_CACHE.clear()

    def test_sanitize_utf8_text_normalizes_bom_and_newlines(self) -> None:
        result = sanitize_utf8_text("\ufeff第一行\r\n第二行\r第三行")
        self.assertEqual(result, "第一行\n第二行\n第三行")

    def test_sanitize_utf8_text_rejects_invalid_surrogate(self) -> None:
        with self.assertRaises(IMAError) as context:
            sanitize_utf8_text("坏字符\ud800")
        self.assertEqual(context.exception.code, "IMA_ENCODING_INVALID")

    def test_note_create_uses_sanitized_content(self) -> None:
        with mock.patch("ima_note_mcp.server.post_note_api") as mocked_post:
            mocked_post.return_value = {"request_id": "req_note_create", "result": {"doc_id": "doc_1"}}
            result = note_create(content="\ufeff你好\r\n世界", folder_id="folder_1")
        self.assertTrue(result["success"])
        payload = mocked_post.call_args[0][1]
        self.assertEqual(payload["content"], "你好\n世界")
        self.assertEqual(payload["folder_id"], "folder_1")

    def test_note_create_accepts_nested_data_doc_id(self) -> None:
        with mock.patch("ima_note_mcp.server.post_note_api") as mocked_post:
            mocked_post.return_value = {
                "request_id": "req_note_nested",
                "result": {"data": {"doc_id": "doc_nested_1"}},
            }
            result = note_create(content="正常内容")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["doc_id"], "doc_nested_1")

    def test_note_append_rejects_invalid_utf8_content(self) -> None:
        result = note_append(doc_id="doc_1", content="非法\ud800字符")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "IMA_ENCODING_INVALID")

    def test_note_append_accepts_nested_doc_wrapper(self) -> None:
        with mock.patch("ima_note_mcp.server.post_note_api") as mocked_post:
            mocked_post.return_value = {
                "request_id": "req_append_nested",
                "result": {"data": {"doc": {"basic_info": {"docid": "doc_nested_2"}}}},
            }
            result = note_append(doc_id="doc_fallback", content="追加内容")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["doc_id"], "doc_nested_2")

    def test_note_create_rejects_over_limit_after_sanitize(self) -> None:
        result = note_create(content="你" * 200001)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "IMA_CONTENT_TOO_LARGE")

    def test_note_create_idempotency_reuses_cached_result(self) -> None:
        with mock.patch("ima_note_mcp.server.post_note_api") as mocked_post:
            mocked_post.return_value = {"request_id": "req_first", "result": {"doc_id": "doc_1"}}
            first = note_create(content="同一内容", idempotency_key="idem-create-1")
            second = note_create(content="同一内容", idempotency_key="idem-create-1")
        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        self.assertFalse(first["data"]["idempotent_hit"])
        self.assertTrue(second["data"]["idempotent_hit"])
        self.assertEqual(mocked_post.call_count, 1)

    def test_note_append_idempotency_rejects_different_payload(self) -> None:
        with mock.patch("ima_note_mcp.server.post_note_api") as mocked_post:
            mocked_post.return_value = {"request_id": "req_append", "result": {"doc_id": "doc_1"}}
            first = note_append(doc_id="doc_1", content="第一次", idempotency_key="idem-append-1")
            second = note_append(doc_id="doc_1", content="第二次", idempotency_key="idem-append-1")
        self.assertTrue(first["success"])
        self.assertFalse(second["success"])
        self.assertEqual(second["error"]["code"], "IMA_DUPLICATE_REQUEST")
        self.assertEqual(mocked_post.call_count, 1)


class WorkflowToolTests(unittest.TestCase):
    def test_workflow_add_note_to_knowledge(self) -> None:
        with mock.patch("ima_note_mcp.server.knowledge_add") as mocked_add:
            mocked_add.return_value = {
                "success": True,
                "data": {
                    "knowledge_item_id": "ki_100",
                    "media_id": "doc_100",
                    "title": "会议纪要",
                },
            }
            result = workflow_add_note_to_knowledge(
                knowledge_id="kb_1",
                note_doc_id="doc_100",
                title="会议纪要",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["workflow"], "add_note_to_knowledge")
        self.assertEqual(result["data"]["knowledge_item_id"], "ki_100")
        self.assertEqual(result["data"]["media_id"], "doc_100")

    def test_workflow_get_knowledge_source_for_note(self) -> None:
        with (
            mock.patch("ima_note_mcp.server.knowledge_get_media_info") as mocked_media,
            mock.patch("ima_note_mcp.server.note_get_content") as mocked_note,
        ):
            mocked_media.return_value = {
                "success": True,
                "data": {
                    "item": {"media_id": "m_1", "media_type": 11, "note_doc_id": "doc_1"},
                    "requires_note_module": True,
                },
            }
            mocked_note.return_value = {
                "success": True,
                "data": {"doc_id": "doc_1", "title": "笔记标题", "content": "正文", "content_format": 0},
            }
            result = workflow_get_knowledge_source(media_id="m_1")

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["source_kind"], "note")
        self.assertEqual(result["data"]["next_action"], "note_content_ready")
        self.assertEqual(result["data"]["source"]["content"], "正文")

    def test_workflow_get_knowledge_source_for_web(self) -> None:
        with mock.patch("ima_note_mcp.server.knowledge_get_media_info") as mocked_media:
            mocked_media.return_value = {
                "success": True,
                "data": {
                    "item": {"media_id": "m_web", "media_type": 10, "source_url": "https://example.com"},
                    "requires_note_module": False,
                },
            }
            result = workflow_get_knowledge_source(media_id="m_web")

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["source_kind"], "web")
        self.assertEqual(result["data"]["next_action"], "open_source_url")
        self.assertEqual(result["data"]["source"]["source_url"], "https://example.com")

    def test_workflow_get_knowledge_source_propagates_error(self) -> None:
        with mock.patch("ima_note_mcp.server.knowledge_get_media_info") as mocked_media:
            mocked_media.return_value = {
                "success": False,
                "error": {"code": "IMA_NOT_FOUND", "message": "资源不存在", "retryable": False, "details": {}},
            }
            result = workflow_get_knowledge_source(media_id="m_missing")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "IMA_NOT_FOUND")


class UpdateCheckTests(unittest.TestCase):
    def test_update_check_skips_without_url(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            result = update_check()
        self.assertTrue(result["success"])
        self.assertTrue(result["data"]["skipped"])
        self.assertFalse(result["data"]["enabled"])

    def test_update_check_can_report_newer_version(self) -> None:
        with (
            mock.patch.dict(os.environ, {UPDATE_CHECK_URL_ENV: "https://example.com/update.json"}, clear=True),
            mock.patch("ima_note_mcp.server.get_project_version", return_value="0.1.0"),
            mock.patch(
                "ima_note_mcp.server.fetch_update_manifest",
                return_value={
                    "latest_version": "0.2.0",
                    "release_desc": "修复若干问题",
                    "instruction": "请升级到 0.2.0",
                },
            ),
            mock.patch("ima_note_mcp.server.save_update_check_cache") as mocked_save,
        ):
            result = update_check(force=True)
        self.assertTrue(result["success"])
        self.assertTrue(result["data"]["checked"])
        self.assertTrue(result["data"]["update_available"])
        self.assertEqual(result["data"]["latest_version"], "0.2.0")
        mocked_save.assert_called_once()

    def test_check_for_updates_respects_disable_flag(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                UPDATE_CHECK_URL_ENV: "https://example.com/update.json",
                UPDATE_CHECK_DISABLE_ENV: "1",
            },
            clear=True,
        ):
            result = check_for_updates(force=True)
        self.assertTrue(result["skipped"])
        self.assertFalse(result["enabled"])

if __name__ == "__main__":
    unittest.main()
