"""Models for the GitHub App Geo Project."""

import logging
from datetime import datetime
from typing import Any

import sqlalchemy
import sqlalchemy.ext.declarative
import sqlalchemy.orm
import sqlalchemy.sql.functions
from sqlalchemy import JSON, DateTime, Integer, Unicode
from sqlalchemy.orm import Mapped, mapped_column

_LOGGER = logging.getLogger(__name__)

DBSession = sqlalchemy.orm.scoped_session(sqlalchemy.orm.sessionmaker())
# Base = sqlalchemy.ext.declarative.declarative_base()

# - new: the job is added in the queue
# - pending: the job is processing
# - error: the job is in error
# - done: the job is finished with success
STATUS_NEW = "new"
STATUS_PENDING = "pending"
STATUS_ERROR = "error"
STATUS_DONE = "done"

_SCHEMA = "github_app"


Base = sqlalchemy.orm.declarative_base()


class Queue(Base):  # type: ignore[misc,valid-type]
    """SQLAlchemy model for the queue."""

    __tablename__ = "queue"
    __table_args__ = {"schema": _SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False, autoincrement=True)
    status: Mapped[str] = mapped_column(Unicode, nullable=False, default=STATUS_NEW, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sqlalchemy.sql.functions.now(), index=True  # type: ignore[no-untyped-call]
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    application: Mapped[str] = mapped_column(Unicode, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    def __repr__(self) -> str:
        """Return the representation of the job."""
        return f"Queue {self.id} [{self.status}]"


class Output(Base):  # type: ignore[misc,valid-type]
    """SQLAlchemy model for the output entries."""

    __tablename__ = "output"
    __table_args__ = {"schema": _SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False, autoincrement=True)
    repository: Mapped[str] = mapped_column(Unicode, nullable=False)
    access_type: Mapped[str] = mapped_column(Unicode, nullable=False)
    title: Mapped[str] = mapped_column(Unicode, nullable=False)
    data: Mapped[str] = mapped_column(Unicode, nullable=False)
