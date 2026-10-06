from __future__ import annotations


def init_telemetry(enabled: bool) -> None:
    """Patch PyMongo (and later FastAPI) for Datadog APM. Call once at process startup."""
    if not enabled:
        return
    from ddtrace import patch_all

    patch_all()
