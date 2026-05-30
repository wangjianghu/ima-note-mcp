from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP


LOGGER = logging.getLogger("ima_note_mcp")
BASE_URL = "https://ima.qq.com"
NOTE_API_PREFIX = "openapi/note/v1"
KNOWLEDGE_API_PREFIX_CANDIDATES = (
    "openapi/wiki/v1",
    "openapi/knowledge_base/v1",
    "openapi/knowledge/v1",
)
IMA_CONFIG_CLIENT_ID = Path.home() / ".config/ima/client_id"
IMA_CONFIG_API_KEY = Path.home() / ".config/ima/api_key"
IDEMPOTENCY_TTL_SECONDS = 3600
IDEMPOTENCY_CACHE_MAX_ENTRIES = 512
UPDATE_CHECK_CACHE_PATH = Path.home() / ".cache/ima-note-mcp/update-check.json"
UPDATE_CHECK_URL_ENV = "IMA_NOTE_MCP_UPDATE_CHECK_URL"
UPDATE_CHECK_DISABLE_ENV = "IMA_NOTE_MCP_DISABLE_UPDATE_CHECK"
UPDATE_CHECK_FORCE_ENV = "IMA_NOTE_MCP_FORCE_UPDATE_CHECK"
DEFAULT_PROJECT_VERSION = "0.1.0"
BLOCKED_VIDEO_EXTENSIONS = {
    ".3gp",
    ".avi",
    ".flv",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".mts",
    ".ts",
    ".webm",
    ".wmv",
}
BLOCKED_VIDEO_HOST_KEYWORDS = (
    "b23.tv",
    "bilibili.com",
    "youtube.com",
    "youtu.be",
)


class IMAError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        super().__init__(message)


@dataclass(slots=True)
class IdempotencyRecord:
    operation: Literal["note_create", "note_append"]
    fingerprint: str
    response: dict[str, Any]
    created_at: float


IDEMPOTENCY_CACHE: dict[str, IdempotencyRecord] = {}
IDEMPOTENCY_CACHE_LOCK = threading.Lock()


@dataclass(slots=True)
class IMACredentials:
    client_id: str
    api_key: str
    client_id_source: Literal["env", "file"]
    api_key_source: Literal["env", "file"]

    @staticmethod
    def from_env() -> "IMACredentials":
        """按环境变量优先、配置文件兜底的顺序解析凭证。"""
        client_lookup = resolve_credential_value("IMA_OPENAPI_CLIENTID", IMA_CONFIG_CLIENT_ID)
        api_lookup = resolve_credential_value("IMA_OPENAPI_APIKEY", IMA_CONFIG_API_KEY)
        if not client_lookup.value or not api_lookup.value:
            raise IMAError(
                code="IMA_AUTH_MISSING",
                message="缺少 IMA 凭证，请设置环境变量或 ~/.config/ima/{client_id,api_key}",
                retryable=False,
                details={
                    "client_id_source": client_lookup.source,
                    "api_key_source": api_lookup.source,
                    "client_id_file_status": client_lookup.file_status,
                    "api_key_file_status": api_lookup.file_status,
                },
            )
        return IMACredentials(
            client_id=client_lookup.value,
            api_key=api_lookup.value,
            client_id_source=client_lookup.source,
            api_key_source=api_lookup.source,
        )


@dataclass(slots=True)
class SecretFileReadResult:
    value: str
    status: Literal["present", "missing", "empty", "unreadable"]


@dataclass(slots=True)
class CredentialLookupResult:
    value: str
    source: Literal["env", "file", "missing"]
    env_present: bool
    file_status: Literal["present", "missing", "empty", "unreadable"]


def read_secret_file_with_status(path: Path) -> SecretFileReadResult:
    """读取本地凭证文件，并返回可用于排障的状态。"""
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return SecretFileReadResult(value="", status="missing")
    except OSError:
        return SecretFileReadResult(value="", status="unreadable")
    if not value:
        return SecretFileReadResult(value="", status="empty")
    return SecretFileReadResult(value=value, status="present")


def resolve_credential_value(env_name: str, file_path: Path) -> CredentialLookupResult:
    """解析单个凭证字段，优先环境变量，其次配置文件。"""
    env_value = os.getenv(env_name, "").strip()
    file_result = read_secret_file_with_status(file_path)
    if env_value:
        return CredentialLookupResult(
            value=env_value,
            source="env",
            env_present=True,
            file_status=file_result.status,
        )
    if file_result.value:
        return CredentialLookupResult(
            value=file_result.value,
            source="file",
            env_present=False,
            file_status=file_result.status,
        )
    return CredentialLookupResult(
        value="",
        source="missing",
        env_present=False,
        file_status=file_result.status,
    )


def summarize_credential_source(
    client_source: Literal["env", "file", "missing"],
    api_source: Literal["env", "file", "missing"],
) -> Literal["env", "file", "mixed", "missing"]:
    """汇总 client_id/api_key 的实际命中来源。"""
    if client_source == "missing" or api_source == "missing":
        return "missing"
    if client_source == api_source == "env":
        return "env"
    if client_source == api_source == "file":
        return "file"
    return "mixed"


def read_secret_file(path: Path) -> str:
    """兼容旧调用方，返回纯文本凭证值。"""
    return read_secret_file_with_status(path).value


def build_error_result(error: IMAError, request_id: str | None = None) -> dict[str, Any]:
    return {
        "success": False,
        "request_id": request_id or str(uuid.uuid4()),
        "error": {
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
            "details": error.details,
        },
    }


