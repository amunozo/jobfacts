import pytest

from jobfacts import locations
from jobfacts.locations import (
    CANTONS,
    OUTSIDE,
    canton_hints,
    find_places,
    mentions_switzerland,
)


def place_keys(text, cantons, **kw):
    return [m.key for m in find_places(text, cantons, **kw)]


def test_cantons_complete():
    assert len(CANTONS) == 26
    assert locations.office_canton_values() == [*CANTONS, OUTSIDE]
    zh = CANTONS["ZH"]
    assert (zh.de, zh.fr, zh.it, zh.en) == ("Zürich", "Zurich", "Zurigo", "Zurich")
    assert all(c.places for c in CANTONS.values())
    assert locations.canton_name("GE", "de") == "Genf"


def test_register_and_aliases_load():
    # every hand-made alias resolves to a register place (checked at load time)
    keys = locations.all_place_keys()
    assert len(keys) > 4000
    assert "place:261:Zürich" in keys
    place = locations.place_by_key("place:261:Zürich")
    assert place.canton == "ZH" and "8001" in place.postcodes


def test_offsets_matched_text_and_fields():
    text = "Office in Zurich, Switzerland"
    (m,) = find_places(text, ["ZH"])
    assert text[m.start : m.end] == m.matched == "Zurich"
    assert (m.key, m.name, m.canton, m.bfs_nr) == ("place:261:Zürich", "Zürich", "ZH", 261)
    assert m.commune == "Zürich"


def test_places_restricted_to_chosen_cantons():
    assert place_keys("Offices in Zürich and Bern", ["ZH"]) == ["place:261:Zürich"]
    assert place_keys("Offices in Zürich and Bern", ["BE"]) == ["place:351:Bern"]
    assert place_keys("Offices in Zürich and Bern", []) == []
    assert place_keys("Offices in Zürich and Bern", [OUTSIDE]) == []
    assert len(place_keys("Offices in Zürich and Bern", ["ZH", "BE", OUTSIDE])) == 2


@pytest.mark.parametrize(
    "text,canton",
    [
        ("Netstal", "GL"),
        ("Ibach", "SZ"),
        ("Grancia", "TI"),
        ("Neuchatel", "NE"),
        ("Neuchâtel", "NE"),
        ("Biel/Bienne", "BE"),
        ("Bienne", "BE"),
        ("Avry-sur-Matran", "FR"),
        ("S. Antonino", "TI"),
        ("Rotkreuz", "ZG"),
    ],
)
def test_swisscom_prefilter_misses_now_match(text, canton):
    assert canton_hints(text, location_field=True) == [canton]
    assert len(find_places(text, [canton], location_field=True)) == 1
    assert mentions_switzerland(text, location_field=True)


@pytest.mark.parametrize(
    "text,canton,name",
    [
        ("Geneva", "GE", "Genève"),
        ("Genf", "GE", "Genève"),
        ("Ginevra", "GE", "Genève"),
        ("Zurigo", "ZH", "Zürich"),
        ("Zuerich", "ZH", "Zürich"),
        ("Bâle", "BS", "Basel"),
        ("Basilea", "BS", "Basel"),
        ("Lucerne", "LU", "Luzern"),
        ("Berne", "BE", "Bern"),
        ("Neuenburg", "NE", "Neuchâtel"),
        ("Freiburg", "FR", "Fribourg"),
        ("Sitten", "VS", "Sion"),
        ("Saint-Gall", "SG", "St. Gallen"),
        ("St Gallen", "SG", "St. Gallen"),
        ("Lausanne", "VD", "Lausanne"),
        ("Muri bei Bern", "BE", "Muri bei Bern"),
        ("Pfäffikon ZH", "ZH", "Pfäffikon"),
        ("Pfäffikon SZ", "SZ", "Pfäffikon"),
    ],
)
def test_names_and_aliases(text, canton, name):
    (m,) = find_places(text, [canton], location_field=True)
    assert m.name == name


def test_same_name_in_two_cantons_resolved_by_canton():
    both = find_places("Pfäffikon", ["ZH", "SZ"], location_field=True)
    assert {m.canton for m in both} == {"ZH", "SZ"}
    assert len({m.key for m in both}) == 2
    assert [m.canton for m in find_places("Pfäffikon", ["SZ"], location_field=True)] == ["SZ"]
    assert set(canton_hints("Pfäffikon", location_field=True)) == {"ZH", "SZ"}


