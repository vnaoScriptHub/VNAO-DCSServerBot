from core import EventListener, utils, Server, Report, Player, event
from typing import Optional, Tuple


class MOTDListener(EventListener):

    def on_join(self, config: dict, server: Server, player: Player) -> Optional[str]:
        if 'messages' in config:
            for cfg in config['messages']:
                message = self.on_join(cfg, server, player)
                if message:
                    return message
        else:
            if 'recipients' in config:
                players = self.plugin.get_recipients(server, config)
                if player not in players:
                    return None
            return utils.format_string(config['message'])

    async def on_birth(self, config: dict, server: Server, player: Player) -> Tuple[Optional[str], Optional[dict]]:
        if 'messages' in config:
            for cfg in config['messages']:
                message, _ = await self.on_birth(cfg, server, player)
                if message:
                    return message, cfg
        else:
            message = None
            if 'recipients' in config:
                players = self.plugin.get_recipients(server, config)
                if player not in players:
                    return None, None
            if 'message' in config:
                message = utils.format_string(config['message'], server=server, player=player)
            elif 'report' in config:
                report = Report(self.bot, self.plugin_name, config['report'])
                env = await report.render(server=server, player=player, guild=self.bot.guilds[0])
                message = utils.embed_to_simpletext(env.embed)
            return message, config

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, data: dict) -> None:
        # make sure the config cache is re-read on mission changes
        self.plugin.get_config(server, use_cache=False)

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1:
            return
        config = self.plugin.get_config(server)
        if config and 'on_join' in config:
            player: Player = server.get_player(id=data['id'])
            player.sendChatMessage(self.on_join(config['on_join'], server, player))

    @event(name="onMissionEvent")
    async def onMissionEvent(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if not config:
            return
        if data['eventName'] == 'S_EVENT_BIRTH' and 'name' in data['initiator'] and 'on_birth' in config:
            player: Player = server.get_player(name=data['initiator']['name'], active=True)
            if not player:
                # should never happen, just in case
                return
            message, cfg = await self.on_birth(config['on_birth'], server, player)
            if message:
                self.plugin.send_message(message, server, cfg, player)
