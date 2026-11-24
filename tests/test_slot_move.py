import unittest
from dataclasses import replace

from delivery.claim import claim
from delivery.publish import RejectedMeasurement, publish
from examples.slot_move_fixture import (
    SAMPLE, CommitRefused, HeldFrames, LocalPort, ReplyDropped, drop_reply, fail_before_commit,
)
from intake.adopt import receive_retained, release_movable
from intake.evidence import announce_origin, bind_preservation
from intake.operations import read_operation
from intake.receipts import find_receipt, receive_live_packet, receive_original
from intake.resume import resume_release
from recorder.capture import (
    move_capture, query_move, read_live_original, read_live_packet, record,
)
from recorder.original import CapturedOriginal
from recorder.release import ReleaseReplyLost, ReleasePermit, query_release, release_capture
from recorder.retained import retain_original
from recorder.slots import SlotBusy, get_occupancy
from review.batch import register_candidate
from review.decision import decide_batch
from tests.support import StationCase


class SlotMoveCase(StationCase):
    def setUp(self):
        super().setUp()
        self.device_port = LocalPort(b'test-only-local-key')
        self.station_port = LocalPort(b'test-only-local-key')
        self.mailbox = HeldFrames()

    def device_record(self, capture_id, *, slot, raw=SAMPLE['raw'], movable=True, source=None,
                      batch='B'):
        source = source or f'head-A/strip/{capture_id}/origin'
        return record(self.device, batch, capture_id, raw, source, slot_id=slot, movable=movable)

    def announce(self, origin):
        announce_origin(self.station, origin)
        register_candidate(self.station, 'B', origin.capture_id)
        return origin

    def announced(self, capture_id, *, slot='s1', raw=SAMPLE['raw'], source=None):
        return self.announce(self.device_record(capture_id, slot=slot, raw=raw, source=source))

    def packet(self, capture_id):
        return read_live_packet(self.device, capture_id, seal=self.device_port.seal)

    def receive(self, capture_id, frame=None):
        frame = self.packet(capture_id) if frame is None else frame
        return receive_live_packet(self.station, capture_id, frame,
                                   verify=self.station_port.verify)


