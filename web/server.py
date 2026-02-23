"""aiohttp web server for IdleRPG."""
from __future__ import annotations
import collections
import html
import math
import pathlib
import time
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from db.store import PlayerDB
    from game.engine import GameEngine

# ---------------------------------------------------------------------------
# Shared HTML chrome
# ---------------------------------------------------------------------------

STYLE = """<style>
*{box-sizing:border-box}
body{font-family:'Courier New',monospace;background:#0d0d1a;color:#c9d1d9;margin:0;padding:0;min-height:100vh}
header{background:#161b22;padding:10px 28px;border-bottom:2px solid #e94560;display:flex;align-items:center;gap:24px}
header h1{margin:0;color:#e94560;font-size:1.4em;letter-spacing:3px;white-space:nowrap}
nav{display:flex;gap:4px;flex-wrap:wrap}
nav a{color:#8b949e;text-decoration:none;padding:5px 12px;border-radius:4px;font-size:0.9em;border:1px solid transparent;transition:all .15s}
nav a:hover,nav a.active{color:#e94560;border-color:#e94560}
main{padding:28px;max-width:1100px;margin:0 auto}
h2{color:#a8dadc;border-bottom:1px solid #21262d;padding-bottom:8px;margin-top:0}
h3{color:#8b949e;margin-bottom:8px}
table{border-collapse:collapse;width:100%}
th{background:#161b22;color:#8b949e;padding:8px 14px;text-align:left;font-weight:normal;font-size:.85em;letter-spacing:1px;text-transform:uppercase;border-bottom:1px solid #21262d}
td{padding:7px 14px;border-bottom:1px solid #161b22;font-size:.9em}
td.lbl{color:#8b949e;width:160px;white-space:nowrap}
tr:hover td{background:#1a1f27}
a{color:#58a6ff;text-decoration:none}
a:hover{text-decoration:underline}
.badge{font-size:.75em;background:#e94560;color:#fff;padding:1px 7px;border-radius:10px;vertical-align:middle;margin-left:6px}
.online{color:#3fb950}
.offline{color:#484f58}
.good{color:#3fb950}.evil{color:#e94560}.neutral{color:#8b949e}
.card{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:20px;margin-bottom:20px}
.card p{margin:6px 0}
.label{color:#8b949e;display:inline-block;min-width:140px;font-size:.85em}
.cmd{background:#161b22;border:1px solid #21262d;border-radius:6px;padding:16px 20px;margin-bottom:12px}
.cmd code{color:#e94560;font-size:1em}
.cmd .desc{color:#8b949e;font-size:.85em;margin-top:4px}
.cmd .example{color:#58a6ff;font-size:.8em;margin-top:4px}
canvas{display:block;border:1px solid #21262d;border-radius:6px;cursor:crosshair}
.map-legend{display:flex;gap:16px;margin-top:8px;font-size:.8em;color:#8b949e;flex-wrap:wrap}
.map-legend span{display:flex;align-items:center;gap:5px}
.map-legend i{display:inline-block;width:10px;height:10px;border-radius:50%}
footer{text-align:center;color:#484f58;padding:28px;font-size:.8em;border-top:1px solid #161b22;margin-top:40px}
.ts{color:#484f58;font-size:.75em;float:right;clear:both}
.noquest{text-align:center;padding:60px 20px;color:#484f58}
.noquest .icon{font-size:3em;margin-bottom:12px}
.profile-grid{display:grid;grid-template-columns:1fr 320px;gap:24px;align-items:start;margin-bottom:24px}
.stats-items-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}
@media(max-width:720px){.profile-grid{grid-template-columns:1fr}.stats-items-grid{grid-template-columns:1fr}}
</style>"""

NAV_LINKS = [
    ("/",        "Home"),
    ("/players", "Player List"),
    ("/map",     "World Map"),
    ("/quest",   "Quest Info"),
    ("/admin",   "Admin Commands"),
]

