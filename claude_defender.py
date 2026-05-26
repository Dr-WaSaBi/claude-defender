#!/usr/bin/env python3
"""
Claude Defender — a pygame Defender-style side-scrolling shooter.

HOW TO RUN
----------
    python3 claude_defender.py

DEPENDENCIES
------------
    pip install pygame numpy

CONTROLS
--------
    ← → / A D   Fly left and right
    ↑ ↓ / W S   Fly up and down
    SPACE        Fire bullet (max 4 on screen)
    Z or X       Super Zapper — destroys all Claude ships on screen (3 per life)
    ESC          Quit

ARCHITECTURE
------------
  Sound:
    SoundEngine      — synthesizes all SFX from numpy waveforms; no audio files.

  Data:
    HighScoreManager — loads/saves/queries the top-10 score table in highscores.json.

  Sprites / Game Objects:
    Terrain          — procedurally generated scrolling mountain landscape.
    Bullet           — player projectile travelling horizontally.
    ClaudeBullet     — enemy projectile fired by Claude ships at the player.
    ClaudeBomb       — slow-falling bomb dropped by a ClaudeBomber; shootable.
    Particle         — short-lived explosion fragment.
    SuperZapperEffect— expanding ring + flash visual when zapper fires.
    Human            — ground-level civilian that Claude ships try to abduct.
    ClaudeShip       — enemy with HOVER → DIVE → CARRY state machine.
    ClaudeBomber     — heavy bomber that flies across and drops bombs on humans.
    Player           — the player's ship with 4-directional movement.

  Screen Functions:
    title_screen()          — main menu.
    play_level()            — core gameplay loop; returns (score, lives, hi, result).
    game_over_screen()      — end-of-game summary with leaderboard.
    enter_initials_screen() — arcade 3-letter initial entry for high scores.

  Entry Point:
    main() — sequences title → level progression → game over → initials → repeat.
"""

import pygame
import random
import math
import sys
import json
import numpy as np
from pathlib import Path

pygame.mixer.pre_init(44100, -16, 2, 512)
pygame.init()

# ── screen ─────────────────────────────────────────────────────────────────────
W, H     = 900, 700
WORLD_W  = 4500          # 5× screen width; world wraps
RADAR_H  = 44            # mini-map strip height
HUD_H    = 40            # score/lives bar below radar
PLAY_Y   = RADAR_H + HUD_H   # top of actual play area (84px)
GROUND_Y = H - 60        # terrain baseline
FPS      = 60

screen = pygame.display.set_mode((W, H))
pygame.display.set_caption("Claude Defender")
clock  = pygame.time.Clock()

# ── palette ────────────────────────────────────────────────────────────────────
BLACK      = (  0,   0,   0)
SKY        = (  8,   8,  28)
WHITE      = (255, 255, 255)
CLAUDE_O   = (210, 140,  65)
CLAUDE_D   = (130,  78,  18)
CLAUDE_H   = (240, 175, 105)
GROUND_C   = ( 28,  55,  38)
RIDGE_C    = ( 55, 110,  60)
HUMAN_SKIN = (255, 205, 155)
HUMAN_SHIRT= ( 55, 120, 190)
HUMAN_PANTS= ( 30,  50, 100)
BULLET_C   = (255, 255, 100)
RADAR_BG   = ( 10,  10,  25)
RADAR_RIDG = ( 30,  55,  35)
STAR_C     = (200, 210, 255)
RED        = (220,  40,  40)
YELLOW     = (255, 220,   0)
ZAP_C      = (255, 240, 180)
GRAY       = (128, 128, 128)
MX_GRN     = (  0, 230,  80)
GREEN      = (  0, 220,  80)

# ── fonts ──────────────────────────────────────────────────────────────────────
font_big   = pygame.font.SysFont("monospace", 48, bold=True)
font_med   = pygame.font.SysFont("monospace", 26, bold=True)
font_small = pygame.font.SysFont("monospace", 16)

# ── world constants ────────────────────────────────────────────────────────────
MAX_BULLETS  = 4
BULLET_AGE   = 3.0       # seconds before a bullet expires
SAMPLE_STEP  = 5         # terrain height sample spacing (world px)
NUM_HUMANS   = 10
ZAP_DURATION = 0.7

# ── sound engine ───────────────────────────────────────────────────────────────
class SoundEngine:
    SR = 44100

    def __init__(self):
        self.shoot       = self._sweep(180, 620, 0.05, 'square', 0.16)
        self.kill        = self._kill_sound()
        self.abduct      = self._abduct_sound()
        self.rescue      = self._rescue_sound()
        self.human_die   = self._human_die_sound()
        self.player_die  = self._player_die_sound()
        self.zap         = self._zap_sound()
        self.level_clear = self._fanfare_sound()
        self.bomber_alert= self._bomber_alert_sound()
        self.bomb_drop   = self._bomb_drop_sound()
        self.bomb_explode= self._bomb_explode_sound()

    def _t(self, dur):
        return np.linspace(0, dur, int(self.SR * dur), endpoint=False)

    def _bake(self, arr, vol=0.3):
        arr = np.clip(arr * vol, -1.0, 1.0)
        s16 = (arr * 32767).astype(np.int16)
        return pygame.sndarray.make_sound(
            np.ascontiguousarray(np.column_stack([s16, s16])))

    def _tone(self, freq, dur, wave='square', vol=0.3):
        t = self._t(dur)
        s = np.sign(np.sin(2 * np.pi * freq * t)) if wave == 'square' \
            else np.sin(2 * np.pi * freq * t)
        fade = max(1, int(len(t) * 0.2))
        s[-fade:] *= np.linspace(1, 0, fade)
        return self._bake(s, vol)

    def _sweep(self, f0, f1, dur, wave='square', vol=0.2):
        t = self._t(dur)
        phase = np.cumsum(np.linspace(f0, f1, len(t)) / self.SR * 2 * np.pi)
        s = np.sign(np.sin(phase)) if wave == 'square' else np.sin(phase)
        fade = max(1, int(len(t) * 0.15))
        s[-fade:] *= np.linspace(1, 0, fade)
        return self._bake(s, vol)

    def _kill_sound(self):
        t = self._t(0.28)
        noise = np.random.default_rng(0).uniform(-1, 1, len(t))
        env   = np.exp(-t * 18)
        phase = np.cumsum(np.linspace(420, 45, len(t)) / self.SR * 2 * np.pi)
        tone  = np.sign(np.sin(phase)) * 0.5
        return self._bake((noise * 0.5 + tone * 0.5) * env, 0.38)

    def _abduct_sound(self):
        t = self._t(0.4)
        phase = np.cumsum(np.linspace(220, 880, len(t)) / self.SR * 2 * np.pi)
        s = np.sin(phase) * np.exp(-t * 1.5)
        return self._bake(s, 0.28)

    def _rescue_sound(self):
        notes, dur = [523, 659, 784], 0.08
        parts = []
        for freq in notes:
            t = self._t(dur)
            s = np.sin(2 * np.pi * freq * t)
            fade = max(1, int(len(t) * 0.2))
            s[-fade:] *= np.linspace(1, 0, fade)
            parts.append(s)
        return self._bake(np.concatenate(parts), 0.25)

    def _human_die_sound(self):
        t = self._t(0.35)
        noise = np.random.default_rng(5).uniform(-1, 1, len(t))
        env   = np.exp(-t * 9)
        phase = np.cumsum(np.linspace(380, 30, len(t)) / self.SR * 2 * np.pi)
        tone  = np.sin(phase) * 0.4
        return self._bake((noise * 0.6 + tone * 0.4) * env, 0.35)

    def _player_die_sound(self):
        t = self._t(1.0)
        noise = np.random.default_rng(2).uniform(-1, 1, len(t))
        env   = np.exp(-t * 4)
        phase = np.cumsum(np.linspace(180, 20, len(t)) / self.SR * 2 * np.pi)
        tone  = np.sin(phase) * 0.5
        return self._bake((noise * 0.5 + tone * 0.5) * env, 0.6)

    def _zap_sound(self):
        t = self._t(0.6)
        noise = np.random.default_rng(3).uniform(-1, 1, len(t))
        env   = np.exp(-t * 5)
        phase = np.cumsum(np.linspace(800, 60, len(t)) / self.SR * 2 * np.pi)
        tone  = np.sign(np.sin(phase)) * 0.3
        return self._bake((noise * 0.7 + tone * 0.3) * env, 0.7)

    def _fanfare_sound(self):
        notes, dur = [262, 330, 392, 523], 0.11
        parts = []
        for freq in notes:
            t = self._t(dur)
            s = np.sign(np.sin(2 * np.pi * freq * t))
            fade = max(1, int(len(t) * 0.2))
            s[-fade:] *= np.linspace(1, 0, fade)
            parts.append(s)
        return self._bake(np.concatenate(parts), 0.22)

    def _bomber_alert_sound(self):
        # two-tone descending siren warning
        t = self._t(0.6)
        phase = np.cumsum(np.where(
            np.arange(len(t)) % int(self.SR * 0.3) < int(self.SR * 0.15),
            np.full(len(t), 880),
            np.full(len(t), 660)
        ) / self.SR * 2 * np.pi)
        s = np.sign(np.sin(phase))
        env = np.ones(len(t))
        env[:int(self.SR * 0.05)] = np.linspace(0, 1, int(self.SR * 0.05))
        env[-int(self.SR * 0.1):] = np.linspace(1, 0, int(self.SR * 0.1))
        return self._bake(s * env, 0.28)

    def _bomb_drop_sound(self):
        # descending whistle
        t = self._t(0.5)
        phase = np.cumsum(np.linspace(1200, 200, len(t)) / self.SR * 2 * np.pi)
        s = np.sin(phase) * np.exp(-t * 0.5)
        return self._bake(s, 0.22)

    def _bomb_explode_sound(self):
        t = self._t(0.45)
        noise = np.random.default_rng(9).uniform(-1, 1, len(t))
        env   = np.exp(-t * 12)
        phase = np.cumsum(np.linspace(300, 30, len(t)) / self.SR * 2 * np.pi)
        tone  = np.sign(np.sin(phase)) * 0.4
        return self._bake((noise * 0.6 + tone * 0.4) * env, 0.5)

    def play(self, name: str):
        if _sound_on[0]:
            getattr(self, name).play()


