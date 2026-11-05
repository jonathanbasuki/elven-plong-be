"""Keeps the camera's zone list current by polling the API in a background thread,
so a slow or down API never stalls the detection loop."""

import logging
from collections.abc import Callable
from threading import Event, Thread

import httpx

logger = logging.getLogger(__name__)


class ZonesPoller:
    """`zones` is the latest successfully fetched list ([] until the first one).
    A failed fetch keeps the previous list. `version` goes up on every change."""

    def __init__(
        self,
        fetch: Callable[[httpx.Client], list[dict] | None],
        interval: float = 5.0,
    ) -> None:
        self._fetch = fetch
        self._interval = interval
        self._stop = Event()
        self._thread = Thread(target=self._run, daemon=True)
        self.zones: list[dict] = []
        self.version = 0

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3.0)

    def poll_once(self, client: httpx.Client) -> None:
        zones = self._fetch(client)
        if zones is not None and zones != self.zones:
            self.zones = zones  # one reference swap: the loop never sees a half list
            self.version += 1
            print(f"loaded {len(zones)} zone(s) from the API")

    def _run(self) -> None:
        with httpx.Client() as client:
            while not self._stop.is_set():
                self.poll_once(client)
                self._stop.wait(self._interval)
