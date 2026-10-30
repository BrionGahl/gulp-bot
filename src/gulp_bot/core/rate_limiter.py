"""A sliding-window rate limit. No Discord in here; users are their numeric IDs, and times are
`time.monotonic()` seconds."""

from __future__ import annotations

from collections import deque


class RateLimiter:
    """At most `max_requests` requests per user in any `window` seconds. With 0, nobody may ask at all."""

    def __init__(self, max_requests: int, window: float):
        self._max = max_requests
        self._window = window
        # The times of each user's requests within the window, oldest first. A user with none isn't kept.
        self._requests: dict[int, deque[float]] = {}

    @property
    def max(self) -> int:
        """How many requests a user may make in the window."""
        return self._max

    def check(self, user: int, now: float) -> float | None:
        """Counts a request by `user` and returns `None`, or, if they may not ask now, returns how many
        seconds until they may (and counts nothing)."""
        # Other users' old requests are dropped too, so the map doesn't grow without bound.
        for times in self._requests.values():
            while times and now - times[0] >= self._window:
                times.popleft()
        self._requests = {key: times for key, times in self._requests.items() if times}

        times = self._requests.setdefault(user, deque())
        if len(times) >= self._max:
            # With a limit of 0 there is no oldest request to wait for: a whole window, then ask again.
            oldest = times[0] if times else now
            if not times:
                del self._requests[user]
            return max(0.0, self._window - max(0.0, now - oldest))

        times.append(now)
        return None

    def count(self, user: int, now: float) -> int:
        """How many requests of `user` are within the window now."""
        return sum(1 for time in self._requests.get(user, ()) if now - time < self._window)

    def undo(self, user: int) -> None:
        """Takes back the request `check` last counted for `user`: for one that got no answer through no
        fault of theirs, or that another limit refused after this one had counted it."""
        times = self._requests.get(user)
        if times:
            times.pop()
            if not times:
                del self._requests[user]
