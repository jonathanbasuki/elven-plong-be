"""The camera's zone poller keeps the last good list and bumps a version on change."""

from plong_elven.cctv.zones_poller import ZonesPoller

A = [{"id": "z1", "name": "A", "polygon": [[0, 0], [1, 0], [1, 1]]}]
B = [{"id": "z2", "name": "B", "polygon": [[0, 0], [1, 0], [1, 1]]}]


def test_poller_tracks_changes_and_ignores_failures() -> None:
    results = iter([A, A, None, B])
    poller = ZonesPoller(lambda _client: next(results))
    assert (poller.zones, poller.version) == ([], 0)

    poller.poll_once(None)
    assert (poller.zones, poller.version) == (A, 1)
    poller.poll_once(None)  # unchanged
    assert poller.version == 1
    poller.poll_once(None)  # API down: keep the last good list
    assert (poller.zones, poller.version) == (A, 1)
    poller.poll_once(None)
    assert (poller.zones, poller.version) == (B, 2)
