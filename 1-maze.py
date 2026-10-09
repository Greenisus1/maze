#!/usr/bin/env python3
"""
LOW-POLY MAZE  --  a first-person maze built entirely from colour blocks.

Runs in a plain terminal (SSH or the Linux console). No desktop, no X11,
no pip packages: just Python 3 and its standard library.

    python3 maze.py                 # default 12x8 maze
    python3 maze.py --maze 20x12    # bigger maze
    python3 maze.py --fast          # lighter rendering for slow boards
    python3 maze.py --colors 256    # force 256-colour output (auto-detected)

Controls:  W A S D move/strafe   |  mouse, Q/E or arrow keys look
           M toggle minimap      |  X or Esc quit
"""
import argparse
import math
import os
import random
import re
import select
import shutil
import sys
import termios
import time
import tty
from collections import defaultdict
from itertools import groupby

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------
HALF = "\u2580"          # upper half block: fg = top pixel, bg = bottom pixel
PLANE = 0.66             # camera plane length (about 66 degree field of view)
EYE = 0.5                # eye height (walls are 1.0 tall)
RADIUS = 0.23            # player collision radius (corridors are 1.0 wide)
SPEED = 3.0              # tiles per second
MAXD = 9.0               # fog distance in tiles
NB = 8                   # fog bands on walls (flat, stepped = low-poly look)
NBD = NB / MAXD
FQ = 6                   # fog bands on floor / ceiling

FOGC = (206, 190, 235)
CEILC = (118, 190, 245)
FLOORC = (255, 232, 190)
WALLC = [(255, 120, 125), (105, 220, 165), (110, 165, 255),
         (255, 220, 95), (185, 140, 255), (255, 165, 105)]
GEMC = [(255, 90, 170), (80, 225, 255), (255, 235, 80), (255, 150, 60)]
RAINBOW = [(255, 120, 125), (255, 165, 105), (255, 225, 100),
           (110, 220, 165), (110, 165, 255), (185, 140, 255)]
PANEL = (255, 244, 214)
INK = (70, 44, 110)
HUDBG = (44, 36, 72)
FACE_K = (1.0, 0.86, 0.74, 0.62)   # per-face brightness: flat shaded facets


def pack(c):
    return (c[0] << 16) | (c[1] << 8) | c[2]


def mix(a, b, t):
    return (int(a[0] + (b[0] - a[0]) * t),
            int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t))


def dim(c, k):
    return (min(255, int(c[0] * k)), min(255, int(c[1] * k)), min(255, int(c[2] * k)))


# --------------------------------------------------------------------------
# Pre-computed palettes (everything is a packed int for speed)
# --------------------------------------------------------------------------
def _wall_palette():
    pal = []
    for base in WALLC:
        faces = []
        for k in FACE_K:
            bands = []
            for b in range(NB):
                t = (b / NB) * 0.88
                bands.append((pack(mix(dim(base, k), FOGC, t)),
                              pack(mix(dim(base, k * 0.74), FOGC, t))))
            faces.append(bands)
        pal.append(faces)
    return pal


WALLPAL = _wall_palette()
CEIL_TAB = [pack(mix(CEILC, FOGC, q / FQ * 0.92)) for q in range(FQ + 1)]
FLOOR_TAB = [pack(mix(FLOORC, FOGC, q / FQ * 0.92)) for q in range(FQ + 1)]
GATE = []
for _b in range(NB):
    _t = (_b / NB) * 0.88
    GATE.append((pack(mix((30, 160, 95), FOGC, _t)),
                 pack(mix((120, 255, 170), FOGC, _t)),
                 pack(mix((255, 240, 120), FOGC, _t))))
MM_RIM = pack((190, 170, 240))
MM_BG = pack((38, 32, 62))
MM_WALL = pack((150, 130, 200))
MM_FLOOR = pack((246, 240, 255))
MM_GEM = pack((255, 90, 170))
MM_ME = pack((255, 235, 60))
MM_DIR = pack((255, 140, 40))
MM_EXIT = pack((60, 230, 120))

_gem_cache = {}


