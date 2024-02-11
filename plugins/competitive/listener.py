import trueskill

from core import EventListener, event, Server, Status, Player, chat_command, Plugin, Side
from dataclasses import dataclass, field
from datetime import datetime, timezone
from discord.ext import tasks
from plugins.competitive import rating
from trueskill import Rating
from typing import Optional, Tuple


@dataclass
class Match:
    match_id: str
    alive: dict[Side, list[Player]] = field(default_factory=dict)
    dead: dict[Side, list[Player]] = field(default_factory=dict)
    log: list[Tuple[datetime, str]] = field(default_factory=list)
    started: datetime = field(init=False)

    @property
    def teams(self) -> dict[Side, list[Player]]:
        return {
            Side.BLUE: [p for p in self.alive[Side.BLUE]] + [p for p in self.dead[Side.BLUE]],
            Side.RED: [p for p in self.alive[Side.RED]] + [p for p in self.dead[Side.RED]]
        }

    def player_join(self, player: Player):
        if player.side.name not in self.alive:
            self.alive[player.side] = []
        self.alive[player.side].append(player)

    def player_dead(self, player: Player):
        if player in self.alive[player.side]:
            self.alive[player.side].remove(player)
            if player.side.name not in self.dead:
                self.dead[player.side] = []
            self.dead[player.side].append(player)

    def is_over(self) -> Optional[Side]:
        if not len(self.alive[Side.RED]):
            return Side.BLUE
        elif not len(self.alive[Side.BLUE]):
            return Side.RED
        return None

    def is_on(self) -> bool:
        return len(self.alive[Side.BLUE]) > 0 and len(self.alive[Side.RED]) > 0


