"""文件下载辅助：安全的 Content-Disposition 与 PDF/Excel 响应。"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from fastapi.responses import FileResponse, StreamingResponse

SAFE_NAME = re.compile(r"[^\w\-.()（）\u4e00-\u9fa5]+")


def sanitize_filename(name: str) -> str:
    cleaned = SAFE_NAME.sub("_", name).strip("._") or "download"
    return cleaned[:120]


def content_disposition(filename: str) -> str:
    ascii_fallback = sanitize_filename(filename).encode("ascii", "ignore").decode("ascii") or "download.xlsx"
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


def file_response(path: Path, filename: str, media_type: str | None = None) -> FileResponse:
    return FileResponse(
        path,
        filename=filename,
        media_type=media_type
        or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition(filename)},
    )


def bytes_response(content: bytes, filename: str, media_type: str) -> StreamingResponse:
    import io

    return StreamingResponse(
        io.BytesIO(content),
        media_type=media_type,
        headers={"Content-Disposition": content_disposition(filename)},
    )


def safe_join(base: Path, *parts: str) -> Path:
    """防止目录遍历（文档 2.8）。"""
    candidate = base.joinpath(*parts).resolve()
    if not str(candidate).startswith(str(base.resolve())):
        raise ValueError("非法路径")
    return candidate
