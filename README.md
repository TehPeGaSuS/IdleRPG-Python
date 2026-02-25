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
cp config.toml.example config.toml

# 2. First-run setup (creates admin account)
python main.py --setup

# 3. Run the bot
python main.py
```

## Configuration (`config.toml`)

| Section | Key | Default | Description |
|---|---|---|---|
| `[bot]` | `owner` | — | Admin account that cannot be de-admined |
| `[bot]` | `rpbase` | `600` | Base seconds to reach level 1 (600 = 10 min) |
| `[bot]` | `rpstep` | `1.14` | TTL multiplier per level (`rpbase * rpstep^level`) |
| `[bot]` | `rppenstep` | `1.14` | Penalty multiplier per level |
| `[bot]` | `self_clock` | `3` | Tick interval in seconds (must divide 60 evenly) |
| `[bot]` | `db_file` | — | Path to player database |
| `[bot]` | `events_file` | — | Path to events file (calamities, godsends, quests) |
| `[bot]` | `reset_on_level` | `0` | Reset all players when someone reaches this level (0 = disabled). At default settings, level 60 ≈ 5 months. |
| `[bot]` | `do_topic` | `false` | Update channel topic with top 3 players (requires op) |
| `[bot]` | `topic_interval` | `3` | Hours between topic updates |
| `[bot]` | `do_top_announce` | `false` | Announce top 3 players in channel (fallback if no op) |
| `[bot]` | `top_announce_interval` | `3` | Hours between announcements |
| `[bot]` | `voice_on_login` | `true` | Give +v to players on login |
| `[bot]` | `send_userlist` | `true` | Announce auto-logged-in users on reconnect |
| `[bot]` | `do_ban` | `false` | Kick/ban non-players posting URLs |
| `[web]` | `host` / `port` | `0.0.0.0:8080` | aiohttp listen address |
| `[web]` | `rate_limit` | — | Max requests per `rate_window` seconds per IP |
| `[network]` | `name` | — | Network display name (shown on website) |
| `[network]` | `host` / `port` / `channel` | — | IRC server details |
| `[network]` | `use_ssl` | `true` | Enable TLS |
| `[network]` | `ident_cmd` | — | Command sent after connect (e.g. NickServ IDENTIFY) |
| `[network]` | `op_cmd` | — | Command to request op after joining channel |

## Web Pages

| URL | Description |
|---|---|
| `/` | Home — welcome, where to play, top 3 players, how to play |
| `/players` | Full player list with status and item sums |
| `/player/<n>` | Player profile: stats, mini-map, items, penalties |
| `/map` | World map — all players as dots, hover for name |
| `/quest` | Active quest status and quester positions |
| `/hof` | Hall of Fame — top 3 of each completed round |
| `/admin` | Admin command reference |

## Auto-Login

Players are automatically logged in on reconnect if their `user@host` matches what was stored at last logout. This works even if their nick has changed. On bot restart, a WHO sweep re-logs everyone who was previously online. The channel receives a login announcement and players get +v if `voice_on_login` is enabled.

## Topic & Announcements

When `do_topic = true` and the bot has op, it updates the channel topic with the top 3 players every `topic_interval` hours. If the bot loses op, it falls back to a channel announcement (if `do_top_announce = true`). Both also fire once ~10 seconds after the bot joins the channel on startup.

Format: `IdleRPG Top Players: 🥇 PlayerA (lvl 12), 🥈 PlayerB (lvl 10), 🥉 PlayerC (lvl 8)`

## Hall of Fame & Round Resets

When `reset_on_level` is set to a non-zero value, the game resets when any player reaches that level:

1. The bot waits for any active quest to finish
2. Announces the round result: `Round X is over! 1st place: A, 2nd place: B, 3rd place: C. A new round begins!`
3. Records top 3 to `hof.json` (persists across restarts)
4. Resets all players' game stats (level, items, TTL, penalties) — accounts and passwords are kept
5. Re-logs in all players currently in the channel via a fresh WHO sweep

At default settings (`rpbase=600`, `rppenstep=1.14`), approximate clean-run times:

| Reset level | Time (no penalties) |
|---|---|
| 40 | ~10 days |
| 50 | ~40 days |
| 60 | ~147 days (~5 months) |
| 100 | ~75 years |

## Bot Commands (send via `/msg BotNick <command>`)

| Command | Access | Description |
|---|---|---|
| `REGISTER <n> <pass> <class>` | Anyone | Create new character |
| `LOGIN <n> <pass>` | Anyone | Log in |
| `LOGOUT` | Player | Log out (with penalty) |
| `NEWPASS <pass>` | Player | Change password |
| `ALIGN <good\|neutral\|evil>` | Player | Change alignment |
| `REMOVEME` | Player | Delete own account |
| `WHOAMI` | Player | Show your own stats |
| `STATUS [name]` | Player | Show player stats |
| `QUEST` | Anyone | Show active quest |
| `HELP` | Anyone | Show help URL |
| `HOG` | Admin | Summon Hand of God |
| `PUSH <n> <secs>` | Admin | Adjust a player's TTL |
| `DEL <n>` | Admin | Delete an account |
| `DELOLD <days>` | Admin | Delete accounts inactive N days |
| `MKADMIN <n>` | Admin | Grant admin access |
| `DELADMIN <n>` | Admin | Revoke admin access |
| `CHPASS <n> <pass>` | Admin | Change player password |
| `CHUSER <old> <new>` | Admin | Rename player |
| `CHCLASS <n> <class>` | Admin | Change player class |
| `PAUSE` | Admin | Toggle pause mode |
| `SILENT <0-3>` | Admin | Set silent mode |
| `BACKUP` | Admin | Force database backup |
| `RELOADDB` | Admin | Reload DB (pause mode only) |
| `DIE` | Admin | Shut down bot |

## File Structure

```
idlerpg/
├── main.py           # Entry point
├── config.py         # Config loader (TOML → dataclasses)
├── config.toml       # Your configuration
├── events.txt        # Calamity/Godsend/Quest events
├── hof.json          # Hall of Fame (auto-created on first round end)
├── bot/
│   └── irc.py        # Async IRC client + command handler
├── db/
│   ├── models.py     # Player dataclass + DB serialization
│   └── store.py      # PlayerDB (in-memory + flat-file persistence)
├── game/
│   └── engine.py     # All game logic (levelling, combat, quests, events)
└── web/
    └── server.py     # aiohttp web app
```

## Database Compatibility

The flat-file format is **tab-delimited**, identical to the original Perl bot's `.db` file (32 columns per player row). You can drop an existing database in directly with no conversion step.

### Password migration

The original Perl bot stores passwords as `crypt()` hashes (DES, MD5 `$1$`, SHA-256 `$5$`, or SHA-512 `$6$`). This port uses its own `sha256$salt$digest` format but **handles legacy hashes transparently**:

- On `LOGIN`, if the stored hash is a legacy `crypt()` hash, the bot verifies it using Python's `crypt` module.
- If correct, the hash is **silently upgraded** to SHA-256 in-place and saved on the next DB write.
- Players notice nothing — they log in as normal and are never asked to reset their password.

> **Python 3.13+ note:** The `crypt` module was removed in Python 3.13. Install [`legacycrypt`](https://pypi.org/project/legacycrypt/) (`pip install legacycrypt`) and change `import crypt` to `import legacycrypt as crypt` in `db/models.py`. New installs with no legacy database are unaffected.

## Events File Format

Same as the original:

```
C <calamity text>                        # bad event
G <godsend text>                         # good event
Q1 <quest text>                          # timed quest
Q2 <x1> <y1> <x2> <y2> <quest text>     # navigation quest
```
