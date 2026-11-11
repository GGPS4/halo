"""Round touchscreen UI (pygame), designed for a 1080×1080 circular panel.

Screens: carousel of drinks → mood picker (ask Claude) → pour (ring fills in each
ingredient's color, matching the LEDs) → back. After a while idle it drifts into ambient
mode: a slow swirl of the loaded bottles' colors (feature 15).
"""
from __future__ import annotations

import math
import threading
import time

import pygame

from .bartender import MOODS, BartenderError
from .hardware import hex_rgb
from .pour import State
from .recipes import STRENGTH

BG = (12, 12, 14)
FG = (241, 237, 230)
MUTE = (130, 126, 120)
ACCENT = (240, 170, 80)
WARN = (240, 120, 90)
TAU = math.tau


class UI:
    def __init__(self, app, size: int = 1080, fullscreen: bool = True):
        pygame.init()
        flags = pygame.FULLSCREEN if fullscreen else 0
        self.screen = pygame.display.set_mode((size, size), flags)
        pygame.display.set_caption("Halo")
        pygame.mouse.set_visible(not fullscreen)
        self.app, self.S, self.c = app, size, size // 2
        f = lambda px, bold=False: pygame.font.SysFont("Inter,DejaVu Sans", int(px * size / 1080), bold=bold)
        self.f_big, self.f_mid, self.f_small, self.f_tiny = f(78), f(40), f(30), f(24, True)
        self.index = 0
        self.screen_name = "home"
        self.strength = app.prefs["default_strength"]
        self.offset = 0.0
        self.drag_x = None
        self.notice, self.notice_t = "", 0.0
        self.thinking = False
        self.mask = self._make_mask()
        self.ambient_after = app.settings["ui"].get("ambient_after_s", 90)

    # ------------------------------------------------------------ helpers
    def _make_mask(self):
        m = pygame.Surface((self.S, self.S), pygame.SRCALPHA)
        m.fill((0, 0, 0, 255))
        pygame.draw.circle(m, (0, 0, 0, 0), (self.c, self.c), self.c)
        return m

    def drinks(self):
        return self.app.menu()

    def say(self, text):
        self.notice, self.notice_t = text, time.monotonic()

    def text(self, s, font, color, center, max_w=None):
        lines = [s]
        if max_w:
            words, lines, cur = s.split(), [], ""
            for w in words:
                t = (cur + " " + w).strip()
                if font.size(t)[0] > max_w and cur:
                    lines.append(cur)
                    cur = w
                else:
                    cur = t
            lines.append(cur)
        h = font.get_linesize()
        y = center[1] - h * (len(lines) - 1) / 2
        for ln in lines:
            surf = font.render(ln, True, color)
            self.screen.blit(surf, surf.get_rect(center=(center[0], y)))
            y += h

    def arc(self, color, start, frac, r, width):
        """Arc from angle `start` (0 = top, clockwise) covering `frac` of the circle."""
        steps = max(2, int(frac * 180))
        pts = []
        for i in range(steps + 1):
            a = start + frac * TAU * i / steps - math.pi / 2
            pts.append((self.c + r * math.cos(a), self.c + r * math.sin(a)))
        if len(pts) > 1:
            pygame.draw.lines(self.screen, color, False, pts, width)

    def pill(self, label, center, active=True, w=0.2):
        w, h = int(self.S * w), int(self.S * 0.075)
        rect = pygame.Rect(0, 0, w, h)
        rect.center = center
        if active:
            pygame.draw.rect(self.screen, ACCENT, rect, border_radius=h // 2)
            self.text(label, self.f_tiny, (26, 18, 6), center)
        else:
            pygame.draw.rect(self.screen, (60, 58, 56), rect, 2, border_radius=h // 2)
            self.text(label, self.f_tiny, FG, center)
        return rect

    # ------------------------------------------------------------ screens
    def draw_home(self):
        ds = self.drinks()
        S, c = self.S, self.c
        party = self.app.party.data
        label = f"✦ {party['theme'].upper()[:18]}" if party["active"] else "✦ ASK HALO"
        self.ask_rect = self.pill(label, (c, int(S * 0.14)), active=False, w=0.3)
        if not ds:
            self.text("No drinks yet", self.f_mid, FG, (c, c - 20))
            self.text("Open halo.local:8080 on your phone to load bottles", self.f_small, MUTE, (c, c + 40), S * 0.6)
            self.single_rect = self.double_rect = pygame.Rect(0, 0, 0, 0)
            self.strength_rects = {}
            return
        self.index %= len(ds)
        r = ds[self.index]
        inv = self.app.inventory
        total = sum(w for _, w in r.weighted_parts(inv, self.strength))
        a = 0.0
        for ing, w in r.weighted_parts(inv, self.strength):
            b = inv.find(ing.bottle)
            frac = w / total
            self.arc(hex_rgb(b.color if b else "#888888"), a + 0.01, frac - 0.02, c - 46, 18)
            a += frac * TAU
        x = c + self.offset
        name = ("♥ " if r.favorite else "") + r.name
        self.text(name, self.f_big, hex_rgb(r.color), (x, c - 165), S * 0.62)
        self.text(r.description, self.f_small, FG, (x, c - 45), S * 0.6)
        line = "  ·  ".join(f"{i.parts:g} {i.bottle}" for i in r.ingredients)
        self.text(line, self.f_tiny, MUTE, (x, c + 75), S * 0.62)
        meta = f"{r.abv(inv, self.strength):.0f}% ABV  ·  {r.glass}" + (f"  ·  {r.garnish}" if r.garnish else "")
        self.text(meta, self.f_tiny, MUTE, (x, c + 125), S * 0.6)
        low = [i.bottle for i in r.ingredients if inv.find(i.bottle) and
               inv.find(i.bottle).level * 100 < self.app.prefs["low_level_pct"]]
        if low:
            self.text("Low: " + ", ".join(low), self.f_tiny, WARN, (x, c + 165), S * 0.6)
        # strength chips (feature 2)
        self.strength_rects = {}
        for k, key in enumerate(STRENGTH):
            p = (c + (k - 1) * int(S * 0.15), int(S * 0.67))
            self.strength_rects[key] = self.pill(key.upper(), p, active=key == self.strength, w=0.13)
        self.single_rect = self.pill("SINGLE", (c - int(S * 0.12), int(S * 0.77)))
        self.double_rect = self.pill("DOUBLE", (c + int(S * 0.12), int(S * 0.77)), active=False)
        dots = len(ds)
        for i in range(min(dots, 14)):
            col = FG if i == self.index else (70, 68, 66)
            pygame.draw.circle(self.screen, col, (c + (i - (min(dots, 14) - 1) / 2) * 22, int(S * 0.86)), 5)

    def draw_moods(self):
        c, S = self.c, self.S
        self.mood_rects = {}
        keys = list(MOODS)
        for i, k in enumerate(keys):
            a = TAU * i / len(keys) - math.pi / 2
            p = (c + 300 * S / 1080 * math.cos(a), c + 300 * S / 1080 * math.sin(a))
            self.mood_rects[k] = self.pill(k.upper(), p, active=False)
        if self.thinking:
            t = time.monotonic()
            self.arc(ACCENT, (t * 2.5) % TAU, 0.25, 120, 10)
            self.text("mixing ideas…", self.f_small, MUTE, (c, c))
        else:
            self.text("What are you", self.f_mid, FG, (c, c - 30))
            self.text("in the mood for?", self.f_mid, FG, (c, c + 30))
        self.back_rect = self.pill("BACK", (c, int(self.S * 0.86)), active=False)

    def draw_pour(self):
        """Feature 28: the ring is split into each ingredient's share, in its color,
        and fills at the same moment the LEDs do."""
        p = self.app.engine.progress
        c, S = self.c, self.S
        recipe = self.app.book.get(p.drink)
        col = hex_rgb(recipe.color) if recipe else ACCENT
        total = sum(s["target_g"] for s in p.segments) or 1
        a = 0.0
        for s in p.segments:
            frac = s["target_g"] / total
            seg_col = hex_rgb(s["color"])
            dim = tuple(int(v * 0.22) for v in seg_col)
            self.arc(dim, a + 0.004, max(frac - 0.008, 0.001), c - 60, 26)
            filled = max(0.0, min(frac, p.fraction - a / TAU))
            if filled > 0:
                self.arc(seg_col, a + 0.004, max(filled - 0.008, 0.001), c - 60, 26)
            a += frac * TAU
        self.text(p.drink, self.f_mid, col, (c, c - 140))
        if p.state in (State.POURING, State.PAUSED):
            self.text(f"{p.fraction * 100:.0f}%", self.f_big, FG, (c, c - 20))
            if p.step:
                self.text(p.step, self.f_small, hex_rgb(p.color or "#f0aa50"), (c, c + 60))
        self.text(p.message, self.f_small, MUTE, (c, c + 130), S * 0.6)
        if p.state == State.DONE:
            self.text("tap to finish", self.f_tiny, MUTE, (c, int(S * 0.8)))
        elif p.state in (State.ERROR, State.CANCELLED):
            self.text("tap to go back", self.f_tiny, MUTE, (c, int(S * 0.8)))
        else:
            self.cancel_rect = self.pill("STOP", (c, int(S * 0.8)), active=False)

    def draw_ambient(self):
        """Feature 15: slow swirl of the loaded bottles' colors, with the time."""
        t = time.monotonic()
        cols = [hex_rgb(b.color) for b in self.app.inventory.loaded()] or [ACCENT]
        n = 60
        for i in range(n):
            p = (i / n * len(cols) + t * 0.05) % len(cols)
            a, b = cols[int(p)], cols[(int(p) + 1) % len(cols)]
            f = p - int(p)
            k = 0.35 + 0.25 * math.sin(t * 0.6 + i * 0.3)
            cc = tuple(int((a[j] + (b[j] - a[j]) * f) * k) for j in range(3))
            self.arc(cc, i / n, 1 / n + 0.003, self.c - 30, 40)
        self.text(time.strftime("%-I:%M"), self.f_big, (200, 196, 190), (self.c, self.c - 10))
        self.text("tap for drinks", self.f_tiny, MUTE, (self.c, self.c + 70))

    # ------------------------------------------------------------ input
    def ask(self, mood):
        def run():
            try:
                r = self.app.bartender.create(self.app.inventory, mood)
                self.app.book.add(r)
                names = [d.name for d in self.drinks()]
                self.index = names.index(r.name) if r.name in names else 0
                self.screen_name = "home"
                self.say(f"New: {r.name}")
            except (BartenderError, ValueError) as e:
                self.say(str(e))
            finally:
                self.thinking = False

        self.thinking = True
        threading.Thread(target=run, daemon=True).start()

    def pour(self, size):
        ds = self.drinks()
        if not ds:
            return
        try:
            self.app.engine.start(ds[self.index], size, self.strength, by="screen")
            self.screen_name = "pour"
        except Exception as e:
            self.say(str(e))

    def on_tap(self, pos):
        self.app.touch()
        if self.screen_name == "ambient":
            self.screen_name = "home"
            return
        if self.screen_name == "home":
            if self.ask_rect.collidepoint(pos):
                self.screen_name = "moods"
            elif self.single_rect.collidepoint(pos):
                self.pour("single")
            elif self.double_rect.collidepoint(pos):
                self.pour("double")
            else:
                for k, rect in self.strength_rects.items():
                    if rect.collidepoint(pos):
                        self.strength = k
        elif self.screen_name == "moods" and not self.thinking:
            if self.back_rect.collidepoint(pos):
                self.screen_name = "home"
            for k, rect in self.mood_rects.items():
                if rect.collidepoint(pos):
                    self.ask(k)
        elif self.screen_name == "pour":
            st = self.app.engine.progress.state
            if st in (State.DONE, State.ERROR, State.CANCELLED):
                self.screen_name = "home"
                self.app.halo.off()
            elif self.cancel_rect.collidepoint(pos):
                self.app.engine.cancel()

    def run(self):
        clock = pygame.time.Clock()
        while True:
            for e in pygame.event.get():
                if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                    return
                if e.type == pygame.MOUSEBUTTONDOWN:
                    self.drag_x = e.pos[0]
                if e.type == pygame.MOUSEMOTION and self.drag_x is not None and self.screen_name == "home":
                    self.offset = e.pos[0] - self.drag_x
                if e.type == pygame.MOUSEBUTTONUP:
                    dx = 0 if self.drag_x is None else e.pos[0] - self.drag_x
                    self.drag_x = None
                    if abs(dx) > 80 and self.screen_name == "home":
                        self.index += -1 if dx > 0 else 1
                        self.app.touch()
                    elif abs(dx) < 15:
                        self.on_tap(e.pos)
            if self.drag_x is None:
                self.offset *= 0.7
            busy = self.app.engine.busy
            if busy and self.screen_name != "pour":          # a pour started from the phone or Levi
                self.screen_name = "pour"
            idle_for = time.monotonic() - self.app.last_activity
            if (self.screen_name == "home" and not busy and self.app.prefs["ambient"]
                    and idle_for > self.ambient_after):
                self.screen_name = "ambient"
            if not busy:
                self.app.idle_ring(time.monotonic())
            self.screen.fill(BG)
            {"home": self.draw_home, "moods": self.draw_moods, "pour": self.draw_pour,
             "ambient": self.draw_ambient}[self.screen_name]()
            if self.notice and time.monotonic() - self.notice_t < 4:
                self.text(self.notice, self.f_tiny, ACCENT, (self.c, int(self.S * 0.93)), self.S * 0.5)
            self.screen.blit(self.mask, (0, 0))
            pygame.display.flip()
            clock.tick(60)