def _nav(active: str) -> str:
    links = "".join(
        f'<a href="{href}"{"class=\"active\"" if href == active else ""}>{label}</a>'
        for href, label in NAV_LINKS
    )
    return f"<nav>{links}</nav>"

def _page(title: str, body: str, active: str = "/") -> web.Response:
    ts = time.strftime("Last updated: %Y-%m-%d %H:%M:%S UTC", time.gmtime())
    content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)} – IdleRPG</title>
  {STYLE}
</head>
<body>
<header>
  <h1>⚔ IdleRPG</h1>
  {_nav(active)}
</header>
<main>
{body}
<p class="ts">{ts}</p>
</main>
<footer>IdleRPG Python Port — refresh your browser to update data</footer>
</body></html>"""
    return web.Response(text=content, content_type="text/html")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _duration(secs: int) -> str:
    secs = max(0, int(secs))
    d = secs // 86400
    h = (secs % 86400) // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    return f"{d} days, {h:02d}:{m:02d}:{s:02d}"

def _pen(secs: int) -> str:
    """Format a penalty value: 'None' if zero, duration otherwise."""
    if secs == 0:
        return "None"
    return _duration(secs)

def _align(a: str) -> str:
    cls   = {"g": "good", "e": "evil"}.get(a, "neutral")
    label = {"g": "Good", "e": "Evil", "n": "Neutral"}.get(a, "Neutral")
    return f'<span class="{cls}">{label}</span>'

def _status(online: bool) -> str:
    return '<span class="online">Online</span>' if online else '<span class="offline">Offline</span>'

def _unique_name(item_val: str) -> str:
    """Return human-readable unique item name from suffix letter, or empty string."""
    suffix_map = {
        "a": "Mattt's Omniscience Grand Crown",
        "b": "Res0's Protectorate Plate Mail",
        "c": "Dwyn's Storm Magic Amulet",
        "d": "Jotun's Fury Colossal Sword",
        "e": "Drdink's Cane of Blind Rage",
        "f": "Mrquick's Magical Boots of Swiftness",
        "g": "Jeff's Cluehammer of Doom",
        "h": "Juliet's Glorious Ring of Sparkliness",
    }
    suffix = "".join(c for c in item_val if not c.isdigit())
    return suffix_map.get(suffix, "")

def _item_display(val: str) -> str:
    """Format item value, appending unique name in parens if applicable."""
    unique = _unique_name(val)
    level  = int(val) if val.isdigit() else int("".join(c for c in val if c.isdigit()) or "0")
    if unique:
        return f"{level} ({unique})"
    return str(level)


# ---------------------------------------------------------------------------
# Shared map canvas JS (used on /map and player profile)
# ---------------------------------------------------------------------------

def _map_canvas(canvas_id: str, width: int, height: int,
                players_json: str, waypoints_js: str,
                highlight_name: str, mapx: int, mapy: int) -> str:
    """Return a <canvas> + <script> block that draws the world map."""
    hl = highlight_name.replace("\\", "\\\\").replace('"', '\\"')
    return f"""
