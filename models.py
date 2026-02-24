"""
ORM-модели для чат-виджета и кабинета оператора.

Все первичные ключи — UUID строки (SQLite не имеет нативного типа UUID).
Временные метки хранятся в UTC с timezone-info.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Dialog(Base):
    """Один диалог (сессия чата) с посетителем сайта."""

    __tablename__ = "dialogs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default="open", nullable=False
    )  # "open" | "closed"
    source: Mapped[str] = mapped_column(
        String(32), default="widget", nullable=False
    )  # "widget" — на будущее: "telegram", "whatsapp" и т.д.
    user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meta: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-строка
    operator_mode: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="0"
    )

    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="dialog",
        order_by="Message.created_at",
    )


class Message(Base):
    """Одно сообщение в диалоге."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    dialog_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("dialogs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,  # критично для быстрой выборки истории по dialog_id
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # "user" | "bot" | "operator"
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    dialog: Mapped["Dialog"] = relationship("Dialog", back_populates="messages")


class AdminUser(Base):
    """Оператор (администратор) — может читать диалоги и отвечать клиентам."""

    __tablename__ = "admin_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
