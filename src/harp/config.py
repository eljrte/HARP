"""Hardware profile and striped page-address mapping."""
from __future__ import annotations
from dataclasses import asdict, dataclass, fields
from collections.abc import Mapping
import json
import math
from numbers import Integral, Real
from pathlib import Path


def integer(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return int(value)


def positive(value, name):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be finite and positive')
    return float(value)


@dataclass(frozen=True)
class PhysicalAddress:
    stack: int
    die: int
    plane: int
    row: int


@dataclass(frozen=True)
class HardwareConfig:
    hbm_capacity_bytes: int = 80_000_000_000
    hbm_bandwidth_Bps: float = 3_350_000_000_000
    hbf_stacks: int = 4
    hbf_capacity_bytes_per_stack: int = 512 * 1024**3
    dies_per_stack: int = 16
    planes_per_die: int = 96
    page_bytes: int = 4096
    hbf_read_bandwidth_Bps_per_stack: float = 1_536_000_000_000
    hbf_write_bandwidth_Bps_per_stack: float = 84_000_000_000
    t_read_s: float = 4.096e-6
    t_program_s: float = 75e-6
    plane_buffers: int = 2

    def __post_init__(self):
        for name in ['hbm_capacity_bytes', 'hbf_stacks', 'hbf_capacity_bytes_per_stack',
                     'dies_per_stack', 'planes_per_die', 'page_bytes', 'plane_buffers']:
            integer(getattr(self, name), name, 1)
        for name in ['hbm_bandwidth_Bps', 'hbf_read_bandwidth_Bps_per_stack',
                     'hbf_write_bandwidth_Bps_per_stack', 't_read_s', 't_program_s']:
            positive(getattr(self, name), name)
        if self.hbf_capacity_bytes_per_stack % self.page_bytes:
            raise ValueError('stack capacity must contain an integer number of pages')

    @property
    def planes_per_stack(self):
        return self.dies_per_stack * self.planes_per_die

    @property
    def total_planes(self):
        return self.hbf_stacks * self.planes_per_stack

    @property
    def hbf_total_capacity_bytes(self):
        return self.hbf_stacks * self.hbf_capacity_bytes_per_stack

    @property
    def total_pages(self):
        return self.hbf_total_capacity_bytes // self.page_bytes

    def address(self, page):
        page = integer(page, 'page', 0)
        if page >= self.total_pages:
            raise ValueError('page address exceeds HBF capacity')
        stack = page % self.hbf_stacks
        local = page // self.hbf_stacks
        flat_plane = local % self.planes_per_stack
        return PhysicalAddress(stack, flat_plane // self.planes_per_die,
                               flat_plane % self.planes_per_die, local // self.planes_per_stack)

    def physical_plane_id(self, page):
        a = self.address(page)
        return a.stack*self.planes_per_stack + a.die*self.planes_per_die + a.plane

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, Mapping):
            raise ValueError('hardware configuration must be a JSON object')
        valid = {f.name for f in fields(cls)}
        unknown = set(data)-valid
        if unknown:
            raise ValueError('unknown hardware fields: '+', '.join(sorted(unknown)))
        return cls(**data)

    @classmethod
    def from_json(cls, path):
        with Path(path).open(encoding='utf-8') as f:
            return cls.from_dict(json.load(f))