<canvas id="{canvas_id}" width="{width}" height="{height}" style="display:block;border:1px solid #21262d;border-radius:6px"></canvas>
<script>
(function(){{
  var canvas = document.getElementById('{canvas_id}');
  if (!canvas) {{ return; }}
  var ctx = canvas.getContext('2d');
  if (!ctx) {{ return; }}

  var players   = {players_json};
  var waypoints = {waypoints_js};
  var HIGHLIGHT = "{hl}";
  var MAPX = {mapx}, MAPY = {mapy};
  var W = {width}, H = {height};

  function toC(x, y) {{
    return [x / MAPX * W, y / MAPY * H];
  }}

  function drawDots() {{
    if (waypoints) {{
      var wps = [[waypoints.p1, 'WP1'], [waypoints.p2, 'WP2']];
      for (var wi = 0; wi < wps.length; wi++) {{
        var wp = wps[wi][0], lbl = wps[wi][1];
        var wc = toC(wp[0], wp[1]);
        ctx.fillStyle   = 'rgba(88,166,255,0.25)';
        ctx.strokeStyle = '#58a6ff';
        ctx.lineWidth   = 1.5;
        ctx.beginPath();
        ctx.rect(wc[0] - 7, wc[1] - 7, 14, 14);
        ctx.fill(); ctx.stroke();
        ctx.fillStyle = '#58a6ff';
        ctx.font      = 'bold 9px monospace';
        ctx.fillText(lbl, wc[0] + 9, wc[1] + 3);
      }}
    }}

    for (var i = 0; i < players.length; i++) {{
      var p  = players[i];
      var pc = toC(p.x, p.y);
      var cx = pc[0], cy = pc[1];
      var isHL  = (p.name === HIGHLIGHT);
      var color = isHL ? '#f7c948' : (p.online ? '#4a9eff' : '#e94560');
      var r     = isHL ? 7 : 4;

      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fillStyle   = color;
      ctx.strokeStyle = '#000000';
      ctx.lineWidth   = 1.5;
      ctx.fill(); ctx.stroke();

      if (isHL) {{
        ctx.fillStyle    = '#000000';
        ctx.font         = 'bold 11px monospace';
        var tw = ctx.measureText(p.name).width;
        ctx.fillRect(cx + 10, cy - 10, tw + 6, 16);
        ctx.fillStyle = '#f7c948';
        ctx.fillText(p.name, cx + 13, cy + 2);
      }}
    }}
  }}

  var bgImg = new Image();
  bgImg.onload = function() {{
    ctx.drawImage(bgImg, 0, 0, W, H);
    drawDots();
  }};
  bgImg.onerror = function() {{
    ctx.fillStyle = '#1a1f2e';
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = '#2a3040';
    ctx.lineWidth = 0.5;
    for (var gx = 0; gx <= W; gx += W/10) {{
      ctx.beginPath(); ctx.moveTo(gx,0); ctx.lineTo(gx,H); ctx.stroke();
    }}
    for (var gy = 0; gy <= H; gy += H/10) {{
      ctx.beginPath(); ctx.moveTo(0,gy); ctx.lineTo(W,gy); ctx.stroke();
    }}
    drawDots();
  }};
  bgImg.src = '/static/map_bg.png';

  var lastMx = -1, lastMy = -1;

  canvas.addEventListener('mousemove', function(e) {{
    var rect  = canvas.getBoundingClientRect();
    var mx    = (e.clientX - rect.left) * (W / rect.width);
    var my    = (e.clientY - rect.top)  * (H / rect.height);
    lastMx = mx; lastMy = my;
    var found = null, best = 8;
    for (var i = 0; i < players.length; i++) {{
      var pc = toC(players[i].x, players[i].y);
      var d  = Math.sqrt((pc[0]-mx)*(pc[0]-mx) + (pc[1]-my)*(pc[1]-my));
      if (d < best) {{ best = d; found = players[i]; }}
    }}
    redraw(found, mx, my);
  }});

  canvas.addEventListener('mouseleave', function() {{
    redraw(null, -1, -1);
  }});

  function redraw(found, mx, my) {{
    ctx.drawImage(bgImg, 0, 0, W, H);
    drawDots();
    if (found) {{
      var label = found.name + ' (' + found.x + ', ' + found.y + ')';
      ctx.font = 'bold 12px monospace';
      var tw  = ctx.measureText(label).width;
      var tx  = mx + 14;
      var ty  = my - 8;
      if (tx + tw + 8 > W) tx = mx - tw - 16;
      if (ty - 16 < 0)     ty = my + 20;
      ctx.fillStyle = 'rgba(0,0,0,0.75)';
      ctx.strokeStyle = '#f7c948';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.rect(tx - 4, ty - 14, tw + 8, 18);
      ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#f7c948';
      ctx.fillText(label, tx, ty);
    }}
  }}
}})();
</script>"""


def _build_map_data(db, engine, highlight: str = "") -> tuple:
    """Return (players_json, waypoints_js, mapx, mapy)."""
    mapx = engine.cfg.mapx if engine else 500
    mapy = engine.cfg.mapy if engine else 500

    quest_members = set(engine.quest.questers) if engine else set()
    parts = []
    for p in db.players.values():
        n = p.username.replace("\\", "\\\\").replace('"', '\\"')
        parts.append(
            f'{{"x":{p.x},"y":{p.y},"name":"{n}",'
            f'"online":{"true" if p.online else "false"},'
            f'"quester":{"true" if p.username in quest_members else "false"},'
            f'"admin":{"true" if p.is_admin else "false"}}}'
        )
    players_json = "[" + ",".join(parts) + "]"

    q = engine.quest if engine else None
    waypoints_js = "null"
    if q and q.is_active() and q.type == 2:
        waypoints_js = (f'{{"p1":[{q.p1[0]},{q.p1[1]}],'
                        f'"p2":[{q.p2[0]},{q.p2[1]}],'
                        f'"stage":{q.stage}}}')
    return players_json, waypoints_js, mapx, mapy


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    def __init__(self, window: int, limit: int):
        self.window = window
        self.limit  = limit
        self._hits: dict = collections.defaultdict(list)

    def is_allowed(self, ip: str) -> bool:
        now    = time.monotonic()
        cutoff = now - self.window
        hits   = [t for t in self._hits[ip] if t > cutoff]
        if len(hits) >= self.limit:
            self._hits[ip] = hits
            return False
        hits.append(now)
        self._hits[ip] = hits
        return True


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def make_app(db: "PlayerDB", engine: "GameEngine", wcfg,
             channel: str = "#idlerpg") -> web.Application:
    limiter = RateLimiter(wcfg.rate_window, wcfg.rate_limit)

    @web.middleware
    async def rate_limit_mw(request, handler):
        ip = request.remote or "unknown"
        if not limiter.is_allowed(ip):
            return web.Response(status=429, text="Rate limit exceeded. Try again shortly.")
        return await handler(request)

    app = web.Application(middlewares=[rate_limit_mw])

    # -----------------------------------------------------------------------
    # / — Home
    # -----------------------------------------------------------------------
    async def home(request):
        online = sum(1 for p in db.players.values() if p.online)
        total  = len(db.players)

        ranked = sorted(db.players.values(), key=lambda p: (-p.level, p.next_ttl))
        medals = ["🥇", "🥈", "🥉"]
        top_rows = "".join(
            f"<tr><td>{medals[i]}</td>"
            f"<td><a href='/player/{html.escape(p.username)}'>{html.escape(p.username)}</a></td>"
            f"<td>{html.escape(p.char_class)}</td>"
            f"<td>{p.level}</td>"
            f"<td>{_duration(p.next_ttl)}</td></tr>"
            for i, p in enumerate(ranked[:3])
        )
        top_section = (
            f"<h2>Top Players</h2>"
            f"<table><tr><th></th><th>Name</th><th>Class</th><th>Level</th><th>Next Level</th></tr>"
            f"{top_rows}</table>"
        ) if top_rows else ""

        body = f"""