class CompetitiveListener(EventListener):

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.matches: dict[str, dict[str, Match]] = dict()
        self.in_match: dict[str, dict[str, Match]] = dict()

    @staticmethod
    async def inform_players(match: Match, message: str, time: Optional[int] = 10):
        all_players = match.teams
        for player in ([p for p in all_players[Side.BLUE]] + [p for p in all_players[Side.RED]]):
            await player.sendPopupMessage(message, timeout=time)

    def rank_teams(self, winners: list[Player], losers: list[Player]):
        r_winners, r_losers = trueskill.rate([
            [self.get_rating(p) for p in winners],
            [self.get_rating(p) for p in losers]
        ], [0, 1])
        for idx, p in enumerate(winners):
            self.set_rating(p, r_winners[idx])
        for idx, p in enumerate(losers):
            self.set_rating(p, r_losers[idx])

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        if server.name not in self.in_match:
            self.in_match[server.name] = dict()
        if server.name not in self.matches:
            self.matches[server.name] = dict()

    @event(name="onSimulationStart")
    async def onSimulationStart(self, server: Server, data: dict) -> None:
        self.matches[server.name] = dict()
        self.in_match[server.name] = dict()

    @event(name="onSimulationStop")
    async def onSimulationStart(self, server: Server, data: dict) -> None:
        self.matches[server.name] = dict()
        self.in_match[server.name] = dict()

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1:
            return
        player: Player = server.get_player(id=data['id'])
        player.sendChatMessage(f"Your current TrueSkill rating is: {self.calculate_rating(self.get_rating(player))}.")

    @event(name="addPlayerToMatch")
    async def addPlayerToMatch(self, server: Server, data: dict) -> None:
        players = server.get_crew_members(server.get_player(name=data['player_name']))
        for player in players:
            match = self.in_match[server.name].get(player.ucid)
            # don't re-add the player to a match
            if match:
                return
            match_id = data['match_id']
            if match_id not in self.matches[server.name]:
                match = Match(match_id=match_id)
                self.matches[server.name][match_id] = match
            else:
                match = self.matches[server.name][match_id]
                # check that we were not in the same match but died
                if player in match.dead[Side.BLUE] or player in match.dead[Side.RED]:
                    server.move_to_spectators(player, reason="You're not allowed to re-join the same match!")
                    return
            is_on = match.is_on()
            match.player_join(player)
            self.in_match[server.name][player.ucid] = match
            await self.inform_players(match,
                                      f"Player {player.name} ({self.calculate_rating(self.get_rating(player))}) "
                                      f"joined the {player.side.name} team!")
            # inform the players if the match is on now
            if not is_on and match.is_on():
                match.started = datetime.now(timezone.utc)
                await self.inform_players(match, "The match is on! If you die, crash or leave now, you lose!")

    def get_rating(self, player: Player) -> Rating:
        with self.pool.connection() as conn:
            row = conn.execute("""
                SELECT skill_mu, skill_sigma 
                FROM trueskill 
                WHERE player_ucid = %s
            """, (player.ucid, )).fetchone()
            if not row:
                self.set_rating(player, rating.create_rating())
                return self.get_rating(player)
            else:
                return Rating(float(row[0]), float(row[1]))

    def set_rating(self, player: Player, skill: Rating) -> None:
        with self.pool.connection() as conn:
            conn.execute("""
                INSERT INTO trueskill (player_ucid, skill_mu, skill_sigma) 
                VALUES (%s, %s, %s)
                ON CONFLICT (player_ucid) DO UPDATE
                SET skill_mu = excluded.skill_mu, skill_sigma = excluded.skill_sigma
            """, (player.ucid, skill.mu, skill.sigma))

    @staticmethod
    def calculate_rating(r: Rating) -> float:
        return round(r.mu - 3.0 * r.sigma, 2)

    async def _onGameEvent(self, server: Server, data: dict) -> None:
        def print_crew(players: list[Player]) -> str:
            return ' and '.join([p.name for p in players])

        now = datetime.now(timezone.utc)
        if data['eventName'] == 'kill':
            # human players only
            if data['arg1'] == -1 or data['arg4'] == -1:
                return
            # self-kill
            if data['arg1'] == data['arg4']:
                data['eventName'] = 'self_kill'
                await self._onGameEvent(server, data)
            # Multi-crew - pilot and all crew members gain points
            killers = server.get_crew_members(server.get_player(id=data['arg1']))
            victims = server.get_crew_members(server.get_player(id=data['arg4']))
            if not killers or not victims:
                return
            # check if we are in a registered match
            match: Match = self.in_match[server.name].get(killers[0].ucid)
            if match:
                for player in victims:
                    match.log.append((now, "{} in {} {} {} in {} with {}".format(
                        print_crew(killers), data['arg2'],
                        'killed' if data['arg3'] != data['arg4'] else 'team-killed',
                        print_crew(victims), data['arg5'],
                        data['arg7'] or 'Guns')))
                    match.player_dead(player)
                    del self.in_match[server.name][player.ucid]
            # no, then we don't count team-kills
            elif data['arg3'] != data['arg6']:
                self.rank_teams(killers, victims)
                for player in killers:
                    player.sendChatMessage("You won against {}! Your new rating is {}".format(
                        print_crew(victims), self.calculate_rating(self.get_rating(player))))
                for player in victims:
                    player.sendChatMessage("You lost against {}! Your new rating is {}".format(
                        print_crew(killers), self.calculate_rating(self.get_rating(player))))
        elif data['eventName'] in ['self_kill', 'crash']:
            players = server.get_crew_members(server.get_player(id=data['arg1']))
            if not players:
                # we should never get here
                return
            match: Match = self.in_match[server.name].get(players[0].ucid)
            if match:
                match.log.append((now, "{} in {} died ({})".format(
                    print_crew(players), data['arg2'], data['eventName'])))
                for player in players:
                    match.player_dead(player)
                    del self.in_match[server.name][player.ucid]
        elif data['eventName'] in ['eject', 'disconnect', 'change_slot']:
            player = server.get_player(id=data['arg1'])
            if not player:
                # we should never get here
                return
            # if the pilot of a MC aircraft leaves, both pilots get booted
            if player.slot == 0:
                players = server.get_crew_members(server.get_player(id=data['arg1']))
            else:
                players = [player]
            match: Match = self.in_match[server.name].get(player.ucid)
            if match:
                match.log.append((now, "{} in {} died ({})".format(
                    print_crew(players), data['arg2'], data['eventName'])))
                for player in players:
                    match.player_dead(player)
                    del self.in_match[server.name][player.ucid]

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        await self._onGameEvent(server, data)

    @chat_command(name="skill", help="Show your TrueSkill")
    async def skill(self, server: Server, player: Player, params: list[str]):
        player.sendChatMessage(f"Your TrueSkill rating is {self.calculate_rating(self.get_rating(player))}")

    @tasks.loop(seconds=5)
    async def check_matches(self):
        for server in self.bot.servers.values():
            if server.status != Status.RUNNING:
                continue
            finished: list[Match] = []
            for match in self.matches[server.name].values():
                winner = match.is_over()
                if winner:
                    self.rank_teams(match.teams[winner], match.teams[Side.BLUE if winner == Side.RED else Side.RED])
                    message = f"The match is over, {winner.name} won!\n\nThe following players are still alive:\n"
                    for player in match.alive[winner]:
                        message += f"- {player.name}\n"
                    message += "\nThis is the log of your last match:\n"
                    for time, log in match.log:
                        message += f"{time:%H:%M:%S}: {log}\n"
                    message += "\nYour new rating is as follows:\n"
                    for player in match.teams[Side.BLUE] + match.teams[Side.RED]:
                        message += f"- {player.name}: {self.calculate_rating(self.get_rating(player))}\n"
                    await self.inform_players(match, message, 30)
                    finished.append(match)
            for match in finished:
                del self.matches[server.name][match.match_id]
