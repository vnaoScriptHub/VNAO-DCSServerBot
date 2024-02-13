from typing import cast

import discord
import os

from core import utils, Server, ServiceRegistry
from discord import SelectOption, TextStyle
from discord.ui import View, Select, Button, Modal, TextInput
from services import MusicService

from services.music.radios import Mode
from .utils import get_tag


class MusicPlayer(View):

    def __init__(self, server: Server, radio_name: str, playlists: list[str]):
        super().__init__()
        self.radio_name = radio_name
        self.service: MusicService = cast(MusicService, ServiceRegistry.get("Music"))
        self.log = self.service.log
        self.server = server
        self.playlists = playlists
        self.songs = self.titles = None
        self.config = None

    async def get_titles(self, songs: list[str]) -> list[str]:
        titles = []
        for x in songs:
            try:
                titles.append(get_tag(os.path.join(await self.service.get_music_dir(), x)).title or x[:-4])
            except OSError:
                self.log.warning(f"Song {x} not found, removed from playlist")
        return titles

    async def render(self) -> discord.Embed:
        if not self.titles:
            self.songs = await self.service.get_songs(self.server, self.radio_name)
            self.titles = await self.get_titles(self.songs)
        self.config = self.service.get_config(self.server, self.radio_name)
        embed = discord.Embed(colour=discord.Colour.blue())
        embed.add_field(name="Frequency", value=self.config['frequency'] + " " + self.config['modulation'])
        embed.add_field(name="Coalition", value="Red" if self.config['coalition'] == 1 else
                                                "Blue" if self.config['coalition'] == 2 else
                                                "Neutral")
        embed.title = "Music Player"
        current = await self.service.get_current_song(self.server, self.radio_name)
        if current:
            tag = get_tag(current)
            title = utils.escape_string(tag.title[:255] if tag.title else os.path.basename(current)[:-4])
            artist = utils.escape_string(tag.artist[:255] if tag.artist else 'n/a')
            album = utils.escape_string(tag.album[:255] if tag.album else 'n/a')
            embed.add_field(name='▬' * 13 + " Now Playing " + '▬' * 13, value='_ _', inline=False)
            embed.add_field(name="Title", value=title)
            embed.add_field(name='Artist', value=artist)
            embed.add_field(name='Album', value=album)
        embed.add_field(name='▬' * 14 + " Playlist " + '▬' * 14, value='_ _', inline=False)
        playlist = []
        for idx, title in enumerate(self.titles):
            playlist.append(
                f"{idx + 1}. - {utils.escape_string(title)}")
        all_songs = '\n'.join(playlist) or '- empty -'
        embed.add_field(name='_ _', value=all_songs[:1024])
        footer = "▬" * 37 + "\n"
        self.clear_items()
        # Select Song
        if self.titles:
            select = Select(placeholder="Pick a song from the list")
            select.options = [
                SelectOption(label=x[:25], value=str(idx))
                for idx, x in enumerate(self.titles)
                if idx < 25
            ]
            select.callback = self.play
            self.add_item(select)
            if len(self.titles) > 25:
                footer += "Use /music play to access all songs in the list.\n"
        # Select Playlists
        if self.playlists:
            select = Select(placeholder="Pick a playlist to play")
            select.options = [SelectOption(label=x) for x in self.playlists]
            select.callback = self.playlist
            self.add_item(select)
        # Play/Stop Button
        button = Button(style=discord.ButtonStyle.primary,
                        emoji="⏹️" if await self.service.get_current_song(self.server, self.radio_name) else "▶️")
        button.callback = self.on_play_stop
        self.add_item(button)
        # Skip Button
        button = Button(style=discord.ButtonStyle.primary, emoji="⏩")
        button.callback = self.on_skip
        self.add_item(button)
        # Repeat Button
        button = Button(style=discord.ButtonStyle.primary,
                        emoji="🔁" if await self.service.get_mode(self.server, self.radio_name) == Mode.REPEAT else "🔂")
        button.callback = self.on_repeat
        self.add_item(button)
        # Edit Button
        button = Button(label="Edit", style=discord.ButtonStyle.secondary)
        button.callback = self.on_edit
        self.add_item(button)
        # Quit Button
        button = Button(label="Quit", style=discord.ButtonStyle.red)
        button.callback = self.on_cancel
        self.add_item(button)
        if await self.service.get_current_song(self.server, self.radio_name):
            footer += "⏹️ Stop"
        else:
            footer += "▶️ Play"
        footer += " | ⏩ Skip | "
        if await self.service.get_mode(self.server, self.radio_name) == Mode.SHUFFLE:
            footer += "🔁 Repeat"
        else:
            footer += "🔂 Shuffle"
        embed.set_footer(text=footer)
        return embed

    def edit(self) -> Modal:
        class EditModal(Modal, title=f"Change Settings for {self.radio_name}"):
            frequency = TextInput(label='Frequency (xxx.xx)', style=TextStyle.short, required=True,
                                  default=self.config['frequency'], min_length=4, max_length=6)
            modulation = TextInput(label='Modulation (AM | FM)', style=TextStyle.short, required=True,
                                   default=self.config['modulation'], min_length=2, max_length=2)
            volume = TextInput(label='Volume', style=TextStyle.short, required=True,
                               default=self.config['volume'], min_length=1, max_length=3)
            coalition = TextInput(label='Coalition (1=red | 2=blue)', style=TextStyle.short, required=True,
                                  default=self.config['coalition'], min_length=1, max_length=2)
            display_name = TextInput(label='Display Name', style=TextStyle.short, required=True,
                                     default=self.config['display_name'], min_length=3, max_length=30)

            async def on_submit(derived, interaction: discord.Interaction):
                await interaction.response.defer()
                self.config['frequency'] = derived.frequency.value
                if derived.modulation.value.upper() in ['AM', 'FM']:
                    self.config['modulation'] = derived.modulation.value.upper()
                else:
                    raise ValueError("Modulation must be one of AM | FM!")
                self.config['volume'] = derived.volume.value
                if derived.coalition.value.isnumeric() and int(derived.coalition.value) in range(1, 3):
                    self.config['coalition'] = derived.coalition.value
                else:
                    raise ValueError("Coalition must be 1 or 2!")
                self.config['display_name'] = derived.display_name.value
                # write the config
                await self.service.set_config(self.server, self.radio_name, self.config)
                await self.service.stop_radios(self.server, self.radio_name)
                await self.service.start_radios(self.server, self.radio_name)

            async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
                await interaction.followup.send(error.__str__(), ephemeral=True)

        return EditModal()

    async def play(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.service.stop_radios(self.server, self.radio_name)
        await self.service.play_song(self.server, self.radio_name,
                                     os.path.join(await self.service.get_music_dir(),
                                                  self.songs[int(interaction.data['values'][0])]))
        await self.service.start_radios(self.server, self.radio_name)
        await interaction.edit_original_response(view=self, embed=await self.render())

    async def playlist(self, interaction: discord.Interaction):
        await interaction.response.defer()
        running = await self.service.get_current_song(self.server, self.radio_name)
        if running:
            await self.service.stop_radios(self.server, self.radio_name)
        await self.service.set_playlist(self.server, self.radio_name, interaction.data['values'][0])
        self.titles = None
        if running:
            await self.service.start_radios(self.server, self.radio_name)
        await interaction.edit_original_response(view=self, embed=await self.render())

    async def on_play_stop(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if await self.service.get_current_song(self.server, self.radio_name):
            await self.service.stop_radios(self.server, self.radio_name)
        else:
            await self.service.start_radios(self.server, self.radio_name)
        embed = await self.render()
        await interaction.edit_original_response(embed=embed, view=self)

    async def on_skip(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.service.skip_song(self.server, self.radio_name)
        embed = await self.render()
        await interaction.edit_original_response(embed=embed, view=self)

    async def on_repeat(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.service.stop_radios(self.server, self.radio_name)
        if await self.service.get_mode(self.server, self.radio_name) == Mode.SHUFFLE:
            await self.service.set_mode(self.server, self.radio_name, Mode.REPEAT)
        else:
            await self.service.set_mode(self.server, self.radio_name, Mode.SHUFFLE)
        await self.service.start_radios(self.server, self.radio_name)
        embed = await self.render()
        await interaction.edit_original_response(embed=embed, view=self)

    async def on_edit(self, interaction: discord.Interaction):
        try:
            modal = self.edit()
            await interaction.response.send_modal(modal)
            if not await modal.wait():
                embed = await self.render()
                await interaction.edit_original_response(embed=embed, view=self)
        except Exception as ex:
            self.log.exception(ex)

    async def on_cancel(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.stop()
