from __future__ import annotations
from contextlib import contextmanager
from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from co4.models import Base, Mutex

class Database:
    def __init__(self, url: str):
        args = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            args["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if ":memory:" in url:
                args["poolclass"] = StaticPool
        self.engine = create_engine(url, **args)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def configure(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=30000")
                connection.execute("PRAGMA journal_mode=WAL")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        # Schema creation is an explicit deployment step in multi-replica production.
        with self.sessions.begin() as s:
            if not s.get(Mutex, 1):
                s.add(Mutex(id=1, version=0))

    @contextmanager
    def transaction(self):
        with self.sessions.begin() as s:
            # DB-level serialization works across API processes; no in-memory queue locks.
            # Deliberate alpha tradeoff: one short writer transaction, no remote I/O inside it.
            s.execute(update(Mutex).where(Mutex.id == 1).values(version=Mutex.version + 1))
            yield s

    @contextmanager
    def read(self):
        with self.sessions() as s:
            yield s
