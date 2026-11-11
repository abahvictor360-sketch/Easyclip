"""Send finished clips to Assign (the Postiz-based scheduler) as drafts."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import resolve_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...media_storage import MediaStorage
from ...services.assign_service import AssignError, AssignService
from ...services.task_service import TaskService
from ...utils.async_helpers import run_in_thread
from .tasks import _require_task_reader

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/assign", tags=["assign"])


class SendClipRequest(BaseModel):
    integration_ids: List[str] = Field(min_length=1, max_length=20)
    caption: str = Field(default="", max_length=5000)
    date: Optional[datetime] = None


def _service() -> AssignService:
    return AssignService(get_config())


@router.get("/status")
async def assign_status(request: Request, db: AsyncSession = Depends(get_db)):
    await resolve_authenticated_user_id(request, db, get_config())
    return {"configured": _service().configured}


@router.get("/channels")
async def assign_channels(request: Request, db: AsyncSession = Depends(get_db)):
    await resolve_authenticated_user_id(request, db, get_config())
    try:
        return {"channels": await _service().list_channels()}
    except AssignError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.post("/clips/{clip_id}/send")
async def send_clip_to_assign(
    clip_id: str,
    payload: SendClipRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    task_service = TaskService(db)
    clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    await _require_task_reader(request, task_service, db, clip["task_id"])

    file_path = await run_in_thread(MediaStorage().local_path, clip["file_path"])
    if not Path(file_path).exists():
        raise HTTPException(status_code=404, detail="Clip file not found")
    # Release the DB connection before the long upload.
    await db.close()

    try:
        return await _service().send_clip_as_draft(
            file_path=Path(file_path),
            filename=clip["filename"],
            caption=payload.caption,
            integration_ids=payload.integration_ids,
            date=payload.date,
        )
    except AssignError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
