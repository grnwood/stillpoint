from __future__ import annotations

from chromadb.telemetry.product import ProductTelemetryClient, ProductTelemetryEvent
from overrides import override


class NoopTelemetryClient(ProductTelemetryClient):
    """Minimal telemetry component that never sends events."""

    @override
    def capture(self, event: ProductTelemetryEvent) -> None:
        return
