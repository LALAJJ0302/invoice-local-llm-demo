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


def test_separation_is_shadow_not_border():
    """The three hairlines that survive are the three that separate parts of one card.

    Pinned because the count is the whole argument of `fe-theme-v2-spec.md` §9, and because a
    single careless `border:1px solid` added later would undo it without failing anything else.
    """
    stylesheet = APP[APP.index("st.markdown(\"\"\"\n<style>"):APP.index("</style>")]
    # Three inside a card, plus the keycap outline on the search field's shortcut hint. A
    # keycap is a border around part of one control rather than an element against the page,
    # which is the side of §9's rule that keeps its hairline. The design draws it that way too.
    assert stylesheet.count("1px solid") == 4, (
        "a hairline was added or removed; §9 allows the three inside a card and the keycap")
    for token in ("--lift:", "--lift-soft:", "--fill-subtle:"):
        assert token in stylesheet, f"{token} is gone, so nothing replaces the borders"


def test_artboard_separates_the_same_way():
    """v3 draws the app, so it may not keep a border the app dropped.

    Counted over the body only: the aside and the header are Claude Design's and are held byte
    for byte, so their rules are not this test's business.
    """
    v3 = (ROOT / "approval-screen-design-v3.html").read_text()
    body = v3[v3.index("</header>"):]
    # 4 section header bands + 2 disabled buttons + 3 secondary buttons + 1 primary button
    # + 1 tab underline + 2 card-internal rules = 13. Streamlit draws the button borders
    # itself because config.toml sets showWidgetBorder, so they are not ours to remove.
    assert body.count("1px solid") == 13, (
        "the artboard and the app disagree about where a hairline belongs")
