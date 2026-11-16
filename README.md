# HARP

HARP is a page-level simulation framework for an HBM--HBF memory system. It provides a hardware configuration interface, page-request input, and an event-driven model of NAND reads, programming operations, transfer queues, and plane buffers.

## Hardware configuration

The default profile is defined in [`configs/hbm_hbf.json`](configs/hbm_hbf.json).

| Component | Parameter | Value |
|---|---|---:|
| HBM | Capacity | 80 GB |
| HBM | Read/write bandwidth | 3.35 TB/s |
| HBF | Stacks | 4 |
| HBF | Capacity per stack | 512 GiB |
| HBF | Total capacity | 2 TiB |
| HBF | NAND dies per stack | 16 |
| HBF | Planes per die | 96 |
| HBF | Total planes | 6,144 |
| HBF | Page size | 4 KiB |
| HBF | Read bandwidth per stack | 1.536 TB/s |
| HBF | Write bandwidth per stack | 0.084 TB/s |
| NAND | Page-read latency, `t_R` | 4.096 us |
| NAND | Page-program latency, `t_PROG` | 75 us |
| NAND | Read-cycle time, `t_RC` | 30 ns |

GB and TB use decimal units; GiB, TiB, and KiB use binary units. In particular, the HBM capacity is 80,000,000,000 bytes. The HBF topology has 1,536 planes per stack. Plane-buffer count is a separate simulator setting, with a default of two buffers per plane.

## Installation

Python 3.10 or later is required. The framework has no third-party runtime dependencies.

```bash
python -m pip install -e .
```

## Running a simulation

```bash
harp hardware --config configs/hbm_hbf.json
harp simulate --config configs/hbm_hbf.json \
  --requests examples/requests.jsonl \
  --out results/example.json
```

Each input line describes one page request:

```json
{"request_id": 0, "kind": "read", "page": 0, "arrival_s": 0.0}
```

Requests may specify `payload_bytes` for transfers shorter than a page. NAND access time remains page-granular. The output contains stage timings, request completion times, buffer occupancy intervals, and resource-consistency checks. Existing output files are not overwritten.

## Execution model

A read follows `page sense -> stack transfer -> HBM write`. A write follows `HBM read -> stack transfer -> page program`. Use `--hbf-only` to omit the HBM staging stages.

The reference backend uses ready-order FIFO arbitration. NAND operations serialize on a plane, while different planes can operate concurrently. Reads and writes share one transfer resource per stack, using their respective bandwidths. HBM transfers share one bandwidth resource. Read buffers remain occupied until the stack transfer finishes; write buffers remain occupied through programming.

Page addresses are striped across stacks and planes. Die identifiers describe the resource hierarchy; block layout, erase operations, and die-level command restrictions are not modeled. The backend uses aggregate stack bandwidth instead of a byte-level NAND bus. `t_RC` is retained in the hardware profile for interface-level backends and is not added as a second transfer delay.

HBM capacity is a configuration limit, not an application memory-allocation model. The framework does not generate application workloads or implement application-specific management policies.

## Python interface

```python
from harp import HardwareConfig, PageRequest
from harp.simulator import Simulator

hardware = HardwareConfig()
requests = [
    PageRequest(request_id=0, kind="read", page=0),
    PageRequest(request_id=1, kind="write", page=1),
]
result = Simulator(hardware).run(requests)
print(result.duration_s)
```

Applications can produce requests through the `RequestSource` protocol in `harp.io`. The simulator accepts any iterable of `PageRequest` objects.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Code availability

This repository currently provides the HBF simulation framework. We will release the complete code upon publication of the paper.
