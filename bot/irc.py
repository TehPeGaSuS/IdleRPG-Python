"""Async IRC client with full IdleRPG game logic."""
from __future__ import annotations
import asyncio
import logging
import random
import re
import ssl
import time
from collections import defaultdict
from typing import Dict, List, Optional

from config import Config
from db.models import Player, hash_password, verify_password, ITEM_SLOTS
from db.store import PlayerDB
from game.engine import GameEngine, duration

log = logging.getLogger(__name__)

_RE_PREFIX = re.compile(r"^:([^!@\s]+)(?:!([^@\s]+))?(?:@(\S+))?")


class IRCBot:
    """Single-network IRC bot. Runs inside an asyncio event loop."""

    def __init__(self, config: Config, db: PlayerDB):
        self.cfg = config
        self.bot_cfg = config.bot
        self.net = config.network
        self.db = db

        self._writer: Optional[asyncio.StreamWriter] = None
        self._send_queue: asyncio.Queue = asyncio.Queue()
        self._free_messages: int = 4        # flood control tokens
        self._out_bytes: int = 0
        self._in_bytes: int = 0

        self.onchan: Dict[str, int] = {}    # nick -> join timestamp
        self.split: Dict[str, dict] = {}    # nick!user@host -> {time, account}
        self.bans: List[str] = []           # pending ban removals

        self.primnick: str = self.net.nick  # original desired nick
        self.bot_nick: str = self.net.nick  # current nick (may have 0 appended)

        self.prev_online: Dict[str, str] = {}  # nick!userhost -> username (for autologin)
        self.auto_login: Dict[str, str] = {}   # username -> 1 (logged in automatically)
        self.lastreg: int = 0
        self.registrations: int = 0

        self.pause_mode: bool = False

        # game engine – announce/notice callbacks are filled in start()
        self.engine: Optional[GameEngine] = None

        self._connected: bool = False
        self._last_tick: float = time.monotonic()

    # ------------------------------------------------------------------
    # Public start coroutine
    # ------------------------------------------------------------------

    async def start(self):
        self.prev_online = self.db.load()

        self.engine = GameEngine(
            db=self.db,
            cfg=self.bot_cfg,
            announce=self._enqueue_chanmsg,
        )

        while True:
            try:
                await self._connect_and_run()
            except Exception as exc:
                log.error("IRC connection error: %s", exc)
            if not self.bot_cfg.reconnect:
                break
            log.info("Reconnecting in %ds...", self.bot_cfg.reconnect_wait)
            await asyncio.sleep(self.bot_cfg.reconnect_wait)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def _connect_and_run(self):
        ssl_ctx = ssl.create_default_context() if self.net.use_ssl else None
        log.info("Connecting to %s:%d (ssl=%s)", self.net.host, self.net.port, bool(ssl_ctx))

        reader, writer = await asyncio.open_connection(
            self.net.host, self.net.port, ssl=ssl_ctx
        )
        self._writer = writer
        self._connected = True
        log.info("Connected.")

        # Registration
        await self._raw(f"NICK {self.bot_nick}")
        await self._raw(f"USER {self.net.username} 0 0 :{self.net.realname}")

        # Kick off sender loop, tick loop, and reader concurrently
        await asyncio.gather(
            self._reader_loop(reader),
            self._sender_loop(),
            self._tick_loop(),
        )

    # ------------------------------------------------------------------
    # Raw send
    # ------------------------------------------------------------------

    async def _raw(self, line: str, priority: bool = False):
        if priority:
            if self._writer:
                data = (line + "\r\n").encode("utf-8", errors="replace")
                self._writer.write(data)
                await self._writer.drain()
                self._out_bytes += len(data)
                log.debug("-> %s", line)
        else:
            await self._send_queue.put(line)

    def _enqueue_chanmsg(self, text: str):
        """Synchronous enqueue – called from game engine."""
        if self.engine and self.engine.silent_mode & 1:
            return
        asyncio.get_event_loop().call_soon_threadsafe(
            self._send_queue.put_nowait,
            f"PRIVMSG {self.net.channel} :{text[:450]}"
        )

    async def _privmsg(self, target: str, text: str, priority: bool = False):
        mode = self.engine.silent_mode if self.engine else 0
        if mode == 3:
            return
        if not target.startswith(("#", "&", "+")) and mode == 2:
            return
        await self._raw(f"PRIVMSG {target} :{text[:450]}", priority)

    async def _notice(self, target: str, text: str):
        await self._raw(f"NOTICE {target} :{text[:450]}")

    async def _chanmsg(self, text: str):
        mode = self.engine.silent_mode if self.engine else 0
        if mode & 1:
            return
        await self._privmsg(self.net.channel, text)

    # ------------------------------------------------------------------
    # Flood-control sender loop
    # ------------------------------------------------------------------

    async def _sender_loop(self):
        while self._connected:
            if self._send_queue.empty():
                self._free_messages = min(4, self._free_messages + 1)
                await asyncio.sleep(1)
                continue
            sent = 0
            for _ in range(self._free_messages + 1):
                if self._send_queue.empty():
                    break
                if sent > 0 and sent > 768:
                    break
                line = await self._send_queue.get()
                if self._writer:
                    data = (line + "\r\n").encode("utf-8", errors="replace")
                    self._writer.write(data)
                    await self._writer.drain()
                    self._out_bytes += len(data)
                    log.debug("(fm%d) -> %s", self._free_messages, line)
                    sent += len(data)
                    self._free_messages = max(0, self._free_messages - 1)
            await asyncio.sleep(0.5)

    # ------------------------------------------------------------------
    # Tick loop
    # ------------------------------------------------------------------

    async def _tick_loop(self):
        while self._connected:
            await asyncio.sleep(self.bot_cfg.self_clock)
            if self._connected and self.engine and self.engine.last_tick != 1:
                try:
                    self.engine.tick(self.onchan, self.primnick, self.bot_nick)
                    if self.engine.rpreport % 60 == 0 and not self.pause_mode:
                        await self.db.async_save()
                except Exception as exc:
                    log.exception("tick error: %s", exc)
            # check ban expirations every tick
            if self.bans and self.engine and self.engine.rpreport % 1200 == 0:
                while self.bans:
                    chunk = self.bans[:4]
                    self.bans = self.bans[4:]
                    await self._raw(f"MODE {self.net.channel} -{'b'*len(chunk)} :{' '.join(chunk)}")
            # nick regain every 30 min
            if self.engine and self.engine.rpreport % 1800 == 0:
                if self.bot_nick != self.primnick:
                    if self.net.ghost_cmd:
                        await self._raw(self.net.ghost_cmd)
                    await self._raw(f"NICK {self.primnick}")
            # pause warning
            if self.engine and self.engine.rpreport % 600 == 0 and self.pause_mode:
                await self._chanmsg("WARNING: Cannot write database in PAUSE mode!")

    # ------------------------------------------------------------------
    # Reader loop
    # ------------------------------------------------------------------

    async def _reader_loop(self, reader: asyncio.StreamReader):
        buffer = b""
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                self._in_bytes += len(chunk)
                buffer += chunk
                while b"\n" in buffer:
                    idx = buffer.index(b"\n")
                    line = buffer[:idx].decode("utf-8", errors="replace").rstrip("\r\n")
                    buffer = buffer[idx + 1:]
                    log.debug("<- %s", line)
                    await self._parse(line)
        except asyncio.CancelledError:
            pass
        finally:
            self._connected = False
            # mark online players so they auto-login on reconnect
            for p in self.db.online_players():
                p.online = True
            await self.db.async_save()
            if self._writer:
                self._writer.close()

    # ------------------------------------------------------------------
    # IRC parser
    # ------------------------------------------------------------------

    async def _parse(self, line: str):
        parts = line.split(" ")

        # PING
        if parts[0].lower() == "ping":
            await self._raw(f"PONG {parts[1]}", priority=True)
            return

        # ERROR
        if parts[0].lower() == "error":
            log.warning("Server ERROR: %s", line)
            return

        # extract prefix nick
        m = _RE_PREFIX.match(parts[0])
        usernick = m.group(1) if m else ""
        username = self.db.find_by_nick(usernick)
        username = username.username if username else None

        cmd = parts[1].lower() if len(parts) > 1 else ""

        # Nick collision
        if cmd == "433" and self.bot_nick == (parts[3] if len(parts) > 3 else ""):
            self.bot_nick += "0"
            await self._raw(f"NICK {self.bot_nick}")
            return

        # ------------------------------------------------------------------
        if cmd == "001":
            if self.net.ident_cmd:
                await self._raw(self.net.ident_cmd)
            if self.net.bot_modes:
                await self._raw(f"MODE {self.bot_nick} :{self.net.bot_modes}")
            await self._raw(f"JOIN {self.net.channel}")

        elif cmd == "315":  # end of WHO
            await self._finish_autologin()

        elif cmd == "005":
            match = re.search(r"MODES=(\d+)", line)
            if match:
                # not used beyond voice-on-login but good to note
                pass

        elif cmd == "352":  # WHO reply
            if len(parts) >= 8:
                who_nick = parts[7]
                who_user = parts[4]
                who_host = parts[5]
                self.onchan[who_nick] = int(time.time())
                key = f"{who_nick}!{who_user}@{who_host}"
                if key in self.prev_online:
                    uname = self.prev_online[key]
                    p = self.db.get(uname)
                    if p:
                        p.online = True
                        self.auto_login[uname] = True

        elif cmd == "join":
            channel = parts[2].lstrip(":") if len(parts) > 2 else ""
            self.onchan[usernick] = int(time.time())
            if self.bot_cfg.detect_splits and f"{parts[0][1:]}" in self.split:
                del self.split[parts[0][1:]]
            elif usernick == self.bot_nick:
                await self._raw(f"WHO {self.net.channel}")
                if self.net.op_cmd:
                    cmd_str = self.net.op_cmd.replace("%botnick%", self.bot_nick)
                    await self._raw(cmd_str)
                if self.engine:
                    self.engine.last_tick = int(time.time())

        elif cmd == "quit":
            if usernick == self.primnick:
                await self._raw(f"NICK {self.primnick}", priority=True)
            elif (self.bot_cfg.detect_splits and
                  len(parts) >= 3 and
                  re.match(r":\S+\.\S+ \S+\.\S+$", " ".join(parts[2:]))):
                if username:
                    self.split[parts[0][1:]] = {"time": time.time(), "account": username}
            else:
                if username and self.engine:
                    self.engine.penalize(username, "quit", primnick=self.primnick)
            self.onchan.pop(usernick, None)

        elif cmd == "nick":
            new_nick = parts[2].lstrip(":") if len(parts) > 2 else ""
            if usernick == self.bot_nick:
                self.bot_nick = new_nick
            elif usernick == self.primnick:
                await self._raw(f"NICK {self.primnick}", priority=True)
            else:
                if username and self.engine:
                    self.engine.penalize(username, "nick", new_nick, primnick=self.primnick)
                self.onchan[new_nick] = self.onchan.pop(usernick, int(time.time()))

        elif cmd == "part":
            if username and self.engine:
                self.engine.penalize(username, "part", primnick=self.primnick)
            self.onchan.pop(usernick, None)

        elif cmd == "kick":
            kicked = parts[3] if len(parts) > 3 else ""
            kicked_user_obj = self.db.find_by_nick(kicked)
            kicked_username = kicked_user_obj.username if kicked_user_obj else None
            if kicked_username and self.engine:
                self.engine.penalize(kicked_username, "kick", primnick=self.primnick)
            self.onchan.pop(kicked, None)

        elif cmd == "notice" and len(parts) > 2 and parts[2] != self.bot_nick:
            msg_len = max(0, len(" ".join(parts[3:])) - 1)
            if username and self.engine:
                self.engine.penalize(username, "notice", msg_len, primnick=self.primnick)

        elif cmd == "privmsg":
            target = parts[2] if len(parts) > 2 else ""
            full_msg = " ".join(parts[3:])[1:] if len(parts) > 3 else ""
            await self._handle_privmsg(parts[0][1:], usernick, username, target, full_msg)

    # ------------------------------------------------------------------
    # PRIVMSG handler
    # ------------------------------------------------------------------

    async def _handle_privmsg(self, prefix: str, usernick: str, username: Optional[str],
                               target: str, msg: str):
        is_to_bot = target.lower() == self.bot_nick.lower()
        is_to_chan = target.lower() == self.net.channel.lower()

        if is_to_bot:
            args = msg.split()
            cmd = args[0].lower() if args else ""
            await self._handle_command(prefix, usernick, username, cmd, args)
        elif is_to_chan:
            # penalize channel talk
            msg_len = len(msg)
            penalized = False
            if username and self.engine:
                penalized = self.engine.penalize(username, "privmsg", msg_len, primnick=self.primnick)
            # URL ban for non-players
            if (not penalized and "http:" in msg.lower() and
                    (time.time() - self.onchan.get(usernick, time.time())) < 90 and
                    self.bot_cfg.do_ban):
                ok = any(u.lower() in msg.lower() for u in self.bot_cfg.ok_urls)
                if not ok:
                    await self._raw(f"MODE {self.net.channel} +b {prefix}")
                    await self._raw(f"KICK {self.net.channel} {usernick} :No advertising; ban will be lifted within the hour.")
                    if len(self.bans) < 12:
                        self.bans.append(prefix)

    async def _handle_command(self, prefix: str, usernick: str, username: Optional[str],
                               cmd: str, args: list):
        """Handle bot commands sent via PRIVMSG to the bot nick."""
        p = self.db.get(username) if username else None
        is_admin = p.is_admin if p else False

        async def reply(text: str, force: bool = False):
            await self._privmsg(usernick, text, priority=force)

        async def nreply(text: str, force: bool = False):
            await self._notice(usernick, text)

        # ------------------------------------------------------------------
        if cmd == "\x01version\x01":
            await nreply("\x01VERSION IdleRPG Python by your server\x01")

        elif cmd == "register":
            if username:
                await reply(f"Sorry, you are already online as {username}.")
                return
            if len(args) < 4:
                await reply("Try: REGISTER <char name> <password> <class>")
                await reply("IE : REGISTER Poseidon MyPassword God of the Sea")
                return
            if self.pause_mode:
                await reply("Sorry, new accounts may not be registered while the bot is in pause mode.")
                return
            char_name = args[1]
            password = args[2]
            char_class = " ".join(args[3:])
            if self.db.username_exists(char_name, self.bot_cfg.casematters):
                await reply("Sorry, that character name is already in use.")
            elif char_name.lower() in (self.bot_nick.lower(), self.primnick.lower()):
                await reply("Sorry, that character name cannot be registered.")
            elif usernick not in self.onchan:
                await reply(f"Sorry, you're not in {self.net.channel}.")
            elif len(char_name) > 16 or len(char_name) < 1:
                await reply("Sorry, character names must be < 17 and > 0 chars long.")
            elif char_name.startswith("#") or "\x01" in char_name:
                await reply("Sorry, invalid character name.")
            elif self.bot_cfg.noccodes and (
                    any(ord(c) < 32 for c in char_name + char_class)):
                await reply("Sorry, names/classes may not include control codes.")
            elif len(char_class) > 30:
                await reply("Sorry, character classes must be < 31 chars long.")
            elif int(time.time()) == self.lastreg:
                await reply("Wait 1 second and try again.")
            else:
                self.lastreg = int(time.time())
                self.registrations += 1
                if self.bot_cfg.voice_on_login:
                    await self._raw(f"MODE {self.net.channel} +v :{usernick}")
                new_player = Player(
                    username=char_name,
                    password=hash_password(password),
                    char_class=char_class,
                    level=0,
                    next_ttl=self.bot_cfg.rpbase,
                    online=True,
                    nick=usernick,
                    userhost=prefix.split('!', 1)[1],
                    x=random.randint(0, self.bot_cfg.mapx),
                    y=random.randint(0, self.bot_cfg.mapy),
                )
                self.db.add(new_player)
                await self._chanmsg(
                    f"Welcome {usernick}'s new player {char_name}, the {char_class}! "
                    f"Next level in {duration(self.bot_cfg.rpbase)}.")
                await reply(f"Success! Account {char_name} created. You have "
                            f"{self.bot_cfg.rpbase} seconds idleness until you reach level 1.")
                await reply("NOTE: The point of the game is to see who can idle the longest. "
                            "As such, talking in the channel, parting, quitting, and changing "
                            "nicks all penalize you.")

        elif cmd == "login":
            if username:
                await nreply(f"Sorry, you are already online as {username}.")
                return
            if len(args) < 3:
                await nreply("Try: LOGIN <username> <password>")
                return
            lname, lpass = args[1], args[2]
            lp = self.db.get(lname)
            if not lp:
                await nreply("Sorry, no such account name. Note that account names are case sensitive.")
            elif usernick not in self.onchan:
                await nreply(f"Sorry, you're not in {self.net.channel}.")
            elif not verify_password(lpass, lp.password):
                await nreply("Wrong password.")
            else:
                if self.bot_cfg.voice_on_login:
                    await self._raw(f"MODE {self.net.channel} +v :{usernick}")
                lp.online = True
                lp.nick = usernick
                lp.userhost = prefix.split('!', 1)[1]
                lp.last_login = int(time.time())
                await self._chanmsg(
                    f"{lname}, the level {lp.level} {lp.char_class}, is now online from "
                    f"nickname {usernick}. Next level in {duration(lp.next_ttl)}.")
                await nreply(f"Logon successful. Next level in {duration(lp.next_ttl)}.")

        elif cmd == "logout":
            if not username or not self.engine:
                await reply("You are not logged in.")
            else:
                self.engine.penalize(username, "logout", primnick=self.primnick)

        elif cmd == "newpass":
            if not p:
                await reply("You are not logged in.")
            elif len(args) < 2:
                await reply("Try: NEWPASS <new password>")
            else:
                p.password = hash_password(args[1])
                await reply("Your password was changed.")

        elif cmd == "align":
            if not p:
                await reply("You are not logged in.")
            elif len(args) < 2 or args[1].lower() not in ("good", "neutral", "evil"):
                await reply("Try: ALIGN <good|neutral|evil>")
            else:
                p.alignment = args[1].lower()[0]
                await self._chanmsg(f"{username} has changed alignment to: {args[1].lower()}.")
                await reply(f"Your alignment was changed to {args[1].lower()}.")

        elif cmd == "removeme":
            if not p:
                await reply("You are not logged in.")
            else:
                await reply(f"Account {username} removed.")
                await self._chanmsg(f"{prefix} removed his account, {username}, the {p.char_class}.")
                self.db.remove(username)

        elif cmd == "whoami":
            if not p:
                await reply("You are not logged in.")
            else:
                await reply(f"You are {username}, the level {p.level} {p.char_class}. "
                            f"Next level in {duration(p.next_ttl)}")

        elif cmd == "status" and self.bot_cfg.allow_userinfo:
            if not username:
                await reply("You are not logged in.")
            elif len(args) > 1 and not self.db.get(args[1]):
                await reply(f"No such user.")
            else:
                target_name = args[1] if len(args) > 1 else username
                tp = self.db.get(target_name)
                await reply(
                    f"{target_name}: Level {tp.level} {tp.char_class}; "
                    f"Status: O{'n' if tp.online else 'ff'}line; "
                    f"TTL: {duration(tp.next_ttl)}; "
                    f"Idled: {duration(tp.idled)}; "
                    f"Item sum: {tp.item_sum()}")

        elif cmd == "quest":
            if not self.engine or not self.engine.quest.is_active():
                await reply("There is no active quest.")
            else:
                q = self.engine.quest
                names = ", ".join(q.questers[:3]) + f", and {q.questers[3]}"
                if q.type == 1:
                    await reply(f"{names} are on a quest to {q.text}. "
                                f"Quest to complete in {duration(q.qtime - int(time.time()))}.")
                else:
                    map_note = f" See {self.bot_cfg.map_url} to monitor their progress." if self.bot_cfg.map_url else ""
                    await reply(f"{names} are on a quest to {q.text}. "
                                f"Participants must reach [{q.p1[0]},{q.p1[1]}], "
                                f"then [{q.p2[0]},{q.p2[1]}].{map_note}")

        elif cmd == "help":
            if not is_admin:
                await reply(f"For information on IRPG bot commands, see {self.bot_cfg.help_url}")
            else:
                await reply(f"Help URL: {self.bot_cfg.help_url}", force=True)
                await reply(f"Admin commands URL: {self.bot_cfg.admin_comm_url}", force=True)

        elif cmd == "info":
            if not is_admin and self.bot_cfg.allow_userinfo:
                admins = ", ".join(p.nick for p in self.db.players.values()
                                   if p.is_admin and p.online)
                await reply(f"IdleRPG Python bot. Admins online: {admins}.")
            elif is_admin:
                online_c = len(self.db.online_players())
                total_c = len(self.db.players)
                qsize = self._send_queue.qsize()
                await reply(
                    f"{self._out_bytes/1024:.2f}kb sent, {self._in_bytes/1024:.2f}kb received. "
                    f"{online_c} IRPG users online of {total_c} total. "
                    f"{self.registrations} accounts created since startup. "
                    f"PAUSE_MODE={int(self.pause_mode)} SILENT_MODE={self.engine.silent_mode if self.engine else 0}. "
                    f"Queue: {qsize} items.",
                    force=True)
            else:
                await reply("You do not have access to INFO.")

        # Admin commands
        elif cmd == "hog":
            if not is_admin:
                await reply("You don't have access to HOG.")
            else:
                await self._chanmsg(f"{usernick} has summoned the Hand of God.")
                if self.engine:
                    self.engine.hog()

        elif cmd == "pause":
            if not is_admin:
                await reply("You don't have access to PAUSE.")
            else:
                self.pause_mode = not self.pause_mode
                await reply(f"PAUSE_MODE set to {int(self.pause_mode)}.", force=True)

        elif cmd == "silent":
            if not is_admin:
                await reply("You don't have access to SILENT.")
            elif len(args) < 2 or not args[1].isdigit() or int(args[1]) > 3:
                await reply("Try: SILENT <0-3>", force=True)
            else:
                if self.engine:
                    self.engine.silent_mode = int(args[1])
                await reply(f"SILENT_MODE set to {args[1]}.", force=True)

        elif cmd == "push":
            if not is_admin:
                await reply("You don't have access to PUSH.")
            elif len(args) < 3 or not re.match(r"^-?\d+$", args[2]):
                await reply("Try: PUSH <char name> <seconds>", force=True)
            else:
                tp = self.db.get(args[1])
                if not tp:
                    await reply(f"No such username {args[1]}.", force=True)
                else:
                    pushed = int(args[2])
                    if pushed > tp.next_ttl:
                        await reply(f"TTL for {args[1]} ({tp.next_ttl}s) < {pushed}; setting TTL to 0.", force=True)
                        await self._chanmsg(f"{usernick} has pushed {args[1]} {tp.next_ttl} seconds toward level {tp.level+1}")
                        tp.next_ttl = 0
                    else:
                        tp.next_ttl -= pushed
                        await self._chanmsg(
                            f"{usernick} has pushed {args[1]} {pushed} seconds toward level {tp.level+1}. "
                            f"{args[1]} reaches next level in {duration(tp.next_ttl)}.")

        elif cmd == "del":
            if not is_admin:
                await reply("You don't have access to DEL.")
            elif len(args) < 2:
                await reply("Try: DEL <char name>", force=True)
            elif not self.db.get(args[1]):
                await reply(f"No such account {args[1]}.", force=True)
            else:
                self.db.remove(args[1])
                await self._chanmsg(f"Account {args[1]} removed by {prefix}.")

        elif cmd == "delold":
            if not is_admin:
                await reply("You don't have access to DELOLD.")
            elif len(args) < 2 or not re.match(r"^\d+\.?\d*$", args[1]):
                await reply("Try: DELOLD <# of days>", force=True)
            else:
                cutoff = float(args[1]) * 86400
                old = [u for u, p in self.db.players.items()
                       if (time.time() - p.last_login) > cutoff and not p.online]
                for u in old:
                    self.db.remove(u)
                await self._chanmsg(
                    f"{len(old)} accounts not accessed in the last {args[1]} days removed by {prefix}.")

        elif cmd == "mkadmin":
            owner_only = self.bot_cfg.owner_add_only
            if not is_admin or (owner_only and username != self.bot_cfg.owner):
                await reply("You don't have access to MKADMIN.")
            elif len(args) < 2:
                await reply("Try: MKADMIN <char name>", force=True)
            else:
                tp = self.db.get(args[1])
                if not tp:
                    await reply(f"No such account {args[1]}.", force=True)
                else:
                    tp.is_admin = True
                    await reply(f"Account {args[1]} is now a bot admin.", force=True)

        elif cmd == "deladmin":
            owner_only = self.bot_cfg.owner_del_only
            if not is_admin or (owner_only and username != self.bot_cfg.owner):
                await reply("You don't have access to DELADMIN.")
            elif len(args) < 2:
                await reply("Try: DELADMIN <char name>", force=True)
            elif args[1] == self.bot_cfg.owner:
                await reply("Cannot DELADMIN owner account.", force=True)
            else:
                tp = self.db.get(args[1])
                if not tp:
                    await reply(f"No such account {args[1]}.", force=True)
                else:
                    tp.is_admin = False
                    await reply(f"Account {args[1]} is no longer a bot admin.", force=True)

        elif cmd == "chpass":
            if not is_admin:
                await reply("You don't have access to CHPASS.")
            elif len(args) < 3:
                await reply("Try: CHPASS <char name> <new pass>", force=True)
            else:
                tp = self.db.get(args[1])
                if not tp:
                    await reply(f"No such username {args[1]}.", force=True)
                else:
                    tp.password = hash_password(args[2])
                    await reply(f"Password for {args[1]} changed.", force=True)

        elif cmd == "chuser":
            if not is_admin:
                await reply("You don't have access to CHUSER.")
            elif len(args) < 3:
                await reply("Try: CHUSER <char name> <new char name>", force=True)
            elif not self.db.get(args[1]):
                await reply(f"No such username {args[1]}.", force=True)
            elif self.db.get(args[2]):
                await reply(f"Username {args[2]} is already taken.", force=True)
            else:
                old_p = self.db.players.pop(args[1])
                old_p.username = args[2]
                self.db.add(old_p)
                await reply(f"Username for {args[1]} changed to {args[2]}.", force=True)

        elif cmd == "chclass":
            if not is_admin:
                await reply("You don't have access to CHCLASS.")
            elif len(args) < 3:
                await reply("Try: CHCLASS <char name> <new char class>", force=True)
            else:
                tp = self.db.get(args[1])
                if not tp:
                    await reply(f"No such username {args[1]}.", force=True)
                else:
                    tp.char_class = " ".join(args[2:])
                    await reply(f"Class for {args[1]} changed to {tp.char_class}.", force=True)

        elif cmd == "backup":
            if not is_admin:
                await reply("You don't have access to BACKUP.")
            else:
                self.db.backup()
                await reply(f"{self.bot_cfg.db_file} backed up.", force=True)

        elif cmd == "reloaddb":
            if not is_admin:
                await reply("You do not have access to RELOADDB.")
            elif not self.pause_mode:
                await reply("ERROR: Can only use RELOADDB while in PAUSE mode.", force=True)
            else:
                self.prev_online = self.db.load()
                await reply(f"Reread player database; {len(self.db.players)} accounts loaded.", force=True)

        elif cmd == "die":
            if not is_admin:
                await reply("You do not have access to DIE.")
            else:
                self.bot_cfg.reconnect = False
                await self.db.async_save()
                await self._raw(f"QUIT :DIE from {prefix}", priority=True)
                self._connected = False

    # ------------------------------------------------------------------
    # Auto-login completion
    # ------------------------------------------------------------------

    async def _finish_autologin(self):
        count = len(self.auto_login)
        online_prev = len(self.prev_online)
        if count and self.bot_cfg.send_userlist:
            names = ", ".join(self.auto_login.keys())
            if len(names) < 1024:
                await self._chanmsg(
                    f"{count} users matching {online_prev} hosts automatically logged in; accounts: {names}")
            else:
                await self._chanmsg(
                    f"{count} users matching {online_prev} hosts automatically logged in.")
        else:
            await self._chanmsg("0 users qualified for auto login.")

        if self.bot_cfg.voice_on_login:
            nicks = [self.db.get(u).nick for u in self.auto_login if self.db.get(u)]
            while nicks:
                chunk = nicks[:3]
                nicks = nicks[3:]
                await self._raw(f"MODE {self.net.channel} +{'v'*len(chunk)} {' '.join(chunk)}")

        self.prev_online = {}
        self.auto_login = {}

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------
    def online_players(self):
        return self.db.online_players()