<h2>Welcome to Idle RPG</h2>
<div class="card">
  <p>IdleRPG is a game where the goal is simple: <strong>do nothing</strong>.
  The longer you idle in <strong>{html.escape(channel)}</strong>,
  the more powerful your character becomes. Talking, changing your nick,
  parting, or quitting all add <em>penalty time</em> to your clock.</p>
  <p><span class="label">Players online:</span> <span class="online">{online}</span> / {total}</p>
</div>

{top_section}
<br/>
<h2>How to Play</h2>
<div class="cmd">
  <code>REGISTER &lt;name&gt; &lt;password&gt; &lt;class&gt;</code>
  <div class="desc">Create a new character. Class can be anything — "Wizard of the North", "Space Cowboy", etc.</div>
  <div class="example">Example: /msg IdleRPGbot REGISTER Thorin s3cr3t Dwarf King of Erebor</div>
</div>
<div class="cmd">
  <code>LOGIN &lt;name&gt; &lt;password&gt;</code>
  <div class="desc">Log in to your existing character (send as a /msg to the bot, not in the channel).</div>
</div>
<div class="cmd">
  <code>LOGOUT</code>
  <div class="desc">Log out. This carries a time penalty, so avoid it if you can.</div>
</div>
<div class="cmd">
  <code>NEWPASS &lt;password&gt;</code>
  <div class="desc">Change your password.</div>