class LiveReceiveTests(SlotMoveCase):
    def test_original_packet_keeps_first_read_location(self):
        self.announced('X')
        self.mailbox.hold('old', self.packet('X'))
        self.station.close()
        moved = move_capture(self.device, 'M-X', 'X', 's2')
        self.assertEqual((moved.from_slot, moved.from_generation, moved.to_slot), ('s1', 1, 's2'))
        z = self.device_record('Z', slot='s1', source=SAMPLE['sources']['Z'])
        self.station = self.connect_station()
        self.announce(z)
        receipt = self.receive('X', self.mailbox.take('old'))
        self.assertEqual(receipt.read_location, ('s1', 1))
        self.assertEqual(receipt.measurement.micrometers, 11)
        self.assertEqual(receipt.original.raw, SAMPLE['raw'])
        self.assertEqual(find_receipt(self.station, 'X'), receipt)
        # Another genuine packet of the same capture replays the first receipt.
        self.assertEqual(self.receive('X'), receipt)
        x_live = read_live_original(self.device, 'X')
        self.assertEqual((x_live.origin.slot_id, x_live.origin.generation), ('s1', 1))
        self.assertEqual(get_occupancy(self.device, 's2').capture_id, 'X')

    def test_later_packet_first_fixes_s2_location(self):
        self.announced('X')
        self.mailbox.hold('old', self.packet('X'))
        move_capture(self.device, 'M-X', 'X', 's2')
        self.announced('Z', slot='s1')
        receipt = self.receive('X')
        self.assertEqual(receipt.read_location, ('s2', 1))
        old = self.receive('X', self.mailbox.take('old'))
        self.assertEqual(old, receipt)
        self.assertEqual(old.read_location, ('s2', 1))

    def test_station_only_needs_its_own_database(self):
        self.announced('X')
        frame = self.packet('X')
        self.device.close()
        receipt = receive_live_packet(self.station, 'X', frame, verify=self.station_port.verify)
        self.assertEqual(receipt.read_location, ('s1', 1))
        self.assertEqual(receipt.measurement.micrometers, 11)

    def test_reject_wrong_binding_and_tamper(self):
        self.announced('X')
        self.announced('Z', slot='s2', source='head-A/strip/Z')
        x_frame = self.packet('X')
        with self.assertRaises(ValueError):
            self.receive('Z', x_frame)  # identical bytes, wrong capture binding
        self.assertIsNone(find_receipt(self.station, 'Z'))
        tampered = replace(x_frame, body=x_frame.body.replace(b'"slot_id":"s1"',
                                                             b'"slot_id":"s2"'))
        self.assertNotEqual(tampered.body, x_frame.body)
        with self.assertRaises(ValueError):
            self.receive('X', tampered)  # body changed, seal no longer matches
        self.assertIsNone(find_receipt(self.station, 'X'))
        with self.assertRaises(ValueError):
            receive_live_packet(self.station, 'X', x_frame,
                                verify=LocalPort(b'a-different-key').verify)
        forged = LocalPort(b'test-only-local-key').seal(x_frame.body.replace(
            b'eyJjb3VudCI6MTMsInF1YWxpdHkiOjB9', b'eyJjb3VudCI6MTcsInF1YWxpdHkiOjB9'))
        with self.assertRaises(ValueError):
            self.receive('X', forged)  # resealed but bytes no longer match announcement
        self.assertEqual(self.receive('X', x_frame).read_location, ('s1', 1))

    def test_old_entries_cannot_bypass_live_receipt(self):
        origin = self.announced('X')
        with self.assertRaises(ValueError):
            receive_original(self.station, CapturedOriginal(origin, SAMPLE['raw']))
        for method in ('station', 'device'):
            with self.subTest(method=method), self.assertRaises(ValueError):
                self.adopt('X', method=method)
        fixed = retain_original(self.device, 'X')
        with self.assertRaises(ValueError):
            receive_retained(self.station, self.device, 'X')
        with self.assertRaisesRegex(ValueError, 'live read receipt'):
            release_movable(self.station, self.device, 'X', 'R-X')
        with self.assertRaisesRegex(ValueError, 'live read receipt'):
            release_capture(self.device, 'R-X', ReleasePermit(origin, 'device', fixed.reference))
        self.assertEqual(get_occupancy(self.device, 's1').capture_id, 'X')
        self.assertIsNone(query_release(self.device, 'R-X'))

    def test_bare_receipt_binding_refused_for_movable(self):
        self.announced('X')
        receipt = self.receive('X')
        for method in ('device', 'station'):
            with self.subTest(method=method), self.assertRaises(ValueError):
                bind_preservation(self.station, receipt.original, method, 'X')

    def test_packet_gone_after_release(self):
        self.announced('X')
        receipt = self.receive('X')
        release_movable(self.station, self.device, 'X', 'R-X')
        self.assertIsNone(read_live_packet(self.device, 'X', seal=self.device_port.seal))
        self.assertIsNone(read_live_original(self.device, 'X'))
        self.assertEqual(receipt.read_location, ('s1', 1))

    def test_rejected_live_packet_still_receives_and_blocks_signing(self):
        self.announced('X', raw=b'{"count":13,"quality":1}',
                       source='head-A/strip/bad')
        receipt = self.receive('X')
        self.assertEqual(receipt.measurement.reason, 'bad_quality')
        self.assertIsNone(receipt.measurement.micrometers)
        self.assertEqual(receipt.read_location, ('s1', 1))
        self.assertEqual(decide_batch(self.station, 'B', 'X').status, 'rejected')
        with self.assertRaises(RejectedMeasurement):
            publish(self.station, 'B')
        self.assertIsNone(claim(self.station, 'B'))
        # Trusted bad pieces still release normally.
        self.assertEqual(release_movable(self.station, self.device, 'X', 'R-X').capture_id, 'X')

    def test_invalid_bodies_refused(self):
        self.announced('X')
        key_port = LocalPort(b'test-only-local-key')
        for body in (b'not json', b'[]', b'{}', b'{"capture_id":"X"}'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                receive_live_packet(self.station, 'X', key_port.seal(body),
                                    verify=self.station_port.verify)
        self.assertIsNone(find_receipt(self.station, 'X'))

    def test_read_live_packet_scoped_to_movable(self):
        self.device_record('Y', slot='s1', movable=False)
        with self.assertRaises(ValueError):
            read_live_packet(self.device, 'Y', seal=self.device_port.seal)
        self.assertIsNone(read_live_packet(self.device, 'missing',
                                           seal=self.device_port.seal))


class MoveTests(SlotMoveCase):
    def test_move_reuses_slots_and_keeps_identity(self):
        moved = self.device_record('X', slot='s1')
        outcome = move_capture(self.device, 'M-X', 'X', 's2')
        self.assertEqual((outcome.from_slot, outcome.from_generation, outcome.to_slot),
                         ('s1', 1, 's2'))
        z = self.device_record('Z', slot='s1')
        self.assertEqual((z.slot_id, z.generation), ('s1', moved.generation + 1))
        self.assertEqual(read_live_original(self.device, 'X').origin, moved)
        self.assertEqual(read_live_original(self.device, 'Z').origin, z)

    def test_replay_and_conflicts(self):
        origin = self.device_record('X', slot='s1')
        first = move_capture(self.device, 'M-X', 'X', 's2')
        self.device_record('Z', slot='s1')
        self.assertEqual(move_capture(self.device, 'M-X', 'X', 's2'), first)
        self.assertEqual(query_move(self.device, 'M-X'), first)
        with self.assertRaises(ValueError):
            move_capture(self.device, 'M-X', 'X', 's1')
        with self.assertRaises(ValueError):
            move_capture(self.device, 'M-X', 'Z', 's2')
        with self.assertRaises(ValueError):
            move_capture(self.device, 'M-Y', 'X', 's2')
        self.assertEqual(read_live_original(self.device, 'X').origin, origin)
        self.assertIsNone(query_move(self.device, 'M-Y'))

    def test_busy_target_leaves_original_and_does_not_consume_move(self):
        origin = self.device_record('X', slot='s1')
        z = self.announced('Z', slot='s2', source='head-A/strip/Z')
        with self.assertRaises(SlotBusy):
            move_capture(self.device, 'M-X', 'X', 's2')
        self.assertEqual(get_occupancy(self.device, 's1').capture_id, 'X')
        self.assertEqual(get_occupancy(self.device, 's2').capture_id, 'Z')
        self.assertIsNone(query_move(self.device, 'M-X'))
        # Clear s2 through Z's live receipt, then retry the same operation number.
        self.receive('Z')
        release_movable(self.station, self.device, 'Z', 'R-Z')
        self.assertIsNone(get_occupancy(self.device, 's2'))
        retried = move_capture(self.device, 'M-X', 'X', 's2')
        self.assertEqual((retried.to_slot, retried.to_generation), ('s2', 2))
        self.assertEqual(read_live_original(self.device, 'X').origin, origin)

    def test_before_commit_failure_keeps_original(self):
        origin = self.device_record('X', slot='s1')
        with self.assertRaises(CommitRefused):
            move_capture(self.device, 'M-X', 'X', 's2', before_commit=fail_before_commit)
        self.assertEqual(get_occupancy(self.device, 's1').capture_id, 'X')
        self.assertIsNone(get_occupancy(self.device, 's2'))
        self.assertIsNone(query_move(self.device, 'M-X'))
        self.assertEqual(move_capture(self.device, 'M-X', 'X', 's2').to_slot, 's2')
        self.assertEqual(read_live_original(self.device, 'X').origin, origin)

    def test_dropped_reply_resumes_by_original_number(self):
        self.device_record('X', slot='s1')
        with self.assertRaises(ReplyDropped):
            drop_reply(lambda: move_capture(self.device, 'M-X', 'X', 's2'))
        committed = query_move(self.device, 'M-X')
        self.assertIsNotNone(committed)
        self.reopen()
        self.assertEqual(query_move(self.device, 'M-X'), committed)
        self.assertEqual(move_capture(self.device, 'M-X', 'X', 's2'), committed)

    def test_non_movable_and_released_capture_refused(self):
        self.device_record('Y', slot='s1', movable=False)
        with self.assertRaises(ValueError):
            move_capture(self.device, 'M-Y', 'Y', 's2')
        self.announced('X', slot='s2')
        self.receive('X')
        release_movable(self.station, self.device, 'X', 'R-X')
        with self.assertRaises(ValueError):
            move_capture(self.device, 'M-X', 'X', 's1')

    def test_movable_marker_survives_replay(self):
        origin = self.device_record('X', slot='s1')
        self.assertEqual(self.device_record('X', slot='s1'), origin)
        self.assertTrue(origin.movable)
        with self.assertRaises(ValueError):
            self.device_record('X', slot='s1', movable=False)


class ReleaseTests(SlotMoveCase):
    def prepare(self):
        self.announced('X', source=SAMPLE['sources']['X'])
        self.mailbox.hold('old', self.packet('X'))
        move_capture(self.device, 'M-X', 'X', 's2')
        self.announced('Z', slot='s1', source=SAMPLE['sources']['Z'])

    def test_release_clears_current_slot_after_move(self):
        self.prepare()
        receipt = self.receive('X', self.mailbox.take('old'))
        self.assertEqual(receipt.read_location, ('s1', 1))
        outcome = release_movable(self.station, self.device, 'X', 'R-X')
        self.assertEqual((outcome.slot_id, outcome.generation), ('s2', 1))
        self.assertIsNone(read_live_original(self.device, 'X'))
        self.assertIsNotNone(read_live_original(self.device, 'Z'))

    def test_lost_reply_resume_does_not_clear_later_pieces(self):
        self.prepare()
        self.receive('X', self.mailbox.take('old'))
        with self.assertRaises(ReleaseReplyLost):
            release_movable(self.station, self.device, 'X', 'R-X', lose_reply=True)
        self.assertIsNone(read_operation(self.station, 'R-X').outcome)
        w = self.device_record('W', slot='s2', source='head-A/strip/W')
        self.reopen()
        outcome = resume_release(self.station, self.device, 'R-X')
        self.assertEqual(outcome, query_release(self.device, 'R-X'))
        self.assertEqual((outcome.capture_id, outcome.slot_id), ('X', 's2'))
        self.assertEqual(read_operation(self.station, 'R-X').outcome, outcome)
        self.assertIsNotNone(read_live_original(self.device, 'Z'))
        self.assertEqual(read_live_original(self.device, 'W').origin, w)
        self.assertEqual(release_movable(self.station, self.device, 'X', 'R-X'), outcome)
        self.assertIsNotNone(read_live_original(self.device, 'W'))

    def test_publish_and_offline_claim_keep_read_location(self):
        self.prepare()
        receipt = self.receive('X', self.mailbox.take('old'))
        release_movable(self.station, self.device, 'X', 'R-X')
        self.assertEqual(decide_batch(self.station, 'B', 'X').status, 'ready')
        piece = publish(self.station, 'B', selected_capture_id='X', receipt=receipt)
        self.assertEqual((piece.micrometers, piece.read_location), (11, ('s1', 1)))
        self.assertEqual(piece.original.origin.slot_id, 's1')
        self.assertEqual(piece.original.origin.source, SAMPLE['sources']['X'])
        self.assertEqual(piece.original.raw, SAMPLE['raw'])
        self.device.close()
        self.station.close()
        self.station = self.connect_station()
        self.assertEqual(claim(self.station, 'B'), piece)
        self.assertEqual(publish(self.station, 'B'), piece)

    def test_release_number_cannot_be_retargeted(self):
        self.prepare()
        old = self.mailbox.take('old')
        self.receive('X', old)
        release_movable(self.station, self.device, 'X', 'R-X')
        w = self.device_record('W', slot='s2', source='head-A/strip/W')
        self.announce(w)
        with self.assertRaises(ValueError):
            release_movable(self.station, self.device, 'W', 'R-X')
        self.assertEqual(read_live_original(self.device, 'W').origin, w)


if __name__ == '__main__':
    unittest.main()
