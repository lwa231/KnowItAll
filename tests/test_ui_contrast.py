"""The design tokens must keep meeting WCAG AA. Parses ui/css/tokens.css, so changing a colour there is checked."""
import re
from pathlib import Path

import pytest

TOKENS = Path(__file__).resolve().parent.parent / "ui" / "css" / "tokens.css"
SURFACES = ["chrome", "bg-base", "bg-raise", "secondary", "secondary-hi"]
TEXT = ["text-bright", "text-main", "text-muted", "text-dim", "accent-text", "green", "warn", "red"]


def theme_blocks():
    css = TOKENS.read_text(encoding="utf-8")
    blocks = {}
    for name, selector in (("dark", r':root, \[data-theme="dark"\]'), ("light", r'\[data-theme="light"\]')):
        match = re.search(selector + r"\s*\{(.*?)\n\}", css, re.S)
        assert match, f"no {name} block in tokens.css"
        blocks[name] = dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})\s*;", match.group(1)))
    return blocks


def luminance(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


BLOCKS = theme_blocks()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("token", TEXT)
def test_text_tokens_meet_aa_on_every_surface(theme, surface, token):
    colours = BLOCKS[theme]
    value = ratio(colours[token], colours[surface])
    assert value >= 4.5, f"{theme}: --{token} on --{surface} is {value:.2f}:1 (needs 4.5)"


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("fill", ["accent", "accent-hover"])
def test_text_on_the_accent_fill_is_readable(theme, fill):
    colours = BLOCKS[theme]
    assert ratio(colours["on-accent"], colours[fill]) >= 4.5, f"{theme}: --on-accent on --{fill}"


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("surface", SURFACES)
def test_focus_ring_is_visible_on_every_surface(theme, surface):
    colours = BLOCKS[theme]
    assert ratio(colours["focus"], colours[surface]) >= 3.0, f"{theme}: focus ring on --{surface}"


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_dimmest_text_is_still_dimmer_than_the_muted_text(theme):
    """Hierarchy check: dim must not be brighter (dark) / darker (light) than muted, or the levels invert."""
    colours = BLOCKS[theme]
    a, b = luminance(colours["text-dim"]), luminance(colours["text-muted"])
    assert (a <= b) if theme == "dark" else (a >= b)


def test_no_font_size_token_is_below_12px():
    css = TOKENS.read_text(encoding="utf-8")
    for name, value in re.findall(r"--(fs-[a-z]+):\s*([\d.]+)rem", css):
        assert float(value) * 16 >= 12, f"--{name} is {float(value) * 16}px"


def test_every_theme_defines_the_same_tokens():
    assert set(BLOCKS["dark"]) == set(BLOCKS["light"])
