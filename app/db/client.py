from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def get_engine(db_path: str):
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    # WAL mode: prevents lock contention between morning brief batch and watcher loop
    @event.listens_for(engine, "connect")
    def set_wal_mode(connection, _):
        connection.execute("PRAGMA journal_mode=WAL")

    return engine


def get_session_factory(db_path: str) -> sessionmaker:
    engine = get_engine(db_path)
    return sessionmaker(engine)  # SQLAlchemy 2.0: bind kwarg removed
