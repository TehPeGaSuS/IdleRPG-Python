"""Load and validate config.toml using tomllib (Python 3.11+)."""
from __future__ import annotations
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class WebConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    rate_window: int = 60
    rate_limit: int = 60


@dataclass
class NetworkConfig:
    name: str = "IRC"
    host: str = "irc.libera.chat"
    port: int = 6697
    channel: str = "#idlerpg"
    nick: str = "IdleRPGbot"
    use_ssl: bool = True
    ident_cmd: str = ""
    bot_modes: str = "+ix"
    op_cmd: str = ""
    ghost_cmd: str = ""
    realname: str = "IdleRPG Bot"
    username: str = "idlerpg"


@dataclass
class BotConfig:
    owner: str = "admin"
    owner_add_only: bool = True
    owner_del_only: bool = True

    db_file: str = "irpg.db"
    events_file: str = "events.txt"
    mods_file: str = "modifiers.txt"
    quest_file: str = "questinfo.txt"

    rpbase: int = 600
    rpstep: float = 1.16
    rppenstep: float = 1.14
    self_clock: int = 3
    limit_pen: int = 604800

    mapx: int = 500
    mapy: int = 500

    do_ban: bool = True
    ok_urls: List[str] = field(default_factory=list)
    noccodes: bool = True
    nononp: bool = True
    casematters: bool = True
    detect_splits: bool = True
    splitwait: int = 900
    voice_on_login: bool = True
    send_userlist: bool = True
    allow_userinfo: bool = True
    noscale: bool = False
    silent_mode: int = 0
    reconnect: bool = True
    reconnect_wait: int = 120

    help_url: str = ""
    admin_comm_url: str = ""
    map_url: str = ""


@dataclass
class Config:
    bot: BotConfig
    web: WebConfig
    network: NetworkConfig


def load(path: str | Path = "config.toml") -> Config:
    p = Path(path)
    if not p.exists():
        print(f"ERROR: Config file '{path}' not found. Copy config.toml.example and edit it.", file=sys.stderr)
        sys.exit(1)

    with open(p, "rb") as f:
        raw = tomllib.load(f)

    def _make(cls, data: dict):
        fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**fields)

    bot_cfg = _make(BotConfig, raw.get("bot", {}))
    web_cfg = _make(WebConfig, raw.get("web", {}))
    net_cfg = _make(NetworkConfig, raw.get("network", {}))
    return Config(bot=bot_cfg, web=web_cfg, network=net_cfg)
