"""Tests for social link-preview metadata (JOE-25).

When the dashboard URL is pasted into Discord, iMessage/SMS, Slack, WhatsApp,
Twitter/X, etc., those clients scrape the page for Open Graph / Twitter Card
tags and a preview image to render a rich card. Both surfaces that serve the
site must carry that metadata:

  * the Flask app   (templates/base.html, on every page)
  * the Pages site  (docs/index.html)

and a real 1200×630 PNG must exist at the URL each one points at. These tests
pin all of that down so the previews can't silently regress.
"""

import os
import re
import struct

import pytest

import app as flask_app

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)

# Where the committed preview image lives on each surface.
APP_IMG = os.path.join(ROOT, "static", "og", "preview.png")
PAGES_IMG = os.path.join(ROOT, "docs", "og-preview.png")
PAGES_URL = "https://joeborrello.github.io/world-cup-2026-dashboard/"


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _png_size(path):
    """(width, height) read straight from a PNG's IHDR — no Pillow needed."""
    with open(path, "rb") as fh:
        header = fh.read(24)
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return struct.unpack(">II", header[16:24])


def _meta(html, *, prop=None, name=None):
    """Return the content="" of a <meta property=…>/<meta name=…> tag, or None."""
    attr, val = ("property", prop) if prop else ("name", name)
    m = re.search(
        r'<meta\s+%s=["\']%s["\']\s+content=["\']([^"\']*)["\']' % (attr, re.escape(val)),
        html, re.IGNORECASE)
    return m.group(1) if m else None


# ── the preview image itself ─────────────────────────────────────────────────

@pytest.mark.parametrize("path", [APP_IMG, PAGES_IMG])
def test_preview_image_exists_and_is_1200x630(path):
    assert os.path.exists(path), f"missing preview image: {path}"
    assert _png_size(path) == (1200, 630), "scrapers expect a 1200×630 card"


def test_both_surfaces_ship_the_same_image():
    # one render, copied to both places — so the card looks identical everywhere
    with open(APP_IMG, "rb") as a, open(PAGES_IMG, "rb") as b:
        assert a.read() == b.read()


# ── Flask app (every page, via base.html) ────────────────────────────────────

@pytest.fixture
def client():
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as c:
        yield c


def test_app_page_has_open_graph_and_twitter_tags(client):
    html = client.get("/").get_data(as_text=True)
    assert _meta(html, prop="og:type") == "website"
    assert _meta(html, prop="og:site_name")
    assert "World Cup 2026" in (_meta(html, prop="og:title") or "")
    assert len(_meta(html, prop="og:description") or "") > 40
    assert _meta(html, name="twitter:card") == "summary_large_image"
    assert _meta(html, prop="og:image:width") == "1200"
    assert _meta(html, prop="og:image:height") == "630"


def test_app_og_image_is_absolute_and_points_at_the_png(client):
    html = client.get("/").get_data(as_text=True)
    for img in (_meta(html, prop="og:image"), _meta(html, name="twitter:image")):
        assert img, "missing preview-image meta tag"
        assert img.startswith("http://") or img.startswith("https://"), \
            "scrapers require an absolute og:image URL"
        # cache-busting ?v= is allowed; the path must be the static preview png
        assert "/static/og/preview.png" in img


def test_app_og_url_is_present_and_absolute(client):
    html = client.get("/").get_data(as_text=True)
    url = _meta(html, prop="og:url")
    assert url and url.startswith("http")


def test_app_description_meta_matches_og_description(client):
    html = client.get("/").get_data(as_text=True)
    assert _meta(html, name="description") == _meta(html, prop="og:description")


def test_tags_present_on_a_second_page_too(client):
    # base.html drives every page, so the bracket page must carry them as well
    html = client.get("/bracket").get_data(as_text=True)
    assert _meta(html, prop="og:image")
    assert _meta(html, name="twitter:card") == "summary_large_image"


# ── GitHub Pages landing site (docs/index.html) ──────────────────────────────

def test_docs_has_open_graph_and_twitter_tags():
    html = _read("docs", "index.html")
    assert _meta(html, prop="og:type") == "website"
    assert _meta(html, prop="og:title")
    assert len(_meta(html, prop="og:description") or "") > 40
    assert _meta(html, name="twitter:card") == "summary_large_image"
    assert _meta(html, prop="og:image:width") == "1200"
    assert _meta(html, prop="og:image:height") == "630"


def test_docs_og_image_and_url_are_absolute_pages_urls():
    html = _read("docs", "index.html")
    assert _meta(html, prop="og:url") == PAGES_URL
    expected_img = PAGES_URL + "og-preview.png"
    assert _meta(html, prop="og:image") == expected_img
    assert _meta(html, name="twitter:image") == expected_img