def test_longest_name_wins():
    assert place_keys("Muri bei Bern", ["BE", "AG"]) != place_keys("Bern", ["BE"])
    assert [m.matched for m in find_places("Muri bei Bern", ["BE"])] == ["Muri bei Bern"]


def test_canton_names_are_hints_not_places():
    assert canton_hints("Kanton Aargau") == ["AG"]
    assert find_places("Kanton Aargau", ["AG"]) == []
    assert canton_hints("Ticino") == ["TI"]
    assert canton_hints("in Graubünden") == ["GR"]


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
        "the leuk cells",
        "We work with Swiss Re and Swiss banks.",
        "Freiburg teams joined us.",
        "Konstanz, Basel, Germany",
    ],
)
def test_false_positives(text):
    assert canton_hints(text) == []


def test_ambiguous_with_cue():
    assert canton_hints("Standort: Zug") == ["ZG"]
    assert canton_hints("Our office in Zug is growing") == ["ZG"]
    assert canton_hints("Wir sind in Baden (AG) ansässig") == ["AG"]
    assert canton_hints("Zug, Switzerland") == ["ZG"]
    assert canton_hints("Our office in Freiburg, Switzerland is new.") == ["FR"]


def test_ambiguous_in_location_field():
    assert canton_hints("Zug", location_field=True) == ["ZG"]
    assert canton_hints("Baden", location_field=True) == ["AG"]
    assert canton_hints("Baden-Württemberg, Germany", location_field=True) == []


def test_free_text_needs_capital_letter():
    assert canton_hints("la région de zürich") == []
    assert canton_hints("Zürich") == ["ZH"]


def test_codes_only_in_location_field():
    assert canton_hints("Foo AG hires in CH") == []
    assert canton_hints("Zürich, ZH, CH", location_field=True) == ["ZH"]
    assert canton_hints("Remote, CH", location_field=True) == []
    assert mentions_switzerland("Remote, CH", location_field=True)
    assert canton_hints("Switzerland, SG", location_field=True) == ["SG"]


def test_codes_alone_are_not_swiss():
    assert canton_hints("Paris, FR", location_field=True) == []
    assert not mentions_switzerland("SG", location_field=True)
    assert not mentions_switzerland("Paris, FR", location_field=True)
    assert not mentions_switzerland("London, United Kingdom", location_field=True)


def test_country_words():
    for word in ("Schweiz", "Suisse", "Svizzera", "Switzerland"):
        assert mentions_switzerland(word, location_field=True)
        assert mentions_switzerland(f"Based in {word}")
    assert not mentions_switzerland("Swiss banks")


def test_freiburg_germany_in_parentheses():
    assert canton_hints("Freiburg (Germany) / Zurich", location_field=True) == ["ZH"]


def test_hints_order_and_multiple_cantons():
    assert canton_hints("Zürich, Genf and Lugano") == ["ZH", "GE", "TI"]
    assert place_keys("Zürich, Genf and Lugano", ["GE", "TI"]) == [
        "place:6621:Genève",
        "place:5192:Lugano",
    ]


def test_small_postal_spillover_into_another_canton_is_ignored():
    from jobfacts.locations import canton_hints

    assert canton_hints("St. Gallen", location_field=True) == ["SG"]


# --- foreign places in a location field ---------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "MONTE CARLO, Monaco",  # "Monte" is a register name
        "CHATEAUNEUF SUR ISERE, France",  # a Châteauneuf exists in Valais
        "12 Rue de la Gare, 75001 Paris, France",  # "Rue" is a register name
        "Zug, Germany",  # a Swiss name directly before a foreign country
        "Sion, Frankreich",
        "Châteauneuf, Francia",
        "Châteauneuf, Italie",
        "Lancy, Royaume-Uni",
        "Aarau, United States of America",
        "Field Sales (Canada), Canada",  # "Sales" is a register name
        "Wil, Spain / Au, Poland",
        "Bern, DE",  # an ISO code as the last part
        "Bern, (US)",
        "Sion, JP",
    ],
)
def test_foreign_place_is_not_swiss(text):
    assert not mentions_switzerland(text, location_field=True)
    assert canton_hints(text, location_field=True) == []
    assert not find_places(text, list(CANTONS), location_field=True)


