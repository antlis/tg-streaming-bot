import re
import logging
import urllib.request
import json
from time import time

log = logging.getLogger(__name__)

_BASE = "https://www.southparkstudios.com"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_CACHE_TTL = 3600  # 1 hour

# In-memory cache: {show_id: {"seasons": [...], "episodes": {season_num: [...]}, "ts": float}}
_CACHE = {}


def _fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read().decode())


def _deep_find_episodes(d, depth=0):
    """Recursively find episode URL entries in the nested React SSR JSON."""
    if depth > 15:
        return
    if isinstance(d, dict):
        url = d.get("url", "")
        if isinstance(url, str) and url.startswith("/episodes/"):
            meta = d.get("meta", {})
            header = meta.get("header", {}).get("title", {}).get("text", "")
            sub = meta.get("subHeader", "")
            desc = meta.get("description", "")
            date = meta.get("date", "")
            media = d.get("media", {})
            duration = media.get("duration", "")
            img = media.get("image", {}).get("url", "")
            m = re.match(r"S(\d+)\s*[•·]\s*E(\d+)", header)
            season = int(m.group(1)) if m else 0
            episode = int(m.group(2)) if m else 0
            yield {
                "title": sub or url.split("/")[-1].replace("-", " ").title(),
                "url": url,
                "season": season,
                "episode": episode,
                "duration": duration,
                "description": desc,
                "thumbnail": img,
                "date": date,
            }
        for v in d.values():
            yield from _deep_find_episodes(v, depth + 1)
    elif isinstance(d, list):
        for item in d:
            yield from _deep_find_episodes(item, depth + 1)


def _deep_find_seasons(d, depth=0):
    """Recursively find season URL entries in the nested React SSR JSON."""
    if depth > 15:
        return
    if isinstance(d, dict):
        url = d.get("url", "")
        if isinstance(url, str) and re.match(r"/seasons/south-park/.+/season-\d+", url):
            meta = d.get("meta", {})
            sub = meta.get("subHeader", "")
            m = re.search(r"season-(\d+)", url)
            season_num = int(m.group(1)) if m else 0
            yield {"name": sub or f"Season {season_num}", "url": url, "season": season_num}
        for v in d.values():
            yield from _deep_find_seasons(v, depth + 1)
    elif isinstance(d, list):
        for item in d:
            yield from _deep_find_seasons(item, depth + 1)


def _dedup_seasons(gen):
    seen = set()
    for s in gen:
        if s["season"] not in seen:
            seen.add(s["season"])
            yield s


def _scrape_seasons(show_id="south-park"):
    """Fetch the list of seasons for a show."""
    now = time()
    cached = _CACHE.get(show_id)
    if cached and now - cached["ts"] < _CACHE_TTL and "seasons" in cached:
        return cached["seasons"]

    url = f"{_BASE}/seasons/{show_id}?json=true"
    try:
        data = _fetch_json(url)
    except Exception as e:
        log.warning("scrape_seasons failed: %s", e)
        return []

    seasons = list(_dedup_seasons(_deep_find_seasons(data)))
    # Season 1 is inline on the main page (no separate link)
    if seasons and seasons[0]["season"] > 1:
        seasons.insert(0, {"name": "Season 1", "url": "/seasons/south-park", "season": 1})
    elif not seasons:
        seasons = [{"name": "Season 1", "url": "/seasons/south-park", "season": 1}]

    if show_id not in _CACHE:
        _CACHE[show_id] = {"ts": now}
    _CACHE[show_id]["seasons"] = seasons
    return seasons


def _scrape_episodes(show_id, season_num):
    """Fetch the episode list for a specific season."""
    now = time()
    cached = _CACHE.get(show_id, {})
    eps = cached.get("episodes", {}).get(season_num)
    if eps is not None and now - cached.get("ts", 0) < _CACHE_TTL:
        return eps

    if season_num == 1:
        eps = _scrape_episodes_from_main(show_id)
    else:
        seasons = _scrape_seasons(show_id)
        season_url = None
        for s in seasons:
            if s["season"] == season_num:
                season_url = s["url"]
                break
        if not season_url:
            return []
        full_url = f"{_BASE}{season_url}?json=true"
        try:
            data = _fetch_json(full_url)
        except Exception as e:
            log.warning("scrape_episodes failed for %s S%d: %s", show_id, season_num, e)
            return []
        eps = list(_deep_find_episodes(data))

    if show_id not in _CACHE:
        _CACHE[show_id] = {"ts": now}
    _CACHE[show_id].setdefault("episodes", {})[season_num] = eps
    return eps


def _scrape_episodes_from_main(show_id):
    """Scrape episodes from the main seasons page (used for season 1)."""
    url = f"{_BASE}/seasons/{show_id}?json=true"
    try:
        data = _fetch_json(url)
    except Exception as e:
        log.warning("scrape_episodes_from_main failed: %s", e)
        return []
    return list(_deep_find_episodes(data))


def _show_name(show_id):
    return {
        "south-park": "South Park",
    }.get(show_id, show_id)
