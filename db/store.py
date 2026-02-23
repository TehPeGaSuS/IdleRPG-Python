"""Synchronous (but asyncio-safe) player database backed by the tab-delimited flat file."""
from __future__ import annotations
import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Dict, Optional

from .models import DB_HEADER, Player, ITEM_SLOTS

log = logging.getLogger(__name__)


class PlayerDB:
    """In-memory dict of username -> Player, persisted to a tab-delimited flat file."""

    def __init__(self, db_path: str):
        self.path = Path(db_path)
        self._players: Dict[str, Player] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public helpers (all sync-safe, called from the bot's async context
    # without awaiting – they never block for long)
    # ------------------------------------------------------------------

    @property
    def players(self) -> Dict[str, Player]:
        return self._players

    def get(self, username: str) -> Optional[Player]:
        return self._players.get(username)

    def find_by_nick(self, nick: str) -> Optional[Player]:
        for p in self._players.values():
            if p.online and p.nick == nick:
                return p
        return None

    def add(self, player: Player) -> None:
        self._players[player.username] = player

    def remove(self, username: str) -> bool:
        return self._players.pop(username, None) is not None

    def online_players(self):
        return [p for p in self._players.values() if p.online]

    def username_exists(self, name: str, case_matters: bool) -> bool:
        if case_matters:
            return name in self._players
        lower = name.lower()
        return any(k.lower() == lower for k in self._players)

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def backup(self) -> None:
        if not self.path.exists():
            return
        bdir = Path(".dbbackup")
        bdir.mkdir(exist_ok=True)
        dest = bdir / f"{self.path.name}{int(time.time())}"
        shutil.copy2(self.path, dest)
        log.debug("DB backed up to %s", dest)

    def load(self) -> Dict[str, str]:
        """Load players from file. Returns {nick!userhost: username} for auto-login."""
        self.backup()
        prev_online: Dict[str, str] = {}
        self._players = {}

        if not self.path.exists():
            return prev_online

        with open(self.path, encoding="utf-8") as f:
            for lineno, raw in enumerate(f, 1):
                raw = raw.rstrip("\n")
                if raw.startswith("#") or not raw.strip():
                    continue
                try:
                    p = Player.from_db_row(raw)
                except ValueError as exc:
                    log.error("loaddb line %d: %s", lineno, exc)
                    continue
                if p.online:
                    prev_online[f"{p.nick}!{p.userhost}"] = p.username
                p.online = False          # mark offline until WHO confirms
                self._players[p.username] = p

        log.info("Loaded %d accounts, %d previously online.", len(self._players), len(prev_online))
        return prev_online

    def save(self) -> bool:
        """Write all players to disk. Returns True on success."""
        tmp = self.path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(DB_HEADER + "\n")
                for p in self._players.values():
                    f.write(p.to_db_row() + "\n")
            tmp.replace(self.path)
            return True
        except OSError as exc:
            log.error("writedb failed: %s", exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    async def async_save(self) -> bool:
        """Non-blocking wrapper: runs save() in executor."""
        loop = asyncio.get_event_loop()
        async with self._lock:
            return await loop.run_in_executor(None, self.save)
