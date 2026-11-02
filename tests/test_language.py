from jobfacts.language import detect


def test_english():
    assert (
        detect("We are looking for a software engineer to join our growing team in Zurich.") == "en"
    )


def test_german():
    assert (
        detect("Wir suchen eine Softwareentwicklerin für unser wachsendes Team in Zürich.") == "de"
    )


def test_french():
    assert (
        detect("Nous recherchons un ingénieur logiciel pour rejoindre notre équipe à Genève.")
        == "fr"
    )


def test_other_and_empty():
    assert detect("Cerchiamo un ingegnere del software per il nostro team a Lugano.") == "other"
    assert detect("   ") == "other"
