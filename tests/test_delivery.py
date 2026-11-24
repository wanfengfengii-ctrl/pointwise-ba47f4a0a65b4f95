import sqlite3

from delivery.claim import claim
from delivery.publish import publish
from delivery.shift import shift_ledger
from intake.adopt import receive_retained
from intake.operations import read_operation
from intake.receipts import find_receipt, receive_original
from measurement.policy import get_policy, open_shift
from recorder.capture import read_live_original
from recorder.retained import read_retained_original
from review.batch import get_candidate, open_batch
from review.decision import FormalDecision, decide_batch, read_decision
from tests.support import StationCase, Y_RAW


def block_commit(db):
    db.execute('CREATE TRIGGER refuse_snapshot BEFORE INSERT ON published_pieces '
               "WHEN EXISTS (SELECT 1 FROM first_receipts WHERE batch_id = NEW.batch_id) "
               "BEGIN SELECT RAISE(ABORT, 'snapshot refused'); END")


class DeliveryTests(StationCase):
    def ready_batch(self, capture_id='Y', batch_id='B'):
        origin = self.capture(capture_id, batch=batch_id)
        self.adopt(capture_id)
        self.assertEqual(decide_batch(self.station, batch_id, capture_id).status, 'ready')
        return origin

    def test_publish_rollback(self):
        origin = self.ready_batch()
        actual = find_receipt(self.station, 'Y')
        block_commit(self.station)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'snapshot refused'):
            publish(self.station, 'B', receipt=actual)
        self.assertIsNone(claim(self.station, 'B'))
        self.assertEqual(read_decision(self.station, 'B'), FormalDecision('B', 'Y'))
        self.assertEqual(find_receipt(self.station, 'Y'), actual)
        self.station.execute('DROP TRIGGER refuse_snapshot')
        piece = publish(self.station, 'B', receipt=actual)
        self.assertEqual(piece.original.origin, origin)
        self.assertEqual(piece, publish(self.station, 'B', selected_capture_id='Y'))
        self.assertEqual(len(shift_ledger(self.station, 'S')), 1)

    def test_failed_commit_invisible_to_reopened_reader(self):
        self.ready_batch()
        block_commit(self.station)
        with self.assertRaises(sqlite3.IntegrityError):
            publish(self.station, 'B')
        observer = self.connect_station()
        self.assertIsNone(claim(observer, 'B'))
        self.assertEqual(shift_ledger(observer, 'S'), [])
        self.assertEqual(observer.execute('SELECT count(*) FROM first_receipts').fetchone()[0], 0)
        self.station.execute('DROP TRIGGER refuse_snapshot')
        piece = publish(self.station, 'B')
        self.assertEqual(claim(observer, 'B'), piece)
        self.assertEqual(observer.execute('SELECT token FROM first_receipts').fetchall(), [(piece.first_receipt,)])

    def test_offline_original_receipt(self):
        origin = self.ready_batch()
        before = publish(self.station, 'B')
        self.device.close()
        self.device_path.unlink()
        self.station.close()
        self.station = self.connect_station()
        after = claim(self.station, 'B')
        self.assertEqual(after, before)
        self.assertEqual(after.original.origin, origin)
        self.assertEqual(after.original.raw, Y_RAW)
        self.assertEqual(after.micrometers, 11)
        self.assertTrue(after.first_receipt)
        self.assertEqual(publish(self.station, 'B'), after)
        entry, = shift_ledger(self.station, 'S')
        self.assertEqual((entry.batch_id, entry.origin, entry.micrometers), ('B', origin, 11))

    def test_offline_same_item_replay(self):
        self.capture('Y')
        self.adopt('Y', method='device')
        receipt = receive_retained(self.station, self.device, 'Y')
        decide_batch(self.station, 'B', 'Y')
        result = publish(self.station, 'B', selected_capture_id='Y', receipt=receipt)
        self.device.close()
        self.station.close()
        self.station = self.connect_station()
        replay_receipt = receive_original(self.station, receipt.original)
        self.assertEqual(replay_receipt, receipt)
        self.assertEqual(decide_batch(self.station, 'B', 'Y').receipt, receipt)
        self.assertEqual(publish(self.station, 'B', receipt=replay_receipt), result)
        self.assertEqual(self.station.execute('SELECT count(*) FROM first_receipts').fetchone()[0], 1)

    def test_ledger_order_is_commit_order(self):
        open_batch(self.station, 'A', 'S')
        origin_a = self.capture('early-choice', batch='A')
        self.adopt('early-choice', method='device')
        self.assertEqual(decide_batch(self.station, 'A', 'early-choice').status, 'pending')
        origin_b = self.ready_batch('Y')
        first = publish(self.station, 'B')
        receive_retained(self.station, self.device, 'early-choice')
        second = publish(self.station, 'A')
        self.assertLess(first.sequence, second.sequence)
        self.device.close()
        self.station.close()
        self.station = self.connect_station()
        ledger = shift_ledger(self.station, 'S')
        self.assertEqual([(entry.batch_id, entry.origin, entry.micrometers) for entry in ledger],
                         [('B', origin_b, 11), ('A', origin_a, 11)])

    def test_lookup_boundaries(self):
        with self.assertRaises(KeyError):
            claim(self.station, 'unknown')
        with self.assertRaises(KeyError):
            shift_ledger(self.station, 'unknown')
        self.assertIsNone(claim(self.station, 'B'))
        self.assertIsNone(publish(self.station, 'B'))
        self.assertEqual(shift_ledger(self.station, 'S'), [])

    def test_reinitialization_keeps_user_data(self):
        policy = open_shift(self.station, 'Custom', 3, 4, 2)
        open_batch(self.station, 'CustomBatch', 'Custom')
        chosen = self.capture('Y', batch='CustomBatch')
        outcome = self.adopt('Y', method='device')
        receipt = receive_retained(self.station, self.device, 'Y')
        self.assertEqual(receipt.measurement.micrometers, 40)
        decide_batch(self.station, 'CustomBatch', 'Y')
        result = publish(self.station, 'CustomBatch')
        live = self.capture('Z', batch='CustomBatch')
        self.reopen()
        self.reopen()
        self.assertEqual(get_policy(self.station, 'Custom'), policy)
        self.assertEqual(get_candidate(self.station, 'CustomBatch', 'Y').origin, chosen)
        self.assertEqual(read_decision(self.station, 'CustomBatch').capture_id, 'Y')
        self.assertEqual(find_receipt(self.station, 'Y'), receipt)
        self.assertEqual(read_operation(self.station, 'R-Y').outcome, outcome)
        self.assertEqual(read_retained_original(self.device, 'Y').original, receipt.original)
        self.assertEqual(read_live_original(self.device, 'Z').origin, live)
        self.assertEqual(claim(self.station, 'CustomBatch'), result)
        entry, = shift_ledger(self.station, 'Custom')
        self.assertEqual((entry.batch_id, entry.micrometers, entry.origin), ('CustomBatch', 40, chosen))
        self.assertEqual(shift_ledger(self.station, 'S'), [])
