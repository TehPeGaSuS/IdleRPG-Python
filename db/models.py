"""Player data model and database read/write (tab-delimited, same format as Perl bot)."""
from __future__ import annotations
import hashlib
import secrets
import string
import time
from dataclasses import dataclass, field
from typing import Dict, Optional


ITEM_SLOTS = [
    "amulet", "charm", "helm", "pair of boots",
    "pair of gloves", "ring", "set of leggings",
    "shield", "tunic", "weapon",
]

PENALTY_FIELDS = [
    "pen_mesg", "pen_nick", "pen_part",
    "pen_kick", "pen_quit", "pen_quest", "pen_logout",
]

# DB column order matches the original Perl bot exactly (32 fields per line)
# index 0 = username, 1..31 = the rest
DB_HEADER = "\t".join([
    "# username", "pass", "is admin", "level", "class", "next ttl",
    "nick", "userhost", "online", "idled", "x pos", "y pos",
    "pen_mesg", "pen_nick", "pen_part", "pen_kick", "pen_quit",
    "pen_quest", "pen_logout", "created", "last login",
    "amulet", "charm", "helm", "boots", "gloves", "ring",
    "leggings", "shield", "tunic", "weapon", "alignment",
])


def _mksalt(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def hash_password(password: str) -> str:
    """SHA-256 hex digest (replaces old crypt() since Python has no portable crypt)."""
    salt = _mksalt()
    digest = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"sha256${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored hash.

    Supports:
      - sha256$salt$digest  — our native format
      - $1$..., $5$..., $6$..., $2b$... — crypt() hashes from the original Perl bot
      - bare 13-char DES crypt() hashes
    Returns True if the password matches.
    """
    if stored.startswith("sha256$"):
        _, salt, digest = stored.split("$", 2)
        return hashlib.sha256((salt + password).encode()).hexdigest() == digest

    # Legacy crypt() hash from the original Perl bot — verify and signal for upgrade
    try:
        import crypt as _crypt
        return _crypt.crypt(password, stored) == stored
    except Exception:
        return False


def is_legacy_hash(stored: str) -> bool:
    """Return True if the stored hash is a legacy crypt() hash that should be upgraded."""
    return not stored.startswith("sha256$")


@dataclass
class Player:
    username: str
    password: str               # hashed

    is_admin: bool = False
    level: int = 0
    char_class: str = ""
    next_ttl: int = 600         # seconds until next level
    nick: str = ""
    userhost: str = ""
    online: bool = False
    idled: int = 0
    x: int = 0
    y: int = 0

    pen_mesg: int = 0
    pen_nick: int = 0
    pen_part: int = 0
    pen_kick: int = 0
    pen_quit: int = 0
    pen_quest: int = 0
    pen_logout: int = 0

    created: int = field(default_factory=lambda: int(time.time()))
    last_login: int = field(default_factory=lambda: int(time.time()))

    # item slot -> level (int suffix + optional letter suffix for uniques)
    items: Dict[str, str] = field(default_factory=lambda: {s: "0" for s in ITEM_SLOTS})

    alignment: str = "n"  # n=neutral, g=good, e=evil

    def item_sum(self, battle: bool = False) -> int:
        total = sum(int(v) for v in self.items.values())
        if battle:
            if self.alignment == "g":
                total = int(total * 1.1)
            elif self.alignment == "e":
                total = int(total * 0.9)
        return total

    def to_db_row(self) -> str:
        cols = [
            self.username,
            self.password,
            "1" if self.is_admin else "0",
            str(self.level),
            self.char_class,
            str(self.next_ttl),
            self.nick,
            self.userhost,
            "1" if self.online else "0",
            str(self.idled),
            str(self.x),
            str(self.y),
            str(self.pen_mesg),
            str(self.pen_nick),
            str(self.pen_part),
            str(self.pen_kick),
            str(self.pen_quit),
            str(self.pen_quest),
            str(self.pen_logout),
            str(self.created),
            str(self.last_login),
            self.items["amulet"],
            self.items["charm"],
            self.items["helm"],
            self.items["pair of boots"],
            self.items["pair of gloves"],
            self.items["ring"],
            self.items["set of leggings"],
            self.items["shield"],
            self.items["tunic"],
            self.items["weapon"],
            self.alignment,
        ]
        return "\t".join(cols)

    @classmethod
    def from_db_row(cls, line: str) -> "Player":
        i = line.rstrip("\n").split("\t")
        if len(i) != 32:
            raise ValueError(f"Expected 32 fields, got {len(i)}: {line!r}")
        return cls(
            username=i[0],
            password=i[1],
            is_admin=i[2] == "1",
            level=int(i[3]),
            char_class=i[4],
            next_ttl=int(i[5]),
            nick=i[6],
            userhost=i[7],
            online=i[8] == "1",
            idled=int(i[9]),
            x=int(i[10]),
            y=int(i[11]),
            pen_mesg=int(i[12]),
            pen_nick=int(i[13]),
            pen_part=int(i[14]),
            pen_kick=int(i[15]),
            pen_quit=int(i[16]),
            pen_quest=int(i[17]),
            pen_logout=int(i[18]),
            created=int(i[19]),
            last_login=int(i[20]),
            items={
                "amulet": i[21],
                "charm": i[22],
                "helm": i[23],
                "pair of boots": i[24],
                "pair of gloves": i[25],
                "ring": i[26],
                "set of leggings": i[27],
                "shield": i[28],
                "tunic": i[29],
                "weapon": i[30],
            },
            alignment=i[31],
        )
