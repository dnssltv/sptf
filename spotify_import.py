import argparse
import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import spotipy
from dotenv import load_dotenv
from requests.exceptions import RequestException, Timeout
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

ENV_PATH = Path(".env")
TOKEN_CACHE_PATH = Path(".spotify_token_cache")
SEARCH_CACHE_PATH = Path("search_cache.json")
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"
DEFAULT_INPUT_FILE = r"C:\Users\User\Downloads\Текстовый документ.txt"
SPOTIFY_SCOPE = "playlist-modify-private playlist-modify-public user-read-private"


@dataclass
class ParsedTrack:
    artists: str
    title: str
    raw: str


def _track_cache_key(track: ParsedTrack) -> str:
    artists = ",".join(part.strip().lower() for part in track.artists.split(",") if part.strip())
    title = track.title.strip().lower()
    return f"{artists}|{title}"


def _load_search_cache() -> Dict[str, Optional[str]]:
    if not SEARCH_CACHE_PATH.exists():
        return {}
    try:
        payload = json.loads(SEARCH_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(k): (None if v is None else str(v)) for k, v in payload.items()}


def _save_search_cache(cache: Dict[str, Optional[str]]) -> None:
    SEARCH_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_track_line(line: str) -> Optional[ParsedTrack]:
    raw = _normalize_for_query(line.strip())
    if not raw:
        return None
    if raw.startswith("#"):
        return None
    if " - " not in raw:
        return None
    artists, title = raw.split(" - ", 1)
    artists = artists.strip()
    title = title.strip()
    if not artists or not title:
        return None
    return ParsedTrack(artists=artists, title=title, raw=raw)


def read_tracks(path: Path) -> List[ParsedTrack]:
    parsed: List[ParsedTrack] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            track = parse_track_line(line)
            if track is not None:
                parsed.append(track)
    return parsed


