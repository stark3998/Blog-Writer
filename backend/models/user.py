"""User model for Entra ID authentication."""

from pydantic import BaseModel


class UserInfo(BaseModel):
    user_id: str
    name: str
    email: str
