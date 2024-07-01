from abc import ABC
from typing import Optional

from core import Server


class VotableItem(ABC):
    def __init__(self, name: str, server: Server, config: dict, params: Optional[list[str]] = None):
        self.name = name
        self.server = server
        self.config = config
        self.param = params

    def can_vote(self) -> bool:
        return True

    def print(self) -> str:
        ...

    def get_choices(self) -> list[str]:
        ...

    async def execute(self, winner: str):
        ...
