import re
from pathlib import Path
from typing import Callable, Dict, List, Tuple
from urllib.parse import urlparse

import requests


PLAYLIST_URL_RE = re.compile(r"/users/([^/]+)/playlists/(\d+)")


def _extract_owner_and_kind(url: str) -> Tuple[str, str]:
    parsed = urlparse(url)
    if not parsed.netloc or "music.yandex" not in parsed.netloc:
        raise ValueError("Invalid Yandex Music URL.")
    match = PLAYLIST_URL_RE.search(parsed.path)
    if not match:
        raise ValueError(
            "Could not parse playlist URL. Expected: https://music.yandex.ru/users/<user>/playlists/<id>"
        )
    return match.group(1), match.group(2)


def _playlist_endpoint(owner: str, kind: str) -> str:
    return (
        "https://music.yandex.ru/handlers/playlist.jsx"
        f"?owner={owner}&kinds={kind}&light=false&lang=ru"
    )


def fetch_playlist_tracks_from_url(
    url: str, log: Callable[[str], None] = print
) -> Dict[str, object]:
    owner, kind = _extract_owner_and_kind(url)
    endpoint = _playlist_endpoint(owner, kind)
    log(f"Fetching Yandex playlist: owner={owner}, id={kind}")

    response = requests.get(
        endpoint,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    data = response.json()

    playlist = data.get("playlist") or data.get("result", {}).get("playlist")
    if not playlist:
        raise RuntimeError("Could not read playlist data from Yandex response.")

    raw_tracks = playlist.get("tracks") or []
    if not raw_tracks:
        raise RuntimeError("Playlist has no tracks or could not be read.")

    lines: List[str] = []
    for item in raw_tracks:
        track = item.get("track") if isinstance(item, dict) and "track" in item else item
        if not isinstance(track, dict):
            continue
        title = (track.get("title") or "").strip()
        artists_data = track.get("artists") or []
        artists = ", ".join(
            artist.get("name", "").strip()
            for artist in artists_data
            if isinstance(artist, dict) and artist.get("name")
        )
        if not title or not artists:
            continue
        lines.append(f"{artists} - {title}")

    if not lines:
        raise RuntimeError("No valid tracks parsed from Yandex playlist.")

    return {
        "title": (playlist.get("title") or f"Playlist {kind}").strip(),
        "owner": owner,
        "kind": kind,
        "tracks": lines,
        "count": len(lines),
    }


def save_tracks_txt(output_path: Path, tracks: List[str]) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(tracks), encoding="utf-8")
    return output_path
