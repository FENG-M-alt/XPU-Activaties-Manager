"""封面图片上传（文档 2.8：JPG/PNG，≤ 2MB，保存时重命名并去除可执行权限）。"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, File, Request, UploadFile

from app.config import settings
from app.core.deps import CurrentAdmin, DbSession
from app.core.errors import ErrorCode, fail
from app.core.responses import ok
from app.services.logs import client_ip, write_log

router = APIRouter(tags=["管理端-上传"])

MAGIC = {
    b"\xff\xd8\xff": ".jpg",
    b"\x89PNG\r\n\x1a\n": ".png",
}


def detect_image_type(content: bytes) -> str | None:
    for magic, ext in MAGIC.items():
        if content.startswith(magic):
            return ext
    return None


@router.post("/uploads/cover", summary="上传活动封面")
async def upload_cover(db: DbSession, admin: CurrentAdmin, request: Request, file: UploadFile = File(...)):
    """仅接受 JPG/PNG 且不超过 2MB；服务端校验文件头并重命名保存。"""
    content = await file.read()
    if len(content) > settings.MAX_COVER_SIZE:
        fail(ErrorCode.PARAM_INVALID, f"封面不得超过 {settings.MAX_COVER_SIZE // (1024 * 1024)}MB",
             data={"field": "file", "reason": "文件过大"})
    if not content:
        fail(ErrorCode.PARAM_INVALID, "文件内容为空", data={"field": "file", "reason": "空文件"})

    ext = detect_image_type(content)
    if ext is None:
        fail(ErrorCode.PARAM_INVALID, "仅支持 JPG/PNG 图片",
             data={"field": "file", "reason": "文件头校验失败"})

    settings.ensure_dirs()
    filename = f"{secrets.token_hex(16)}{ext}"
    path = settings.COVER_DIR / filename
    path.write_bytes(content)
    try:
        path.chmod(0o644)  # 去除可执行权限
    except OSError:  # pragma: no cover - Windows
        pass

    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="UPLOAD_COVER",
        target_type="file",
        detail={"filename": filename, "size": len(content)},
        ip=client_ip(request),
    )
    db.commit()

    url = f"{request.base_url}api/v1/uploads/{filename}"
    return ok({"url": url, "path": f"/api/v1/uploads/{filename}", "size": len(content)}, message="上传成功")
