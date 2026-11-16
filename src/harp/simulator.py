"""Generic page-I/O discrete-event model with finite plane buffers.

Only ready work can acquire a resource. There are no future reservations, cache
policies, inference kernels, or optimized request-selection algorithms here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import heapq
import math
from numbers import Integral
from typing import Iterable

from .config import HardwareConfig
from .requests import PageRequest


@dataclass(frozen=True)
class TraceEvent:
    request_id: int
    operation: str
    stage: str
    resource: str
    start_s: float
    end_s: float
    stack: int
    die: int
    plane: int
    payload_bytes: int


@dataclass(frozen=True)
class BufferInterval:
    request_id: int
    operation: str
    physical_plane: int
    start_s: float
    end_s: float


@dataclass(frozen=True)
class Completion:
    request_id: int
    operation: str
    arrival_s: float
    completion_s: float
    latency_s: float
    payload_bytes: int


@dataclass(frozen=True)
class SimulationResult:
    start_s: float
    end_s: float
    duration_s: float
    completed_requests: int
    read_requests: int
    write_requests: int
    payload_bytes: int
    read_payload_bytes: int
    write_payload_bytes: int
    events: tuple[TraceEvent, ...]
    buffer_intervals: tuple[BufferInterval, ...]
    completions: tuple[Completion, ...]
    resource_busy_s: dict[str, float]
    certificate: dict

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class _Job:
    request: PageRequest
    address: object
    physical_plane: int
    payload: int
    buffer_start: float | None = None


class Simulator:
    """FIFO-ready page reads/writes over explicit NAND and transfer resources.

    Reads: page sense -> shared stack bus -> shared HBM write port.
    Writes: shared HBM read port -> shared stack bus -> page program.

    The stack bus is half-duplex: reads and writes share one resource, with
    direction-dependent bandwidth. One HBM bandwidth resource is shared by
    reads and writes. Set ``include_hbm=False`` to model the HBF portion alone.
    Die identifiers are address hierarchy, not an additional shared-bus model.
    ``t_rc_s`` is retained by the hardware profile but not added to latency:
    channel width and command/data-cycle counts are not supplied by the profile.
    """

    def __init__(self, config: HardwareConfig, *, include_hbm: bool = True):
        if not isinstance(include_hbm, bool):
            raise ValueError('include_hbm must be a boolean')
        self.config = config
        self.include_hbm = include_hbm

    def run(self, requests: Iterable[PageRequest], *, hbm_reserved_bytes: int = 0) -> SimulationResult:
        """Run a finite, arrival-timed page-I/O episode.

        ``hbm_reserved_bytes`` is a caller-declared allocation checked against
        the capacity. This framework does not infer allocations from application
        state, or claim to measure allocator peaks or persistent storage.
        """
        cfg = self.config
        if (isinstance(hbm_reserved_bytes, bool) or not isinstance(hbm_reserved_bytes, Integral)
                or not 0 <= hbm_reserved_bytes <= cfg.hbm_capacity_bytes):
            raise ValueError('hbm_reserved_bytes must be an integer within the HBM capacity')
        jobs: list[_Job] = []
        ids: set[int] = set()
        for req in requests:
            if not isinstance(req, PageRequest):
                raise TypeError('requests must contain PageRequest objects')
            req.validate(cfg)
            if req.request_id in ids:
                raise ValueError('request IDs must be unique')
            ids.add(req.request_id)
            jobs.append(_Job(req, cfg.address(req.page), cfg.physical_plane_id(req.page), req.normalized_payload(cfg)))

        # The calendar contains completed stages and arrivals only. A ready
        # stage is not placed on a resource until that resource is free now.
        calendar: list[tuple[float, int, str, int, str]] = []
        serial = 0
        for i, job in enumerate(jobs):
            heapq.heappush(calendar, (float(job.request.arrival_s), serial, 'arrival', i, ''))
            serial += 1
        ready: list[tuple[int, int, str]] = []
        ready_serial = 0
        busy: set[str] = set()
        buffers: dict[int, int] = {}
        peak_buffers = 0
        events: list[TraceEvent] = []
        intervals: list[BufferInterval] = []
        completed: dict[int, float] = {}

        def enqueue(i: int, stage: str) -> None:
            nonlocal ready_serial
            ready.append((ready_serial, i, stage))
            ready_serial += 1

        def release_buffer(job: _Job, now: float) -> None:
            if job.buffer_start is None or buffers.get(job.physical_plane, 0) < 1:
                raise AssertionError('buffer release without a reservation')
            intervals.append(BufferInterval(job.request.request_id, job.request.kind,
                                            job.physical_plane, job.buffer_start, now))
            buffers[job.physical_plane] -= 1
            job.buffer_start = None

        def stage_spec(job: _Job, stage: str) -> tuple[str, float, bool, int]:
            if stage == 'read_sense':
                return f'nand:{job.physical_plane}', cfg.t_read_s, True, cfg.page_bytes
            if stage == 'program':
                return f'nand:{job.physical_plane}', cfg.t_program_s, False, cfg.page_bytes
            if stage == 'read_transfer':
                return f'stack:{job.address.stack}', job.payload / cfg.hbf_read_bandwidth_Bps_per_stack, False, job.payload
            if stage == 'write_transfer':
                return f'stack:{job.address.stack}', job.payload / cfg.hbf_write_bandwidth_Bps_per_stack, True, job.payload
            if stage in ('hbm_read', 'hbm_write'):
                return 'hbm', job.payload / cfg.hbm_bandwidth_Bps, False, job.payload
            raise AssertionError('unknown pipeline stage')

        while calendar or ready:
            if not calendar:
                raise AssertionError('ready work cannot make progress without an active resource')
            now = calendar[0][0]
            while calendar and calendar[0][0] == now:
                _, _, kind, i, stage = heapq.heappop(calendar)
                job = jobs[i]
                if kind == 'arrival':
                    enqueue(i, 'read_sense' if job.request.kind == 'read' else
                            ('hbm_read' if self.include_hbm else 'write_transfer'))
                    continue
                resource, _, _, _ = stage_spec(job, stage)
                if resource not in busy:
                    raise AssertionError('resource completion without an active stage')
                busy.remove(resource)
                if stage == 'read_sense':
                    enqueue(i, 'read_transfer')
                elif stage == 'read_transfer':
                    release_buffer(job, now)
                    if self.include_hbm:
                        enqueue(i, 'hbm_write')
                    else:
                        completed[i] = now
                elif stage == 'hbm_write':
                    completed[i] = now
                elif stage == 'hbm_read':
                    enqueue(i, 'write_transfer')
                elif stage == 'write_transfer':
                    enqueue(i, 'program')
                elif stage == 'program':
                    release_buffer(job, now)
                    completed[i] = now

            # Scan in ready order, skipping entries blocked on another resource.
            # This avoids artificial head-of-line blocking across planes/stacks.
            pending: list[tuple[int, int, str]] = []
            for order, i, stage in ready:
                job = jobs[i]
                resource, duration, needs_buffer, payload = stage_spec(job, stage)
                if (resource in busy or
                        (needs_buffer and buffers.get(job.physical_plane, 0) >= cfg.plane_buffers)):
                    pending.append((order, i, stage))
                    continue
                end = now + duration
                if not math.isfinite(end) or (duration > 0 and end <= now):
                    raise ValueError('time magnitude prevents a representable finite stage duration')
                if needs_buffer:
                    if job.buffer_start is not None:
                        raise AssertionError('request already owns a plane buffer')
                    job.buffer_start = now
                    buffers[job.physical_plane] = buffers.get(job.physical_plane, 0) + 1
                    peak_buffers = max(peak_buffers, buffers[job.physical_plane])
                busy.add(resource)
                events.append(TraceEvent(job.request.request_id, job.request.kind, stage, resource,
                                         now, end, job.address.stack, job.address.die,
                                         job.address.plane, payload))
                heapq.heappush(calendar, (end, serial, 'completion', i, stage))
                serial += 1
            ready = pending

        if busy or any(buffers.values()) or len(completed) != len(jobs):
            raise AssertionError('unfinished resources, buffers, or requests')
        start = min((float(j.request.arrival_s) for j in jobs), default=0.0)
        end = max(completed.values(), default=start)
        completions = tuple(Completion(j.request.request_id, j.request.kind, float(j.request.arrival_s),
                                       completed[i], completed[i]-float(j.request.arrival_s), j.payload)
                            for i, j in enumerate(jobs))
        events_out = tuple(events)
        intervals_out = tuple(intervals)
        certificate, busy_s = self._audit(jobs, events_out, intervals_out, completions,
                                          peak_buffers, hbm_reserved_bytes)
        reads = sum(j.request.kind == 'read' for j in jobs)
        read_bytes = sum(j.payload for j in jobs if j.request.kind == 'read')
        write_bytes = sum(j.payload for j in jobs if j.request.kind == 'write')
        return SimulationResult(start, end, end-start, len(jobs), reads, len(jobs)-reads,
                                read_bytes+write_bytes, read_bytes, write_bytes, events_out,
                                intervals_out, completions, busy_s, certificate)

    def _audit(self, jobs: list[_Job], events: tuple[TraceEvent, ...],
               intervals: tuple[BufferInterval, ...], completions: tuple[Completion, ...],
               observed_peak: int, hbm_reserved_bytes: int) -> tuple[dict, dict[str, float]]:
        cfg = self.config
        by_resource: dict[str, list[TraceEvent]] = {}
        by_request: dict[int, dict[str, TraceEvent]] = {}
        busy_s: dict[str, float] = {}
        for e in events:
            by_resource.setdefault(e.resource, []).append(e)
            if e.stage in by_request.setdefault(e.request_id, {}):
                raise AssertionError('duplicate pipeline stage')
            by_request[e.request_id][e.stage] = e
            busy_s[e.resource] = busy_s.get(e.resource, 0.0) + e.end_s-e.start_s
        nonoverlap = True
        for rows in by_resource.values():
            rows = sorted(rows, key=lambda e: (e.start_s, e.end_s))
            if any(b.start_s < a.end_s for a, b in zip(rows, rows[1:])):
                nonoverlap = False
        precedence = True
        duration_valid = True
        for j in jobs:
            chain = ['read_sense', 'read_transfer'] if j.request.kind == 'read' else ['write_transfer', 'program']
            if self.include_hbm:
                if j.request.kind == 'read':
                    chain.append('hbm_write')
                else:
                    chain.insert(0, 'hbm_read')
            rows = by_request.get(j.request.request_id, {})
            if set(rows) != set(chain):
                precedence = False
                continue
            if rows[chain[0]].start_s < j.request.arrival_s:
                precedence = False
            if any(rows[a].end_s > rows[b].start_s for a, b in zip(chain, chain[1:])):
                precedence = False
            for name in chain:
                e = rows[name]
                expected = (cfg.t_read_s if name == 'read_sense' else cfg.t_program_s if name == 'program'
                            else j.payload/cfg.hbf_read_bandwidth_Bps_per_stack if name == 'read_transfer'
                            else j.payload/cfg.hbf_write_bandwidth_Bps_per_stack if name == 'write_transfer'
                            else j.payload/cfg.hbm_bandwidth_Bps)
                if not math.isclose(e.end_s-e.start_s, expected, rel_tol=1e-9, abs_tol=1e-15):
                    duration_valid = False
        edges: dict[int, list[tuple[float, int]]] = {}
        buffer_valid = len(intervals) == len(jobs)
        for interval in intervals:
            if interval.end_s < interval.start_s:
                buffer_valid = False
            edges.setdefault(interval.physical_plane, []).extend([(interval.start_s, 1), (interval.end_s, -1)])
            rows = by_request[interval.request_id]
            first, last = (('read_sense', 'read_transfer') if interval.operation == 'read' else
                           ('write_transfer', 'program'))
            if interval.start_s != rows[first].start_s or interval.end_s != rows[last].end_s:
                buffer_valid = False
        audit_peak = 0
        for row in edges.values():
            active = 0
            for _, change in sorted(row):  # Release before acquisition at an equal timestamp.
                active += change
                audit_peak = max(audit_peak, active)
                if not 0 <= active <= cfg.plane_buffers:
                    buffer_valid = False
            if active:
                buffer_valid = False
        buffer_valid &= audit_peak == observed_peak
        transfer_bytes = sum(e.payload_bytes for e in events if e.stage in ('read_transfer', 'write_transfer'))
        hbm_bytes = sum(e.payload_bytes for e in events if e.stage in ('hbm_read', 'hbm_write'))
        total_bytes = sum(j.payload for j in jobs)
        nand_counts = (sum(e.stage == 'read_sense' for e in events) == sum(j.request.kind == 'read' for j in jobs)
                       and sum(e.stage == 'program' for e in events) == sum(j.request.kind == 'write' for j in jobs))
        checks = dict(all_requests_completed=len(completions)==len(jobs), resource_non_overlap=nonoverlap,
                      precedence_valid=precedence, stage_durations_valid=duration_valid,
                      buffer_bound=bool(buffer_valid), bytes_conserved=transfer_bytes==total_bytes,
                      hbm_bytes_conserved=hbm_bytes==(total_bytes if self.include_hbm else 0),
                      nand_operation_counts=nand_counts, declared_hbm_budget_valid=hbm_reserved_bytes<=cfg.hbm_capacity_bytes)
        failures = sum(not ok for ok in checks.values())
        certificate = dict(violations=failures, **checks, max_buffers_observed=audit_peak,
                           declared_hbm_reserved_bytes=int(hbm_reserved_bytes), hbm_capacity_bytes=cfg.hbm_capacity_bytes,
                           hbm_stage_included=self.include_hbm, stack_bus='shared half-duplex',
                           t_rc_explicitly_timed=False)
        if failures:
            raise AssertionError('simulation resource certificate failed')
        return certificate, busy_s
