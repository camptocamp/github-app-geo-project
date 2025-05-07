"""Models for the GitHub App Geo Project."""

import enum
import logging
import os
from datetime import datetime
from typing import Any, TypedDict, Union

import sqlalchemy
import sqlalchemy.sql.functions
from sqlalchemy import JSON, BigInteger, DateTime, Enum, Integer, Unicode
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_LOGGER = logging.getLogger(__name__)

_SCHEMA = os.environ.get("GHCI_SCHEMA", "ghci")


class Base(DeclarativeBase):
    """Base class for the models."""


class JobStatus(enum.Enum):
    """Enum for the status of the job."""

    NEW = "new"
    PENDING = "pending"
    ERROR = "error"
    DONE = "done"
    SKIPPED = "skipped"


class Queue(Base):
    """SQLAlchemy model for the queue."""

    __tablename__ = "queue"
    __table_args__ = {"schema": _SCHEMA}  # noqa: RUF012

    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False, autoincrement=True)
    status: Mapped[str] = mapped_column(
        Unicode,
        nullable=False,
        default=JobStatus.NEW.value,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy.sql.functions.now(),
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    application: Mapped[str] = mapped_column(Unicode, nullable=False)
    owner: Mapped[str] = mapped_column(Unicode, nullable=True)
    repository: Mapped[str] = mapped_column(Unicode, nullable=True)
    event_name: Mapped[str] = mapped_column(Unicode, nullable=False)
    event_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    module: Mapped[str] = mapped_column(Unicode, nullable=True)
    module_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=True)
    log: Mapped[str] = mapped_column(Unicode, nullable=True)
    check_run_id: Mapped[int] = mapped_column(BigInteger, nullable=True)

    def __repr__(self) -> str:
        """Return the representation of the job."""
        return f"Queue {self.id} [{self.status}]"

    @property
    def status_enum(self) -> JobStatus:
        """Return the status as an enum."""
        return JobStatus(self.status)

    @status_enum.setter
    def status_enum(self, value: JobStatus) -> None:
        """Set the status from an enum."""
        self.status = value.value


class OutputStatus(enum.Enum):
    """Enum for the status of the output."""

    ERROR = "error"
    SUCCESS = "success"


class AccessType(enum.Enum):
    """Enum for the access type of the output."""

    PUBLIC = "public"
    PULL = "pull"
    PUSH = "push"
    ADMIN = "admin"


class OutputData(TypedDict):
    """Type for the output data."""

    title: str
    # The children will be collapsed by default
    children: list[Union[str, "OutputData"]]


class Output(Base):
    """SQLAlchemy model for the output entries."""

    __tablename__ = "output"
    __table_args__ = {"schema": _SCHEMA}  # noqa: RUF012

    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy.sql.functions.now(),
        index=True,
    )
    status: Mapped[OutputStatus] = mapped_column(
        Enum(OutputStatus, create_type=False, native_enum=False),
        nullable=False,
        index=True,
    )
    owner: Mapped[str] = mapped_column(Unicode, nullable=False)
    repository: Mapped[str] = mapped_column(Unicode, nullable=False, index=True)
    access_type: Mapped[AccessType] = mapped_column(
        Enum(AccessType, create_type=False, native_enum=False),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Unicode, nullable=False)
    data: Mapped[list[str | OutputData]] = mapped_column(JSON, nullable=False)


class ModuleStatus(Base):
    """SQLAlchemy model for the output entries."""

    __tablename__ = "module_status"
    __table_args__ = {"schema": _SCHEMA}  # noqa: RUF012

    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False, autoincrement=True)
    module: Mapped[str] = mapped_column(Unicode, nullable=False, unique=True, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
