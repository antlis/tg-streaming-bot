"""Custom extractor plugins: user-supplied resolvers for pages yt-dlp can't
(or shouldn't) handle out of the box. Same contract as tg-media-bot's and
tg-mpv-bot's.

A plugin is a plain ``.py`` file in ``PLUGIN_DIR`` exposing two callables::

    def match(url: str) -> bool:
        '''Return True for URLs this plugin knows how to resolve.'''

    async def resolve(url: str):   # may also be a plain (sync) def
        '''Turn the page URL into a media URL ffmpeg can stream.'''

``resolve`` may return a media URL ``str``, a ``(media_url, referer)`` tuple, a
:class:`ResolveResult`, or ``None`` to fall through to the next plugin / yt-dlp
/ the headless-browser fallback. Plugins run in alphabetical order; the first
whose ``match`` is true and whose ``resolve`` yields a URL wins. A plugin that
raises is logged and skipped — it can never break playback.
"""

import importlib.util
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)


@dataclass
class ResolveResult:
    """What a plugin's ``resolve`` hands back for ffmpeg to stream."""

    media_url: str
    referer: Optional[str] = None
    headers: dict = field(default_factory=dict)


@dataclass
class _Plugin:
    name: str
    match: Callable[[str], bool]
    resolve: Callable


def load_plugins(plugin_dir: str) -> list:
    """Import every ``*.py`` in *plugin_dir* that exposes ``match`` + ``resolve``.

    Files starting with ``_`` are skipped (helpers). A file that fails to
    import is logged and ignored rather than taking the bot down.
    """
    plugins: list = []
    directory = Path(plugin_dir) if plugin_dir else None
    if directory is None or not directory.is_dir():
        return plugins
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"tgsb_plugin_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            match = getattr(module, "match", None)
            resolve = getattr(module, "resolve", None)
            if not callable(match) or not callable(resolve):
                log.warning("Plugin %s skipped: no match()/resolve()", path.name)
                continue
            plugins.append(_Plugin(name=path.stem, match=match, resolve=resolve))
            log.info("Loaded extractor plugin %s", path.stem)
        except Exception as exc:  # a bad plugin must not stop the bot
            log.warning("Plugin %s failed to load: %s", path.name, exc)
    return plugins


def _normalize(result, page_url: str) -> Optional[ResolveResult]:
    """Coerce a plugin's return value into a ResolveResult (or None)."""
    if result is None:
        return None
    if isinstance(result, ResolveResult):
        if not result.referer:
            result.referer = page_url
        return result
    if isinstance(result, str):
        return ResolveResult(media_url=result, referer=page_url)
    if isinstance(result, (tuple, list)) and result:
        referer = result[1] if len(result) > 1 and result[1] else page_url
        return ResolveResult(media_url=result[0], referer=referer)
    return None


async def resolve_with_plugins(url: str, plugins: list) -> Optional[ResolveResult]:
    """First matching plugin that yields a media URL wins; broken plugins skipped."""
    for plugin in plugins:
        try:
            if not plugin.match(url):
                continue
        except Exception as exc:
            log.warning("Plugin %s match() raised: %s", plugin.name, exc)
            continue
        try:
            log.info("Trying extractor plugin %s", plugin.name)
            out = plugin.resolve(url)
            if inspect.isawaitable(out):
                out = await out
            resolved = _normalize(out, url)
            if resolved and resolved.media_url:
                log.info("Extractor plugin %s resolved a media URL", plugin.name)
                return resolved
        except Exception as exc:
            log.warning("Plugin %s resolve() raised: %s", plugin.name, exc)
    return None
