import pytest

from jobfacts.locations import find_candidates


def keys(text, **kw):
    return [c.key for c in find_candidates(text, **kw)]


def test_offsets_and_accents():
    text = "Office in Zurich, Switzerland"
    cs = find_candidates(text)
    assert [c.key for c in cs] == ["city:Zürich", "country:CH"]
    assert all(text[c.start : c.end] == c.matched for c in cs)
    assert cs[0].canton == "ZH"


@pytest.mark.parametrize(
    "text,key",
    [
        ("Genève", "city:Genève"),
        ("Geneva", "city:Genève"),
        ("Genf", "city:Genève"),
        ("Bâle", "city:Basel"),
        ("Basilea", "city:Basel"),
        ("Lucerne", "city:Luzern"),
        ("Neuenburg", "city:Neuchâtel"),
        ("Bienne", "city:Biel/Bienne"),
        ("Sitten", "city:Sion"),
        ("Saint-Gall", "city:St. Gallen"),
        ("St Gallen", "city:St. Gallen"),
        ("Lausanne", "city:Lausanne"),
        ("Rotkreuz", "city:Risch-Rotkreuz"),
        ("Muri bei Bern", "city:Muri bei Bern"),
        ("Kanton Aargau", "canton:AG"),
        ("Ticino", "canton:TI"),
    ],
)
def test_names(text, key):
    assert keys(text) == [key]


def test_fribourg_variants_and_canton_of_city():
    assert keys("Freiburg, Schweiz") == ["city:Fribourg", "country:CH"]
    assert find_candidates("Fribourg")[0].canton == "FR"


@pytest.mark.parametrize(
    "text",
    [
        "Baden-Württemberg",
        "Baden-Baden",
        "Freiburg im Breisgau",
        "Freiburg i. Br., Germany",
        "Standort: Baden-Württemberg",
        "Office in New Bern",
        "Basel III requirements",
        "Basel 3 compliance",
        "Wir fahren mit dem Zug zur Arbeit.",
        "Der Zug hat Verspätung.",
        "Im Zug lesen.",
        "Sie sollten Baden gehen.",
        "Zeit zum Thun.",
        "Berner Oberland ist schön.",
        "Zurichstrasse",
    ],
)
def test_false_positives(text):
    assert keys(text) == []


def test_ambiguous_with_cue():
    assert keys("Standort: Zug") == ["city:Zug"]
    assert keys("Our office in Zug is growing") == ["city:Zug"]
    assert keys("Wir sind in Baden (AG) ansässig") == ["city:Baden"]
    assert keys("Zug, Switzerland") == ["city:Zug", "country:CH"]


def test_ambiguous_in_location_field():
    assert keys("Zug", location_field=True) == ["city:Zug"]
    assert keys("Baden", location_field=True) == ["city:Baden"]
    assert keys("Baden-Württemberg, Germany", location_field=True) == []


def test_codes_only_in_location_field():
    assert keys("Foo AG hires in CH") == []
    assert keys("Zürich, ZH, CH", location_field=True) == [
        "city:Zürich",
        "canton:ZH",
        "country:CH",
    ]


def test_country_words():
    assert keys("Schweiz Suisse Svizzera Switzerland") == ["country:CH"] * 4


def test_non_swiss():
    assert keys("London, United Kingdom", location_field=True) == []


def test_codes_alone_are_not_swiss():
    assert keys("Paris, FR", location_field=True) == []
    assert keys("SG", location_field=True) == []
    assert keys("Remote, CH", location_field=True) == ["country:CH"]


def test_lowercase_free_text_ignored():
    assert keys("the leuk cells") == []


def test_freiburg_germany_in_parentheses():
    assert keys("Freiburg (Germany) / Zurich", location_field=True) == ["city:Zürich"]


def test_swiss_adjective_and_bare_freiburg_need_a_cue():
    keys = [c.key for c in find_candidates("We work with Swiss Re and Swiss banks.")]
    assert "country:CH" not in keys
    keys = [c.key for c in find_candidates("Freiburg teams joined us.")]
    assert "city:Fribourg" not in keys
    keys = [c.key for c in find_candidates("Our office in Freiburg, Switzerland is new.")]
    assert "city:Fribourg" in keys
