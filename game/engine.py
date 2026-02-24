"""Pure game logic: levelling, items, combat, events, quests, penalties."""
from __future__ import annotations
import logging
import math
import random
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from db.models import Player, ITEM_SLOTS, hash_password
from db.store import PlayerDB

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def duration(secs: int) -> str:
    """Format seconds as 'X day(s), HH:MM:SS'."""
    secs = max(0, int(secs))
    days = secs // 86400
    h = (secs % 86400) // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    return f"{days} day{'s' if days != 1 else ''}, {h:02d}:{m:02d}:{s:02d}"


def _load_events(path: str) -> Tuple[List[str], List[str], List[Tuple]]:
    calamities: List[str] = []
    godsends: List[str] = []
    quests: List[Tuple] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if line.startswith("C "):
                    calamities.append(line[2:])
                elif line.startswith("G "):
                    godsends.append(line[2:])
                elif line.startswith("Q1 "):
                    quests.append(("Q1", line[3:]))
                elif line.startswith("Q2 "):
                    parts = line[3:].split(" ", 4)
                    if len(parts) == 5:
                        quests.append(("Q2", int(parts[0]), int(parts[1]),
                                       int(parts[2]), int(parts[3]), parts[4]))
    except OSError:
        log.warning("Could not open events file: %s", path)
    return calamities, godsends, quests


# ---------------------------------------------------------------------------
# Quest state
# ---------------------------------------------------------------------------

class Quest:
    def __init__(self):
        self.questers: List[str] = []
        self.type: int = 1
        self.stage: int = 1
        self.text: str = ""
        self.p1: List[int] = [0, 0]
        self.p2: List[int] = [0, 0]
        self.qtime: int = int(time.time()) + random.randint(0, 3600)

    def is_active(self) -> bool:
        return bool(self.questers)

    def clear(self):
        self.questers = []
        self.type = 1
        self.stage = 1
        self.text = ""


# ---------------------------------------------------------------------------
# GameEngine
# ---------------------------------------------------------------------------

