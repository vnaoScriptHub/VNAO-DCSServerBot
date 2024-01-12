import discord
import sys
import uuid
import matplotlib.figure

from core import EventListener, Plugin, Server, event, Player, PersistentReport, Channel
from io import BytesIO
from matplotlib import pyplot as plt
from typing import Tuple, Literal

from .const import StrafeQuality, BombQuality


class FunkManEventListener(EventListener):

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.config = self.get_config()
        sys.path.append(self.config['install'])
        from funkman.utils.utils import _GetVal
        self.funkplot = None
        self._GetVal = _GetVal

    def get_funkplot(self):
        if not self.funkplot:
            from funkman.funkplot.funkplot import FunkPlot
            self.funkplot = FunkPlot(ImagePath=self.config['IMAGEPATH'])
        return self.funkplot

    # from FunkBot, to be replaced with a proper function call!
    def create_lso_embed(self, result: dict) -> discord.Embed:
        actype = self._GetVal(result, "airframe", "Unkown")
        Tgroove = self._GetVal(result, "Tgroove", "?", 1)
        player = self._GetVal(result, "name", "Ghostrider")
        grade = self._GetVal(result, "grade", "?")
        points = self._GetVal(result, "points", "?")
        details = self._GetVal(result, "details", "?")
        case = self._GetVal(result, "case", "?")
        wire = self._GetVal(result, "wire", "?")
        carriertype = self._GetVal(result, "carriertype", "?")
        carriername = self._GetVal(result, "carriername", "?")
        windondeck = self._GetVal(result, "wind", "?", 1)
        missiontime = self._GetVal(result, "mitime", "?")
        missiondate = self._GetVal(result, "midate", "?")
        theatre = self._GetVal(result, "theatre", "Unknown Map")
        theta = self._GetVal(result, "carrierrwy", -9)

        color = 0x000000 # Grey (Should only see this with a WOFD)
        urlIm = "https://i.imgur.com/qwGBxgt.png"
        if type(points) != str:
            if points == 0:
                color = 0xff0000  # red
                urlIm = "https://i.imgur.com/6kbbdwl.png"
            elif points == 1:
                color = 0x000000  # black
                urlIm = "https://i.imgur.com/5uuGG5h.png"
            elif points == 2:
                color = 0xB47E59  # brown
                urlIm = "https://i.imgur.com/AZBCh1E.png"
            elif points == 2.5:
                color = 0x0000FF  # blue
                urlIm = "https://i.imgur.com/u0PJFdt.png"
            elif points == 3:
                color = 0xFFFF00  # yellow
                urlIm = "https://i.imgur.com/R5WpdWB.png"
            elif points == 4:
                color = 0x00FF00  # green
                urlIm = "https://i.imgur.com/O6RzvRY.png"
            elif points == 5:
                color = 0x00FF00  # green
                urlIm = "https://i.imgur.com/O6RzvRY.png"

        # Create Embed
        embed = discord.Embed(title="LSO Grade",
                              description=f"Result for {player} at carrier {carriername} [{carriertype}]",
                              color=color)

        # Images.
        embed.set_thumbnail(url=urlIm)

        # Data.
        embed.add_field(name="Grade", value=grade)
        embed.add_field(name="Points", value=points)
        embed.add_field(name="Details", value=details)
        embed.add_field(name="Groove", value=Tgroove)
        if wire != "?":
            embed.add_field(name="Wire", value=wire)
        embed.add_field(name="Case", value=case)
        embed.add_field(name="Wind", value=windondeck)
        embed.add_field(name="Aircraft", value=actype)

        # Footer.
        embed.set_footer(text=f"{theatre}: {missiondate} ({missiontime})")
        return embed

    @staticmethod
    def save_fig(fig: matplotlib.figure.Figure) -> Tuple[str, BytesIO]:
        filename = f'{uuid.uuid4()}.png'
        buffer = BytesIO()
        fig.savefig(buffer, format='png', bbox_inches='tight', facecolor='#2C2F33')
        buffer.seek(0)
        plt.close(fig)
        return filename, buffer

    async def send_fig(self, server: Server, fig: matplotlib.figure.Figure, channel: discord.TextChannel):
        filename, buffer = self.save_fig(fig)
        with buffer:
            await channel.send(file=discord.File(fp=buffer, filename=filename),
                               delete_after=self.config.get('delete_after'))

    async def update_rangeboard(self, server: Server, what: Literal['strafe', 'bomb']):
        # update the server specific board
        config = self.plugin.get_config(server)
        if config.get(f'{what}_board', False):
            channel_id = int(config.get(f'{what}_channel', server.channels[Channel.STATUS]))
            num_rows = config.get('num_rows', 10)
            report = PersistentReport(self.bot, self.plugin_name, f'{what}board.json',
                                      embed_name=f'{what}board', server=server, channel_id=channel_id)
            await report.render(server_name=server.name, num_rows=num_rows)
        # update the global board
        config = self.get_config()
        if f'{what}_channel' in config and config.get(f'{what}_board', False):
            num_rows = config.get('num_rows', 10)
            report = PersistentReport(self.bot, self.plugin_name, f'{what}board.json', embed_name=f'{what}board',
                                      channel_id=int(config[f'{what}_channel']))
            await report.render(server_name=None, num_rows=num_rows)

    @event(name="moose_text")
    async def moose_text(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        channel = self.bot.get_channel(int(config.get('CHANNELID_MAIN', -1)))
        if not channel:
            return
        await channel.send(data['text'], delete_after=self.config.get('delete_after'))

    @event(name="moose_bomb_result")
    async def moose_bomb_result(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        player: Player = server.get_player(name=data['player'])
        if player:
            with self.pool.connection() as conn:
                with conn.transaction():
                    conn.execute("""
                        INSERT INTO bomb_runs (mission_id, player_ucid, unit_type, range_name, distance, quality)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (server.mission_id, player.ucid, player.unit_type, data.get('rangename', 'n/a'),
                          data['distance'], BombQuality[data['quality']].value))
            await self.update_rangeboard(server, 'bomb')
        channel = self.bot.get_channel(int(config.get('CHANNELID_RANGE', -1)))
        if not channel:
            return
        fig, _ = self.get_funkplot().PlotBombRun(data)
        if not fig:
            self.log.error("Bomb result could not be plotted (due to missing data?)")
            return
        await self.send_fig(server, fig, channel)

    @event(name="moose_strafe_result")
    async def moose_strafe_result(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        player: Player = server.get_player(name=data['player'])
        if player:
            with self.pool.connection() as conn:
                with conn.transaction():
                    conn.execute("""
                        INSERT INTO strafe_runs (mission_id, player_ucid, unit_type, range_name, accuracy, quality)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (server.mission_id, player.ucid, player.unit_type, data.get('rangename', 'n/a'),
                          data['strafeAccuracy'], StrafeQuality[data['roundsQuality'].replace(' ', '_')].value if not data.get('invalid', False) else None))
            await self.update_rangeboard(server, 'strafe')
        channel = self.bot.get_channel(int(config.get('CHANNELID_RANGE', -1)))
        if not channel:
            return
        fig, _ = self.get_funkplot().PlotStrafeRun(data)
        if not fig:
            self.log.error("Strafe result could not be plotted (due to missing data?)")
            return
        await self.send_fig(server, fig, channel)

    @event(name="moose_lso_grade")
    async def moose_lso_grade(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        channel = self.bot.get_channel(int(config.get('CHANNELID_AIRBOSS', -1)))
        if not channel:
            return
        try:
            fig, _ = self.get_funkplot().PlotTrapSheet(data)
            if not fig:
                self.log.error("Trapsheet could not be plotted (due to missing data?)")
                return
            filename, buffer = self.save_fig(fig)
            with buffer:
                embed = self.create_lso_embed(data)
                embed.set_image(url=f"attachment://{filename}")
                await channel.send(embed=embed, file=discord.File(fp=buffer, filename=filename),
                                   delete_after=self.config.get('delete_after'))
        except (ValueError, TypeError):
            self.log.error("No trapsheet data received from DCS!")
