"""Closed-form and independent resource checks for the page-I/O framework."""
from dataclasses import replace
import json
import math
import random
import unittest

from harp.config import HardwareConfig
from harp.requests import PageRequest
from harp.simulator import Simulator


class SimulatorTests(unittest.TestCase):
    def setUp(self):
        self.cfg = HardwareConfig()

    def assert_close(self, left, right):
        self.assertTrue(math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-15), (left, right))

    def test_single_read_includes_declared_hbm_transfer(self):
        c = self.cfg
        result = Simulator(c).run([PageRequest(0, 'read', 0)])
        self.assert_close(result.duration_s, c.t_read_s+c.page_bytes/c.hbf_read_bandwidth_Bps_per_stack+c.page_bytes/c.hbm_bandwidth_Bps)
        self.assertEqual([e.stage for e in result.events], ['read_sense','read_transfer','hbm_write'])
        self.assertEqual(result.completed_requests, 1)
        self.assertEqual(result.read_payload_bytes, 4096)
        self.assertEqual(result.certificate['violations'], 0)

    def test_single_write_has_transfer_then_full_page_program(self):
        c = self.cfg
        result = Simulator(c).run([PageRequest(4, 'write', 5)])
        expected = c.page_bytes/c.hbm_bandwidth_Bps+c.page_bytes/c.hbf_write_bandwidth_Bps_per_stack+c.t_program_s
        self.assert_close(result.duration_s, expected)
        self.assertEqual([e.stage for e in result.events], ['hbm_read','write_transfer','program'])
        self.assertEqual(result.write_payload_bytes, 4096)
        self.assertEqual(result.buffer_intervals[0].start_s, result.events[1].start_s)
        self.assertEqual(result.buffer_intervals[0].end_s, result.events[2].end_s)

    def test_partial_read_still_senses_a_complete_page(self):
        c = self.cfg
        result = Simulator(c, include_hbm=False).run([PageRequest(0, 'read', 0, payload_bytes=128)])
        self.assert_close(result.duration_s, c.t_read_s+128/c.hbf_read_bandwidth_Bps_per_stack)
        self.assertEqual(result.events[0].payload_bytes, c.page_bytes)
        self.assertEqual(result.events[1].payload_bytes, 128)
        self.assertFalse(result.certificate['hbm_stage_included'])

    def test_partial_write_still_programs_a_complete_page(self):
        c = self.cfg
        result = Simulator(c, include_hbm=False).run([PageRequest(0, 'write', 0, payload_bytes=64)])
        self.assert_close(result.duration_s, 64/c.hbf_write_bandwidth_Bps_per_stack+c.t_program_s)
        self.assertEqual(result.events[-1].payload_bytes, c.page_bytes)
        self.assertEqual(result.payload_bytes, 64)

    def test_distinct_planes_in_a_stack_share_one_transfer_bus(self):
        c = self.cfg
        requests = [PageRequest(0,'read',0), PageRequest(1,'read',c.hbf_stacks)]
        result = Simulator(c, include_hbm=False).run(requests)
        self.assert_close(result.duration_s, c.t_read_s+2*c.page_bytes/c.hbf_read_bandwidth_Bps_per_stack)
        senses = [e for e in result.events if e.stage=='read_sense']
        self.assertEqual([e.start_s for e in senses], [0,0])
        transfer = [e for e in result.events if e.stage=='read_transfer']
        self.assertEqual(transfer[0].resource, transfer[1].resource)
        self.assert_close(transfer[1].start_s, transfer[0].end_s)

    def test_different_stacks_transfer_in_parallel_but_hbm_is_shared(self):
        c = self.cfg
        requests = [PageRequest(0,'read',0),PageRequest(1,'read',1)]
        result = Simulator(c).run(requests)
        expected = c.t_read_s+c.page_bytes/c.hbf_read_bandwidth_Bps_per_stack+2*c.page_bytes/c.hbm_bandwidth_Bps
        self.assert_close(result.duration_s, expected)
        transfer = [e for e in result.events if e.stage=='read_transfer']
        self.assertEqual(transfer[0].start_s,transfer[1].start_s)
        self.assertNotEqual(transfer[0].resource,transfer[1].resource)
        hbm = [e for e in result.events if e.stage=='hbm_write']
        self.assertEqual(hbm[0].end_s,hbm[1].start_s)

    def test_one_and_two_buffers_have_expected_overlap(self):
        base = self.cfg
        # Adjacent rows on the same physical plane, not adjacent stripes.
        page2 = base.total_planes
        req = [PageRequest(0,'read',0),PageRequest(1,'read',page2)]
        one = Simulator(replace(base,plane_buffers=1),include_hbm=False).run(req)
        two = Simulator(replace(base,plane_buffers=2),include_hbm=False).run(req)
        dma = base.page_bytes/base.hbf_read_bandwidth_Bps_per_stack
        self.assert_close(one.duration_s,2*base.t_read_s+2*dma)
        self.assert_close(two.duration_s,2*base.t_read_s+dma)
        self.assertEqual(one.certificate['max_buffers_observed'],1)
        self.assertEqual(two.certificate['max_buffers_observed'],2)

    def test_same_plane_read_and_program_never_overlap(self):
        c = self.cfg
        req = [PageRequest(0,'write',0),PageRequest(1,'read',c.total_planes,arrival_s=1e-6)]
        result = Simulator(c).run(req)
        nand = sorted([e for e in result.events if e.resource=='nand:0'],key=lambda e:e.start_s)
        self.assertEqual(len(nand),2)
        self.assertLessEqual(nand[0].end_s,nand[1].start_s)
        self.assertEqual({e.stage for e in nand},{'program','read_sense'})

    def test_shared_stack_bus_is_half_duplex(self):
        c = replace(self.cfg,t_read_s=1e-12)
        result = Simulator(c).run([PageRequest(0,'read',0),PageRequest(1,'write',c.hbf_stacks)])
        transfers = sorted([e for e in result.events if e.resource=='stack:0'],key=lambda e:e.start_s)
        self.assertEqual({e.stage for e in transfers},{'read_transfer','write_transfer'})
        self.assertLessEqual(transfers[0].end_s,transfers[1].start_s)

    def test_no_future_transfer_reservation_blocks_an_earlier_ready_write(self):
        c = self.cfg
        req = [PageRequest(0,'read',0),PageRequest(1,'write',c.hbf_stacks,arrival_s=1e-6)]
        result = Simulator(c).run(req)
        transfer = {e.stage:e for e in result.events if e.stage in ('read_transfer','write_transfer')}
        self.assertLess(transfer['write_transfer'].start_s,transfer['read_transfer'].start_s)
        self.assertGreaterEqual(transfer['write_transfer'].start_s,1e-6)

    def test_later_arrival_is_not_scheduled_before_it_exists(self):
        result = Simulator(self.cfg).run([PageRequest(99,'read',0,arrival_s=.125)])
        self.assertEqual(result.start_s,.125)
        self.assertTrue(all(e.start_s>=.125 for e in result.events))
        self.assert_close(result.completions[0].latency_s,result.duration_s)

    def test_declared_hbm_budget_rejects_overallocation_and_nonintegers(self):
        sim = Simulator(self.cfg)
        for amount in [self.cfg.hbm_capacity_bytes+1,-1,1.1,True]:
            with self.assertRaises(ValueError):sim.run([],hbm_reserved_bytes=amount)
        r = sim.run([],hbm_reserved_bytes=self.cfg.hbm_capacity_bytes)
        self.assertEqual(r.certificate['declared_hbm_reserved_bytes'],80_000_000_000)

    def test_duplicate_request_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            Simulator(self.cfg).run([PageRequest(1,'read',0),PageRequest(1,'write',1)])

    def test_invalid_request_object_and_nonrepresentable_time_are_rejected(self):
        with self.assertRaises(TypeError):Simulator(self.cfg).run([{'kind':'read'}])
        with self.assertRaises(ValueError):Simulator(self.cfg).run([PageRequest(1,'read',0,arrival_s=1e30)])

    def test_empty_episode_and_result_json(self):
        r = Simulator(self.cfg).run([])
        self.assertEqual(r.duration_s,0)
        self.assertEqual(r.completed_requests,0)
        self.assertEqual(r.certificate['violations'],0)
        self.assertEqual(r.events,())
        json.dumps(r.as_dict(),allow_nan=False)

    def test_random_mixed_trace_independent_resource_audit(self):
        rng = random.Random(117)
        c = replace(self.cfg,t_read_s=1e-6,t_program_s=4e-6,
                    hbf_read_bandwidth_Bps_per_stack=4.096e8,
                    hbf_write_bandwidth_Bps_per_stack=2.048e8,hbm_bandwidth_Bps=1e9)
        requests = [PageRequest(i,'read' if rng.randrange(2) else 'write',
                                rng.randrange(3)*c.total_planes+rng.randrange(12),
                                arrival_s=rng.randrange(10)*1e-6,payload_bytes=rng.choice([128,1024,4096]))
                    for i in range(100)]
        result = Simulator(c).run(requests)
        resources = {}
        for e in result.events:resources.setdefault(e.resource,[]).append(e)
        for events in resources.values():
            rows = sorted(events,key=lambda e:e.start_s)
            for a,b in zip(rows,rows[1:]):self.assertLessEqual(a.end_s,b.start_s)
        for plane in {x.physical_plane for x in result.buffer_intervals}:
            intervals = [x for x in result.buffer_intervals if x.physical_plane==plane]
            points = sorted([(x.start_s,1) for x in intervals]+[(x.end_s,-1) for x in intervals])
            count = 0
            for _,change in points:
                count += change
                self.assertGreaterEqual(count,0);self.assertLessEqual(count,c.plane_buffers)
            self.assertEqual(count,0)
        self.assertEqual(result.completed_requests,len(requests))
        self.assertEqual(result.payload_bytes,sum(r.normalized_payload(c) for r in requests))
        self.assertEqual(result.certificate['violations'],0)
        self.assertEqual(len(result.buffer_intervals),len(requests))
        json.dumps(result.as_dict(),allow_nan=False)
        self.assertEqual(result.as_dict(),Simulator(c).run(requests).as_dict())

if __name__=='__main__':unittest.main()
