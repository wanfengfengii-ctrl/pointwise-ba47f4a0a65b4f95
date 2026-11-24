import json
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from delivery.claim import claim
from delivery.publish import publish
from delivery.shift import shift_ledger
from intake.adopt import adopt, receive_retained
from intake.evidence import announce_origin, open_station
from intake.operations import read_operation
from intake.receipts import find_receipt, receive_original
from intake.resume import resume_release
from measurement.policy import open_shift
from recorder.capture import open_device, read_live_original, record
from recorder.release import ReleaseReplyLost, query_release
from recorder.retained import read_retained_original
from review.batch import open_batch, register_candidate
from review.decision import decide_batch
from review.preview import preview


def require(condition, detail):
    if not condition:
        raise AssertionError(f'SELECTED_ORIGINAL_MISMATCH {detail}')


def run(directory, script):
    station_path = directory / 'station.sqlite'
    device_path = directory / 'device.sqlite'
    shift_id, batch_id = script['shift_id'], script['batch_id']
    input_y = script['captures']['Y']

    def capture(station, device, key):
        entry = script['captures'][key]
        origin = record(device, batch_id, key, entry['raw'].encode('utf-8'), entry['source'],
                        slot_id=script['slot_id'])
        announce_origin(station, origin)
        register_candidate(station, batch_id, key)
        return origin

    with closing(open_station(station_path)) as station, closing(open_device(device_path)) as device:
        open_shift(station, shift_id, script['zero_offset'], script['micrometers_per_count'],
                   script['bad_quality_mask'])
        open_batch(station, batch_id, shift_id)
        capture(station, device, 'X')
        adopt(station, device, 'X', 'R-X')
        preview(station, batch_id, 'X')
        origin_y = capture(station, device, 'Y')
        try:
            adopt(station, device, 'Y', 'R-Y', method='device', lose_reply=True)
        except ReleaseReplyLost:
            require(read_operation(station, 'R-Y').outcome is None, 'Y reply unexpectedly confirmed')
        else:
            require(False, 'Y release reply was not lost')
        origin_z = capture(station, device, 'Z')

    with closing(open_station(station_path)) as station, closing(open_device(device_path)) as device:
        release_y = resume_release(station, device, 'R-Y')
        require(release_y is not None and release_y == query_release(device, 'R-Y')
                and (release_y.capture_id, release_y.slot_id, release_y.generation)
                == ('Y', origin_y.slot_id, origin_y.generation), 'expected original release for Y')
        live_z = read_live_original(device, 'Z')
        require(live_z is not None and live_z.origin == origin_z
                and origin_z.slot_id == origin_y.slot_id
                and origin_z.generation > origin_y.generation, 'new Z occupancy was not preserved')
        fixed_y = read_retained_original(device, 'Y')
        require(fixed_y is not None and fixed_y.original.origin == origin_y
                and fixed_y.original.raw == input_y['raw'].encode('utf-8'), 'missing fixed Y original')
        adopt(station, device, 'Z', 'R-Z')
        actual_z = find_receipt(station, 'Z')
        require(actual_z is not None and actual_z.measurement.micrometers == 11, 'expected 11 μm for Z')
        before = decide_batch(station, batch_id, 'Y')
        unexpected = publish(station, batch_id)
        require(before.status == 'pending' and unexpected is None,
                f'expected pending for Y before receipt, got {before.status} and {unexpected!r}')
        require(claim(station, batch_id) is None and shift_ledger(station, shift_id) == [],
                'a result was visible before Y arrived')
        firsts_before = station.execute('SELECT count(*) FROM first_receipts').fetchone()[0]
        require(firsts_before == 0, 'a first receipt was visible before Y arrived')
        actual_y = receive_retained(station, device, 'Y')
        require(actual_y.measurement.micrometers == 11, 'expected 11 μm for Y')
        signed = publish(station, batch_id, selected_capture_id='Y', receipt=actual_y)
        require(signed is not None and signed.original == fixed_y.original and signed.micrometers == 11,
                f'expected signed Y original, got {signed!r}')

    with closing(open_station(station_path)) as station:
        replay_receipt = receive_original(station, actual_y.original)
        require(decide_batch(station, batch_id, 'Y').status == 'ready', 'Y replay was not ready')
        replay = publish(station, batch_id, receipt=replay_receipt)
        offline = claim(station, batch_id)
        ledger = shift_ledger(station, shift_id)
        require(offline == signed == replay, 'offline reclaim or replay changed the signed result')
        require(len(ledger) == 1 and (ledger[0].origin, ledger[0].micrometers) == (origin_y, 11),
                'ledger did not identify Y at 11 μm')
        firsts_after = station.execute('SELECT count(*) FROM first_receipts').fetchone()[0]
        require(firsts_after == 1, 'expected one first receipt after replay')

    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'late',
        'input': {'selected': 'Y', 'Y': input_y, 'Z': script['captures']['Z'],
                  'zero_offset': script['zero_offset'], 'scale': script['micrometers_per_count']},
        'expected': {'before_y': 'pending', 'first_receipts_before_y': 0, 'micrometers': 11},
        'actual': {'before_y': before.status, 'first_receipts_before_y': firsts_before,
                   'micrometers': actual_y.measurement.micrometers},
        'downstream_expected': {'capture': 'Y', 'source': input_y['source'], 'raw': input_y['raw'],
                                'same_first_receipt': True, 'receipt_count': 1},
        'downstream_actual': {'capture': offline.original.origin.capture_id,
                              'source': offline.original.origin.source,
                              'raw': offline.original.raw.decode('utf-8'),
                              'same_first_receipt': offline.first_receipt == signed.first_receipt,
                              'receipt_count': firsts_after},
        'code_paths': ['measurement/units.py', 'intake/adopt.py', 'intake/resume.py',
                       'review/readiness.py', 'delivery/publish.py', 'delivery/claim.py'],
        'oracle': 'Hand calculation (13 - 2) * 1 = 11. Compare with the exact bytes and source of Y in the input script.',
    }, ensure_ascii=False))
    print(f'{offline.batch_id}  {offline.original.origin.capture_id}  {offline.micrometers} μm  '
          f'{offline.original.origin.source}  {offline.first_receipt}')


def main():
    script = json.loads(Path(__file__).with_name('shift.json').read_text(encoding='utf-8'))
    with TemporaryDirectory() as directory:
        run(Path(directory), script)


if __name__ == '__main__':
    main()
