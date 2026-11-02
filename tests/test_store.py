import pytest

from jobfacts.collect import store
from jobfacts.collect.posting import Posting
from jobfacts.collect.run import collect

pytestmark = pytest.mark.db


def make(job_id, title="Title", text="Text"):
    return Posting(job_id, f"https://example.test/{job_id}", title, "Zurich", text)


@pytest.fixture
def employer(conn):
    return conn.execute(
        "INSERT INTO employers (name, ats, board_id) VALUES ('Acme', 'lever', 'acme') RETURNING id"
    ).fetchone()[0]


def read(conn, employer, postings):
    run = store.start_run(conn)
    return store.record_success(conn, run, employer, postings)


def state(conn, job_id):
    return conn.execute(
        "SELECT closed_at IS NOT NULL FROM postings WHERE source_job_id = %s", (job_id,)
    ).fetchone()[0]


def test_new_postings_are_inserted(conn, employer):
    c = read(conn, employer, [make("a"), make("b")])
    assert (c.new, c.updated, c.closed) == (2, 0, 0)
    c = read(conn, employer, [make("a"), make("b")])
    assert (c.new, c.updated, c.closed) == (0, 0, 0)


def test_failed_read_does_not_close(conn, employer):
    read(conn, employer, [make("a")])
    store.record_failure(conn, store.start_run(conn), employer, "boom")
    assert state(conn, "a") is False
    assert conn.execute("SELECT ok, error FROM board_reads WHERE NOT ok").fetchone() == (
        False,
        "boom",
    )


def test_missing_after_successful_read_closes_then_reopens(conn, employer):
    read(conn, employer, [make("a"), make("b")])
    c = read(conn, employer, [make("a")])
    assert c.closed == 1 and state(conn, "b") is True and state(conn, "a") is False
    c = read(conn, employer, [make("a"), make("b")])
    assert c.updated == 1 and c.closed == 0 and state(conn, "b") is False


def test_changed_content_updates_hash(conn, employer):
    read(conn, employer, [make("a")])
    old = conn.execute("SELECT content_hash FROM postings").fetchone()[0]
    c = read(conn, employer, [make("a", text="Edited")])
    assert c.updated == 1
    assert conn.execute("SELECT content_hash FROM postings").fetchone()[0] != old


def test_collect_end_to_end_with_fake_boards(conn, tmp_path):
    path = tmp_path / "e.toml"
    path.write_text(
        '[[employer]]\nname="Good"\nats="fake"\nboard_id="good"\n'
        '[[employer]]\nname="Bad"\nats="fake"\nboard_id="bad"\n'
    )
    calls = {"bad_fails": False}

    def fake(client, board_id, extra):
        if board_id == "bad" and calls["bad_fails"]:
            raise RuntimeError("down")
        return [make(f"{board_id}-1")]

    s = collect(conn, fetchers={"fake": fake}, employers_file=path)
    assert (s.boards_ok, s.boards_failed, s.counts.new) == (2, 0, 2)
    calls["bad_fails"] = True
    s = collect(conn, fetchers={"fake": fake}, employers_file=path)
    assert (s.boards_ok, s.boards_failed, s.counts.closed) == (1, 1, 0)
    assert state(conn, "bad-1") is False


def test_empty_response_does_not_close_open_board(conn, tmp_path):
    path = tmp_path / "e.toml"
    path.write_text('[[employer]]\nname="Flaky"\nats="fake"\nboard_id="flaky"\n')
    answers = [[make("flaky-1")], []]

    def fake(client, board_id, extra):
        return answers.pop(0)

    collect(conn, fetchers={"fake": fake}, employers_file=path)
    s = collect(conn, fetchers={"fake": fake}, employers_file=path)
    assert (s.boards_ok, s.boards_failed, s.counts.closed) == (0, 1, 0)
    assert state(conn, "flaky-1") is False
