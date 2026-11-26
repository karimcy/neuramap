"""Guard the design-system foundation (cinematic redesign; see docs/goal/redesign-audit.md).

The system: static/tokens.css is the ONE token source (near-black surfaces, hairline
elevation, a single amber accent, semantic green/red reserved for pass/fail, self-hosted
fonts, spacing/motion scales); app.css consumes tokens only. These tests assert the token
set stays defined, the reusable animated-background component and the a11y baselines
(skip-link, focusable main, reduced-motion) survive, and that motion easings are named
once as tokens rather than pasted as literals across rules.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from gridq.app import create_app

_STATIC = Path(__file__).resolve().parents[1] / "src" / "gridq" / "app" / "static"
_TOKENS = (_STATIC / "tokens.css").read_text(encoding="utf-8")
_APP = (_STATIC / "app.css").read_text(encoding="utf-8")
_CSS = _TOKENS + "\n" + _APP  # the full cascade, in load order

client = TestClient(create_app())

# Token groups every screen relies on; each must be defined on :root in tokens.css.
_REQUIRED_TOKENS = (
    # spacing scale
    "--sp-1:",
    "--sp-4:",
    "--sp-8:",
    # motion
    "--dur-fast:",
    "--dur-base:",
    "--dur-slow:",
    "--ease-standard:",
    "--ease-spring:",
    "--ease-out:",
    # dark cinematic palette
    "--ink-900:",
    "--on-dark:",
    "--on-dark-muted:",
    "--glow-accent:",
    "--glow-cool:",
    # the cinematic system's own spine
    "--bg:",
    "--surface-1:",
    "--line:",
    "--accent:",
    "--ok:",
    "--err:",
    "--font-sans:",
    "--font-mono:",
    "--eyebrow-tracking:",
    "--radius:",
    "--maxw:",
    "--reveal-shift:",
)


def test_design_tokens_defined() -> None:
    missing = [t for t in _REQUIRED_TOKENS if t not in _TOKENS]
    assert not missing, f"design tokens missing from tokens.css: {missing}"


def test_run_progress_is_real_not_a_random_ramp() -> None:
    # The run/loading bar must be driven by real backend phase facts, not a client-side fake:
    # the old Math.random() ramp jittered backward and looked crashed. Guard that it is gone and
    # that the track is a proper progressbar (numeric, monotonic, accessible).
    js = (_STATIC / "run_progress.js").read_text(encoding="utf-8")
    assert "Math.random" not in js, "run_progress.js must not fake progress with Math.random()"
    # Assert the actual code forms, not bare words: a stray comment mentioning "progressbar" or
    # "aria-valuenow" would satisfy a substring grep while the real setAttribute calls were deleted.
    assert 'setAttribute("role", "progressbar")' in js, (
        "the run track must be a real progressbar (role set in code, not merely mentioned)"
    )
    assert 'setAttribute("aria-valuenow"' in js, (
        "the progressbar must set aria-valuenow in code as the bar advances"
    )
    # The monotonic clamp is the headline guarantee (the bar can never move backward again) and the
    # poll loop's ~12-minute timeout are both load-bearing: guard the expressions, not just intent.
    assert "Math.max(prevPct" in js, (
        "the bar must be clamped monotonic (Math.max(prevPct, ...)) so it can never regress"
    )
    assert "> 900" in js, "the poll loop must keep its ~12-minute (900-poll) timeout"


def test_app_css_defines_no_tokens_of_its_own() -> None:
    # app.css may alias legacy names onto tokens (var(...) values only); any raw colour
    # literal on a :root custom property would fork the palette from the token source.
    import re

    root_block = _APP.split(":root", 1)[1].split("}", 1)[0]
    raw = [
        line.strip()
        for line in root_block.splitlines()
        if re.search(r"--[\w-]+\s*:\s*(#|rgba?\()", line)
    ]
    assert not raw, f"app.css :root must alias tokens, not define colours: {raw}"


def test_animated_background_component_defined() -> None:
    # The reusable component exists and is built on the dark/motion tokens (not hard-coded values).
    assert ".gq-ambient" in _CSS and ".gq-ambient__orb" in _CSS
    assert "var(--glow-accent)" in _CSS and "var(--glow-cool)" in _CSS


def test_one_off_glow_removed() -> None:
    # The landing's old one-off background must not linger now the shared component exists.
    assert "vhero__glow" not in _CSS


def test_reduced_motion_baseline_present() -> None:
    assert "prefers-reduced-motion: reduce" in _CSS


def test_motion_uses_tokens_not_repeated_literals() -> None:
    # The spring easing is named once (the --ease-spring token in tokens.css) - not pasted
    # as a literal across rules, and the old pre-redesign curve is gone entirely.
    assert _CSS.count("cubic-bezier(0.16, 1, 0.3, 1)") == 1
    assert "cubic-bezier(0.22, 0.61, 0.36, 1)" not in _CSS


def test_fonts_self_hosted_no_cdn() -> None:
    # Self-hosted variable fonts only: @font-face sources point at the vendored files and
    # no stylesheet reaches for a font CDN (offline / no-CDN posture).
    assert _TOKENS.count("@font-face") == 2
    assert "vendor/fonts/inter-latin-wght-normal.woff2" in _TOKENS
    assert "vendor/fonts/jetbrains-mono-latin-wght-normal.woff2" in _TOKENS
    for banned in ("fonts.googleapis.com", "fonts.gstatic.com", "http://", "https://"):
        assert banned not in _CSS, f"external fetch in CSS: {banned}"
    for f in (
        "vendor/fonts/inter-latin-wght-normal.woff2",
        "vendor/fonts/jetbrains-mono-latin-wght-normal.woff2",
    ):
        assert (_STATIC / f).is_file(), f"vendored font missing: {f}"


def test_numbers_render_monospace() -> None:
    # The monospace-data rule: the figure/metric components and numeric table cells sit in
    # the mono stack, so every number reads as data everywhere.
    for anchor in (
        ".metric .figure { font-family: var(--font-mono)",
        ".stat__num { font-family: var(--font-mono)",
        "table.data td.num, table.data th.num { text-align: right; font-family: var(--font-mono)",
    ):
        assert anchor in _APP, f"mono data rule missing: {anchor}"


def test_landing_wires_skip_link_main_and_ambient() -> None:
    html = client.get("/").text
    assert 'class="skip-link"' in html and 'href="#main"' in html
    assert 'id="main"' in html
    assert 'class="gq-ambient"' in html  # reference page uses the shared component
    assert 'rel="icon"' in html and "data:image/svg+xml" in html  # self-contained favicon, no 404
    # The token stylesheet loads before app.css, and the motion utility is wired.
    assert html.index("/static/tokens.css") < html.index("/static/app.css")
    assert "/static/motion.js" in html


def test_ui_copy_carries_no_em_dashes() -> None:
    """WS4 app-row invariant: no em dash anywhere the UI renders from.

    Sweeps every Jinja template and every non-vendored static asset (vendored third-party
    files - plotly, leaflet, three, fonts - are excluded: not our copy). Python
    docstrings/comments keep the repo prose convention; the RENDERED python strings (the
    demo label, form prefill notes, chart captions) are covered by the template/static
    sweep plus the explicit constants below.
    """
    app_dir = Path(__file__).resolve().parents[1] / "src" / "gridq" / "app"
    offenders: list[str] = []
    for f in sorted(app_dir.glob("templates/*.html")):
        if "—" in f.read_text(encoding="utf-8"):
            offenders.append(str(f.relative_to(app_dir)))
    for f in sorted(app_dir.glob("static/*.js")) + sorted(app_dir.glob("static/*.css")):
        if f.name == "plotly.min.js":  # vendored
            continue
        if "—" in f.read_text(encoding="utf-8"):
            offenders.append(str(f.relative_to(app_dir)))
    assert not offenders, f"em dashes in UI sources: {offenders}"

    from gridq.app.app import _EXAMPLE_NOTE
    from gridq.app.demo import DEMO_LABEL

    assert "—" not in DEMO_LABEL
    assert "—" not in _EXAMPLE_NOTE