def gem_col(ci, facet, band):
    key = (ci, facet, band)
    v = _gem_cache.get(key)
    if v is None:
        k = (1.18, 0.98, 0.90, 0.70)[facet]
        v = pack(mix(dim(GEMC[ci], k), FOGC, (band / NB) * 0.88))
        _gem_cache[key] = v
    return v


def bg_column(ph, horizon):
    col = [0] * ph
    half = ph / 2.0
    for y in range(ph):
        d = abs(y + 0.5 - horizon) / half
        q = int(max(0.0, 1.0 - d * 2.2) * FQ)
        col[y] = CEIL_TAB[q] if y + 0.5 < horizon else FLOOR_TAB[q]
    return col


# --------------------------------------------------------------------------
# Terminal colour output
# --------------------------------------------------------------------------
P16 = [(0, 0, 0), (170, 0, 0), (0, 170, 0), (170, 85, 0), (0, 0, 170),
       (170, 0, 170), (0, 170, 170), (170, 170, 170), (85, 85, 85),
       (255, 85, 85), (85, 255, 85), (255, 255, 85), (85, 85, 255),
       (255, 85, 255), (85, 255, 255), (255, 255, 255)]


def make_color(mode):
    fc, bc = {}, {}

    if mode == "true":
        def fg(c):
            s = fc.get(c)
            if s is None:
                s = fc[c] = "\x1b[38;2;%d;%d;%dm" % (c >> 16, (c >> 8) & 255, c & 255)
            return s

        def bg(c):
            s = bc.get(c)
            if s is None:
                s = bc[c] = "\x1b[48;2;%d;%d;%dm" % (c >> 16, (c >> 8) & 255, c & 255)
            return s
    elif mode == "256":
        def idx(c):
            r, g, b = c >> 16, (c >> 8) & 255, c & 255
            return 16 + 36 * min(5, round(r / 51)) + 6 * min(5, round(g / 51)) + min(5, round(b / 51))

        def fg(c):
            s = fc.get(c)
            if s is None:
                s = fc[c] = "\x1b[38;5;%dm" % idx(c)
            return s

        def bg(c):
            s = bc.get(c)
            if s is None:
                s = bc[c] = "\x1b[48;5;%dm" % idx(c)
            return s
    else:  # 16 colours (Linux console)
        def near(c):
            r, g, b = c >> 16, (c >> 8) & 255, c & 255
            return min(range(16), key=lambda i: 2 * (r - P16[i][0]) ** 2
                       + 4 * (g - P16[i][1]) ** 2 + 3 * (b - P16[i][2]) ** 2)

        def fg(c):
            s = fc.get(c)
            if s is None:
                i = near(c)
                s = fc[c] = "\x1b[%dm" % (30 + i if i < 8 else 90 + i - 8)
            return s

        def bg(c):
            s = bc.get(c)
            if s is None:
                i = near(c)
                s = bc[c] = "\x1b[%dm" % (40 + i if i < 8 else 100 + i - 8)
            return s
    return fg, bg


def detect_colors(choice):
    if choice != "auto":
        return choice
    ct = os.environ.get("COLORTERM", "").lower()
    term = os.environ.get("TERM", "")
    if "truecolor" in ct or "24bit" in ct:
        return "true"
    if term == "linux":
        return "16"
    return "256"


# --------------------------------------------------------------------------
# Maze + game state
# --------------------------------------------------------------------------
NEIGH = ((1, 0), (-1, 0), (0, 1), (0, -1))


