"""One module per job board. Each exposes `fetch(client, board_id, extra) -> list[Posting]`."""

from . import ashby, greenhouse, lever, recruitee, smartrecruiters, workable

BOARDS = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
    "smartrecruiters": smartrecruiters.fetch,
    "recruitee": recruitee.fetch,
    "workable": workable.fetch,
}
