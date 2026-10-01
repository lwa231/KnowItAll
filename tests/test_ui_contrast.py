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


# ---- Phase 6: brand red, slider, status colour, progress gradient ----
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_slider_ring_is_visible_against_the_track_and_the_panel(theme):
    colours = BLOCKS[theme]
    assert ratio(colours["slider-ring"], colours["secondary-hi"]) >= 3.0, f"{theme}: ring on the track"
    assert ratio(colours["slider-ring"], colours["bg-raise"]) >= 3.0, f"{theme}: ring on the panel"


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_active_rail_icon_reads_on_its_highlight(theme):
    colours = BLOCKS[theme]
    assert ratio(colours["accent-text"], colours["secondary"]) >= 4.5


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_scanning_colour_reads_on_the_header(theme):
    colours = BLOCKS[theme]
    assert ratio(colours["status-active"], colours["chrome"]) >= 4.5, f"{theme}: --status-active on --chrome"


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_progress_gradient_colours_exist(theme):
    for token in ("progress-start", "progress-mid", "progress-end"):
        assert token in BLOCKS[theme], f"{theme} has no --{token}"


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_brand_red_is_the_one_in_the_brief(theme):
    assert BLOCKS[theme]["accent"].lower() == "#bf1704"


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_error_colour_is_not_the_brand_red(theme):
    """Red is the brand and red also means error: the error colour keeps a different hue (icon and words carry it too)."""
    import colorsys

    def hue(value):
        r, g, b = (int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))
        return colorsys.rgb_to_hsv(r, g, b)[0] * 360

    gap = abs(hue(BLOCKS[theme]["red"]) - hue(BLOCKS[theme]["accent-text"]))
    assert min(gap, 360 - gap) >= 15, f"{theme}: error and brand red are only {gap:.0f} degrees apart"


def test_no_purple_or_blue_is_left_in_the_ui():
    """Every hue of the old indigo accent is gone from tokens, styles and scripts."""
    ui = TOKENS.parent.parent
    old = ("35365e", "414273", "9899c8", "2a2b4d", "eceef3")
    for path in [*(ui / "css").glob("*.css"), *(ui / "js").rglob("*.js"), ui / "index.html"]:
        text = path.read_text(encoding="utf-8").lower()
        for value in old:
            assert value not in text, f"{path.name} still has the old accent #{value}"
