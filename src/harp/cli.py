"""Command-line entry point for hardware profiles and page-I/O simulation."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .config import HardwareConfig
from .io import load_requests, write_json


def main(argv=None):
    parser = argparse.ArgumentParser(description='HARP HBF simulation framework')
    parser.add_argument('--version', action='version', version='HARP 0.2.0')
    commands = parser.add_subparsers(dest='command', required=True)
    hardware = commands.add_parser('hardware', help='Show the effective hardware profile')
    hardware.add_argument('--config', type=Path)
    simulate = commands.add_parser('simulate', help='Simulate a JSONL stream of page requests')
    simulate.add_argument('--config', type=Path)
    simulate.add_argument('--requests', required=True, type=Path)
    simulate.add_argument('--out', required=True, type=Path)
    simulate.add_argument('--hbf-only', action='store_true', help='Exclude HBM staging transfers')
    args = parser.parse_args(argv)
    config = HardwareConfig.from_json(args.config) if args.config else HardwareConfig()
    if args.command == 'hardware':
        data = dict(hardware=config.as_dict(), derived=dict(planes_per_stack=config.planes_per_stack,
                    total_planes=config.total_planes, hbf_total_capacity_bytes=config.hbf_total_capacity_bytes,
                    aggregate_read_bandwidth_Bps=config.hbf_stacks*config.hbf_read_bandwidth_Bps_per_stack,
                    aggregate_write_bandwidth_Bps=config.hbf_stacks*config.hbf_write_bandwidth_Bps_per_stack))
        print(json.dumps(data, indent=2))
    else:
        from .simulator import Simulator
        if args.out.exists():
            raise FileExistsError('output already exists: '+str(args.out))
        result = Simulator(config, include_hbm=not args.hbf_only).run(load_requests(args.requests, config))
        data = dict(hardware=config.as_dict(), include_hbm=not args.hbf_only, simulation=result.as_dict())
        write_json(args.out, data)
        print(json.dumps(dict(completed_requests=result.completed_requests, duration_s=result.duration_s,
                              output=str(args.out))))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
