from core import report, Server, Side, Coalition


class Main(report.EmbedElement):
    async def render(self, server: Server, sides: list[Coalition]):
        players = server.get_active_players()
        coalitions = {
            Side.SPECTATOR: {"names": [], "units": []},
            Side.BLUE: {"names": [], "units": []},
            Side.RED: {"names": [], "units": []},
            Side.NEUTRAL: {"names": [], "units": []}
        }
        for player in players:
            coalitions[player.side]['names'].append(player.display_name)
            coalitions[player.side]['units'].append(player.unit_type if player.side != 0 else '')
        if Coalition.BLUE in sides and len(coalitions[Side.BLUE]['names']):
            self.add_field(name='Blue', value='_ _')
            self.add_field(name='Name', value='\n'.join(coalitions[Side.BLUE]['names']) or '_ _')
            self.add_field(name='Unit', value='\n'.join(coalitions[Side.BLUE]['units']) or '_ _')
        if Coalition.RED in sides and len(coalitions[Side.RED]['names']):
            self.add_field(name='Red', value='_ _')
            self.add_field(name='Name', value='\n'.join(coalitions[Side.RED]['names']) or '_ _')
            self.add_field(name='Unit', value='\n'.join(coalitions[Side.RED]['units']) or '_ _')
        if Coalition.NEUTRAL in sides and len(coalitions[Side.NEUTRAL]['names']):
            self.add_field(name='Neutral', value='_ _')
            self.add_field(name='Name', value='\n'.join(coalitions[Side.NEUTRAL]['names']) or '_ _')
            self.add_field(name='Unit', value='\n'.join(coalitions[Side.NEUTRAL]['units']) or '_ _')
        # Spectators
        if len(coalitions[Side.SPECTATOR]['names']):
            self.add_field(name='Spectator', value='_ _')
            self.add_field(name='Name', value='\n'.join(coalitions[Side.SPECTATOR]['names']) or '_ _')
            self.add_field(name='_ _', value='_ _')
