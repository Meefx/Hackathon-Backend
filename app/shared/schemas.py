from pydantic import BaseModel


class Page[Item](BaseModel):
    items: list[Item]
    total: int
    limit: int
    offset: int
