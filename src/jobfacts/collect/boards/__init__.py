"""One module per job board. Each exposes `fetch(client, board_id, extra) -> list[Posting]`."""

from . import (
    ashby,
    greenhouse,
    lever,
    personio,
    recruitee,
    smartrecruiters,
    teamtailor,
    workable,
    workday,
)

BOARDS = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
    "smartrecruiters": smartrecruiters.fetch,
    "recruitee": recruitee.fetch,
    "workable": workable.fetch,
    "personio": personio.fetch,
    "teamtailor": teamtailor.fetch,
    "workday": workday.fetch,
}