class Game:
    def __init__(self, cw, ch, seed):
        self.cw, self.ch = cw, ch
        self.mw, self.mh = cw * 2 + 1, ch * 2 + 1
        self.rng = random.Random(seed)
        self.new_maze()

    # ---- generation -------------------------------------------------------
    def new_maze(self):
        rng, cw, ch, mw, mh = self.rng, self.cw, self.ch, self.mw, self.mh
        g = [[1] * mw for _ in range(mh)]
        g[1][1] = 0
        seen = {(0, 0)}
        stack = [(0, 0)]
        while stack:
            x, y = stack[-1]
            nb = [(x + dx, y + dy, dx, dy) for dx, dy in NEIGH
                  if 0 <= x + dx < cw and 0 <= y + dy < ch and (x + dx, y + dy) not in seen]
            if not nb:
                stack.pop()
                continue
            nx, ny, dx, dy = rng.choice(nb)
            g[2 * y + 1 + dy][2 * x + 1 + dx] = 0
            g[2 * ny + 1][2 * nx + 1] = 0
            seen.add((nx, ny))
            stack.append((nx, ny))
        # a few extra openings so it is not a pure tree
        for _ in range(max(2, cw * ch // 14)):
            x, y = rng.randrange(1, mw - 1), rng.randrange(1, mh - 1)
            if g[y][x] and (x + y) % 2 == 1:
                if (g[y][x - 1] == 0 and g[y][x + 1] == 0) or (g[y - 1][x] == 0 and g[y + 1][x] == 0):
                    g[y][x] = 0
        self.g = g
        self.tilecol = [[rng.randrange(len(WALLC)) for _ in range(mw)] for _ in range(mh)]

        # exit = farthest cell from start (BFS)
        dist = {(1, 1): 0}
        q = [(1, 1)]
        for (x, y) in q:
            for dx, dy in NEIGH:
                n = (x + dx, y + dy)
                if g[n[1]][n[0]] == 0 and n not in dist:
                    dist[n] = dist[(x, y)] + 1
                    q.append(n)
        cells = [c for c in dist if c[0] % 2 == 1 and c[1] % 2 == 1]
        ex = max(cells, key=lambda c: dist[c])
        self.exit = ex
        self.exitspr = (ex[0] + 0.5, ex[1] + 0.5, -1, 0.0)

        # gems: dead ends first, then random cells
        def opens(c):
            return sum(1 for dx, dy in NEIGH if g[c[1] + dy][c[0] + dx] == 0)
        spots = [c for c in cells if c != ex and dist[c] > 3]
        dead = [c for c in spots if opens(c) == 1]
        rest = [c for c in spots if opens(c) != 1]
        rng.shuffle(dead)
        rng.shuffle(rest)
        want = max(6, cw * ch // 9)
        chosen = (dead + rest)[:want]
        self.gems = [(c[0] + 0.5, c[1] + 0.5, rng.randrange(len(GEMC)), rng.random() * 6.28)
                     for c in chosen]
        self.total = len(self.gems)
        self.got = 0

        # player
        self.px = self.py = 1.5
        self.ang = 0.0 if g[1][2] == 0 else math.pi / 2
        self.vx = self.vy = 0.0
        self.pitch = 0.0
        self.pitch_pend = 0.0
        self.yaw_pend = 0.0
        self.bob = 0.0
        self.explored = set()
        self.msg = ""
        self.msg_until = 0.0
        self.won = False
        self.show_map = True

    # ---- physics ----------------------------------------------------------
    def resolve(self):
        """Push the player circle out of any wall tile it overlaps."""
        g, r = self.g, RADIUS
        for _ in range(3):
            ix, iy = int(self.px), int(self.py)
            moved = False
            for j in range(max(0, iy - 1), min(self.mh, iy + 2)):
                row = g[j]
                for i in range(max(0, ix - 1), min(self.mw, ix + 2)):
                    if not row[i]:
                        continue
                    cx = min(max(self.px, i), i + 1)
                    cy = min(max(self.py, j), j + 1)
                    ddx, ddy = self.px - cx, self.py - cy
                    d2 = ddx * ddx + ddy * ddy
                    if d2 < r * r:
                        if d2 > 1e-9:
                            d = math.sqrt(d2)
                            k = (r - d) / d
                            self.px += ddx * k
                            self.py += ddy * k
                        else:  # centre inside a wall: back out the way we came
                            self.px -= self.vx * 0.02
                            self.py -= self.vy * 0.02
                        moved = True
            if not moved:
                break

    def update(self, dt, held, now, edge_turn):
        f = (1 if held["w"] else 0) - (1 if held["s"] else 0)
        s = (1 if held["d"] else 0) - (1 if held["a"] else 0)
        turn = ((1 if (held["e"] or held["right"]) else 0)
                - (1 if (held["q"] or held["left"]) else 0)) * 2.4 + edge_turn
        self.ang += turn * dt
        ap = self.yaw_pend * min(1.0, dt * 16)
        self.yaw_pend -= ap
        self.ang += ap
        pp = self.pitch_pend * min(1.0, dt * 16)
        self.pitch_pend -= pp
        self.pitch += pp
        self.pitch *= 1.0 - min(1.0, dt * 0.7)

        ca, sa = math.cos(self.ang), math.sin(self.ang)
        tx = ca * f - sa * s
        ty = sa * f + ca * s
        ln = math.hypot(tx, ty)
        if ln > 1:
            tx /= ln
            ty /= ln
        a = min(1.0, dt * 14)
        self.vx += (tx * SPEED - self.vx) * a
        self.vy += (ty * SPEED - self.vy) * a
        ox, oy = self.px, self.py
        dx, dy = self.vx * dt, self.vy * dt
        steps = int(math.hypot(dx, dy) / 0.07) + 1
        for _ in range(steps):
            self.px += dx / steps
            self.py += dy / steps
            self.resolve()
        if dt > 0:                       # keep only the velocity that really happened
            self.vx = (self.px - ox) / dt
            self.vy = (self.py - oy) / dt
        self.bob += math.hypot(self.vx, self.vy) * dt * 3.2

        # collectibles
        keep = []
        for gem in self.gems:
            if (gem[0] - self.px) ** 2 + (gem[1] - self.py) ** 2 < 0.42 ** 2:
                self.got += 1
                self.msg = "+1 gem!" if self.got < self.total else "all gems!"
                self.msg_until = now + 1.2
            else:
                keep.append(gem)
        self.gems = keep
        # exit
        if (self.exitspr[0] - self.px) ** 2 + (self.exitspr[1] - self.py) ** 2 < 0.5 ** 2:
            self.won = True

    # ---- rendering --------------------------------------------------------
    def render(self, W, PH, rx, t):
        g, mw = self.g, self.mw
        px, py = self.px, self.py
        ca, sa = math.cos(self.ang), math.sin(self.ang)
        plx, ply = -sa * PLANE, ca * PLANE
        horizon = PH / 2 + self.pitch + math.sin(self.bob) * PH * 0.012
        n = max(1, W // rx)
        bg = bg_column(PH, horizon)
        unitk = W / (2 * PLANE)
        explored, tilecol = self.explored, self.tilecol
        cols, zbuf = [], []

        for x in range(n):
            camx = 2 * (x + 0.5) / n - 1
            rdx, rdy = ca + plx * camx, sa + ply * camx
            mx, my = int(px), int(py)
            ddx = abs(1 / rdx) if rdx else 1e9
            ddy = abs(1 / rdy) if rdy else 1e9
            if rdx < 0:
                stx, sdx = -1, (px - mx) * ddx
            else:
                stx, sdx = 1, (mx + 1 - px) * ddx
            if rdy < 0:
                sty, sdy = -1, (py - my) * ddy
            else:
                sty, sdy = 1, (my + 1 - py) * ddy
            while True:
                if sdx < sdy:
                    sdx += ddx
                    mx += stx
                    side = 0
                    d = sdx - ddx
                else:
                    sdy += ddy
                    my += sty
                    side = 1
                    d = sdy - ddy
                if d <= MAXD:
                    explored.add(my * mw + mx)
                if g[my][mx]:
                    break
            perp = d if d > 0.05 else 0.05
            wx = (py + perp * rdy) if side == 0 else (px + perp * rdx)
            wx -= math.floor(wx)
            edge = 1 if (wx < 0.045 or wx > 0.955) else 0
            face = (0 if stx > 0 else 1) if side == 0 else (2 if sty > 0 else 3)
            band = int(perp * NBD)
            if band >= NB:
                band = NB - 1
            c = WALLPAL[tilecol[my][mx]][face][band][edge]
            unit = unitk / perp
            y0 = int(horizon - unit * 0.5)
            y1 = int(horizon + unit * 0.5) + 1
            if y0 < 0:
                y0 = 0
            if y1 > PH:
                y1 = PH
            col = bg[:]
            if y1 > y0:
                col[y0:y1] = [c] * (y1 - y0)
            cols.append(col)
            zbuf.append(perp)

        # ---- sprites (gems + exit gate), far to near ----------------------
        inv = 1.0 / (plx * sa - ca * ply)
        items = []
        for (sx, sy, ci, ph) in self.gems + [self.exitspr]:
            rxp, ryp = sx - px, sy - py
            tx = inv * (sa * rxp - ca * ryp)
            ty = inv * (-ply * rxp + plx * ryp)
            if ty > 0.2:
                items.append((ty, tx, ci, ph))
        items.sort(reverse=True)
        for ty, tx, ci, ph in items:
            unit = unitk / ty
            scr = n * 0.5 * (1 + tx / ty) - 0.5
            band = min(NB - 1, int(ty * NBD))
            if ci >= 0:  # low-poly gem: a four-facet diamond
                zc = 0.40 + 0.045 * math.sin(t * 3 + ph)
                cy = horizon + (EYE - zc) * unit
                hw = max(0.6, 0.17 * unit / rx)
                hh = 0.21 * unit
                for xc in range(max(0, int(scr - hw)), min(n, int(scr + hw) + 1)):
                    if ty >= zbuf[xc]:
                        continue
                    fx = (xc - scr) / hw
                    if fx < -1 or fx > 1:
                        continue
                    span = hh * (1 - abs(fx))
                    ya, yb = max(0, int(cy - span)), min(PH, int(cy + span) + 1)
                    colx = cols[xc]
                    left = 0 if fx < 0 else 2
                    for y in range(ya, yb):
                        colx[y] = gem_col(ci, left + (0 if y < cy else 1), band)
            else:        # exit gate: glowing arch with scrolling stripes
                hw = max(0.8, 0.32 * unit / rx)
                top = horizon + (EYE - 0.92) * unit
                bot = horizon + EYE * unit
                gf, ga, gb = GATE[band]
                stripe = max(1.0, unit * 0.1)
                for xc in range(max(0, int(scr - hw)), min(n, int(scr + hw) + 1)):
                    if ty >= zbuf[xc]:
                        continue
                    fx = (xc - scr) / hw
                    if fx < -1 or fx > 1:
                        continue
                    ya = max(0, int(top + fx * fx * unit * 0.15))
                    yb = min(PH, int(bot) + 1)
                    colx = cols[xc]
                    framed = abs(fx) > 0.72
                    for y in range(ya, yb):
                        if framed:
                            colx[y] = gf
                        else:
                            colx[y] = ga if int((y - top) / stripe + t * 5) & 1 else gb

        # ---- expand columns if rendering at reduced width -----------------
        if rx > 1:
            full = []
            for c in cols:
                full.append(c)
                for _ in range(rx - 1):
                    full.append(c[:])
            cols = full[:W]
            while len(cols) < W:
                cols.append(cols[-1][:])

        if self.show_map:
            self.draw_minimap(cols, W, PH, t)
        return list(zip(*cols))

    def draw_minimap(self, cols, W, PH, t):
        mw, mh, ex, g = self.mw, self.mh, self.explored, self.g
        s = 2 if (W >= mw * 2 + 30 and PH >= mh * 2 + 6) else 1
        if W < mw * s + 6 or PH < mh * s + 6:
            return
        x0, y0 = W - mw * s - 3, 3
        for x in range(x0 - 2, x0 + mw * s + 2):
            c = cols[x]
            for y in range(y0 - 2, y0 + mh * s + 2):
                c[y] = MM_RIM
        for x in range(x0 - 1, x0 + mw * s + 1):
            c = cols[x]
            for y in range(y0 - 1, y0 + mh * s + 1):
                c[y] = MM_BG

        def block(i, j, color):
            for dx in range(s):
                c = cols[x0 + i * s + dx]
                for dy in range(s):
                    c[y0 + j * s + dy] = color

        for j in range(mh):
            row, base = g[j], j * mw
            for i in range(mw):
                if base + i in ex:
                    block(i, j, MM_WALL if row[i] else MM_FLOOR)
        for (gx, gy, _, _) in self.gems:
            i, j = int(gx), int(gy)
            if j * mw + i in ex:
                block(i, j, MM_GEM)
        ei, ej = self.exit
        if ej * mw + ei in ex and int(t * 3) & 1:
            block(ei, ej, MM_EXIT)
        elif ej * mw + ei in ex:
            block(ei, ej, pack((30, 160, 95)))
        pi, pj = int(self.px), int(self.py)
        di = int(self.px + math.cos(self.ang) * 1.1)
        dj = int(self.py + math.sin(self.ang) * 1.1)
        if (di, dj) != (pi, pj) and 0 <= di < mw and 0 <= dj < mh:
            block(di, dj, MM_DIR)
        block(pi, pj, MM_ME)


# --------------------------------------------------------------------------
# Text overlays, HUD, frame output
# --------------------------------------------------------------------------
def text_box(lines, tc, tr):
    """Centred panel with a rainbow border. lines: [(text, colour)]"""
    wd = max(len(l[0]) for l in lines) + 8
    hg = len(lines) + 4
    r0, c0 = max(0, (tr - hg) // 2), max(0, (tc - wd) // 2)
    ov = {}
    pan, ink = pack(PANEL), pack(INK)
    for r in range(hg):
        for c in range(wd):
            if r in (0, hg - 1) or c in (0, wd - 1):
                col = pack(RAINBOW[(r + c) % len(RAINBOW)])
                ov[(r0 + r, c0 + c)] = (col, col, " ")
            else:
                ov[(r0 + r, c0 + c)] = (ink, pan, " ")
    for k, (txt, colr) in enumerate(lines):
        start = c0 + (wd - len(txt)) // 2
        for q, chh in enumerate(txt):
            ov[(r0 + 2 + k, start + q)] = (pack(colr), pan, chh)
    return ov


def hud_cells(width, left, right):
    cells = []
    for txt, colr in left:
        for ch in txt:
            cells.append((pack(colr), pack(HUDBG), ch))
    rcells = []
    for txt, colr in right:
        for ch in txt:
            rcells.append((pack(colr), pack(HUDBG), ch))
    pad = width - len(cells) - len(rcells)
    if pad < 1:
        rcells = []
        pad = width - len(cells)
    cells += [(0, pack(HUDBG), " ")] * max(0, pad) + rcells
    return cells[:width]


def present(rows, vr, W, rowmap, hud, fg, bg):
    parts = []
    pf = pb = -1
    for r in range(vr):
        cells = [(t, b, HALF) for t, b in zip(rows[2 * r], rows[2 * r + 1])]
        ov = rowmap.get(r)
        if ov:
            for c, v in ov.items():
                if 0 <= c < W:
                    cells[c] = v
        parts.append("\x1b[%d;1H" % (r + 1))
        for key, grp in groupby(cells):
            cnt = sum(1 for _ in grp)
            if key[0] != pf:
                parts.append(fg(key[0]))
                pf = key[0]
            if key[1] != pb:
                parts.append(bg(key[1]))
                pb = key[1]
            parts.append(key[2] * cnt)
    parts.append("\x1b[%d;1H" % (vr + 1))
    for key, grp in groupby(hud):
        cnt = sum(1 for _ in grp)
        if key[0] != pf:
            parts.append(fg(key[0]))
            pf = key[0]
        if key[1] != pb:
            parts.append(bg(key[1]))
            pb = key[1]
        parts.append(key[2] * cnt)
    parts.append("\x1b[0m")
    return "".join(parts)


def fmt_time(t):
    return "%02d:%04.1f" % (int(t // 60), t % 60)


# --------------------------------------------------------------------------
# Input parsing (keys, arrows, SGR mouse reports)
# --------------------------------------------------------------------------
MOUSE_RE = re.compile(rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])")
ARROWS = {ord("A"): "up", ord("B"): "down", ord("C"): "right", ord("D"): "left"}


def parse(buf):
    ev, i, n = [], 0, len(buf)
    while i < n:
        b = buf[i]
        if b != 0x1B:
            ev.append(("key", chr(b).lower()))
            i += 1
            continue
        if i + 1 >= n:
            ev.append(("esc",))
            i += 1
            continue
        nxt = buf[i + 1]
        if nxt == 0x5B and i + 2 < n and buf[i + 2] == 0x3C:
            m = MOUSE_RE.match(buf, i)
            if m:
                ev.append(("mouse", int(m.group(1)), int(m.group(2)), int(m.group(3)),
                           m.group(4) == b"M"))
                i = m.end()
                continue
            if n - i < 32:
                break          # incomplete report, wait for more bytes
            i += 1
            continue
        if nxt in (0x5B, 0x4F):
            j = i + 2
            while j < n and 48 <= buf[j] <= 59:
                j += 1
            if j >= n:
                break
            if buf[j] in ARROWS:
                ev.append(("arrow", ARROWS[buf[j]]))
            i = j + 1
            continue
        i += 2
    return ev, buf[i:]


OPPOSITE = {"w": "s", "s": "w", "a": "d", "d": "a", "q": "e", "e": "q",
            "left": "right", "right": "left"}
HOLDKEYS = ("w", "a", "s", "d", "q", "e", "left", "right")


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------
def play(game, args):
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    fg, bg = make_color(detect_colors(args.colors))
    out = sys.stdout.buffer
    mouse_on = not args.no_mouse

    def w(s):
        out.write(s.encode("utf-8"))
        out.flush()

    state = "title"
    buf = b""
    expire = {}
    frame = 1.0 / max(5, args.fps)
    rx = 3 if args.fast else 1
    ema = 0.0
    last = time.time()
    tstart = 0.0
    elapsed = 0.0
    lastsize = None
    lastmx = None
    mx_norm = None
    try:
        tty.setcbreak(fd)
        w("\x1b[?1049h\x1b[?25l\x1b[2J" + ("\x1b[?1003h\x1b[?1006h" if mouse_on else ""))
        while True:
            fstart = time.time()
            now = fstart
            dt = min(0.05, now - last)
            last = now

            # --- input ----------------------------------------------------
            events, buf = parse(buf)
            quit_ = False
            started = False
            for e in events:
                kind = e[0]
                if kind == "esc":
                    quit_ = True
                elif kind == "key":
                    k = e[1]
                    if k in ("x", "\x03"):
                        quit_ = True
                    elif state == "win":
                        if k == "q":
                            quit_ = True
                        elif k == "r":
                            game.new_maze()
                            state, tstart, elapsed = "play", now, 0.0
                    else:
                        if state == "title":
                            started = True
                        if k == "m":
                            game.show_map = not game.show_map
                        elif k in HOLDKEYS:
                            expire[OPPOSITE[k]] = 0
                            expire[k] = max(expire.get(k, 0),
                                            now + (0.12 if expire.get(k, 0) > now else 0.22))
                elif kind == "arrow" and state == "play":
                    k = e[1]
                    if k in ("left", "right"):
                        expire[OPPOSITE[k]] = 0
                        expire[k] = max(expire.get(k, 0),
                                        now + (0.12 if expire.get(k, 0) > now else 0.22))
                    else:
                        game.pitch_pend += 5 if k == "up" else -5
                elif kind == "mouse" and mouse_on:
                    _, code, mxc, myc, press = e
                    if press and (code & 32) and not (code & 64):
                        if lastmx is not None and state == "play":
                            dxm = max(-15, min(15, mxc - lastmx[0]))
                            dym = max(-8, min(8, myc - lastmx[1]))
                            game.yaw_pend += dxm * 0.045
                            game.pitch_pend -= dym * 2.2
                        lastmx = (mxc, myc)
                        mx_norm = (mxc - 0.5) / max(1, lastsize[0] if lastsize else 80)
            if quit_:
                break
            if started:
                state, tstart = "play", now

            # --- terminal size -------------------------------------------
            cols_t, rows_t = shutil.get_terminal_size((80, 24))
            if (cols_t, rows_t) != lastsize:
                lastsize = (cols_t, rows_t)
                w("\x1b[0m\x1b[2J")
            if cols_t < 44 or rows_t < 14:
                w("\x1b[H\x1b[0mTerminal too small - need at least 44x14.")
                buf += _sleep(fd, frame)
                continue
            W = cols_t
            vr = rows_t - 1
            PH = vr * 2

            # --- update ---------------------------------------------------
            if state == "play":
                held = {k: expire.get(k, 0) > now for k in HOLDKEYS}
                edge = 0.0
                if mouse_on and mx_norm is not None:
                    if mx_norm < 0.05:
                        edge = -1.4
                    elif mx_norm > 0.95:
                        edge = 1.4
                game.update(dt, held, now, edge)
                elapsed = now - tstart
                if game.won:
                    state = "win"
                    game.won = False
            elif state == "win":
                game.ang += dt * 0.7          # victory spin

            # --- render ---------------------------------------------------
            rows = game.render(W, PH, rx, now)
            ov = {}
            if state == "title":
                ov = text_box([
                    ("LOW-POLY MAZE", (255, 120, 125)),
                    ("", INK),
                    ("Find the glowing green gate.", INK),
                    ("Grab gems on the way!", INK),
                    ("", INK),
                    ("W A S D  move / strafe", INK),
                    ("mouse or Q E or arrows  look", INK),
                    ("M minimap    X quit", INK),
                    ("", INK),
                    ("press any key to start", (110, 165, 255)),
                ], W, vr)
            elif state == "win":
                ov = text_box([
                    ("YOU FOUND THE EXIT!", (255, 120, 125)),
                    ("", INK),
                    ("Time  " + fmt_time(elapsed), INK),
                    ("Gems  %d / %d" % (game.got, game.total), INK),
                    ("", INK),
                    ("R  new maze     X  quit", (110, 165, 255)),
                ], W, vr)
            rowmap = defaultdict(dict)
            for (r, c), v in ov.items():
                rowmap[r][c] = v
            left = [(" TIME ", (200, 190, 230)), (fmt_time(elapsed), (255, 235, 80)),
                    ("   GEMS ", (200, 190, 230)),
                    ("%d/%d" % (game.got, game.total), (255, 120, 190))]
            if game.msg and now < game.msg_until:
                left.append(("  " + game.msg, (120, 235, 170)))
            right = [("WASD move  mouse/QE look  M map  X quit ", (150, 140, 190))]
            hud = hud_cells(cols_t, left, right)
            w(present(rows, vr, W, rowmap, hud, fg, bg))

            # --- adaptive quality ----------------------------------------
            spent = time.time() - fstart
            ema = spent if ema == 0 else ema * 0.9 + spent * 0.1
            if ema > frame * 1.15 and rx < 3:
                rx += 1
                ema = 0.0
            buf += _sleep(fd, frame - spent)
    finally:
        w("\x1b[?1003l\x1b[?1006l\x1b[0m\x1b[?25h\x1b[?1049l")
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _sleep(fd, secs):
    """Wait up to `secs`, returning any keyboard/mouse bytes that arrive."""
    end = time.time() + max(0.0, secs)
    got = b""
    while True:
        rem = max(0.0, end - time.time())
        if select.select([fd], [], [], rem)[0]:
            try:
                got += os.read(fd, 4096)
            except OSError:
                pass
        if time.time() >= end:
            break
    return got


def main():
    ap = argparse.ArgumentParser(description="Low-poly first-person terminal maze")
    ap.add_argument("--maze", default="12x8", help="maze size in cells, e.g. 12x8 or 20x12")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--colors", choices=["auto", "true", "256", "16"], default="auto")
    ap.add_argument("--fps", type=int, default=20)
    ap.add_argument("--fast", action="store_true", help="render at lower horizontal resolution")
    ap.add_argument("--no-mouse", action="store_true")
    args = ap.parse_args()
    try:
        cw, ch = (int(v) for v in args.maze.lower().split("x"))
        assert 3 <= cw <= 60 and 3 <= ch <= 40
    except Exception:
        sys.exit("--maze must look like 12x8 (each side between 3 and 60/40)")
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        sys.exit("Run this directly in a terminal (it needs an interactive TTY).")
    game = Game(cw, ch, args.seed)
    play(game, args)
    print("Thanks for playing!")


if __name__ == "__main__":
    main()
