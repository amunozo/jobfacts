"""Fake model clients for tests (no network)."""

from types import SimpleNamespace

from jobfacts.extract.posting import PostingText

DESCRIPTION = (
    "We build machine learning systems in Zürich. You will train models. "
    "We need 3 years of experience. Fluent German is required. This is a hybrid role."
)


def posting(description: str = DESCRIPTION, location: str = "Zürich, Switzerland"):
    return PostingText.build(1, "Senior ML Engineer", location, description)


class FakeJev:
    """Answers every Choice with its first option (or `pick[name]`) and every Noul with `noul`
    (default 0.9; `noul_pick[name]` overrides one question)."""

    def __init__(self, pick=None, noul=0.9, noul_pick=None):
        self.pick, self.noul, self.calls = pick or {}, noul, []
        self.noul_pick = noul_pick or {}

    def system_one(self, state, questions, model=None):
        self.calls.append((state, questions, model))
        answers = {}
        for name, q in questions.items():
            if hasattr(q, "criteria") and q.criteria is not None:
                options = list(q.criteria)
                choice = self.pick.get(name, options[0])
                probs = {
                    o: (0.8 if o == choice else 0.2 / max(1, len(options) - 1)) for o in options
                }
                answers[name] = SimpleNamespace(choice=choice, probabilities=probs, confidence=0.5)
            else:
                answers[name] = SimpleNamespace(noul=self.noul_pick.get(name, self.noul))
        return SimpleNamespace(
            answers=answers, usage=SimpleNamespace(input_tokens=1000, output_tokens=5), model="x"
        )


class FakeChat:
    """OpenAI-style client that returns the queued replies one by one."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        content = self.replies.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
        )


def openrouter_answers(body, pick=None, noul=0.9, noul_pick=None):
    """The `answers` of an OpenRouter / TypeSafe response for a serialized request body, built
    with the same rules as FakeJev (first option unless `pick`; `noul` unless `noul_pick`)."""
    pick, noul_pick = pick or {}, noul_pick or {}
    answers = {}
    for name, q in body["questions"].items():
        if q["type"] == "choice":
            options = list(q["criteria"])
            choice = pick.get(name, options[0])
            probs = {o: (0.8 if o == choice else 0.2 / max(1, len(options) - 1)) for o in options}
            answers[name] = {
                "type": "choice",
                "choice": choice,
                "probabilities": probs,
                "confidence": 0.5,
            }
        else:
            answers[name] = {"type": "noul", "noul": noul_pick.get(name, noul)}
    return answers


def openrouter_payload(body, **kwargs):
    return {
        "model": "typesafe/jev-1.14-20261014",
        "answers": openrouter_answers(body, **kwargs),
        "usage": {"input_tokens": 1000, "output_tokens": 5, "cost": 0.000042},
        "id": "gen-1",
        "provider": "TypeSafe",
    }
