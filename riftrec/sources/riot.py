"""RiotSource - Riot Live Client Data API as a signal source (EW-28/33).

Polls `/liveclientdata/allgamedata` (local, self-signed cert, only during a
running match) at a fixed interval and emits:
- one GameEvent per new event (deduplicated by Riot EventID)
- one GameSnapshot of the active player every `snapshot_interval_s` (KDA/CS/gold)

Game start = first reachable poll; game end = endpoint no longer reachable
(match over) OR a GameEnd event. When the source ends, the runtime closes the
session (see RecorderRuntime._supervise).

The HTTP access is injectable via `fetch` so the logic can be tested without a
running LoL match.
"""

from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable, Optional

from ..clock import SessionClock
from ..model import GameEvent, GameSnapshot
from .base import EmitFn

DEFAULT_BASE_URL = "https://127.0.0.1:2999"
_ALLGAMEDATA = "/liveclientdata/allgamedata"

# () -> dict of the allgamedata JSON, or None when the endpoint is unreachable
# (no match active / match ended).
FetchFn = Callable[[], Awaitable[Optional[dict]]]


def new_events(events: list[dict], last_id: Optional[int]) -> list[dict]:
    """Events with EventID > last_id, sorted ascending."""
    fresh = [e for e in events if last_id is None or e.get("EventID", -1) > last_id]
    return sorted(fresh, key=lambda e: e.get("EventID", 0))


def _find_active_row(data: dict) -> dict:
    """Find the active player's scoreboard row in allPlayers (robust across
    summonerName / riotId / riotIdGameName)."""
    active = data.get("activePlayer") or {}
    name = active.get("summonerName") or active.get("riotIdGameName")
    riot_id = active.get("riotId")
    for p in data.get("allPlayers") or []:
        if name and p.get("summonerName") == name:
            return p
        if riot_id and p.get("riotId") == riot_id:
            return p
        if name and p.get("riotIdGameName") == name:
            return p
    return {}


def extract_snapshot(data: dict, mono_ns: int, utc: str) -> GameSnapshot:
    active = data.get("activePlayer") or {}
    row = _find_active_row(data)
    scores = row.get("scores") or {}
    game_time = (data.get("gameData") or {}).get("gameTime")
    level = active.get("level")
    if level is None:
        level = row.get("level")
    return GameSnapshot(
        mono_ns=mono_ns,
        utc=utc,
        game_time_s=game_time,
        kills=scores.get("kills"),
        deaths=scores.get("deaths"),
        assists=scores.get("assists"),
        cs=scores.get("creepScore"),
        gold=active.get("currentGold"),
        level=level,
    )


class RiotSource:
    name = "riot"

    def __init__(
        self,
        *,
        poll_interval_s: float = 1.0,
        snapshot_interval_s: float = 5.0,
        base_url: str = DEFAULT_BASE_URL,
        fetch: Optional[FetchFn] = None,
    ) -> None:
        self._poll_interval_s = poll_interval_s
        self._snapshot_interval_s = snapshot_interval_s
        self._url = base_url.rstrip("/") + _ALLGAMEDATA
        self._fetch = fetch

    async def run(self, emit: EmitFn, clock: SessionClock) -> None:
        fetch, close = self._make_fetch()
        started = False
        last_event_id: Optional[int] = None
        last_snapshot_mono = 0
        try:
            while True:
                data = await fetch()
                if data is None:
                    if started:
                        return  # match over -> source ends, session closes
                    await asyncio.sleep(self._poll_interval_s)
                    continue
                started = True
                mono, utc = clock.now()

                events = (data.get("events") or {}).get("Events") or []
                end_seen = False
                for event in new_events(events, last_event_id):
                    last_event_id = event.get("EventID", last_event_id)
                    emit(GameEvent(
                        mono_ns=mono, utc=utc,
                        game_time_s=event.get("EventTime"),
                        event_id=event.get("EventID"),
                        event_type=event.get("EventName", "Unknown"),
                        payload_json=json.dumps(event),
                    ))
                    if event.get("EventName") == "GameEnd":
                        end_seen = True

                if mono - last_snapshot_mono >= self._snapshot_interval_s * 1e9:
                    emit(extract_snapshot(data, mono, utc))
                    last_snapshot_mono = mono

                if end_seen:
                    return
                await asyncio.sleep(self._poll_interval_s)
        finally:
            await close()

    def _make_fetch(self) -> tuple[FetchFn, Callable[[], Awaitable[None]]]:
        """Return (fetch, close). With an injected fetch, close is a no-op."""
        if self._fetch is not None:
            async def _noop() -> None:
                return None

            return self._fetch, _noop

        import httpx

        client = httpx.AsyncClient(verify=False, timeout=2.0)

        async def _fetch() -> Optional[dict]:
            try:
                resp = await client.get(self._url)
            except httpx.RequestError:
                return None  # unreachable => no match active / ended
            if resp.status_code != 200:
                return None
            try:
                return resp.json()
            except json.JSONDecodeError:
                return None

        async def _close() -> None:
            await client.aclose()

        return _fetch, _close
