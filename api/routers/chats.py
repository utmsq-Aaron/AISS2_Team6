"""Chat sessions — per-user persistent conversations (CRUD).

The list + history the UI's chat sidebar reads. Turns themselves are appended by
the streaming /api/chat endpoint as each turn completes. All routes are scoped to
the authenticated user.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import current_user
from core import chat_store as store

router = APIRouter(prefix="/chats", tags=["chats"])


class CreateChat(BaseModel):
    title: str = ""


class RenameChat(BaseModel):
    title: str


@router.get("")
def list_chats(user: str = Depends(current_user)) -> dict:
    return {"chats": store.list_chats(user)}


@router.post("")
def create_chat(body: CreateChat | None = None, user: str = Depends(current_user)) -> dict:
    return store.create_chat(user, (body.title if body else "") or "")


@router.get("/{chat_id}")
def get_chat(chat_id: str, user: str = Depends(current_user)) -> dict:
    chat = store.get_chat(user, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


@router.patch("/{chat_id}")
def rename_chat(chat_id: str, body: RenameChat, user: str = Depends(current_user)) -> dict:
    if not store.rename_chat(user, chat_id, body.title):
        raise HTTPException(status_code=404, detail="Chat not found")
    return {"ok": True}


@router.delete("/{chat_id}")
def delete_chat(chat_id: str, user: str = Depends(current_user)) -> dict:
    store.delete_chat(user, chat_id)
    return {"ok": True}