</div>
<div class="cmd">
  <code>ALIGN &lt;good|neutral|evil&gt;</code>
  <div class="desc">Set your alignment. Good players get a combat bonus; evil players can steal items but also suffer divine wrath.</div>
</div>
<div class="cmd">
  <code>WHOAMI</code>
  <div class="desc">Check your current level and time to next level.</div>
</div>
<div class="cmd">
  <code>STATUS [name]</code>
  <div class="desc">View your stats, or another player's stats.</div>
</div>
<div class="cmd">
  <code>QUEST</code>
  <div class="desc">Check the current active quest.</div>
</div>
<div class="cmd">
  <code>REMOVEME</code>
  <div class="desc">Permanently delete your character.</div>
</div>

<h2>Penalties</h2>
<div class="card">
  <p><span class="label">Talking in channel:</span> penalty per character typed</p>
  <p><span class="label">Nick change:</span> 30 × penalty multiplier</p>
  <p><span class="label">Parting channel:</span> 200 × penalty multiplier</p>
  <p><span class="label">Being kicked:</span> 250 × penalty multiplier</p>
  <p><span class="label">Quitting IRC:</span> 20 × penalty multiplier</p>
  <p><span class="label">Logging out:</span> 20 × penalty multiplier</p>
  <p><span class="label">Abandoning a quest:</span> penalty applied to <em>all</em> online players</p>
</div>