@pytest.mark.parametrize(
    ("text", "cantons"),
    [
        ("Zurich, Switzerland / Paris, France", ["ZH"]),
        ("Paris, France / Zurich, Switzerland", ["ZH"]),
        ("Paris, France / Zürich", ["ZH"]),
        ("Berlin, Germany; Zug", ["ZG"]),  # Greenhouse joins with ";"
        ("Zug | Wien, Austria", ["ZG"]),
        ("Genève, Suisse / Lyon, France", ["GE"]),
        ("Lugano, Svizzera / Milano, Italia", ["TI"]),
        ("Basel, Schweiz, Deutschland", ["BS"]),  # the same place names Switzerland and Germany
        ("Geneva, London, Paris, Singapore", ["GE"]),  # a comma list where Singapore is a city
        ("Singapore / Zug", ["ZG"]),
        ("Biel/Bienne, Switzerland", ["BE"]),  # a slash without spaces is part of a name
    ],
)
def test_one_swiss_place_keeps_a_multi_place_string_swiss(text, cantons):
    assert mentions_switzerland(text, location_field=True)
    assert canton_hints(text, location_field=True) == cantons


def test_foreign_place_of_a_multi_place_string_gives_no_canton_or_place():
    text = "Chateauneuf sur Isere, France / Zurich, Switzerland"
    assert canton_hints(text, location_field=True) == ["ZH"]
    assert not find_places(text, ["VS"], location_field=True)
    assert place_keys(text, ["ZH"], location_field=True) == ["place:261:Zürich"]


def test_canton_codes_never_veto_even_when_they_are_country_codes():
    # FR = Fribourg or France, GE = Geneva or Georgia, AG = Aargau or Antigua, SG = St. Gallen
    # or Singapore, TI = Ticino, NE = Neuchâtel or Niger, BE = Bern or Belgium, LU = Lucerne
    for text, canton in [
        ("Fribourg, FR", "FR"),
        ("Geneva, GE", "GE"),
        ("Aarau, AG", "AG"),
        ("St. Gallen, SG", "SG"),
        ("Lugano, TI", "TI"),
        ("Neuchâtel, NE", "NE"),
        ("Bern, BE", "BE"),
        ("Luzern, LU", "LU"),
    ]:
        assert canton in canton_hints(text, location_field=True), text
        assert mentions_switzerland(text, location_field=True), text


def test_country_codes_veto_only_when_they_are_not_canton_codes():
    assert mentions_switzerland("Zurich, CH", location_field=True)
    assert not mentions_switzerland("Paris, FR", location_field=True)  # no Swiss place anyway
    assert not mentions_switzerland("Tbilisi, GE", location_field=True)
    for code in ("FR", "GE", "AG", "SG", "TI", "NE", "BE", "LU", "GR", "AR"):
        assert code not in locations._load().foreign_codes
    assert {"DE", "US", "IT", "IN", "AU"} <= locations._load().foreign_codes
    # a two-letter register name written as a code is not a city that exempts the place
    assert not mentions_switzerland("North Sydney, AU", location_field=True)


def test_foreign_country_names_in_four_languages_and_no_register_clash():
    index = locations._load()
    for name in (
        "Germany", "Deutschland", "Allemagne", "Germania", "Österreich", "Autriche", "Spagna",
        "Royaume-Uni", "Vereinigtes Königreich", "États-Unis", "Stati Uniti", "UAE", "Israel",
        "Singapore", "Mexiko", "Brésil", "Giappone", "India", "China", "Canada", "Australia",
    ):  # fmt: skip
        assert index.foreign.search(locations.normalize(name)), name
    assert not index.foreign.search("switzerland")
    for tokens in locations._tokens("Monaco"), locations._tokens("Luxembourg"):
        assert tokens not in index.names  # no country name is also a Swiss place


def test_free_text_is_unchanged_by_the_foreign_rule():
    assert mentions_switzerland("Based in Zürich; offices in Paris, France")
    assert canton_hints("Monte Carlo, Monaco") == []
