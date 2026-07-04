"""Browser regression for the JOE-42 revision: "the left hand side of the
projection tree runs off the page and I can't scroll over to view it."

Root cause: .wi-map is the horizontal scroll container, but .wi-tree was a
plain flex row with justify-content:center. A centered flex row wider than its
scrollport overflows BOTH edges, and a scroll container can only reach
right-side overflow — so the left half of a wide scenario map was clipped with
no way to scroll to it. The fix sizes .wi-tree to max-content (min-width:100%
keeps narrow maps centered), which puts all overflow on the scrollable right.

This renders the real stylesheet against representative wide-tree markup (the
same structure whatif.js emits), so it needs no Flask server and no LLM call.
Like the other browser suites it is opt-in via WC_E2E=1.
"""

import os
import pathlib

import pytest

if not os.environ.get("WC_E2E"):
    pytest.skip("browser e2e disabled (set WC_E2E=1 to run)",
                allow_module_level=True)

pytest.importorskip("playwright.sync_api",
                    reason="playwright not installed — skipping browser e2e")
from playwright.sync_api import sync_playwright  # noqa: E402

STYLE = pathlib.Path(__file__).resolve().parent.parent / "static" / "css" / "style.css"

NODE = ('<div class="wi-node"><div class="wi-node-head">Branch title</div>'
        '<div class="wi-node-sum">Some summary text for the node body.</div></div>')


def _page_html(branches: int) -> str:
    kids = "".join(f"<li>{NODE}</li>" for _ in range(branches))
    return f"""<!doctype html><html><head><style>
    :root {{ --line:#ccc; --card:#fff; --bg:#f5f5f5; --green:#2a7; --green-dark:#185;
             --shadow:0 1px 2px rgba(0,0,0,.1); }}
    {STYLE.read_text()}
    </style></head><body>
    <div class="wi-map" id="map"><ul class="wi-tree"><li>
      <div class="wi-node wi-root"><div class="wi-node-head">Root question?</div></div>
      <ul>{kids}</ul>
    </li></ul></div>
    </body></html>"""


@pytest.fixture(scope="module")
def browser():
    try:
        with sync_playwright() as p:
            try:
                b = p.chromium.launch()
            except Exception as exc:                       # browser binary missing
                pytest.skip(f"chromium unavailable: {exc}")
            yield b
            b.close()
    except Exception as exc:                               # playwright host deps
        pytest.skip(f"playwright unavailable: {exc}")


def test_wide_tree_is_fully_reachable_by_scrolling(browser):
    page = browser.new_page(viewport={"width": 900, "height": 800})
    # 8 branches of ~230px each → ~1850px of tree in a 900px viewport
    page.set_content(_page_html(branches=8))
    m = page.evaluate("""() => {
      const map = document.getElementById('map');
      const first = document.querySelector('.wi-tree ul li:first-child .wi-node');
      const last = document.querySelector('.wi-tree ul li:last-child .wi-node');
      map.scrollLeft = 0;
      const leftEdgeAtStart = first.getBoundingClientRect().left;
      map.scrollLeft = map.scrollWidth;
      const rightEdgeAtEnd = last.getBoundingClientRect().right;
      return { scrollWidth: map.scrollWidth, clientWidth: map.clientWidth,
               leftEdgeAtStart, rightEdgeAtEnd };
    }""")
    page.close()

    assert m["scrollWidth"] > m["clientWidth"], "fixture tree should overflow the viewport"
    # the reported bug: the leftmost branch sat at a negative x with nothing to
    # scroll to (scrollLeft can't go below 0)
    assert m["leftEdgeAtStart"] >= 0, \
        f"leftmost node clipped off-screen at x={m['leftEdgeAtStart']}"
    assert m["rightEdgeAtEnd"] <= 900, \
        "rightmost node should be reachable by scrolling to the end"


def test_narrow_tree_stays_centered(browser):
    """min-width:100% must keep the centered layout for maps that fit."""
    page = browser.new_page(viewport={"width": 900, "height": 800})
    page.set_content(_page_html(branches=2))
    m = page.evaluate("""() => {
      const map = document.getElementById('map');
      const root = document.querySelector('.wi-node.wi-root');
      const r = root.getBoundingClientRect();
      return { scrollable: map.scrollWidth > map.clientWidth,
               center: r.left + r.width / 2, mapCenter: map.clientWidth / 2 };
    }""")
    page.close()

    assert not m["scrollable"], "a 2-branch tree should fit without scrolling"
    assert abs(m["center"] - m["mapCenter"]) < 10, \
        "a tree that fits should stay horizontally centered"
