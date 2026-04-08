"""Conversation history endpoints."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.db_models import Conversation, Message, User
from app.services.auth import get_current_user

router = APIRouter(prefix="/conversations", tags=["conversations"])


# --- Schemas ---
class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime
    nextflow_code: Optional[str] = None
    mermaid_code: Optional[str] = None
    ast_json: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class ConversationRename(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)


class ConversationOut(BaseModel):
    id: int
    session_id: str
    title: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ConversationDetail(ConversationOut):
    messages: List[MessageOut]


# --- Endpoints ---
@router.get("", response_model=List[ConversationOut])
def list_conversations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(Conversation)
        .filter(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
        .all()
    )


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conv = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id, Conversation.user_id == user.id)
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.patch("/{conversation_id}", response_model=ConversationOut)
def rename_conversation(
    conversation_id: int,
    payload: ConversationRename,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conv = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id, Conversation.user_id == user.id)
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conv.title = payload.title.strip()[:255]
    conv.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(conv)
    return conv


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conv = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id, Conversation.user_id == user.id)
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.delete(conv)
    db.commit()
    return {"status": "ok"}


# --- Helpers used by /chat ---
def get_or_create_conversation(
    db: Session, user: User, session_id: str, first_user_message: str
) -> Conversation:
    conv = (
        db.query(Conversation)
        .filter(Conversation.user_id == user.id, Conversation.session_id == session_id)
        .first()
    )
    if conv:
        return conv
    title = (first_user_message or "New chat").strip().splitlines()[0][:80]
    conv = Conversation(user_id=user.id, session_id=session_id, title=title)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def append_message(
    db: Session,
    conversation: Conversation,
    role: str,
    content: str,
    nextflow_code: Optional[str] = None,
    mermaid_code: Optional[str] = None,
    ast_json: Optional[Dict[str, Any]] = None,
) -> Message:
    msg = Message(
        conversation_id=conversation.id,
        role=role,
        content=content,
        nextflow_code=nextflow_code,
        mermaid_code=mermaid_code,
        ast_json=ast_json,
    )
    db.add(msg)
    conversation.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(msg)
    return msg
