from typing import Optional

from core import Server, Player, Coalition
from plugins.voting.base import VotableItem


class Kick(VotableItem):

    def __init__(self, server: Server, config: dict, params: Optional[list[str]] = None):
        super().__init__('mission', server, config, params)
        if not params or not len(params):
            raise TypeError("Usage: .vote kick <player name>")
        self.player: Player = server.get_player(name=' '.join(params))
        if not self.player:
            raise ValueError('Player "{}" not found.'.format(' '.join(params)))

    def print(self) -> str:
        return f"You can now vote to kick player {self.player.name} because of misbehaviour."

    def get_choices(self) -> list[str]:
        return [f"Kick {self.player.name}", f"Don't kick {self.player.name}"]

    async def execute(self, winner: str):
        if winner.startswith("Don't"):
            message = f"Player {self.player.name} not kicked."
        else:
            self.server.kick(self.player, reason=f"Annoying people on the server")
            message = f"Player {self.player.name} kicked."
        self.server.sendChatMessage(Coalition.ALL, message)
        self.server.sendPopupMessage(Coalition.ALL, message)