def build_success_result(data: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
    return {
        "success": True,
        "request_id": request_id or str(uuid.uuid4()),
        "data": data,
    }


def validate_limit(limit: int) -> None:
    if not isinstance(limit, int) or limit < 1 or limit > 100:
        raise IMAError("IMA_PARAM_INVALID", "limit 必须为 1~100 的整数", False)


def validate_search_range(start: int, end: int) -> None:
    if not isinstance(start, int) or start < 0:
        raise IMAError("IMA_PARAM_INVALID", "start 必须为 >=0 的整数", False)
    if not isinstance(end, int) or end <= start:
        raise IMAError("IMA_PARAM_INVALID", "end 必须为 > start 的整数", False)


def sanitize_utf8_text(text: str, field_name: str = "content") -> str:
    """清洗并校验文本可被安全编码为 UTF-8。"""
    if not isinstance(text, str):
        raise IMAError("IMA_PARAM_INVALID", f"{field_name} 必须为字符串", False)
    sanitized = text.replace("\r\n", "\n").replace("\r", "\n")
    sanitized = sanitized.removeprefix("\ufeff")
    try:
        sanitized.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise IMAError(
            "IMA_ENCODING_INVALID",
            f"{field_name} 包含非法 UTF-8 字符，请先清理异常字符后重试",
            False,
            {"field": field_name, "start": exc.start, "end": exc.end},
        ) from exc
    return sanitized


def validate_content(content: str) -> str:
    """校验正文长度与编码，并返回清洗后的安全文本。"""
    sanitized = sanitize_utf8_text(content, "content")
    if not sanitized.strip():
        raise IMAError("IMA_PARAM_INVALID", "content 不能为空", False)
    if len(sanitized) > 200_000:
        raise IMAError("IMA_CONTENT_TOO_LARGE", "content 超过长度限制（200000）", False)
    return sanitized


def validate_upload_file_metadata(file_name: str, content_type: str) -> None:
    """拦截当前不支持的上传文件类型。"""
    suffix = Path(file_name).suffix.lower()
    if suffix in BLOCKED_VIDEO_EXTENSIONS:
        raise IMAError(
            "IMA_UNSUPPORTED_MEDIA",
            "当前不支持视频文件上传，请改用 IMA 桌面客户端处理",
            False,
            {"file_name": file_name, "suffix": suffix},
        )
    if content_type.lower().startswith("video/"):
        raise IMAError(
            "IMA_UNSUPPORTED_MEDIA",
            "当前不支持视频文件上传，请改用 IMA 桌面客户端处理",
            False,
            {"file_name": file_name, "content_type": content_type},
        )


def validate_source_url_for_knowledge(source_url: str) -> None:
    """拦截当前不支持的知识库链接类型。"""
    parsed = urllib.parse.urlparse(source_url)
    scheme = parsed.scheme.lower()
    host = (parsed.netloc or "").lower()
    suffix = Path(parsed.path).suffix.lower()
    if scheme == "file":
        raise IMAError(
            "IMA_UNSUPPORTED_MEDIA",
            "当前不支持 file:// 本地文件链接，请改用 IMA 桌面客户端上传本地文件",
            False,
            {"source_url": source_url},
        )
    if scheme not in ("http", "https"):
        raise IMAError("IMA_PARAM_INVALID", "source_url 必须为 http/https 链接", False)
    if any(keyword in host for keyword in BLOCKED_VIDEO_HOST_KEYWORDS):
        raise IMAError(
            "IMA_UNSUPPORTED_MEDIA",
            "当前不支持 Bilibili/YouTube 视频链接，请改用 IMA 桌面客户端处理",
            False,
            {"source_url": source_url, "host": host},
        )
    if suffix in BLOCKED_VIDEO_EXTENSIONS:
        raise IMAError(
            "IMA_UNSUPPORTED_MEDIA",
            "当前不支持视频链接，请改用 IMA 桌面客户端处理",
            False,
            {"source_url": source_url, "suffix": suffix},
        )


def env_flag(name: str) -> bool:
    """解析布尔环境变量。"""
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def get_project_version() -> str:
    """获取当前运行版本，安装态优先，源码态回退默认版本。"""
    try:
        return importlib.metadata.version("ima-note-mcp")
    except importlib.metadata.PackageNotFoundError:
        return DEFAULT_PROJECT_VERSION


def parse_version_tuple(version: str) -> tuple[int, ...]:
    """将语义版本转换为可比较的整数元组。"""
    parts = []
    for token in version.strip().split("."):
        digits = "".join(ch for ch in token if ch.isdigit())
        parts.append(int(digits or "0"))
    return tuple(parts)


def load_update_check_cache() -> dict[str, Any]:
    """读取更新检查缓存。"""
    try:
        return json.loads(UPDATE_CHECK_CACHE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_update_check_cache(payload: dict[str, Any]) -> None:
    """写入更新检查缓存。"""
    try:
        UPDATE_CHECK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        UPDATE_CHECK_CACHE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        LOGGER.debug("update_check_cache_write_failed", exc_info=True)


def should_run_update_check(force: bool = False) -> bool:
    """判断当前是否需要执行更新检查。"""
    if env_flag(UPDATE_CHECK_DISABLE_ENV):
        return False
    if force or env_flag(UPDATE_CHECK_FORCE_ENV):
        return True
    cache = load_update_check_cache()
    today = time.strftime("%Y-%m-%d", time.localtime())
    return cache.get("checked_date") != today


def fetch_update_manifest(url: str, timeout: float = 2.0) -> dict[str, Any]:
    """获取远端更新清单。"""
    req = urllib.request.Request(
        url=url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": f"ima-note-mcp/{get_project_version()}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IMAError(
            "IMA_UPSTREAM_BAD_RESPONSE",
            "更新清单不是合法 JSON",
            False,
            {"raw": raw[:1000], "url": url},
        ) from exc
    if not isinstance(payload, dict):
        raise IMAError("IMA_UPSTREAM_BAD_RESPONSE", "更新清单根节点必须是对象", False, {"url": url})
    return payload


def check_for_updates(force: bool = False) -> dict[str, Any]:
    """检查是否存在新版本，未配置更新源时安全跳过。"""
    update_url = os.getenv(UPDATE_CHECK_URL_ENV, "").strip()
    current_version = get_project_version()
    if not update_url:
        return {
            "enabled": False,
            "checked": False,
            "skipped": True,
            "reason": f"未设置 {UPDATE_CHECK_URL_ENV}",
            "current_version": current_version,
        }
    if env_flag(UPDATE_CHECK_DISABLE_ENV):
        return {
            "enabled": False,
            "checked": False,
            "skipped": True,
            "reason": f"{UPDATE_CHECK_DISABLE_ENV}=1",
            "current_version": current_version,
        }
    if not should_run_update_check(force=force):
        cache = load_update_check_cache()
        return {
            "enabled": True,
            "checked": False,
            "skipped": True,
            "reason": "today_already_checked",
            "current_version": current_version,
            "latest_version": cache.get("latest_version", current_version),
            "update_available": bool(cache.get("update_available", False)),
            "release_desc": cache.get("release_desc", ""),
            "instruction": cache.get("instruction", ""),
            "download_url": cache.get("download_url", ""),
            "checked_date": cache.get("checked_date", ""),
        }
    manifest = fetch_update_manifest(update_url)
    latest_version = str(manifest.get("latest_version") or "").strip()
    if not latest_version:
        raise IMAError("IMA_UPSTREAM_BAD_RESPONSE", "更新清单缺少 latest_version", False, {"url": update_url})
    update_available = parse_version_tuple(latest_version) > parse_version_tuple(current_version)
    checked_date = time.strftime("%Y-%m-%d", time.localtime())
    result = {
        "enabled": True,
        "checked": True,
        "skipped": False,
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": update_available,
        "release_desc": str(manifest.get("release_desc") or ""),
        "instruction": str(manifest.get("instruction") or ""),
        "download_url": str(manifest.get("download_url") or manifest.get("url") or ""),
        "checked_date": checked_date,
        "check_url": update_url,
    }
    save_update_check_cache(result)
    return result


def maybe_warn_update_available() -> None:
    """按需执行每日一次更新检查，并通过日志提示新版本。"""
    try:
        result = check_for_updates(force=False)
    except IMAError:
        LOGGER.debug("update_check_failed", exc_info=True)
        return
    if result.get("update_available"):
        LOGGER.warning(
            "update_available current=%s latest=%s instruction=%s",
            result.get("current_version"),
            result.get("latest_version"),
            result.get("instruction") or result.get("download_url") or "请查看发布说明后升级",
        )


def map_http_error(status: int, body: str, request_id: str) -> IMAError:
    details = {"http_status": status, "upstream_body": body, "request_id": request_id}
    if status in (401, 403):
        return IMAError("IMA_AUTH_INVALID", "凭证无效或无权限", False, details)
    if status == 404:
        return IMAError("IMA_NOT_FOUND", "资源不存在", False, details)
    if status == 409:
        return IMAError("IMA_DUPLICATE_REQUEST", "重复请求或幂等冲突", False, details)
    if status == 413:
        return IMAError("IMA_CONTENT_TOO_LARGE", "内容超限", False, details)
    if status == 429:
        return IMAError("IMA_RATE_LIMITED", "触发上游限流", True, details)
    if status in (408, 504):
        return IMAError("IMA_TIMEOUT", "上游超时", True, details)
    if 500 <= status <= 599:
        return IMAError("IMA_UPSTREAM_UNAVAILABLE", "上游服务不可用", True, details)
    return IMAError("IMA_UPSTREAM_BAD_RESPONSE", "上游返回异常", False, details)


def map_business_error(code: int, message: str, request_id: str, result: dict[str, Any]) -> IMAError:
    """将业务层 code/msg 错误统一映射为 IMAError。"""
    details = {
        "request_id": request_id,
        "upstream_code": code,
        "upstream_message": message,
    }
    upstream_data = result.get("data")
    if upstream_data is not None:
        details["upstream_data"] = upstream_data
    normalized_message = message.strip() or "上游业务处理失败"
    if code in (401, 403) or "凭证" in normalized_message or "权限" in normalized_message:
        return IMAError("IMA_AUTH_INVALID", normalized_message, False, details)
    if code == 404 or "不存在" in normalized_message:
        return IMAError("IMA_NOT_FOUND", normalized_message, False, details)
    if code == 409 or "重复" in normalized_message or "已存在" in normalized_message:
        return IMAError("IMA_DUPLICATE_REQUEST", normalized_message, False, details)
    if code == 413 or "超限" in normalized_message or "过大" in normalized_message:
        return IMAError("IMA_CONTENT_TOO_LARGE", normalized_message, False, details)
    if code == 429 or "限流" in normalized_message or "频繁" in normalized_message:
        return IMAError("IMA_RATE_LIMITED", normalized_message, True, details)
    if code in (408, 504) or "超时" in normalized_message:
        return IMAError("IMA_TIMEOUT", normalized_message, True, details)
    if "参数" in normalized_message or "非法" in normalized_message or "不能为空" in normalized_message:
        return IMAError("IMA_PARAM_INVALID", normalized_message, False, details)
    return IMAError("IMA_UPSTREAM_BUSINESS_ERROR", normalized_message, False, details)


def normalize_business_response(result: Any, request_id: str) -> dict[str, Any]:
    """统一解析上游响应，兼容 code/msg/data 与直接业务对象两种格式。"""
    if not isinstance(result, dict):
        raise IMAError(
            "IMA_UPSTREAM_BAD_RESPONSE",
            "上游返回的 JSON 根节点必须是对象",
            False,
            {"request_id": request_id, "type": type(result).__name__},
        )
    if "code" not in result:
        return result
    raw_code = result.get("code")
    try:
        code = int(raw_code)
    except (TypeError, ValueError) as exc:
        raise IMAError(
            "IMA_UPSTREAM_BAD_RESPONSE",
            "上游业务 code 不是合法整数",
            False,
            {"request_id": request_id, "raw_code": raw_code},
        ) from exc
    message = str(result.get("msg") or "")
    if code != 0:
        raise map_business_error(code, message, request_id, result)
    if "data" in result:
        if isinstance(result["data"], dict):
            return result["data"]
        return {"value": result["data"]}
    return {key: value for key, value in result.items() if key not in {"code", "msg"}}


def post_ima(endpoint: str, body: dict[str, Any], timeout: float = 20.0) -> dict[str, Any]:
    """执行 IMA POST 请求，并统一处理 HTTP 与业务层错误。"""
    maybe_warn_update_available()
    creds = IMACredentials.from_env()
    request_id = str(uuid.uuid4())
    endpoint_normalized = endpoint.lstrip("/")
    url = urllib.parse.urljoin(f"{BASE_URL}/", endpoint_normalized)
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "ima-openapi-clientid": creds.client_id,
            "ima-openapi-apikey": creds.api_key,
            "x-request-id": request_id,
        },
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                result = json.loads(raw) if raw else {}
            except json.JSONDecodeError as exc:
                raise IMAError(
                    "IMA_UPSTREAM_BAD_RESPONSE",
                    "上游返回非 JSON 数据",
                    False,
                    {"request_id": request_id, "raw": raw[:1000]},
                ) from exc
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            LOGGER.info(
                "ima_call_ok endpoint=%s request_id=%s elapsed_ms=%s",
                endpoint_normalized,
                request_id,
                elapsed_ms,
            )
            normalized_result = normalize_business_response(result, request_id)
            return {"request_id": request_id, "result": normalized_result}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise map_http_error(exc.code, raw[:1000], request_id) from exc
    except urllib.error.URLError as exc:
        retryable = isinstance(exc.reason, (socket.timeout, TimeoutError))
        raise IMAError(
            "IMA_TIMEOUT" if retryable else "IMA_UPSTREAM_UNAVAILABLE",
            "连接上游失败",
            retryable=True,
            details={"request_id": request_id, "reason": str(exc.reason)},
        ) from exc


def post_ima_candidates(endpoints: list[str], body: dict[str, Any], timeout: float = 20.0) -> dict[str, Any]:
    if not endpoints:
        raise IMAError("IMA_PARAM_INVALID", "endpoints 不能为空", False)
    last_error: IMAError | None = None
    for endpoint in endpoints:
        try:
            return post_ima(endpoint, body, timeout=timeout)
        except IMAError as error:
            if error.code == "IMA_NOT_FOUND":
                last_error = error
                continue
            raise
    if last_error:
        raise IMAError(
            "IMA_NOT_FOUND",
            "未匹配到可用接口路径，请升级服务端或检查账号权限",
            False,
            {"candidates": endpoints},
        )
    raise IMAError("IMA_INTERNAL_ERROR", "接口调用失败", False)


def post_note_api(endpoint: str, body: dict[str, Any], timeout: float = 20.0) -> dict[str, Any]:
    endpoint_path = f"{NOTE_API_PREFIX}/{endpoint.lstrip('/')}"
    return post_ima(endpoint_path, body, timeout=timeout)


def post_knowledge_api(endpoint_candidates: list[str], body: dict[str, Any], timeout: float = 20.0) -> dict[str, Any]:
    full_candidates = []
    for prefix in KNOWLEDGE_API_PREFIX_CANDIDATES:
        for endpoint in endpoint_candidates:
            full_candidates.append(f"{prefix}/{endpoint.lstrip('/')}")
    return post_ima_candidates(full_candidates, body, timeout=timeout)


def normalize_doc(doc_raw: dict[str, Any]) -> dict[str, Any]:
    basic = doc_raw.get("basic_info") or doc_raw.get("basicInfo") or doc_raw
    return {
        "doc_id": basic.get("docid") or basic.get("doc_id") or "",
        "title": basic.get("title") or "",
        "summary": basic.get("summary") or "",
        "folder_id": basic.get("folder_id") or "",
        "folder_name": basic.get("folder_name") or "",
        "create_time": basic.get("create_time") or 0,
        "modify_time": basic.get("modify_time") or 0,
        "status": basic.get("status"),
    }


def extract_note_doc_id(result: dict[str, Any]) -> str:
    """从多种上游返回结构中提取笔记 doc_id。

    兼容上游 API 返回的多种字段名格式，包括 doc_id、docid、note_id 等。
    """
    candidates = [
        result,
        result.get("data") or {},
        result.get("doc") or {},
        (result.get("doc") or {}).get("basic_info") or {},
        (result.get("doc") or {}).get("basicInfo") or {},
        (result.get("data") or {}).get("doc") or {},
        ((result.get("data") or {}).get("doc") or {}).get("basic_info") or {},
        ((result.get("data") or {}).get("doc") or {}).get("basicInfo") or {},
        result.get("basic_info") or {},
        result.get("basicInfo") or {},
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        doc_id = candidate.get("doc_id") or candidate.get("docid") or candidate.get("note_id") or ""
        if isinstance(doc_id, str) and doc_id.strip():
            return doc_id.strip()
    return ""


def normalize_folder(folder_raw: dict[str, Any]) -> dict[str, Any]:
    basic = folder_raw.get("basic_info") or folder_raw.get("basicInfo") or folder_raw
    return {
        "folder_id": basic.get("folder_id") or "",
        "name": basic.get("name") or "",
        "note_number": basic.get("note_number") or 0,
        "create_time": basic.get("create_time") or 0,
        "modify_time": basic.get("modify_time") or 0,
        "folder_type": basic.get("folder_type"),
        "status": basic.get("status"),
    }


def _safe_int(value: Any, default: int = 0) -> int:
    """安全地将字符串或数字转换为整数。"""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except (ValueError, TypeError):
            return default
    return default


def normalize_knowledge_base(knowledge_raw: dict[str, Any]) -> dict[str, Any]:
    """将多种上游知识库返回结构归一化为统一格式。"""
    basic = knowledge_raw.get("basic_info") or knowledge_raw.get("basicInfo") or knowledge_raw
    item_count_raw = (
        basic.get("item_count")
        or basic.get("doc_count")
        or basic.get("count")
        or basic.get("content_count")
        or 0
    )
    return {
        "knowledge_id": (
            basic.get("knowledge_id")
            or basic.get("knowledgeId")
            or basic.get("kb_id")
            or basic.get("id")
            or ""
        ),
        "name": basic.get("name") or basic.get("kb_name") or basic.get("title") or "",
        "description": basic.get("description") or "",
        "item_count": _safe_int(item_count_raw),
        "create_time": basic.get("create_time") or 0,
        "modify_time": basic.get("modify_time") or 0,
        "status": basic.get("status"),
        "creator": basic.get("creator") or "",
        "role_type": basic.get("role_type") or "",
        "base_type": basic.get("base_type") or "",
    }


def extract_knowledge_list_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    """兼容多种知识库列表返回结构并提取知识库数组。"""
    return (
        result.get("knowledge_bases")
        or result.get("knowledges")
        or result.get("knowledge_list")
        or result.get("info_list")
        or result.get("data", {}).get("knowledge_bases")
        or result.get("data", {}).get("knowledges")
        or result.get("data", {}).get("knowledge_list")
        or result.get("data", {}).get("info_list")
        or []
    )


def build_knowledge_page_data(
    cursor: str,
    limit: int,
    result: dict[str, Any],
    knowledges: list[dict[str, Any]],
    *,
    fallback_used: str | None = None,
) -> dict[str, Any]:
    """构建统一的知识库分页返回结构。"""
    data = {
        "knowledges": knowledges,
        "page": {
            "cursor": cursor,
            "next_cursor": result.get("next_cursor", ""),
            "is_end": bool(result.get("is_end", True)),
            "limit": limit,
        },
    }
    if fallback_used:
        data["fallback_used"] = fallback_used
    return data


def build_knowledge_base_payload(knowledge_id: str, **extra_fields: Any) -> dict[str, Any]:
    """构建兼容新版字段名的知识库请求载荷。"""
    payload: dict[str, Any] = {
        "knowledge_id": knowledge_id,
        "knowledge_base_id": knowledge_id,
    }
    payload.update(extra_fields)
    return payload


def extract_knowledge_items_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    """兼容多种知识库内容返回结构并提取条目数组。"""
    return (
        result.get("items")
        or result.get("medias")
        or result.get("contents")
        or result.get("list")
        or result.get("info_list")
        or result.get("data", {}).get("items")
        or result.get("data", {}).get("medias")
        or result.get("data", {}).get("contents")
        or result.get("data", {}).get("list")
        or result.get("data", {}).get("info_list")
        or []
    )


def find_knowledge_base_by_id_via_search(knowledge_id: str, limit: int = 20, max_pages: int = 5) -> dict[str, Any]:
    """通过搜索知识库列表回退获取指定知识库详情。"""
    cursor = "0"
    last_request_id = ""
    for _ in range(max_pages):
        response = post_knowledge_api(["search_knowledge_base"], {"query": "", "cursor": cursor, "limit": limit})
        last_request_id = response["request_id"]
        result = response["result"]
        for item in extract_knowledge_list_from_result(result):
            normalized = normalize_knowledge_base(item.get("knowledge", item))
            if normalized["knowledge_id"] == knowledge_id:
                return {"request_id": last_request_id, "knowledge": normalized}
        next_cursor = result.get("next_cursor", "")
        if bool(result.get("is_end", True)) or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
    raise IMAError(
        "IMA_UPSTREAM_BUSINESS_ERROR",
        "invalid knowledge_base_id",
        False,
        {"request_id": last_request_id, "knowledge_id": knowledge_id, "fallback": "search_knowledge_base"},
    )


def normalize_knowledge_item(item_raw: dict[str, Any]) -> dict[str, Any]:
    basic = item_raw.get("basic_info") or item_raw.get("basicInfo") or item_raw
    doc_info = basic.get("doc_info") or basic.get("docInfo") or {}
    url_info = basic.get("url_info") or basic.get("urlInfo") or {}
    note_doc_id = (
        doc_info.get("doc_id")
        or doc_info.get("docid")
        or basic.get("doc_id")
        or basic.get("docid")
        or ""
    )
    return {
        "media_id": basic.get("media_id") or basic.get("id") or "",
        "media_type": basic.get("media_type"),
        "title": basic.get("title") or "",
        "summary": basic.get("summary") or "",
        "description": basic.get("description") or "",
        "source_url": basic.get("source_url") or basic.get("url") or url_info.get("url") or "",
        "download_url": basic.get("download_url") or basic.get("downloadUrl") or "",
        "content_type": basic.get("content_type") or basic.get("mime_type") or basic.get("mimeType") or "",
        "file_name": basic.get("file_name") or basic.get("filename") or basic.get("name") or "",
        "file_size": basic.get("file_size") or basic.get("size") or 0,
        "knowledge_id": basic.get("knowledge_id") or basic.get("knowledge_base_id") or "",
        "knowledge_name": basic.get("knowledge_name") or "",
        "note_doc_id": note_doc_id,
        "create_time": basic.get("create_time") or 0,
        "modify_time": basic.get("modify_time") or 0,
        "status": basic.get("status"),
    }


def safe_call(fn, *args, **kwargs) -> dict[str, Any]:
    try:
        return fn(*args, **kwargs)
    except IMAError as error:
        LOGGER.warning("ima_call_error code=%s message=%s", error.code, error.message)
        return build_error_result(error)
    except Exception as error:
        LOGGER.exception("ima_internal_error")
        return build_error_result(
            IMAError("IMA_INTERNAL_ERROR", "服务器内部异常", False, {"reason": str(error)})
        )


def require_success(result: dict[str, Any], action_name: str) -> dict[str, Any]:
    """将工具返回的标准结果解包为 data，失败时转成 IMAError。"""
    if result.get("success"):
        return result.get("data", {})
    error = result.get("error") or {}
    raise IMAError(
        error.get("code") or "IMA_INTERNAL_ERROR",
        error.get("message") or f"{action_name} 执行失败",
        bool(error.get("retryable", False)),
        error.get("details") or {"action": action_name},
    )


def build_idempotency_fingerprint(operation: str, payload: dict[str, Any]) -> str:
    """根据操作名和请求体生成稳定指纹，用于本地轻量幂等控制。"""
    canonical_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{operation}:{canonical_payload}".encode("utf-8")).hexdigest()


def prune_idempotency_cache(now: float | None = None) -> None:
    """清理过期或超量的幂等缓存记录。"""
    current = now if now is not None else time.time()
    expired_keys = [
        key
        for key, record in IDEMPOTENCY_CACHE.items()
        if current - record.created_at > IDEMPOTENCY_TTL_SECONDS
    ]
    for key in expired_keys:
        IDEMPOTENCY_CACHE.pop(key, None)
    if len(IDEMPOTENCY_CACHE) <= IDEMPOTENCY_CACHE_MAX_ENTRIES:
        return
    overflow = len(IDEMPOTENCY_CACHE) - IDEMPOTENCY_CACHE_MAX_ENTRIES
    oldest_keys = sorted(IDEMPOTENCY_CACHE, key=lambda key: IDEMPOTENCY_CACHE[key].created_at)[:overflow]
    for key in oldest_keys:
        IDEMPOTENCY_CACHE.pop(key, None)


def get_idempotency_response(
    operation: Literal["note_create", "note_append"],
    idempotency_key: str | None,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """查询是否已存在相同幂等键的成功结果。"""
    if not idempotency_key:
        return None
    fingerprint = build_idempotency_fingerprint(operation, payload)
    with IDEMPOTENCY_CACHE_LOCK:
        prune_idempotency_cache()
        record = IDEMPOTENCY_CACHE.get(idempotency_key)
        if record is None:
            return None
        if record.operation != operation or record.fingerprint != fingerprint:
            raise IMAError(
                "IMA_DUPLICATE_REQUEST",
                "相同 idempotency_key 已用于其他请求参数，请更换新的 idempotency_key",
                False,
                {"idempotency_key": idempotency_key, "operation": operation},
            )
        cached_response = dict(record.response)
        cached_data = dict(cached_response.get("data", {}))
        cached_data["idempotent_hit"] = True
        cached_response["data"] = cached_data
        return cached_response


def save_idempotency_response(
    operation: Literal["note_create", "note_append"],
    idempotency_key: str | None,
    payload: dict[str, Any],
    response: dict[str, Any],
) -> None:
    """保存成功结果，供后续相同幂等键直接复用。"""
    if not idempotency_key:
        return
    fingerprint = build_idempotency_fingerprint(operation, payload)
    stored_response = json.loads(json.dumps(response, ensure_ascii=False))
    with IDEMPOTENCY_CACHE_LOCK:
        prune_idempotency_cache()
        IDEMPOTENCY_CACHE[idempotency_key] = IdempotencyRecord(
            operation=operation,
            fingerprint=fingerprint,
            response=stored_response,
            created_at=time.time(),
        )


def build_cursor_mcp_config(
    server_name: str,
    python_path: str,
    client_id: str,
    api_key: str,
    log_level: str,
) -> dict[str, Any]:
    """构建可直接用于 Cursor 的 MCP 配置。"""
    return {
        "mcpServers": {
            server_name: {
                "command": "/usr/bin/env",
                "args": [python_path, "-m", "ima_note_mcp.server"],
                "env": {
                    "IMA_OPENAPI_CLIENTID": client_id,
                    "IMA_OPENAPI_APIKEY": api_key,
                    "IMA_NOTE_MCP_LOG_LEVEL": log_level,
                },
            }
        }
    }


def write_cursor_mcp_config(config_path: Path, server_name: str, config: dict[str, Any]) -> Path:
    """将 MCP 配置写入文件并保留其他服务配置。"""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise IMAError(
                "IMA_PARAM_INVALID",
                "现有 mcp.json 不是合法 JSON，请先修复后再重试",
                False,
                {"path": str(config_path), "reason": str(error)},
            ) from error
    else:
        existing = {}
    if not isinstance(existing, dict):
        raise IMAError("IMA_PARAM_INVALID", "mcp.json 根节点必须是对象", False, {"path": str(config_path)})
    servers = existing.get("mcpServers")
    if servers is None:
        servers = {}
    if not isinstance(servers, dict):
        raise IMAError(
            "IMA_PARAM_INVALID",
            "mcp.json 的 mcpServers 字段必须是对象",
            False,
            {"path": str(config_path)},
        )
    servers[server_name] = config["mcpServers"][server_name]
    existing["mcpServers"] = servers
    config_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return config_path


def resolve_default_config_path(ide: Literal["cursor", "trae", "codebuddy", "claude"]) -> str:
    if ide == "cursor":
        return ".cursor/mcp.json"
    if ide == "trae":
        return ".trae/mcp.json"
    if ide == "codebuddy":
        return ".codebuddy/mcp.json"
    if sys.platform == "darwin":
        return str(Path.home() / "Library/Application Support/Claude/claude_desktop_config.json")
    return str(Path.home() / ".config/claude/claude_desktop_config.json")


mcp = FastMCP("ima-note-mcp")


@mcp.tool(name="ima.update.check", description="检查 MCP 是否存在可用更新，可选强制忽略当天缓存")
def update_check(force: bool = False) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        result = check_for_updates(force=force)
        return build_success_result(result)

    return safe_call(_impl)


@mcp.tool(
    name="ima.credentials_check",
    description="检查 IMA 凭证是否可从环境变量或配置文件读取，可选执行远端连通性检查",
)
def credentials_check(check_remote: bool = False) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        client_lookup = resolve_credential_value("IMA_OPENAPI_CLIENTID", IMA_CONFIG_CLIENT_ID)
        api_lookup = resolve_credential_value("IMA_OPENAPI_APIKEY", IMA_CONFIG_API_KEY)
        client_id = bool(client_lookup.value)
        api_key = bool(api_lookup.value)
        data: dict[str, Any] = {
            "client_id_present": client_id,
            "api_key_present": api_key,
            "credential_source": {
                "client_id": client_lookup.source,
                "api_key": api_lookup.source,
                "effective": summarize_credential_source(client_lookup.source, api_lookup.source),
                "env_client_id": client_lookup.env_present,
                "env_api_key": api_lookup.env_present,
                "client_id_file_status": client_lookup.file_status,
                "api_key_file_status": api_lookup.file_status,
            },
        }
        if check_remote and client_id and api_key:
            payload = {"cursor": "0", "limit": 1}
            response = post_note_api("list_note_folder_by_cursor", payload)
            data["remote_ok"] = isinstance(response.get("result"), dict)
            return build_success_result(data, response["request_id"])
        data["remote_ok"] = False if check_remote else None
        return build_success_result(data)

    return safe_call(_impl)


@mcp.tool(name="ima.folder.list", description="分页列出笔记本列表")
def folder_list(cursor: str = "0", limit: int = 20) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        if not isinstance(cursor, str):
            raise IMAError("IMA_PARAM_INVALID", "cursor 必须为字符串", False)
        validate_limit(limit)
        payload = {"cursor": cursor, "limit": limit}
        response = post_note_api("list_note_folder_by_cursor", payload)
        result = response["result"]
        folders_raw = result.get("folders") or result.get("data", {}).get("folders") or []
        folders = [normalize_folder(item.get("folder", item)) for item in folders_raw]
        data = {
            "folders": folders,
            "page": {
                "cursor": cursor,
                "next_cursor": result.get("next_cursor", ""),
                "is_end": bool(result.get("is_end", True)),
                "limit": limit,
            },
        }
        return build_success_result(data, response["request_id"])

    return safe_call(_impl)


@mcp.tool(name="ima.note.list", description="分页列出某笔记本或全部笔记")
def note_list(folder_id: str = "", cursor: str = "", limit: int = 20) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        if not isinstance(folder_id, str) or not isinstance(cursor, str):
            raise IMAError("IMA_PARAM_INVALID", "folder_id 与 cursor 必须为字符串", False)
        validate_limit(limit)
        payload = {"folder_id": folder_id, "cursor": cursor, "limit": limit}
        response = post_note_api("list_note_by_folder_id", payload)
        result = response["result"]
        notes_raw = result.get("notes") or result.get("data", {}).get("notes") or []
        notes = []
        for item in notes_raw:
            wrapped = item.get("basic_info", item)
            doc_wrapped = wrapped.get("basic_info", wrapped)
            notes.append(normalize_doc(doc_wrapped))
        data = {
            "notes": notes,
            "page": {
                "cursor": cursor,
                "next_cursor": result.get("next_cursor", ""),
                "is_end": bool(result.get("is_end", True)),
                "limit": limit,
            },
        }
        return build_success_result(data, response["request_id"])

    return safe_call(_impl)


@mcp.tool(name="ima.note.search", description="按标题或正文检索笔记")
def note_search(
    search_type: int = 0,
    query_info: dict[str, str] | None = None,
    start: int = 0,
    end: int = 20,
) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        if search_type not in (0, 1):
            raise IMAError("IMA_PARAM_INVALID", "search_type 仅支持 0 或 1", False)
        query_info_local = query_info or {}
        if search_type == 0 and not query_info_local.get("title", "").strip():
            raise IMAError("IMA_PARAM_INVALID", "标题搜索时 query_info.title 不能为空", False)
        if search_type == 1 and not query_info_local.get("content", "").strip():
            raise IMAError("IMA_PARAM_INVALID", "正文搜索时 query_info.content 不能为空", False)
        validate_search_range(start, end)
        payload = {
            "search_type": search_type,
            "query_info": query_info_local,
            "start": start,
            "end": end,
        }
        response = post_note_api("search_note_book", payload)
        result = response["result"]
        docs_raw = result.get("docs") or result.get("data", {}).get("docs") or []
        notes = []
        for item in docs_raw:
            doc_obj = item.get("doc", item)
            notes.append(
                {
                    "doc": normalize_doc(doc_obj),
                    "highlight_info": item.get("highlight_info", {}),
                }
            )
        data = {
            "notes": notes,
            "page": {"start": start, "end": end, "is_end": bool(result.get("is_end", True))},
        }
        return build_success_result(data, response["request_id"])

    return safe_call(_impl)


@mcp.tool(name="ima.note.get_content", description="读取指定笔记正文")
def note_get_content(
    doc_id: str,
    target_content_format: int = 0,
    privacy_mode: str = "normal",
) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        if not isinstance(doc_id, str) or not doc_id.strip():
            raise IMAError("IMA_PARAM_INVALID", "doc_id 不能为空", False)
        if target_content_format != 0:
            raise IMAError("IMA_CONTENT_FORMAT_UNSUPPORTED", "仅支持 target_content_format=0", False)
        if privacy_mode not in ("normal", "safe_summary"):
            raise IMAError("IMA_PARAM_INVALID", "privacy_mode 仅支持 normal/safe_summary", False)
        payload = {"doc_id": doc_id, "target_content_format": target_content_format}
        response = post_note_api("get_doc_content", payload)
        result = response["result"]
        content = result.get("content") or result.get("doc_content") or ""
        title = result.get("title") or ""
        summary = result.get("summary") or ""
        if privacy_mode == "safe_summary":
            data = {
                "doc_id": doc_id,
                "title": title,
                "summary": summary or content[:200],
                "content": "",
                "content_format": 0,
                "truncated": False,
            }
        else:
            truncated = len(content) > 200_000
            data = {
                "doc_id": doc_id,
                "title": title,
                "content": content[:200_000],
                "content_format": 0,
                "truncated": truncated,
            }
        return build_success_result(data, response["request_id"])

    return safe_call(_impl)


@mcp.tool(name="ima.note.create", description="创建新笔记（Markdown 写入）")
def note_create(
    content: str,
    content_format: int = 1,
    folder_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        if content_format != 1:
            raise IMAError("IMA_CONTENT_FORMAT_UNSUPPORTED", "写入仅支持 content_format=1", False)
        if folder_id is not None and not isinstance(folder_id, str):
            raise IMAError("IMA_PARAM_INVALID", "folder_id 必须为字符串", False)
        if idempotency_key is not None and len(idempotency_key) > 128:
            raise IMAError("IMA_PARAM_INVALID", "idempotency_key 长度不能超过 128", False)
        sanitized_content = validate_content(content)
        payload = {"content_format": 1, "content": sanitized_content}
        if folder_id:
            payload["folder_id"] = folder_id
        cached_response = get_idempotency_response("note_create", idempotency_key, payload)
        if cached_response is not None:
            return cached_response
        response = post_note_api("import_doc", payload)
        result = response["result"]
        doc_id = extract_note_doc_id(result)
        if not doc_id:
            raise IMAError(
                "IMA_UPSTREAM_BAD_RESPONSE",
                "创建成功但未返回 doc_id",
                False,
                {"request_id": response["request_id"], "result_keys": sorted(result.keys())},
            )
        data = {
            "doc_id": doc_id,
            "created": True,
            "idempotency_key": idempotency_key,
            "idempotent_hit": False,
        }
        success_result = build_success_result(data, response["request_id"])
        save_idempotency_response("note_create", idempotency_key, payload, success_result)
        return success_result

    return safe_call(_impl)


@mcp.tool(name="ima.note.append", description="向已有笔记追加 Markdown 内容")
def note_append(
    doc_id: str,
    content: str,
    content_format: int = 1,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        if not isinstance(doc_id, str) or not doc_id.strip():
            raise IMAError("IMA_PARAM_INVALID", "doc_id 不能为空", False)
        if content_format != 1:
            raise IMAError("IMA_CONTENT_FORMAT_UNSUPPORTED", "写入仅支持 content_format=1", False)
        if idempotency_key is not None and len(idempotency_key) > 128:
            raise IMAError("IMA_PARAM_INVALID", "idempotency_key 长度不能超过 128", False)
        sanitized_content = validate_content(content)
        payload = {"doc_id": doc_id, "content_format": 1, "content": sanitized_content}
        cached_response = get_idempotency_response("note_append", idempotency_key, payload)
        if cached_response is not None:
            return cached_response
        response = post_note_api("append_doc", payload)
        result = response["result"]
        result_doc_id = extract_note_doc_id(result) or doc_id
        data = {
            "doc_id": result_doc_id,
            "appended": True,
            "idempotency_key": idempotency_key,
            "idempotent_hit": False,
        }
        success_result = build_success_result(data, response["request_id"])
        save_idempotency_response("note_append", idempotency_key, payload, success_result)
        return success_result

    return safe_call(_impl)


@mcp.tool(name="ima.note.list_recent", description="获取最近更新笔记（按 modify_time 倒序）")
def note_list_recent(limit: int = 20) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        validate_limit(limit)
        payload = {"folder_id": "", "cursor": "", "limit": limit}
        response = post_note_api("list_note_by_folder_id", payload)
        result = response["result"]
        notes_raw = result.get("notes") or result.get("data", {}).get("notes") or []
        notes = []
        for item in notes_raw:
            wrapped = item.get("basic_info", item)
            doc_wrapped = wrapped.get("basic_info", wrapped)
            notes.append(normalize_doc(doc_wrapped))
        notes.sort(key=lambda x: int(x.get("modify_time") or 0), reverse=True)
        data = {
            "notes": notes[:limit],
            "page": {
                "cursor": "",
                "next_cursor": result.get("next_cursor", ""),
                "is_end": bool(result.get("is_end", True)),
                "limit": limit,
            },
        }
        return build_success_result(data, response["request_id"])

    return safe_call(_impl)


@mcp.tool(name="ima.knowledge.list", description="分页列出知识库列表")
def knowledge_list(cursor: str = "0", limit: int = 20) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        if not isinstance(cursor, str):
            raise IMAError("IMA_PARAM_INVALID", "cursor 必须为字符串", False)
        validate_limit(limit)
        payload = {"query": "", "cursor": cursor, "limit": limit}
        response = post_knowledge_api(["search_knowledge_base"], payload)
        result = response["result"]
        knowledge_raw = extract_knowledge_list_from_result(result)
        knowledges = [normalize_knowledge_base(item.get("knowledge", item)) for item in knowledge_raw]
        data = build_knowledge_page_data(cursor, limit, result, knowledges)
        return build_success_result(data, response["request_id"])

    return safe_call(_impl)


@mcp.tool(name="ima.knowledge.list_addable", description="分页列出可添加内容的知识库列表")
def knowledge_list_addable(cursor: str = "0", limit: int = 20) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        if not isinstance(cursor, str):
            raise IMAError("IMA_PARAM_INVALID", "cursor 必须为字符串", False)
        validate_limit(limit)
        payload = {"cursor": cursor, "limit": limit}
        fallback_used: str | None = None
        try:
            response = post_knowledge_api(
                ["list_addable_knowledge_base_by_cursor", "list_addable_knowledge_base"],
                payload,
            )
        except IMAError as error:
            if error.code != "IMA_NOT_FOUND":
                raise
            fallback_used = "search_knowledge_base"
            response = post_knowledge_api(["search_knowledge_base"], {"query": "", "cursor": cursor, "limit": limit})
        result = response["result"]
        knowledge_raw = extract_knowledge_list_from_result(result)
        knowledges = [normalize_knowledge_base(item.get("knowledge", item)) for item in knowledge_raw]
        data = build_knowledge_page_data(cursor, limit, result, knowledges, fallback_used=fallback_used)
        return build_success_result(data, response["request_id"])

    return safe_call(_impl)


@mcp.tool(name="ima.knowledge.get_info", description="获取知识库详情")
def knowledge_get_info(knowledge_id: str) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        if not isinstance(knowledge_id, str) or not knowledge_id.strip():
            raise IMAError("IMA_PARAM_INVALID", "knowledge_id 不能为空", False)
        payload = build_knowledge_base_payload(knowledge_id)
        fallback_used: str | None = None
        try:
            response = post_knowledge_api(["get_knowledge_base_info", "get_knowledge_info"], payload)
            result = response["result"]
            knowledge = normalize_knowledge_base(
                result.get("knowledge_base")
                or result.get("knowledge")
                or result.get("data", {}).get("knowledge_base")
                or result.get("data", {}).get("knowledge")
                or result
            )
            request_id = response["request_id"]
        except IMAError as error:
            if error.code != "IMA_NOT_FOUND":
                raise
            fallback_used = "search_knowledge_base"
            fallback = find_knowledge_base_by_id_via_search(knowledge_id)
            knowledge = fallback["knowledge"]
            request_id = fallback["request_id"]
        data = {"knowledge": knowledge}
        if fallback_used:
            data["fallback_used"] = fallback_used
        return build_success_result(data, request_id)

    return safe_call(_impl)


@mcp.tool(name="ima.knowledge.search", description="检索知识库内容")
def knowledge_search(knowledge_id: str, query: str, start: int = 0, end: int = 20) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        if not isinstance(knowledge_id, str) or not knowledge_id.strip():
            raise IMAError("IMA_PARAM_INVALID", "knowledge_id 不能为空", False)
        if not isinstance(query, str) or not query.strip():
            raise IMAError("IMA_PARAM_INVALID", "query 不能为空", False)
        validate_search_range(start, end)
        payload = build_knowledge_base_payload(knowledge_id, query=query, start=start, end=end)
        response = post_knowledge_api(["search_knowledge_content", "search_knowledge"], payload)
        result = response["result"]
        item_raw = extract_knowledge_items_from_result(result)
        items = []
        for item in item_raw:
            normalized = normalize_knowledge_item(item.get("media", item))
            items.append({"item": normalized, "highlight_info": item.get("highlight_info", {})})
        data = {"items": items, "page": {"start": start, "end": end, "is_end": bool(result.get("is_end", True))}}
        return build_success_result(data, response["request_id"])

    return safe_call(_impl)


@mcp.tool(name="ima.knowledge.list_content", description="分页浏览知识库内容")
def knowledge_list_content(knowledge_id: str, cursor: str = "", limit: int = 20) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        if not isinstance(knowledge_id, str) or not knowledge_id.strip():
            raise IMAError("IMA_PARAM_INVALID", "knowledge_id 不能为空", False)
        if not isinstance(cursor, str):
            raise IMAError("IMA_PARAM_INVALID", "cursor 必须为字符串", False)
        validate_limit(limit)
        payload = build_knowledge_base_payload(knowledge_id, cursor=cursor, limit=limit)
        fallback_used: str | None = None
        try:
            response = post_knowledge_api(
                ["list_knowledge_content_by_cursor", "list_knowledge_media_by_cursor"],
                payload,
            )
            result = response["result"]
            next_cursor = result.get("next_cursor", "")
            is_end = bool(result.get("is_end", True))
        except IMAError as error:
            if error.code != "IMA_NOT_FOUND":
                raise
            fallback_used = "search_knowledge"
            start = int(cursor) if cursor.isdigit() else 0
            end = start + limit
            response = post_knowledge_api(
                ["search_knowledge"],
                build_knowledge_base_payload(knowledge_id, query=" ", start=start, end=end),
            )
            result = response["result"]
            next_cursor = "" if bool(result.get("is_end", True)) else str(end)
            is_end = bool(result.get("is_end", True))
        item_raw = extract_knowledge_items_from_result(result)
        items = [normalize_knowledge_item(item.get("media", item)) for item in item_raw]
        data = {
            "items": items,
            "page": {
                "cursor": cursor,
                "next_cursor": next_cursor,
                "is_end": is_end,
                "limit": limit,
            },
        }
        if fallback_used:
            data["fallback_used"] = fallback_used
        return build_success_result(data, response["request_id"])

    return safe_call(_impl)


@mcp.tool(name="ima.knowledge.get_media_info", description="获取知识库条目详情，可用于原文查看与跨模块分析")
def knowledge_get_media_info(media_id: str) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        if not isinstance(media_id, str) or not media_id.strip():
            raise IMAError("IMA_PARAM_INVALID", "media_id 不能为空", False)
        payload = {"media_id": media_id}
        response = post_knowledge_api(["get_media_info", "get_knowledge_media_info"], payload)
        result = response["result"]
        media_raw = (
            result.get("media")
            or result.get("media_info")
            or result.get("knowledge_media")
            or result.get("data", {}).get("media")
            or result.get("data", {}).get("media_info")
            or result
        )
        item = normalize_knowledge_item(media_raw)
        if not item["media_id"]:
            item["media_id"] = media_id
        data = {
            "item": item,
            "view_source_supported": bool(item["source_url"] or item["download_url"] or item["note_doc_id"]),
            "analyze_source_supported": bool(item["source_url"] or item["download_url"] or item["note_doc_id"]),
            "export_source_supported": bool(item["download_url"]),
            "requires_note_module": item["media_type"] == 11 and bool(item["note_doc_id"]),
            "raw": result,
        }
        return build_success_result(data, response["request_id"])

    return safe_call(_impl)


@mcp.tool(name="ima.knowledge.create_media", description="创建知识库媒体并返回上传参数")
def knowledge_create_media(
    knowledge_id: str,
    file_name: str,
    file_size: int,
    content_type: str = "application/octet-stream",
) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        if not isinstance(knowledge_id, str) or not knowledge_id.strip():
            raise IMAError("IMA_PARAM_INVALID", "knowledge_id 不能为空", False)
        if not isinstance(file_name, str) or not file_name.strip():
            raise IMAError("IMA_PARAM_INVALID", "file_name 不能为空", False)
        if not isinstance(file_size, int) or file_size <= 0:
            raise IMAError("IMA_PARAM_INVALID", "file_size 必须为正整数", False)
        if not isinstance(content_type, str) or not content_type.strip():
            raise IMAError("IMA_PARAM_INVALID", "content_type 不能为空", False)
        validate_upload_file_metadata(file_name, content_type)
        payload = build_knowledge_base_payload(
            knowledge_id,
            file_name=file_name,
            file_size=file_size,
            content_type=content_type,
        )
        response = post_knowledge_api(["create_media", "create_knowledge_media"], payload)
        result = response["result"]
        upload_info = result.get("upload_info") or result.get("cos") or result.get("upload") or {}
        data = {
            "media_id": result.get("media_id") or result.get("id") or "",
            "file_name": result.get("file_name") or file_name,
            "file_size": result.get("file_size") or file_size,
            "content_type": result.get("content_type") or content_type,
            "upload_info": upload_info,
            "upload_url": upload_info.get("url") or upload_info.get("upload_url") or "",
            "upload_method": upload_info.get("method") or "PUT",
            "raw": result,
        }
        return build_success_result(data, response["request_id"])

    return safe_call(_impl)


@mcp.tool(
    name="ima.knowledge.add",
    description="向知识库新增条目（当前仅支持文件类型）。正确流程：1) 调用 ima.knowledge.create_media 获取 COS 上传凭证；2) 使用腾讯云 COS SDK 上传文件；3) 将返回的 media_id 传入此接口关联到知识库。",
)
def knowledge_add(
    knowledge_id: str,
    media_type: int,
    media_id: str = "",
    title: str = "",
    source_url: str = "",
) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        if not isinstance(knowledge_id, str) or not knowledge_id.strip():
            raise IMAError("IMA_PARAM_INVALID", "knowledge_id 不能为空", False)
        if not isinstance(media_type, int) or media_type <= 0:
            raise IMAError("IMA_PARAM_INVALID", "media_type 必须为正整数", False)
        if source_url:
            if not isinstance(source_url, str):
                raise IMAError("IMA_PARAM_INVALID", "source_url 必须为字符串", False)
            validate_source_url_for_knowledge(source_url)
        payload: dict[str, Any] = build_knowledge_base_payload(knowledge_id, media_type=media_type)
        if media_id:
            payload["media_id"] = media_id
        if title:
            payload["title"] = title
        if source_url:
            payload["source_url"] = source_url
        if media_type == 11 and media_id:
            payload["note_info"] = {
                "doc_id": media_id,
                "note_id": media_id,
            }
            endpoint_candidates = ["add_note_to_knowledge", "add_note_knowledge", "add_knowledge_note", "add_knowledge"]
        else:
            endpoint_candidates = ["add_knowledge"]
        response = post_knowledge_api(endpoint_candidates, payload)
        result = response["result"]
        data = {
            "knowledge_id": knowledge_id,
            "media_type": media_type,
            "media_id": result.get("media_id") or media_id,
            "knowledge_item_id": result.get("knowledge_item_id") or result.get("id") or "",
            "title": result.get("title") or title,
            "source_url": result.get("source_url") or source_url,
            "added": True,
            "raw": result,
        }
        return build_success_result(data, response["request_id"])

    return safe_call(_impl)


@mcp.tool(
    name="ima.workflow.add_note_to_knowledge",
    description="将已有笔记关联到知识库。但注意：ima API 的 add_knowledge 接口不支持 media_type=11（笔记类型）。建议使用文件上传方式：1) 通过 ima.note.create 创建笔记；2) 通过 ima.knowledge.create_media 获取 COS 上传凭证；3) 上传文件到 COS；4) 调用 ima.knowledge.add 将文件关联到知识库。",
)
def workflow_add_note_to_knowledge(knowledge_id: str, note_doc_id: str, title: str = "") -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        if not isinstance(knowledge_id, str) or not knowledge_id.strip():
            raise IMAError("IMA_PARAM_INVALID", "knowledge_id 不能为空", False)
        if not isinstance(note_doc_id, str) or not note_doc_id.strip():
            raise IMAError("IMA_PARAM_INVALID", "note_doc_id 不能为空", False)
        add_result = require_success(
            knowledge_add(
                knowledge_id=knowledge_id,
                media_type=11,
                media_id=note_doc_id,
                title=title,
            ),
            "workflow_add_note_to_knowledge",
        )
        data = {
            "workflow": "add_note_to_knowledge",
            "knowledge_id": knowledge_id,
            "note_doc_id": note_doc_id,
            "media_type": 11,
            "title": add_result.get("title") or title,
            "linked": True,
            "knowledge_item_id": add_result.get("knowledge_item_id", ""),
            "media_id": add_result.get("media_id") or note_doc_id,
        }
        return build_success_result(data)

    return safe_call(_impl)


@mcp.tool(name="ima.workflow.get_knowledge_source", description="跨模块工作流：获取知识库条目的原始来源或下一步动作")
def workflow_get_knowledge_source(media_id: str, privacy_mode: str = "normal") -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        if not isinstance(media_id, str) or not media_id.strip():
            raise IMAError("IMA_PARAM_INVALID", "media_id 不能为空", False)
        if privacy_mode not in ("normal", "safe_summary"):
            raise IMAError("IMA_PARAM_INVALID", "privacy_mode 仅支持 normal/safe_summary", False)
        media_info = require_success(
            knowledge_get_media_info(media_id),
            "workflow_get_knowledge_source.media_info",
        )
        item = media_info.get("item", {})
        source_kind = "unknown"
        source_payload: dict[str, Any] = {}
        next_action = "manual_review"
        if media_info.get("requires_note_module") and item.get("note_doc_id"):
            note_data = require_success(
                note_get_content(
                    doc_id=item["note_doc_id"],
                    target_content_format=0,
                    privacy_mode=privacy_mode,
                ),
                "workflow_get_knowledge_source.note_get_content",
            )
            source_kind = "note"
            source_payload = note_data
            next_action = "note_content_ready"
        elif item.get("source_url"):
            source_kind = "web"
            source_payload = {"source_url": item["source_url"], "download_url": item.get("download_url", "")}
            next_action = "open_source_url"
        elif item.get("download_url"):
            source_kind = "file"
            source_payload = {"download_url": item["download_url"], "file_name": item.get("file_name", "")}
            next_action = "download_source_file"
        data = {
            "workflow": "get_knowledge_source",
            "media_id": media_id,
            "source_kind": source_kind,
            "next_action": next_action,
            "requires_note_module": bool(media_info.get("requires_note_module")),
            "item": item,
            "source": source_payload,
        }
        return build_success_result(data)

    return safe_call(_impl)


def _upload_to_cos(
    upload_info: dict[str, Any],
    content: bytes,
    content_type: str = "text/markdown; charset=utf-8",
) -> None:
    """使用临时凭证上传内容到 COS。"""
    url = upload_info.get("url") or upload_info.get("upload_url") or ""
    if not url:
        raise IMAError("IMA_UPSTREAM_BAD_RESPONSE", "COS 上传 URL 为空", False, {"upload_info_keys": list(upload_info.keys())})
    method = upload_info.get("method") or "PUT"
    headers: dict[str, str] = {"Content-Type": content_type}
    if upload_info.get("x-cos-security-token"):
        headers["x-cos-security-token"] = str(upload_info["x-cos-security-token"])
    if upload_info.get("token"):
        headers["x-cos-security-token"] = str(upload_info["token"])
    req = urllib.request.Request(
        url=url,
        data=content,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status >= 400:
            raise IMAError("IMA_UPSTREAM_BAD_RESPONSE", f"COS 上传失败: HTTP {resp.status}", False, {"status": resp.status})


@mcp.tool(
    name="ima.workflow.note_to_knowledge",
    description="将笔记内容直接写入知识库（自动完成文件创建和上传）。工作流程：1) 创建临时文件；2) 获取 COS 上传凭证；3) 上传文件；4) 关联到知识库。返回 media_id 可用于后续操作。",
)
def workflow_note_to_knowledge(
    knowledge_id: str,
    content: str,
    title: str = "",
) -> dict[str, Any]:
    """将笔记内容直接写入知识库。

    完整流程：先创建笔记（获取 note_id），再创建临时文件并上传到 COS，最后关联到知识库。
    不依赖 add_knowledge 的 media_type=11 支持。
    """
    def _impl() -> dict[str, Any]:
        if not isinstance(knowledge_id, str) or not knowledge_id.strip():
            raise IMAError("IMA_PARAM_INVALID", "knowledge_id 不能为空", False)
        if not isinstance(content, str) or not content.strip():
            raise IMAError("IMA_PARAM_INVALID", "content 不能为空", False)
        if not isinstance(title, str):
            raise IMAError("IMA_PARAM_INVALID", "title 必须为字符串", False)
        content_bytes = content.encode("utf-8")
        file_size = len(content_bytes)
        if file_size > 10 * 1024 * 1024:
            raise IMAError("IMA_CONTENT_TOO_LARGE", "内容超过 10MB 限制", False, {"size": file_size})
        file_name = f"note_{uuid.uuid4().hex[:16]}.md"
        if title:
            safe_title = "".join(c for c in title if c.isalnum() or c in (" ", "-", "_")).strip()[:50]
            if safe_title:
                file_name = f"{safe_title[:40]}_{uuid.uuid4().hex[:8]}.md"
        note_create_result = require_success(
            note_create(content=content, content_format=1),
            "workflow_note_to_knowledge.note_create",
        )
        note_doc_id = note_create_result.get("doc_id") or ""
        if not note_doc_id:
            raise IMAError("IMA_UPSTREAM_BAD_RESPONSE", "创建笔记成功但未返回 doc_id", False, {"result": note_create_result})
        create_media_result = require_success(
            knowledge_create_media(
                knowledge_id=knowledge_id,
                file_name=file_name,
                file_size=file_size,
                content_type="text/markdown; charset=utf-8",
            ),
            "workflow_note_to_knowledge.create_media",
        )
        media_id = create_media_result.get("media_id", "")
        LOGGER.info(
            "workflow_note_to_knowledge: create_media returned media_id=%s, full_result=%s",
            media_id, create_media_result
        )
        if not media_id:
            raise IMAError("IMA_UPSTREAM_BAD_RESPONSE", "create_media 未返回 media_id", False, {"result": create_media_result})
        upload_info = create_media_result.get("upload_info", {})
        if not upload_info:
            raise IMAError("IMA_UPSTREAM_BAD_RESPONSE", "create_media 未返回 upload_info", False, {"result": create_media_result})
        try:
            _upload_to_cos(upload_info, content_bytes, "text/markdown; charset=utf-8")
        except Exception as exc:
            raise IMAError("IMA_UPLOAD_FAILED", f"文件上传到 COS 失败: {exc}", False, {"file_name": file_name}) from exc
        LOGGER.info(
            "workflow_note_to_knowledge: calling add_knowledge with media_type=1 media_id=%s",
            media_id
        )
        add_result = require_success(
            knowledge_add(
                knowledge_id=knowledge_id,
                media_type=1,
                media_id=media_id,
                title=title or file_name,
            ),
            "workflow_note_to_knowledge.add_knowledge",
        )
        data = {
            "workflow": "note_to_knowledge",
            "knowledge_id": knowledge_id,
            "note_doc_id": note_doc_id,
            "file_name": file_name,
            "file_size": file_size,
            "media_id": add_result.get("media_id", ""),
            "knowledge_item_id": add_result.get("knowledge_item_id", ""),
            "title": title or file_name,
            "added": True,
        }
        return build_success_result(data)

    return safe_call(_impl)


def init_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="初始化 MCP 配置（兼容 Trae / Cursor / Code Buddy / Claude）")
    parser.add_argument(
        "--ide",
        choices=("cursor", "trae", "codebuddy", "claude"),
        default="cursor",
        help="目标 IDE，默认 cursor",
    )
    parser.add_argument(
        "--config-path",
        default="",
        help="MCP 配置文件路径，留空将按 --ide 自动选择默认路径",
    )
    parser.add_argument(
        "--server-name",
        default="ima-note-mcp",
        help="写入 mcpServers 的服务名称，默认 ima-note-mcp",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="仅输出 JSON，不写入文件",
    )
    parser.add_argument(
        "--embed-env-secrets",
        action="store_true",
        help="将当前终端环境变量中的凭证写入配置（默认不写入真实凭证）",
    )
    args = parser.parse_args(argv)
    if args.embed_env_secrets:
        client_id = os.getenv("IMA_OPENAPI_CLIENTID", "").strip() or "请替换为你的_client_id"
        api_key = os.getenv("IMA_OPENAPI_APIKEY", "").strip() or "请替换为你的_api_key"
    else:
        client_id = "${IMA_OPENAPI_CLIENTID}"
        api_key = "${IMA_OPENAPI_APIKEY}"
    log_level = os.getenv("IMA_NOTE_MCP_LOG_LEVEL", "INFO").upper()
    config = build_cursor_mcp_config(
        server_name=args.server_name,
        python_path=sys.executable,
        client_id=client_id,
        api_key=api_key,
        log_level=log_level,
    )
    print(json.dumps(config, ensure_ascii=False, indent=2))
    if args.print_only:
        return
    target_path = args.config_path.strip() or resolve_default_config_path(args.ide)
    output_path = Path(target_path).expanduser().resolve()
    written_path = write_cursor_mcp_config(output_path, args.server_name, config)
    print(f"已写入: {written_path}")


def main() -> None:
    log_level = os.getenv("IMA_NOTE_MCP_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
