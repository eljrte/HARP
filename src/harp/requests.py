"""Public page-I/O request contract."""
from __future__ import annotations
from dataclasses import dataclass
import math
from numbers import Real
from .config import HardwareConfig, integer


@dataclass(frozen=True)
class PageRequest:
    request_id: int
    kind: str
    page: int
    arrival_s: float = 0.0
    payload_bytes: int | None = None

    def normalized_payload(self, config: HardwareConfig):
        payload = config.page_bytes if self.payload_bytes is None else integer(self.payload_bytes, 'payload_bytes', 1)
        if payload > config.page_bytes:
            raise ValueError('one page request cannot transfer more than page_bytes')
        return payload

    def validate(self, config: HardwareConfig):
        integer(self.request_id, 'request_id', 0)
        if self.kind not in ('read', 'write'):
            raise ValueError('kind must be read or write')
        config.address(self.page)
        if isinstance(self.arrival_s, bool) or not isinstance(self.arrival_s, Real) or not math.isfinite(self.arrival_s) or self.arrival_s < 0:
            raise ValueError('arrival_s must be finite and nonnegative')
        self.normalized_payload(config)
