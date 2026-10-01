"""Static checks on the UI files: every import resolves, every id the scripts look up exists, nothing comes
from the internet, and every referenced file is there. (There is no JavaScript engine in the test environment,
so behaviour is covered by the in-browser checks; this catches the typos that would break the page on load.)"""
import re
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "ui"
JS_FILES = sorted((UI / "js").rglob("*.js"))
HTML = (UI / "index.html").read_text(encoding="utf-8")


def exports_of(path):
    text = path.read_text(encoding="utf-8")
    names = set(re.findall(r"^export\s+(?:async\s+)?(?:function|const|let|class)\s+([A-Za-z0-9_$]+)", text, re.M))
    for group in re.findall(r"^export\s*\{([^}]*)\}", text, re.M):
        names |= {part.split(" as ")[-1].strip() for part in group.split(",") if part.strip()}
    return names


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: str(p.relative_to(UI)))
def test_imports_resolve_to_real_exports(path):
    text = path.read_text(encoding="utf-8")
    for names, source in re.findall(r"import\s*\{([^}]*)\}\s*from\s*'(\.[^']+)'", text):
        target = (path.parent / source).resolve()
        assert target.exists(), f"{path.name} imports {source}, which does not exist"
        available = exports_of(target)
        for name in (n.split(" as ")[0].strip() for n in names.split(",") if n.strip()):
            assert name in available, f"{path.name} imports {name} from {source}, which does not export it"
    for source in re.findall(r"import\s*\*\s*as\s+\w+\s+from\s*'(\.[^']+)'", text):
        assert (path.parent / source).resolve().exists(), f"{path.name}: missing {source}"


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: str(p.relative_to(UI)))
def test_ids_that_scripts_look_up_exist_in_the_page(path):
    text = path.read_text(encoding="utf-8")
    for id_ in re.findall(r"""\$\('#([A-Za-z0-9_-]+)'\)""", text) + re.findall(r"getElementById\('([^']+)'\)", text):
        assert f'id="{id_}"' in HTML, f"{path.name} looks up #{id_}, which index.html does not define"


def test_every_view_has_a_section_and_a_nav_button():
    sections = set(re.findall(r'<section class="view[^"]*" data-view="([a-z]+)"', HTML))
    buttons = set(re.findall(r'<button class="rail-item"[^>]*data-view="([a-z]+)"', HTML))
    assert sections == buttons and {"scraper", "history", "output", "settings", "system"} <= sections


def test_files_the_page_and_stylesheets_reference_exist():
    for reference in re.findall(r'(?:href|src)="(/ui/[^"]+)"', HTML):
        assert (UI / reference[len("/ui/"):]).is_file(), f"index.html references {reference}"
    for sheet in (UI / "css").glob("*.css"):
        for reference in re.findall(r"url\('?([^')]+)'?\)", sheet.read_text(encoding="utf-8")):
            assert (sheet.parent / reference).resolve().is_file(), f"{sheet.name} references {reference}"


def test_nothing_is_loaded_from_the_internet():
    for path in [UI / "index.html", *(UI / "css").glob("*.css"), *JS_FILES]:
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"https?://[^\s\"')>]+", text):
            line = text[:match.start()].splitlines()[-1] if text[:match.start()].splitlines() else ""
            assert "//" in line[-3:] or line.strip().startswith(("/*", "//", "*")) or "SIL" in line, \
                f"{path.name} refers to {match.group(0)}"
    assert "cdn.tailwindcss.com" not in HTML and "fonts.googleapis.com" not in HTML


def test_page_placeholders_and_basics():
    assert "__KNOWITALL_TOKEN__" in HTML and "__KNOWITALL_THEME__" in HTML
    assert '<html lang="en"' in HTML and 'name="viewport"' in HTML
    assert 'type="module" src="/ui/js/main.js"' in HTML


def test_every_static_button_declares_its_type():
    for tag in re.findall(r"<button\b[^>]*>", HTML):
        assert "type=" in tag, f"a button without type (would submit forms): {tag[:80]}"


def test_every_form_control_in_the_page_has_a_label():
    for control in re.findall(r"<input\b[^>]*>", HTML):
        id_ = re.search(r'id="([^"]+)"', control)
        assert id_, f"an input without an id cannot be labelled: {control[:80]}"
        labelled = re.search(rf'<label[^>]*for="{re.escape(id_.group(1))}"', HTML) or "aria-label" in control
        assert labelled, f"#{id_.group(1)} has no label"


def test_switches_and_radio_groups_are_named():
    for tag in re.findall(r'<button[^>]*role="switch"[^>]*>', HTML):
        assert "aria-labelledby" in tag or "aria-label" in tag, tag[:80]
    for tag in re.findall(r'<div[^>]*role="radiogroup"[^>]*>', HTML):
        assert "aria-label" in tag or "aria-labelledby" in tag, tag[:80]


def test_the_stylesheets_use_tokens_not_raw_colours():
    """Colours live in tokens.css; component styles refer to them, so a restyle is a one-file change."""
    for sheet in (UI / "css").glob("*.css"):
        if sheet.name in ("tokens.css", "fonts.css"):
            continue
        for line_no, line in enumerate(sheet.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("/*")[0]
            assert not re.search(r"#[0-9a-fA-F]{3,8}\b", code), f"{sheet.name}:{line_no} has a raw colour: {code.strip()}"


def test_no_font_size_below_12px_in_the_stylesheets():
    for sheet in (UI / "css").glob("*.css"):
        for match in re.finditer(r"font-size:\s*(\d+(?:\.\d+)?)px", sheet.read_text(encoding="utf-8")):
            assert float(match.group(1)) >= 12, f"{sheet.name}: font-size {match.group(0)}"


def test_the_rail_has_a_named_button_and_a_tooltip_for_every_item():
    rail = re.search(r'<aside class="rail".*?</aside>', HTML, re.S).group(0)
    buttons = re.findall(r'<button class="rail-item"[^>]*>.*?</button>', rail, re.S)
    assert len(buttons) == 7                                    # scraper, queue, history, output, settings, system, quit
    for button in buttons:
        assert re.search(r'aria-label="[^"]+"', button), button[:80]
        assert 'class="tip" aria-hidden="true"' in button, button[:80]
    assert 'id="quitBtn"' in rail and 'aria-controls="queuePanel"' in rail


def test_the_old_sidebar_is_gone():
    assert 'class="sidebar"' not in HTML and "logo-slot" not in HTML and "Job Listing Scraper" not in HTML
    css = "".join(p.read_text(encoding="utf-8") for p in (UI / "css").glob("*.css"))
    for name in (".sidebar", ".brand", ".nav-item", ".side-foot", ".side-section", ".logo-slot"):
        assert name not in css, f"{name} is still styled"


def test_settings_panels_and_sliders_have_their_ids():
    for id_ in ("settings-scanning", "settings-appearance", "settings-output", "setLimit", "setMaxJobs", "setDepth"):
        assert f'id="{id_}"' in HTML
    for gone in ("hdrMaxJobs", "hdrDepth", "freshSwitch"):
        assert gone not in HTML


def test_logo_sources_are_copied_into_the_ui():
    assert (UI / "img" / "logo-kia.svg").is_file() and (UI / "img" / "icon-glyph.svg").is_file()
