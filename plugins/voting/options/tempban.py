from typing import Optional

from core import Server, Player, Coalition
from plugins.voting.base import VotableItem


class Tempban(VotableItem):

    def __init__(self, server: Server, config: dict, params: Optional[list[str]] = None):
        super().__init__('mission', server, config, params)
        if not params or not len(params):
            raise TypeError("Usage: .vote tempban <player name>")
        self.player: Player = server.get_player(name=' '.join(params))
        if not self.player:
            raise ValueError('Player "{}" not found.'.format(' '.join(params)))

    def print(self) -> str:
        return (f"You can now vote to temporary ban player {self.player.name} for {self.config.get('duration', 3)} "
                f"days because of misbehaviour.")

    def get_choices(self) -> list[str]:
        return [f"Ban {self.player.name}", f"Don't ban {self.player.name}"]

    async def execute(self, winner: str):
        if winner.startswith("Don't"):
            message = f"Player {self.player.name} not banned."
        else:
            duration = self.config.get('duration', 3)
            self.server.bus.ban(self.player.ucid, banned_by='Other players', reason=f"Annoying people on the server",
                                days=duration)
            message = f"Player {self.player.name} banned for {duration} days."
        self.server.sendChatMessage(Coalition.ALL, message)
        self.server.sendPopupMessage(Coalition.ALL, message)
