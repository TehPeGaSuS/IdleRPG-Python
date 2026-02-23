# IdleRPG – Python Port

A complete Python 3.11+ port of the classic IRPG Perl bot (v3.1.2).  
Single IRC network, async-native (asyncio + aiohttp), flat-file database compatible with the original format.

## Requirements

- Python 3.11+
- `aiohttp` (`pip install aiohttp`)

## Quick Start

```bash
pip install aiohttp

# 1. Copy and edit the config
cp config.toml.example config.toml  # already exists as config.toml

# 2. First-run setup (creates admin account)
python main.py --setup

# 3. Run the bot
python main.py
```

## Configuration (`config.toml`)

| Section | Key | Description |
|---|---|---|
| `[bot]` | `owner` | Admin account that cannot be de-admined |
| `[bot]` | `rpbase` | Base seconds to reach level 1 (600 = 10 min) |
| `[bot]` | `rpstep` | TTL multiplier per level (`rpbase * rpstep^level`) |
| `[bot]` | `rppenstep` | Penalty multiplier per level |
| `[bot]` | `self_clock` | Tick interval in seconds (must divide 60 evenly) |
| `[bot]` | `db_file` | Path to player database |
| `[bot]` | `events_file` | Path to events file (calamities, godsends, quests) |
| `[web]` | `host` / `port` | aiohttp listen address |
| `[web]` | `rate_limit` | Max requests per `rate_window` seconds per IP |
| `[network]` | `host` / `port` / `channel` | IRC server details |
| `[network]` | `use_ssl` | Enable TLS |
| `[network]` | `ident_cmd` | Command sent after connect (NickServ IDENTIFY) |

## Web Pages

| URL | Description |
|---|---|
| `/` | Leaderboard of all players |
| `/player/<name>` | Detailed player stats, items, penalties |
| `/quest` | Active quest status |

## Bot Commands (send via `/msg BotNick <command>`)

| Command | Access | Description |
|---|---|---|
| `REGISTER <name> <pass> <class>` | Anyone | Create new character |
| `LOGIN <name> <pass>` | Anyone | Log in |
| `LOGOUT` | Player | Log out (with penalty) |
| `NEWPASS <pass>` | Player | Change password |
| `ALIGN <good\|neutral\|evil>` | Player | Change alignment |
| `REMOVEME` | Player | Delete own account |
| `WHOAMI` | Player | Show your own stats |
| `STATUS [name]` | Player | Show player stats |
| `QUEST` | Anyone | Show active quest |
| `HELP` | Anyone | Show help URL |
| `HOG` | Admin | Summon Hand of God |
| `PUSH <name> <secs>` | Admin | Adjust a player's TTL |
| `DEL <name>` | Admin | Delete an account |
| `DELOLD <days>` | Admin | Delete accounts inactive N days |
| `MKADMIN <name>` | Admin | Grant admin access |
| `DELADMIN <name>` | Admin | Revoke admin access |
| `CHPASS <name> <pass>` | Admin | Change player password |
| `CHUSER <old> <new>` | Admin | Rename player |
| `CHCLASS <name> <class>` | Admin | Change player class |
| `PAUSE` | Admin | Toggle pause mode |
| `SILENT <0-3>` | Admin | Set silent mode |
| `BACKUP` | Admin | Force database backup |
| `RELOADDB` | Admin | Reload DB (pause mode only) |
| `DIE` | Admin | Shut down bot |

## File Structure

```
idlerpg/
├── main.py          # Entry point
├── config.py        # Config loader (TOML → dataclasses)
├── config.toml      # Your configuration
├── events.txt       # Calamity/Godsend/Quest events
├── bot/
│   └── irc.py       # Async IRC client + command handler
├── db/
│   ├── models.py    # Player dataclass + DB serialization
│   └── store.py     # PlayerDB (in-memory + flat-file persistence)
├── game/
│   └── engine.py    # All game logic (levelling, combat, quests, events)
└── web/
    └── server.py    # aiohttp app (leaderboard, player detail, quest)
```

## Database Compatibility

The flat-file format is **tab-delimited**, identical to the original Perl bot's `.db` file (32 columns per player row). You can migrate an existing database directly — the only difference is that passwords are re-hashed using SHA-256 on first `LOGIN` (the old `crypt()` hashes won't verify, so players will need a `CHPASS` reset).

## Events File Format

Same as the original:

```
C <calamity text>     # bad event
G <godsend text>      # good event
Q1 <quest text>       # timed quest
Q2 <x1> <y1> <x2> <y2> <quest text>   # navigation quest
```
