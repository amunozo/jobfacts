from jobfacts.collect.boards import (
    ashby,
    greenhouse,
    lever,
    personio,
    recruitee,
    smartrecruiters,
    teamtailor,
    workable,
)
from jobfacts.collect.posting import parse_time
from jobfacts.text import html_to_text


def test_html_to_text_keeps_paragraphs():
    text = html_to_text("<p>One &amp; two</p><p>Three</p><ul><li>a</li><li>b</li></ul>")
    assert text == "One & two\n\nThree\n\n- a\n- b"


def test_html_to_text_unescapes_escaped_html():
    assert html_to_text("&lt;p&gt;Hello&lt;/p&gt;&lt;p&gt;World&lt;/p&gt;") == "Hello\n\nWorld"


def test_parse_time_variants():
    assert parse_time("2026-10-28 18:48:11 UTC").year == 2026
    assert parse_time(1760000000000).year == 2025
    assert parse_time("garbage") is None
    assert parse_time(None) is None


def test_greenhouse():
    data = {
        "jobs": [
            {
                "id": 11,
                "title": "ML Engineer",
                "absolute_url": "https://example.test/gh/11",
                "location": {"name": "Zurich"},
                "first_published": "2026-10-01T10:00:00-04:00",
                "content": "&lt;p&gt;Build models.&lt;/p&gt;&lt;p&gt;Fun team.&lt;/p&gt;",
            }
        ]
    }
    [p] = greenhouse.parse(data)
    assert (p.source_job_id, p.title, p.location_raw) == ("11", "ML Engineer", "Zurich")
    assert p.description_text == "Build models.\n\nFun team."
    assert p.posted_at is not None


def test_lever():
    data = [
        {
            "id": "abc-1",
            "text": "Data Scientist",
            "hostedUrl": "https://example.test/lever/abc-1",
            "createdAt": 1760000000000,
            "categories": {"location": "Geneva", "allLocations": ["Geneva", "Remote"]},
            "descriptionPlain": "About the job.",
            "lists": [{"text": "Requirements", "content": "<li>Python</li>"}],
            "additionalPlain": "Apply now.",
        }
    ]
    [p] = lever.parse(data)
    assert p.source_job_id == "abc-1"
    assert p.location_raw == "Geneva / Remote"
    assert "About the job." in p.description_text and "Python" in p.description_text
    assert p.description_text.endswith("Apply now.")


def test_ashby_skips_unlisted():
    data = {
        "jobs": [
            {
                "id": "u1",
                "title": "Research Engineer",
                "jobUrl": "https://example.test/ashby/u1",
                "location": "Lausanne",
                "secondaryLocations": [{"location": "Remote"}],
                "descriptionHtml": "<p>Do research.</p>",
                "publishedAt": "2026-10-02T08:00:00.000+00:00",
            },
            {"id": "u2", "title": "Hidden", "isListed": False},
        ]
    }
    [p] = ashby.parse(data)
    assert p.source_job_id == "u1"
    assert p.location_raw == "Lausanne / Remote"
    assert p.description_text == "Do research."


def test_recruitee():
    data = {
        "offers": [
            {
                "id": 5,
                "title": "Analyst",
                "careers_url": "https://example.test/rec/5",
                "location": "Basel",
                "locations": [{"name": "Switzerland, Basel"}],
                "description": "<p>Analyse.</p>",
                "requirements": "<p>SQL.</p>",
                "published_at": "2026-10-28 18:48:11 UTC",
            }
        ]
    }
    [p] = recruitee.parse(data)
    assert p.source_job_id == "5"
    assert p.description_text == "Analyse.\n\nSQL."
    assert p.posted_at is not None


def test_workable():
    data = {
        "jobs": [
            {
                "shortcode": "ABC123",
                "title": "AI Engineer",
                "url": "https://example.test/wk/ABC123",
                "city": "Bern",
                "country": "Switzerland",
                "description": "<p>Ship AI.</p>",
                "published_on": "2026-10-03",
            }
        ]
    }
    [p] = workable.parse(data, "acme")
    assert p.source_job_id == "ABC123"
    assert p.location_raw == "Bern, Switzerland"
    assert p.description_text == "Ship AI."


def test_smartrecruiters():
    item = {
        "id": "744",
        "name": "Data Engineer",
        "location": {"city": "Zug", "country": "ch", "remote": True},
        "releasedDate": "2026-10-04T09:00:00.000Z",
    }
    p = smartrecruiters.parse_list_item(item, "acme")
    assert p.url == "https://jobs.smartrecruiters.com/acme/744"
    assert p.location_raw == "Zug, CH (remote)"
    detail = {
        "jobAd": {"sections": {"jobDescription": {"title": "Job", "text": "<p>Pipelines.</p>"}}}
    }
    assert smartrecruiters.parse_detail_text(detail) == "Job\nPipelines."


PERSONIO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
  <position>
    <id>101</id>
    <office>Zürich</office>
    <additionalOffices><office>Bern</office></additionalOffices>
    <name>Data Scientist (m/w/d)</name>
    <jobDescriptions>
      <jobDescription><name>Ihre Aufgaben</name>
        <value><![CDATA[<p>Modelle bauen.</p>]]></value></jobDescription>
      <jobDescription><name>Ihr Profil</name>
        <value><![CDATA[<ul><li>Python</li></ul>]]></value></jobDescription>
    </jobDescriptions>
    <createdAt>2026-10-05T09:30:00+00:00</createdAt>
  </position>
</workzag-jobs>"""


def test_personio():
    [p] = personio.parse(PERSONIO_XML, "acme")
    assert p.source_job_id == "101"
    assert p.url == "https://acme.jobs.personio.de/job/101"
    assert p.title == "Data Scientist (m/w/d)"
    assert p.location_raw == "Zürich / Bern"
    assert p.description_text == "Ihre Aufgaben\nModelle bauen.\n\nIhr Profil\n- Python"
    assert p.posted_at is not None


def test_personio_rejects_other_documents():
    import pytest

    with pytest.raises(ValueError):
        personio.parse("<html></html>", "acme")


TEAMTAILOR_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:tt="https://teamtailor.com/locations">
  <channel>
    <title>Acme</title>
    <item>
      <title>ML Engineer</title>
      <description>&lt;p&gt;Entraîner des modèles.&lt;/p&gt;</description>
      <pubDate>Thu, 06 Aug 2026 17:27:26 +0200</pubDate>
      <link>https://acme.teamtailor.com/jobs/1-ml-engineer</link>
      <remoteStatus>hybrid</remoteStatus>
      <guid>guid-1</guid>
      <tt:locations>
        <tt:location><tt:name>Lausanne</tt:name><tt:city>Lausanne</tt:city>
          <tt:country>Switzerland</tt:country></tt:location>
      </tt:locations>
    </item>
  </channel>
</rss>"""


def test_teamtailor():
    [p] = teamtailor.parse(TEAMTAILOR_RSS)
    assert p.source_job_id == "guid-1"
    assert p.title == "ML Engineer"
    assert p.location_raw == "Lausanne, Switzerland / (hybrid)"
    assert p.description_text == "Entraîner des modèles."
    assert p.posted_at is not None
