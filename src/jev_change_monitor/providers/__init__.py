from jev_change_monitor.providers.base import JudgementProvider, ProviderResponse
from jev_change_monitor.providers.heuristic import HeuristicBaselineProvider
from jev_change_monitor.providers.jev_command import JevCommandProvider
from jev_change_monitor.providers.jev_http import JevHttpProvider

PROVIDERS = {
    "heuristic": HeuristicBaselineProvider,
    "jev-command": JevCommandProvider,
    "jev-http": JevHttpProvider,
}


def get_provider(name: str) -> JudgementProvider:
    try:
        return PROVIDERS[name]()
    except KeyError as exc:
        raise ValueError(f"unknown provider {name!r}; choose from {sorted(PROVIDERS)}") from exc


def provider_for_mode(mode: str) -> list[JudgementProvider]:
    """Providers available for a requested mode ('deterministic' or 'live')."""
    if mode == "deterministic":
        return [HeuristicBaselineProvider()]
    if mode == "live":
        return [JevCommandProvider(), JevHttpProvider()]
    raise ValueError(f"unknown mode {mode!r}")