from pathlib import Path
import json
import tempfile
import unittest
from harp import HardwareConfig, PageRequest
from harp.io import load_requests, write_json

ROOT = Path(__file__).resolve().parents[1]

class HardwareTests(unittest.TestCase):
    def test_default_profile_matches_file_and_requested_units(self):
        config = HardwareConfig.from_json(ROOT/'configs/hbm_hbf.json')
        self.assertEqual(config, HardwareConfig())
        self.assertEqual(config.hbm_capacity_bytes, 80*10**9)
        self.assertEqual(config.hbm_bandwidth_Bps, 3.35*10**12)
        self.assertEqual(config.hbf_total_capacity_bytes, 2*1024**4)
        self.assertEqual(config.planes_per_stack, 1536)
        self.assertEqual(config.total_planes, 6144)
        self.assertEqual(config.hbf_read_bandwidth_Bps_per_stack, 1_536_000_000_000)
        self.assertEqual(config.hbf_write_bandwidth_Bps_per_stack, 84_000_000_000)
        self.assertEqual(config.t_read_s, 4.096e-6)
        self.assertEqual(config.t_program_s, 75e-6)
        self.assertEqual(config.t_rc_s, 30e-9)

    def test_first_stripe_covers_every_plane_once(self):
        c=HardwareConfig()
        addresses=[c.address(i) for i in range(c.total_planes)]
        self.assertEqual(len({(a.stack,a.die,a.plane) for a in addresses}),6144)
        self.assertTrue(all(a.row==0 for a in addresses))
        self.assertEqual(c.address(6144).row,1)
        self.assertEqual(c.physical_plane_id(6144),c.physical_plane_id(0))
        self.assertEqual(c.address(c.total_pages-1).stack,3)
        for invalid in [-1,c.total_pages,True,1.5]:
            with self.assertRaises(ValueError):c.address(invalid)

    def test_invalid_profiles_rejected(self):
        for kwargs in [{'hbm_capacity_bytes':80e9},{'hbm_bandwidth_Bps':float('nan')},
                       {'hbf_stacks':False},{'planes_per_die':0},{'t_read_s':-1},
                       {'hbf_capacity_bytes_per_stack':4097},{'plane_buffers':0}]:
            with self.assertRaises(ValueError):HardwareConfig(**kwargs)
        with self.assertRaises(ValueError):HardwareConfig.from_dict({'unknown_setting':1})

    def test_request_contract(self):
        c=HardwareConfig()
        PageRequest(0,'read',0).validate(c)
        self.assertEqual(PageRequest(0,'read',0).normalized_payload(c),4096)
        self.assertEqual(PageRequest(0,'write',0,payload_bytes=1024).normalized_payload(c),1024)
        for r in [PageRequest(True,'read',0),PageRequest(-1,'read',0),PageRequest(0,'erase',0),
                  PageRequest(0,'read',-1),PageRequest(0,'read',0,arrival_s=float('inf')),
                  PageRequest(0,'read',0,payload_bytes=4097),PageRequest(0,'write',0,payload_bytes=0)]:
            with self.assertRaises(ValueError):r.validate(c)

    def test_jsonl_and_exclusive_output(self):
        c=HardwareConfig()
        rows=load_requests(ROOT/'examples/requests.jsonl',c)
        self.assertEqual(len(rows),6)
        self.assertEqual([r.kind for r in rows],['read','read','read','write','write','read'])
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'result.json';write_json(p,{'completed_requests':6})
            self.assertEqual(json.loads(p.read_text())['completed_requests'],6)
            with self.assertRaises(FileExistsError):write_json(p,{'completed_requests':0})
            bad=Path(t)/'bad.jsonl';bad.write_text('{"request_id":0,"kind":"read","page":0,"extra":1}')
            with self.assertRaises(ValueError):load_requests(bad,c)

    def test_nonobject_profile_and_nonboolean_option_rejected(self):
        from harp.simulator import Simulator
        for data in [None, [], 'hardware']:
            with self.assertRaises(ValueError):HardwareConfig.from_dict(data)
        for option in ['false', 0, 1, None]:
            with self.assertRaises(ValueError):Simulator(HardwareConfig(), include_hbm=option)

if __name__=='__main__':unittest.main()
