"""The artboard has to keep describing the app.

`approval-screen-design-v3.html` is called a true picture of the build in
`fe-theme-v2-spec.md`, and the report leans on that claim. Nothing enforced it, and the section
header geometry drifted within an hour of the claim being written: `app.py` was adjusted after
the artboard was generated and the generator was not.

This pins the values that actually drifted rather than attempting a general diff. A generated
artboard and a Streamlit stylesheet do not have a shared representation to compare, so a broad
check would either be vacuous or would fail on every legitimate change.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text()
BUILDER = (ROOT / "scripts" / "build_design_mockups.py").read_text()


def app_section_head() -> str:
    """The `[class*="st-key-sechead-"]` declaration block, whitespace collapsed."""
    start = APP.index('[class*="st-key-sechead-"] {')
    return " ".join(APP[start:APP.index("}", start)].split())


def builder_section_head() -> str:
    start = BUILDER.index('frame = ("margin:20px')
    return " ".join(BUILDER[start:BUILDER.index(")\n", start)].split())


def test_section_head_padding_matches():
    assert "padding:8px 14px" in app_section_head()
    assert "padding:8px 14px" in builder_section_head()


def test_section_head_radius_matches():
    assert "border-radius:6px" in app_section_head()
    assert "border-radius:6px" in builder_section_head()


def test_identity_rule_width_matches():
    """Both draw the section identity as a 3px left rule."""
    assert "border-left:3px solid" in app_section_head()
    assert "border-left:3px solid" in builder_section_head()


def test_builder_token_map_covers_every_changed_token():
    """Every value the app's `:root` moved away from has a mapping, so v2 and v3 differ by
    colour alone rather than by an accidental half-translation."""
    root = APP[APP.index(":root {"):APP.index("}", APP.index(":root {"))]
    new_values = {v.upper() for v in re.findall(r"#[0-9A-Fa-f]{6}", root)}
    mapped = {v.upper() for v in re.findall(r'"(#[0-9A-Fa-f]{6})":', BUILDER)}
    targets = {v.upper() for v in re.findall(r':\s*"(#[0-9A-Fa-f]{6})"', BUILDER)}
    # Anything the map produces must be a value the app actually holds, or the artboard is
    # painting a colour the build does not have. Two are allowed by name, and both belong to
    # chrome the app has never rendered: the page behind the 1440 artboard, and the slash in
    # the breadcrumb. There is no token for either because there is nothing to tokenise.
    chrome_only = {"#DDE3EF", "#C4CAD8"}
    stray = targets - new_values - chrome_only
    assert not stray, f"artboard paints colours the app does not hold: {sorted(stray)}"
    assert mapped, "the token map is empty"
