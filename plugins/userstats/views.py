import discord

from core import utils
from discord.ui import Modal, TextInput
from psycopg.errors import UniqueViolation
from typing import Optional


class SquadronModal(Modal):
    description = TextInput(label="Enter a description for this squadron:", style=discord.TextStyle.long, required=True)
    image_url = TextInput(label="Squadron Image (URL):", style=discord.TextStyle.short, required=False)

    def __init__(self, name: str, role: Optional[discord.Role] = None, description: Optional[str] = None,
                 image_url: Optional[str] = None):
        super().__init__(title=f"Description for Squadron {name}")
        self.name = name
        self.role = role
        self.description.default = description
        self.image_url.default = image_url

    async def on_submit(self, interaction: discord.Interaction):
        ephemeral = utils.get_ephemeral(interaction)
        async with interaction.client.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO squadrons (name, description, role, image_url) 
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (name) DO UPDATE
                    SET description = excluded.description, role = excluded.role, image_url = excluded.image_url
                """, (self.name, self.description.value, self.role.id if self.role else None, self.image_url.value))
                if self.role:
                    cursor = await conn.execute("SELECT id FROM squadrons WHERE name = %s", (self.name,))
                    # might be a role change, so wipe the squadron first
                    squadron_id = (await cursor.fetchone())[0]
                    await conn.execute("""
                        DELETE FROM squadron_members WHERE squadron_id = %s
                    """, (squadron_id, ))
                    for member in self.role.members:
                        ucid = await interaction.client.get_ucid_by_member(member, verified=True)
                        if ucid:
                            await conn.execute("""
                                INSERT INTO squadron_members VALUES (%s, %s)
                                ON CONFLICT (squadron_id, player_ucid) DO NOTHING
                            """, (squadron_id, ucid))
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(f"Squadron {self.name} created/updated.", ephemeral=ephemeral)

    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
        if isinstance(error, UniqueViolation):
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"Squadron {self.name} exists already. Please chose another name.",
                                                    ephemeral=True)
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"Error while creating squadron: {error}", ephemeral=True)
