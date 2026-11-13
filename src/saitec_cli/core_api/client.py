"""Core 平台 HTTP 客户端。

负责：URL 拼接、X-API-Key、BaseResponse 解包、错误分类、multipart 上传、
流式下载。业务语义（任务/检测/评测）由 runtime/core_ops 负责。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from email.message import Message
from pathlib import Path
from typing import Any

import aiohttp

from .endpoints import (
    FILE_CONTENT,
    FILE_DOWNLOAD,
    FILE_UPLOAD,
    HEALTH,
    VALID_FILE_TYPES,
)
from .errors import CoreApiError, CoreApiErrorKind

_JSON_MAX_SNIPPET = 500
_CHUNK_SIZE = 64 * 1024
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _decode_json(text: str, content_type: str) -> Any:
    """按 Content-Type / 文本形态解析 JSON；失败返回 None（不抛错）。"""
    text = text or ""
    if not text.strip():
        return None
    ctype = content_type.split(";")[0].strip().lower()
    looks_json = (
        ctype in ("application/json", "application/x-json")
        or ctype.endswith("+json")
        or text.lstrip().startswith(("{", "["))
    )
    if not looks_json:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _extract_message(payload: Any, text: str) -> str:
    """从 BaseResponse / FastAPI 错误体中提取可读消息。"""
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()[:_JSON_MAX_SNIPPET]
        if isinstance(detail, list):
            parts: list[str] = []
            for item in detail:
                if isinstance(item, dict):
                    msg = item.get("msg") or item.get("message")
                    loc = item.get("loc")
                    if msg:
                        prefix = ".".join(str(x) for x in loc) if loc else ""
                        parts.append(f"{prefix}: {msg}" if prefix else str(msg))
                elif isinstance(item, str):
                    parts.append(item)
            if parts:
                return "; ".join(parts)[:_JSON_MAX_SNIPPET]
        for key in ("message", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:_JSON_MAX_SNIPPET]
            if isinstance(value, dict):
                nested = value.get("message")
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()[:_JSON_MAX_SNIPPET]
    compact = " ".join((text or "").split())
    return compact[:_JSON_MAX_SNIPPET] or "Core API request failed"


def _kind_for_status(status: int) -> CoreApiErrorKind:
    if status in (401, 403):
        return CoreApiErrorKind.AUTH
    if status == 404:
        return CoreApiErrorKind.NOT_FOUND
    if status == 422:
        return CoreApiErrorKind.PAYLOAD
    if status == 408:
        return CoreApiErrorKind.TIMEOUT
    if status == 429:
        return CoreApiErrorKind.RATE_LIMIT
    if status >= 500:
        return CoreApiErrorKind.SERVER
    return CoreApiErrorKind.PAYLOAD


def _safe_filename(name: str | None) -> str | None:
    """只取 basename，剔除路径/Windows 非法字符/保留名，限制长度。"""
    if not name:
        return None
    name = str(name).replace("\\", "/").split("/")[-1]
    name = _INVALID_FILENAME_CHARS.sub("_", name).strip().rstrip(" .")
    if not name or name in (".", ".."):
        return None
    if name.split(".")[0].upper() in _WINDOWS_RESERVED:
        name = "_" + name
    # 按 UTF-8 字节截断（保留扩展名），避免多字节文件名触发 ENAMETOOLONG
    if len(name.encode("utf-8")) > 255:
        suffix = Path(name).suffix
        suffix_bytes = suffix.encode("utf-8")
        if len(suffix_bytes) >= 255:
            suffix = ""
            suffix_bytes = b""
        stem = name[: len(name) - len(suffix)] if suffix else name
        keep = 255 - len(suffix_bytes)
        stem = stem.encode("utf-8")[:keep].decode("utf-8", "ignore")
        name = stem + suffix
    return name


def _filename_from_disposition(header: str | None) -> str | None:
    """解析 Content-Disposition；优先 filename*（RFC 2231），再回退 filename。"""
    if not header:
        return None
    msg = Message()
    msg["content-disposition"] = header
    for name, value in msg.get_params(header="content-disposition") or []:
        if str(name).lower() != "filename" or not isinstance(value, tuple):
            continue
        charset, _lang, text = value
        try:
            decoded = text.encode("latin-1", errors="replace").decode(
                charset or "utf-8", errors="replace"
            )
        except LookupError:
            decoded = text
        return _safe_filename(decoded)
    return _safe_filename(msg.get_filename())


def _is_attachment(resp: aiohttp.ClientResponse) -> bool:
    """只有 Content-Disposition: attachment 才视为文件成功响应。"""
    header = resp.headers.get("Content-Disposition")
    if not header:
        return False
    msg = Message()
    msg["content-disposition"] = header
    return msg.get_content_disposition() == "attachment"


class CoreClient:
    """Core 平台 API 客户端（异步）。

    用法：
        async with CoreClient(base_url, api_key) as client:
            data = await client.request_json("GET", "/api/v1/tasks")
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_sec: float = 30.0,
        upload_timeout_sec: float = 300.0,
        download_timeout_sec: float = 300.0,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.base_url = self._validate_base_url(base_url)
        self.api_key = (api_key or "").strip()
        self.timeout_sec = float(timeout_sec)
        self.upload_timeout_sec = float(upload_timeout_sec)
        self.download_timeout_sec = float(download_timeout_sec)
        self._session = session
        self._owns_session = session is None

    @staticmethod
    def _validate_base_url(base_url: str) -> str:
        value = (base_url or "").strip().rstrip("/")
        if not value or not value.startswith(("http://", "https://")):
            raise CoreApiError(
                CoreApiErrorKind.CONFIG,
                f"Core base_url 必须是 http/https 地址，当前为 {base_url!r}",
            )
        return value

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None)
            )
        return self._session

    def _url(self, path: str) -> str:
        return self.base_url + (path if path.startswith("/") else f"/{path}")

    def _headers(self, *, require_key: bool = True) -> dict[str, str]:
        if require_key and not self.api_key:
            raise CoreApiError(
                CoreApiErrorKind.CONFIG,
                "Core API Key 未配置（detector.api_key）",
            )
        return {"X-API-Key": self.api_key} if self.api_key else {}

    def _timeout(self, total: float | None = None) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(total=self.timeout_sec if total is None else total)

    def _parse_response(self, resp: aiohttp.ClientResponse, text: str) -> Any:
        content_type = resp.headers.get("Content-Type", "")
        payload = _decode_json(text, content_type)
        if 300 <= resp.status < 400:
            raise CoreApiError(
                CoreApiErrorKind.SERVER,
                f"HTTP {resp.status}: 收到未跟随的重定向（已禁用重定向以保护 API Key）",
                status=resp.status,
                retry_after=resp.headers.get("Retry-After"),
            )
        if resp.status >= 400:
            raise CoreApiError(
                _kind_for_status(resp.status),
                f"HTTP {resp.status}: {_extract_message(payload, text)}",
                status=resp.status,
                detail=payload,
                retry_after=resp.headers.get("Retry-After"),
            )
        if isinstance(payload, dict) and "success" in payload and not isinstance(
            payload.get("success"), bool
        ):
            raise CoreApiError(
                CoreApiErrorKind.SERVER,
                "Core 响应的 success 字段不是 bool（协议异常）",
                status=resp.status,
                detail=payload,
            )
        if isinstance(payload, dict) and payload.get("success") is False:
            raise CoreApiError(
                CoreApiErrorKind.BUSINESS,
                _extract_message(payload, text),
                status=resp.status,
                detail=payload,
            )
        if isinstance(payload, dict) and "success" in payload:
            return payload.get("data")
        if payload is None and text.strip():
            raise CoreApiError(
                CoreApiErrorKind.SERVER,
                "Core 响应不是合法 JSON（可能被网关/登录页拦截）",
                status=resp.status,
                detail=text[:_JSON_MAX_SNIPPET],
            )
        return payload if payload is not None else None

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: Any = None,
        require_key: bool = True,
        timeout: float | None = None,
    ) -> Any:
        """发 JSON 请求并返回 BaseResponse.data；非 JSON 2xx 视为协议错误。"""
        session = self._get_session()
        try:
            async with session.request(
                method.upper(),
                self._url(path),
                params=params,
                json=payload,
                headers=self._headers(require_key=require_key),
                timeout=self._timeout(timeout),
                allow_redirects=False,
            ) as resp:
                text = await resp.text(errors="replace")
                return self._parse_response(resp, text)
        except CoreApiError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise CoreApiError(
                CoreApiErrorKind.NETWORK,
                f"Core 网络错误: {type(exc).__name__}: {exc}",
            ) from exc

    async def health(self) -> Any:
        """GET /health（无需 Key）。"""
        return await self.request_json("GET", HEALTH, require_key=False)

    async def read_file_content(self, file_id: str) -> Any:
        """GET 文件文本内容。"""
        return await self.request_json("GET", FILE_CONTENT.format(file_id=file_id))

    async def upload_file(
        self, path: Path | str, file_type: str, *, timeout: float | None = None
    ) -> Any:
        """multipart 上传本地文件（字段 file + file_type）。"""
        if file_type not in VALID_FILE_TYPES:
            raise CoreApiError(
                CoreApiErrorKind.PAYLOAD,
                f"file_type 必须是 {list(VALID_FILE_TYPES)}，当前为 {file_type!r}",
            )
        form = aiohttp.FormData()
        form.add_field("file_type", file_type)
        session = self._get_session()
        try:
            src = Path(path).expanduser().resolve()
            if not src.is_file():
                raise CoreApiError(CoreApiErrorKind.IO, f"文件不存在: {src}")
            with src.open("rb") as fh:
                form.add_field(
                    "file",
                    fh,
                    filename=src.name,
                    content_type="application/octet-stream",
                )
                async with session.post(
                    self._url(FILE_UPLOAD),
                    data=form,
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(
                        total=None,
                        connect=10.0,
                        sock_read=(
                            timeout if timeout is not None else self.upload_timeout_sec
                        ),
                    ),
                    allow_redirects=False,
                ) as resp:
                    text = await resp.text(errors="replace")
                    return self._parse_response(resp, text)
        except CoreApiError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise CoreApiError(
                CoreApiErrorKind.NETWORK,
                f"Core 上传网络错误: {type(exc).__name__}: {exc}",
            ) from exc
        except OSError as exc:
            raise CoreApiError(
                CoreApiErrorKind.IO, f"读取本地文件失败: {exc}"
            ) from exc

    async def download_file(
        self,
        file_id: str,
        *,
        dest: Path | str | None = None,
        dest_dir: Path | str | None = None,
        overwrite: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """流式下载文件；默认写入 dest_dir/<服务端文件名>。

        - 成功：先写唯一 `.part`，完成后 `os.replace`。
        - 失败/取消：清理 `.part`，不留目标文件。
        - 只有带 Content-Disposition 的响应才视为文件；HTTP 200 的
          BaseResponse 错误包络会被识别为失败，不会写盘。
        """
        if dest is None and dest_dir is None:
            raise CoreApiError(
                CoreApiErrorKind.CONFIG,
                "download_file 需要 dest 或 dest_dir",
            )
        session = self._get_session()
        try:
            async with session.get(
                self._url(FILE_DOWNLOAD.format(file_id=file_id)),
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(
                    total=None,
                    connect=10.0,
                    sock_read=(
                        timeout if timeout is not None else self.download_timeout_sec
                    ),
                ),
                allow_redirects=False,
            ) as resp:
                if 300 <= resp.status < 400:
                    raise CoreApiError(
                        CoreApiErrorKind.SERVER,
                        f"HTTP {resp.status}: 收到未跟随的重定向（已禁用重定向以保护 API Key）",
                        status=resp.status,
                    )
                if resp.status >= 400:
                    text = await resp.text(errors="replace")
                    payload = _decode_json(text, resp.headers.get("Content-Type", ""))
                    raise CoreApiError(
                        _kind_for_status(resp.status),
                        f"HTTP {resp.status}: {_extract_message(payload, text)}",
                        status=resp.status,
                        detail=payload,
                        retry_after=resp.headers.get("Retry-After"),
                    )

                if not _is_attachment(resp):
                    text = await resp.text(errors="replace")
                    payload = _decode_json(
                        text, resp.headers.get("Content-Type", "")
                    )
                    if (
                        isinstance(payload, dict)
                        and "success" in payload
                        and not isinstance(payload.get("success"), bool)
                    ):
                        raise CoreApiError(
                            CoreApiErrorKind.SERVER,
                            "Core 响应的 success 字段不是 bool（协议异常）",
                            status=resp.status,
                            detail=payload,
                        )
                    if isinstance(payload, dict) and payload.get("success") is False:
                        raise CoreApiError(
                            CoreApiErrorKind.BUSINESS,
                            _extract_message(payload, text),
                            status=resp.status,
                            detail=payload,
                        )
                    raise CoreApiError(
                        CoreApiErrorKind.SERVER,
                        "服务端未返回文件（缺少 Content-Disposition，可能是网关/登录页错误响应）",
                        status=resp.status,
                        detail=text[:_JSON_MAX_SNIPPET],
                    )

                filename = (
                    _filename_from_disposition(
                        resp.headers.get("Content-Disposition")
                    )
                    or _safe_filename(file_id)
                    or "download"
                )
                target = self._resolve_target(dest, dest_dir, filename)
                if target.exists() and not overwrite:
                    raise CoreApiError(
                        CoreApiErrorKind.IO,
                        f"目标文件已存在（加 --overwrite 覆盖）: {target}",
                    )
                target.parent.mkdir(parents=True, exist_ok=True)

                fd, part_name = tempfile.mkstemp(
                    prefix=".part-", suffix=".part", dir=str(target.parent)
                )
                os.close(fd)
                part = Path(part_name)
                replaced = False
                try:
                    with part.open("wb") as fh:
                        async for chunk in resp.content.iter_chunked(_CHUNK_SIZE):
                            fh.write(chunk)
                    size_bytes = part.stat().st_size
                    os.replace(part, target)
                    replaced = True
                finally:
                    if not replaced:
                        try:
                            part.unlink(missing_ok=True)
                        except OSError:
                            pass
        except CoreApiError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise CoreApiError(
                CoreApiErrorKind.NETWORK,
                f"Core 下载网络错误: {type(exc).__name__}: {exc}",
            ) from exc
        except OSError as exc:
            raise CoreApiError(
                CoreApiErrorKind.IO, f"写入本地文件失败: {exc}"
            ) from exc

        return {
            "file_id": file_id,
            "path": str(target),
            "filename": target.name,
            "size_bytes": size_bytes,
        }

    @staticmethod
    def _resolve_target(
        dest: Path | str | None,
        dest_dir: Path | str | None,
        filename: str,
    ) -> Path:
        if dest is None:
            base = Path(str(dest_dir)).expanduser()
            target = base / filename
            CoreClient._ensure_within(target, base)
            return target
        raw = str(dest)
        target = Path(dest).expanduser()
        if raw.endswith(("/", "\\")) or (target.exists() and target.is_dir()):
            target.mkdir(parents=True, exist_ok=True)
            resolved = target / filename
            CoreClient._ensure_within(resolved, target)
            return resolved
        return target

    @staticmethod
    def _ensure_within(target: Path, base: Path) -> None:
        try:
            target.resolve().relative_to(base.resolve())
        except ValueError as exc:
            raise CoreApiError(
                CoreApiErrorKind.IO,
                f"下载路径越界（拒绝写到目标目录之外）: {target}",
            ) from exc

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> CoreClient:
        self._get_session()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()
