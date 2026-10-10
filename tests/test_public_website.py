"""Public website, legal pages and crawl-control contract tests."""

from xml.etree import ElementTree


def test_public_homepage_has_seo_metadata(client):
    response = client.get("/public")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '<meta name="description"' in body
    assert '<meta name="robots" content="index,follow">' in body
    assert 'rel="canonical"' in body
    assert 'href="/privacy"' in body


def test_public_domain_root_serves_public_homepage(client):
    response = client.get("/", base_url="https://iveras.com")
    assert response.status_code == 200
    assert "Van eerste aanwijzing naar controleerbaar onderzoeksrapport." in response.get_data(
        as_text=True
    )


def test_public_legal_pages_are_available(client):
    for path, marker in (
        ("/privacy", "Privacyverklaring"),
        ("/voorwaarden", "Algemene voorwaarden"),
        ("/cookies", "Cookiebeleid"),
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert marker in response.get_data(as_text=True)


def test_public_contact_page_explains_customer_routes(client):
    response = client.get("/contact")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Eenmalig OSINT-onderzoek" in body
    assert "Eigen klantomgeving" in body
    assert "Een andere vraag" in body
    assert "92717144" in body


def test_public_legal_pages_show_confirmed_business_identity(client):
    for path in ("/privacy", "/voorwaarden", "/cookies"):
        body = client.get(path).get_data(as_text=True)
        assert "Iveras" in body
        assert "92717144" in body
        assert "Charlotte de Bourbonstraat 67" in body


def test_public_crawl_controls_exclude_private_surfaces(client):
    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    body = robots.get_data(as_text=True)
    assert "Disallow: /cms/" in body
    assert "Disallow: /portal/" in body
    assert "Sitemap:" in body

    sitemap = client.get("/sitemap.xml")
    assert sitemap.status_code == 200
    xml = sitemap.get_data(as_text=True)
    locations = {
        element.text
        for element in ElementTree.fromstring(xml).iter()
        if element.tag.endswith("}loc")
    }
    assert "https://iveras.com/" in locations
    assert "https://iveras.com/contact" in locations
    assert all("/cms/" not in location for location in locations)
    assert all("/portal/" not in location for location in locations)
