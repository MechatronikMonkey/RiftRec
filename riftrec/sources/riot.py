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
import hashlib
import json
import zlib
from typing import Awaitable, Callable, Optional

from ..clock import SessionClock
from ..model import GameEvent, GameRaw, GameSnapshot
from .base import EmitFn

DEFAULT_BASE_URL = "https://127.0.0.1:2999"
_ALLGAMEDATA = "/liveclientdata/allgamedata"

# How often the full response is stored. Far coarser than the poll interval:
# the parsed tables already carry the fast-moving numbers, while the raw
# channel exists so that fields we do not parse today (champion, position,
# team, items, runes, enemy scoreboard) remain recoverable (EW-86).
DEFAULT_RAW_INTERVAL_S = 30.0

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


def active_riot_id(data: dict) -> Optional[str]:
    """The recording player's Riot Name#TAG (falls back to summonerName on
    pre-Riot-ID clients). Used once per session to tell "you" apart from
    enemies in ChampionKill events - see SupervisorService."""
    active = data.get("activePlayer") or {}
    return active.get("riotId") or active.get("summonerName") or None


# -- Pseudonymisation of foreign players (EW-86 / EW-67) --------------------
#
# `allgamedata` carries the Riot IDs of all ten players. Nine of them never
# consented to anything, so their identifiers must not reach the session file.
# The recording player's own id is left untouched: RiftLab uses it to tell own
# kills/deaths from enemy ones, and it is already stored openly in
# `session.active_riot_id` (pseudonymising that one is EW-67's scope).
#
# The hash is salted with the session id, so the same person is consistent
# within one session but cannot be linked across sessions.

_NAME_KEYS = ("summonerName", "riotId", "riotIdGameName")


def _pseudonym(salt: str, name: str) -> str:
    digest = hashlib.sha256(f"{salt}|{name}".encode("utf-8")).hexdigest()
    return f"p_{digest[:12]}"


def _name_variants(player: dict) -> set[str]:
    return {v for k in _NAME_KEYS if isinstance(v := player.get(k), str) and v}


def build_pseudonym_map(data: dict, salt: str) -> dict[str, str]:
    """Map every foreign player's name variants to a session-local pseudonym.

    Built once per session - the player list does not change during a match -
    and reused for both the raw payloads and the event payloads so that the
    kill/death attribution in RiftLab stays consistent.

    `salt` is anything unique per session (the session id, or the session
    clock's start timestamp). It keeps the same person consistent within a
    session while preventing linkage across sessions.
    """
    own = _name_variants(data.get("activePlayer") or {}) | _name_variants(_find_active_row(data))
    mapping: dict[str, str] = {}
    for player in data.get("allPlayers") or []:
        variants = _name_variants(player) - own
        if not variants:
            continue
        # ONE pseudonym per person, shared by every spelling of their name.
        # Hashing each variant separately gave the same player a different id
        # in game_raw (which carries `riotId`, "Name#TAG") than in game_event
        # (whose Assisters carry the bare game name), so an assist could not be
        # matched to the scoreboard row of the player who made it. That linkage
        # cannot be repaired afterwards: the salt is session-local and the
        # plaintext is never stored.
        key = player.get("riotId") or min(variants)
        pseudo = _pseudonym(salt, key)
        for name in variants:
            mapping[name] = pseudo
    return mapping


def apply_pseudonyms(obj, mapping: dict[str, str]):
    """Recursively replace known foreign player names in a JSON-like structure.

    Only names present in `mapping` are touched, so turret/minion identifiers
    in event payloads are left readable.
    """
    if not mapping:
        return obj
    if isinstance(obj, str):
        return mapping.get(obj, obj)
    if isinstance(obj, list):
        return [apply_pseudonyms(v, mapping) for v in obj]
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            # The bare tag line would survive name replacement and still
            # identify a player together with the game name - drop it.
            if key == "riotIdTagLine" and _name_variants(obj) & mapping.keys():
                out[key] = ""
            else:
                out[key] = apply_pseudonyms(value, mapping)
        return out
    return obj


