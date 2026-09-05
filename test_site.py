"""
Tests for sage-site static content.
Validates structural and content requirements; these checks do not establish legal compliance.
"""
import os

ROOT = os.path.dirname(__file__)

def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


# --- File existence ---

def test_index_exists():
    assert os.path.isfile(os.path.join(ROOT, "index.html"))

def test_privacy_exists():
    assert os.path.isfile(os.path.join(ROOT, "privacy.html"))

def test_terms_exists():
    assert os.path.isfile(os.path.join(ROOT, "terms.html"))

def test_style_exists():
    assert os.path.isfile(os.path.join(ROOT, "style.css"))


# --- Privacy policy compliance ---

def test_privacy_names_spark_ai():
    content = read("privacy.html")
    assert "Spark AI Pty Ltd" in content

def test_privacy_has_contact_email():
    content = read("privacy.html")
    assert "ridingoneggshells@gmail.com" in content

def test_privacy_mentions_privacy_act():
    content = read("privacy.html")
    assert "Privacy Act" in content

def test_privacy_covers_discord_user_ids():
    content = read("privacy.html")
    assert "Discord User ID" in content or "user id" in content.lower()

def test_privacy_covers_message_content():
    content = read("privacy.html")
    assert "message content" in content.lower() or "Message content" in content

def test_privacy_covers_third_parties():
    content = read("privacy.html")
    assert "OpenAI" in content
    assert "Anthropic" in content

def test_privacy_covers_retention():
    content = read("privacy.html")
    assert "retention" in content.lower() or "retain" in content.lower()

def test_privacy_covers_user_rights():
    content = read("privacy.html")
    assert "access" in content.lower()
    assert "deletion" in content.lower() or "delete" in content.lower()


# --- Terms compliance ---

def test_terms_names_spark_ai():
    content = read("terms.html")
    assert "Spark AI Pty Ltd" in content

def test_terms_no_medical_advice():
    content = read("terms.html")
    assert "medical" in content.lower()

def test_terms_australian_law():
    content = read("terms.html")
    assert "Australia" in content

def test_terms_has_contact_email():
    content = read("terms.html")
    assert "ridingoneggshells@gmail.com" in content


# --- Navigation: all pages link to each other ---

PAGES = ["index.html", "board.html", "privacy.html", "terms.html"]

def _check_nav_links(filename):
    content = read(filename)
    for page in PAGES:
        assert page in content, f"{filename} missing link to {page}"

def test_index_links_to_all():
    _check_nav_links("index.html")

def test_privacy_links_to_all():
    _check_nav_links("privacy.html")

def test_terms_links_to_all():
    _check_nav_links("terms.html")


# --- Responsive CSS ---

def test_css_has_viewport_meta():
    for page in PAGES:
        content = read(page)
        assert 'name="viewport"' in content, f"{page} missing viewport meta"

def test_css_has_media_query():
    css = read("style.css")
    assert "@media" in css

def test_css_mobile_breakpoint():
    css = read("style.css")
    # Should have a max-width breakpoint for mobile
    assert "max-width" in css
