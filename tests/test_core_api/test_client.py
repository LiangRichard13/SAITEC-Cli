"""CoreClient 组件测试：本地 aiohttp mock Core。"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import web

from saitec_cli.core_api.client import CoreClient
from saitec_cli.core_api.errors import CoreApiError, CoreApiErrorKind


async def _health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def _tasks(request: web.Request) -> web.Response:
    request.app["last_headers"] = dict(request.headers)
    return web.json_response(
        {"success": True, "message": "success", "data": {"tasks": [{"id": "t1"}], "total": 1}}
    )


async def _business_fail(request: web.Request) -> web.Response:
    return web.json_response({"success": False, "message": "Task not found"})


async def _auth_fail(request: web.Request) -> web.Response:
    return web.json_response(
        {"success": False, "message": "Invalid or expired API Key"}, status=401
    )


async def _validation_fail(request: web.Request) -> web.Response:
    return web.json_response(
        {"detail": [{"loc": ["body", "task_name"], "msg": "Field required"}]},
        status=422,
    )


async def _server_fail(request: web.Request) -> web.Response:
    return web.json_response({"detail": "boom"}, status=500)


async def _upload(request: web.Request) -> web.Response:
    reader = await request.multipart()
    fields: dict[str, str] = {}
    data = b""
    filename: str | None = None
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "file":
            filename = part.filename
            data = await part.read()
        else:
            fields[part.name] = await part.text()
    request.app["upload"] = {"filename": filename, "data": data, "fields": fields}
    return web.json_response(
        {
            "success": True,
            "message": "success",
            "data": {
                "file_id": "file-1",
                "storage_uri": "/data/user_storage/dataset/u/file.txt",
                "size_bytes": len(data),
            },
        }
    )


async def _download(request: web.Request) -> web.Response:
    return web.Response(
        body=b"hello world",
        content_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="report.md"'},
    )


async def _download_unicode(request: web.Request) -> web.Response:
    return web.Response(
        body=b"unicode",
        content_type="application/octet-stream",
        headers={
            "Content-Disposition": (
                "attachment; filename=\"____.md\"; "
                "filename*=UTF-8''%E6%8A%A5%E5%91%8A.md"
            )
        },
    )


async def _download_inline_html(request: web.Request) -> web.Response:
    return web.Response(
        body=b"<html>login</html>",
        content_type="text/html",
        headers={"Content-Disposition": 'inline; filename="login.html"'},
    )


async def _download_long_name(request: web.Request) -> web.Response:
    name = "a" * 240 + ".md"
    return web.Response(
        body=b"long",
        content_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


async def _download_json_error(request: web.Request) -> web.Response:
    return web.json_response({"success": False, "message": "File not found"})


async def _download_ndjson(request: web.Request) -> web.Response:
    return web.Response(
        body=b'{"a": 1}\n{"a": 2}\n',
        content_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="data.jsonl"'},
    )


async def _download_json_file_with_success_false(request: web.Request) -> web.Response:
    """合法 JSON 文件：顶层恰好有 success:false，仍应作为文件下载。"""
    return web.Response(
        body=b'{"success": false, "data": [1, 2, 3]}',
        content_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="data.json"'},
    )


async def _download_no_cd_html(request: web.Request) -> web.Response:
    return web.Response(
        body=b"<html>gateway login</html>",
        content_type="text/html",
    )


async def _download_no_filename(request: web.Request) -> web.Response:
    return web.Response(
        body=b"fallback",
        content_type="application/octet-stream",
        headers={"Content-Disposition": "attachment"},
    )


async def _redirect(request: web.Request) -> web.Response:
    raise web.HTTPFound("/health")


async def _bad_encoding(request: web.Request) -> web.Response:
    return web.Response(body=b"\xff\xfe", content_type="text/plain", status=500)


async def _download_evil_name(request: web.Request) -> web.Response:
    return web.Response(
        body=b"x",
        content_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="../../etc/passwd"'},
    )


async def _content(request: web.Request) -> web.Response:
    return web.json_response(
        {"success": True, "message": "success", "data": {"content": "hello"}}
    )


@pytest_asyncio.fixture
async def core_server():
    app = web.Application()
    app.router.add_get("/health", _health)
    app.router.add_get("/api/v1/tasks", _tasks)
    app.router.add_get("/api/v1/business-fail", _business_fail)
    app.router.add_get("/api/v1/auth-fail", _auth_fail)
    app.router.add_get("/api/v1/validation-fail", _validation_fail)
    app.router.add_get("/api/v1/server-fail", _server_fail)
    app.router.add_post("/api/v1/skills/file-manage/upload", _upload)
    app.router.add_get("/api/v1/skills/file-manage/files/file-1", _download)
    app.router.add_get("/api/v1/skills/file-manage/files/unicode-file", _download_unicode)
    app.router.add_get("/api/v1/skills/file-manage/files/bad-file", _download_json_error)
    app.router.add_get("/api/v1/skills/file-manage/files/data.jsonl", _download_ndjson)
    app.router.add_get(
        "/api/v1/skills/file-manage/files/json-file", _download_json_file_with_success_false
    )
    app.router.add_get(
        "/api/v1/skills/file-manage/files/html-error", _download_no_cd_html
    )
    app.router.add_get(
        "/api/v1/skills/file-manage/files/no-name", _download_no_filename
    )
    app.router.add_get(
        "/api/v1/skills/file-manage/files/CON", _download_no_filename
    )
    app.router.add_get(
        "/api/v1/skills/file-manage/files/inline-html", _download_inline_html
    )
    app.router.add_get(
        "/api/v1/skills/file-manage/files/long-name", _download_long_name
    )
    app.router.add_get("/redirect", _redirect)
    app.router.add_get("/bad-encoding", _bad_encoding)
    app.router.add_get(
        "/api/v1/skills/file-manage/files/evil", _download_evil_name
    )
    app.router.add_get(
        "/api/v1/skills/file-manage/files/file-1/content", _content
    )

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", app
    finally:
        await runner.cleanup()


async def test_health_without_key(core_server):
    base_url, _ = core_server
    async with CoreClient(base_url, "") as client:
        assert await client.health() == {"status": "ok"}
        with pytest.raises(CoreApiError) as exc:
            await client.request_json("GET", "/api/v1/tasks")
    assert exc.value.kind is CoreApiErrorKind.CONFIG


async def test_json_unwrap_and_api_key_header(core_server):
    base_url, app = core_server
    async with CoreClient(base_url, "test-key") as client:
        data = await client.request_json("GET", "/api/v1/tasks")
    assert data["total"] == 1
    assert app["last_headers"]["X-API-Key"] == "test-key"


async def test_business_failure_is_error(core_server):
    base_url, _ = core_server
    async with CoreClient(base_url, "test-key") as client:
        with pytest.raises(CoreApiError) as exc:
            await client.request_json("GET", "/api/v1/business-fail")
    assert exc.value.kind is CoreApiErrorKind.BUSINESS
    assert "Task not found" in str(exc.value)


async def test_auth_failure(core_server):
    base_url, _ = core_server
    async with CoreClient(base_url, "test-key") as client:
        with pytest.raises(CoreApiError) as exc:
            await client.request_json("GET", "/api/v1/auth-fail")
    assert exc.value.kind is CoreApiErrorKind.AUTH
    assert exc.value.status == 401


async def test_validation_failure_message(core_server):
    base_url, _ = core_server
    async with CoreClient(base_url, "test-key") as client:
        with pytest.raises(CoreApiError) as exc:
            await client.request_json("GET", "/api/v1/validation-fail")
    assert exc.value.kind is CoreApiErrorKind.PAYLOAD
    assert "body.task_name" in str(exc.value)


async def test_server_failure(core_server):
    base_url, _ = core_server
    async with CoreClient(base_url, "test-key") as client:
        with pytest.raises(CoreApiError) as exc:
            await client.request_json("GET", "/api/v1/server-fail")
    assert exc.value.kind is CoreApiErrorKind.SERVER


async def test_upload_multipart(core_server, tmp_path: Path):
    base_url, app = core_server
    src = tmp_path / "data.jsonl"
    src.write_bytes(b'{"a": 1}\n')
    async with CoreClient(base_url, "test-key") as client:
        data = await client.upload_file(src, "dataset")
    assert data["file_id"] == "file-1"
    assert app["upload"]["filename"] == "data.jsonl"
    assert app["upload"]["fields"] == {"file_type": "dataset"}
    assert app["upload"]["data"] == b'{"a": 1}\n'


async def test_upload_rejects_invalid_file_type(core_server, tmp_path: Path):
    base_url, _ = core_server
    src = tmp_path / "x.txt"
    src.write_text("x")
    async with CoreClient(base_url, "test-key") as client:
        with pytest.raises(CoreApiError) as exc:
            await client.upload_file(src, "bogus")
    assert exc.value.kind is CoreApiErrorKind.PAYLOAD


async def test_download_stream(core_server, tmp_path: Path):
    base_url, _ = core_server
    async with CoreClient(base_url, "test-key") as client:
        data = await client.download_file("file-1", dest_dir=tmp_path)
    target = Path(data["path"])
    assert target.read_bytes() == b"hello world"
    assert target.name == "report.md"
    assert data["size_bytes"] == 11
    assert not list(tmp_path.glob("*.part"))


async def test_download_unicode_filename(core_server, tmp_path: Path):
    base_url, _ = core_server
    async with CoreClient(base_url, "test-key") as client:
        data = await client.download_file("unicode-file", dest_dir=tmp_path)
    assert Path(data["path"]).name == "报告.md"


async def test_download_json_error_not_written(core_server, tmp_path: Path):
    base_url, _ = core_server
    async with CoreClient(base_url, "test-key") as client:
        with pytest.raises(CoreApiError) as exc:
            await client.download_file("bad-file", dest_dir=tmp_path)
    assert exc.value.kind is CoreApiErrorKind.BUSINESS
    assert list(tmp_path.iterdir()) == []


async def test_download_legal_json_with_success_false_is_file(core_server, tmp_path: Path):
    """合法 JSON 文件顶层含 success:false 时，只要带 Content-Disposition 就按文件下载。"""
    base_url, _ = core_server
    async with CoreClient(base_url, "test-key") as client:
        data = await client.download_file("json-file", dest_dir=tmp_path)
    body = Path(data["path"]).read_text(encoding="utf-8")
    assert '"success": false' in body


async def test_download_without_attachment_header_is_error(core_server, tmp_path: Path):
    """200 HTML 且无 Content-Disposition：不得写盘，报 SERVER。"""
    base_url, _ = core_server
    async with CoreClient(base_url, "test-key") as client:
        with pytest.raises(CoreApiError) as exc:
            await client.download_file("html-error", dest_dir=tmp_path)
    assert exc.value.kind is CoreApiErrorKind.SERVER
    assert list(tmp_path.iterdir()) == []


async def test_download_inline_html_is_not_written(core_server, tmp_path: Path):
    """Content-Disposition: inline 的错误页不得当文件写盘。"""
    base_url, _ = core_server
    async with CoreClient(base_url, "test-key") as client:
        with pytest.raises(CoreApiError) as exc:
            await client.download_file("inline-html", dest_dir=tmp_path)
    assert exc.value.kind is CoreApiErrorKind.SERVER
    assert list(tmp_path.iterdir()) == []


async def test_download_long_filename(core_server, tmp_path: Path):
    base_url, _ = core_server
    async with CoreClient(base_url, "test-key") as client:
        data = await client.download_file("long-name", dest_dir=tmp_path)
    assert len(Path(data["path"]).name) == 243
    assert Path(data["path"]).read_bytes() == b"long"


async def test_download_file_id_fallback_is_sanitized(core_server, tmp_path: Path):
    base_url, _ = core_server
    async with CoreClient(base_url, "test-key") as client:
        data = await client.download_file("CON", dest_dir=tmp_path)
    assert Path(data["path"]).name == "_CON"
    assert Path(data["path"]).parent == tmp_path


async def test_download_trailing_slash_treats_dest_as_directory(core_server, tmp_path: Path):
    base_url, _ = core_server
    dest = str(tmp_path / "newdir") + "/"
    async with CoreClient(base_url, "test-key") as client:
        data = await client.download_file("file-1", dest=dest)
    assert Path(data["path"]) == tmp_path / "newdir" / "report.md"
    assert Path(data["path"]).is_file()


async def test_upload_network_error_is_network_not_io(tmp_path: Path):
    src = tmp_path / "x.txt"
    src.write_text("x", encoding="utf-8")
    async with CoreClient("http://127.0.0.1:1", "test-key", timeout_sec=0.2) as client:
        with pytest.raises(CoreApiError) as exc:
            await client.upload_file(src, "dataset")
    assert exc.value.kind is CoreApiErrorKind.NETWORK


async def test_download_network_error_is_network_not_io(tmp_path: Path):
    async with CoreClient("http://127.0.0.1:1", "test-key", timeout_sec=0.2) as client:
        with pytest.raises(CoreApiError) as exc:
            await client.download_file("f1", dest_dir=tmp_path)
    assert exc.value.kind is CoreApiErrorKind.NETWORK


async def test_redirect_not_followed(core_server):
    base_url, _ = core_server
    async with CoreClient(base_url, "test-key") as client:
        with pytest.raises(CoreApiError) as exc:
            await client.request_json("GET", "/redirect")
    assert exc.value.kind is CoreApiErrorKind.SERVER
    assert exc.value.status == 302


async def test_non_utf8_error_body_is_wrapped(core_server):
    base_url, _ = core_server
    async with CoreClient(base_url, "test-key") as client:
        with pytest.raises(CoreApiError) as exc:
            await client.request_json("GET", "/bad-encoding")
    assert exc.value.kind is CoreApiErrorKind.SERVER
    assert "HTTP 500" in str(exc.value)


def test_safe_filename_rejects_windows_paths_and_reserved_names():
    from saitec_cli.core_api.client import _safe_filename

    assert _safe_filename("../../etc/passwd") == "passwd"
    assert _safe_filename(r"C:\evil.txt") == "evil.txt"
    assert _safe_filename("C:evil.txt") == "C_evil.txt"
    assert _safe_filename("CON") == "_CON"
    assert _safe_filename("a;b.txt") == "a;b.txt"
    assert _safe_filename("report.md .") == "report.md"
    assert _safe_filename("..") is None
    long_cn = "报" * 120 + ".md"
    safe = _safe_filename(long_cn)
    assert safe.endswith(".md")
    assert len(safe.encode("utf-8")) <= 255


def test_filename_from_disposition_handles_quotes_and_language():
    from saitec_cli.core_api.client import _filename_from_disposition

    assert _filename_from_disposition('attachment; filename="a;b.txt"') == "a;b.txt"
    assert (
        _filename_from_disposition(
            "attachment; filename*=UTF-8'en'%E6%8A%A5%E5%91%8A.md"
        )
        == "报告.md"
    )
    assert _filename_from_disposition('attachment; filename="a%20b.txt"') == "a%20b.txt"
    assert (
        _filename_from_disposition(
            'attachment; filename="____.md"; '
            "filename*=UTF-8''%E5%AF%B9%E6%AF%94%E6%8A%A5%E5%91%8A.md"
        )
        == "对比报告.md"
    )
    assert (
        _filename_from_disposition(
            'attachment; filename="____.md"; '
            "filename*=UTF-8''%E8%AF%84%E6%B5%8B%E6%8A%A5%E5%91%8A.md"
        )
        == "评测报告.md"
    )


async def test_download_ndjson_is_not_parsed_as_error(core_server, tmp_path: Path):
    base_url, _ = core_server
    async with CoreClient(base_url, "test-key") as client:
        data = await client.download_file("data.jsonl", dest_dir=tmp_path)
    assert Path(data["path"]).read_bytes() == b'{"a": 1}\n{"a": 2}\n'


async def test_download_overwrite_protection(core_server, tmp_path: Path):
    base_url, _ = core_server
    target = tmp_path / "report.md"
    target.write_bytes(b"old")
    async with CoreClient(base_url, "test-key") as client:
        with pytest.raises(CoreApiError) as exc:
            await client.download_file("file-1", dest=target)
        assert exc.value.kind is CoreApiErrorKind.IO
        data = await client.download_file("file-1", dest=target, overwrite=True)
    assert data["size_bytes"] == 11
    assert target.read_bytes() == b"hello world"


async def test_download_filename_sanitization(core_server, tmp_path: Path):
    """服务端返回 ../ 路径时只取 basename。"""
    base_url, _ = core_server
    async with CoreClient(base_url, "test-key") as client:
        data = await client.download_file("evil", dest_dir=tmp_path)
    assert Path(data["path"]).name == "passwd"
    assert Path(data["path"]).parent == tmp_path


async def test_read_file_content(core_server):
    base_url, _ = core_server
    async with CoreClient(base_url, "test-key") as client:
        data = await client.read_file_content("file-1")
    assert data == {"content": "hello"}


async def test_invalid_base_url():
    with pytest.raises(CoreApiError) as exc:
        CoreClient("ftp://example.com", "k")
    assert exc.value.kind is CoreApiErrorKind.CONFIG
