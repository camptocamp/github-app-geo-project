import logging

import sqlalchemy
import sqlalchemy.ext.declarative
import sqlalchemy.orm
import sqlalchemy.sql.functions
from sqlalchemy import JSON, Column, DateTime, Integer, Unicode

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

_schema = "github_app"


class Base(sqlalchemy.ext.declarative.DeclarativeBase):
    """Base class for the SQLAlchemy models."""


class Queue(Base):
    """SQLAlchemy model for the queue."""

    __tablename__ = "queue"
    __table_args__ = {"schema": _schema}

    id = Column(Integer, primary_key=True, nullable=False, autoincrement=True)
    status = Column(Unicode, nullable=False, default=STATUS_NEW, index=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=sqlalchemy.sql.functions.now(), index=True  # type: ignore[no-untyped-call]
    )
    started_at = Column(DateTime(timezone=True))
    priority = Column(Integer, nullable=False, default=0, index=True)
    application = Column(Unicode, nullable=False)
    data = Column(JSON, nullable=False)

    def __repr__(self) -> str:
        """Return the representation of the job."""
        return f"Queue {self.id} [{self.status}]"


class Output(Base):
    """SQLAlchemy model for the output entries."""

    __tablename__ = "output"
    __table_args__ = {"schema": _schema}

    id = Column(Integer, primary_key=True, nullable=False, autoincrement=True)
    repository = Column(Unicode, nullable=False)
    access_type = Column(Unicode, nullable=False)
    title = Column(Unicode, nullable=False)
    data = Column(Unicode, nullable=False)