"""
        return _page("Home", body, active="/")

    # -----------------------------------------------------------------------
    # /players — Player list
    # -----------------------------------------------------------------------
    async def player_list(request):
        players = sorted(db.players.values(), key=lambda p: (-p.level, p.next_ttl))
        rows = "".join(
            f"<tr>"
            f"<td>{i}</td>"
            f"<td><a href='/player/{html.escape(p.username)}'>{html.escape(p.username)}</a>"
            f"{'<span class=\"badge\">Admin</span>' if p.is_admin else ''}</td>"
            f"<td>{html.escape(p.char_class)}</td>"
            f"<td>{p.level}</td>"
            f"<td>{_duration(p.next_ttl)}</td>"
            f"<td>{p.item_sum()}</td>"
            f"<td>{_align(p.alignment)}</td>"
            f"<td>{_status(p.online)}</td>"
            f"</tr>"
            for i, p in enumerate(players, 1)
        )
        online = sum(1 for p in db.players.values() if p.online)
        body = (
            f"<h2>Player List "
            f"<span style='font-size:.6em;color:#8b949e;font-weight:normal'>"
            f"{online} online / {len(db.players)} total</span></h2>"
            "<table>"
            "<tr><th>#</th><th>Name</th><th>Class</th><th>Level</th>"
            "<th>Next Level</th><th>Item Sum</th><th>Alignment</th><th>Status</th></tr>"
            + rows + "</table>"
        )
        return _page("Player List", body, active="/players")

    # -----------------------------------------------------------------------
    # /player/<name> — Player profile (matches reference screenshot layout)
    # -----------------------------------------------------------------------
    async def player_detail(request):
        name = request.match_info["name"]
        p    = db.get(name)
        if not p:
            return _page("Not Found",
                f"<h2>Player Not Found</h2><p>No player named <b>{html.escape(name)}</b>.</p>")

        # Mini-map: only this player's dot, nobody else
        solo_json = (
            f'[{{"x":{p.x},"y":{p.y},"name":"{p.username.replace(chr(92), chr(92)*2).replace(chr(34), chr(92)+chr(34))}",'
            f'"quester":false,"admin":false}}]'
        )
        mini_map = _map_canvas(
            canvas_id      = "minimap",
            width          = 300,
            height         = 300,
            players_json   = solo_json,
            waypoints_js   = "null",
            highlight_name = p.username,
            mapx           = engine.cfg.mapx if engine else 500,
            mapy           = engine.cfg.mapy if engine else 500,
        )

        # Items table — display name (slot label) : level, unique name if applicable
        slot_labels = {
            "amulet":          "Amulet",
            "charm":           "Charm",
            "helm":            "Helm",
            "pair of boots":   "Boots",
            "pair of gloves":  "Gloves",
            "ring":            "Ring",
            "set of leggings": "Leggings",
            "shield":          "Shield",
            "tunic":           "Tunic",
            "weapon":          "Weapon",
        }
        item_rows = "".join(
            f"<tr><td class='lbl'>{slot_labels.get(slot, slot)}</td>"
            f"<td>{_item_display(p.items[slot])}</td></tr>"
            for slot in sorted(p.items, key=lambda s: slot_labels.get(s, s))
        )

        # Penalties — same order as the reference screenshot
        pen_total = (p.pen_mesg + p.pen_nick + p.pen_part +
                     p.pen_kick + p.pen_quit + p.pen_quest + p.pen_logout)
        pen_rows = (
            f"<tr><td class='lbl'>Kick</td><td>{_pen(p.pen_kick)}</td></tr>"
            f"<tr><td class='lbl'>Logout</td><td>{_pen(p.pen_logout)}</td></tr>"
            f"<tr><td class='lbl'>Mesg</td><td>{_pen(p.pen_mesg)}</td></tr>"
            f"<tr><td class='lbl'>Nick</td><td>{_pen(p.pen_nick)}</td></tr>"
            f"<tr><td class='lbl'>Part</td><td>{_pen(p.pen_part)}</td></tr>"
            f"<tr><td class='lbl'>Quest</td><td>{_pen(p.pen_quest)}</td></tr>"
            f"<tr><td class='lbl'>Quit</td><td>{_pen(p.pen_quit)}</td></tr>"
            f"<tr style='border-top:1px solid #21262d'>"
            f"<td class='lbl'><strong>Total</strong></td>"
            f"<td><strong>{_pen(pen_total)}</strong></td></tr>"
        )

#        host = p.userhost or "—"
        host = f"{p.nick}!{p.userhost}" if p.userhost else "—"

        body = f"""
<h2>View Stats ({html.escape(p.username)}
  {'<span class="badge">Admin</span>' if p.is_admin else ''})</h2>

<div class="profile-grid">
  <table>
    <tr><td class="lbl">User</td><td>{html.escape(p.username)}</td></tr>
    <tr><td class="lbl">Class</td><td>{html.escape(p.char_class)}</td></tr>
    <tr><td class="lbl">Level</td><td>{p.level}</td></tr>
    <tr><td class="lbl">Next Level</td><td>{_duration(p.next_ttl)}</td></tr>
    <tr><td class="lbl">Status</td><td>{_status(p.online)}</td></tr>
    <tr><td class="lbl">Host</td><td>{html.escape(host)}</td></tr>
    <tr><td class="lbl">Account Created</td><td>{time.strftime('%a %dst %b %Y a %H:%M:%S', time.localtime(p.created))}</td></tr>
    <tr><td class="lbl">Last Login</td><td>{time.strftime('%a %dst %b %Y a %H:%M:%S', time.localtime(p.last_login))}</td></tr>
    <tr><td class="lbl">Total Time Idled</td><td>{_duration(p.idled)}</td></tr>
    <tr><td class="lbl">Current Position</td><td>{p.x}, {p.y}</td></tr>
    <tr><td class="lbl">Alignment</td><td>{_align(p.alignment)}</td></tr>
  </table>
  <div>
    {mini_map}
  </div>
