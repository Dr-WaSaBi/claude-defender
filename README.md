# Claude Defender

A Defender-style side-scrolling arcade shooter built with Python and pygame.

Protect the humans from the rogue Claude AI fleet! Enemy Claude ships swoop down to abduct humans from the mountain terrain below. Shoot them down, catch falling humans, and unleash your Super Zapper to wipe the screen when things get desperate.

![Claude Defender gameplay](screenshot.png)

## Gameplay

- Fly left and right across a large scrolling world (5× the screen width)
- Claude AI ships hover, then dive to grab humans from the ground
- Shoot a carrying Claude ship — the human will fall; fly into them to rescue
- Use your **Super Zapper** to destroy every Claude ship on screen at once
- A radar mini-map at the top shows the full world: white = you, orange = Claudes, green = humans
- 6 levels of escalating difficulty; lose all humans and face a score penalty

## Controls

| Key | Action |
|---|---|
| `← →` or `A D` | Fly left / right |
| `↑ ↓` or `W S` | Fly up / down |
| `SPACE` | Fire (max 4 bullets on screen) |
| `Z` or `X` | Super Zapper — destroys all Claude ships (3 per life) |
| `ESC` | Quit |

## Scoring

| Event | Points |
|---|---|
| Kill hovering Claude | 100 × level |
| Kill diving Claude | 150 × level |
| Kill carrying Claude | 200 × level |
| Rescue a falling human | 500 |
| Level clear bonus | lives × 500 |

## Requirements

- Python 3.8+
- pygame 2.x
- numpy

```bash
pip install pygame numpy
```

## Running

```bash
python3 claude_defender.py
```

## Features

- Single-file, no external assets — all graphics and audio generated at runtime
- Procedurally generated scrolling terrain with seamless world wrap
- Radar mini-map strip showing full world state
- numpy-synthesized sound effects
- Persistent high score leaderboard (top 10, saved to `highscores.json`)
- Screen shake on impacts
- Particle explosion effects
- 6 levels with increasing enemy speed, aggression, and spawn rate
