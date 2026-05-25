from __future__ import annotations

from typing import TypeVar

from sqlmodel import Session, SQLModel, select

T = TypeVar("T", bound=SQLModel)


class Repository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, item: T) -> T:
        self.session.add(item)
        self.session.commit()
        self.session.refresh(item)
        return item

    def add_all(self, items: list[SQLModel]) -> None:
        self.session.add_all(items)
        self.session.commit()

    def list(self, model: type[T], limit: int = 100, offset: int = 0) -> list[T]:
        statement = select(model).offset(offset).limit(limit)
        return list(self.session.exec(statement).all())

    def get(self, model: type[T], item_id: str) -> T | None:
        return self.session.get(model, item_id)