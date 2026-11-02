import pytest

from jobfacts.sentences import split


def texts(s):
    return [x.text for x in split(s)]


def test_offsets_invariant_and_ids():
    src = "  Intro line.  \n\n- First bullet\n• Second one. Another one!\r\nLast"
    out = split(src)
    assert [x.id for x in out] == list(range(1, len(out) + 1))
    for x in out:
        assert src[x.start : x.end] == x.text
        assert x.text == x.text.strip()
    assert texts(src) == ["Intro line.", "First bullet", "Second one.", "Another one!", "Last"]


def test_basic_split():
    assert texts("We build things. You will help us. Join now!") == [
        "We build things.",
        "You will help us.",
        "Join now!",
    ]


@pytest.mark.parametrize(
    "s",
    [
        "Experience with tools, e.g. Docker and Kubernetes is welcome.",
        "Kenntnisse in z.B. Python bzw. Rust sind gut.",
        "Skills such as SQL, etc. Are useful.",
        "Dr. Meier leads the team.",
        "Prof. Dr. Muster is our advisor.",
        "Voir Nr. 5 dans la liste.",
        "Des outils, p. ex. Docker, sont utiles.",
        "Environ 50 personnes, env. Dix équipes.",
        "Salary of 2.5 million and version 3.11.2 is used.",
        "Visit https://example.com/jobs.html or mail a.b@example.com today.",
        "Start am 1. Januar in Bern.",
        "Wir bieten u.a. Weiterbildung, d.h. Kurse.",
    ],
)
def test_no_split_inside(s):
    assert texts(s) == [s]


def test_split_after_real_end_before_digit_and_german():
    assert texts("Das ist es. Wir freuen uns. 5 Jahre Erfahrung.") == [
        "Das ist es.",
        "Wir freuen uns.",
        "5 Jahre Erfahrung.",
    ]


def test_french():
    assert texts("Nous cherchons un ingénieur. Vous travaillerez à Genève.") == [
        "Nous cherchons un ingénieur.",
        "Vous travaillerez à Genève.",
    ]


def test_long_piece_split_at_semicolon():
    part = "a" * 200
    s = f"{part}; {part}; {part}; {part}"
    out = texts(s)
    assert len(out) > 1 and all(x.endswith(";") or x == part for x in out)
    assert all(s[x.start : x.end] == x.text for x in split(s))


def test_empty():
    assert split("") == []
    assert split("  \n \n") == []
