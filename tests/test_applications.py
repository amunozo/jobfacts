"""Import of application job descriptions (synthetic files)."""

import pytest

from jobfacts.applications import import_applications, split_link


def test_split_link():
    assert split_link("Link: https://x.example/1\n\n# Title\nBody") == (
        "https://x.example/1",
        "# Title\nBody",
    )
    assert split_link("Link: not provided\nBody") == ("", "Body")
    assert split_link("https://www.linkedin.com/jobs/view/<PASTE_LINK>\nBody")[0] == ""
    assert split_link("Acme\nSenior Role\nBody") == ("", "Acme\nSenior Role\nBody")


@pytest.mark.db
def test_import_is_idempotent_and_never_closed(conn, tmp_path):
    d = tmp_path / "Acme" / "ML Engineer"
    d.mkdir(parents=True)
    (d / "job_description.md").write_text("Link: https://x.example/1\n\nWe hire.")
    first = import_applications(conn, tmp_path)
    again = import_applications(conn, tmp_path)
    assert (first.new, again.new, again.unchanged) == (1, 0, 1)
    row = conn.execute(
        "SELECT origin, source_job_id, title, location_raw, description_text, closed_at "
        "FROM postings"
    ).fetchone()
    assert row == ("application", "Acme/ML Engineer", "ML Engineer", "", "We hire.", None)
    (d / "job_description.md").write_text("Link: x\n\nChanged.")
    assert import_applications(conn, tmp_path).updated == 1