</div>

<div class="stats-items-grid">
  <div>
    <h3>Items</h3>
    <table>{item_rows}</table>
  </div>
  <div>
    <h3>Penalties</h3>
    <table>{pen_rows}</table>
  </div>
</div>
"""
        return _page(f"View Stats ({p.username})", body, active="/players")

    # -----------------------------------------------------------------------
    # /map — Full world map
    # -----------------------------------------------------------------------
    async def world_map(request):
        players_json, waypoints_js, mapx, mapy = _build_map_data(db, engine)
        online = sum(1 for p in db.players.values() if p.online)

        map_canvas = _map_canvas(
            canvas_id      = "worldmap",
            width          = 800,
            height         = 600,
            players_json   = players_json,
            waypoints_js   = waypoints_js,
            highlight_name = "",
            mapx           = mapx,
            mapy           = mapy,
        )

        body = f"""
<h2>World Map</h2>
<p style="color:#8b949e;font-size:.85em">
  {sum(1 for p in db.players.values() if p.online)} of {len(db.players)} players online &mdash; hover a dot to see the name.
</p>
{map_canvas}
<div class="map-legend">
  <span><i style="background:#4a9eff"></i> Online</span>
  <span><i style="background:#e94560"></i> Offline</span>
  <span><i style="background:#58a6ff;border-radius:2px"></i> Quest waypoint</span>
</div>
"""
        return _page("World Map", body, active="/map")

    # -----------------------------------------------------------------------
    # /quest — Quest info
    # -----------------------------------------------------------------------
    async def quest_page(request):
        q = engine.quest if engine else None
        if not q or not q.is_active():
            body = """
<h2>Quest Info</h2>
<div class="noquest">
  <div class="icon">🗺</div>
  <p>No active quest right now.</p>
  <p style="font-size:.85em">Quests begin when 4 players of level 40+ have been online for 10+ hours.</p>
</div>"""
        else:
            names_html = ", ".join(
                f'<a href="/player/{html.escape(u)}">{html.escape(u)}</a>'
                for u in q.questers
            )
            if q.type == 1:
                progress = (
                    f"<p><span class='label'>Type</span> Timed</p>"
                    f"<p><span class='label'>Time remaining</span> {_duration(q.qtime - int(time.time()))}</p>"
                )
            else:
                stage_label = "Traveling to Waypoint 1" if q.stage == 1 else "Traveling to Waypoint 2"
                pos_rows = ""
                for u in q.questers:
                    qp = db.get(u)
                    if qp:
                        tx, ty = (q.p1 if q.stage == 1 else q.p2)
                        dist   = math.hypot(qp.x - tx, qp.y - ty)
                        pos_rows += (
                            f"<tr>"
                            f"<td><a href='/player/{html.escape(u)}'>{html.escape(u)}</a></td>"
                            f"<td>({qp.x}, {qp.y})</td>"
                            f"<td>({tx}, {ty})</td>"
                            f"<td>{dist:.1f}</td>"
                            f"</tr>"
                        )
                progress = (
                    f"<p><span class='label'>Type</span> Navigation</p>"
                    f"<p><span class='label'>Stage</span> {html.escape(stage_label)}</p>"
                    f"<p><span class='label'>Waypoint 1</span> ({q.p1[0]}, {q.p1[1]})</p>"
                    f"<p><span class='label'>Waypoint 2</span> ({q.p2[0]}, {q.p2[1]})</p>"
                    f"<h3>Quester Positions</h3>"
                    f"<table><tr><th>Player</th><th>Position</th><th>Target</th><th>Distance</th></tr>"
                    f"{pos_rows}</table>"
                )

            body = f"""
