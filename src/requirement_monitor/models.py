from typing import Optional

from pydantic import BaseModel


class SendResult(BaseModel):
    success: bool
    attempts: int = 0
    format_used: str = "card"
    status_code: Optional[int] = None
    feishu_code: Optional[int] = None
    error: Optional[str] = None
