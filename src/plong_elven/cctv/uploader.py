from collections.abc import Callable
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import TypeVar

import httpx

T = TypeVar("T")


class LatestUploader[T]:
    """Send only the newest pending item without blocking the producer."""

    def __init__(self, send: Callable[[httpx.Client, T], bool]) -> None:
        self._send = send
        self._pending: Queue[T] = Queue(maxsize=1)
        self._stop = Event()
        self._thread = Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def submit(self, item: T) -> None:
        try:
            self._pending.put_nowait(item)
        except Full:
            try:
                self._pending.get_nowait()
                self._pending.task_done()
            except Empty:
                pass
            self._pending.put_nowait(item)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3.0)

    def _run(self) -> None:
        retry_delay = 0.5
        with httpx.Client() as client:
            while not self._stop.is_set():
                try:
                    item = self._pending.get(timeout=0.1)
                except Empty:
                    continue
                try:
                    sent = self._send(client, item)
                    if sent:
                        retry_delay = 0.5
                    else:
                        self._stop.wait(retry_delay)
                        retry_delay = min(retry_delay * 2, 10.0)
                finally:
                    self._pending.task_done()
