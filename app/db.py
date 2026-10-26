from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker


class Base(DeclarativeBase):
    pass


Session = scoped_session(sessionmaker(autoflush=True, expire_on_commit=False))