class GameEngine:
    """Stateful game engine. Holds shared state; driven by the bot's tick loop."""

    def __init__(self, db: PlayerDB, cfg, announce: Callable[[str], None]):
        """
        :param db: PlayerDB instance
        :param cfg: BotConfig
        :param announce: callable to send a message to the channel
        """
        self.db = db
        self.cfg = cfg
        self._announce = announce       # sync callable – bot wraps in asyncio

        self.quest = Quest()
        self.rpreport: int = 0          # tick counter (seconds accumulated)
        self.pause_mode: bool = False
        self.silent_mode: int = cfg.silent_mode
        self.last_tick: int = 1         # 1 = not yet connected

    # ------------------------------------------------------------------
    # Announce / notice wrappers
    # ------------------------------------------------------------------

    def _msg(self, text: str):
        if self.silent_mode & 1:
            return
        self._announce(text)

    def _clog(self, text: str) -> str:
        """Log to modsfile and return text (mirrors Perl clog())."""
        try:
            with open(self.cfg.mods_file, "a", encoding="utf-8") as f:
                ts = time.strftime("[%m/%d/%y %H:%M:%S] ")
                f.write(f"{ts}{text}\n")
        except OSError as e:
            log.warning("clog failed: %s", e)
        return text

    # ------------------------------------------------------------------
    # TTL helpers
    # ------------------------------------------------------------------

    def _level_ttl(self, level: int) -> int:
        if level > 60:
            return int(self.cfg.rpbase * (self.cfg.rpstep ** 60) +
                       86400 * (level - 60))
        return int(self.cfg.rpbase * (self.cfg.rpstep ** level))

    # ------------------------------------------------------------------
    # Item finding
    # ------------------------------------------------------------------

    def find_item(self, username: str):
        p = self.db.get(username)
        if not p:
            return
        slot = random.choice(ITEM_SLOTS)
        # roll item level
        level = 1
        for num in range(1, int(p.level * 1.5) + 1):
            if random.random() < 1 / (1.4 ** (num / 4)):
                level = num

        # Unique item chances
        unique_map = [
            (25, 40, 50, 25, "helm", "a",
             "Mattt's Omniscience Grand Crown",
             "Your enemies fall before you as you anticipate their every move."),
            (25, 40, 50, 25, "ring", "h",
             "Juliet's Glorious Ring of Sparkliness",
             "Your enemies are blinded by both its glory and their greed."),
            (30, 40, 75, 25, "tunic", "b",
             "Res0's Protectorate Plate Mail",
             "Your enemies cower in fear as their attacks have no effect on you."),
            (35, 40, 100, 25, "amulet", "c",
             "Dwyn's Storm Magic Amulet",
             "Your enemies are swept away by an elemental fury."),
            (40, 40, 150, 25, "weapon", "d",
             "Jotun's Fury Colossal Sword",
             "Your enemies' hatred is brought to a quick end."),
            (45, 40, 175, 26, "weapon", "e",
             "Drdink's Cane of Blind Rage",
             "Your enemies are tossed aside as you blindly swing around."),
            (48, 40, 250, 51, "pair of boots", "f",
             "Mrquick's Magical Boots of Swiftness",
             "Your enemies are left choking on your dust."),
            (52, 40, 300, 51, "weapon", "g",
             "Jeff's Cluehammer of Doom",
             "Your enemies are left with a sudden and intense clarity of mind."),
        ]
        for min_lvl, chance, base_lvl, variance, islot, suffix, name, flavour in unique_map:
            if p.level >= min_lvl and random.randint(0, chance - 1) < 1:
                ulevel = base_lvl + random.randint(0, variance - 1)
                cur = int(p.items[islot])
                if ulevel >= level and ulevel > cur:
                    self._msg(self._clog(
                        f"*** The light of the gods shines upon {p.username}! They have found "
                        f"the level {ulevel} {name}! {flavour}"))
                    p.items[islot] = f"{ulevel}{suffix}"
                    return
                break  # tried this tier, stop

        cur = int(p.items[slot])
        if level > cur:
            self._msg(
                f"*** {p.username} found a level {level} {slot}! "
                f"Their current {slot} is only level {cur}, so it seems Luck is with them!")
            p.items[slot] = str(level)
        else:
            self._msg(
                f"*** {p.username} found a level {level} {slot}. "
                f"Their current {slot} is level {cur}, so it seems Luck is against them. "
                f"They toss the {slot}.")

    # ------------------------------------------------------------------
    # Combat
    # ------------------------------------------------------------------

    def _bot_item_sum(self) -> int:
        """The bot (primnick) is a pseudo-opponent with the highest item sum + 1."""
        if not self.db.players:
            return 1
        return max(p.item_sum() for p in self.db.players.values()) + 1

    def _roll_combat(self, username: str, opp_name: str):
        """Generic 1v1 challenge. opp_name may be the bot nick."""
        p = self.db.get(username)
        is_bot_opp = opp_name not in self.db.players

        mysum = p.item_sum(battle=True)
        oppsum = self._bot_item_sum() if is_bot_opp else self.db.get(opp_name).item_sum(battle=True)
        myroll = random.randint(0, max(mysum - 1, 0))
        opproll = random.randint(0, max(oppsum - 1, 0))

        if myroll >= opproll:
            gain_pct = 20 if is_bot_opp else max(7, self.db.get(opp_name).level // 4)
            gain = int((gain_pct / 100) * p.next_ttl)
            self._msg(self._clog(
                f"{username} [{myroll}/{mysum}] has challenged {opp_name} "
                f"[{opproll}/{oppsum}] in combat and won! {duration(gain)} is removed from {username}'s clock."))
            p.next_ttl -= gain
            self._msg(f"{username} reaches next level in {duration(p.next_ttl)}.")

            # critical strike
            cs_factor = 50 if p.alignment == "g" else (20 if p.alignment == "e" else 35)
            if not is_bot_opp and random.randint(0, cs_factor - 1) < 1:
                opp = self.db.get(opp_name)
                cs = int(((5 + random.randint(0, 19)) / 100) * opp.next_ttl)
                self._msg(self._clog(
                    f"{username} has dealt {opp_name} a Critical Strike! {duration(cs)} is added to {opp_name}'s clock."))
                opp.next_ttl += cs
                self._msg(f"{opp_name} reaches next level in {duration(opp.next_ttl)}.")
            # item steal
            elif not is_bot_opp and random.randint(0, 24) < 1 and p.level > 19:
                self._try_steal_item(username, opp_name)
        else:
            gain_pct = 10 if is_bot_opp else max(7, self.db.get(opp_name).level // 7)
            gain = int((gain_pct / 100) * p.next_ttl)
            self._msg(self._clog(
                f"{username} [{myroll}/{mysum}] has challenged {opp_name} "
                f"[{opproll}/{oppsum}] in combat and lost! {duration(gain)} is added to {username}'s clock."))
            p.next_ttl += gain
            self._msg(f"{username} reaches next level in {duration(p.next_ttl)}.")

    def _try_steal_item(self, winner: str, loser: str):
        pw = self.db.get(winner)
        pl = self.db.get(loser)
        slot = random.choice(ITEM_SLOTS)
        if int(pl.items[slot]) > int(pw.items[slot]):
            self._msg(self._clog(
                f"In the fierce battle, {loser} dropped his level {int(pl.items[slot])} {slot}! "
                f"{winner} picks it up, tossing his old level {int(pw.items[slot])} {slot} to {loser}."))
            pw.items[slot], pl.items[slot] = pl.items[slot], pw.items[slot]

    def challenge_opp(self, username: str, primnick: str):
        p = self.db.get(username)
        if not p:
            return
        if p.level < 25 and random.randint(0, 3) != 0:
            return
        online = [u for u in self.db.players if self.db.players[u].online and u != username]
        if not online:
            return
        opp = random.choice(online)
        # small chance the opponent is the bot itself
        if random.randint(0, len(online)) == 0:
            opp = primnick
        self._roll_combat(username, opp)

    def collision_fight(self, u: str, opp: str):
        """Fight triggered by map collision."""
        pu = self.db.get(u)
        po = self.db.get(opp)
        mysum = pu.item_sum(battle=True)
        oppsum = po.item_sum(battle=True)
        myroll = random.randint(0, max(mysum - 1, 0))
        opproll = random.randint(0, max(oppsum - 1, 0))
        if myroll >= opproll:
            gain = max(7, po.level // 4)
            gain = int((gain / 100) * pu.next_ttl)
            self._msg(self._clog(
                f"{u} [{myroll}/{mysum}] has come upon {opp} [{opproll}/{oppsum}] "
                f"and taken them in combat! {duration(gain)} is removed from {u}'s clock."))
            pu.next_ttl -= gain
            self._msg(f"{u} reaches next level in {duration(pu.next_ttl)}.")
            if random.randint(0, 34) < 1:
                cs = int(((5 + random.randint(0, 19)) / 100) * po.next_ttl)
                self._msg(self._clog(f"{u} has dealt {opp} a Critical Strike! {duration(cs)} is added to {opp}'s clock."))
                po.next_ttl += cs
                self._msg(f"{opp} reaches next level in {duration(po.next_ttl)}.")
            elif random.randint(0, 24) < 1 and pu.level > 19:
                self._try_steal_item(u, opp)
        else:
            gain = max(7, po.level // 7)
            gain = int((gain / 100) * pu.next_ttl)
            self._msg(self._clog(
                f"{u} [{myroll}/{mysum}] has come upon {opp} [{opproll}/{oppsum}] "
                f"and been defeated in combat! {duration(gain)} is added to {u}'s clock."))
            pu.next_ttl += gain
            self._msg(f"{u} reaches next level in {duration(pu.next_ttl)}.")

    def team_battle(self):
        online = [u for u in self.db.players if self.db.players[u].online]
        if len(online) < 6:
            return
        random.shuffle(online)
        picks = online[:6]
        team1, team2 = picks[:3], picks[3:]
        s1 = sum(self.db.get(u).item_sum(battle=True) for u in team1)
        s2 = sum(self.db.get(u).item_sum(battle=True) for u in team2)
        gain = int(min(self.db.get(u).next_ttl for u in team1) * 0.20)
        r1 = random.randint(0, max(s1 - 1, 0))
        r2 = random.randint(0, max(s2 - 1, 0))
        names1 = ", ".join(team1)
        names2 = ", ".join(team2)
        if r1 >= r2:
            self._msg(self._clog(
                f"{names1} [{r1}/{s1}] have team battled {names2} [{r2}/{s2}] and won! "
                f"{duration(gain)} is removed from their clocks."))
            for u in team1:
                self.db.get(u).next_ttl -= gain
        else:
            self._msg(self._clog(
                f"{names1} [{r1}/{s1}] have team battled {names2} [{r2}/{s2}] and lost! "
                f"{duration(gain)} is added to their clocks."))
            for u in team1:
                self.db.get(u).next_ttl += gain

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def hog(self):
        """Hand of God."""
        online = self.db.online_players()
        if not online:
            return
        p = random.choice(online)
        win = random.randint(0, 4) > 0
        t = int(((5 + random.randint(0, 70)) / 100) * p.next_ttl)
        if win:
            self._msg(self._clog(
                f"Verily I say unto thee, the Heavens have burst forth, and the blessed hand of God "
                f"carried {p.username} {duration(t)} toward level {p.level + 1}."))
            p.next_ttl -= t
        else:
            self._msg(self._clog(
                f"Thereupon He stretched out His little finger among them and consumed {p.username} "
                f"with fire, slowing the heathen {duration(t)} from level {p.level + 1}."))
            p.next_ttl += t
        self._msg(f"{p.username} reaches next level in {duration(p.next_ttl)}.")

    def calamity(self):
        online = self.db.online_players()
        if not online:
            return
        p = random.choice(online)
        if random.randint(0, 9) < 1:
            # item degradation
            slot = random.choice(["amulet", "charm", "weapon", "tunic", "set of leggings", "shield"])
            flavours = {
                "amulet": f"{p.username} fell, chipping the stone in his amulet!",
                "charm": f"{p.username} slipped and dropped his charm in a dirty bog!",
                "weapon": f"{p.username} left his weapon out in the rain to rust!",
                "tunic": f"{p.username} spilled a level 7 shrinking potion on his tunic!",
                "shield": f"{p.username}'s shield was damaged by a dragon's fiery breath!",
                "set of leggings": f"{p.username} burned a hole through his leggings while ironing them!",
            }
            self._msg(self._clog(f"{flavours[slot]} {p.username}'s {slot} loses 10% of its effectiveness."))
            cur = p.items[slot]
            suffix = "".join(c for c in cur if not c.isdigit())
            p.items[slot] = str(int(int(cur.rstrip(suffix or "x") or "0") * 0.9)) + suffix
        else:
            calamities, _, _ = _load_events(self.cfg.events_file)
            if not calamities:
                return
            action = random.choice(calamities)
            t = int((5 + random.randint(0, 7)) / 100 * p.next_ttl)
            self._msg(self._clog(
                f"{p.username} {action}. This terrible calamity has slowed them "
                f"{duration(t)} from level {p.level + 1}."))
            p.next_ttl += t
            self._msg(f"{p.username} reaches next level in {duration(p.next_ttl)}.")

    def godsend(self):
        online = self.db.online_players()
        if not online:
            return
        p = random.choice(online)
        if random.randint(0, 9) < 1:
            slot = random.choice(["amulet", "charm", "weapon", "tunic", "set of leggings", "shield"])
            flavours = {
                "amulet": f"{p.username}'s amulet was blessed by a passing cleric!",
                "charm": f"{p.username}'s charm ate a bolt of lightning!",
                "weapon": f"{p.username} sharpened the edge of his weapon!",
                "tunic": f"A magician cast a spell of Rigidity on {p.username}'s tunic!",
                "shield": f"{p.username} reinforced his shield with a dragon's scales!",
                "set of leggings": f"The local wizard imbued {p.username}'s pants with a Spirit of Fortitude!",
            }
            self._msg(self._clog(f"{flavours[slot]} {p.username}'s {slot} gains 10% effectiveness."))
            cur = p.items[slot]
            suffix = "".join(c for c in cur if not c.isdigit())
            base_val = int(cur.rstrip(suffix or "x") or "0")
            p.items[slot] = str(int(base_val * 1.1)) + suffix
        else:
            _, godsends, _ = _load_events(self.cfg.events_file)
            if not godsends:
                return
            action = random.choice(godsends)
            t = int((5 + random.randint(0, 7)) / 100 * p.next_ttl)
            self._msg(self._clog(
                f"{p.username} {action}! This wondrous godsend has accelerated them "
                f"{duration(t)} towards level {p.level + 1}."))
            p.next_ttl -= t
            self._msg(f"{p.username} reaches next level in {duration(p.next_ttl)}.")

    def goodness(self):
        good = [p for p in self.db.online_players() if p.alignment == "g"]
        if len(good) < 2:
            return
        chosen = random.sample(good, 2)
        gain = 5 + random.randint(0, 7)
        self._msg(self._clog(
            f"{chosen[0].username} and {chosen[1].username} have not let the iniquities of evil men "
            f"poison them. Together have they prayed to their god, and it is his light that now "
            f"shines upon them. {gain}% of their time is removed from their clocks."))
        for p in chosen:
            p.next_ttl = int(p.next_ttl * (1 - gain / 100))
            self._msg(f"{p.username} reaches next level in {duration(p.next_ttl)}.")

    def evilness(self):
        evil = [p for p in self.db.online_players() if p.alignment == "e"]
        if not evil:
            return
        me = random.choice(evil)
        if random.randint(0, 1) == 0:
            good = [p for p in self.db.online_players() if p.alignment == "g"]
            if not good:
                return
            target = random.choice(good)
            slot = random.choice(ITEM_SLOTS)
            if int(target.items[slot]) > int(me.items[slot]):
                self._msg(self._clog(
                    f"{me.username} stole {target.username}'s level {int(target.items[slot])} {slot} "
                    f"while they were sleeping! {me.username} leaves his old level "
                    f"{int(me.items[slot])} {slot} behind, which {target.username} then takes."))
                me.items[slot], target.items[slot] = target.items[slot], me.items[slot]
            else:
                self._msg(
                    f"*** {me.username} made to steal {target.username}'s {slot}, "
                    f"but realized it was lower level than their own. They creep back into the shadows.")
        else:
            gain = 1 + random.randint(0, 4)
            added = int(me.next_ttl * (gain / 100))
            self._msg(self._clog(
                f"{me.username} is forsaken by his evil god. {duration(added)} is added to his clock."))
            me.next_ttl = int(me.next_ttl * (1 + gain / 100))
            self._msg(f"{me.username} reaches next level in {duration(me.next_ttl)}.")

    # ------------------------------------------------------------------
    # Quests
    # ------------------------------------------------------------------

    def start_quest(self):
        eligible = [
            p for p in self.db.online_players()
            if p.level > 39 and (time.time() - p.last_login) > 36000
        ]
        if len(eligible) < 4:
            self.quest.clear()
            return
        questers = random.sample(eligible, 4)
        self.quest.questers = [p.username for p in questers]

        _, _, quest_events = _load_events(self.cfg.events_file)
        if not quest_events:
            self.quest.clear()
            return
        chosen = random.choice(quest_events)

        names = ", ".join(self.quest.questers[:3]) + f", and {self.quest.questers[3]}"
        if chosen[0] == "Q1":
            self.quest.type = 1
            self.quest.text = chosen[1]
            self.quest.qtime = int(time.time()) + 43200 + random.randint(0, 43200)
            self._msg(
                f"{names} have been chosen by the gods to {self.quest.text}. "
                f"Quest to end in {duration(self.quest.qtime - int(time.time()))}.")
        elif chosen[0] == "Q2":
            self.quest.type = 2
            self.quest.stage = 1
            self.quest.p1 = [chosen[1], chosen[2]]
            self.quest.p2 = [chosen[3], chosen[4]]
            self.quest.text = chosen[5]
            map_note = (f" See {self.cfg.map_url} to monitor their journey's progress."
                        if self.cfg.map_url else "")
            self._msg(
                f"{names} have been chosen by the gods to {self.quest.text}. "
                f"Participants must first reach [{self.quest.p1[0]},{self.quest.p1[1]}], "
                f"then [{self.quest.p2[0]},{self.quest.p2[1]}].{map_note}")
        self.write_quest_file()

    def quest_penalty(self, username: str):
        if username in self.quest.questers:
            self._msg(self._clog(
                f"{username}'s prudence and self-regard has brought the wrath of the gods upon the realm. "
                f"Hell rains down upon you as you beg for the sweet release of death."))
            for p in self.db.online_players():
                gain = int(15 * (self.cfg.rppenstep ** p.level))
                p.pen_quest += gain
                p.next_ttl += gain
            self.quest.clear()
            self.quest.qtime = int(time.time()) + 43200

    def write_quest_file(self):
        if not self.cfg.quest_file:
            return
        try:
            with open(self.cfg.quest_file, "w", encoding="utf-8") as f:
                if not self.quest.is_active():
                    return
                qs = self.quest
                f.write(f"T {qs.text}\n")
                if qs.type == 1:
                    f.write(f"Y 1\nS {qs.qtime}\n")
                else:
                    f.write(f"Y 2\nS {qs.stage}\n"
                            f"P {qs.p1[0]} {qs.p1[1]} {qs.p2[0]} {qs.p2[1]}\n")
                for i, uname in enumerate(qs.questers, 1):
                    p = self.db.get(uname)
                    if p and qs.type == 2:
                        f.write(f"P{i} {uname} {p.x} {p.y}\n")
                    else:
                        f.write(f"P{i} {uname}\n")
        except OSError as e:
            log.warning("write_quest_file: %s", e)

    # ------------------------------------------------------------------
    # Player movement
    # ------------------------------------------------------------------

    def move_players(self, onchan: dict):
        if self.last_tick <= 1:
            return
        online_count = len(self.db.online_players())
        if not online_count:
            return
        q = self.quest

        for _ in range(self.cfg.self_clock):
            positions: Dict[Tuple[int, int], dict] = {}

            if q.type == 2 and q.questers:
                # move non-questers randomly
                non_questers = [p for p in self.db.online_players()
                                if p.username not in q.questers]
                self._move_group(non_questers, positions, online_count, onchan)

                # check if all questers reached waypoint
                target = q.p1 if q.stage == 1 else q.p2
                all_there = all(
                    self.db.get(u) and
                    self.db.get(u).x == target[0] and
                    self.db.get(u).y == target[1]
                    for u in q.questers
                )
                if all_there and q.stage == 1:
                    q.stage = 2
                elif all_there and q.stage == 2:
                    names = ", ".join(q.questers[:3]) + f", and {q.questers[3]}"
                    self._msg(self._clog(
                        f"{names} have completed their journey! 25% of their burden is eliminated."))
                    for uname in q.questers:
                        p = self.db.get(uname)
                        if p:
                            p.next_ttl = int(p.next_ttl * 0.75)
                    q.clear()
                    q.qtime = int(time.time()) + 3600
                    q.type = 1
                    self.write_quest_file()
                else:
                    # move questers toward target
                    for uname in q.questers:
                        p = self.db.get(uname)
                        if not p:
                            continue
                        if random.randint(0, 99) < 1:
                            tx, ty = (q.p1 if q.stage == 1 else q.p2)
                            if p.x != tx:
                                p.x += 1 if p.x < tx else -1
                            if p.y != ty:
                                p.y += 1 if p.y < ty else -1
            else:
                self._move_group(self.db.online_players(), positions, online_count, onchan)

    def _move_group(self, players, positions, online_count, onchan):
        for p in players:
            if not (p.online and p.nick and p.nick in onchan):
                continue
            p.x = (p.x + random.randint(-1, 1)) % (self.cfg.mapx + 1)
            p.y = (p.y + random.randint(-1, 1)) % (self.cfg.mapy + 1)
            coord = (p.x, p.y)
            if coord in positions and not positions[coord]["battled"]:
                other_name = positions[coord]["user"]
                other = self.db.get(other_name)
                if other and other.is_admin and not p.is_admin and random.randint(0, 99) < 1:
                    self._msg(f"{p.username} encounters {other_name} and bows humbly.")
                if random.randint(0, online_count - 1) < 1:
                    positions[coord]["battled"] = True
                    self.collision_fight(p.username, other_name)
            else:
                positions[coord] = {"battled": False, "user": p.username}

    # ------------------------------------------------------------------
    # Penalty
    # ------------------------------------------------------------------

    def penalize(self, username: str, ptype: str, extra=None, primnick: str = "") -> bool:
        p = self.db.get(username)
        if not p:
            return False
        self.quest_penalty(username)

        cfg = self.cfg
        pen = 0
        if ptype == "quit":
            pen = int(20 * (cfg.rppenstep ** p.level))
            if cfg.limit_pen and pen > cfg.limit_pen:
                pen = cfg.limit_pen
            p.pen_quit += pen
            p.online = False
        elif ptype == "nick":
            pen = int(30 * (cfg.rppenstep ** p.level))
            if cfg.limit_pen and pen > cfg.limit_pen:
                pen = cfg.limit_pen
            p.pen_nick += pen
            new_nick = str(extra).lstrip(":")
            p.nick = new_nick
            self._msg(f"*** {username}: Penalty of {duration(pen)} added to their timer for nick change.")
        elif ptype in ("privmsg", "notice"):
            pen = int((extra or 0) * (cfg.rppenstep ** p.level))
            if cfg.limit_pen and pen > cfg.limit_pen:
                pen = cfg.limit_pen
            p.pen_mesg += pen
            self._msg(f"*** {username}: Penalty of {duration(pen)} added to their timer for {ptype}.")
        elif ptype == "part":
            pen = int(200 * (cfg.rppenstep ** p.level))
            if cfg.limit_pen and pen > cfg.limit_pen:
                pen = cfg.limit_pen
            p.pen_part += pen
            self._msg(f"*** {username}: Penalty of {duration(pen)} added to their timer for parting.")
            p.online = False
        elif ptype == "kick":
            pen = int(250 * (cfg.rppenstep ** p.level))
            if cfg.limit_pen and pen > cfg.limit_pen:
                pen = cfg.limit_pen
            p.pen_kick += pen
            self._msg(f"*** {username}: Penalty of {duration(pen)} added to their timer for being kicked.")
            p.online = False
        elif ptype == "logout":
            pen = int(20 * (cfg.rppenstep ** p.level))
            if cfg.limit_pen and pen > cfg.limit_pen:
                pen = cfg.limit_pen
            p.pen_logout += pen
            self._msg(f"*** {username}: Penalty of {duration(pen)} added to their timer for logout.")
            p.online = False

        p.next_ttl += pen
        return True

    # ------------------------------------------------------------------
    # Main tick (called every self_clock seconds)
    # ------------------------------------------------------------------

    def tick(self, onchan: dict, primnick: str, bot_nick: str):
        """Advance game by one self_clock tick. onchan = {nick: join_time}."""
        online_list = self.db.online_players()
        online = len(online_list)
        if not online:
            return

        clock = self.cfg.self_clock
        if not self.cfg.noscale:
            if random.random() < online / (20 * 86400 / clock):
                self.hog()
            if random.random() < online / (24 * 86400 / clock):
                self.team_battle()
            if random.random() < online / (8 * 86400 / clock):
                self.calamity()
            if random.random() < online / (4 * 86400 / clock):
                self.godsend()
        else:
            if random.randint(0, 3999) < 1:
                self.hog()
            if random.randint(0, 3999) < 1:
                self.team_battle()
            if random.randint(0, 3999) < 1:
                self.calamity()
            if random.randint(0, 1999) < 1:
                self.godsend()

        evil_count = sum(1 for p in online_list if p.alignment == "e")
        good_count = sum(1 for p in online_list if p.alignment == "g")
        if random.random() < evil_count / (8 * 86400 / clock):
            self.evilness()
        if random.random() < good_count / (12 * 86400 / clock):
            self.goodness()

        self.move_players(onchan)

        if self.rpreport % 120 == 0:
            self.write_quest_file()

        now = int(time.time())
        if now > self.quest.qtime:
            if not self.quest.is_active():
                self.start_quest()
            elif self.quest.type == 1:
                names = ", ".join(self.quest.questers[:3]) + f", and {self.quest.questers[3]}"
                self._msg(self._clog(
                    f"{names} have blessed the realm by completing their quest! "
                    f"25% of their burden is eliminated."))
                for uname in self.quest.questers:
                    p = self.db.get(uname)
                    if p:
                        p.next_ttl = int(p.next_ttl * 0.75)
                self.quest.clear()
                self.quest.qtime = now + 21600

        # Challenge every 20 min when 15%+ are level 45+
        if self.rpreport % 1200 == 0 and self.rpreport:
            high_level = [u for u, p in self.db.players.items()
                          if p.online and p.level > 44]
            if online and (len(high_level) / online) > 0.15 and high_level:
                self.challenge_opp(random.choice(high_level), primnick)

        # Advance clocks for users online in channel
        if self.last_tick != 1:
            for p in self.db.online_players():
                if p.nick and p.nick in onchan:
                    p.next_ttl -= clock
                    p.idled += clock
                    if p.next_ttl < 1:
                        p.level += 1
                        p.next_ttl = self._level_ttl(p.level)
                        self._msg(
                            f"{p.username}, the {p.char_class}, has attained level {p.level}! "
                            f"Next level in {duration(p.next_ttl)}.")
                        self.find_item(p.username)
                        self.challenge_opp(p.username, primnick)

        self.rpreport += clock
        self.last_tick = now