<h2>Quest Info</h2>
<div class="card">
  <p><span class="label">Questers</span> {names_html}</p>
  <p><span class="label">Quest</span> {html.escape(q.text)}</p>
  {progress}
</div>
<p style="color:#8b949e;font-size:.85em">
  Completing a quest removes 25% of each quester's time-to-next-level.
  Abandoning it punishes <em>all</em> online players.
</p>"""

        return _page("Quest Info", body, active="/quest")

    # -----------------------------------------------------------------------
    # /admin — Admin command reference
    # -----------------------------------------------------------------------
    async def admin_page(request):
        def cmd(syntax, desc, example=""):
            ex = (f'<div class="example">Example: /msg IdleRPGbot {html.escape(example)}</div>'
                  if example else "")
            return (f'<div class="cmd"><code>{html.escape(syntax)}</code>'
                    f'<div class="desc">{desc}</div>{ex}</div>')

        body = f"""
<h2>Admin Commands</h2>
<p style="color:#8b949e">All commands are sent via <code>/msg IdleRPGbot &lt;command&gt;</code>.
Admin access is required for all commands on this page.</p>

<h3>Player Management</h3>
{cmd("DEL <n>", "Permanently delete a player account.", "DEL Badguy")}
{cmd("DELOLD <days>", "Delete all accounts not accessed in the last N days (offline only).", "DELOLD 30")}
{cmd("CHPASS <n> <newpass>", "Change a player's password.", "CHPASS Thorin newpassword")}
{cmd("CHUSER <oldname> <newname>", "Rename a player account.", "CHUSER Thorin Thorin_Oakenshield")}
{cmd("CHCLASS <n> <class>", "Change a player's character class.", "CHCLASS Thorin Dwarf Lord")}
{cmd("PUSH <n> <seconds>", "Add or remove seconds from a player's next-level timer. Negative values speed them up.", "PUSH Thorin -300")}
{cmd("MKADMIN <n>", "Grant admin status to a player account.")}
{cmd("DELADMIN <n>", "Revoke admin status from a player account.")}

<h3>Bot Control</h3>
{cmd("HOG", "Summon the Hand of God — randomly blesses or punishes an online player.")}
{cmd("PAUSE", "Toggle pause mode. In pause mode the DB is not written and new registrations are blocked.")}
{cmd("SILENT <0-3>", "Set silent mode: 0=all, 1=no channel msgs, 2=no private msgs, 3=silent.", "SILENT 1")}
{cmd("BACKUP", "Force an immediate backup of the player database.")}
{cmd("RELOADDB", "Reload the player database from disk. Only works while in PAUSE mode.")}
{cmd("DIE", "Disconnect the bot and shut it down (no reconnect).")}

<h3>Information</h3>
{cmd("INFO", "Display bot stats: bytes sent/received, player counts, queue size, uptime.")}

<p style="color:#8b949e;margin-top:24px;font-size:.85em">
  Owner-only commands (controlled by <code>owner_add_only</code> / <code>owner_del_only</code> in config):
  <strong>MKADMIN</strong>, <strong>DELADMIN</strong>.
</p>
"""
        return _page("Admin Commands", body, active="/admin")

    # -----------------------------------------------------------------------
    # Routes
    # -----------------------------------------------------------------------
    app.router.add_get("/",              home)
    app.router.add_get("/players",       player_list)
    app.router.add_get("/player/{name}", player_detail)
    app.router.add_get("/map",           world_map)
    app.router.add_get("/quest",         quest_page)
    app.router.add_get("/admin",         admin_page)
    app.router.add_static("/static",     pathlib.Path(__file__).parent)

    return app