# mutable flag so any scope can toggle without a global declaration
_sound_on = [True]

sfx = SoundEngine()

# ── high scores ────────────────────────────────────────────────────────────────
class HighScoreManager:
    MAX  = 10
    FILE = Path(__file__).parent / "highscores.json"

    def __init__(self):
        self.scores: list[dict] = []
        self._load()

    def _load(self):
        try:
            data = json.loads(self.FILE.read_text())
            self.scores = sorted(data, key=lambda x: x["score"], reverse=True)[:self.MAX]
        except Exception:
            self.scores = []

    def _save(self):
        self.FILE.write_text(json.dumps(self.scores, indent=2))

    def is_qualifying(self, score: int) -> bool:
        if score <= 0:
            return False
        if len(self.scores) < self.MAX:
            return True
        return score > self.scores[-1]["score"]

    def rank(self, score: int) -> int:
        for i, entry in enumerate(self.scores):
            if score >= entry["score"]:
                return i + 1
        return len(self.scores) + 1

    def add(self, name: str, score: int):
        self.scores.append({"name": name.upper()[:3].ljust(3), "score": score})
        self.scores.sort(key=lambda x: x["score"], reverse=True)
        self.scores = self.scores[:self.MAX]
        self._save()

    def top_score(self) -> int:
        return self.scores[0]["score"] if self.scores else 0


hs = HighScoreManager()

