"""Round touchscreen UI (pygame), designed for a 1080×1080 circular panel.

Screens: carousel of drinks → mood picker (ask Claude) → pour (ring fills with the LEDs).
Swipe left/right to browse; tap SINGLE or DOUBLE to pour.
"""
from __future__ import annotations

import math
import threading
import time

import pygame

from .bartender import MOODS, BartenderError
from .pour import State

BG = (12, 12, 14)
FG = (241, 237, 230)
MUTE = (130, 126, 120)
ACCENT = (240, 170, 80)
TAU = math.tau


def hex_rgb(h: str):
    h = h.lstrip("#")
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return ACCENT


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
        self.offset = 0.0           # carousel slide animation
        self.drag_x = None
        self.notice, self.notice_t = "", 0.0
        self.thinking = False
        self.mask = self._make_mask()

    # ------------------------------------------------------------ helpers
    def _make_mask(self):
        m = pygame.Surface((self.S, self.S), pygame.SRCALPHA)
        m.fill((0, 0, 0, 255))
        pygame.draw.circle(m, (0, 0, 0, 0), (self.c, self.c), self.c)
        return m

    def drinks(self):
        return self.app.book.available(self.app.inventory)

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

    def pill(self, label, center, active=True):
        w, h = int(self.S * 0.2), int(self.S * 0.075)
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
        self.ask_rect = self.pill("✦ ASK HALO", (c, int(S * 0.14)), active=False)
        if not ds:
            self.text("No drinks yet", self.f_mid, FG, (c, c - 20))
            self.text("Load bottles at  halo.local:8080  or tap Ask", self.f_small, MUTE, (c, c + 40), S * 0.6)
            self.single_rect = self.double_rect = pygame.Rect(0, 0, 0, 0)
            return
        self.index %= len(ds)
        r = ds[self.index]
        inv = self.app.inventory
        # Ingredient ring: each bottle gets an arc proportional to its share.
        total = sum(i.parts for i in r.ingredients)
        a = 0.0
        for ing in r.ingredients:
            b = inv.find(ing.bottle)
            frac = ing.parts / total
            self.arc(hex_rgb(b.color if b else "#888888"), a + 0.01, frac - 0.02, c - 46, 18)
            a += frac * TAU
        x = c + self.offset
        self.text(r.name, self.f_big, hex_rgb(r.color), (x, c - 150), S * 0.62)
        self.text(r.description, self.f_small, FG, (x, c - 20), S * 0.6)
        line = "  ·  ".join(f"{i.parts:g} {i.bottle}" for i in r.ingredients)
        self.text(line, self.f_tiny, MUTE, (x, c + 110), S * 0.62)
        meta = f"{r.abv(inv):.0f}% ABV  ·  {r.glass}" + (f"  ·  {r.garnish}" if r.garnish else "")
        self.text(meta, self.f_tiny, MUTE, (x, c + 170), S * 0.6)
        self.single_rect = self.pill("SINGLE", (c - int(S * 0.12), int(S * 0.76)))
        self.double_rect = self.pill("DOUBLE", (c + int(S * 0.12), int(S * 0.76)), active=False)
        dots = len(ds)
        for i in range(dots):
            col = FG if i == self.index else (70, 68, 66)
            pygame.draw.circle(self.screen, col, (c + (i - (dots - 1) / 2) * 22, int(S * 0.86)), 5)

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
        p = self.app.engine.progress
        c, S = self.c, self.S
        recipe = self.app.book.get(p.drink)
        col = hex_rgb(recipe.color) if recipe else ACCENT
        self.arc((40, 39, 38), 0, 0.999, c - 60, 26)
        if p.fraction > 0:
            self.arc(col, 0, min(p.fraction, 0.999), c - 60, 26)
        self.text(p.drink, self.f_mid, col, (c, c - 120))
        if p.state in (State.POURING, State.PAUSED):
            self.text(f"{p.fraction * 100:.0f}%", self.f_big, FG, (c, c))
        self.text(p.message, self.f_small, MUTE, (c, c + 110), S * 0.6)
        if p.state == State.DONE:
            self.text("tap to finish", self.f_tiny, MUTE, (c, int(S * 0.8)))
        elif p.state in (State.ERROR, State.CANCELLED):
            self.text("tap to go back", self.f_tiny, MUTE, (c, int(S * 0.8)))
        else:
            self.cancel_rect = self.pill("STOP", (c, int(S * 0.8)), active=False)

    # ------------------------------------------------------------ input
    def ask(self, mood):
        def run():
            try:
                r = self.app.bartender.create(self.app.inventory, mood)
                self.app.book.add(r)
                self.index = [d.name for d in self.drinks()].index(r.name)
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
            self.app.engine.start(ds[self.index], size)
            self.screen_name = "pour"
        except Exception as e:
            self.say(str(e))

    def on_tap(self, pos):
        if self.screen_name == "home":
            if self.ask_rect.collidepoint(pos):
                self.screen_name = "moods"
            elif self.single_rect.collidepoint(pos):
                self.pour("single")
            elif self.double_rect.collidepoint(pos):
                self.pour("double")
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
                    elif abs(dx) < 15:
                        self.on_tap(e.pos)
            if self.drag_x is None:
                self.offset *= 0.7
            if self.screen_name == "home" and not self.app.engine.busy:
                self.app.halo.breathe(time.monotonic(), (90, 70, 50))
            self.screen.fill(BG)
            {"home": self.draw_home, "moods": self.draw_moods, "pour": self.draw_pour}[self.screen_name]()
            if self.notice and time.monotonic() - self.notice_t < 4:
                self.text(self.notice, self.f_tiny, ACCENT, (self.c, int(self.S * 0.93)), self.S * 0.5)
            self.screen.blit(self.mask, (0, 0))
            pygame.display.flip()
            clock.tick(60)
