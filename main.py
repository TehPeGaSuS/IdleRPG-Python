#!/usr/bin/env python3
"""
IdleRPG Python Port – entry point.

Usage:
    python main.py [--config config.toml] [--setup] [--debug] [--verbose]

  --setup    Interactive first-run wizard to create initial admin account.
  --config   Path to TOML config file (default: config.toml)
  --debug    Enable DEBUG logging: raw IRC lines, queue details, tick events.
  --verbose  Enable INFO logging. (Default is WARNING — quiet in production.)
"""
from __future__ import annotations
import argparse
import asyncio
import getpass
import logging
import random
import sys
import time

if sys.version_info < (3, 11):
    print("ERROR: Python 3.11+ required (for tomllib).", file=sys.stderr)
    sys.exit(1)

import tomllib  # noqa: E402

from config import load as load_config
from db.models import Player, hash_password
from db.store import PlayerDB
from bot.irc import IRCBot
from web.server import make_app

log = logging.getLogger("idlerpg")


# ---------------------------------------------------------------------------
# First-run setup wizard
# ---------------------------------------------------------------------------

def setup_wizard(db: PlayerDB, cfg):
    print("\n=== IdleRPG First-Run Setup ===\n")
    default_name = cfg.bot.owner
    name = input(f"Admin account name [{default_name}]: ").strip() or default_name
    char_class = input("Character class: ").strip() or "God of Bots"
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("ERROR: Passwords do not match.")
        sys.exit(1)
    admin = Player(
        username=name,
        password=hash_password(password),
        is_admin=True,
        char_class=char_class,
        level=0,
        next_ttl=cfg.bot.rpbase,
        x=random.randint(0, cfg.bot.mapx),
        y=random.randint(0, cfg.bot.mapy),
    )
    db.add(admin)
    db.save()
    print(f"\nAccount '{name}' created and saved to {cfg.bot.db_file}.")
    print("Start the bot normally (without --setup) to connect.\n")


# ---------------------------------------------------------------------------
# Main async
# ---------------------------------------------------------------------------

async def main_async(cfg):
    db = PlayerDB(cfg.bot.db_file)
    bot = IRCBot(cfg, db)

    bot_task = asyncio.create_task(bot.start(), name="irc-bot")

    # Small delay so bot.engine is initialised before the web layer reads it
    await asyncio.sleep(0.5)

    from aiohttp import web
    app = make_app(db, bot.engine, cfg.web, cfg.network, channel=cfg.network.channel)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, cfg.web.host, cfg.web.port)
    await site.start()
    log.info("Web server listening on http://%s:%d", cfg.web.host, cfg.web.port)

    try:
        await bot_task
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="IdleRPG Python Bot")
    parser.add_argument("--config", default="config.toml",
                        help="Path to config.toml (default: config.toml)")
    parser.add_argument("--setup", action="store_true",
                        help="Run first-time setup wizard")
    parser.add_argument("--debug", "-d", action="store_true",
                        help="Enable DEBUG logging: raw IRC traffic, queue, ticks")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable INFO logging (less noisy than --debug)")
    args = parser.parse_args()

    # Logging level: --debug > --verbose > default (WARNING)
    if args.debug:
        log_level = logging.DEBUG
    elif args.verbose:
        log_level = logging.INFO
    else:
        log_level = logging.WARNING

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = load_config(args.config)

    if args.setup:
        db = PlayerDB(cfg.bot.db_file)
        setup_wizard(db, cfg)
        return

    try:
        asyncio.run(main_async(cfg))
    except KeyboardInterrupt:
        log.info("Stopped by user.")


if __name__ == "__main__":
    main()
