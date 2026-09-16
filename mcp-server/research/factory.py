from __future__ import annotations

import importlib
import os

from .adapter import SearchProvider

_PROVIDERS = {
    "serpapi": (".providers.serpapi_provider", "SerpAPIProvider"),
    "tavily": (".providers.tavily_provider", "TavilyProvider"),
}


def get_provider(name: str | None = None) -> SearchProvider:
    """The single place that knows both provider classes exist.

    tools.py never imports SerpAPIProvider or TavilyProvider directly -- only
    this factory does. Selection order: explicit `name` argument, else the
    SEARCH_PROVIDER env var, else "tavily". An unrecognized/missing choice
    raises rather than silently falling back, per the capstone guide's "do not
    silently claim SerpAPI support when the implementation only calls Tavily."
    """
    resolved = (name or os.environ.get("SEARCH_PROVIDER") or "tavily").lower()
    if resolved not in _PROVIDERS:
        raise ValueError(
            f"Unknown SEARCH_PROVIDER '{resolved}'; expected one of {sorted(_PROVIDERS)}"
        )

    module_path, class_name = _PROVIDERS[resolved]
    module = importlib.import_module(module_path, package=__package__)
    provider_class = getattr(module, class_name)
    return provider_class()
