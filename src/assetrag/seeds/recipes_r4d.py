"""Round-4 recipes, part D (Python, `unittest`): game algorithms and a polite web crawler."""
from .recipes import RC, UT

R4D = [
RC("py-grid-astar-pathfinding", "A* and Dijkstra on a weighted grid: 4/8-way movement without corner cutting, octile heuristic, distance maps, expansion limit", "python",
   "Move a unit across a tile map, find the cheapest route through terrain, or compute 'distance to the player' for every tile for AI.",
   "A grid parsed from text ('#' wall, digits 1-9 as terrain cost), A* with a binary heap and tie-breaking counter, an admissible heuristic (Manhattan for 4-way, octile for 8-way) so the result is optimal, diagonal moves that may not cut wall corners and cost sqrt(2), stale-entry skipping instead of decrease-key, a cap on expansions for real-time budgets, and a Dijkstra distance map. Property-tested: A* cost equals Dijkstra cost on hundreds of random grids.",
   {"gridpath.py": r'''"""Grid pathfinding. Coordinates are (x, y) with y growing downward, like screen/tile maps."""
import heapq
import math
from itertools import count

SQRT2 = math.sqrt(2)


class Grid:
    def __init__(self, rows):
        """rows: list of equal-length strings. '#' = wall, '1'-'9' = cost to ENTER the tile, anything else ('.', 'S', 'G') = cost 1."""
        if not rows or len({len(r) for r in rows}) != 1:
            raise ValueError("grid must be non-empty and rectangular")
        self.h, self.w = len(rows), len(rows[0])
        self.cost = [[None if c == "#" else int(c) if c.isdigit() and c != "0" else 1 for c in r] for r in rows]

    def passable(self, x, y):
        return 0 <= x < self.w and 0 <= y < self.h and self.cost[y][x] is not None

    def neighbors(self, x, y, diagonal=True):
        """Yield (nx, ny, step_cost). A diagonal step needs BOTH orthogonal neighbours open, so units cannot squeeze through wall corners."""
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if self.passable(x + dx, y + dy):
                yield x + dx, y + dy, self.cost[y + dy][x + dx]
        if diagonal:
            for dx, dy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                if self.passable(x + dx, y + dy) and self.passable(x + dx, y) and self.passable(x, y + dy):
                    yield x + dx, y + dy, self.cost[y + dy][x + dx] * SQRT2


def heuristic(a, b, diagonal):
    dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
    return (dx + dy) + (SQRT2 - 2) * min(dx, dy) if diagonal else dx + dy       # octile / manhattan; admissible because every tile costs >= 1


def astar(grid, start, goal, diagonal=True, max_expansions=None):
    """-> {'path': [(x, y), ...], 'cost': float, 'expanded': int} or None (blocked/unreachable/over budget)."""
    if not grid.passable(*start) or not grid.passable(*goal):
        return None
    tie = count()                                        # heap tie-break: never compare tuples' nodes, and FIFO among equal f
    open_ = [(heuristic(start, goal, diagonal), next(tie), 0.0, start)]
    best, came, expanded = {start: 0.0}, {}, 0
    while open_:
        _, _, g, cur = heapq.heappop(open_)
        if g > best.get(cur, math.inf):
            continue                                     # stale entry: a cheaper route to cur was found after this was queued
        if cur == goal:
            path = [cur]
            while cur in came:
                cur = came[cur]
                path.append(cur)
            return dict(path=path[::-1], cost=g, expanded=expanded)
        expanded += 1
        if max_expansions is not None and expanded > max_expansions:
            return None
        for nx, ny, step in grid.neighbors(*cur, diagonal):
            ng = g + step
            if ng < best.get((nx, ny), math.inf):
                best[(nx, ny)] = ng
                came[(nx, ny)] = cur
                heapq.heappush(open_, (ng + heuristic((nx, ny), goal, diagonal), next(tie), ng, (nx, ny)))
    return None


def distance_map(grid, source, diagonal=True):
    """Dijkstra from one tile to every reachable tile: {(x, y): cost}. Use for 'flee/approach' AI and reachability checks."""
    dist, tie, heap = {source: 0.0}, count(), [(0.0, 0, source)]
    if not grid.passable(*source):
        return {}
    while heap:
        d, _, cur = heapq.heappop(heap)
        if d > dist[cur]:
            continue
        for nx, ny, step in grid.neighbors(*cur, diagonal):
            if d + step < dist.get((nx, ny), math.inf):
                dist[(nx, ny)] = d + step
                heapq.heappush(heap, (d + step, next(tie), (nx, ny)))
    return dist


def downhill_step(dist, pos):
    """Next tile when following a distance_map toward its source (flow-field style); None at the source."""
    options = [(d, p) for p, d in dist.items() if abs(p[0] - pos[0]) <= 1 and abs(p[1] - pos[1]) <= 1 and p != pos and d < dist.get(pos, math.inf)]
    return min(options)[1] if options else None
''', "test_gridpath.py": r'''import math, random, unittest
import gridpath as G

MAP = ["S...#...",
       ".##.#.#.",
       ".#..#.#.",
       ".#.##.#.",
       ".#....#G",
       "........"]


def valid(grid, path, diagonal):
    for a, b in zip(path, path[1:]):
        assert grid.passable(*b) and max(abs(a[0] - b[0]), abs(a[1] - b[1])) == 1
        if abs(a[0] - b[0]) + abs(a[1] - b[1]) == 2:
            assert diagonal and grid.passable(b[0], a[1]) and grid.passable(a[0], b[1]), "corner cut"
    return True


class Astar(unittest.TestCase):
    def test_straight_line_costs(self):
        g = G.Grid(["....."]); self.assertEqual(G.astar(g, (0, 0), (4, 0))["cost"], 4); self.assertEqual(G.astar(g, (0, 0), (4, 0))["path"], [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)])
        d = G.Grid(["...", "...", "..."]); self.assertAlmostEqual(G.astar(d, (0, 0), (2, 2))["cost"], 2 * math.sqrt(2)); self.assertEqual(G.astar(d, (0, 0), (2, 2), diagonal=False)["cost"], 4)

    def test_start_equals_goal_and_blocked_endpoints(self):
        g = G.Grid(["..#"]); self.assertEqual(G.astar(g, (0, 0), (0, 0)), dict(path=[(0, 0)], cost=0.0, expanded=0))
        self.assertIsNone(G.astar(g, (0, 0), (2, 0))); self.assertIsNone(G.astar(g, (5, 5), (0, 0))); self.assertIsNone(G.astar(g, (0, 0), (-1, 0)))

    def test_route_around_walls_and_unreachable(self):
        g = G.Grid(MAP); r = G.astar(g, (0, 0), (7, 4)); self.assertTrue(valid(g, r["path"], True)); self.assertEqual((r["path"][0], r["path"][-1]), ((0, 0), (7, 4)))
        walled = G.Grid(["..#..", "..#..", "..#.."]); self.assertIsNone(G.astar(walled, (0, 0), (4, 0)))

    def test_no_corner_cutting(self):
        g = G.Grid([".#", "#."]); self.assertIsNone(G.astar(g, (0, 0), (1, 1)))                   # the only link is a diagonal squeeze between two walls
        self.assertEqual(sorted((x, y) for x, y, _ in g.neighbors(0, 0)), [])

    def test_prefers_cheap_detour_over_expensive_tiles(self):
        g = G.Grid(["1991", "1111"]); r = G.astar(g, (0, 0), (3, 0), diagonal=False)
        self.assertEqual(r["cost"], 5); self.assertTrue(all(p[1] == 1 or p in ((0, 0), (3, 0)) for p in r["path"]))

    def test_expansion_budget(self):
        g = G.Grid(["." * 40] * 40); self.assertIsNone(G.astar(g, (0, 0), (39, 39), max_expansions=5)); self.assertIsNotNone(G.astar(g, (0, 0), (39, 39)))

    def test_optimal_against_dijkstra_on_random_grids(self):
        rng = random.Random(1234)
        for _ in range(300):
            w, h = rng.randint(2, 12), rng.randint(2, 12)
            rows = ["".join(rng.choice("....1239#") for _ in range(w)) for _ in range(h)]; g = G.Grid(rows)
            free = [(x, y) for y in range(h) for x in range(w) if g.passable(x, y)]
            if len(free) < 2: continue
            a, b = rng.sample(free, 2)
            for diag in (True, False):
                dm = G.distance_map(g, a, diag); r = G.astar(g, a, b, diag)
                if b not in dm: self.assertIsNone(r, rows)
                else:
                    self.assertAlmostEqual(r["cost"], dm[b], msg=str(rows)); self.assertTrue(valid(g, r["path"], diag))

    def test_bad_grids(self):
        for rows in ([], ["..", "."]):
            with self.assertRaises(ValueError): G.Grid(rows)


class DistanceMap(unittest.TestCase):
    def test_distances_and_walls(self):
        g = G.Grid(["..#.", "..#."]); d = G.distance_map(g, (0, 0), diagonal=False); self.assertEqual(d[(1, 1)], 2); self.assertNotIn((3, 0), d); self.assertEqual(G.distance_map(g, (2, 0)), {})

    def test_following_downhill_reaches_source(self):
        g = G.Grid(MAP); d = G.distance_map(g, (0, 0)); pos = (7, 4); steps = 0
        while pos != (0, 0): pos = G.downhill_step(d, pos); steps += 1; self.assertLess(steps, 60)
        self.assertIsNone(G.downhill_step(d, (0, 0)))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Uniform grid with per-tile entry cost >= 1 (the heuristic relies on it); for costs < 1 scale the map", "Optimal paths can look jagged: smooth with line-of-sight string pulling or use Theta*/JPS when that matters", "Very large or dynamic maps want hierarchical pathfinding or flow fields; this is the 95% building block"], compat={"python": ">=3.9"},
   tags=["pathfinding", "a-star", "astar", "dijkstra", "grid", "game", "tilemap", "roguelike", "ai", "heuristic", "shortest-path", "flow-field", "navigation", "algorithm", "heap"],
   avoid="An inadmissible heuristic (path not optimal); diagonal moves through wall corners; comparing node tuples in the heap (use a counter); decrease-key hacks instead of skipping stale entries; no expansion cap in a frame loop."),

RC("py-collision-aabb-circle-ray-spatial-hash", "2D collision toolkit: AABB/circle tests with push-out vectors, ray casts, swept boxes against tunnelling, spatial-hash broadphase", "python",
   "Overlap tests that return how to separate the shapes, bullets that don't skip through thin walls, and 'who is near me' queries that stay fast with thousands of objects.",
   "Strict-overlap AABB and circle tests with minimum-translation vectors, circle-vs-rectangle via closest point (including the centre-inside case), slab-method ray vs box returning entry distance and normal, swept AABB (time of impact along a velocity) to stop fast movers tunnelling, and a spatial hash broadphase with insert/move/remove, region queries and duplicate-free candidate pairs - property-tested against brute force.",
   {"collide.py": r'''"""2D collision helpers. Boxes are (x, y, w, h) with y down; circles are (cx, cy, r). Touching edges do NOT overlap."""
import math
from collections import defaultdict


def aabb_overlap(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def aabb_push(a, b):
    """Minimum translation vector to move `a` out of `b` along the axis of least penetration; None if apart."""
    if not aabb_overlap(a, b):
        return None
    left, right = b[0] + b[2] - a[0], a[0] + a[2] - b[0]
    up, down = b[1] + b[3] - a[1], a[1] + a[3] - b[1]
    px = left if left < right else -right
    py = up if up < down else -down
    return (px, 0) if abs(px) <= abs(py) else (0, py)


def circle_overlap(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 < (a[2] + b[2]) ** 2


def circle_push(a, b):
    """Vector that moves circle a out of circle b; None if apart. Concentric circles get an arbitrary +x push instead of dividing by zero."""
    if not circle_overlap(a, b):
        return None
    dx, dy = a[0] - b[0], a[1] - b[1]
    d = math.hypot(dx, dy)
    if d == 0:
        return (a[2] + b[2], 0.0)
    pen = a[2] + b[2] - d
    return (dx / d * pen, dy / d * pen)


def circle_rect_push(c, r):
    """Circle vs box via the closest point on the box. If the centre is inside the box, push out through the nearest face."""
    cx, cy, rad = c
    nx, ny = min(max(cx, r[0]), r[0] + r[2]), min(max(cy, r[1]), r[1] + r[3])
    dx, dy = cx - nx, cy - ny
    d2 = dx * dx + dy * dy
    if d2 >= rad * rad:
        return None
    if d2 == 0:                                          # centre inside (or on the edge of) the box
        gaps = [(cx - r[0], (-1, 0)), (r[0] + r[2] - cx, (1, 0)), (cy - r[1], (0, -1)), (r[1] + r[3] - cy, (0, 1))]
        g, (ux, uy) = min(gaps)
        return ((g + rad) * ux, (g + rad) * uy)
    d = math.sqrt(d2)
    return (dx / d * (rad - d), dy / d * (rad - d))


def ray_box(origin, direction, box):
    """Slab method. -> (t, normal) for the first hit with t >= 0 (point = origin + direction * t), else None. Direction need not be unit length."""
    tmin, tmax, normal = -math.inf, math.inf, (0, 0)
    for axis in (0, 1):
        o, d, lo, hi = origin[axis], direction[axis], box[axis], box[axis] + box[2 + axis]
        if d == 0:
            if o < lo or o > hi:
                return None
            continue
        t1, t2 = (lo - o) / d, (hi - o) / d
        n = -1 if t1 < t2 else 1
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > tmin:
            tmin, normal = t1, ((n, 0) if axis == 0 else (0, n))
        tmax = min(tmax, t2)
        if tmin > tmax:
            return None
    if tmax < 0:
        return None
    return (max(tmin, 0.0), normal) if tmin >= 0 else (0.0, (0, 0))      # origin already inside


def swept_aabb(box, velocity, other):
    """Time of impact in [0, 1] when `box` moves by `velocity` this frame against a static `other`, plus the surface normal. Stops fast movers tunnelling through thin walls."""
    expanded = (other[0] - box[2] / 2, other[1] - box[3] / 2, other[2] + box[2], other[3] + box[3])      # Minkowski sum
    centre = (box[0] + box[2] / 2, box[1] + box[3] / 2)
    hit = ray_box(centre, velocity, expanded)
    if hit is None or hit[0] > 1:
        return None
    return hit


class SpatialHash:
    """Uniform grid broadphase. Pick cell size around the typical object size; narrow-phase with the tests above."""

    def __init__(self, cell=64):
        self.cell, self.cells, self.boxes = cell, defaultdict(set), {}

    def _keys(self, b):
        c = self.cell
        return [(i, j) for i in range(math.floor(b[0] / c), math.floor((b[0] + b[2]) / c) + 1) for j in range(math.floor(b[1] / c), math.floor((b[1] + b[3]) / c) + 1)]

    def insert(self, ident, box):
        if ident in self.boxes:
            self.remove(ident)
        self.boxes[ident] = box
        for k in self._keys(box):
            self.cells[k].add(ident)

    def remove(self, ident):
        for k in self._keys(self.boxes.pop(ident)):
            self.cells[k].discard(ident)
            if not self.cells[k]:
                del self.cells[k]

    move = insert

    def query(self, box):
        """Ids whose boxes overlap `box` (exact test, not just same cell)."""
        found = set()
        for k in self._keys(box):
            found |= self.cells.get(k, set())
        return {i for i in found if aabb_overlap(self.boxes[i], box)}

    def pairs(self):
        """Each overlapping pair once, as (a, b) with a < b."""
        out = set()
        for ids in self.cells.values():
            ids = sorted(ids)
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    if aabb_overlap(self.boxes[a], self.boxes[b]):
                        out.add((a, b))
        return out
''', "test_collide.py": r'''import math, random, unittest
import collide as C


class Boxes(unittest.TestCase):
    def test_touching_is_not_overlapping(self):
        self.assertFalse(C.aabb_overlap((0, 0, 10, 10), (10, 0, 5, 5))); self.assertTrue(C.aabb_overlap((0, 0, 10, 10), (9.9, 0, 5, 5)))

    def test_push_vector_separates(self):
        a, b = (8, 2, 10, 10), (0, 0, 10, 10); v = C.aabb_push(a, b); self.assertEqual(v, (2, 0))
        moved = (a[0] + v[0], a[1] + v[1], a[2], a[3]); self.assertFalse(C.aabb_overlap(moved, b))
        self.assertEqual(C.aabb_push((2, 8, 6, 6), b), (0, 2)); self.assertEqual(C.aabb_push((-9, 3, 10, 4), b), (-1, 0)); self.assertIsNone(C.aabb_push((20, 20, 1, 1), b))


class Circles(unittest.TestCase):
    def test_circle_push_separates_exactly(self):
        a, b = (3, 4, 5), (0, 0, 5); v = C.circle_push(a, b); moved = (a[0] + v[0], a[1] + v[1], 5)
        self.assertAlmostEqual(math.hypot(moved[0], moved[1]), 10); self.assertIsNone(C.circle_push((10, 0, 5), b)); self.assertEqual(C.circle_push((0, 0, 1), (0, 0, 2)), (3, 0.0))

    def test_circle_rect(self):
        r = (0, 0, 10, 10)
        self.assertIsNone(C.circle_rect_push((15, 5, 4), r)); v = C.circle_rect_push((13, 5, 4), r); self.assertEqual((round(v[0], 6), round(v[1], 6)), (1, 0))
        corner = C.circle_rect_push((12, 12, 3), r); self.assertAlmostEqual(math.hypot(*corner), 3 - math.hypot(2, 2)); self.assertGreater(corner[0], 0)
        inside = C.circle_rect_push((8, 5, 1), r); self.assertEqual(inside, (3, 0))                      # centre inside: leave through the nearest face

    def test_circle_just_touching_does_not_collide(self):
        self.assertIsNone(C.circle_rect_push((14, 5, 4), (0, 0, 10, 10)))


class Rays(unittest.TestCase):
    box = (10, 10, 10, 10)

    def test_hit_distance_and_normal(self):
        t, n = C.ray_box((0, 15), (1, 0), self.box); self.assertEqual((t, n), (10, (-1, 0)))
        t, n = C.ray_box((15, 30), (0, -2), self.box); self.assertEqual((t, n), (5, (0, 1)))

    def test_misses_and_behind(self):
        for o, d in (((0, 0), (1, 0)), ((0, 15), (-1, 0)), ((15, 0), (0, 0)), ((0, 25), (1, 0.001))): self.assertIsNone(C.ray_box(o, d, self.box), (o, d))

    def test_origin_inside(self):
        self.assertEqual(C.ray_box((15, 15), (1, 0), self.box), (0.0, (0, 0)))


class Swept(unittest.TestCase):
    wall = (50, 0, 2, 100)

    def test_fast_bullet_would_tunnel_with_discrete_test_but_swept_catches_it(self):
        bullet, vel = (0, 40, 4, 4), (200, 0)
        after = (bullet[0] + vel[0], bullet[1] + vel[1], 4, 4); self.assertFalse(C.aabb_overlap(after, self.wall))        # discrete check: no collision at all
        t, n = C.swept_aabb(bullet, vel, self.wall); self.assertAlmostEqual(t, (50 - 4) / 200); self.assertEqual(n, (-1, 0))

    def test_not_reaching_this_frame(self):
        self.assertIsNone(C.swept_aabb((0, 40, 4, 4), (10, 0), self.wall)); self.assertIsNone(C.swept_aabb((0, 200, 4, 4), (200, 0), self.wall))


class Hash(unittest.TestCase):
    def test_matches_brute_force(self):
        rng = random.Random(7); h = C.SpatialHash(32); boxes = {}
        for i in range(300):
            boxes[i] = (rng.uniform(0, 500), rng.uniform(0, 500), rng.uniform(1, 40), rng.uniform(1, 40)); h.insert(i, boxes[i])
        brute = {(a, b) for a in boxes for b in boxes if a < b and C.aabb_overlap(boxes[a], boxes[b])}
        self.assertEqual(h.pairs(), brute); self.assertGreater(len(brute), 5)
        q = (100, 100, 80, 80); self.assertEqual(h.query(q), {i for i, b in boxes.items() if C.aabb_overlap(b, q)})

    def test_move_remove_and_negative_coordinates(self):
        h = C.SpatialHash(10); h.insert("a", (-25, -25, 5, 5)); h.insert("b", (100, 100, 5, 5)); self.assertEqual(h.query((-30, -30, 20, 20)), {"a"})
        h.move("a", (99, 99, 5, 5)); self.assertEqual(h.pairs(), {("a", "b")}); h.remove("b"); self.assertEqual(h.pairs(), set()); self.assertEqual(h.query((-30, -30, 20, 20)), set())
        h.remove("a"); self.assertEqual(dict(h.cells), {})                                                  # no empty cells leak

    def test_big_object_spans_many_cells_pair_reported_once(self):
        h = C.SpatialHash(10); h.insert(1, (0, 0, 100, 100)); h.insert(2, (50, 50, 30, 30)); self.assertEqual(h.pairs(), {(1, 2)})


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Axis-aligned boxes and circles only; rotated shapes need SAT/GJK (shapely, pymunk, or a physics engine)", "Swept test handles one static target per call: sort hits by t and resolve the earliest for multi-object levels", "For a handful of objects brute force is faster than any broadphase - measure first"], compat={"python": ">=3.9"},
   tags=["collision", "game", "aabb", "bounding-box", "circle", "rectangle", "raycast", "ray", "swept", "tunnelling", "spatial-hash", "broadphase", "physics", "hitbox", "2d", "grid", "quadtree", "nearby"],
   avoid="Treating touching edges as collisions; discrete overlap checks for fast bullets; O(n^2) pair loops for thousands of objects; dividing by zero for concentric circles; forgetting objects that span several grid cells."),

RC("py-game-loop-fsm-ecs-save-rng", "Game architecture basics: fixed-timestep loop, state machine with guards, entity-component store, versioned JSON save/load, seeded weighted RNG", "python",
   "The skeleton under most small games and simulations: deterministic updates independent of frame rate, AI/UI states, entities without deep inheritance, saves that survive game updates, and reproducible randomness.",
   "A fixed-step accumulator with a spiral-of-death cap and render interpolation alpha; a table-driven finite state machine (enter/exit hooks, guarded transitions, ignored invalid events); a small entity-component world with typed components and smallest-store-first queries that are safe to mutate while iterating; atomic, versioned save files with a migration chain and a refusal to load saves from the future; and a seeded RNG with weighted choice, shuffle bag and state capture for replays.",
   {"gamecore.py": r'''"""Small, dependency-free game-architecture parts."""
import json, os, random


class FixedStep:
    """Run `update(step)` zero or more times per rendered frame so physics/AI are deterministic at any FPS."""

    def __init__(self, update, step=1 / 60, max_frame=0.25):
        self.update, self.step, self.max_frame, self.acc, self.ticks = update, step, max_frame, 0.0, 0

    def advance(self, frame_dt):
        self.acc += min(frame_dt, self.max_frame)         # after a hitch (breakpoint, window drag) do not try to catch up for seconds: spiral of death
        while self.acc >= self.step - 1e-12:
            self.update(self.step)
            self.acc -= self.step
            self.ticks += 1
        return max(self.acc, 0.0) / self.step              # alpha for interpolating the render between the last two states


class FSM:
    def __init__(self, initial, transitions, on_enter=None, on_exit=None):
        """transitions: {(state, event): (new_state, guard_or_None)}"""
        self.state, self.table, self.enter, self.exit, self.history = initial, transitions, on_enter or {}, on_exit or {}, [initial]
        if initial in self.enter:
            self.enter[initial](None)

    def fire(self, event, **ctx):
        """-> True if a transition happened. Unknown events and failed guards are ignored, not errors (a 'jump' while stunned is normal)."""
        t = self.table.get((self.state, event))
        if not t or (t[1] and not t[1](**ctx)):
            return False
        old, new = self.state, t[0]
        if old in self.exit:
            self.exit[old](new)
        self.state = new
        self.history.append(new)
        if new in self.enter:
            self.enter[new](old)
        return True


class World:
    """Entities are ints; components are plain objects stored by type."""

    def __init__(self):
        self.next_id, self.stores = 1, {}

    def create(self, *components):
        e = self.next_id
        self.next_id += 1
        for c in components:
            self.add(e, c)
        return e

    def add(self, e, comp):
        self.stores.setdefault(type(comp), {})[e] = comp

    def get(self, e, kind):
        return self.stores.get(kind, {}).get(e)

    def remove(self, e, kind):
        self.stores.get(kind, {}).pop(e, None)

    def destroy(self, e):
        for s in self.stores.values():
            s.pop(e, None)

    def query(self, *kinds):
        """[(entity, comp1, comp2, ...)] for entities having ALL kinds. A snapshot list, so systems may add/destroy while looping."""
        if any(k not in self.stores for k in kinds):
            return []
        smallest = min(kinds, key=lambda k: len(self.stores[k]))
        return [(e, *[self.stores[k][e] for k in kinds]) for e in list(self.stores[smallest]) if all(e in self.stores[k] for k in kinds)]


class SaveError(Exception):
    pass


def save_game(path, state, version):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": version, "state": state}, f, sort_keys=True, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)                                  # a crash mid-save leaves the previous save intact


def load_game(path, current_version, migrations):
    """migrations: {from_version: fn(state) -> state at from_version + 1}. Old saves are upgraded step by step; newer-than-us saves are refused, not guessed at."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        v, state = doc["version"], doc["state"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise SaveError("unreadable save: %s" % e)
    if v > current_version:
        raise SaveError("save is from a newer game version (%s > %s)" % (v, current_version))
    while v < current_version:
        if v not in migrations:
            raise SaveError("no migration from save version %s" % v)
        state, v = migrations[v](state), v + 1
    return state


class Rng:
    """Seeded randomness you can snapshot: replays, daily seeds, and 'save-scumming' control all need this."""

    def __init__(self, seed):
        self.r = random.Random(seed)

    def weighted(self, table):
        """table: [(item, weight)] with weight >= 0 and a positive total."""
        total = sum(w for _, w in table)
        if total <= 0 or any(w < 0 for _, w in table):
            raise ValueError("weights must be non-negative with a positive sum")
        x = self.r.random() * total
        for item, w in table:
            x -= w
            if x < 0 and w > 0:
                return item
        return next(i for i, w in reversed(table) if w > 0)     # float rounding fallback

    def bag(self, items):
        """Shuffle-bag: every item once per cycle (Tetris-style fairness instead of long streaks)."""
        while True:
            cycle = list(items)
            self.r.shuffle(cycle)
            yield from cycle

    def getstate(self):
        v, internal, g = self.r.getstate()
        return [v, list(internal), g]

    def setstate(self, s):
        self.r.setstate((s[0], tuple(s[1]), s[2]))
''', "test_gamecore.py": r'''import collections, json, os, tempfile, unittest
from dataclasses import dataclass
import gamecore as G


class Loop(unittest.TestCase):
    def test_same_number_of_updates_regardless_of_frame_rate(self):
        for fps in (30, 60, 144, 7):
            n = []; f = G.FixedStep(lambda dt: n.append(dt)); alpha = 0
            for _ in range(fps * 2): alpha = f.advance(1 / fps)
            self.assertAlmostEqual(f.ticks, 120, delta=1, msg=fps); self.assertTrue(0 <= alpha < 1)

    def test_hitch_is_capped(self):
        f = G.FixedStep(lambda dt: None); f.advance(10.0); self.assertLessEqual(f.ticks, 16)       # 0.25s / (1/60) = 15 steps, not 600

    def test_alpha(self):
        f = G.FixedStep(lambda dt: None, step=0.1); self.assertAlmostEqual(f.advance(0.15), 0.5)


class Machine(unittest.TestCase):
    def build(self, log, hp):
        t = {("idle", "move"): ("walk", None), ("walk", "stop"): ("idle", None), ("idle", "hit"): ("stunned", lambda dmg: dmg > 0), ("stunned", "recover"): ("idle", lambda: hp["v"] > 0)}
        return G.FSM("idle", t, on_enter={s: (lambda old, s=s: log.append(("enter", s, old))) for s in ("idle", "walk", "stunned")}, on_exit={s: (lambda new, s=s: log.append(("exit", s, new))) for s in ("idle", "walk", "stunned")})

    def test_hooks_order_guards_and_ignored_events(self):
        log, hp = [], {"v": 0}; m = self.build(log, hp)
        self.assertTrue(m.fire("move")); self.assertFalse(m.fire("jump")); self.assertFalse(m.fire("hit", dmg=5)); self.assertTrue(m.fire("stop"))
        self.assertFalse(m.fire("hit", dmg=0)); self.assertTrue(m.fire("hit", dmg=3)); self.assertFalse(m.fire("recover")); hp["v"] = 1; self.assertTrue(m.fire("recover"))
        self.assertEqual(m.history, ["idle", "walk", "idle", "stunned", "idle"]); self.assertEqual(log[:3], [("enter", "idle", None), ("exit", "idle", "walk"), ("enter", "walk", "idle")])


@dataclass
class Pos: x: float = 0; y: float = 0
@dataclass
class Vel: dx: float = 0; dy: float = 0
@dataclass
class Health: hp: int = 10


class Ecs(unittest.TestCase):
    def test_query_requires_all_components(self):
        w = G.World(); a = w.create(Pos(), Vel(1, 0)); b = w.create(Pos()); c = w.create(Vel(), Health())
        self.assertEqual([e for e, *_ in w.query(Pos, Vel)], [a]); self.assertEqual([e for e, *_ in w.query(Pos)], [a, b]); self.assertEqual(w.query(Health, Pos), []); self.assertEqual(w.query(str), [])

    def test_movement_system_and_safe_destroy_while_iterating(self):
        w = G.World(); ids = [w.create(Pos(), Vel(1, 2), Health(1)) for _ in range(5)]
        for e, p, v, h in w.query(Pos, Vel, Health):
            p.x += v.dx; p.y += v.dy
            if e in ids[::2]: w.destroy(e)
        self.assertEqual([e for e, *_ in w.query(Pos)], [ids[1], ids[3]]); self.assertEqual(w.get(ids[1], Pos), Pos(1, 2)); self.assertIsNone(w.get(ids[0], Health))
        w.remove(ids[1], Vel); self.assertEqual([e for e, *_ in w.query(Pos, Vel)], [ids[3]])


class Saves(unittest.TestCase):
    @staticmethod
    def m1(s): s = dict(s); s["gold"] = s.pop("coins"); return s

    @staticmethod
    def m2(s): s = dict(s); s["inventory"] = {"items": s.pop("items")}; return s

    MIG = {1: m1.__func__, 2: m2.__func__}

    def path(self): return os.path.join(tempfile.mkdtemp(), "s.json")

    def test_round_trip(self):
        p = self.path(); G.save_game(p, {"hp": 5}, 3); self.assertEqual(G.load_game(p, 3, self.MIG), {"hp": 5}); self.assertEqual(os.listdir(os.path.dirname(p)), ["s.json"])

    def test_old_save_is_migrated_through_every_step(self):
        p = self.path(); G.save_game(p, {"coins": 7, "items": ["sword"]}, 1); self.assertEqual(G.load_game(p, 3, self.MIG), {"gold": 7, "inventory": {"items": ["sword"]}})

    def test_future_corrupt_and_unmigratable_are_refused_clearly(self):
        p = self.path(); G.save_game(p, {}, 9)
        with self.assertRaisesRegex(G.SaveError, "newer"): G.load_game(p, 3, self.MIG)
        open(p, "w").write("{not json")
        with self.assertRaisesRegex(G.SaveError, "unreadable"): G.load_game(p, 3, self.MIG)
        with self.assertRaisesRegex(G.SaveError, "unreadable"): G.load_game(p + ".missing", 3, self.MIG)
        G.save_game(p, {}, 0)
        with self.assertRaisesRegex(G.SaveError, "no migration"): G.load_game(p, 3, self.MIG)


class Randomness(unittest.TestCase):
    def test_same_seed_same_sequence(self):
        a, b = G.Rng(42), G.Rng(42); t = [("c", 70), ("u", 25), ("r", 5)]; self.assertEqual([a.weighted(t) for _ in range(50)], [b.weighted(t) for _ in range(50)])

    def test_weights_respected_and_zero_weight_never_drawn(self):
        r = G.Rng(1); n = collections.Counter(r.weighted([("a", 90), ("b", 10), ("never", 0)]) for _ in range(5000))
        self.assertEqual(n["never"], 0); self.assertTrue(0.86 < n["a"] / 5000 < 0.94)
        for bad in ([], [("a", 0)], [("a", -1), ("b", 5)]):
            with self.assertRaises(ValueError): r.weighted(bad)

    def test_shuffle_bag_has_no_long_streaks(self):
        it = G.Rng(3).bag("ABC"); out = [next(it) for _ in range(300)]
        for i in range(0, 300, 3): self.assertEqual(sorted(out[i:i + 3]), ["A", "B", "C"])

    def test_state_snapshot_replays_exactly(self):
        r = G.Rng(5); [r.weighted([(1, 1), (2, 1)]) for _ in range(10)]; snap = json.loads(json.dumps(r.getstate()))
        want = [r.weighted([(i, 1) for i in range(100)]) for _ in range(20)]; r2 = G.Rng(0); r2.setstate(snap); self.assertEqual([r2.weighted([(i, 1) for i in range(100)]) for _ in range(20)], want)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Single-threaded, pure-Python building blocks; for large entity counts use numpy-backed storage or an engine (esper, pygame-ce, Godot, Bevy)", "Save files are plain JSON: add a checksum/signature if tampering matters, and never pickle saves", "Interpolating render state with the returned alpha is up to the caller"], compat={"python": ">=3.9"},
   tags=["game", "game-loop", "fixed-timestep", "state-machine", "fsm", "ecs", "entity-component", "save-game", "load", "serialize", "json", "migration", "versioning", "rng", "seed", "weighted-random", "shuffle-bag", "deterministic", "replay", "ai"],
   avoid="Variable-dt physics; letting a long frame trigger hundreds of catch-up updates; pickle saves; overwriting the save in place; deep inheritance trees for game objects; unseeded random in anything that must be replayable."),

RC("py-polite-crawler-extract-normalize", "Web scraping core: link/text/article extraction with html.parser, URL normalisation and dedupe, robots.txt, per-host delay, retries, disk cache, concurrent downloads", "python",
   "Write a crawler or scraper that behaves: parse pages, pull out links and the main article text, never visit the same page twice under different URLs, obey robots.txt and rate limits, survive flaky servers, and cache responses.",
   "Everything is injectable (fetch, clock, sleep) so it is testable without a network. HTMLParser-based link/title/meta extraction that honours <base>; article extraction by paragraph-text score per container with link-density rejection; URL normalisation (resolve, lowercase host, drop fragment/default port/tracking params, sort query, http(s) only); robots.txt via urllib.robotparser with RFC 9309 failure semantics (4xx = allow, 5xx/unreachable = disallow); BFS crawler with depth/page caps, same-host rule, per-host delay honouring Crawl-delay, retry with backoff on 5xx/errors, and reasons for every skipped URL; TTL disk cache; and a thread-pool downloader that collects failures instead of raising.",
   {"crawl.py": r'''"""Polite crawler building blocks. `fetch(url) -> (status, content_type, body_text)` is injected: plug in urllib, requests/httpx, or a headless browser for JS-rendered pages."""
import hashlib, json, os, time
import urllib.parse as up
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from urllib.robotparser import RobotFileParser

TRACKING = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref_src")
SKIP_TEXT = {"script", "style", "noscript", "template", "svg"}


class Page(HTMLParser):
    """One pass: links, title, meta description, canonical, <base href>, visible text, and per-container paragraph scoring."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.title, self.desc, self.canonical, self.base = [], "", "", None, None
        self.text, self._skip, self._stack, self._in_title, self._in_p, self._in_a = [], 0, [], False, None, 0
        self.blocks = {}                                # container id -> [paragraph texts]; link text counted separately

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag not in ("br", "img", "meta", "link", "input", "hr"):
            self._stack.append((tag, len(self._stack) and id(self._stack[-1])))
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
            self._in_a += 1
        elif tag == "base" and a.get("href") and not self.base:
            self.base = a["href"]
        elif tag == "meta" and (a.get("name") or "").lower() == "description":
            self.desc = (a.get("content") or "").strip()
        elif tag == "link" and "canonical" in (a.get("rel") or "").lower().split() and a.get("href"):
            self.canonical = a["href"]
        elif tag == "title":
            self._in_title = True
        elif tag == "p":
            self._in_p = {"parent": self._parent_key(), "chars": 0, "link_chars": 0}
        if tag in SKIP_TEXT:
            self._skip += 1

    def _parent_key(self):
        return tuple((t, p) for t, p in self._stack[:-1])           # the path to the paragraph's container identifies it

    def handle_endtag(self, tag):
        if tag in SKIP_TEXT and self._skip:
            self._skip -= 1
        if tag == "a" and self._in_a:
            self._in_a -= 1
        if tag == "title":
            self._in_title = False
        if tag == "p" and self._in_p:
            p = self._in_p
            self._in_p = None
            self.blocks.setdefault(p["parent"], []).append((" ".join("".join(p.get("buf", [])).split()), p["chars"], p["link_chars"]))
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                break

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._skip:
            return
        if data.strip():
            self.text.append(data.strip())
        if self._in_p is not None:
            self._in_p.setdefault("buf", []).append(data)
            self._in_p["chars"] += len(data.strip())
            if self._in_a:
                self._in_p["link_chars"] += len(data.strip())

    def article(self, min_chars=80):
        """Paragraphs of the container with the most non-link text. Navigation, footers and link lists lose; returns '' if nothing substantial."""
        best, best_score = "", 0
        for paras in self.blocks.values():
            good = [t for t, chars, link in paras if chars and link / chars < 0.5]
            score = sum(len(t) for t in good)
            if score > best_score and score >= min_chars:
                best, best_score = "\n\n".join(good), score
        return best


def parse(html):
    p = Page()
    p.feed(html)
    p.close()
    p.title = " ".join(p.title.split())
    return p


def normalize_url(url, base=None):
    """Canonical form for dedupe, or None for non-web schemes (mailto:, javascript:, tel:, data:)."""
    u = up.urljoin(base, url.strip()) if base else url.strip()
    s = up.urlsplit(u)
    if s.scheme.lower() not in ("http", "https") or not s.hostname:
        return None
    host = s.hostname.lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    port = s.port
    netloc = host if port in (None, 80 if s.scheme.lower() == "http" else 443) else "%s:%d" % (host, port)
    q = sorted((k, v) for k, v in up.parse_qsl(s.query, keep_blank_values=True) if not k.lower().startswith(TRACKING))
    return up.urlunsplit((s.scheme.lower(), netloc, s.path or "/", up.urlencode(q), ""))


class Robots:
    """RFC 9309 semantics: 2xx parse; 4xx = no rules (allow); 5xx or unreachable = assume everything disallowed."""

    def __init__(self, fetch, agent):
        self.fetch, self.agent, self.cache = fetch, agent, {}

    def _for(self, url):
        s = up.urlsplit(url)
        origin = "%s://%s" % (s.scheme, s.netloc)
        if origin not in self.cache:
            rp = RobotFileParser()
            try:
                status, _, body = self.fetch(origin + "/robots.txt")
            except Exception:
                status, body = 599, ""
            if status == 200:
                rp.parse(body.splitlines())
            elif 400 <= status < 500:
                rp.parse([])
            else:
                rp.parse(["User-agent: *", "Disallow: /"])
            self.cache[origin] = rp
        return self.cache[origin]

    def allowed(self, url):
        return self._for(url).can_fetch(self.agent, url)

    def delay(self, url):
        return self._for(url).crawl_delay(self.agent) or 0


class DiskCache:
    def __init__(self, directory, ttl=3600, clock=time.time):
        self.dir, self.ttl, self.clock = directory, ttl, clock
        os.makedirs(directory, exist_ok=True)

    def _path(self, url):
        return os.path.join(self.dir, hashlib.sha256(url.encode()).hexdigest() + ".json")

    def get(self, url):
        try:
            with open(self._path(url), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return None
        return tuple(d["value"]) if self.clock() - d["at"] <= self.ttl else None

    def put(self, url, value):
        tmp = self._path(url) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"at": self.clock(), "value": list(value)}, f)
        os.replace(tmp, self._path(url))


def cached(fetch, cache):
    """Only successful responses are cached; errors must be retried, not remembered."""
    def inner(url):
        hit = cache.get(url)
        if hit:
            return hit
        res = fetch(url)
        if res[0] == 200:
            cache.put(url, res)
        return res
    return inner


def crawl(start, fetch, agent="mybot/1.0", max_pages=50, max_depth=3, delay=1.0, retries=2, same_host=True, clock=time.monotonic, sleep=time.sleep):
    """BFS. -> (pages {url: dict(title, text, article, links, depth)}, skipped {url: reason}). Every URL is accounted for."""
    robots, pages, skipped, last_hit = Robots(fetch, agent), {}, {}, {}
    start = normalize_url(start)
    host0 = up.urlsplit(start).netloc
    queue, seen = deque([(start, 0)]), {start}
    while queue and len(pages) < max_pages:
        url, depth = queue.popleft()
        if not robots.allowed(url):
            skipped[url] = "robots.txt"
            continue
        host = up.urlsplit(url).netloc
        wait = max(delay, robots.delay(url)) - (clock() - last_hit.get(host, -1e9))
        if wait > 0:
            sleep(wait)
        res = None
        for attempt in range(retries + 1):
            last_hit[host] = clock()
            try:
                res = fetch(url)
            except Exception as e:
                res = (599, "", str(e))
            if res[0] < 500:
                break
            if attempt < retries:
                sleep(delay * 2 ** attempt)
        status, ctype, body = res
        if status != 200:
            skipped[url] = "http %d" % status
            continue
        if "html" not in ctype:
            skipped[url] = "not html (%s)" % ctype
            continue
        pg = parse(body)
        base = pg.base and normalize_url(pg.base, url) or url
        links = []
        for h in pg.links:
            n = normalize_url(h, base)
            if n and n not in links:
                links.append(n)
        pages[url] = dict(title=pg.title, text=" ".join(pg.text), article=pg.article(), links=links, depth=depth)
        for n in links:
            if n in seen:
                continue
            seen.add(n)
            if same_host and up.urlsplit(n).netloc != host0:
                skipped[n] = "other host"
            elif depth + 1 > max_depth:
                skipped[n] = "too deep"
            else:
                queue.append((n, depth + 1))
    for url, _ in queue:
        skipped.setdefault(url, "page limit")
    return pages, skipped


def download_all(urls, fetch, workers=4, on_progress=None):
    """Concurrent downloads. Results keep input order; failures are collected as (url, error) instead of aborting the batch."""
    results, errors = {}, []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch, u): u for u in urls}
        for i, f in enumerate(as_completed(futs), 1):
            u = futs[f]
            try:
                results[u] = f.result()
            except Exception as e:
                errors.append((u, str(e)))
            if on_progress:
                on_progress(i, len(futs))
    return [results[u] for u in urls if u in results], errors
''', "test_crawl.py": r'''import tempfile, threading, time, unittest
import crawl as C

ARTICLE = """<html><head><title>  My   Post </title><meta name="description" content="About things"><link rel="canonical" href="/post"><base href="https://example.com/blog/"></head>
<body><nav><p><a href="/a">Home</a> <a href="/b">About</a> <a href="/c">Contact us today</a></p></nav>
<div id="main"><p>This is the first long paragraph of the real article with plenty of words in it to count.</p>
<p>Second paragraph, also quite substantial, mentions <a href="next.html">a link</a> but is mostly prose about something.</p><p>Third paragraph closes the story with a decent amount of text as well.</p></div>
<footer><p><a href="/x">Terms</a> <a href="/y">Privacy</a></p></footer><script>var junk = "not visible";</script><style>p{}</style></body></html>"""


class Parsing(unittest.TestCase):
    def test_metadata_links_and_visible_text(self):
        p = C.parse(ARTICLE); self.assertEqual((p.title, p.desc, p.canonical, p.base), ("My Post", "About things", "/post", "https://example.com/blog/"))
        self.assertEqual(p.links, ["/a", "/b", "/c", "next.html", "/x", "/y"]); self.assertNotIn("junk", " ".join(p.text)); self.assertIn("Third paragraph", " ".join(p.text))

    def test_article_is_the_prose_container_not_nav_or_footer(self):
        a = C.parse(ARTICLE).article(); self.assertTrue(a.startswith("This is the first long paragraph")); self.assertIn("Second paragraph, also quite substantial, mentions a link", a)
        self.assertNotIn("Terms", a); self.assertNotIn("Contact", a); self.assertEqual(a.count("\n\n"), 2)

    def test_no_article_on_thin_page_and_broken_html(self):
        self.assertEqual(C.parse("<html><body><p>hi</p></body></html>").article(), ""); C.parse("<div><p>unclosed <b>bold<p>next</div></span>"); C.parse("")


class Urls(unittest.TestCase):
    def test_normalisation(self):
        n = C.normalize_url
        self.assertEqual(n("HTTP://Example.COM:80/a/b?z=1&utm_source=x&a=2#frag"), "http://example.com/a/b?a=2&z=1"); self.assertEqual(n("https://example.com"), "https://example.com/")
        self.assertEqual(n("../x?fbclid=1", "https://e.com/a/b/c"), "https://e.com/a/x"); self.assertEqual(n("//cdn.e.com/y", "https://e.com/"), "https://cdn.e.com/y"); self.assertEqual(n("https://e.com:8443/p"), "https://e.com:8443/p")
        self.assertEqual(n("https://bücher.example/"), "https://xn--bcher-kva.example/")
        for bad in ("mailto:a@b.c", "javascript:void(0)", "tel:123", "data:text/html,hi", "/relative-without-base", ""): self.assertIsNone(n(bad), bad)

    def test_equivalent_urls_collapse(self):
        variants = ["https://e.com/p?a=1&b=2", "HTTPS://E.com:443/p?b=2&a=1#top", "https://e.com/p?utm_campaign=x&b=2&a=1"]; self.assertEqual(len({C.normalize_url(v) for v in variants}), 1)


def site(pages, robots=(200, "User-agent: *\nDisallow: /private\nCrawl-delay: 2\n"), log=None):
    def fetch(url):
        if log is not None: log.append(url)
        if url.endswith("/robots.txt"): return (robots[0], "text/plain", robots[1])
        return pages[url] if url in pages else (404, "text/html", "")
    return fetch


H = lambda *links: (200, "text/html; charset=utf-8", "<html><title>t</title><body>%s</body></html>" % "".join('<a href="%s">l</a>' % l for l in links))


class Crawling(unittest.TestCase):
    PAGES = {"https://s.io/": H("/a", "/b?utm_source=x", "/private/secret", "https://other.com/", "/pdf", "mailto:x@y.z", "#top"), "https://s.io/a": H("/", "/b", "/c"), "https://s.io/b": H(),
             "https://s.io/c": H("/d"), "https://s.io/d": H("/e"), "https://s.io/pdf": (200, "application/pdf", "%PDF"), "https://s.io/private/secret": H()}

    def run_crawl(self, **kw):
        sleeps, t = [], [0.0]
        def sleep(s): sleeps.append(s); t[0] += s
        res = C.crawl("https://s.io", site(self.PAGES, log=kw.pop("log", None)), clock=lambda: t[0], sleep=sleep, **kw); return res, sleeps

    def test_visits_each_page_once_and_explains_every_skip(self):
        (pages, skipped), _ = self.run_crawl(max_depth=2)
        self.assertEqual(sorted(pages), ["https://s.io/", "https://s.io/a", "https://s.io/b", "https://s.io/c"])
        self.assertEqual(skipped["https://s.io/private/secret"], "robots.txt"); self.assertEqual(skipped["https://other.com/"], "other host"); self.assertEqual(skipped["https://s.io/pdf"], "not html (application/pdf)")
        self.assertEqual(skipped["https://s.io/d"], "too deep"); self.assertEqual(pages["https://s.io/c"]["depth"], 2)

    def test_crawl_delay_from_robots_is_honoured(self):
        _, sleeps = self.run_crawl(max_depth=1, delay=0.5); self.assertTrue(sleeps); self.assertTrue(all(abs(s - 2) < 1e-9 for s in sleeps), sleeps)       # Crawl-delay 2 beats our own 0.5

    def test_page_limit(self):
        (pages, skipped), _ = self.run_crawl(max_pages=2); self.assertEqual(len(pages), 2); self.assertIn("page limit", skipped.values())

    def test_robots_failure_modes(self):
        for status, allowed in ((404, True), (403, True), (500, False), (503, False)):
            pages, skipped = C.crawl("https://s.io/", site(self.PAGES, robots=(status, "")), clock=lambda: 0, sleep=lambda s: None, delay=0, max_depth=0); self.assertEqual(bool(pages), allowed, status)
        def boom(url): raise OSError("dns")
        self.assertEqual(C.crawl("https://s.io/", boom, clock=lambda: 0, sleep=lambda s: None)[0], {})

    def test_retries_5xx_with_backoff_then_gives_up_or_succeeds(self):
        calls = {"n": 0}; sleeps = []
        def flaky(url):
            if url.endswith("robots.txt"): return (404, "", "")
            calls["n"] += 1; return (503, "", "") if calls["n"] < 3 else H()
        pages, _ = C.crawl("https://s.io/", flaky, delay=1, sleep=sleeps.append, clock=lambda: 0); self.assertEqual((len(pages), calls["n"]), (1, 3)); self.assertEqual([s for s in sleeps if s > 0], [1, 2])
        always = lambda u: (404, "", "") if u.endswith("robots.txt") else (500, "", ""); p, sk = C.crawl("https://s.io/", always, delay=0, sleep=lambda s: None, clock=lambda: 0); self.assertEqual((p, sk), ({}, {"https://s.io/": "http 500"}))

    def test_fetch_exceptions_are_treated_as_failures(self):
        def f(url):
            if url.endswith("robots.txt"): return (404, "", "")
            raise ConnectionError("reset")
        p, sk = C.crawl("https://s.io/", f, delay=0, sleep=lambda s: None, clock=lambda: 0); self.assertEqual(sk, {"https://s.io/": "http 599"})

    def test_base_href_resolves_links(self):
        pg = {"https://s.io/": (200, "text/html", '<base href="https://s.io/docs/"><a href="x">x</a>'), "https://s.io/docs/x": H()}
        pages, _ = C.crawl("https://s.io/", site(pg, robots=(404, "")), delay=0, sleep=lambda s: None, clock=lambda: 0); self.assertIn("https://s.io/docs/x", pages)


class Caching(unittest.TestCase):
    def test_hit_expiry_and_errors_not_cached(self):
        now = [1000.0]; cache = C.DiskCache(tempfile.mkdtemp(), ttl=60, clock=lambda: now[0]); calls = []
        def fetch(url): calls.append(url); return (200, "text/html", "body") if "ok" in url else (500, "", "")
        f = C.cached(fetch, cache); f("http://x/ok"); f("http://x/ok"); self.assertEqual(len(calls), 1)
        now[0] += 61; f("http://x/ok"); self.assertEqual(len(calls), 2); f("http://x/bad"); f("http://x/bad"); self.assertEqual(len(calls), 4)
        self.assertEqual(f("http://x/ok"), (200, "text/html", "body"))


class Downloads(unittest.TestCase):
    def test_concurrent_ordered_with_progress_and_collected_errors(self):
        lock, state = threading.Lock(), {"now": 0, "peak": 0}; progress = []
        def fetch(u):
            with lock: state["now"] += 1; state["peak"] = max(state["peak"], state["now"])
            time.sleep(0.03)
            with lock: state["now"] -= 1
            if u.endswith("bad"): raise IOError("boom")
            return u.upper()
        urls = ["u%d" % i for i in range(8)] + ["bad"]; out, errs = C.download_all(urls, fetch, workers=3, on_progress=lambda d, n: progress.append((d, n)))
        self.assertEqual(out, [u.upper() for u in urls[:8]]); self.assertEqual(errs, [("bad", "boom")]); self.assertTrue(1 < state["peak"] <= 3); self.assertEqual(progress[-1], (9, 9))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["JavaScript-rendered pages need a browser: inject a Playwright/Selenium-backed fetch(url) -> (status, content_type, html); everything else stays the same", "Article extraction is a text-density heuristic; trafilatura or readability-lxml are better on messy real-world pages", "Respect each site's terms of service as well as robots.txt; the crawler identifies itself with the User-Agent you pass", "Verified against an in-memory fake site, not live servers"], compat={"python": ">=3.9"},
   tags=["scraping", "web-scraping", "crawler", "spider", "html", "parser", "beautifulsoup", "links", "article", "readability", "robots-txt", "rate-limit", "politeness", "retry", "backoff", "cache", "url", "normalize", "dedupe", "download", "concurrent", "playwright", "headless"],
   avoid="Ignoring robots.txt or Crawl-delay; hammering one host with many workers; visiting url?utm_x=1 and url as different pages; regexes for HTML; caching error responses; letting one failed download abort a batch."),
]
