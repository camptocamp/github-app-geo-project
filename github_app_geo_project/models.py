"""Models for the GitHub App Geo Project."""

import enum
import logging
from datetime import datetime
from typing import Any, TypedDict, Union

import sqlalchemy
import sqlalchemy.ext.declarative
import sqlalchemy.orm
import sqlalchemy.sql.functions
from sqlalchemy import JSON, DateTime, Enum, Integer, Unicode
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_LOGGER = logging.getLogger(__name__)

DBSession = sqlalchemy.orm.scoped_session(sqlalchemy.orm.sessionmaker())
# Base = sqlalchemy.ext.declarative.declarative_base()

_SCHEMA = "github_app"


class Base(DeclarativeBase):
    pass


class JobStatus(enum.Enum):
    """Enum for the status of the job."""

    new = "new"
    pending = "pending"
    error = "error"
    done = "done"


class Queue(Base):  # type: ignore[misc,valid-type]
    """SQLAlchemy model for the queue."""

    __tablename__ = "queue"
    __table_args__ = {"schema": _SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False, autoincrement=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus), native_enum=False, nullable=False, default=JobStatus.new, index=True
    )
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


class OutputStatus(enum.Enum):
    """Enum for the status of the output."""

    error = "error"
    success = "success"


class AccessType(enum.Enum):
    """Enum for the access type of the output."""

    public = "public"
    pull = "pull"
    push = "push"
    admin = "admin"


class OutputData(TypedDict):
    """Type for the output data."""

    title: str
    # The children will be collapsed by default
    children: list[Union[str, "OutputData"]]


class Output(Base):  # type: ignore[misc,valid-type]
    """SQLAlchemy model for the output entries."""

    __tablename__ = "output"
    __table_args__ = {"schema": _SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False, autoincrement=True)
    status: Mapped[OutputStatus] = mapped_column(
        Enum(OutputStatus), native_enum=False, nullable=False, index=True
    )
    repository: Mapped[str] = mapped_column(Unicode, nullable=False, index=True)
    access_type: Mapped[AccessType] = mapped_column(Enum(AccessType), native_enum=False, nullable=False)
    title: Mapped[str] = mapped_column(Unicode, nullable=False)
    data: Mapped[list[Union[str, OutputData]]] = mapped_column(JSON, nullable=False)
