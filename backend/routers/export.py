"""Export Router — Convert blog content to various formats."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Literal

from backend.services.export_service import export_blog, ExportFormat

router = APIRouter(prefix="/api", tags=["export"])


class ExportRequest(BaseModel):
    content: str
    format: ExportFormat


@router.post("/export")
async def export_blog_content(request: ExportRequest):
    """Export blog content to the specified format.

    Returns the file as a downloadable attachment.
    """
    try:
        file_bytes, filename, content_type = export_blog(
            content=request.content,
            format=request.format,
        )
        return Response(
            content=file_bytes,
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")
