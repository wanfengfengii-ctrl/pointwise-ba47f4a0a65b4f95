import sqlite3
from dataclasses import replace

from intake.adopt import adopt, receive_retained
from intake.evidence import announce_origin, bind_preservation, read_preservation
from intake.operations import bind_operation, read_operation, record_outcome
from intake.receipts import find_receipt, receive_original
from intake.resume import resume_release
from measurement.policy import get_policy, open_shift
from recorder.capture import read_live_original, record
from recorder.original import CapturedOriginal
from recorder.release import ReleasePermit, ReleaseReplyLost, query_release, release_capture
from recorder.retained import read_retained_original, retain_original
from recorder.slots import SlotBusy, get_occupancy
from review.batch import open_batch
from review.preview import preview
from tests.support import StationCase, Y_RAW, Z_RAW


class CaptureTests(StationCase):
    def test_shift_conversion(self):
        origin = self.capture('Y')
        self.adopt('Y')
        actual = find_receipt(self.station, 'Y')
        self.assertEqual(actual.measurement.micrometers, 11)
        self.assertIsNone(actual.measurement.reason)
        self.assertEqual(actual.original, CapturedOriginal(origin, Y_RAW))
        self.assertEqual(preview(self.station, 'B', 'Y').measurement.micrometers, 11)

    def test_frozen_shift_reopen(self):
        initial = open_shift(self.station, 'Scaled', 2, 3, 1)
        self.assertEqual(open_shift(self.station, 'Scaled', 2, 3, 1), initial)
        with self.assertRaises(ValueError):
            open_shift(self.station, 'Scaled', 4, 2, 1)
        for zero, scale, mask in ((False, 1, 1), (2, 0, 1), (2, 1, -1), (2, 1.5, 1)):
            with self.subTest(zero=zero, scale=scale, mask=mask), self.assertRaises(ValueError):
                open_shift(self.station, 'Invalid', zero, scale, mask)
        self.reopen()
        self.assertEqual(get_policy(self.station, 'Scaled'), initial)
        with self.assertRaises(KeyError):
            get_policy(self.station, 'Invalid')
        open_batch(self.station, 'ScaledBatch', 'Scaled')
        self.capture('Y', batch='ScaledBatch')
        self.adopt('Y')
        self.assertEqual(find_receipt(self.station, 'Y').measurement.micrometers, 33)

    def test_nonunit_scale_and_zero_count(self):
        open_shift(self.station, 'Scaled', 2, 3, 1)
        open_batch(self.station, 'ScaledBatch', 'Scaled')
        for capture_id, raw, expected in (
            ('reading', Y_RAW, 33), ('zero', b'{"count":0,"quality":2}', -6),
            ('large', b'{"count":100000000000000000000,"quality":0}', 299999999999999999994),
        ):
            with self.subTest(capture_id=capture_id):
                self.capture(capture_id, batch='ScaledBatch', raw=raw)
                self.adopt(capture_id)
                self.assertEqual(find_receipt(self.station, capture_id).measurement.micrometers, expected)

    def test_unselected_capture_survives_reuse(self):
        self.capture('X')
        self.adopt('X')
        preview(self.station, 'B', 'X')
        origin_y = self.capture('Y')
        self.adopt('Y', method='device')
        origin_z = self.capture('Z')
        self.reopen()
        self.assertEqual(origin_y.slot_id, origin_z.slot_id)
        self.assertGreater(origin_z.generation, origin_y.generation)
        self.assertIsNone(read_live_original(self.device, 'Y'))
        self.assertEqual(read_retained_original(self.device, 'Y').original, CapturedOriginal(origin_y, Y_RAW))
        self.assertEqual(read_live_original(self.device, 'Z'), CapturedOriginal(origin_z, Z_RAW))
        self.assertIsNone(find_receipt(self.station, 'Y'))

    def test_full_slots_preserve_existing_originals(self):
        origin_x = self.capture('X')
        origin_y = self.capture('Y', slot='s2')
        with self.assertRaises(SlotBusy):
            record(self.device, 'B', 'Z', Z_RAW, 'head/strip/Z')
        self.assertEqual(read_live_original(self.device, 'X').origin, origin_x)
        self.assertEqual(read_live_original(self.device, 'Y').origin, origin_y)
        self.adopt('X')
        origin_z = record(self.device, 'B', 'Z', Z_RAW, 'head/strip/Z')
        self.assertEqual(origin_z.slot_id, 's1')
        self.assertEqual(origin_z.generation, origin_x.generation + 1)

    def test_original_identity_conflicts_leave_original_intact(self):
        origin = self.capture('Y')
        legit = read_live_original(self.device, 'Y')
        self.assertEqual(record(self.device, 'B', 'Y', Y_RAW, origin.source, slot_id='s1'), origin)
        with self.assertRaises(ValueError):
            record(self.device, 'B', 'Y', Z_RAW, origin.source)
        with self.assertRaises(ValueError):
            announce_origin(self.station, replace(origin, source='another head'))
        for altered in (CapturedOriginal(origin, Z_RAW),
                        CapturedOriginal(replace(origin, source='another head'), Y_RAW)):
            with self.subTest(altered=altered), self.assertRaises(ValueError):
                receive_original(self.station, altered)
        self.assertIsNone(find_receipt(self.station, 'Y'))
        actual = receive_original(self.station, legit)
        self.assertEqual(receive_original(self.station, legit), actual)
        self.assertEqual(actual.original, CapturedOriginal(origin, Y_RAW))

    def test_both_preservation_paths(self):
        self.capture('Y')
        self.adopt('Y')
        self.capture('Z')
        self.adopt('Z', method='device')
        self.reopen()
        self.assertEqual(read_preservation(self.station, 'Y').method, 'station')
        self.assertEqual(find_receipt(self.station, 'Y').original.raw, Y_RAW)
        self.assertIsNone(read_retained_original(self.device, 'Y'))
        self.assertEqual(read_preservation(self.station, 'Z').method, 'device')
        self.assertIsNone(find_receipt(self.station, 'Z'))
        fixed_receipt = receive_retained(self.station, self.device, 'Z')
        self.assertEqual(fixed_receipt.original.raw, Z_RAW)
        self.assertEqual(fixed_receipt.measurement.micrometers, 11)

    def test_preservation_failure_prevents_release(self):
        origin = self.capture('Y')
        with self.assertRaisesRegex(ValueError, 'confirmed preservation'):
            release_capture(self.device, 'R-Y', ReleasePermit(origin, 'station', 'Y'))
        failures = (
            (self.device, 'retained_originals', 'device'),
            (self.station, 'capture_receipts', 'station'),
        )
        for db, table, method in failures:
            with self.subTest(method=method):
                db.execute(f'CREATE TRIGGER refuse_copy BEFORE INSERT ON {table} '
                           "BEGIN SELECT RAISE(ABORT, 'copy refused'); END")
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'copy refused'):
                    self.adopt('Y', method=method)
                self.assertEqual(get_occupancy(self.device, 's1').capture_id, 'Y')
                self.assertIsNone(read_preservation(self.station, 'Y'))
                self.assertIsNone(read_operation(self.station, 'R-Y'))
                self.assertIsNone(query_release(self.device, 'R-Y'))
                db.execute('DROP TRIGGER refuse_copy')
        self.assertEqual(self.adopt('Y', method='device').capture_id, 'Y')

    def test_release_reply_loss(self):
        old = self.capture('Y')
        with self.assertRaisesRegex(ReleaseReplyLost, 'R-Y'):
            self.adopt('Y', method='device', lose_reply=True)
        self.assertIsNone(read_operation(self.station, 'R-Y').outcome)
        newer = self.capture('Z')
        self.reopen()
        outcome = resume_release(self.station, self.device, 'R-Y')
        self.assertEqual(outcome, query_release(self.device, 'R-Y'))
        self.assertEqual((outcome.capture_id, outcome.slot_id, outcome.generation),
                         ('Y', old.slot_id, old.generation))
        self.assertEqual(read_operation(self.station, 'R-Y').outcome, outcome)
        self.assertEqual(self.adopt('Y', method='device'), outcome)
        self.assertEqual(read_live_original(self.device, 'Z').origin, newer)

    def test_operation_binding_rejects_other_occupancy(self):
        self.capture('Y')
        outcome = self.adopt('Y', method='device')
        newer = self.capture('Z')
        fixed = retain_original(self.device, 'Z')
        with self.assertRaisesRegex(ValueError, 'already bound'):
            adopt(self.station, self.device, 'Z', 'R-Y')
        with self.assertRaisesRegex(ValueError, 'already bound'):
            release_capture(self.device, 'R-Y', ReleasePermit(newer, 'device', fixed.reference))
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            record_outcome(self.station, replace(outcome, generation=newer.generation))
        self.assertEqual(query_release(self.device, 'R-Y'), outcome)
        self.assertEqual(read_live_original(self.device, 'Z').origin, newer)

    def test_resume_unresolved_operation(self):
        origin = self.capture('Y')
        fixed = retain_original(self.device, 'Y')
        bind_preservation(self.station, fixed.original, 'device', fixed.reference)
        bind_operation(self.station, 'R-Y', origin)
        self.reopen()
        self.assertIsNone(resume_release(self.station, self.device, 'R-Y'))
        self.assertEqual(get_occupancy(self.device, 's1').capture_id, 'Y')
        outcome = resume_release(self.station, self.device, 'R-Y', retry=True)
        self.assertEqual(outcome, query_release(self.device, 'R-Y'))
        self.assertEqual(outcome, read_operation(self.station, 'R-Y').outcome)
        self.assertIsNone(get_occupancy(self.device, 's1'))