def chunked(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def save_credentials(client_id: str, client_secret: str, redirect_uri: str) -> None:
    content = (
        f"SPOTIPY_CLIENT_ID={client_id}\n"
        f"SPOTIPY_CLIENT_SECRET={client_secret}\n"
        f"SPOTIPY_REDIRECT_URI={redirect_uri}\n"
    )
    ENV_PATH.write_text(content, encoding="utf-8")


def load_saved_credentials() -> Dict[str, str]:
    load_dotenv(override=False)
    return {
        "client_id": os.getenv("SPOTIPY_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("SPOTIPY_CLIENT_SECRET", "").strip(),
        "redirect_uri": os.getenv("SPOTIPY_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip()
        or DEFAULT_REDIRECT_URI,
    }


def ensure_spotify_credentials(
    interactive: bool = True, log: Callable[[str], None] = print
) -> Dict[str, str]:
    creds = load_saved_credentials()
    client_id = creds["client_id"]
    client_secret = creds["client_secret"]
    redirect_uri = creds["redirect_uri"]

    if client_id and client_secret:
        return creds

    if not interactive:
        raise RuntimeError(
            "Spotify credentials are missing. Fill SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET."
        )

    log("Spotify credentials not found.")
    log("Open https://developer.spotify.com/dashboard and create an app.")
    log(f"Add Redirect URI: {DEFAULT_REDIRECT_URI}\n")
    client_id = input("Enter SPOTIPY_CLIENT_ID: ").strip()
    client_secret = input("Enter SPOTIPY_CLIENT_SECRET: ").strip()
    redirect_uri_input = input(
        f"Enter SPOTIPY_REDIRECT_URI [{DEFAULT_REDIRECT_URI}]: "
    ).strip()
    redirect_uri = redirect_uri_input or DEFAULT_REDIRECT_URI

    if not client_id or not client_secret:
        raise RuntimeError("SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET are required.")

    save_credentials(client_id, client_secret, redirect_uri)
    log(f"Saved credentials to: {ENV_PATH.resolve()}\n")

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }


def clear_token_cache(log: Callable[[str], None] = print) -> None:
    if TOKEN_CACHE_PATH.exists():
        TOKEN_CACHE_PATH.unlink()
        log(f"Removed cached token: {TOKEN_CACHE_PATH.resolve()}")


def build_client(
    creds: Dict[str, str],
    force_reauth: bool = False,
    log: Callable[[str], None] = print,
) -> spotipy.Spotify:
    if force_reauth:
        clear_token_cache(log=log)

    auth_manager = SpotifyOAuth(
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
        redirect_uri=creds["redirect_uri"],
        scope=SPOTIFY_SCOPE,
        open_browser=True,
        cache_path=str(TOKEN_CACHE_PATH),
        show_dialog=force_reauth,
    )
    return spotipy.Spotify(
        auth_manager=auth_manager,
        requests_timeout=12,
        retries=0,
        status_retries=0,
        backoff_factor=0,
    )


def _format_spotify_error(exc: SpotifyException) -> str:
    base_text = str(exc).strip()
    if not base_text:
        base_text = "Unknown Spotify error"
    details = ""
    if getattr(exc, "msg", None):
        details = f"\nDetails: {exc.msg}"
    text = f"{base_text}{details}"
    if exc.http_status == 403 and "premium subscription required" in text.lower():
        return (
            "Spotify API rejected request (403): premium subscription is required for the app owner.\n\n"
            "What to do:\n"
            "1) Ensure app owner account has active Premium.\n"
            "2) Wait a few hours after subscription activation.\n"
            "3) Re-login (delete .spotify_token_cache and authorize again)."
        )
    return f"Spotify API error ({exc.http_status}): {text}"


def _raise_spotify_runtime_error(stage: str, exc: SpotifyException) -> RuntimeError:
    return RuntimeError(f"{stage} failed.\n{_format_spotify_error(exc)}")


class RateLimitExceeded(RuntimeError):
    def __init__(self, message: str, retry_after_seconds: Optional[int] = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def _parse_retry_after_seconds(exc: SpotifyException) -> Optional[int]:
    headers = getattr(exc, "headers", None)
    if isinstance(headers, dict):
        retry_header = headers.get("Retry-After") or headers.get("retry-after")
        if retry_header is not None:
            try:
                return int(retry_header)
            except (TypeError, ValueError):
                pass
    match = re.search(r"after:\s*(\d+)\s*s", f"{exc}", flags=re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def _format_retry_after(seconds: Optional[int]) -> str:
    if seconds is None:
        return "неизвестно"
    if seconds < 60:
        return f"{seconds} сек"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин"
    hours = minutes // 60
    return f"{hours} ч {minutes % 60} мин"


def _normalize_for_query(text: str) -> str:
    # Normalize Unicode and remove invisible/control symbols that can break search.
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\u200B-\u200D\uFEFF]", "", text)
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201C", '"').replace("\u201D", '"')
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _call_with_retry(
    fn: Callable[[], Any],
    stage: str,
    retries: int = 2,
    delay_seconds: float = 0.8,
) -> Any:
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except (SpotifyException, Timeout, RequestException) as exc:
            if isinstance(exc, SpotifyException):
                text = f"{exc}".lower()
                if exc.http_status == 429 or "rate/request limit" in text or "retry after" in text:
                    retry_after = _parse_retry_after_seconds(exc)
                    msg = "Превышен лимит запросов Spotify API."
                    if retry_after is not None:
                        msg += f" Retry-After: {retry_after} сек ({_format_retry_after(retry_after)})."
                    raise RateLimitExceeded(msg, retry_after_seconds=retry_after) from exc
            last_exc = exc
            if attempt < retries:
                time.sleep(delay_seconds * (attempt + 1))
                continue
            if isinstance(exc, SpotifyException):
                raise _raise_spotify_runtime_error(stage, exc) from exc
            raise RuntimeError(f"{stage} failed.\n{exc!r}") from exc
    if last_exc is not None:
        raise RuntimeError(f"{stage} failed.\n{last_exc!r}")
    raise RuntimeError(f"{stage} failed with unknown error.")


def _add_uris_resilient(
    sp: spotipy.Spotify,
    playlist_id: str,
    uris: List[str],
    log: Callable[[str], None],
) -> int:
    if not uris:
        return 0
    try:
        _call_with_retry(
            lambda: sp.playlist_add_items(playlist_id, uris),
            stage="Add tracks to playlist",
        )
        return len(uris)
    except RateLimitExceeded:
        raise
    except Exception as exc:
        log(f"Batch add failed for {len(uris)} tracks: {exc}. Retrying one by one...")

    added = 0
    for uri in uris:
        try:
            _call_with_retry(
                lambda u=uri: sp.playlist_add_items(playlist_id, [u]),
                stage="Add single track to playlist",
            )
            added += 1
        except RateLimitExceeded:
            raise
        except Exception:
            continue
    return added


def _create_playlist_with_fallback(
    sp: spotipy.Spotify,
    user_id: str,
    playlist_name: str,
    public: bool,
    log: Callable[[str], None],
) -> Dict:
    try:
        return _call_with_retry(
            lambda: sp.user_playlist_create(
                user=user_id,
                name=playlist_name,
                public=public,
                description="Imported from text file",
            ),
            stage="Create playlist",
        )
    except RuntimeError as exc:
        if "Spotify API error (403)" not in str(exc):
            raise
        log("Create playlist via /users/{id}/playlists was forbidden. Retrying via /me/playlists...")
        return _call_with_retry(
            lambda: sp._post(
                "me/playlists",
                payload={
                    "name": playlist_name,
                    "public": public,
                    "description": "Imported from text file",
                },
            ),
            stage="Create playlist",
        )
    except Exception as exc:
        raise RuntimeError(f"Create playlist failed.\n{exc!r}") from exc


def check_spotify_status(
    credentials: Optional[Dict[str, str]] = None,
    interactive_credentials: bool = True,
    force_reauth: bool = False,
    log: Callable[[str], None] = print,
) -> Dict[str, str]:
    if credentials is None:
        credentials = ensure_spotify_credentials(
            interactive=interactive_credentials,
            log=log,
        )
    sp = build_client(credentials, force_reauth=force_reauth, log=log)
    try:
        me = sp.current_user()
    except SpotifyException as exc:
        raise RuntimeError(_format_spotify_error(exc)) from exc

    display_name = me.get("display_name") or me.get("id") or "unknown"
    product = me.get("product") or "unknown"
    country = me.get("country") or "unknown"
    user_id = me.get("id") or ""
    images = me.get("images") or []
    image_url = ""
    if images and isinstance(images, list):
        first = images[0] or {}
        if isinstance(first, dict):
            image_url = str(first.get("url") or "")

    log(f"Авторизован: {display_name} ({user_id})")
    log(f"Тип аккаунта: {product}, страна: {country}")

    if product != "premium":
        log("Внимание: аккаунт не Premium. Некоторые операции могут быть недоступны.")

    return {
        "display_name": str(display_name),
        "user_id": str(user_id),
        "product": str(product),
        "country": str(country),
        "image_url": image_url,
    }


def search_track_uri(
    sp: spotipy.Spotify,
    track: ParsedTrack,
    market: Optional[str] = None,
) -> Optional[str]:
    title = _normalize_for_query(track.title)
    artists = _normalize_for_query(track.artists)
    primary_artist = artists.split(",")[0].strip()
    title_no_paren = re.sub(r"\s*\([^)]*\)", "", title).strip()

    queries = [
        f'track:"{title}" artist:"{primary_artist}"',
        f"{title} {artists}",
    ]
    if title_no_paren and title_no_paren != title:
        queries.append(f'track:"{title_no_paren}" artist:"{primary_artist}"')
        queries.append(f"{title_no_paren} {artists}")

    best_item: Optional[Dict[str, Any]] = None
    for i, q in enumerate(queries):
        result = _call_with_retry(
            lambda query=q: sp.search(q=query, type="track", limit=5, market=market),
            stage=f"Track search #{i + 1}",
            retries=1,
        )
        items = result.get("tracks", {}).get("items", [])
        if not items:
            continue

        # Prefer candidates with matching main artist and closer title.
        normalized_title = title.lower()
        for item in items:
            item_name = _normalize_for_query(str(item.get("name", ""))).lower()
            item_artists = [str(a.get("name", "")) for a in (item.get("artists") or [])]
            if not item_artists:
                continue
            first_item_artist = _normalize_for_query(item_artists[0]).lower()
            if primary_artist.lower() in first_item_artist and normalized_title in item_name:
                return item.get("uri")
        if best_item is None:
            best_item = items[0]

    if best_item:
        return best_item.get("uri")
    return None


def _ask_yes_no(prompt: str, default: bool = False) -> bool:
    yes_values = {"y", "yes", "1", "true"}
    no_values = {"n", "no", "0", "false"}
    suffix = "Y/n" if default else "y/N"
    value = input(f"{prompt} [{suffix}]: ").strip().lower()
    if not value:
        return default
    if value in yes_values:
        return True
    if value in no_values:
        return False
    print("Could not understand input, using default value.")
    return default


def _collect_missing_args(args: argparse.Namespace) -> argparse.Namespace:
    if args.input_file is None:
        input_path = input(
            f'Path to track list txt file [{DEFAULT_INPUT_FILE}]: '
        ).strip()
        args.input_file = Path(input_path or DEFAULT_INPUT_FILE)

    if not args.playlist_name:
        playlist_name = input("Playlist name [Yandex Music Import]: ").strip()
        args.playlist_name = playlist_name or "Yandex Music Import"

    if not args.public:
        args.public = _ask_yes_no("Create public playlist?", default=False)

    return args


def import_tracks(
    input_file: Path,
    playlist_name: str,
    public: bool,
    credentials: Optional[Dict[str, str]] = None,
    interactive_credentials: bool = True,
    force_reauth: bool = False,
    flush_batch_size: int = 100,
    is_paused: Optional[Callable[[], bool]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    state_path: Optional[Path] = None,
    resume: bool = False,
    verbose_track_log: bool = True,
    search_delay_sec: float = 0.35,
    auto_wait_max_sec: int = 120,
    log: Callable[[str], None] = print,
) -> Dict[str, Any]:
    def emit_progress(
        processed: int,
        total: int,
        added: int,
        not_found_count: int,
        cancelled: bool = False,
        track: str = "",
    ) -> None:
        if on_progress is None:
            return
        on_progress(
            {
                "processed": processed,
                "total": total,
                "added": added,
                "not_found": not_found_count,
                "cancelled": cancelled,
                "track": track,
            }
        )

    if not input_file.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_file}")

    if credentials is None:
        credentials = ensure_spotify_credentials(
            interactive=interactive_credentials,
            log=log,
        )
    status = check_spotify_status(
        credentials=credentials,
        interactive_credentials=False,
        force_reauth=force_reauth,
        log=log,
    )

    tracks = read_tracks(input_file)
    if not tracks:
        raise ValueError("No valid tracks found. Expected format per line: Artist - Track")

    total_tracks = len(tracks)
    log(f"Загружено {total_tracks} треков из: {input_file}")
    sp = build_client(credentials, force_reauth=False, log=log)
    user_id = status["user_id"]

    if flush_batch_size < 1:
        flush_batch_size = 1

    pending_uris: List[str] = []
    found_count = 0
    added_count = 0
    not_found: List[str] = []
    search_cache: Dict[str, Optional[str]] = _load_search_cache()
    cache_dirty = False
    processed_count = 0
    cancelled = False
    start_index = 0
    playlist_id = ""
    playlist_url = ""

    def save_state() -> None:
        if state_path is None:
            return
        payload = {
            "input_file": str(input_file.resolve()),
            "playlist_name": playlist_name,
            "public": public,
            "playlist_id": playlist_id,
            "playlist_url": playlist_url,
            "user_id": user_id,
            "total_tracks": total_tracks,
            "next_index": processed_count,
            "found_count": found_count,
            "added_count": added_count,
            "not_found": not_found,
            "pending_uris": pending_uris,
            "search_cache": search_cache,
        }
        state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if state_path is not None and resume and state_path.exists():
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        if raw.get("input_file") != str(input_file.resolve()):
            raise RuntimeError("Resume file belongs to a different input file.")
        playlist_id = str(raw.get("playlist_id") or "")
        playlist_url = str(raw.get("playlist_url") or "")
        start_index = int(raw.get("next_index") or 0)
        found_count = int(raw.get("found_count") or 0)
        added_count = int(raw.get("added_count") or 0)
        not_found = list(raw.get("not_found") or [])
        pending_uris = list(raw.get("pending_uris") or [])
        raw_cache = raw.get("search_cache") or {}
        if isinstance(raw_cache, dict):
            search_cache = {
                str(k): (None if v is None else str(v))
                for k, v in raw_cache.items()
            }
            cache_dirty = True
        processed_count = start_index
        log(f"Продолжаем с трека {start_index + 1}/{total_tracks}...")
    else:
        log("Создаем плейлист...")
        playlist = _create_playlist_with_fallback(
            sp=sp,
            user_id=user_id,
            playlist_name=playlist_name,
            public=public,
            log=log,
        )
        playlist_id = playlist["id"]
        playlist_url = playlist.get("external_urls", {}).get("spotify", "created")
        log(f"Плейлист создан: {playlist_url}")
        save_state()

    if not playlist_id:
        raise RuntimeError("No playlist ID available for import.")

    if pending_uris:
        log(f"Продолжение: добавляем {len(pending_uris)} отложенных треков...")
        try:
            added_count += _add_uris_resilient(sp, playlist_id, pending_uris, log=log)
        except RateLimitExceeded as exc:
            retry = exc.retry_after_seconds
            if retry is not None and retry <= auto_wait_max_sec:
                log(f"Лимит API. Ждем {retry} сек и продолжаем...")
                time.sleep(max(1, retry))
                added_count += _add_uris_resilient(sp, playlist_id, pending_uris, log=log)
            else:
                save_state()
                raise RuntimeError(
                    "Превышен лимит запросов Spotify API. Подождите и нажмите "
                    "'Продолжить после сбоя'."
                ) from exc
        pending_uris.clear()
        save_state()

    log("Начинаем импорт треков...")
    emit_progress(processed_count, total_tracks, added_count, len(not_found), cancelled=False)

    for idx, track in enumerate(tracks[start_index:], start=start_index + 1):
        if is_cancelled and is_cancelled():
            cancelled = True
            log("Импорт остановлен пользователем.")
            break
        while is_paused and is_paused():
            if is_cancelled and is_cancelled():
                cancelled = True
                break
            time.sleep(0.2)
        if cancelled:
            log("Импорт остановлен пользователем.")
            break

        if idx == 1 or idx % 25 == 0:
            log(f"Обрабатываем трек {idx}/{total_tracks}...")
        if verbose_track_log:
            log(f"Сейчас: {track.raw}")

        cache_key = _track_cache_key(track)
        try:
            if cache_key in search_cache:
                uri = search_cache[cache_key]
            else:
                if search_delay_sec > 0:
                    time.sleep(search_delay_sec)
                uri = search_track_uri(
                    sp=sp,
                    track=track,
                    market=status.get("country"),
                )
                search_cache[cache_key] = uri
                cache_dirty = True
                if idx % 50 == 0:
                    _save_search_cache(search_cache)
                    cache_dirty = False
        except RateLimitExceeded as exc:
            retry = exc.retry_after_seconds
            if retry is not None and retry <= auto_wait_max_sec:
                log(f"Лимит API. Ждем {retry} сек и продолжаем...")
                time.sleep(max(1, retry))
                continue
            save_state()
            raise RuntimeError(
                "Превышен лимит запросов Spotify API. Подождите и нажмите "
                "'Продолжить после сбоя'."
            ) from exc
        except Exception as exc:
            not_found.append(track.raw)
            if verbose_track_log:
                log(f"[{idx}/{total_tracks}] ОШИБКА: {track.raw} ({type(exc).__name__})")
            processed_count = idx
            save_state()
            emit_progress(processed_count, total_tracks, added_count, len(not_found), cancelled=cancelled, track=track.raw)
            continue
        if uri:
            pending_uris.append(uri)
            found_count += 1
            if verbose_track_log:
                log(f"[{idx}/{total_tracks}] НАЙДЕН: {track.raw}")
        else:
            not_found.append(track.raw)
            if verbose_track_log:
                log(f"[{idx}/{total_tracks}] НЕ НАЙДЕН: {track.raw}")

        processed_count = idx
        if len(pending_uris) >= flush_batch_size:
            try:
                added_now = _add_uris_resilient(sp, playlist_id, pending_uris, log=log)
            except RateLimitExceeded as exc:
                retry = exc.retry_after_seconds
                if retry is not None and retry <= auto_wait_max_sec:
                    log(f"Лимит API при добавлении. Ждем {retry} сек и продолжаем...")
                    time.sleep(max(1, retry))
                    added_now = _add_uris_resilient(sp, playlist_id, pending_uris, log=log)
                else:
                    save_state()
                    raise RuntimeError(
                        "Превышен лимит запросов Spotify API при добавлении в плейлист. "
                        "Подождите и нажмите 'Продолжить после сбоя'."
                    ) from exc
            added_count += added_now
            log(f"Добавлено {added_now}/{len(pending_uris)} треков (текущий пакет).")
            pending_uris.clear()

        save_state()
        emit_progress(processed_count, total_tracks, added_count, len(not_found), cancelled=cancelled, track=track.raw)

    if pending_uris:
        try:
            added_now = _add_uris_resilient(sp, playlist_id, pending_uris, log=log)
        except RateLimitExceeded as exc:
            retry = exc.retry_after_seconds
            if retry is not None and retry <= auto_wait_max_sec:
                log(f"Лимит API при финальном пакете. Ждем {retry} сек и продолжаем...")
                time.sleep(max(1, retry))
                added_now = _add_uris_resilient(sp, playlist_id, pending_uris, log=log)
            else:
                save_state()
                raise RuntimeError(
                    "Превышен лимит запросов Spotify API при добавлении в плейлист. "
                    "Подождите и нажмите 'Продолжить после сбоя'."
                ) from exc
        added_count += added_now
        log(f"Добавлен финальный пакет: {added_now}/{len(pending_uris)}.")
        pending_uris.clear()
        save_state()

    if cache_dirty:
        _save_search_cache(search_cache)

    emit_progress(processed_count, total_tracks, added_count, len(not_found), cancelled=cancelled)

    log("\nГотово.")
    log(f"Плейлист: {playlist_url}")
    log(f"Обработано: {processed_count}/{total_tracks}")
    log(f"Добавлено: {added_count}")
    log(f"Не найдено: {len(not_found)}")
    if cancelled:
        log("Статус: остановлен пользователем.")

    not_found_path = ""
    if not_found:
        path = Path("not_found.txt")
        path.write_text("\n".join(not_found), encoding="utf-8")
        not_found_path = str(path.resolve())
        log(f"Сохранен список ненайденных треков: {not_found_path}")

    if state_path is not None:
        if cancelled:
            log(f"Сессия сохранена для продолжения: {state_path.resolve()}")
        elif state_path.exists():
            state_path.unlink()
            log("Session file removed (import completed).")

    return {
        "playlist_url": playlist_url,
        "added": added_count,
        "processed": processed_count,
        "total": total_tracks,
        "not_found": len(not_found),
        "cancelled": cancelled,
        "not_found_path": not_found_path,
        "resume_state_path": str(state_path.resolve()) if state_path is not None else "",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import tracks from text file into a dedicated Spotify playlist."
    )
    parser.add_argument(
        "input_file",
        type=Path,
        nargs="?",
        help="Path to text file with lines: Artist - Track",
    )
    parser.add_argument(
        "--playlist-name",
        default="Yandex Music Import",
        help="Name of playlist to create in Spotify",
    )
    parser.add_argument(
        "--public",
        action="store_true",
        help="Create playlist as public (default: private)",
    )
    args = parser.parse_args()
    args = _collect_missing_args(args)
    import_tracks(
        args.input_file,
        args.playlist_name,
        args.public,
        credentials=None,
        interactive_credentials=True,
        force_reauth=False,
        log=print,
    )


if __name__ == "__main__":
    main()
