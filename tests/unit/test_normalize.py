from datetime import UTC

import pytest

from app.sam.normalize import extract_pop_state, format_opp, normalize_set_aside, parse_deadline


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-19", "2026-10-20T03:59:59+00:00"),
        ("2026-01-19", "2026-01-20T04:59:59+00:00"),
        ("2026-10-19T14:00:00", "2026-10-19T18:00:00+00:00"),
        ("2026-01-19T14:00:00", "2026-01-19T19:00:00+00:00"),
        ("2026-10-19T14:00:00Z", "2026-10-19T14:00:00+00:00"),
        ("2026-10-19T14:00:00-05:00", "2026-10-19T19:00:00+00:00"),
        ("2026-03-08", "2026-03-09T03:59:59+00:00"),
        ("2026-11-01", "2026-11-02T04:59:59+00:00"),
        ("2026-11-01T01:30:00", "2026-11-01T06:30:00+00:00"),
        (None, None),
        ("", None),
        ("invalid", None),
        ("2026-02-30", None),
    ],
)
def test_deadline_formats(raw, expected):
    result = parse_deadline(raw)
    assert (result.isoformat() if result else None) == expected
    if result:
        assert result.tzinfo is UTC


@pytest.mark.parametrize(
    ("raw", "category"),
    [
        (None, "none"),
        ("NONE", "none"),
        ("SBA", "small_business"),
        ("8A", "eight_a"),
        ("HZC", "hubzone"),
        ("SDVOSBC", "sdvosb"),
        ("WOSB", "wosb"),
        ("EDWOSB", "edwosb"),
        ("unknown", "other"),
    ],
)
def test_set_asides(raw, category):
    assert normalize_set_aside(raw)[1] == category


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"state": {"code": "VA", "name": "Virginia"}}, "VA"),
        ({"state": "MD"}, "MD"),
        ("Washington, DC", "DC"),
        ("Los Angeles, CA, 90001", "CA"),
        (None, None),
        ({"state": {"name": "Unknown"}}, None),
    ],
)
def test_state(raw, expected):
    assert extract_pop_state(raw) == expected


def test_normalizes_legacy_strings_and_nulls(sample):
    sample.update(
        resourceLinks="https://example.com/a.pdf | https://example.com/b.pdf",
        officeAddress={"city": "Richmond", "state": "VA", "zipcode": "12345"},
        placeOfPerformance={"state": {"code": "MD"}},
        api_key="secret",
    )
    result = format_opp(sample)
    assert result.additional_info_link is None
    assert result.description_url.startswith("https://api.sam.gov")
    assert result.pop_state == "MD"
    assert len(result.resource_links) == 2
    assert "api_key" not in result.raw_payload
    assert result.synced_at.tzinfo is UTC


def test_missing_identity_or_posted_date_fails(sample):
    sample["noticeId"] = ""
    with pytest.raises(ValueError):
        format_opp(sample)
    sample["postedDate"] = None
    with pytest.raises(ValueError):
        format_opp(sample)