def compress_game_data(data: dict, mapping: dict[str, str]) -> bytes:
    """Pseudonymise, serialise and zlib-compress one `allgamedata` response.

    Uncompressed a response is roughly 40 kB; at the storage cadence that would
    add up to ~16 MB per match, which does not fit a manual e-mail return path.
    """
    cleaned = apply_pseudonyms(data, mapping)
    return zlib.compress(json.dumps(cleaned, separators=(",", ":")).encode("utf-8"), 6)


def decompress_game_data(blob: bytes) -> dict:
    """Inverse of `compress_game_data` - for RiftLab and for tests."""
    return json.loads(zlib.decompress(blob).decode("utf-8"))


def extract_snapshot(data: dict, mono_ns: int, utc: str) -> GameSnapshot:
    active = data.get("activePlayer") or {}
    row = _find_active_row(data)
    scores = row.get("scores") or {}
    game_time = (data.get("gameData") or {}).get("gameTime")
    level = active.get("level")
    if level is None:
        level = row.get("level")
    is_dead = row.get("isDead")
    respawn = row.get("respawnTimer")
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
        is_dead=bool(is_dead) if is_dead is not None else None,
        # Sampled here at the snapshot interval rather than only in game_raw:
        # death timers run 10-50 s, so the 30 s raw cadence misses most of them,
        # and EW-61 needs the value at the moment of death.
        respawn_timer_s=float(respawn) if respawn is not None else None,
    )


class RiotSource:
    name = "riot"

    def __init__(
        self,
        *,
        poll_interval_s: float = 1.0,
        snapshot_interval_s: float = 5.0,
        raw_interval_s: float = DEFAULT_RAW_INTERVAL_S,
        base_url: str = DEFAULT_BASE_URL,
        fetch: Optional[FetchFn] = None,
    ) -> None:
        self._poll_interval_s = poll_interval_s
        self._snapshot_interval_s = snapshot_interval_s
        self._raw_interval_s = raw_interval_s
        self._url = base_url.rstrip("/") + _ALLGAMEDATA
        self._fetch = fetch

    async def run(self, emit: EmitFn, clock: SessionClock) -> None:
        fetch, close = self._make_fetch()
        started = False
        last_event_id: Optional[int] = None
        last_snapshot_mono = 0
        last_raw_mono: Optional[int] = None
        pseudonyms: dict[str, str] = {}
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
                game_time = (data.get("gameData") or {}).get("gameTime")

                # Built once, from the first response that carries a player
                # list, and reused for both raw and event payloads.
                if not pseudonyms and data.get("allPlayers"):
                    pseudonyms = build_pseudonym_map(data, clock.started_utc)

                events = (data.get("events") or {}).get("Events") or []
                end_seen = False
                for event in new_events(events, last_event_id):
                    last_event_id = event.get("EventID", last_event_id)
                    emit(GameEvent(
                        mono_ns=mono, utc=utc,
                        game_time_s=event.get("EventTime"),
                        event_id=event.get("EventID"),
                        event_type=event.get("EventName", "Unknown"),
                        payload_json=json.dumps(apply_pseudonyms(event, pseudonyms)),
                    ))
                    if event.get("EventName") == "GameEnd":
                        end_seen = True

                if mono - last_snapshot_mono >= self._snapshot_interval_s * 1e9:
                    emit(extract_snapshot(data, mono, utc))
                    last_snapshot_mono = mono

                # First poll always, then at the coarse raw interval. The last
                # response before the match ends is stored too, so the final
                # scoreboard is never lost.
                due = last_raw_mono is None or mono - last_raw_mono >= self._raw_interval_s * 1e9
                if due or end_seen:
                    emit(GameRaw(
                        mono_ns=mono, utc=utc, game_time_s=game_time,
                        payload_zlib=compress_game_data(data, pseudonyms),
                    ))
                    last_raw_mono = mono

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
