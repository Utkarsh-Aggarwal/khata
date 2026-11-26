"""WCAG 2.1 contrast check for the page's text colours. Run: python scripts/contrast.py

Opacity is blended into the foreground before measuring. AA needs 4.5:1 for normal text.
"""
import sys


def hex_rgb(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def blend(fg, bg, a):
    return [round(f * a + b * (1 - a)) for f, b in zip(fg, bg)]


def lum(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(fg, bg, alpha=1.0):
    f, b = hex_rgb(fg), hex_rgb(bg)
    f = blend(f, b, alpha)
    hi, lo = sorted((lum(f), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


PAPER, INK, BLOOD, MOSS, NIGHT, GHOST, CARD = "#f3e7c9", "#2a1e12", "#8c1d1d", "#2f5d3a", "#15121a", "#e9e6f5", "#fffdf5"
PAIRS = [
    ("body text on paper", INK, PAPER, 1),
    ("headings (blood) on paper", BLOOD, PAPER, 1),
    (".note on paper (opacity .8)", INK, PAPER, .8),
    ("header small text on night (opacity .85)", GHOST, NIGHT, .85),
    ("ghost name on night", GHOST, NIGHT, 1),
    (".tag on night", "#f6c7c7", NIGHT, 1),
    ("button text (paper on ink)", PAPER, INK, 1),
    ("alive status (moss) on card", MOSS, CARD, 1),
    ("dormant status (blood) on card", BLOOD, CARD, 1),
    ("dormant card text (opacity .85)", INK, CARD, .85),
    ("cancelled row (opacity .75)", INK, PAPER, .75),
    ("séance quote (opacity .85)", INK, PAPER, .85),
    # redesign
    (".note / labels on paper", "#4a3c2c", PAPER, 1),
    (".note / labels on card", "#4a3c2c", CARD, 1),
    ("hero and steps text on night2", GHOST, "#221c2b", 1),
    ("footer text on night", "#cfc9e0", NIGHT, 1),
    ("chat header subtitle on night2", "#cfc9e0", "#221c2b", 1),
    ("my chat bubble text", INK, "#dcefd2", 1),
    ("status pill: white on moss", "#ffffff", MOSS, 1),
    ("status pill / udhaar badge: white on blood", "#ffffff", BLOOD, 1),
    ("stat number (blood) on card", BLOOD, CARD, 1),
    ("primary button: night on ghost", NIGHT, GHOST, 1),
    ("séance quote on card", "#3d3022", CARD, 1),
]

fail = 0
for label, fg, bg, a in PAIRS:
    r = ratio(fg, bg, a)
    ok = r >= 4.5
    fail += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {r:5.2f}:1  {label}")
sys.exit(1 if fail else 0)
