"""JSONL input and simulation-result output."""
from __future__ import annotations
from dataclasses import fields
import json
from pathlib import Path
from typing import Iterator, Protocol
from .config import HardwareConfig
from .requests import PageRequest


class RequestSource(Protocol):
    """Applications supply page requests through this interface."""
    def __iter__(self) -> Iterator[PageRequest]: ...


def load_requests(path: str | Path, config: HardwareConfig) -> list[PageRequest]:
    allowed = {field.name for field in fields(PageRequest)}
    result = []
    with Path(path).open(encoding='utf-8') as f:
        for line_number, line in enumerate(f, 1):
            if not line.strip():
                continue
            data = json.loads(line)
            if not isinstance(data, dict) or set(data)-allowed:
                raise ValueError(f'line {line_number}: invalid request fields')
            try:
                request = PageRequest(**data)
            except TypeError as exc:
                raise ValueError(f'line {line_number}: incomplete request') from exc
            request.validate(config)
            result.append(request)
    return result


def write_json(path: str | Path, data: dict):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(data, f, indent=2, allow_nan=False)
        f.write('\n')
