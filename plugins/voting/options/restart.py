import asyncio
from typing import Optional

from core import Coalition, Server
from plugins.voting.base import VotableItem


class Restart(VotableItem):

    def __init__(self, server: Server, config: dict, params: Optional[list[str]] = None):
        super().__init__('restart', server, config, params)

    def can_vote(self) -> bool:
        return True
        # return not self.server.restart_pending

    def print(self) -> str:
        self.server.restart_pending = True
        message = "You can now vote for a mission restart.\n"
        if self.config.get('run_extensions', False):
            message += "Time and/or weather of this mission might change!\n"
        else:
            message += "The mission will be reset to its initial time."
        return message

    def get_choices(self) -> list[str]:
        return ["Restart", "Don't restart"]

    async def execute(self, winner: str):
        try:
            if winner == "Don't restart":
                return
            message = f"The mission will restart in 60s."
            await self.server.sendChatMessage(Coalition.ALL, message)
            await self.server.sendPopupMessage(Coalition.ALL, message)
            await asyncio.sleep(60)
            await self.server.restart(modify_mission=self.config.get('run_extensions', False))
        finally:
            self.server.restart_pending = False
