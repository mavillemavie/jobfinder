import httpx
import respx

from jobfinder.contacts.providers.phone import (
    fetch_contact_page_phone,
    normalize_phone,
    phone_from_html,
    phones_in_text,
)
from jobfinder.discovery.fetch import HttpClient


def test_normalize_phone() -> None:
    assert normalize_phone("(514) 555-0100", "CA") == "+1 514-555-0100"
    assert normalize_phone("+1 514.555.0100 ext 12", "CA") == "+1 514-555-0100"
    assert normalize_phone("1-800-555-0199", "CA") == "+1 800-555-0199"
    assert normalize_phone("020 7946 0958", "GB") == "+44 20 7946 0958"
    assert normalize_phone("+44 (0)161 496 0000", "GB") == "+44 161 496 0000"
    assert normalize_phone("12345", "CA") is None
    assert normalize_phone("514-555-0100", "GB") is None


def test_phones_in_text_dedupes() -> None:
    text = "Call 514-555-0100 or (514) 555-0100, fax 514-555-0199. Ref 2026-09-04."
    assert phones_in_text(text, "CA") == ["+1 514-555-0100", "+1 514-555-0199"]


def test_phone_from_html_prefers_tel_links() -> None:
    html = '<p>Head office 514-555-0111</p><a href="tel:+15145550100">Call us</a>'
    assert phone_from_html(html, "CA") == "+1 514-555-0100"
    assert phone_from_html("<p>no numbers here</p>", "CA") is None


@respx.mock
def test_fetch_contact_page_phone_tries_paths() -> None:
    respx.get("https://acmelogistics.example/contact").mock(return_value=httpx.Response(404))
    respx.get("https://acmelogistics.example/contact-us").mock(
        return_value=httpx.Response(200, text='<a href="tel:514-555-0100">514-555-0100</a>')
    )
    found = fetch_contact_page_phone(HttpClient(retries=0), "https://acmelogistics.example", "CA")
    assert found == ("+1 514-555-0100", "https://acmelogistics.example/contact-us")