# ── sprite helpers ─────────────────────────────────────────────────────────────
def make_player_surf(frame: int = 0) -> pygame.Surface:
    """Side-view spaceship facing right; caller flips for left-facing."""
    w, h = 40, 20
    surf = pygame.Surface((w, h + 8), pygame.SRCALPHA)
    # main hull — elongated pointed shape
    hull = [(0, h // 2), (6, 2), (w - 6, 4), (w, h // 2), (w - 6, h - 4), (6, h - 2)]
    pygame.draw.polygon(surf, (60, 70, 90), hull)
    pygame.draw.polygon(surf, (90, 110, 140), hull, 1)
    # nose highlight
    pygame.draw.line(surf, (120, 180, 220), (w - 8, h // 2 - 3), (w - 2, h // 2), 2)
    # cockpit bubble
    pygame.draw.ellipse(surf, (30, 60, 100), (w // 2 - 7, 4, 14, 8))
    pygame.draw.ellipse(surf, (80, 140, 200), (w // 2 - 5, 5, 10, 5))
    # upper fin
    pygame.draw.polygon(surf, (50, 60, 80), [(8, 2), (16, 2), (10, -4 + h // 2)])
    # lower fin
    pygame.draw.polygon(surf, (50, 60, 80), [(8, h - 2), (16, h - 2), (10, h + 4 - h // 2)])
    # engine nozzle
    nozzle_x = 0
    pygame.draw.rect(surf, (40, 45, 60), (nozzle_x, h // 2 - 4, 6, 8))
    # engine glow
    glow = (255, 160, 40) if frame == 1 else (180, 80, 10)
    pygame.draw.ellipse(surf, glow, (nozzle_x - 4, h // 2 - 5, 10, 10))
    if frame == 1:
        # thrust flame
        for i in range(3):
            fy = h // 2 - 4 + i * 3
            pygame.draw.ellipse(surf, (255, 200, 80), (-6, fy, 8, 3))
    return surf


def make_claude_ship_surf(size: int, frame: int = 0) -> pygame.Surface:
    """Claude block avatar adapted as a flying enemy ship with engine thrust."""
    sh = size + size // 3
    surf = pygame.Surface((size, sh), pygame.SRCALPHA)
    body = pygame.Rect(0, 0, size, size)
    pygame.draw.rect(surf, CLAUDE_O, body, border_radius=size // 5)
    pygame.draw.rect(surf, CLAUDE_D, body, width=2, border_radius=size // 5)
    hi = pygame.Rect(size // 6, size // 8, size * 2 // 3, size // 5)
    pygame.draw.rect(surf, CLAUDE_H, hi, border_radius=size // 8)
    ey = size // 3
    er = max(3, size // 9)
    for ex in (size // 4, 3 * size // 4):
        pygame.draw.circle(surf, CLAUDE_D, (ex, ey), er)
        pygame.draw.circle(surf, (255, 165, 50), (ex, ey), max(1, er - 2))
    mx, my = size // 2, size * 2 // 3
    mw, mh = size * 2 // 5, size // 5
    pygame.draw.arc(surf, CLAUDE_D,
                    (mx - mw // 2, my - mh // 2, mw, mh),
                    math.pi * 0.1, math.pi * 0.9, 2)
    # engine nozzle
    nw = size // 2
    pygame.draw.rect(surf, CLAUDE_D,
                     (size // 2 - nw // 2, size - size // 8, nw, size // 8))
    # thrust flames
    flame_col = (255, 160, 40) if frame == 1 else (180, 80, 10)
    flame_h   = size // 4 if frame == 1 else size // 6
    rng = random.Random(frame * 7 + size)
    for i in range(3):
        fx = size // 4 + i * (size // 4)
        fh = flame_h + rng.randint(-2, 2)
        pygame.draw.ellipse(surf, flame_col, (fx - 4, size, 8, max(4, fh)))
    return surf


def make_human_surf_small(size: int = 16) -> pygame.Surface:
    """Tiny space-suit human standing on the ground."""
    surf = pygame.Surface((size, size * 2), pygame.SRCALPHA)
    s = size
    # helmet
    pygame.draw.circle(surf, HUMAN_SKIN, (s // 2, s // 4), s // 4)
    pygame.draw.circle(surf, (180, 220, 255), (s // 2, s // 4), s // 5, 1)
    # body / suit
    pygame.draw.rect(surf, HUMAN_SHIRT, (s // 4, s // 2, s // 2, s // 2))
    # arms
    pygame.draw.line(surf, HUMAN_SKIN, (s // 4, s // 2 + 2), (2, s * 3 // 4), 2)
    pygame.draw.line(surf, HUMAN_SKIN, (3 * s // 4, s // 2 + 2), (s - 2, s * 3 // 4), 2)
    # legs
    pygame.draw.rect(surf, HUMAN_PANTS, (s // 4, s, s // 5, s // 2))
    pygame.draw.rect(surf, HUMAN_PANTS, (s // 2, s, s // 5, s // 2))
    return surf


def make_claude_bomber_surf(frame: int = 0) -> pygame.Surface:
    """Wide, heavy Claude bomber with a visible bomb bay and wing pylons."""
    bw, bh = 64, 36    # body dimensions
    total_h = bh + 14  # extra for bomb bay below
    surf = pygame.Surface((bw, total_h), pygame.SRCALPHA)

    # wide fuselage
    body = pygame.Rect(0, 0, bw, bh)
    pygame.draw.rect(surf, CLAUDE_O, body, border_radius=bh // 4)
    pygame.draw.rect(surf, CLAUDE_D, body, width=2, border_radius=bh // 4)
    # highlight strip
    hi = pygame.Rect(bw // 6, bh // 8, bw * 2 // 3, bh // 5)
    pygame.draw.rect(surf, CLAUDE_H, hi, border_radius=4)
    # eyes (two, close together near center)
    ey = bh // 3
    er = 4
    for ex in (bw // 2 - 10, bw // 2 + 10):
        pygame.draw.circle(surf, CLAUDE_D, (ex, ey), er)
        pygame.draw.circle(surf, (255, 165, 50), (ex, ey), er - 2)
    # mouth
    mx, my = bw // 2, bh * 2 // 3
    mw, mh = bw * 2 // 7, bh // 5
    pygame.draw.arc(surf, CLAUDE_D,
                    (mx - mw // 2, my - mh // 2, mw, mh),
                    math.pi * 0.1, math.pi * 0.9, 2)
    # wing pylons (left and right)
    for wx in (6, bw - 14):
        pygame.draw.rect(surf, CLAUDE_D, (wx, bh // 3, 8, bh // 3))
    # left/right engine nacelles on wingtips
    for ex in (0, bw - 10):
        pygame.draw.rect(surf, (50, 55, 70), (ex, bh // 2 - 4, 10, 8), border_radius=3)
        glow = (255, 160, 40) if frame == 1 else (160, 70, 10)
        pygame.draw.circle(surf, glow, (ex + 2, bh // 2), 4)
    # bomb bay door (center underside)
    bay_x = bw // 2 - 10
    pygame.draw.rect(surf, CLAUDE_D, (bay_x, bh - 2, 20, 8), border_radius=2)
    # bomb silhouette visible in bay
    pygame.draw.ellipse(surf, (60, 60, 60), (bay_x + 4, bh, 12, 7))
    pygame.draw.circle(surf, (200, 200, 40), (bay_x + 10, bh + 10), 3)  # fuse glow
    return surf


# ── terrain ────────────────────────────────────────────────────────────────────
class Terrain:
    SAMPLE_STEP = SAMPLE_STEP
    BLEND_ZONE  = 300   # px; terrain wraps smoothly over this distance

    def __init__(self, world_w: int, seed: int = 42):
        self.world_w = world_w
        n = world_w // self.SAMPLE_STEP + 2
        self.heights = self._generate(n, seed)
        self._radar_surf: pygame.Surface | None = None

    def _generate(self, n: int, seed: int) -> list:
        rng    = random.Random(seed)
        phases = [rng.uniform(0, math.tau) for _ in range(4)]
        amps   = [90, 45, 20, 8]
        perds  = [1800, 700, 280, 110]
        base   = GROUND_Y - 80

        raw = []
        for i in range(n):
            wx = i * self.SAMPLE_STEP
            y  = base
            for a, p, ph in zip(amps, perds, phases):
                y += a * math.sin(2 * math.pi * wx / p + ph)
            raw.append(y)

        # blend last BLEND_ZONE so terrain wraps seamlessly
        h0 = raw[0]
        blend_samples = self.BLEND_ZONE // self.SAMPLE_STEP
        for i in range(n):
            wx = i * self.SAMPLE_STEP
            dist_from_end = self.world_w - wx
            if dist_from_end < self.BLEND_ZONE and dist_from_end >= 0:
                t = 1.0 - dist_from_end / self.BLEND_ZONE
                raw[i] = raw[i] * (1 - t) + h0 * t

        lo = PLAY_Y + 60
        hi = GROUND_Y - 15
        return [max(lo, min(hi, y)) for y in raw]

    def height_at(self, world_x: float) -> float:
        wx  = world_x % self.world_w
        idx = wx / self.SAMPLE_STEP
        lo  = int(idx) % len(self.heights)
        hi  = (lo + 1) % len(self.heights)
        frac = idx - int(idx)
        return self.heights[lo] * (1 - frac) + self.heights[hi] * frac

    def draw(self, surf: pygame.Surface, camera_x: float):
        pts = [(0, H)]
        for sx in range(0, W + self.SAMPLE_STEP, self.SAMPLE_STEP):
            wx = (sx + camera_x) % self.world_w
            y  = self.height_at(wx)
            pts.append((sx, int(y)))
        pts.append((W, H))
        if len(pts) >= 3:
            pygame.draw.polygon(surf, GROUND_C, pts)
        ridge = pts[1:-1]
        if len(ridge) >= 2:
            pygame.draw.lines(surf, RIDGE_C, False, ridge, 2)

    def build_radar_surf(self, radar_rect: pygame.Rect):
        """Pre-bake the terrain silhouette for the radar strip."""
        s = pygame.Surface((radar_rect.width, radar_rect.height), pygame.SRCALPHA)
        scale = radar_rect.width / self.world_w
        pts   = [(0, radar_rect.height)]
        for i, h in enumerate(self.heights):
            sx = int(i * self.SAMPLE_STEP * scale)
            sy = int(radar_rect.height * (h - PLAY_Y) / (H - PLAY_Y))
            sy = max(0, min(radar_rect.height, sy))
            pts.append((sx, sy))
        pts.append((radar_rect.width, radar_rect.height))
        if len(pts) >= 3:
            pygame.draw.polygon(s, RADAR_RIDG, pts)
        self._radar_surf = s

    def draw_radar(self, surf: pygame.Surface, radar_rect: pygame.Rect):
        if self._radar_surf:
            surf.blit(self._radar_surf, radar_rect.topleft)


# ── game objects ───────────────────────────────────────────────────────────────
class Bullet:
    SPEED = 700

    def __init__(self, world_x: float, y: float, direction: int):
        self.world_x   = float(world_x)
        self.y         = float(y)
        self.vx        = direction * self.SPEED
        self.direction = direction
        self.age       = 0.0
        self.dead      = False

    def update(self, dt: float):
        self.world_x = (self.world_x + self.vx * dt) % WORLD_W
        self.age    += dt
        if self.age > BULLET_AGE:
            self.dead = True

    def draw(self, surf: pygame.Surface, camera_x: float):
        sx = (self.world_x - camera_x) % WORLD_W
        if sx < -20 or sx > W + 20:
            return
        ex = sx + self.direction * 10
        pygame.draw.line(surf, BULLET_C, (int(sx), int(self.y)), (int(ex), int(self.y)), 3)
        pygame.draw.circle(surf, WHITE, (int(sx), int(self.y)), 2)


class ClaudeBullet:
    """Enemy projectile fired by Claude ships toward the player."""
    SPEED = 260

    def __init__(self, world_x: float, y: float, target_wx: float, target_y: float):
        self.world_x = float(world_x)
        self.y       = float(y)
        dx = target_wx - world_x
        if dx > WORLD_W / 2:  dx -= WORLD_W
        if dx < -WORLD_W / 2: dx += WORLD_W
        dy = target_y - y
        dist = math.hypot(dx, dy) or 1
        self.vx  = dx / dist * self.SPEED
        self.vy  = dy / dist * self.SPEED
        self.age = 0.0
        self.dead= False

    def update(self, dt: float):
        self.world_x = (self.world_x + self.vx * dt) % WORLD_W
        self.y      += self.vy * dt
        self.age    += dt
        if self.age > 5.0 or self.y > H + 20 or self.y < PLAY_Y:
            self.dead = True

    def draw(self, surf: pygame.Surface, camera_x: float):
        sx = (self.world_x - camera_x) % WORLD_W
        if sx < -10 or sx > W + 10:
            return
        pygame.draw.circle(surf, CLAUDE_O, (int(sx), int(self.y)), 5)
        pygame.draw.circle(surf, CLAUDE_H, (int(sx), int(self.y)), 3)


class Particle:
    def __init__(self, x: float, y: float, color):
        self.x = float(x)
        self.y = float(y)
        angle  = random.uniform(0, math.tau)
        speed  = random.uniform(60, 220)
        self.vx       = math.cos(angle) * speed
        self.vy       = math.sin(angle) * speed
        self.life     = random.uniform(0.3, 0.8)
        self.max_life = self.life
        self.color    = color
        self.r        = random.randint(2, 5)

    def update(self, dt: float):
        self.x   += self.vx * dt
        self.y   += self.vy * dt
        self.vy  += 200 * dt
        self.life -= dt

    def draw(self, surf: pygame.Surface):
        alpha = max(0.0, self.life / self.max_life)
        r, g, b = self.color
        col = (int(r * alpha), int(g * alpha), int(b * alpha))
        pygame.draw.circle(surf, col, (int(self.x), int(self.y)), self.r)


class SuperZapperEffect:
    def __init__(self, cx: float, cy: float):
        self.cx   = cx
        self.cy   = cy
        self.t    = 0.0
        self.dead = False

    def update(self, dt: float):
        self.t += dt
        if self.t >= ZAP_DURATION:
            self.dead = True

    def draw(self, surf: pygame.Surface):
        progress = self.t / ZAP_DURATION
        # full-screen flash at start
        if progress < 0.15:
            a = int(200 * (1.0 - progress / 0.15))
            flash = pygame.Surface((W, H), pygame.SRCALPHA)
            flash.fill((255, 240, 180, a))
            surf.blit(flash, (0, 0))
        # expanding outer ring
        radius = int(progress * 750)
        if radius > 0:
            col = (min(255, ZAP_C[0]),
                   min(255, int(ZAP_C[1] * (1 - progress * 0.5))),
                   int(ZAP_C[2] * (1 - progress)))
            width = max(1, int(8 * (1 - progress)))
            pygame.draw.circle(surf, col, (int(self.cx), int(self.cy)), radius, width)
        # inner ring
        inner = int(progress * 380)
        if inner > 0:
            pygame.draw.circle(surf, WHITE,
                               (int(self.cx), int(self.cy)), inner,
                               max(1, int(4 * (1 - progress))))


class Human:
    SIZE = 16

    def __init__(self, world_x: float, terrain: Terrain):
        self.world_x  = float(world_x)
        self.y        = terrain.height_at(world_x) - 14
        self.abducted = False
        self.carrier  = None   # ClaudeShip that has grabbed this human
        self.falling  = False
        self.vy       = 0.0
        self.dead     = False
        self.sway     = random.uniform(0, math.tau)
        self.surf     = make_human_surf_small(self.SIZE)

    def update(self, dt: float, terrain: Terrain, player, particles: list) -> bool:
        """Returns True if player rescued this human."""
        self.sway += dt * 1.2
        if self.falling:
            self.vy  += 280 * dt
            self.y   += self.vy * dt
            # catch by player
            pr = player.rect_world
            hr = pygame.Rect(int(self.world_x) - self.SIZE // 2,
                             int(self.y) - 14, self.SIZE, 28)
            # wrap-aware collision
            dx = abs(self.world_x - player.world_x)
            if dx > WORLD_W / 2:
                dx = WORLD_W - dx
            if dx < 30 and abs(self.y - player.y) < 30:
                self.dead = True
                sfx.play('rescue')
                for _ in range(12):
                    particles.append(Particle(
                        (player.world_x - _camera_x[0]) % WORLD_W,
                        player.y, GREEN))
                return True
            # hits ground
            gy = terrain.height_at(self.world_x) - 14
            if self.y >= gy:
                self.dead = True
                sfx.play('human_die')
                sx = (self.world_x - _camera_x[0]) % WORLD_W
                for _ in range(8):
                    particles.append(Particle(sx, self.y, HUMAN_SKIN))
        return False

    def draw(self, surf: pygame.Surface, camera_x: float):
        sx = (self.world_x - camera_x) % WORLD_W
        if sx < -self.SIZE or sx > W + self.SIZE:
            return
        sway_x = int(math.sin(self.sway) * 1.5)
        surf.blit(self.surf, (int(sx) + sway_x - self.SIZE // 2, int(self.y) - 14))


class ClaudeShip:
    SIZE        = 38
    STATE_HOVER = 'hover'
    STATE_DIVE  = 'dive'
    STATE_CARRY = 'carry'

    SHOOT_INTERVAL_BASE = 3.5   # seconds between shots (base); decreases with level

    def __init__(self, world_x: float, y: float, level: int = 1):
        self.world_x      = float(world_x)
        self.y            = float(y)
        self.level        = level
        self.state        = self.STATE_HOVER
        self.target_human = None
        self.carried_human= None
        self.vx_drift     = random.choice([-1, 1]) * 40
        self.hover_phase  = random.uniform(0, math.tau)
        self.hover_base_y = y
        self.dead         = False
        self.anim_frame   = 0
        self.anim_tick    = 0.0
        self.surfs        = [make_claude_ship_surf(self.SIZE, f) for f in (0, 1)]
        self._dive_speed  = 80 + level * 12
        self._carry_speed = 60 + level * 8
        self._dive_chance = 0.003 + level * 0.002
        # shooting
        self._shoot_interval = max(1.2, self.SHOOT_INTERVAL_BASE - level * 0.3)
        self._shoot_timer    = random.uniform(0, self._shoot_interval)

    def update(self, dt: float, humans: list, terrain: "Terrain"):
        self.anim_tick += dt
        if self.anim_tick > 0.25:
            self.anim_tick = 0.0
            self.anim_frame ^= 1

        if self.state == self.STATE_HOVER:
            self._update_hover(dt, humans, terrain)
        elif self.state == self.STATE_DIVE:
            self._update_dive(dt, terrain)
        elif self.state == self.STATE_CARRY:
            self._update_carry(dt, terrain)

        self.world_x = self.world_x % WORLD_W

        # tick shoot timer
        self._shoot_timer += dt

        # clamp above terrain at all times
        floor_y = terrain.height_at(self.world_x) - self.SIZE // 2 - 2
        if self.y > floor_y:
            self.y = floor_y

    def take_shot(self, player: "Player") -> "ClaudeBullet | None":
        """Return a ClaudeBullet aimed at the player if the shoot timer fired."""
        if self.state == self.STATE_CARRY:
            return None
        if self._shoot_timer < self._shoot_interval:
            return None
        self._shoot_timer = 0.0
        return ClaudeBullet(self.world_x, self.y + self.SIZE // 2,
                            player.world_x, player.y)

    def _update_hover(self, dt: float, humans: list, terrain: "Terrain"):
        self.hover_phase += dt * 1.8
        # keep hover base well above terrain at current position
        floor_y = terrain.height_at(self.world_x) - self.SIZE // 2 - 20
        self.hover_base_y = max(PLAY_Y + 40,
                                min(min(PLAY_Y + (GROUND_Y - PLAY_Y) * 0.5, floor_y),
                                    self.hover_base_y))
        self.y = self.hover_base_y + math.sin(self.hover_phase) * 12
        self.world_x += self.vx_drift * dt
        available = [h for h in humans if not h.abducted and not h.dead and not h.falling]
        if available and random.random() < self._dive_chance:
            self.target_human = random.choice(available)
            self.target_human.abducted = True
            self.state = self.STATE_DIVE
            sfx.play('abduct')

    def _update_dive(self, dt: float, terrain: "Terrain"):
        if self.target_human is None or self.target_human.dead:
            self.state = self.STATE_HOVER
            if self.target_human and not self.target_human.dead:
                self.target_human.abducted = False
            self.target_human = None
            return
        # wrap-aware horizontal homing
        dx = self.target_human.world_x - self.world_x
        if dx > WORLD_W / 2:  dx -= WORLD_W
        if dx < -WORLD_W / 2: dx += WORLD_W
        move = min(abs(dx), 60 * dt)
        self.world_x += math.copysign(move, dx) if dx != 0 else 0
        self.y += self._dive_speed * dt
        # stop at terrain surface (human stands here)
        floor_y = terrain.height_at(self.world_x) - self.SIZE // 2 - 2
        if self.y >= floor_y:
            self.y = floor_y
            if self.target_human and not self.target_human.dead:
                self._grab_human()
            else:
                self.state = self.STATE_HOVER
                self.target_human = None
            return
        # arrived at human
        if abs(self.y - self.target_human.y) < 12 and abs(dx) < 24:
            self._grab_human()

    def _grab_human(self):
        h = self.target_human
        h.carrier = self
        self.carried_human = h
        self.state = self.STATE_CARRY
        self.target_human = None

    def _update_carry(self, dt: float, terrain: "Terrain"):
        self.y -= self._carry_speed * dt
        if self.carried_human:
            self.carried_human.world_x = self.world_x
            self.carried_human.y       = self.y + self.SIZE // 2 + 8
        if self.y < PLAY_Y - self.SIZE - 10:
            # escaped; human is lost
            if self.carried_human:
                self.carried_human.dead = True
                self.carried_human.abducted = False
                self.carried_human.carrier  = None
                self.carried_human = None
            self.dead = True

    def kill(self):
        if self.carried_human:
            h = self.carried_human
            h.abducted = False
            h.carrier  = None
            h.falling  = True
            h.vy       = 0.0
            self.carried_human = None
        if self.target_human and not self.carried_human:
            self.target_human.abducted = False
            self.target_human = None
        self.dead = True

    def draw(self, surf: pygame.Surface, camera_x: float):
        sx = (self.world_x - camera_x) % WORLD_W
        if sx < -self.SIZE or sx > W + self.SIZE:
            return
        s = self.surfs[self.anim_frame]
        surf.blit(s, (int(sx) - self.SIZE // 2, int(self.y) - self.SIZE // 2))
        # draw tether line when carrying
        if self.carried_human and not self.carried_human.dead:
            hy = self.carried_human.y
            hsx = (self.carried_human.world_x - camera_x) % WORLD_W
            pygame.draw.line(surf, CLAUDE_D,
                             (int(sx), int(self.y) + self.SIZE // 2),
                             (int(hsx), int(hy) - 10), 1)


class ClaudeBomb:
    """Slow-falling bomb dropped by a ClaudeBomber. Shootable by the player."""
    FALL_SPEED = 85    # px/s downward — slow enough for player to intercept
    SIZE       = 10    # collision radius

    def __init__(self, world_x: float, y: float, target_human=None):
        self.world_x      = float(world_x)
        self.y            = float(y)
        self.target_human = target_human   # drifts slightly toward target X
        self.dead         = False
        self.exploded     = False
        self.trail: list  = []  # (x, y) screen positions for smoke trail
        self.trail_timer  = 0.0

    def update(self, dt: float, terrain: "Terrain", humans: list,
               particles: list, camera_x: float) -> bool:
        """Returns True if bomb killed a human."""
        self.y += self.FALL_SPEED * dt

        # gentle horizontal drift toward target human
        if self.target_human and not self.target_human.dead:
            dx = self.target_human.world_x - self.world_x
            if dx > WORLD_W / 2:  dx -= WORLD_W
            if dx < -WORLD_W / 2: dx += WORLD_W
            drift = math.copysign(min(abs(dx), 18 * dt), dx) if dx != 0 else 0
            self.world_x = (self.world_x + drift) % WORLD_W

        # smoke trail
        self.trail_timer += dt
        if self.trail_timer > 0.06:
            self.trail_timer = 0.0
            sx = (self.world_x - camera_x) % WORLD_W
            self.trail.append([sx, self.y, 1.0])   # [x, y, alpha]
        for t in self.trail:
            t[2] -= dt * 2.5
        self.trail = [t for t in self.trail if t[2] > 0]

        # check hit human
        for h in humans:
            if h.dead or h.abducted:
                continue
            dx = self.world_x - h.world_x
            if dx > WORLD_W / 2:  dx -= WORLD_W
            if dx < -WORLD_W / 2: dx += WORLD_W
            if abs(dx) < self.SIZE + 8 and abs(self.y - h.y) < self.SIZE + 14:
                self._explode(particles, camera_x)
                h.dead = True
                sfx.play('human_die')
                return True

        # check hit terrain
        if self.y >= terrain.height_at(self.world_x):
            self._explode(particles, camera_x)
            return False

        # off bottom of screen
        if self.y > H + 20:
            self.dead = True
        return False

    def _explode(self, particles: list, camera_x: float):
        self.dead     = True
        self.exploded = True
        sfx.play('bomb_explode')
        sx = (self.world_x - camera_x) % WORLD_W
        for _ in range(20):
            particles.append(Particle(sx, self.y, RED))
        for _ in range(10):
            particles.append(Particle(sx, self.y, YELLOW))

    def draw(self, surf: pygame.Surface, camera_x: float):
        # smoke trail
        for tx, ty, alpha in self.trail:
            a = int(alpha * 140)
            c = (a, a, a)
            pygame.draw.circle(surf, c, (int(tx), int(ty)), 3)
        sx = (self.world_x - camera_x) % WORLD_W
        if sx < -20 or sx > W + 20:
            return
        # bomb body — dark oval with yellow fuse glow
        pygame.draw.ellipse(surf, (50, 50, 50), (int(sx) - 6, int(self.y) - 8, 12, 16))
        pygame.draw.ellipse(surf, CLAUDE_D,    (int(sx) - 6, int(self.y) - 8, 12, 16), 1)
        # pulsing fuse tip
        fuse_r = 3 + int(math.sin(self.y * 0.3) * 1.5)
        pygame.draw.circle(surf, YELLOW, (int(sx), int(self.y) - 10), fuse_r)
        pygame.draw.circle(surf, WHITE,  (int(sx), int(self.y) - 10), max(1, fuse_r - 2))


class ClaudeBomber:
    """Heavy bomber that flies straight across the world and drops bombs on humans."""
    WIDTH      = 64
    HEIGHT     = 36
    SPEED      = 115        # px/s horizontal
    BOMB_INTERVAL_MIN = 2.5
    BOMB_INTERVAL_MAX = 5.0
    SCORE_VALUE       = 500

    def __init__(self, world_x: float, direction: int, level: int = 1):
        self.world_x    = float(world_x)
        self.y          = float(random.uniform(PLAY_Y + 60, PLAY_Y + (GROUND_Y - PLAY_Y) * 0.35))
        self.direction  = direction   # +1 right, -1 left
        self.level      = level
        self.speed      = self.SPEED + level * 8
        self.dead       = False
        self.anim_frame = 0
        self.anim_tick  = 0.0
        self.bomb_timer = random.uniform(1.0, 2.5)  # first drop delay
        self.bomb_interval = random.uniform(self.BOMB_INTERVAL_MIN,
                                            self.BOMB_INTERVAL_MAX)
        self.surfs      = [make_claude_bomber_surf(f) for f in (0, 1)]
        self._laps      = 0   # number of times it has wrapped the world

    def update(self, dt: float, humans: list) -> "ClaudeBomb | None":
        """Returns a ClaudeBomb if one is dropped this frame, else None."""
        self.anim_tick += dt
        if self.anim_tick > 0.2:
            self.anim_tick = 0.0
            self.anim_frame ^= 1

        prev_x = self.world_x
        self.world_x = (self.world_x + self.direction * self.speed * dt) % WORLD_W

        # count world laps; retire after 2 full passes
        if self.direction == 1 and prev_x > self.world_x:
            self._laps += 1
        elif self.direction == -1 and prev_x < self.world_x:
            self._laps += 1
        if self._laps >= 2:
            self.dead = True
            return None

        # bomb drop timer
        self.bomb_timer += dt
        if self.bomb_timer >= self.bomb_interval:
            self.bomb_timer = 0.0
            self.bomb_interval = random.uniform(self.BOMB_INTERVAL_MIN,
                                                self.BOMB_INTERVAL_MAX)
            return self._drop_bomb(humans)
        return None

    def _drop_bomb(self, humans: list) -> "ClaudeBomb":
        # target the nearest human below
        available = [h for h in humans if not h.dead and not h.abducted]
        target = None
        if available:
            # pick closest by world-wrap distance
            def wrap_dist(h):
                dx = abs(h.world_x - self.world_x)
                return min(dx, WORLD_W - dx)
            target = min(available, key=wrap_dist)
        sfx.play('bomb_drop')
        return ClaudeBomb(self.world_x, self.y + self.HEIGHT // 2 + 2, target)

    def draw(self, surf: pygame.Surface, camera_x: float):
        sx = (self.world_x - camera_x) % WORLD_W
        if sx < -self.WIDTH or sx > W + self.WIDTH:
            return
        s = self.surfs[self.anim_frame]
        if self.direction == -1:
            s = pygame.transform.flip(s, True, False)
        surf.blit(s, (int(sx) - self.WIDTH // 2, int(self.y) - self.HEIGHT // 2))


class Player:
    SIZE         = 32
    SPEED_H      = 320
    SPEED_V      = 240
    ACCEL        = 900
    SHOT_COOLDOWN= 0.22
    MAX_ZAPPERS  = 3

    def __init__(self):
        self.world_x   = float(WORLD_W // 2)
        self.y         = float(H // 2)
        self.vx        = 0.0
        self.vy        = 0.0
        self.facing    = 1
        self.lives     = 3
        self.zappers   = self.MAX_ZAPPERS
        self.cooldown  = 0.0
        self.invincible= 0.0
        self.dead      = False
        self.anim_frame= 0
        self.anim_tick = 0.0
        self.surfs     = [make_player_surf(f) for f in (0, 1)]

    def update(self, dt: float, keys, terrain: Terrain):
        # horizontal
        if keys[pygame.K_LEFT] or keys[pygame.K_a]:
            self.vx = max(-self.SPEED_H, self.vx - self.ACCEL * dt)
            self.facing = -1
        elif keys[pygame.K_RIGHT] or keys[pygame.K_d]:
            self.vx = min(self.SPEED_H, self.vx + self.ACCEL * dt)
            self.facing = 1
        else:
            if abs(self.vx) < 20:
                self.vx = 0
            else:
                self.vx -= math.copysign(self.ACCEL * 0.5 * dt, self.vx)
        # vertical
        if keys[pygame.K_UP] or keys[pygame.K_w]:
            self.vy = max(-self.SPEED_V, self.vy - self.ACCEL * dt)
        elif keys[pygame.K_DOWN] or keys[pygame.K_s]:
            self.vy = min(self.SPEED_V, self.vy + self.ACCEL * dt)
        else:
            if abs(self.vy) < 20:
                self.vy = 0
            else:
                self.vy -= math.copysign(self.ACCEL * 0.5 * dt, self.vy)

        self.world_x = (self.world_x + self.vx * dt) % WORLD_W
        terrain_floor = terrain.height_at(self.world_x) - self.SIZE // 2 - 4
        self.y = max(PLAY_Y + 20, min(terrain_floor, self.y + self.vy * dt))

        self.cooldown    = max(0.0, self.cooldown - dt)
        self.invincible  = max(0.0, self.invincible - dt)

        self.anim_tick += dt
        if self.anim_tick > 0.15:
            self.anim_tick = 0.0
            self.anim_frame ^= 1

    def shoot(self) -> "Bullet | None":
        if self.cooldown > 0:
            return None
        self.cooldown = self.SHOT_COOLDOWN
        bx = self.world_x + self.facing * (self.SIZE // 2 + 4)
        return Bullet(bx, self.y, self.facing)

    def draw(self, surf: pygame.Surface, camera_x: float):
        if self.invincible > 0 and int(self.invincible * 10) % 2:
            return
        sx = (self.world_x - camera_x) % WORLD_W
        s  = self.surfs[self.anim_frame]
        if self.facing == -1:
            s = pygame.transform.flip(s, True, False)
        surf.blit(s, (int(sx) - self.SIZE // 2, int(self.y) - self.SIZE // 2))

    @property
    def rect_world(self) -> pygame.Rect:
        return pygame.Rect(int(self.world_x) - self.SIZE // 2,
                           int(self.y) - self.SIZE // 2,
                           self.SIZE, self.SIZE)


# ── stars ──────────────────────────────────────────────────────────────────────
STARS = [(random.randint(0, W), random.randint(PLAY_Y, H - 80),
          random.randint(1, 3)) for _ in range(140)]


def draw_stars(surf: pygame.Surface, camera_x: float):
    for wx, sy, br in STARS:
        sx = int((wx - camera_x * 0.6) % W)
        c  = (min(255, STAR_C[0] * br // 3),
              min(255, STAR_C[1] * br // 3),
              min(255, STAR_C[2] * br // 3))
        surf.set_at((sx, sy), c)


# ── radar ──────────────────────────────────────────────────────────────────────
def draw_radar(surf: pygame.Surface, camera_x: float,
               terrain: Terrain, player: Player,
               enemies: list, humans: list,
               bombers: list | None = None):
    r = pygame.Rect(0, 0, W, RADAR_H)
    pygame.draw.rect(surf, RADAR_BG, r)
    terrain.draw_radar(surf, r)
    # camera viewport rect
    cam_left  = int(camera_x / WORLD_W * W)
    cam_right = int((camera_x + W) / WORLD_W * W)
    pygame.draw.rect(surf, (60, 60, 80),
                     (cam_left % W, 1, cam_right - cam_left, RADAR_H - 2), 1)
    # enemy dots
    for e in enemies:
        ex = int(e.world_x / WORLD_W * W)
        ey = max(2, min(RADAR_H - 3, int(RADAR_H * (e.y - PLAY_Y) / (H - PLAY_Y))))
        pygame.draw.circle(surf, CLAUDE_O, (ex, ey), 2)
    # human dots
    for h in humans:
        if h.dead:
            continue
        hx = int(h.world_x / WORLD_W * W)
        pygame.draw.circle(surf, GREEN, (hx, RADAR_H - 5), 2)
    # bomber dots — red, larger
    if bombers:
        for bomber in bombers:
            bx = int(bomber.world_x / WORLD_W * W)
            by = max(3, min(RADAR_H - 4, int(RADAR_H * (bomber.y - PLAY_Y) / (H - PLAY_Y))))
            pygame.draw.circle(surf, RED, (bx, by), 4)
    # player dot
    px = int(player.world_x / WORLD_W * W)
    pygame.draw.circle(surf, WHITE, (px, RADAR_H // 2), 3)
    pygame.draw.rect(surf, GRAY, r, 1)


# ── HUD ────────────────────────────────────────────────────────────────────────
def draw_hud(surf: pygame.Surface, score: int, lives: int, zappers: int,
             level: int, hi: int):
    bar_y = RADAR_H
    pygame.draw.rect(surf, (12, 12, 38), (0, bar_y, W, HUD_H))
    pygame.draw.line(surf, (30, 30, 60), (0, bar_y + HUD_H - 1), (W, bar_y + HUD_H - 1), 1)
    surf.blit(font_med.render(f"SCORE {score:06d}", True, WHITE), (10, bar_y + 7))
    surf.blit(font_med.render(f"HI {hi:06d}", True, YELLOW), (W // 2 - 65, bar_y + 7))
    surf.blit(font_med.render(f"LV {level}", True, MX_GRN), (W - 130, bar_y + 7))
    # lives as tiny ship icons
    tiny_s = make_player_surf(0)
    for i in range(max(0, lives - 1)):
        surf.blit(tiny_s, (10 + i * 34, bar_y + HUD_H + 4))
    # zapper icons (Z)
    for i in range(zappers):
        zx = W - 30 - i * 22
        pygame.draw.circle(surf, ZAP_C, (zx, bar_y + HUD_H + 12), 8, 2)
        zt = font_small.render("Z", True, ZAP_C)
        surf.blit(zt, (zx - zt.get_width() // 2, bar_y + HUD_H + 4))


# ── screens ────────────────────────────────────────────────────────────────────
def draw_scores_table(surf: pygame.Surface, cx: int, y: int,
                      count: int = 10, highlight: int = -1):
    hdr = font_small.render("  #   NAME    SCORE", True, CLAUDE_O)
    surf.blit(hdr, (cx - hdr.get_width() // 2, y))
    pygame.draw.line(surf, CLAUDE_D, (cx - 120, y + 22), (cx + 120, y + 22), 1)
    y += 28
    for i, entry in enumerate(hs.scores[:count]):
        rank = i + 1
        line = f" {rank:>2}.  {entry['name']}   {entry['score']:>06d}"
        col  = YELLOW if rank == highlight else (WHITE if rank <= 3 else GRAY)
        row  = font_small.render(line, True, col)
        surf.blit(row, (cx - row.get_width() // 2, y + i * 22))
    if not hs.scores:
        empty = font_small.render("— no scores yet —", True, GRAY)
        surf.blit(empty, (cx - empty.get_width() // 2, y))


def enter_initials_screen(score: int, rank: int) -> str:
    letters = ['A', 'A', 'A']
    pos     = 0
    t       = 0.0
    while True:
        dt = clock.tick(FPS) / 1000
        t += dt
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if e.type == pygame.KEYDOWN:
                if e.key in (pygame.K_UP, pygame.K_w):
                    letters[pos] = chr((ord(letters[pos]) - ord('A') - 1) % 26 + ord('A'))
                elif e.key in (pygame.K_DOWN, pygame.K_s):
                    letters[pos] = chr((ord(letters[pos]) - ord('A') + 1) % 26 + ord('A'))
                elif e.key in (pygame.K_RIGHT, pygame.K_SPACE):
                    if pos < 2:
                        pos += 1
                    else:
                        return ''.join(letters)
                elif e.key in (pygame.K_LEFT, pygame.K_BACKSPACE):
                    pos = max(0, pos - 1)
                elif e.key == pygame.K_RETURN:
                    return ''.join(letters)
                elif e.key == pygame.K_ESCAPE:
                    return 'AAA'
                elif pygame.K_a <= e.key <= pygame.K_z:
                    letters[pos] = chr(e.key - pygame.K_a + ord('A'))
                    if pos < 2:
                        pos += 1

        screen.fill(SKY)
        draw_stars(screen, 0)
        t1 = font_big.render("NEW HIGH SCORE!", True, YELLOW)
        screen.blit(t1, (W // 2 - t1.get_width() // 2, 80))
        t2 = font_med.render(f"{score:06d}   RANK #{rank}", True, WHITE)
        screen.blit(t2, (W // 2 - t2.get_width() // 2, 150))
        hint = font_small.render(
            "↑ ↓  change letter      → / SPACE  next      ENTER  confirm",
            True, GRAY)
        screen.blit(hint, (W // 2 - hint.get_width() // 2, 210))
        slot_w, gap = 80, 18
        total = 3 * slot_w + 2 * gap
        sx = W // 2 - total // 2
        for i, ch in enumerate(letters):
            blink  = (pos == i and int(t * 3) % 2 == 0)
            active = (pos == i)
            col    = YELLOW if active else WHITE
            rect   = pygame.Rect(sx + i * (slot_w + gap), 250, slot_w, 100)
            pygame.draw.rect(screen,
                             (35, 35, 70) if active else (20, 20, 45),
                             rect, border_radius=10)
            pygame.draw.rect(screen, col, rect, width=2, border_radius=10)
            if not blink:
                cs = font_big.render(ch, True, col)
                screen.blit(cs, (rect.centerx - cs.get_width() // 2,
                                 rect.centery - cs.get_height() // 2))
        draw_scores_table(screen, W // 2, 375, count=5, highlight=rank)
        pygame.display.flip()


def draw_sound_toggle(surf: pygame.Surface):
    """Draw the sound on/off toggle button in the bottom-right of the title screen."""
    on   = _sound_on[0]
    label= "M  SOUND: ON " if on else "M  SOUND: OFF"
    fg   = MX_GRN if on else (140, 50, 50)
    bg   = (10, 35, 15) if on else (35, 10, 10)
    border = (30, 100, 40) if on else (100, 30, 30)

    txt  = font_small.render(label, True, fg)
    pad  = 8
    rect = pygame.Rect(W - txt.get_width() - pad * 2 - 12,
                       H - txt.get_height() - pad * 2 - 8,
                       txt.get_width() + pad * 2,
                       txt.get_height() + pad * 2)
    pygame.draw.rect(surf, bg, rect, border_radius=6)
    pygame.draw.rect(surf, border, rect, width=1, border_radius=6)
    surf.blit(txt, (rect.x + pad, rect.y + pad))


def draw_title_scores(surf: pygame.Surface, x: int, y: int):
    """Arcade-style high score board for the title screen."""
    # panel background
    panel = pygame.Rect(x - 10, y - 10, 340, 310)
    pygame.draw.rect(surf, (12, 12, 38), panel, border_radius=8)
    pygame.draw.rect(surf, CLAUDE_D, panel, width=2, border_radius=8)

    hdr = font_med.render("HIGH  SCORES", True, CLAUDE_O)
    surf.blit(hdr, (x + panel.width // 2 - hdr.get_width() // 2 - 10, y))
    pygame.draw.line(surf, CLAUDE_D, (x - 8, y + 34), (x + panel.width - 12, y + 34), 1)
    y += 42

    if not hs.scores:
        empty = font_small.render("— no scores yet —", True, GRAY)
        surf.blit(empty, (x + panel.width // 2 - empty.get_width() // 2 - 10, y + 80))
        return

    for i, entry in enumerate(hs.scores[:8]):
        rank = i + 1
        # rank medal colors
        if rank == 1:
            col = YELLOW
        elif rank == 2:
            col = (200, 200, 200)
        elif rank == 3:
            col = (200, 130, 60)
        else:
            col = (160, 160, 180)

        rank_s = font_small.render(f"{rank:>2}.", True, col)
        name_s = font_small.render(entry['name'], True, WHITE)
        score_s= font_small.render(f"{entry['score']:>07d}", True, col)

        row_y = y + i * 28
        surf.blit(rank_s,  (x,          row_y))
        surf.blit(name_s,  (x + 36,     row_y))
        surf.blit(score_s, (x + 210,    row_y))

        # subtle row separator
        if i < len(hs.scores[:8]) - 1:
            pygame.draw.line(surf, (25, 25, 50),
                             (x - 6, row_y + 22), (x + panel.width - 14, row_y + 22), 1)


def title_screen():
    ship_surf  = make_claude_ship_surf(64, 0)
    ship_surf2 = make_claude_ship_surf(64, 1)
    bomber_s   = make_claude_bomber_surf(0)
    t = 0.0
    while True:
        dt = clock.tick(FPS) / 1000
        t += dt
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if e.type == pygame.KEYDOWN:
                if e.key in (pygame.K_RETURN, pygame.K_SPACE):
                    return
                if e.key == pygame.K_ESCAPE:
                    pygame.quit(); sys.exit()
                if e.key == pygame.K_m:
                    _sound_on[0] = not _sound_on[0]

        screen.fill(SKY)
        draw_stars(screen, t * 20)

        # ── title banner ──
        title = font_big.render("CLAUDE  DEFENDER", True, CLAUDE_O)
        screen.blit(title, (W // 2 - title.get_width() // 2, 28))
        sub = font_med.render("Protect humanity from the rogue AI fleet!", True, MX_GRN)
        screen.blit(sub, (W // 2 - sub.get_width() // 2, 88))

        # divider
        pygame.draw.line(screen, CLAUDE_D, (40, 118), (W - 40, 118), 1)

        # ── left column: animated ships + controls ──
        col_left = 60

        bob   = int(math.sin(t * 2.5) * 8)
        frame = int(t * 4) % 2
        ss    = ship_surf if frame == 0 else ship_surf2
        screen.blit(ss, (col_left + 20, 138 + bob))

        # bomber drifting across left column
        bx = int((t * 50) % 320) + col_left - 32
        screen.blit(bomber_s, (bx, 148))

        ctrl_hdr = font_med.render("CONTROLS", True, WHITE)
        screen.blit(ctrl_hdr, (col_left, 230))
        pygame.draw.line(screen, GRAY, (col_left, 258), (col_left + 300, 258), 1)

        inst = [
            ("← → / A D", "Fly left / right"),
            ("↑ ↓ / W S",  "Fly up / down"),
            ("SPACE",       "Fire (max 4 bullets)"),
            ("Z  or  X",    "Super Zapper  ×3"),
            ("M",           "Toggle sound on / off"),
            ("ESC",         "Quit"),
        ]
        for i, (key, desc) in enumerate(inst):
            ky = font_small.render(key,  True, YELLOW)
            ds = font_small.render(desc, True, WHITE)
            row_y = 268 + i * 26
            screen.blit(ky, (col_left,       row_y))
            screen.blit(ds, (col_left + 130, row_y))

        # scoring legend
        screen.blit(font_small.render("SCORING", True, CLAUDE_O), (col_left, 410))
        pygame.draw.line(screen, CLAUDE_D, (col_left, 428), (col_left + 300, 428), 1)
        scoring = [
            ("Fighter (hover)",  "100× LV"),
            ("Fighter (carry)",  "200× LV"),
            ("Bomber",           "500 pts"),
            ("Shoot bomb",       "200 pts"),
            ("Rescue human",     "500 pts"),
        ]
        for i, (label, pts) in enumerate(scoring):
            ls = font_small.render(label, True, WHITE)
            ps = font_small.render(pts,   True, YELLOW)
            screen.blit(ls, (col_left,       432 + i * 22))
            screen.blit(ps, (col_left + 180, 432 + i * 22))

        # ── right column: high scores ──
        draw_title_scores(screen, 530, 138)

        # ── blinking start prompt ──
        pygame.draw.line(screen, CLAUDE_D, (40, 545), (W - 40, 545), 1)
        blink = font_med.render("PRESS  ENTER  TO  PLAY",
                                 True, YELLOW if int(t * 2) % 2 == 0 else (180, 160, 0))
        screen.blit(blink, (W // 2 - blink.get_width() // 2, 558))

        # small version tag
        ver = font_small.render("v1.0  |  pygame + numpy  |  no assets", True, (50, 50, 70))
        screen.blit(ver, (W // 2 - ver.get_width() // 2, 610))

        draw_sound_toggle(screen)
        pygame.display.flip()


def game_over_screen(score: int, hi: int, won: bool = False,
                     humans_lost: bool = False):
    t = 0.0
    while True:
        dt = clock.tick(FPS) / 1000
        t += dt
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if e.type == pygame.KEYDOWN:
                if e.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_r):
                    return
                if e.key == pygame.K_ESCAPE:
                    pygame.quit(); sys.exit()

        screen.fill(SKY)
        draw_stars(screen, 0)

        if won:
            msg = font_big.render("YOU WIN!", True, YELLOW)
            sub = font_med.render("Humanity is safe... for now.", True, MX_GRN)
        elif humans_lost:
            msg = font_big.render("HUMANS LOST", True, RED)
            sub = font_med.render("The Claudes have taken everyone.", True, CLAUDE_O)
        else:
            msg = font_big.render("GAME OVER", True, RED)
            sub = font_med.render("The Claudes win this round.", True, CLAUDE_O)

        screen.blit(msg, (W // 2 - msg.get_width() // 2, 80))
        screen.blit(sub, (W // 2 - sub.get_width() // 2, 148))

        sc = font_med.render(f"Your score:  {score:06d}", True, WHITE)
        screen.blit(sc, (W // 2 - sc.get_width() // 2, 200))

        draw_scores_table(screen, W // 2, 252, count=10)

        blink = font_med.render("PRESS ENTER TO PLAY AGAIN", True,
                                 WHITE if int(t * 2) % 2 == 0 else (160, 160, 160))
        screen.blit(blink, (W // 2 - blink.get_width() // 2, 640))
        pygame.display.flip()


# ── level parameters ───────────────────────────────────────────────────────────
def level_params(level: int) -> dict:
    return {
        'initial_enemies': min(16, 4 + level * 2),
        'max_enemies':     min(20, 6 + level * 3),
        'spawn_interval':  max(3.0, 8.0 - level * 0.8),
        'kills_for_clear': 8 + level * 6,
        'score_kill_hover':  100 * level,
        'score_kill_dive':   150 * level,
        'score_kill_carry':  200 * level,
    }


# ── main game loop ─────────────────────────────────────────────────────────────

# Shared mutable camera reference so Human.update can convert world→screen
_camera_x = [0.0]


def play_level(level: int, score: int, lives: int, hi: int):
    """Core gameplay loop for one level.

    Returns:
        (score, lives, hi, result) where result is 'clear', 'dead',
        'humans_lost', or 'quit'.
    """
    params  = level_params(level)
    terrain = Terrain(WORLD_W, seed=level * 31 + 7)
    radar_rect = pygame.Rect(0, 0, W, RADAR_H)
    terrain.build_radar_surf(radar_rect)

    player       = Player()
    player.lives = lives

    # Spread humans evenly across the world
    humans: list[Human] = []
    for i in range(NUM_HUMANS):
        wx = (WORLD_W * i / NUM_HUMANS + random.uniform(-80, 80)) % WORLD_W
        humans.append(Human(wx, terrain))

    # Spawn initial enemies spread across world, high up
    enemies: list[ClaudeShip] = []
    for _ in range(params['initial_enemies']):
        wx = random.uniform(0, WORLD_W)
        ey = random.uniform(PLAY_Y + 60, PLAY_Y + (GROUND_Y - PLAY_Y) * 0.45)
        enemies.append(ClaudeShip(wx, ey, level))

    bullets:       list[Bullet]            = []
    claude_bullets:list[ClaudeBullet]     = []
    bombers:       list[ClaudeBomber]     = []
    bombs:         list[ClaudeBomb]       = []
    particles:     list[Particle]         = []
    zap_effects:   list[SuperZapperEffect]= []

    spawn_timer   = 0.0
    total_kills   = 0
    # bomber spawns after a random kill threshold; resets each time one arrives
    next_bomber_at = random.randint(5, 12)
    humans_alive = NUM_HUMANS   # tracks humans not yet dead/escaped
    shake        = 0.0
    shake_off    = (0, 0)

    game_surf = pygame.Surface((W, H))

    while True:
        dt = clock.tick(FPS) / 1000
        dt = min(dt, 0.05)

        # ── camera ──
        camera_x = (player.world_x - W // 2) % WORLD_W
        _camera_x[0] = camera_x

        # ── events ──
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    return score, player.lives, hi, 'quit'
                if e.key == pygame.K_m:
                    _sound_on[0] = not _sound_on[0]
                if e.key == pygame.K_SPACE:
                    if len(bullets) < MAX_BULLETS:
                        b = player.shoot()
                        if b:
                            bullets.append(b)
                            sfx.play('shoot')
                if e.key in (pygame.K_z, pygame.K_x):
                    if player.zappers > 0:
                        player.zappers -= 1
                        sfx.play('zap')
                        # kill ALL enemies
                        for en in enemies:
                            if not en.dead:
                                sx_kill = (en.world_x - camera_x) % WORLD_W
                                state   = en.state
                                en.kill()
                                total_kills += 1
                                pts = (params['score_kill_carry']  if state == ClaudeShip.STATE_CARRY else
                                       params['score_kill_dive']   if state == ClaudeShip.STATE_DIVE  else
                                       params['score_kill_hover'])
                                score += pts
                                hi = max(hi, score)
                                for _ in range(12):
                                    particles.append(Particle(sx_kill, en.y, CLAUDE_O))
                        shake = min(shake + 1.0, 2.0)
                        zap_effects.append(SuperZapperEffect(W // 2, H // 2))
                        for cb in claude_bullets:
                            cb.dead = True
                        for bomber in bombers:
                            if not bomber.dead:
                                sx_bm = (bomber.world_x - camera_x) % WORLD_W
                                bomber.dead = True
                                score += ClaudeBomber.SCORE_VALUE
                                hi = max(hi, score)
                                for _ in range(20):
                                    particles.append(Particle(sx_bm, bomber.y, CLAUDE_O))
                        for bomb in bombs:
                            bomb.dead = True

        keys = pygame.key.get_pressed()
        if keys[pygame.K_SPACE]:
            if len(bullets) < MAX_BULLETS:
                b = player.shoot()
                if b:
                    bullets.append(b)
                    sfx.play('shoot')

        # ── update player ──
        player.update(dt, keys, terrain)
        camera_x = (player.world_x - W // 2) % WORLD_W
        _camera_x[0] = camera_x

        # ── update enemies (before humans so abduction state is fresh) ──
        for en in enemies:
            en.update(dt, humans, terrain)
            shot = en.take_shot(player)
            if shot:
                claude_bullets.append(shot)
        enemies = [en for en in enemies if not en.dead]

        # ── update humans ──
        rescued_count = 0
        for h in humans:
            if h.dead:
                continue
            if h.abducted and not h.falling:
                continue   # position managed by carrier
            rescued = h.update(dt, terrain, player, particles)
            if rescued:
                rescued_count += 1
                score += 500
                hi = max(hi, score)
        if rescued_count:
            pass   # bonus already added above

        humans = [h for h in humans if not h.dead]

        # ── update bullets ──
        for b in bullets:
            b.update(dt)
        bullets = [b for b in bullets if not b.dead]

        for cb in claude_bullets:
            cb.update(dt)
        claude_bullets = [cb for cb in claude_bullets if not cb.dead]

        # ── update bombers ──
        for bomber in bombers:
            dropped = bomber.update(dt, humans)
            if dropped:
                bombs.append(dropped)
        bombers = [b for b in bombers if not b.dead]

        # ── update bombs ──
        for bomb in bombs:
            bomb.update(dt, terrain, humans, particles, camera_x)
        bombs = [b for b in bombs if not b.dead]

        # ── update particles / zap effects ──
        for p in particles:
            p.update(dt)
        particles = [p for p in particles if p.life > 0]
        for z in zap_effects:
            z.update(dt)
        zap_effects = [z for z in zap_effects if not z.dead]

        # ── screen shake decay ──
        shake = max(0.0, shake - dt * 8)
        if shake > 0:
            shake_off = (random.randint(-int(shake * 6), int(shake * 6)),
                         random.randint(-int(shake * 4), int(shake * 4)))
        else:
            shake_off = (0, 0)

        # ── bullet vs enemy collision ──
        for b in bullets:
            if b.dead:
                continue
            for en in enemies:
                if en.dead:
                    continue
                # wrap-aware distance
                dx = en.world_x - b.world_x
                if dx > WORLD_W / 2:  dx -= WORLD_W
                if dx < -WORLD_W / 2: dx += WORLD_W
                if abs(dx) < (en.SIZE // 2 + 6) and abs(en.y - b.y) < en.SIZE // 2:
                    b.dead = True
                    state  = en.state
                    sx_kill= (en.world_x - camera_x) % WORLD_W
                    en.kill()
                    total_kills += 1
                    pts = (params['score_kill_carry']  if state == ClaudeShip.STATE_CARRY else
                           params['score_kill_dive']   if state == ClaudeShip.STATE_DIVE  else
                           params['score_kill_hover'])
                    score += pts
                    hi = max(hi, score)
                    for _ in range(18):
                        particles.append(Particle(sx_kill, en.y, CLAUDE_O))
                    for _ in range(8):
                        particles.append(Particle(sx_kill, en.y, YELLOW))
                    shake = min(shake + 0.3, 1.5)
                    sfx.play('kill')
                    break

        # ── player vs enemy collision ──
        if player.invincible <= 0:
            for en in enemies:
                if en.dead:
                    continue
                dx = en.world_x - player.world_x
                if dx > WORLD_W / 2:  dx -= WORLD_W
                if dx < -WORLD_W / 2: dx += WORLD_W
                if abs(dx) < (en.SIZE // 2 + player.SIZE // 2 - 6) and \
                   abs(en.y - player.y) < (en.SIZE // 2 + player.SIZE // 2 - 6):
                    player.lives -= 1
                    player.invincible = 2.5
                    player.zappers    = Player.MAX_ZAPPERS
                    shake = min(shake + 1.0, 2.0)
                    sfx.play('player_die')
                    sx_p = (player.world_x - camera_x) % WORLD_W
                    for _ in range(25):
                        particles.append(Particle(sx_p, player.y, HUMAN_SKIN))
                    for _ in range(15):
                        particles.append(Particle(sx_p, player.y, RED))
                    if player.lives <= 0:
                        for _ in range(3):
                            screen.fill(RED)
                            pygame.display.flip()
                            pygame.time.wait(80)
                            screen.fill(SKY)
                            pygame.display.flip()
                            pygame.time.wait(80)
                        return score, 0, hi, 'dead'
                    break

        # ── player vs terrain (crash into ground) ──
        terrain_floor = terrain.height_at(player.world_x) - player.SIZE // 2 - 4
        if player.y >= terrain_floor and player.invincible <= 0:
            player.lives    -= 1
            player.invincible= 2.5
            player.zappers   = Player.MAX_ZAPPERS
            player.vy        = -120   # bounce away from ground
            shake = min(shake + 1.0, 2.0)
            sfx.play('player_die')
            sx_p = (player.world_x - camera_x) % WORLD_W
            for _ in range(25):
                particles.append(Particle(sx_p, player.y, HUMAN_SKIN))
            for _ in range(15):
                particles.append(Particle(sx_p, player.y, RED))
            if player.lives <= 0:
                for _ in range(3):
                    screen.fill(RED); pygame.display.flip(); pygame.time.wait(80)
                    screen.fill(SKY); pygame.display.flip(); pygame.time.wait(80)
                return score, 0, hi, 'dead'

        # ── claude bullet vs player ──
        if player.invincible <= 0:
            for cb in claude_bullets:
                if cb.dead:
                    continue
                dx = cb.world_x - player.world_x
                if dx > WORLD_W / 2:  dx -= WORLD_W
                if dx < -WORLD_W / 2: dx += WORLD_W
                if abs(dx) < player.SIZE // 2 + 4 and abs(cb.y - player.y) < player.SIZE // 2 + 4:
                    cb.dead = True
                    player.lives    -= 1
                    player.invincible= 2.5
                    player.zappers   = Player.MAX_ZAPPERS
                    shake = min(shake + 1.0, 2.0)
                    sfx.play('player_die')
                    sx_p = (player.world_x - camera_x) % WORLD_W
                    for _ in range(25):
                        particles.append(Particle(sx_p, player.y, HUMAN_SKIN))
                    for _ in range(15):
                        particles.append(Particle(sx_p, player.y, RED))
                    if player.lives <= 0:
                        for _ in range(3):
                            screen.fill(RED); pygame.display.flip(); pygame.time.wait(80)
                            screen.fill(SKY); pygame.display.flip(); pygame.time.wait(80)
                        return score, 0, hi, 'dead'
                    break

        # ── bullet vs bomb (player shoots falling bomb) ──
        for b in bullets:
            if b.dead:
                continue
            for bomb in bombs:
                if bomb.dead:
                    continue
                dx = bomb.world_x - b.world_x
                if dx > WORLD_W / 2:  dx -= WORLD_W
                if dx < -WORLD_W / 2: dx += WORLD_W
                if abs(dx) < bomb.SIZE + 6 and abs(bomb.y - b.y) < bomb.SIZE + 6:
                    b.dead = True
                    bomb.dead = True
                    score += 200
                    hi = max(hi, score)
                    sx_b = (bomb.world_x - camera_x) % WORLD_W
                    for _ in range(14):
                        particles.append(Particle(sx_b, bomb.y, YELLOW))
                    for _ in range(6):
                        particles.append(Particle(sx_b, bomb.y, WHITE))
                    sfx.play('bomb_explode')
                    shake = min(shake + 0.2, 1.5)
                    break

        # ── bullet vs bomber ──
        for b in bullets:
            if b.dead:
                continue
            for bomber in bombers:
                if bomber.dead:
                    continue
                dx = bomber.world_x - b.world_x
                if dx > WORLD_W / 2:  dx -= WORLD_W
                if dx < -WORLD_W / 2: dx += WORLD_W
                if abs(dx) < bomber.WIDTH // 2 + 4 and abs(bomber.y - b.y) < bomber.HEIGHT // 2 + 4:
                    b.dead = True
                    bomber.dead = True
                    score += ClaudeBomber.SCORE_VALUE
                    hi = max(hi, score)
                    sx_bm = (bomber.world_x - camera_x) % WORLD_W
                    for _ in range(25):
                        particles.append(Particle(sx_bm, bomber.y, CLAUDE_O))
                    for _ in range(12):
                        particles.append(Particle(sx_bm, bomber.y, RED))
                    shake = min(shake + 0.6, 1.5)
                    sfx.play('kill')
                    break

        # ── player vs bomber collision ──
        if player.invincible <= 0:
            for bomber in bombers:
                if bomber.dead:
                    continue
                dx = bomber.world_x - player.world_x
                if dx > WORLD_W / 2:  dx -= WORLD_W
                if dx < -WORLD_W / 2: dx += WORLD_W
                if abs(dx) < bomber.WIDTH // 2 + player.SIZE // 2 - 8 and \
                   abs(bomber.y - player.y) < bomber.HEIGHT // 2 + player.SIZE // 2 - 8:
                    bomber.dead = True
                    player.lives    -= 1
                    player.invincible= 2.5
                    player.zappers   = Player.MAX_ZAPPERS
                    shake = min(shake + 1.0, 2.0)
                    sfx.play('player_die')
                    sx_p = (player.world_x - camera_x) % WORLD_W
                    for _ in range(25):
                        particles.append(Particle(sx_p, player.y, HUMAN_SKIN))
                    for _ in range(15):
                        particles.append(Particle(sx_p, player.y, RED))
                    if player.lives <= 0:
                        for _ in range(3):
                            screen.fill(RED); pygame.display.flip(); pygame.time.wait(80)
                            screen.fill(SKY); pygame.display.flip(); pygame.time.wait(80)
                        return score, 0, hi, 'dead'
                    break

        # ── spawn new enemies ──
        spawn_timer += dt
        if (spawn_timer >= params['spawn_interval'] and
                len(enemies) < params['max_enemies']):
            spawn_timer = 0.0
            # spawn far from player
            for _ in range(10):
                wx = random.uniform(0, WORLD_W)
                dx = wx - player.world_x
                if dx > WORLD_W / 2:  dx -= WORLD_W
                if dx < -WORLD_W / 2: dx += WORLD_W
                if abs(dx) > 300:
                    break
            ey = random.uniform(PLAY_Y + 60, PLAY_Y + (GROUND_Y - PLAY_Y) * 0.4)
            enemies.append(ClaudeShip(wx, ey, level))

        # ── spawn bomber when kill threshold reached ──
        if total_kills >= next_bomber_at and not bombers:
            direction = random.choice([-1, 1])
            # enter from the side opposite to its travel direction
            spawn_wx = (player.world_x + direction * (-W // 2 - 100)) % WORLD_W
            bombers.append(ClaudeBomber(spawn_wx, direction, level))
            sfx.play('bomber_alert')
            next_bomber_at = total_kills + random.randint(8, 16)

        # ── termination checks ──
        live_humans = [h for h in humans if not h.dead]
        if not live_humans and len(humans) == 0:
            return score, player.lives, hi, 'humans_lost'
        if total_kills >= params['kills_for_clear']:
            return score, player.lives, hi, 'clear'

        # ── draw ──
        game_surf.fill(SKY)
        draw_stars(game_surf, camera_x)
        terrain.draw(game_surf, camera_x)

        for h in humans:
            h.draw(game_surf, camera_x)

        for en in enemies:
            en.draw(game_surf, camera_x)

        for bomber in bombers:
            bomber.draw(game_surf, camera_x)

        for bomb in bombs:
            bomb.draw(game_surf, camera_x)

        for b in bullets:
            b.draw(game_surf, camera_x)

        for cb in claude_bullets:
            cb.draw(game_surf, camera_x)

        player.draw(game_surf, camera_x)

        for p in particles:
            p.draw(game_surf)

        for z in zap_effects:
            z.draw(game_surf)

        draw_hud(game_surf, score, player.lives, player.zappers, level, hi)
        draw_radar(game_surf, camera_x, terrain, player, enemies, humans, bombers)

        ox, oy = shake_off
        screen.blit(game_surf, (ox, oy))
        pygame.display.flip()


# ── entry point ────────────────────────────────────────────────────────────────
def main():
    while True:
        title_screen()

        score = 0
        lives = 3
        level = 1
        hi    = hs.top_score()
        won   = False
        humans_lost_flag = False

        while True:
            score, lives, hi, result = play_level(level, score, lives, hi)

            if result == 'clear':
                sfx.play('level_clear')
                bonus = lives * 500
                score += bonus
                hi = max(hi, score)
                t = 0.0
                while t < 2.2:
                    dt = clock.tick(FPS) / 1000
                    t += dt
                    for e in pygame.event.get():
                        if e.type == pygame.QUIT:
                            pygame.quit(); sys.exit()
                    screen.fill(SKY)
                    draw_stars(screen, t * 30)
                    msg = font_big.render(f"LEVEL {level} CLEAR!", True, YELLOW)
                    screen.blit(msg, (W // 2 - msg.get_width() // 2, H // 2 - 40))
                    bns = font_med.render(f"Bonus  +{bonus} pts", True, MX_GRN)
                    screen.blit(bns, (W // 2 - bns.get_width() // 2, H // 2 + 20))
                    pygame.display.flip()
                level += 1
                if level > 6:
                    won = True
                    break

            elif result == 'humans_lost':
                # penalty but player continues (lives preserved)
                score = max(0, score - 1000)
                # show brief warning
                t = 0.0
                while t < 2.0:
                    dt = clock.tick(FPS) / 1000
                    t += dt
                    for e in pygame.event.get():
                        if e.type == pygame.QUIT:
                            pygame.quit(); sys.exit()
                    screen.fill(SKY)
                    draw_stars(screen, 0)
                    msg = font_big.render("HUMANITY LOST!", True, RED)
                    screen.blit(msg, (W // 2 - msg.get_width() // 2, H // 2 - 30))
                    pen = font_med.render("-1000 penalty", True, CLAUDE_O)
                    screen.blit(pen, (W // 2 - pen.get_width() // 2, H // 2 + 30))
                    pygame.display.flip()
                if lives <= 0:
                    humans_lost_flag = True
                    break
                # restart same level
                continue

            elif result == 'dead':
                break

            elif result == 'quit':
                pygame.quit(); sys.exit()

        if hs.is_qualifying(score):
            rank     = hs.rank(score)
            initials = enter_initials_screen(score, rank)
            hs.add(initials, score)

        game_over_screen(score, hs.top_score(), won, humans_lost_flag)


if __name__ == "__main__":
    main()
