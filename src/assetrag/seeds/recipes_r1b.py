"""Round-1 persona recipes, part B: games, embedded/hardware, data, bots, docs sites (Python stdlib), UI logic (TypeScript,
node --test) and a Go interactive prompt. Hardware recipes test the *logic* (control maths, framing, filtering) - nothing
here talks to real devices, and the evidence says so."""
from .recipes import RC, UT, GOT
from .recipes_algo import NODE_TS, TSA

HW = "logic only: no real device, bus or timing hardware is exercised in the sandbox"

R1B = [
# ------------------------------------------------------------------ games
RC("py-game-fixed-timestep-loop", "Fixed-timestep game loop with interpolation (deterministic updates)", "python",
   "A game or simulation behaves differently at 30 vs 144 fps, or physics explodes after a lag spike.",
   "Accumulator pattern: update in fixed dt steps while accumulated time allows, clamp the frame time to avoid the spiral of death, render with an interpolation alpha; the clock is injectable so the loop is testable and replayable.",
   {"loop.py": '''"""Fixed timestep loop: simulation is deterministic regardless of frame rate."""


class Loop:
    def __init__(self, update, dt=1 / 60, max_frame=0.25):
        self.update, self.dt, self.max_frame = update, dt, max_frame
        self.acc = 0.0
        self.steps = 0

    def frame(self, elapsed):
        """Feed real elapsed seconds; returns the render interpolation alpha in [0, 1)."""
        self.acc += min(elapsed, self.max_frame)          # clamp: a 5 s hitch must not trigger 300 catch-up updates
        while self.acc >= self.dt:
            self.update(self.dt)
            self.acc -= self.dt
            self.steps += 1
        return self.acc / self.dt
'''
    , "test_loop.py": '''import unittest
from loop import Loop


def simulate(frame_times):
    pos = [0.0]
    lp = Loop(lambda dt: pos.__setitem__(0, pos[0] + 10 * dt))
    for f in frame_times: lp.frame(f)
    return pos[0], lp.steps


class T(unittest.TestCase):
    def test_same_result_at_different_frame_rates(self):
        a, _ = simulate([1 / 30] * 60)           # 2 s at 30 fps
        b, _ = simulate([1 / 144] * 288)         # 2 s at 144 fps
        self.assertAlmostEqual(a, b, delta=2 * 10 / 60)       # at most one fixed step of quantisation apart

    def test_steps_match_elapsed(self):
        _, steps = simulate([1 / 60] * 120)
        self.assertIn(steps, (119, 120))

    def test_spiral_of_death_clamped(self):
        lp = Loop(lambda dt: None); lp.frame(5.0)
        self.assertLessEqual(lp.steps, 15)

    def test_alpha_in_range(self):
        lp = Loop(lambda dt: None)
        for f in (0.001, 0.02, 0.016, 0.5):
            a = lp.frame(f); self.assertTrue(0 <= a < 1)

    def test_no_update_below_dt(self):
        lp = Loop(lambda dt: None); lp.frame(0.001); self.assertEqual(lp.steps, 0)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Rendering is separate from update", "Inputs are sampled per update"], compat={"python": ">=3.9"},
   tags=["game", "game-loop", "timestep", "physics", "simulation", "pygame", "godot"],
   avoid="Multiplying by variable delta time in physics (non-deterministic, tunnels through walls)."),

RC("py-ecs-minimal", "Minimal entity-component-system (ECS) with queries and safe despawn", "python",
   "A game has grown into inheritance spaghetti; entities need composable data and systems that iterate over what they care about.",
   "Entities are integer ids, components are plain dataclasses stored per type, queries iterate the smallest component set and intersect, despawns are deferred to the end of the frame so systems can remove entities while iterating.",
   {"ecs.py": '''"""Tiny ECS. Components are keyed by type; despawn is deferred until flush()."""
from collections import defaultdict
from dataclasses import dataclass


class World:
    def __init__(self):
        self._next = 1
        self._store = defaultdict(dict)      # component type -> {entity: component}
        self._dead = set()

    def spawn(self, *components):
        e = self._next; self._next += 1
        for c in components: self.add(e, c)
        return e

    def add(self, e, c):
        self._store[type(c)][e] = c

    def get(self, e, kind):
        return self._store[kind].get(e)

    def remove(self, e, kind):
        self._store[kind].pop(e, None)

    def despawn(self, e):
        self._dead.add(e)

    def flush(self):
        for e in self._dead:
            for comps in self._store.values(): comps.pop(e, None)
        n, self._dead = len(self._dead), set()
        return n

    def query(self, *kinds):
        """Yield (entity, *components) for live entities that have ALL kinds."""
        if not kinds: return
        smallest = min(kinds, key=lambda k: len(self._store[k]))
        for e in list(self._store[smallest]):
            if e in self._dead: continue
            comps = [self._store[k].get(e) for k in kinds]
            if all(c is not None for c in comps): yield (e, *comps)


@dataclass
class Pos:
    x: float; y: float
@dataclass
class Vel:
    dx: float; dy: float
@dataclass
class Health:
    hp: int


def movement(world, dt):
    for _, p, v in world.query(Pos, Vel):
        p.x += v.dx * dt; p.y += v.dy * dt


def reaper(world):
    for e, h in world.query(Health):
        if h.hp <= 0: world.despawn(e)
'''
    , "test_ecs.py": '''import unittest
from ecs import World, Pos, Vel, Health, movement, reaper


class T(unittest.TestCase):
    def test_query_requires_all_components(self):
        w = World(); a = w.spawn(Pos(0, 0), Vel(1, 0)); w.spawn(Pos(5, 5))
        self.assertEqual([e for e, *_ in w.query(Pos, Vel)], [a])

    def test_system_moves_only_matching(self):
        w = World(); a = w.spawn(Pos(0, 0), Vel(2, 1)); b = w.spawn(Pos(9, 9))
        movement(w, 0.5)
        self.assertEqual((w.get(a, Pos).x, w.get(a, Pos).y), (1.0, 0.5)); self.assertEqual(w.get(b, Pos).x, 9)

    def test_despawn_during_iteration_is_safe(self):
        w = World(); es = [w.spawn(Health(0 if i % 2 else 5)) for i in range(10)]
        reaper(w); self.assertEqual(len(list(w.query(Health))), 5)      # dead hidden immediately
        self.assertEqual(w.flush(), 5)
        self.assertEqual(len(list(w.query(Health))), 5)

    def test_flush_removes_all_components(self):
        w = World(); e = w.spawn(Pos(0, 0), Vel(0, 0)); w.despawn(e); w.flush()
        self.assertIsNone(w.get(e, Pos)); self.assertIsNone(w.get(e, Vel))

    def test_add_remove_component(self):
        w = World(); e = w.spawn(Pos(0, 0)); w.add(e, Vel(1, 1)); self.assertEqual(len(list(w.query(Pos, Vel))), 1)
        w.remove(e, Vel); self.assertEqual(len(list(w.query(Pos, Vel))), 0)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Not cache-friendly (dict storage); use bevy/flecs/esper for large worlds"], compat={"python": ">=3.9"},
   tags=["game", "ecs", "entity-component", "architecture", "bevy", "simulation"],
   avoid="Deep inheritance hierarchies for game objects; mutating the entity set while iterating it."),

RC("py-aabb-collision-resolve", "AABB collision detection and axis-separated resolution (no tunnelling)", "python",
   "A platformer/top-down game: the player sticks to walls, falls through floors or gets stuck in corners.",
   "Move on one axis at a time, resolve against solid boxes after each axis, and sweep the movement into steps no larger than the smallest box so fast bodies cannot tunnel; report which side was hit (grounded, wall).",
   {"collide.py": '''"""Axis-separated AABB movement. Boxes are (x, y, w, h); y grows downward."""


def overlap(a, b):
    return a[0] < b[0] + b[2] and a[0] + a[2] > b[0] and a[1] < b[1] + b[3] and a[1] + a[3] > b[1]


def move(box, dx, dy, solids, max_step=4.0):
    """-> (new_box, hits) where hits is a set of 'left','right','up','down'. Sub-steps stop tunnelling at high speed."""
    x, y, w, h = box
    hits = set()
    steps = max(1, int(max(abs(dx), abs(dy)) // max_step) + 1)
    sx, sy = dx / steps, dy / steps
    for _ in range(steps):
        x += sx
        for s in solids:
            if overlap((x, y, w, h), s):
                if sx > 0: x = s[0] - w; hits.add("right")
                elif sx < 0: x = s[0] + s[2]; hits.add("left")
                sx = 0
        y += sy
        for s in solids:
            if overlap((x, y, w, h), s):
                if sy > 0: y = s[1] - h; hits.add("down")
                elif sy < 0: y = s[1] + s[3]; hits.add("up")
                sy = 0
    return (x, y, w, h), hits
'''
    , "test_collide.py": '''import unittest
from collide import move, overlap

FLOOR = (0, 100, 200, 10)
WALL = (50, 0, 10, 100)


class T(unittest.TestCase):
    def test_lands_on_floor_and_reports_grounded(self):
        b, hits = move((10, 80, 10, 10), 0, 30, [FLOOR])
        self.assertEqual(b[1], 90); self.assertIn("down", hits)

    def test_fast_body_does_not_tunnel(self):
        b, hits = move((10, 0, 10, 10), 0, 500, [FLOOR])          # 500 px in one frame through a 10 px floor
        self.assertEqual(b[1], 90)

    def test_wall_blocks_x_but_not_y(self):
        b, hits = move((30, 50, 10, 10), 30, 5, [WALL])
        self.assertEqual(b[0], 40); self.assertEqual(b[1], 55); self.assertIn("right", hits)

    def test_slides_along_floor_without_sticking(self):
        b, _ = move((10, 90, 10, 10), 20, 1, [FLOOR])
        self.assertAlmostEqual(b[0], 30)

    def test_ceiling(self):
        b, hits = move((10, 20, 10, 10), 0, -50, [(0, 0, 100, 10)])
        self.assertEqual(b[1], 10); self.assertIn("up", hits)

    def test_touching_is_not_overlap(self):
        self.assertFalse(overlap((0, 0, 10, 10), (10, 0, 10, 10)))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Axis-aligned rectangles only", "Moving solids and slopes need more (swept AABB / physics engine)"], compat={"python": ">=3.9"},
   tags=["game", "collision", "aabb", "platformer", "physics", "2d", "tunnelling"],
   avoid="Moving diagonally and resolving once (corner sticking); one huge step per frame at high speed."),

RC("py-xp-levels-cooldowns", "XP levels curve and per-user cooldowns for chat bots", "python",
   "A Discord/Twitch bot with levels: XP can't be farmed by spamming, level maths is monotonic and invertible, cooldowns are per user per command.",
   "Level thresholds from a quadratic curve with an exact integer inverse, XP awarded at most once per cooldown window (injectable clock), caps on per-award XP, and a leaderboard sorted with deterministic tie-breaks.",
   {"levels.py": '''"""XP/level maths + cooldowns. Pure functions and a tiny state object; persistence is up to the bot."""
import math


def xp_for_level(level):
    return 50 * level * level + 50 * level          # level 0 = 0 xp, 1 = 100, 2 = 300, 3 = 600 ...


def level_for_xp(xp):
    if xp < 0: return 0
    # solve 50L^2 + 50L <= xp exactly
    lvl = int((-50 + math.sqrt(2500 + 200 * xp)) // 100)
    while xp_for_level(lvl + 1) <= xp: lvl += 1
    while lvl > 0 and xp_for_level(lvl) > xp: lvl -= 1
    return lvl


class Tracker:
    def __init__(self, cooldown=60, award=15, clock=None):
        import time
        self.cooldown, self.award, self.clock = cooldown, award, clock or time.monotonic
        self.xp, self.last = {}, {}

    def message(self, user):
        """Award XP for a chat message unless the user is on cooldown. -> (awarded, leveled_up_to or None)"""
        now = self.clock()
        if now - self.last.get(user, -1e18) < self.cooldown:
            return 0, None
        self.last[user] = now
        before = level_for_xp(self.xp.get(user, 0))
        self.xp[user] = self.xp.get(user, 0) + self.award
        after = level_for_xp(self.xp[user])
        return self.award, (after if after > before else None)

    def leaderboard(self, n=10):
        return sorted(self.xp.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
'''
    , "test_levels.py": '''import unittest
from levels import xp_for_level, level_for_xp, Tracker


class T(unittest.TestCase):
    def test_inverse_is_exact_everywhere(self):
        for lvl in range(0, 200):
            self.assertEqual(level_for_xp(xp_for_level(lvl)), lvl)
            if lvl: self.assertEqual(level_for_xp(xp_for_level(lvl) - 1), lvl - 1)

    def test_monotonic(self):
        prev = 0
        for xp in range(0, 5000, 7):
            l = level_for_xp(xp); self.assertGreaterEqual(l, prev); prev = l

    def test_cooldown_blocks_spam(self):
        t = [0.0]; tr = Tracker(cooldown=60, clock=lambda: t[0])
        self.assertEqual(tr.message("u")[0], 15)
        t[0] = 30; self.assertEqual(tr.message("u")[0], 0)
        t[0] = 61; self.assertEqual(tr.message("u")[0], 15)

    def test_level_up_reported_once(self):
        t = [0.0]; tr = Tracker(cooldown=1, award=100, clock=lambda: t[0])
        t[0] = 10; self.assertEqual(tr.message("u"), (100, 1))          # 100 xp -> level 1
        t[0] = 20; self.assertEqual(tr.message("u"), (100, None))       # 200 xp, still level 1
        t[0] = 30; self.assertEqual(tr.message("u"), (100, 2))          # 300 xp -> level 2

    def test_users_are_independent(self):
        t = [0.0]; tr = Tracker(clock=lambda: t[0]); tr.message("a"); self.assertEqual(tr.message("b")[0], 15)

    def test_leaderboard_ties_deterministic(self):
        tr = Tracker(cooldown=0); tr.xp = {"b": 10, "a": 10, "c": 99}
        self.assertEqual([u for u, _ in tr.leaderboard()], ["c", "a", "b"])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["State is persisted by the caller", "Uses math.sqrt then corrects with integer checks"], compat={"python": ">=3.9"},
   tags=["discord", "twitch", "bot", "xp", "levels", "cooldown", "game", "leaderboard"],
   avoid="Awarding XP per message with no cooldown; floating-point level formulas that disagree with their inverse."),

# ------------------------------------------------------------------ bots
RC("py-chat-command-parser", "Chat bot command parser: prefix, quoted args, flags, aliases, permissions", "python",
   "A Discord/Telegram/Slack/IRC bot needs `!cmd arg \"quoted arg\" --flag=value` parsed safely with helpful errors.",
   "shlex-based tokenizer with a strict prefix check, registry with aliases and per-command role/min-args, unknown commands answered with a suggestion (difflib), parse errors turned into user-readable messages, never eval.",
   {"botcmd.py": '''"""Chat command dispatcher. Transport-agnostic: feed it message text, it returns the reply text."""
import difflib, shlex


class Bot:
    def __init__(self, prefix="!"):
        self.prefix, self.cmds, self.alias = prefix, {}, {}

    def command(self, name, aliases=(), min_args=0, role=None):
        def deco(fn):
            self.cmds[name] = dict(fn=fn, min_args=min_args, role=role)
            for a in aliases: self.alias[a] = name
            return fn
        return deco

    def handle(self, text, user_roles=()):
        if not text.startswith(self.prefix):
            return None                                       # not for us: never reply to normal chatter
        try:
            tokens = shlex.split(text[len(self.prefix):])
        except ValueError:
            return "Unbalanced quotes in that command."
        if not tokens:
            return None
        name = self.alias.get(tokens[0], tokens[0])
        if name not in self.cmds:
            close = difflib.get_close_matches(tokens[0], list(self.cmds) + list(self.alias), n=1)
            return f"Unknown command. Did you mean {self.prefix}{close[0]}?" if close else "Unknown command."
        spec = self.cmds[name]
        if spec["role"] and spec["role"] not in user_roles:
            return "You are not allowed to use that."
        args = [t for t in tokens[1:] if not t.startswith("--")]
        flags = dict((t[2:].partition("=")[0], t.partition("=")[2] or True) for t in tokens[1:] if t.startswith("--"))
        if len(args) < spec["min_args"]:
            return f"Usage: {self.prefix}{name} needs at least {spec['min_args']} argument(s)."
        return spec["fn"](args, flags)
'''
    , "test_botcmd.py": '''import unittest
from botcmd import Bot

b = Bot()
b.command("echo", aliases=["say"], min_args=1)(lambda a, f: " ".join(a) + ("!" if f.get("loud") else ""))
b.command("ban", min_args=1, role="mod")(lambda a, f: f"banned {a[0]}")


class T(unittest.TestCase):
    def test_ignores_normal_chat(self): self.assertIsNone(b.handle("hello everyone"))
    def test_basic_and_alias(self): self.assertEqual(b.handle("!echo hi there"), "hi there"); self.assertEqual(b.handle("!say yo"), "yo")
    def test_quoted_args(self): self.assertEqual(b.handle('!echo "two words" x'), "two words x")
    def test_flags(self): self.assertEqual(b.handle("!echo a --loud"), "a!")
    def test_unbalanced_quote_is_a_message_not_a_crash(self): self.assertIn("Unbalanced", b.handle('!echo "oops'))
    def test_suggestion(self): self.assertIn("!echo", b.handle("!ech hi"))
    def test_unknown_without_suggestion(self): self.assertEqual(b.handle("!zzzzzzz"), "Unknown command.")
    def test_role_required(self): self.assertIn("not allowed", b.handle("!ban bob")); self.assertEqual(b.handle("!ban bob", ["mod"]), "banned bob")
    def test_min_args(self): self.assertIn("at least 1", b.handle("!echo"))
    def test_only_prefix(self): self.assertIsNone(b.handle("!"))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Transport (discord.py, telegram, slack) feeds text in and sends the reply out"], compat={"python": ">=3.9"},
   tags=["bot", "discord", "telegram", "slack", "irc", "twitch", "command", "chat", "parser"],
   avoid="str.split on user text (breaks quotes); replying to every message; eval/exec of chat text."),

RC("py-rss-poller-dedupe", "RSS/Atom feed poller with persistent de-duplication for bots", "python",
   "A bot or digest posts new feed items only once, survives restarts and does not flood on first run.",
   "Parse RSS 2.0 and Atom with xml.etree (defusedxml-style entity safety by rejecting DOCTYPE), stable item id = guid/id/link, SQLite seen-table, first run primes the table without posting, per-poll cap and oldest-first delivery.",
   {"feedpoll.py": '''"""Feed poller. Fetching is injected (so tests need no network); parsing refuses DTDs/entities."""
import sqlite3
import xml.etree.ElementTree as ET

ATOM = "{http://www.w3.org/2005/Atom}"


def parse(xml_text):
    if "<!DOCTYPE" in xml_text.upper() or "<!ENTITY" in xml_text.upper():
        raise ValueError("DTD/entity declarations are not accepted in feeds")
    root = ET.fromstring(xml_text)
    items = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        items.append(dict(id=(it.findtext("guid") or link).strip(), title=(it.findtext("title") or "").strip(), link=link))
    for en in root.iter(ATOM + "entry"):
        l = en.find(ATOM + "link")
        link = (l.get("href") if l is not None else "") or ""
        items.append(dict(id=(en.findtext(ATOM + "id") or link).strip(), title=(en.findtext(ATOM + "title") or "").strip(), link=link))
    return [i for i in items if i["id"]]


class Poller:
    def __init__(self, db=":memory:"):
        self.db = sqlite3.connect(db)
        self.db.execute("CREATE TABLE IF NOT EXISTS seen(feed TEXT, id TEXT, PRIMARY KEY(feed, id))")
        self.db.execute("CREATE TABLE IF NOT EXISTS primed(feed TEXT PRIMARY KEY)")

    def poll(self, feed_url, fetch, max_new=5):
        items = parse(fetch(feed_url))
        first = not self.db.execute("SELECT 1 FROM primed WHERE feed=?", (feed_url,)).fetchone()
        new = [i for i in items if not self.db.execute("SELECT 1 FROM seen WHERE feed=? AND id=?", (feed_url, i["id"])).fetchone()]
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO seen VALUES(?,?)", [(feed_url, i["id"]) for i in new])
            self.db.execute("INSERT OR IGNORE INTO primed VALUES(?)", (feed_url,))
        if first:
            return []                                        # do not announce the whole back catalogue
        return list(reversed(new))[-max_new:]                # feeds list newest first; post oldest first
'''
    , "test_feedpoll.py": '''import unittest
from feedpoll import parse, Poller

def rss(*ids): return "<rss><channel>" + "".join(f"<item><guid>{i}</guid><title>T{i}</title><link>http://x/{i}</link></item>" for i in ids) + "</channel></rss>"
ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>a1</id><title>A</title><link href="http://x/a1"/></entry></feed>"""


class T(unittest.TestCase):
    def test_parse_rss_and_atom(self):
        self.assertEqual([i["id"] for i in parse(rss("3", "2", "1"))], ["3", "2", "1"])
        self.assertEqual(parse(ATOM)[0]["link"], "http://x/a1")

    def test_first_poll_posts_nothing_then_only_new(self):
        p = Poller(); f = lambda url: feeds[0]
        feeds = [rss("2", "1")]
        self.assertEqual(p.poll("u", f), [])
        feeds[0] = rss("4", "3", "2", "1")
        self.assertEqual([i["id"] for i in p.poll("u", f)], ["3", "4"])         # oldest first
        self.assertEqual(p.poll("u", f), [])                                    # nothing twice

    def test_max_new_keeps_newest(self):
        p = Poller(); feeds = [rss("1")]; f = lambda u: feeds[0]; p.poll("u", f)
        feeds[0] = rss(*map(str, range(20, 0, -1)))
        self.assertEqual(len(p.poll("u", f, max_new=3)), 3)

    def test_feeds_are_independent(self):
        p = Poller(); p.poll("a", lambda u: rss("1")); self.assertEqual(p.poll("b", lambda u: rss("1")), [])

    def test_entity_bomb_refused(self):
        bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel><item><guid>&a;</guid></item></channel></rss>'
        with self.assertRaises(ValueError): parse(bomb)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["HTTP fetch (with timeouts and ETag/If-None-Match) is supplied by the caller"], compat={"python": ">=3.9"},
   tags=["rss", "atom", "feed", "bot", "discord", "slack", "digest", "polling", "scraper", "dedupe"],
   avoid="Announcing the whole feed on first run; keying items by title; parsing untrusted XML with entity expansion on."),

# ------------------------------------------------------------------ embedded / hardware
RC("py-pid-controller", "PID controller with anti-windup, derivative filtering and output clamp", "python",
   "Temperature, motor speed, drone or heater control that oscillates, overshoots or winds up the integrator.",
   "Discrete PID with derivative-on-measurement (no setpoint kick), low-pass filtered derivative, integral clamping (anti-windup) and output limits; dt is passed in so it works with irregular loops; tested against a first-order plant.",
   {"pid.py": '''"""Discrete PID. Derivative on measurement, clamped integral, filtered derivative."""


class PID:
    def __init__(self, kp, ki, kd, out_min=-1.0, out_max=1.0, d_alpha=0.2):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.lo, self.hi, self.alpha = out_min, out_max, d_alpha
        self.i = 0.0
        self.prev = None
        self.d = 0.0

    def reset(self):
        self.i, self.prev, self.d = 0.0, None, 0.0

    def update(self, setpoint, measurement, dt):
        if dt <= 0: raise ValueError("dt must be > 0")
        err = setpoint - measurement
        if self.prev is not None:
            raw = -(measurement - self.prev) / dt                   # d(error)/dt without setpoint kick
            self.d += self.alpha * (raw - self.d)                   # low-pass the noisy derivative
        self.prev = measurement
        # anti-windup: only integrate while the output is not saturated in the same direction
        trial = self.kp * err + self.i + self.ki * err * dt + self.kd * self.d
        if self.lo < trial < self.hi or (trial >= self.hi and err < 0) or (trial <= self.lo and err > 0):
            self.i += self.ki * err * dt
        out = self.kp * err + self.i + self.kd * self.d
        return max(self.lo, min(self.hi, out))
'''
    , "test_pid.py": '''import unittest
from pid import PID


def run(pid, setpoint, steps=3000, dt=0.01, gain=1.0, tau=0.5, disturbance=None):
    y, hist = 0.0, []
    for k in range(steps):
        u = pid.update(setpoint, y, dt)
        d = disturbance(k) if disturbance else 0
        y += dt * (-y + gain * u + d) / tau                          # first-order plant
        hist.append(y)
    return hist


class T(unittest.TestCase):
    def test_reaches_setpoint_with_integral(self):
        h = run(PID(2.0, 4.0, 0.0, -10, 10), 1.0)
        self.assertAlmostEqual(h[-1], 1.0, delta=0.01)

    def test_p_only_leaves_steady_state_error(self):
        h = run(PID(2.0, 0.0, 0.0, -10, 10), 1.0)
        self.assertLess(h[-1], 0.9)

    def test_output_is_clamped(self):
        p = PID(100, 0, 0, -1, 1); self.assertEqual(p.update(10, 0, 0.01), 1.0); self.assertEqual(p.update(-10, 0, 0.01), -1.0)

    def test_antiwindup_recovers_quickly(self):
        p = PID(1.0, 5.0, 0.0, -1, 1)
        for _ in range(500): p.update(100, 0, 0.01)                  # unreachable setpoint saturates the output
        self.assertLessEqual(abs(p.i), 1.5)                         # integral did not run away
        h = run(p, 0.2, steps=800)
        self.assertLess(max(h) , 0.3)                                # no big overshoot after the saturation

    def test_no_derivative_kick_on_setpoint_change(self):
        p = PID(0, 0, 1.0, -100, 100); p.update(0, 0, 0.01)
        self.assertEqual(p.update(50, 0, 0.01), 0.0)                 # measurement unchanged -> D term silent

    def test_bad_dt(self):
        with self.assertRaises(ValueError): PID(1, 0, 0).update(1, 0, 0)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=[HW, "Plant is approximated as first order; tune on the real system"], compat={"python": ">=3.9"},
   tags=["pid", "control", "embedded", "arduino", "raspberry-pi", "robotics", "motor", "temperature", "drone"],
   avoid="Derivative on error (setpoint kick); integrating while saturated; assuming a fixed dt."),

RC("py-sensor-filters-debounce", "Sensor filtering: EMA, median-of-N, hysteresis and button debounce", "python",
   "Noisy ADC/sensor readings and bouncing buttons make embedded projects flaky.",
   "Median filter for spikes, exponential moving average for smoothing, Schmitt-trigger hysteresis so thresholds do not chatter, and a time-based debouncer that reports one clean edge per press.",
   {"filters.py": '''"""Small, allocation-light filters usable on MicroPython/CircuitPython as well."""


class Median:
    def __init__(self, n=5):
        self.n, self.buf = n, []

    def update(self, x):
        self.buf.append(x)
        if len(self.buf) > self.n: self.buf.pop(0)
        return sorted(self.buf)[len(self.buf) // 2]


class EMA:
    def __init__(self, alpha=0.2):
        self.alpha, self.y = alpha, None

    def update(self, x):
        self.y = x if self.y is None else self.y + self.alpha * (x - self.y)
        return self.y


class Hysteresis:
    """On above `high`, off below `low`; in between keeps its previous state (no chatter)."""
    def __init__(self, low, high, state=False):
        if low >= high: raise ValueError("low must be < high")
        self.low, self.high, self.state = low, high, state

    def update(self, x):
        if x >= self.high: self.state = True
        elif x <= self.low: self.state = False
        return self.state


class Debounce:
    """Accept a change only after the raw input has been stable for `ms`. Returns (stable_value, edge) where edge in (None,'press','release')."""
    def __init__(self, ms=30, initial=False):
        self.ms, self.stable, self.raw, self.since = ms, initial, initial, None

    def update(self, raw, now_ms):
        if raw != self.raw:
            self.raw, self.since = raw, now_ms
        edge = None
        if raw != self.stable and self.since is not None and now_ms - self.since >= self.ms:
            self.stable = raw
            edge = "press" if raw else "release"
        return self.stable, edge
'''
    , "test_filters.py": '''import unittest
from filters import Median, EMA, Hysteresis, Debounce


class T(unittest.TestCase):
    def test_median_removes_spikes(self):
        m = Median(5); out = [m.update(x) for x in (10, 10, 999, 10, 10, 10)]
        self.assertEqual(out[-1], 10); self.assertTrue(all(o < 100 for o in out[2:]))

    def test_ema_converges(self):
        e = EMA(0.5); v = [e.update(10) for _ in range(20)]; self.assertAlmostEqual(v[-1], 10, places=3)
        self.assertEqual(EMA().update(7), 7)

    def test_hysteresis_no_chatter(self):
        h = Hysteresis(20, 25); seq = [18, 22, 24, 26, 24, 22, 21, 19, 22]
        self.assertEqual([h.update(x) for x in seq], [False, False, False, True, True, True, True, False, False])
        with self.assertRaises(ValueError): Hysteresis(5, 5)

    def test_debounce_one_edge_per_press(self):
        d, edges = Debounce(30), []
        # bouncy press at t=0..12, stable high from 12, release with bounce around 100
        samples = [(0, 1), (2, 0), (4, 1), (6, 0), (8, 1), (12, 1), (30, 1), (45, 1), (100, 0), (102, 1), (104, 0), (140, 0)]
        for t, raw in samples:
            _, e = d.update(bool(raw), t)
            if e: edges.append((e, t))
        self.assertEqual([e for e, _ in edges], ["press", "release"])

    def test_short_glitch_ignored(self):
        d = Debounce(30); d.update(False, 0); d.update(True, 5); self.assertEqual(d.update(False, 10), (False, None))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=[HW, "Python syntax kept MicroPython-friendly"], compat={"python": ">=3.9"},
   tags=["embedded", "sensor", "debounce", "filter", "arduino", "micropython", "adc", "button", "hysteresis"],
   avoid="Reading a button once per loop without debouncing; comparing a noisy value to a single threshold."),

RC("py-serial-frame-crc", "Serial/UART framing protocol with length, CRC-8 and resynchronisation", "python",
   "Talking to a microcontroller over UART/USB-serial: bytes arrive split, merged or corrupted and the parser must recover.",
   "Frame = 0x7E | len | payload | crc8 (poly 0x07); a byte-stream parser keeps a buffer, discards bytes until a valid frame, resynchronises after a bad CRC by dropping one byte, and never trusts the length without bounds.",
   {"frame.py": '''"""Byte-stream framing: 0x7E, len(1), payload(len), crc8(len+payload)."""
SOF = 0x7E
MAX = 64


def crc8(data, poly=0x07, init=0):
    c = init
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ poly) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c


def encode(payload: bytes) -> bytes:
    if len(payload) > MAX: raise ValueError("payload too long")
    body = bytes([len(payload)]) + payload
    return bytes([SOF]) + body + bytes([crc8(body)])


class Parser:
    def __init__(self):
        self.buf = bytearray()
        self.errors = 0

    def feed(self, data: bytes):
        """Return a list of complete, CRC-valid payloads found so far."""
        self.buf += data
        out = []
        while True:
            try:
                i = self.buf.index(SOF)
            except ValueError:
                self.buf.clear(); break
            del self.buf[:i]
            if len(self.buf) < 2: break
            n = self.buf[1]
            if n > MAX:
                del self.buf[0]; self.errors += 1; continue          # impossible length: this 0x7E was payload noise
            if len(self.buf) < n + 3: break                           # wait for more bytes
            body = bytes(self.buf[1:2 + n])
            if crc8(body) == self.buf[2 + n]:
                out.append(body[1:]); del self.buf[:n + 3]
            else:
                del self.buf[0]; self.errors += 1                     # resync: skip this SOF and look for the next
        return out
'''
    , "test_frame.py": '''import unittest
from frame import encode, Parser, crc8


class T(unittest.TestCase):
    def test_crc8_known_value(self):
        self.assertEqual(crc8(b"123456789"), 0xF4)                    # CRC-8 (poly 0x07) check value

    def test_roundtrip(self):
        p = Parser(); self.assertEqual(p.feed(encode(b"hello")), [b"hello"])

    def test_split_across_reads(self):
        f = encode(b"abcdef"); p = Parser(); out = []
        for i in range(len(f)): out += p.feed(f[i:i + 1])
        self.assertEqual(out, [b"abcdef"])

    def test_two_frames_in_one_read_with_leading_garbage(self):
        p = Parser(); self.assertEqual(p.feed(b"\\x00\\x01" + encode(b"a") + encode(b"bc")), [b"a", b"bc"])

    def test_corrupted_frame_dropped_next_one_survives(self):
        bad = bytearray(encode(b"zzz")); bad[3] ^= 0xFF
        p = Parser(); self.assertEqual(p.feed(bytes(bad) + encode(b"ok")), [b"ok"]); self.assertGreaterEqual(p.errors, 1)

    def test_sof_inside_payload(self):
        p = Parser(); self.assertEqual(p.feed(encode(b"\\x7e\\x7e\\x01")), [b"\\x7e\\x7e\\x01"])

    def test_oversize_rejected_and_bogus_length_survives(self):
        with self.assertRaises(ValueError): encode(b"x" * 65)
        p = Parser(); self.assertEqual(p.feed(b"\\x7e\\xff\\xff" + encode(b"ok")), [b"ok"])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=[HW, "Same framing is easy to port to C/Rust on the MCU"], compat={"python": ">=3.9"},
   tags=["serial", "uart", "embedded", "protocol", "crc", "framing", "arduino", "esp32", "parser", "pyserial"],
   avoid="Assuming one read() returns one message; trusting the length byte; line-based parsing for binary data."),

RC("py-mqtt-topic-match", "MQTT topic filter matching (+ and # wildcards) and validation", "python",
   "An IoT/home-automation router needs to decide whether a published topic matches a subscription, exactly like a broker.",
   "Split on '/', '+' matches exactly one level, '#' only as the last level and matches the parent too, '$SYS'-style topics are not matched by leading wildcards, and invalid filters are rejected with the reason.",
   {"mqttmatch.py": '''"""MQTT 3.1.1 topic-filter semantics."""


def valid_filter(f):
    if not f or "\\x00" in f: return False
    parts = f.split("/")
    for i, p in enumerate(parts):
        if "#" in p and (p != "#" or i != len(parts) - 1): return False
        if "+" in p and p != "+": return False
    return True


def matches(filt, topic):
    if not valid_filter(filt): raise ValueError(f"invalid topic filter {filt!r}")
    if not topic or "+" in topic or "#" in topic: raise ValueError("publish topics may not contain wildcards")
    f, t = filt.split("/"), topic.split("/")
    if t[0].startswith("$") and f[0] in ("+", "#"):
        return False                                          # $SYS/... is hidden from wildcard subscribers
    for i, seg in enumerate(f):
        if seg == "#": return True                            # also matches the parent level (a/# matches a)
        if i >= len(t): return False
        if seg != "+" and seg != t[i]: return False
    return len(f) == len(t)
'''
    , "test_mqttmatch.py": '''import unittest
from mqttmatch import matches, valid_filter


class T(unittest.TestCase):
    def test_exact(self): self.assertTrue(matches("home/kitchen/temp", "home/kitchen/temp")); self.assertFalse(matches("home/kitchen/temp", "home/kitchen"))
    def test_plus_one_level(self):
        self.assertTrue(matches("home/+/temp", "home/kitchen/temp")); self.assertFalse(matches("home/+/temp", "home/a/b/temp"))
        self.assertFalse(matches("home/+", "home"))
    def test_hash_multi_level_and_parent(self):
        self.assertTrue(matches("home/#", "home/a/b/c")); self.assertTrue(matches("home/#", "home")); self.assertTrue(matches("#", "x/y"))
    def test_sys_topics_hidden_from_wildcards(self):
        self.assertFalse(matches("#", "$SYS/broker/load")); self.assertFalse(matches("+/broker", "$SYS/broker")); self.assertTrue(matches("$SYS/#", "$SYS/broker/load"))
    def test_invalid_filters(self):
        for bad in ("", "a/#/b", "a#", "a+/b", "a/b+"): self.assertFalse(valid_filter(bad), bad)
        with self.assertRaises(ValueError): matches("a/#/b", "a/x/b")
    def test_publish_topic_cannot_have_wildcards(self):
        with self.assertRaises(ValueError): matches("a/b", "a/+")
    def test_empty_levels(self): self.assertTrue(matches("a//b", "a//b")); self.assertTrue(matches("a/+/b", "a//b"))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["MQTT 3.1.1/5 filter rules (shared subscriptions `$share/` not handled)"], compat={"python": ">=3.9"},
   tags=["mqtt", "iot", "home-assistant", "embedded", "topic", "pubsub", "esp32", "broker"],
   avoid="Regex-converting filters ad hoc (misses the a/# parent rule and $SYS rule)."),

# ------------------------------------------------------------------ data
RC("py-csv-clean-report", "CSV cleaning pipeline: trim, normalise, parse types, dedupe, with a rejection report", "python",
   "Messy CSV/Excel exports must be cleaned reproducibly and every dropped or altered row explained.",
   "csv module streaming; strip and unicode-normalise cells, map empty-ish values to None, parse numbers (1,234.50 / 1.234,50 / currency) and dates (several formats, ambiguity reported), dedupe on a key keeping the last, and return (clean_rows, issues) instead of silently fixing.",
   {"clean.py": '''"""Clean CSV rows and report what happened to every problem row."""
import csv, io, re, unicodedata
from datetime import datetime

NULLS = {"", "na", "n/a", "null", "none", "-", "nan"}
DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%b %d, %Y"]


def cell(v):
    v = unicodedata.normalize("NFKC", v or "").strip()
    v = re.sub(r"\\s+", " ", v)
    return None if v.lower() in NULLS else v


def number(s):
    """'1,234.50' '1.234,50' '$ 12' '(5.00)' -> float. Raises ValueError when ambiguous or not a number."""
    t = re.sub(r"[^\\d.,()\\-]", "", s)
    neg = t.startswith("(") and t.endswith(")") or t.startswith("-")
    t = t.strip("()-")
    if "," in t and "." in t:
        dec = "," if t.rfind(",") > t.rfind(".") else "."
        t = t.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in t:
        head, _, tail = t.rpartition(",")
        t = t.replace(",", "") if len(tail) == 3 else head.replace(",", "") + "." + tail     # 1,234 = thousands; 12,5 = decimal comma
    v = float(t)
    return -v if neg else v


def date(s):
    ok = []
    for f in DATE_FORMATS:
        try: ok.append(datetime.strptime(s, f).date())
        except ValueError: pass
    if not ok: raise ValueError(f"unrecognised date {s!r}")
    if len({d for d in ok}) > 1: raise ValueError(f"ambiguous date {s!r}")
    return ok[0]


def clean(text, key, numbers=(), dates=(), required=()):
    rows, issues, seen = [], [], {}
    for n, row in enumerate(csv.DictReader(io.StringIO(text)), start=2):    # line 1 is the header
        r = {k.strip().lower(): cell(v) for k, v in row.items() if k}
        bad = None
        for c in required:
            if r.get(c) is None: bad = f"missing {c}"
        for c in numbers:
            if r.get(c) is not None and not bad:
                try: r[c] = number(r[c])
                except ValueError: bad = f"bad number in {c}: {r[c]!r}"
        for c in dates:
            if r.get(c) is not None and not bad:
                try: r[c] = date(r[c])
                except ValueError as e: bad = str(e)
        if bad:
            issues.append((n, bad)); continue
        if r[key] in seen:
            issues.append((n, f"duplicate {key}={r[key]!r} (kept the later row)")); rows[seen[r[key]]] = r
        else:
            seen[r[key]] = len(rows); rows.append(r)
    return rows, issues
'''
    , "test_clean.py": '''import unittest
from datetime import date as D
from clean import clean, number, cell, date


class T(unittest.TestCase):
    def test_cell(self):
        self.assertEqual(cell("  a   b \\u00a0"), "a b"); self.assertIsNone(cell("N/A")); self.assertIsNone(cell(None))
        self.assertEqual(cell("\\uff21"), "A")                         # full-width A normalised

    def test_numbers(self):
        for s, v in [("1,234.50", 1234.5), ("1.234,50", 1234.5), ("$ 12", 12), ("(5.00)", -5), ("-3", -3), ("1,234", 1234), ("12,5", 12.5)]:
            self.assertEqual(number(s), v, s)
        with self.assertRaises(ValueError): number("abc")

    def test_dates(self):
        self.assertEqual(date("2026-03-04"), D(2026, 3, 4)); self.assertEqual(date("25/12/2026"), D(2026, 12, 25))
        with self.assertRaises(ValueError): date("03/04/2026")             # day-first or month-first? refuse to guess
        with self.assertRaises(ValueError): date("sometime")

    def test_pipeline_reports_everything(self):
        text = "ID , Name,Amount,Joined\\n1, Ann ,\\"1,200.00\\",2026-01-05\\n2,Bob,abc,2026-01-06\\n,Cy,5,2026-01-07\\n1,Ann B,300,05.02.2026\\n3,N/A,7,31/12/2026\\n"
        rows, issues = clean(text, "id", numbers=["amount"], dates=["joined"], required=["id"])
        self.assertEqual([r["id"] for r in rows], ["1", "3"])
        self.assertEqual(rows[0]["name"], "Ann B"); self.assertEqual(rows[0]["amount"], 300.0); self.assertIsNone(rows[1]["name"])
        self.assertEqual([i[0] for i in issues], [3, 4, 5])
        self.assertIn("bad number", issues[0][1]); self.assertIn("missing id", issues[1][1]); self.assertIn("duplicate", issues[2][1])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Thousands/decimal heuristics: a lone comma with 3 trailing digits is read as thousands; pass a locale if you know it"], compat={"python": ">=3.9"},
   tags=["csv", "data-cleaning", "etl", "pandas-free", "excel", "dedupe", "dates", "numbers", "data-science"],
   avoid="Silently dropping or 'fixing' bad rows; guessing day/month order; float parsing without locale awareness."),

RC("py-rolling-timeseries", "Time-series helpers: resample to buckets, rolling mean, anomaly flags", "python",
   "Sensor/finance/log metrics arrive irregularly; you need bucketed averages, a rolling window and outlier flags without pandas.",
   "Floor timestamps to a bucket, aggregate per bucket, fill gaps explicitly (None or forward-fill), rolling mean over the last N buckets in O(1) per point, and z-score flags using a rolling mean/std with a minimum sample count.",
   {"ts.py": '''"""Stdlib time-series helpers. Timestamps are epoch seconds (float/int)."""
import math
from collections import deque


def resample(points, bucket, agg=lambda v: sum(v) / len(v), fill=None):
    """points: [(t, value)] -> [(bucket_start, aggregated)] with EVERY bucket present between first and last."""
    if not points: return []
    grouped = {}
    for t, v in points:
        grouped.setdefault(int(t // bucket) * bucket, []).append(v)
    lo, hi = min(grouped), max(grouped)
    out, last = [], None
    for b in range(lo, hi + 1, bucket):
        if b in grouped:
            last = agg(grouped[b]); out.append((b, last))
        else:
            out.append((b, last if fill == "ffill" else None))
    return out


def rolling_mean(values, n):
    """Mean of the last n non-None values (fewer at the start); O(1) per step."""
    win, s, out = deque(), 0.0, []
    for v in values:
        if v is not None:
            win.append(v); s += v
            if len(win) > n: s -= win.popleft()
        out.append(s / len(win) if win else None)
    return out


def anomalies(values, n=20, z=3.0, min_samples=5):
    """Indexes whose value is > z standard deviations from the mean of the previous n points."""
    flagged, win = [], deque(maxlen=n)
    for i, v in enumerate(values):
        if v is None: continue
        if len(win) >= min_samples:
            m = sum(win) / len(win); sd = math.sqrt(sum((x - m) ** 2 for x in win) / len(win))
            if (sd > 0 and abs(v - m) / sd > z) or (sd == 0 and v != m): flagged.append(i)
        win.append(v)
    return flagged
'''
    , "test_ts.py": '''import unittest
from ts import resample, rolling_mean, anomalies


class T(unittest.TestCase):
    def test_resample_means_and_gap(self):
        out = resample([(0, 1), (10, 3), (130, 10)], 60)
        self.assertEqual(out, [(0, 2.0), (60, None), (120, 10.0)])

    def test_resample_ffill(self):
        self.assertEqual(resample([(0, 4), (130, 10)], 60, fill="ffill")[1], (60, 4.0))

    def test_resample_custom_agg_and_empty(self):
        self.assertEqual(resample([(1, 1), (2, 5)], 60, agg=max), [(0, 5)]); self.assertEqual(resample([], 60), [])

    def test_unsorted_input_ok(self):
        self.assertEqual(resample([(130, 10), (0, 2)], 60)[0], (0, 2.0))

    def test_rolling_mean(self):
        self.assertEqual(rolling_mean([1, 2, 3, 4, 5], 3), [1.0, 1.5, 2.0, 3.0, 4.0])
        self.assertEqual(rolling_mean([None, 2, None, 4], 2), [None, 2.0, 2.0, 3.0])

    def test_anomaly_spike(self):
        data = [10.0, 10.2, 9.9, 10.1, 10.0, 9.8, 10.1, 50.0, 10.0]
        self.assertEqual(anomalies(data), [7])

    def test_flat_series_then_change_flagged(self):
        self.assertEqual(anomalies([5] * 10 + [6]), [10])

    def test_needs_warmup(self):
        self.assertEqual(anomalies([1, 100, 1, 100]), [])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Small/medium series; use pandas/polars for millions of points"], compat={"python": ">=3.9"},
   tags=["time-series", "resample", "rolling", "anomaly", "monitoring", "sensor", "data-science", "metrics"],
   avoid="Averaging only the buckets that happen to have data (hides gaps); z-score against a window that includes the point being tested."),

# ------------------------------------------------------------------ docs / static sites
RC("py-docs-toc-sitemap-feed", "Markdown headings to slugs/TOC, plus sitemap.xml and RSS generation", "python",
   "A static docs site or blog needs stable heading anchors, a table of contents, a sitemap and a valid RSS feed.",
   "GitHub-style slugify with duplicate suffixes, TOC from ATX headings skipping code fences, sitemap with ISO dates and XML escaping, RSS 2.0 with RFC 822 dates and CDATA-free escaping.",
   {"sitegen.py": '''"""Docs-site helpers: heading anchors, TOC, sitemap.xml, rss.xml. No dependencies."""
import re, unicodedata
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape


def slugify(text):
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^\\w\\s-]", "", t).strip()
    return re.sub(r"[\\s_]+", "-", t)


def headings(md):
    out, fence, used = [], False, {}
    for line in md.splitlines():
        if line.lstrip().startswith(("```", "~~~")): fence = not fence; continue
        m = None if fence else re.match(r"^(#{1,6})\\s+(.*?)\\s*#*\\s*$", line)
        if not m: continue
        slug = slugify(m.group(2)) or "section"
        n = used.get(slug, 0); used[slug] = n + 1
        out.append((len(m.group(1)), m.group(2), slug if n == 0 else f"{slug}-{n}"))
    return out


def toc(md, min_level=2, max_level=3):
    return "\\n".join(f"{'  ' * (lvl - min_level)}- [{text}](#{slug})" for lvl, text, slug in headings(md) if min_level <= lvl <= max_level)


def sitemap(base, pages):
    """pages: [(path, datetime)]"""
    urls = "".join(f"<url><loc>{escape(base.rstrip('/') + '/' + p.lstrip('/'))}</loc><lastmod>{d.date().isoformat()}</lastmod></url>" for p, d in pages)
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'


def rss(title, base, desc, posts):
    """posts: [(title, path, datetime, summary)] newest first"""
    items = "".join(f"<item><title>{escape(t)}</title><link>{escape(base.rstrip('/') + '/' + p.lstrip('/'))}</link><guid>{escape(base.rstrip('/') + '/' + p.lstrip('/'))}</guid>"
                    f"<pubDate>{format_datetime(d)}</pubDate><description>{escape(s)}</description></item>" for t, p, d, s in posts)
    return f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{escape(title)}</title><link>{escape(base)}</link><description>{escape(desc)}</description>{items}</channel></rss>'
'''
    , "test_sitegen.py": '''import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from sitegen import slugify, headings, toc, sitemap, rss

MD = "# Title\\n## Install\\n```\\n# not a heading\\n```\\n## Usage\\n### Flags & Options\\n## Usage\\n"


class T(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("Flags & Options"), "flags-options"); self.assertEqual(slugify("Café déjà vu!"), "cafe-deja-vu")

    def test_duplicates_get_suffix_and_fences_skipped(self):
        self.assertEqual([h[2] for h in headings(MD)], ["title", "install", "usage", "flags-options", "usage-1"])

    def test_toc_levels(self):
        t = toc(MD); self.assertIn("- [Install](#install)", t); self.assertIn("  - [Flags & Options](#flags-options)", t); self.assertNotIn("Title", t)

    def test_sitemap_valid_and_escaped(self):
        x = sitemap("https://e.com/", [("a&b/", datetime(2026, 1, 2, tzinfo=timezone.utc))]); root = ET.fromstring(x)
        self.assertIn("a&amp;b", x); self.assertEqual(root[0][1].text, "2026-01-02")

    def test_rss_valid_with_rfc822_date(self):
        x = rss("Blog <1>", "https://e.com", "d", [("Post & more", "p/1", datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc), "s")])
        root = ET.fromstring(x); self.assertEqual(root.find("channel/item/pubDate").text, "Fri, 02 Jan 2026 03:04:05 +0000")
        self.assertEqual(root.find("channel/title").text, "Blog <1>")


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["ATX (#) headings only; setext headings are not handled"], compat={"python": ">=3.9"},
   tags=["docs", "markdown", "static-site", "sitemap", "rss", "blog", "toc", "seo", "mkdocs"],
   avoid="Hand-writing XML with f-strings and no escaping; anchors that change when headings repeat."),

# ------------------------------------------------------------------ Go
RC("go-interactive-confirm-prompt", "Interactive confirm/choice prompt for a Go CLI (testable, TTY-aware)", "go",
   "A Go CLI must ask 'Delete 12 files? [y/N]' or offer a numbered choice, but also work in scripts and tests.",
   "Prompt takes io.Reader/io.Writer, loops on invalid input with a retry cap, treats EOF as the default (never as yes), supports --yes bypass and refuses to prompt when stdin is not a terminal unless forced.",
   {"prompt.go": '''package main

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"strconv"
	"strings"
)

var ErrNoAnswer = errors.New("no answer given")

// Confirm asks a yes/no question. def is returned on empty input or EOF - EOF is never "yes".
func Confirm(in *bufio.Reader, out io.Writer, q string, def bool) (bool, error) {
	hint := "[y/N]"
	if def {
		hint = "[Y/n]"
	}
	for tries := 0; tries < 3; tries++ {
		fmt.Fprintf(out, "%s %s ", q, hint)
		line, err := in.ReadString('\\n')
		switch strings.ToLower(strings.TrimSpace(line)) {
		case "y", "yes":
			return true, nil
		case "n", "no":
			return false, nil
		case "":
			return def, nil
		}
		if err != nil {
			return def, nil
		}
		fmt.Fprintln(out, "Please answer y or n.")
	}
	return def, ErrNoAnswer
}

// Choose shows a numbered list and returns the selected index.
func Choose(in *bufio.Reader, out io.Writer, q string, options []string) (int, error) {
	fmt.Fprintln(out, q)
	for i, o := range options {
		fmt.Fprintf(out, "  %d) %s\\n", i+1, o)
	}
	for tries := 0; tries < 3; tries++ {
		fmt.Fprintf(out, "Choice [1-%d]: ", len(options))
		line, err := in.ReadString('\\n')
		n, convErr := strconv.Atoi(strings.TrimSpace(line))
		if convErr == nil && n >= 1 && n <= len(options) {
			return n - 1, nil
		}
		if err != nil {
			return -1, ErrNoAnswer
		}
		fmt.Fprintln(out, "Enter a number from the list.")
	}
	return -1, ErrNoAnswer
}

// ShouldPrompt: only ask when a human can answer, unless the caller forces it. assumeYes always skips the question.
func ShouldPrompt(stdinIsTTY, assumeYes bool) bool { return stdinIsTTY && !assumeYes }
'''
    , "prompt_test.go": '''package main

import (
	"bufio"
	"bytes"
	"strings"
	"testing"
)

func run(input string, f func(*bufio.Reader, *bytes.Buffer)) string {
	var out bytes.Buffer
	f(bufio.NewReader(strings.NewReader(input)), &out)
	return out.String()
}

func TestConfirm(t *testing.T) {
	cases := []struct {
		in   string
		def  bool
		want bool
	}{{"y\\n", false, true}, {"YES\\n", false, true}, {"n\\n", true, false}, {"\\n", true, true}, {"\\n", false, false}, {"", true, true}, {"", false, false}, {"maybe\\ny\\n", false, true}}
	for _, c := range cases {
		var got bool
		run(c.in, func(r *bufio.Reader, w *bytes.Buffer) { got, _ = Confirm(r, w, "Delete?", c.def) })
		if got != c.want {
			t.Errorf("input %q default %v: got %v want %v", c.in, c.def, got, c.want)
		}
	}
}

func TestConfirmEOFIsNeverYesByDefault(t *testing.T) {
	var got bool
	run("", func(r *bufio.Reader, w *bytes.Buffer) { got, _ = Confirm(r, w, "Delete?", false) })
	if got {
		t.Fatal("EOF must not confirm a destructive action")
	}
}

func TestConfirmGivesUpAfterRetries(t *testing.T) {
	var err error
	run("a\\nb\\nc\\nd\\n", func(r *bufio.Reader, w *bytes.Buffer) { _, err = Confirm(r, w, "Q?", false) })
	if err != ErrNoAnswer {
		t.Fatalf("want ErrNoAnswer, got %v", err)
	}
}

func TestChoose(t *testing.T) {
	var idx int
	out := run("9\\nx\\n2\\n", func(r *bufio.Reader, w *bytes.Buffer) { idx, _ = Choose(r, w, "Pick", []string{"a", "b", "c"}) })
	if idx != 1 || !strings.Contains(out, "1) a") {
		t.Fatalf("idx=%d out=%q", idx, out)
	}
	run("", func(r *bufio.Reader, w *bytes.Buffer) {
		if i, err := Choose(r, w, "Pick", []string{"a"}); i != -1 || err == nil {
			t.Fatal("EOF must error")
		}
	})
}

func TestShouldPrompt(t *testing.T) {
	if !ShouldPrompt(true, false) || ShouldPrompt(false, false) || ShouldPrompt(true, true) {
		t.Fatal("prompt policy wrong")
	}
}
'''
    , "main.go": "package main\n\nfunc main() {}\n"}, GOT, assumptions=["TTY detection is done by the caller (golang.org/x/term IsTerminal) and passed in", "Use charmbracelet/huh for richer prompts"], compat={"go": ">=1.21"},
   tags=["cli", "prompt", "confirm", "interactive", "stdin", "go", "yes-flag", "destructive"],
   avoid="Treating EOF/empty input as yes; prompting when stdin is a pipe; fmt.Scanln (swallows lines, no retry)."),

# ------------------------------------------------------------------ TypeScript UI logic
RC("ts-data-table-model", "Data table model: sort, filter, paginate with stable sorting (TypeScript)", "typescript",
   "A React/Vue/Svelte table needs multi-column sort, text filter and pagination that behave predictably and are testable without a DOM.",
   "Pure functions: filter by case/diacritic-insensitive substring across chosen columns, stable multi-key sort with natural number ordering and nulls last, pagination that clamps the page and returns page count; the UI just renders the result.",
   {"table.ts": r'''export type Row = Record<string, string | number | null>
export interface SortKey { col: string; dir: 'asc' | 'desc' }

const collator = new Intl.Collator('en', { numeric: true, sensitivity: 'base' })
const fold = (s: string) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()

export function filterRows(rows: Row[], query: string, cols: string[]): Row[] {
  const q = fold(query.trim())
  if (!q) return rows
  return rows.filter((r) => cols.some((c) => r[c] != null && fold(String(r[c])).includes(q)))
}

function cmp(a: Row[string], b: Row[string]): number {
  if (a == null && b == null) return 0
  if (a == null) return 1            // nulls last regardless of direction handled by caller
  if (b == null) return -1
  if (typeof a === 'number' && typeof b === 'number') return a - b
  return collator.compare(String(a), String(b))
}

export function sortRows(rows: Row[], keys: SortKey[]): Row[] {
  const indexed = rows.map((r, i) => ({ r, i }))
  indexed.sort((x, y) => {
    for (const k of keys) {
      const a = x.r[k.col], b = y.r[k.col]
      if (a == null || b == null) { const n = cmp(a, b); if (n) return n }      // nulls always last
      else { const n = cmp(a, b); if (n) return k.dir === 'asc' ? n : -n }
    }
    return x.i - y.i                                                         // stable
  })
  return indexed.map((x) => x.r)
}

export function paginate<T>(items: T[], page: number, size: number) {
  const pages = Math.max(1, Math.ceil(items.length / size))
  const p = Math.min(Math.max(1, Math.floor(page) || 1), pages)
  return { page: p, pages, total: items.length, rows: items.slice((p - 1) * size, p * size) }
}

export function view(rows: Row[], o: { query?: string; cols?: string[]; sort?: SortKey[]; page?: number; size?: number }) {
  const f = filterRows(rows, o.query ?? '', o.cols ?? Object.keys(rows[0] ?? {}))
  return paginate(sortRows(f, o.sort ?? []), o.page ?? 1, o.size ?? 10)
}
''', "table.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { filterRows, sortRows, paginate, view, type Row } from './table.ts'

const rows: Row[] = [
  { name: 'Élodie', age: 30, team: 'b' }, { name: 'adam', age: 25, team: 'a' }, { name: 'Zed', age: null, team: 'a' },
  { name: 'file10', age: 25, team: 'b' }, { name: 'file2', age: 41, team: 'a' },
]

test('filter ignores case and diacritics', () => {
  assert.deepEqual(filterRows(rows, 'elodie', ['name']).map((r) => r.name), ['Élodie'])
  assert.equal(filterRows(rows, '  ', ['name']).length, 5)
})

test('numeric-aware natural sort', () => {
  const names = sortRows(rows, [{ col: 'name', dir: 'asc' }]).map((r) => r.name)
  assert.ok(names.indexOf('file2') < names.indexOf('file10'))
})

test('nulls last in both directions', () => {
  assert.equal(sortRows(rows, [{ col: 'age', dir: 'asc' }]).at(-1)!.name, 'Zed')
  assert.equal(sortRows(rows, [{ col: 'age', dir: 'desc' }]).at(-1)!.name, 'Zed')
})

test('multi-key and stable', () => {
  const out = sortRows(rows, [{ col: 'age', dir: 'asc' }, { col: 'team', dir: 'desc' }]).map((r) => r.name)
  assert.deepEqual(out.slice(0, 2), ['file10', 'adam'])       // age 25: team b before a
  const same = sortRows([{ k: 1, id: 'x' }, { k: 1, id: 'y' }], [{ col: 'k', dir: 'asc' }]).map((r) => r.id)
  assert.deepEqual(same, ['x', 'y'])
})

test('pagination clamps', () => {
  const p = paginate([1, 2, 3, 4, 5], 99, 2); assert.deepEqual(p, { page: 3, pages: 3, total: 5, rows: [5] })
  assert.equal(paginate([], 1, 10).pages, 1); assert.equal(paginate([1, 2, 3], -4, 2).page, 1)
})

test('view composes filter, sort and page', () => {
  const v = view(rows, { query: 'a', cols: ['team'], sort: [{ col: 'name', dir: 'asc' }], size: 2, page: 2 })
  assert.equal(v.total, 3); assert.equal(v.rows.length, 1)
})
'''}, NODE_TS, assumptions=TSA, compat={"node": ">=22.18"},
   tags=["table", "datagrid", "sort", "filter", "pagination", "react", "ui", "dashboard", "admin"],
   avoid="Sorting with localeCompare on numbers-as-strings; unstable sorts that shuffle equal rows; page numbers beyond the last page after filtering."),

RC("ts-toast-queue", "Toast/notification queue: stacking limit, dedupe, auto-dismiss, pause on hover", "typescript",
   "An app needs toasts that do not flood the screen, repeat the same message ten times, or vanish while the user is reading.",
   "Framework-free store: max visible with overflow queued, identical messages coalesced into a counter, per-toast timers with pause/resume that remember the remaining time, errors sticky by default, subscribe() for any UI layer.",
   {"toasts.ts": r'''export interface Toast { id: number; message: string; kind: 'info' | 'success' | 'error'; count: number; remaining: number }
type Timers = { set: (fn: () => void, ms: number) => unknown; clear: (h: unknown) => void; now: () => number }

export class ToastStore {
  private list: Toast[] = []
  private queue: Toast[] = []
  private handles = new Map<number, { h: unknown; started: number }>()
  private subs = new Set<(t: Toast[]) => void>()
  private seq = 0
  private max: number
  private ms: number
  private t: Timers
  constructor(max = 3, ms = 4000, t: Timers = { set: setTimeout, clear: (h) => clearTimeout(h as any), now: Date.now }) { this.max = max; this.ms = ms; this.t = t }

  subscribe(fn: (t: Toast[]) => void) { this.subs.add(fn); fn(this.list); return () => this.subs.delete(fn) }
  private emit() { for (const s of this.subs) s(this.list) }

  push(message: string, kind: Toast['kind'] = 'info') {
    const dup = this.list.find((x) => x.message === message && x.kind === kind) ?? this.queue.find((x) => x.message === message && x.kind === kind)
    if (dup) { dup.count++; if (this.list.includes(dup)) this.start(dup); this.emit(); return dup.id }
    const toast: Toast = { id: ++this.seq, message, kind, count: 1, remaining: kind === 'error' ? Infinity : this.ms }
    if (this.list.length < this.max) { this.list = [...this.list, toast]; this.start(toast) } else this.queue.push(toast)
    this.emit(); return toast.id
  }

  private start(toast: Toast) {
    this.stop(toast.id)
    if (!Number.isFinite(toast.remaining)) return
    toast.remaining = this.ms
    this.handles.set(toast.id, { h: this.t.set(() => this.dismiss(toast.id), toast.remaining), started: this.t.now() })
  }
  private stop(id: number) { const h = this.handles.get(id); if (h) { this.t.clear(h.h); this.handles.delete(id) } }

  pause(id: number) {
    const h = this.handles.get(id), toast = this.list.find((x) => x.id === id)
    if (h && toast) { toast.remaining = Math.max(0, toast.remaining - (this.t.now() - h.started)); this.stop(id) }
  }
  resume(id: number) {
    const toast = this.list.find((x) => x.id === id)
    if (toast && Number.isFinite(toast.remaining) && !this.handles.has(id)) this.handles.set(id, { h: this.t.set(() => this.dismiss(id), toast.remaining), started: this.t.now() })
  }
  dismiss(id: number) {
    this.stop(id); this.list = this.list.filter((x) => x.id !== id)
    const next = this.queue.shift(); if (next) { this.list = [...this.list, next]; this.start(next) }
    this.emit()
  }
  get visible() { return this.list }
  get waiting() { return this.queue.length }
}
''', "toasts.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { ToastStore } from './toasts.ts'

function fake() {
  let now = 0, id = 0; const timers = new Map<number, { at: number; fn: () => void }>()
  return {
    t: { now: () => now, set: (fn: () => void, ms: number) => { timers.set(++id, { at: now + ms, fn }); return id }, clear: (h: unknown) => { timers.delete(h as number) } },
    advance(ms: number) { now += ms; for (const [k, v] of [...timers]) if (v.at <= now) { timers.delete(k); v.fn() } },
  }
}

test('auto-dismisses and promotes queued toasts', () => {
  const f = fake(); const s = new ToastStore(2, 1000, f.t)
  s.push('a'); s.push('b'); s.push('c')
  assert.deepEqual(s.visible.map((x) => x.message), ['a', 'b']); assert.equal(s.waiting, 1)
  f.advance(1000); assert.deepEqual(s.visible.map((x) => x.message), ['c']); assert.equal(s.waiting, 0)
  f.advance(1000); assert.equal(s.visible.length, 0)
})

test('identical messages coalesce into a count and restart the timer', () => {
  const f = fake(); const s = new ToastStore(3, 1000, f.t)
  s.push('saved'); f.advance(800); s.push('saved')
  assert.equal(s.visible.length, 1); assert.equal(s.visible[0].count, 2)
  f.advance(800); assert.equal(s.visible.length, 1); f.advance(200); assert.equal(s.visible.length, 0)
})

test('hover pause keeps the remaining time', () => {
  const f = fake(); const s = new ToastStore(3, 1000, f.t); const id = s.push('x')
  f.advance(400); s.pause(id); f.advance(5000); assert.equal(s.visible.length, 1)
  s.resume(id); f.advance(599); assert.equal(s.visible.length, 1); f.advance(1); assert.equal(s.visible.length, 0)
})

test('errors are sticky until dismissed', () => {
  const f = fake(); const s = new ToastStore(3, 1000, f.t); const id = s.push('boom', 'error')
  f.advance(60000); assert.equal(s.visible.length, 1); s.dismiss(id); assert.equal(s.visible.length, 0)
})

test('subscribers are notified and can unsubscribe', () => {
  const f = fake(); const s = new ToastStore(3, 1000, f.t); const seen: number[] = []
  const off = s.subscribe((t) => seen.push(t.length)); s.push('a'); off(); s.push('b')
  assert.deepEqual(seen, [0, 1])
})
'''}, NODE_TS, assumptions=TSA, compat={"node": ">=22.18"},
   tags=["toast", "notification", "snackbar", "ui", "react", "svelte", "vue", "queue"],
   avoid="One setTimeout per push with no cleanup; unlimited stacking; dismissing while the user hovers."),

RC("ts-virtual-window-infinite-scroll", "Virtual list window maths and infinite-scroll trigger (TypeScript)", "typescript",
   "Rendering 100k rows or an infinite feed freezes the page; you need to render only what is visible and load the next page at the right moment.",
   "Fixed-height window calculation with overscan, total/offset spacers, clamping, a `shouldLoadMore` rule with threshold and in-flight/hasMore guards, and a scroll-anchoring helper for prepend; all pure so any framework can wire it to scroll events.",
   {"virtual.ts": r'''export interface Win { start: number; end: number; padTop: number; padBottom: number }

export function windowFor(scrollTop: number, viewport: number, count: number, itemH: number, overscan = 3): Win {
  if (count <= 0 || itemH <= 0) return { start: 0, end: 0, padTop: 0, padBottom: 0 }
  const top = Math.min(Math.max(0, scrollTop), Math.max(0, count * itemH - viewport))   // elastic overscroll / stale offsets
  const first = Math.floor(top / itemH)
  const last = Math.ceil((top + viewport) / itemH)
  const start = Math.max(0, first - overscan)
  const end = Math.min(count, last + overscan)
  return { start, end, padTop: start * itemH, padBottom: (count - end) * itemH }
}

export function shouldLoadMore(o: { scrollTop: number; viewport: number; contentHeight: number; threshold?: number; loading: boolean; hasMore: boolean }): boolean {
  if (o.loading || !o.hasMore) return false
  return o.contentHeight - (o.scrollTop + o.viewport) <= (o.threshold ?? 300)
}

/** After prepending `added` items, keep the same item under the viewport top. */
export function anchorAfterPrepend(scrollTop: number, added: number, itemH: number): number {
  return scrollTop + added * itemH
}
''', "virtual.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { windowFor, shouldLoadMore, anchorAfterPrepend } from './virtual.ts'

test('window covers the viewport plus overscan', () => {
  const w = windowFor(1000, 500, 10000, 50, 2)       // items 20..30 visible
  assert.deepEqual(w, { start: 18, end: 32, padTop: 900, padBottom: (10000 - 32) * 50 })
})

test('clamped at both ends', () => {
  assert.equal(windowFor(-50, 500, 100, 50, 3).start, 0)
  const w = windowFor(99999, 500, 100, 50, 3); assert.equal(w.end, 100); assert.equal(w.padBottom, 0)
})

test('spacers + rendered rows always add up to the full height', () => {
  for (const top of [0, 123, 4999, 9000]) { const w = windowFor(top, 400, 200, 40, 4); assert.equal(w.padTop + (w.end - w.start) * 40 + w.padBottom, 200 * 40) }
})

test('empty and invalid input', () => { assert.deepEqual(windowFor(0, 500, 0, 50), { start: 0, end: 0, padTop: 0, padBottom: 0 }); assert.equal(windowFor(0, 500, 5, 0).end, 0) })

test('load more near the bottom only when idle and more exists', () => {
  const base = { scrollTop: 700, viewport: 300, contentHeight: 1200, threshold: 250, loading: false, hasMore: true }
  assert.equal(shouldLoadMore(base), true)
  assert.equal(shouldLoadMore({ ...base, scrollTop: 100 }), false)
  assert.equal(shouldLoadMore({ ...base, loading: true }), false)
  assert.equal(shouldLoadMore({ ...base, hasMore: false }), false)
})

test('prepend keeps position', () => assert.equal(anchorAfterPrepend(120, 10, 40), 520))
'''}, NODE_TS, assumptions=TSA + ["Fixed item height (variable heights: use TanStack Virtual)"], compat={"node": ">=22.18"},
   tags=["virtual-list", "infinite-scroll", "performance", "react", "ui", "feed", "lazy-load", "scroll"],
   avoid="Rendering every row; scroll handlers that fire fetches repeatedly while one is in flight."),

RC("ts-theme-toggle-logic", "Dark/light/system theme preference logic with no flash on load", "typescript",
   "A site needs a theme toggle that follows the OS by default, remembers an explicit choice, and doesn't flash the wrong theme.",
   "Three-state preference (light|dark|system) stored under one key with try/catch for blocked storage, resolve() against prefers-color-scheme, an inline head script string that sets data-theme before paint, and a cycle() for the toggle button.",
   {"theme.ts": r'''export type Pref = 'light' | 'dark' | 'system'
export type Resolved = 'light' | 'dark'
const KEY = 'theme'

export interface Env { storage?: { getItem(k: string): string | null; setItem(k: string, v: string): void }; prefersDark: () => boolean }

export function readPref(env: Env): Pref {
  try { const v = env.storage?.getItem(KEY); return v === 'light' || v === 'dark' ? v : 'system' } catch { return 'system' }     // blocked storage -> follow OS
}
export function writePref(env: Env, p: Pref): void {
  try { env.storage?.setItem(KEY, p) } catch { /* private mode: preference lasts for this page only */ }
}
export function resolve(p: Pref, env: Env): Resolved { return p === 'system' ? (env.prefersDark() ? 'dark' : 'light') : p }
export function cycle(p: Pref): Pref { return p === 'system' ? 'light' : p === 'light' ? 'dark' : 'system' }

/** Put this string inline in <head> BEFORE the stylesheet: it runs before first paint so the page never flashes. */
export const NO_FLASH = `(function(){try{var t=localStorage.getItem('${KEY}');var d=t==='dark'||((t!=='light')&&matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.dataset.theme=d?'dark':'light';document.documentElement.style.colorScheme=d?'dark':'light'}catch(e){}})()`
''', "theme.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { readPref, writePref, resolve, cycle, NO_FLASH, type Env } from './theme.ts'

const mem = () => { const m = new Map<string, string>(); return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) } }

test('defaults to system and follows the OS', () => {
  const env: Env = { storage: mem(), prefersDark: () => true }
  assert.equal(readPref(env), 'system'); assert.equal(resolve('system', env), 'dark')
  assert.equal(resolve('system', { ...env, prefersDark: () => false }), 'light')
})

test('explicit choice wins over OS and persists', () => {
  const env: Env = { storage: mem(), prefersDark: () => true }
  writePref(env, 'light'); assert.equal(readPref(env), 'light'); assert.equal(resolve(readPref(env), env), 'light')
})

test('garbage in storage or blocked storage falls back to system', () => {
  const s = mem(); s.setItem('theme', 'purple'); assert.equal(readPref({ storage: s, prefersDark: () => false }), 'system')
  const blocked = { getItem() { throw new Error('denied') }, setItem() { throw new Error('denied') } }
  const env: Env = { storage: blocked, prefersDark: () => false }
  assert.equal(readPref(env), 'system'); assert.doesNotThrow(() => writePref(env, 'dark'))
})

test('toggle cycles through all three', () => assert.deepEqual([cycle('system'), cycle('light'), cycle('dark')], ['light', 'dark', 'system']))

test('no-flash snippet sets data-theme from storage / media query', () => {
  const run = (stored: string | null, dark: boolean) => {
    const doc: any = { documentElement: { dataset: {} as any, style: {} as any } }
    vm.runInNewContext(NO_FLASH, { localStorage: { getItem: () => stored }, matchMedia: () => ({ matches: dark }), document: doc })
    return doc.documentElement.dataset.theme
  }
  assert.equal(run(null, true), 'dark'); assert.equal(run('light', true), 'light'); assert.equal(run('dark', false), 'dark'); assert.equal(run('system', false), 'light')
})
'''}, NODE_TS, assumptions=TSA, compat={"node": ">=22.18"},
   tags=["dark-mode", "theme", "toggle", "css", "ui", "prefers-color-scheme", "web", "tailwind"],
   avoid="Reading the theme in a late-loading script (flash of wrong theme); treating 'no preference stored' as light."),

RC("ts-sortable-reorder", "Drag-and-drop reorder logic: move item, keyboard reorder, stable keys (TypeScript)", "typescript",
   "A sortable list or kanban board needs correct reordering, keyboard access and cross-column moves, independent of the drag library.",
   "Pure `move(list, from, to)` that returns a new array, `moveBetween` for columns with index clamping, keyboard handlers (Alt+Arrow) mapped to moves, and a fractional-index helper so persisting a reorder updates one row instead of the whole list.",
   {"reorder.ts": r'''export function move<T>(list: readonly T[], from: number, to: number): T[] {
  if (from < 0 || from >= list.length) return list.slice()
  const out = list.slice()
  const [item] = out.splice(from, 1)
  out.splice(Math.max(0, Math.min(to, out.length)), 0, item)
  return out
}

export function moveBetween<T>(cols: Record<string, T[]>, from: { col: string; index: number }, to: { col: string; index: number }): Record<string, T[]> {
  if (from.col === to.col) return { ...cols, [from.col]: move(cols[from.col], from.index, to.index) }
  const src = cols[from.col].slice(), dst = cols[to.col].slice()
  if (from.index < 0 || from.index >= src.length) return cols
  const [item] = src.splice(from.index, 1)
  dst.splice(Math.max(0, Math.min(to.index, dst.length)), 0, item)
  return { ...cols, [from.col]: src, [to.col]: dst }
}

/** Alt+ArrowUp/Down moves the focused item; returns the new index or null when the key is not a reorder key. */
export function keyboardTarget(key: string, alt: boolean, index: number, length: number): number | null {
  if (!alt) return null
  if (key === 'ArrowUp') return Math.max(0, index - 1)
  if (key === 'ArrowDown') return Math.min(length - 1, index + 1)
  if (key === 'Home') return 0
  if (key === 'End') return length - 1
  return null
}

/** Position between two neighbours so a reorder is ONE database write. Returns null when precision ran out (rebalance then). */
export function between(before: number | null, after: number | null): number | null {
  const lo = before ?? (after != null ? after - 2 : 0), hi = after ?? (before != null ? before + 2 : 2)
  const mid = (lo + hi) / 2
  return mid > lo && mid < hi ? mid : null
}
''', "reorder.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { move, moveBetween, keyboardTarget, between } from './reorder.ts'

test('move returns a new array and does not mutate', () => {
  const a = ['a', 'b', 'c', 'd']; const b = move(a, 0, 2)
  assert.deepEqual(b, ['b', 'c', 'a', 'd']); assert.deepEqual(a, ['a', 'b', 'c', 'd']); assert.notEqual(a, b)
})
test('move clamps and ignores invalid source', () => {
  assert.deepEqual(move([1, 2, 3], 0, 99), [2, 3, 1]); assert.deepEqual(move([1, 2, 3], 2, -5), [3, 1, 2]); assert.deepEqual(move([1, 2], 5, 0), [1, 2])
})
test('cross-column move', () => {
  const cols = { todo: ['a', 'b'], done: ['c'] }
  const r = moveBetween(cols, { col: 'todo', index: 0 }, { col: 'done', index: 1 })
  assert.deepEqual(r, { todo: ['b'], done: ['c', 'a'] }); assert.deepEqual(cols, { todo: ['a', 'b'], done: ['c'] })
  assert.deepEqual(moveBetween(cols, { col: 'todo', index: 9 }, { col: 'done', index: 0 }), cols)
  assert.deepEqual(moveBetween(cols, { col: 'todo', index: 0 }, { col: 'todo', index: 1 }).todo, ['b', 'a'])
})
test('keyboard targets', () => {
  assert.equal(keyboardTarget('ArrowUp', true, 0, 5), 0); assert.equal(keyboardTarget('ArrowDown', true, 4, 5), 4)
  assert.equal(keyboardTarget('ArrowDown', false, 1, 5), null); assert.equal(keyboardTarget('End', true, 1, 5), 4); assert.equal(keyboardTarget('x', true, 1, 5), null)
})
test('fractional positions allow one-row updates and detect exhaustion', () => {
  assert.equal(between(1, 2), 1.5); assert.equal(between(null, 10), 9); assert.equal(between(10, null), 11); assert.equal(between(null, null), 1)
  let lo = 1, hi = 2, n = 0; for (; n < 2000; n++) { const m = between(lo, hi); if (m == null) break; hi = m }
  assert.ok(n > 40 && n < 2000)                                     // eventually returns null -> caller rebalances
})
'''}, NODE_TS, assumptions=TSA + ["Wire to dnd-kit / SortableJS / native drag events; this is the state logic"], compat={"node": ">=22.18"},
   tags=["drag-and-drop", "dnd", "sortable", "kanban", "reorder", "accessibility", "ui", "react"],
   avoid="Mutating state arrays during drag; mouse-only reordering; renumbering every row on each move."),
]
