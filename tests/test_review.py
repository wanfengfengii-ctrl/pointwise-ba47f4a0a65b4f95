from delivery.claim import claim
from delivery.publish import RejectedMeasurement, publish
from delivery.shift import shift_ledger
from intake.adopt import receive_retained
from intake.receipts import find_receipt, receive_original
from recorder.capture import read_live_original
from recorder.release import ReleasePermit, release_capture
from recorder.retained import retain_original
from review.batch import open_batch, register_candidate
from review.decision import FormalDecision, decide_batch, read_decision
from review.preview import preview
from tests.support import StationCase, Y_RAW, Z_RAW


class ReviewTests(StationCase):
    def test_preview_then_final(self):
        self.capture('X')
        self.adopt('X')
        origin_y = self.capture('Y', slot='s2')
        self.adopt('Y')
        self.assertEqual(preview(self.station, 'B', 'Y').measurement.micrometers, 11)
        self.assertEqual(preview(self.station, 'B', 'X').measurement.micrometers, 15)
        self.assertIsNone(claim(self.station, 'B'))
        self.assertEqual(decide_batch(self.station, 'B', 'Y').status, 'ready')
        self.assertEqual(read_decision(self.station, 'B'), FormalDecision('B', 'Y'))
        piece = publish(self.station, 'B')
        self.assertEqual(piece.original.origin, origin_y)
        self.assertEqual(piece.original.raw, Y_RAW)
        self.assertEqual(piece.micrometers, 11)
        self.assertEqual([entry.origin.capture_id for entry in shift_ledger(self.station, 'S')], ['Y'])

    def test_candidate_membership(self):
        open_batch(self.station, 'Other', 'S')
        self.capture('Y', batch='Other')
        self.capture('Z', slot='s2', candidate=False)
        for capture_id in ('Y', 'missing'):
            with self.subTest(capture_id=capture_id), self.assertRaises(ValueError):
                register_candidate(self.station, 'B', capture_id)
        for capture_id in ('Y', 'Z'):
            with self.subTest(capture_id=capture_id):
                with self.assertRaises(ValueError):
                    preview(self.station, 'B', capture_id)
                with self.assertRaises(ValueError):
                    decide_batch(self.station, 'B', capture_id)
        self.assertIsNone(read_decision(self.station, 'B'))
        register_candidate(self.station, 'B', 'Z')
        self.assertEqual(preview(self.station, 'B', 'Z').capture_id, 'Z')

    def test_formal_choice_replay(self):
        self.capture('X')
        self.adopt('X')
        self.capture('Y')
        self.assertEqual(decide_batch(self.station, 'B', 'Y').status, 'pending')
        self.reopen()
        self.assertEqual(decide_batch(self.station, 'B', 'Y').status, 'pending')
        with self.assertRaises(ValueError):
            decide_batch(self.station, 'B', 'X')
        with self.assertRaises(ValueError):
            preview(self.station, 'B', 'Y')
        self.adopt('Y')
        self.assertEqual(decide_batch(self.station, 'B', 'Y').status, 'ready')
        self.assertEqual(read_decision(self.station, 'B'), FormalDecision('B', 'Y'))

    def test_late_selected_original(self):
        choice = self.capture('Y')
        self.adopt('Y', method='device')
        self.capture('Z')
        self.adopt('Z')
        self.assertEqual(find_receipt(self.station, 'Z').measurement.micrometers, 11)
        self.assertEqual(decide_batch(self.station, 'B', 'Y').status, 'pending')
        self.assertIsNone(publish(self.station, 'B'))
        self.assertIsNone(claim(self.station, 'B'))
        self.assertEqual(shift_ledger(self.station, 'S'), [])
        self.assertEqual(self.station.execute('SELECT count(*) FROM first_receipts').fetchone()[0], 0)
        actual = receive_retained(self.station, self.device, 'Y')
        self.assertEqual(actual.measurement.micrometers, 11)
        self.assertEqual(decide_batch(self.station, 'B', 'Y').status, 'ready')
        piece = publish(self.station, 'B', selected_capture_id='Y', receipt=actual)
        self.assertEqual(piece.original.origin, choice)
        self.assertEqual(piece.original.raw, Y_RAW)
        self.assertEqual(claim(self.station, 'B'), piece)
        self.assertEqual(self.station.execute('SELECT count(*) FROM first_receipts').fetchone()[0], 1)

    def test_device_fixed_only_is_pending(self):
        origin = self.capture('Y')
        self.adopt('Y', method='device')
        self.reopen()
        self.assertIsNone(preview(self.station, 'B', 'Y').measurement)
        self.assertEqual(decide_batch(self.station, 'B', 'Y').status, 'pending')
        self.assertIsNone(publish(self.station, 'B'))
        actual = receive_retained(self.station, self.device, 'Y')
        self.assertEqual(actual.original.origin, origin)
        self.assertEqual(publish(self.station, 'B').original, actual.original)

    def test_wrong_receipt_cannot_fill_formal_choice(self):
        origin_y = self.capture('Y')
        actual_y = read_live_original(self.device, 'Y')
        fixed = retain_original(self.device, 'Y')
        release_capture(self.device, 'device-only-Y', ReleasePermit(origin_y, 'device', fixed.reference))
        self.capture('Z')
        actual_z = receive_original(self.station, read_live_original(self.device, 'Z'))
        self.assertEqual(actual_z.original.raw, Z_RAW)
        self.assertEqual(actual_z.measurement.micrometers, 11)
        self.assertEqual(decide_batch(self.station, 'B', 'Y').status, 'pending')
        with self.assertRaises(ValueError):
            publish(self.station, 'B', selected_capture_id='Z')
        with self.assertRaises(ValueError):
            publish(self.station, 'B', selected_capture_id='Y', receipt=actual_z)
        self.assertIsNone(publish(self.station, 'B'))
        self.assertIsNone(claim(self.station, 'B'))
        self.assertEqual(self.station.execute('SELECT count(*) FROM first_receipts').fetchone()[0], 0)
        receive_original(self.station, actual_y)
        self.assertEqual(publish(self.station, 'B').original, actual_y)
        with self.assertRaises(ValueError):
            publish(self.station, 'B', receipt=actual_z)

    def assert_rejected_original(self, capture_id, batch_id, raw, reason):
        source = self.capture(capture_id, batch=batch_id, raw=raw)
        self.adopt(capture_id)
        state = decide_batch(self.station, batch_id, capture_id)
        self.assertEqual((state.status, state.reason), ('rejected', reason))
        self.assertEqual(state.receipt.original.raw, raw)
        self.assertEqual(state.receipt.original.origin, source)
        self.assertIsNone(state.receipt.measurement.micrometers)
        with self.assertRaisesRegex(RejectedMeasurement, reason):
            publish(self.station, batch_id)
        self.assertIsNone(claim(self.station, batch_id))
        self.assertIsNone(read_live_original(self.device, capture_id))
        self.assertEqual(self.station.execute('SELECT count(*) FROM first_receipts').fetchone()[0], 0)

    def test_reject_bad_measurement(self):
        self.capture('X')
        self.adopt('X')
        preview(self.station, 'B', 'X')
        self.assert_rejected_original('Y', 'B', b'{"count":13,"quality":1}', 'bad_quality')

    def test_null_and_missing_count(self):
        self.assert_rejected_original('null', 'B', b'{"count":null,"quality":0}', 'null_count')
        open_batch(self.station, 'Missing', 'S')
        self.assert_rejected_original('missing', 'Missing', b'{"quality":0}', 'missing_count')

    def test_invalid_payload_and_fields(self):
        for index, (raw, reason) in enumerate((
            (b'\xff', 'invalid_payload'), (b'{', 'invalid_payload'),
            (b'{"count":true,"quality":0}', 'invalid_count'),
            (b'{"count":13,"quality":-1}', 'invalid_quality'),
        )):
            with self.subTest(raw=raw):
                batch = f'Bad-{index}'
                open_batch(self.station, batch, 'S')
                self.assert_rejected_original(f'bad-{index}', batch, raw, reason)
