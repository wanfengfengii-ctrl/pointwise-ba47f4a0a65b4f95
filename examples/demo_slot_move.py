"""Demonstrate the online slot-move flow described in SLOT_MOVE.md.

The station is opened and closed independently of the device, a delayed packet is
accepted after the slots have been reused, a wrong binding and a tampered frame
are refused, and a dropped release reply is resumed by its original number before
the signed piece is claimed from the station database alone.
"""
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from delivery.claim import claim
from delivery.publish import publish
from examples.slot_move_fixture import SAMPLE, HeldFrames, LocalPort
from intake.adopt import release_movable
from intake.evidence import announce_origin, open_station
from intake.operations import read_operation
from intake.receipts import find_receipt, receive_live_packet
from intake.resume import resume_release
from measurement.policy import open_shift
from recorder.capture import move_capture, open_device, read_live_packet, read_live_original, record
from recorder.release import ReleaseReplyLost, query_release
from recorder.slots import get_occupancy
from review.batch import open_batch, register_candidate
from review.decision import decide_batch


def require(condition, detail):
    if not condition:
        raise AssertionError(f'SLOT_MOVE_MISMATCH {detail}')


def _reject(action, detail):
    try:
        action()
    except ValueError:
        return
    raise AssertionError(f'EXPECTED_REJECTION {detail}')


def run(directory):
    shift_id, batch_id = SAMPLE['shift'], SAMPLE['batch']
    raw, sources = SAMPLE['raw'], SAMPLE['sources']
    station_path = directory / 'station.sqlite'
    device_path = directory / 'device.sqlite'
    device_port = LocalPort(b'test-only-local-key')
    station_port = LocalPort(b'test-only-local-key')
    frames = HeldFrames()

    # Day shift: record X, read its packet, then move it while the station is offline.
    station = open_station(station_path)
    device = open_device(device_path)
    try:
        open_shift(station, shift_id, SAMPLE['zero_offset'], SAMPLE['scale'],
                   SAMPLE['bad_quality_mask'])
        open_batch(station, batch_id, shift_id)
        origin_x = record(device, batch_id, 'X', raw, sources['X'], slot_id='s1', movable=True)
        announce_origin(station, origin_x)
        register_candidate(station, batch_id, 'X')
        frames.hold('old', read_live_packet(device, 'X', seal=device_port.seal))
    finally:
        station.close()
    moved = move_capture(device, 'MOVE-X', 'X', 's2')
    origin_z = record(device, batch_id, 'Z', raw, sources['Z'], slot_id='s1', movable=True)
    device.close()

    # X is online in s2 and Z online in s1; both read the same bytes.
    station = open_station(station_path)
    device = open_device(device_path)
    try:
        announce_origin(station, origin_z)
        register_candidate(station, batch_id, 'Z')
        x_live = read_live_packet(device, 'X', seal=device_port.seal)
        z_live = read_live_packet(device, 'Z', seal=device_port.seal)
        require(x_live is not None and z_live is not None, 'X and Z must both read online')
        require(get_occupancy(device, 's1').capture_id == 'Z', 's1 must hold Z')
        require(get_occupancy(device, 's2').capture_id == 'X', 's2 must hold X')

        # A wrong binding and a tampered frame are refused before any receipt exists.
        _reject(lambda: receive_live_packet(station, 'Z', x_live, verify=station_port.verify),
                'the X packet must not bind to Z')
        original_frame = frames.peek('old')
        tampered = replace(original_frame, body=original_frame.body.replace(
            b'"slot_id":"s1"', b'"slot_id":"s2"'))
        _reject(lambda: receive_live_packet(station, 'X', tampered, verify=station_port.verify),
                'a frame whose body was edited after sealing must be refused')
        _reject(lambda: release_movable(station, device, 'X', 'RELEASE-X'),
                'release without a committed receipt must be refused')
        require(find_receipt(station, 'X') is None, 'no receipt may exist yet')

        # The delayed genuine packet from s1 is still valid after the move.
        receipt = receive_live_packet(station, 'X', frames.take('old'), verify=station_port.verify)
        require(receipt.read_location == ('s1', 1),
                f'the first read location should be (s1, 1), got {receipt.read_location!r}')
        require(receipt.measurement.micrometers == SAMPLE['expected_micrometers'],
                'X should measure 11 μm from the delayed packet')

        # Release clears only X's current occupancy in s2, and its reply is lost.
        try:
            release_movable(station, device, 'X', 'RELEASE-X', lose_reply=True)
        except ReleaseReplyLost:
            pass
        else:
            raise AssertionError('EXPECTED_REJECTION release reply was not lost')
        require(read_operation(station, 'RELEASE-X').outcome is None,
                'a lost release must not confirm on the station yet')
        require(read_live_original(device, 'X') is None, 'X must be off the device')
        require(read_live_original(device, 'Z') is not None, 'Z must remain online in s1')
        origin_w = record(device, batch_id, 'W', raw, 'head-A/strip-99', slot_id='s2',
                          movable=True)
    finally:
        station.close()
        device.close()

    # Reopen the station and device; resume the original release number.
    station = open_station(station_path)
    device = open_device(device_path)
    try:
        outcome = resume_release(station, device, 'RELEASE-X')
        require(outcome is not None and outcome == query_release(device, 'RELEASE-X'),
                'resume must find the committed release')
        require((outcome.slot_id, outcome.generation) == ('s2', 1),
                f'release should clear s2 generation 1, got {outcome!r}')
        require(read_live_original(device, 'Z') is not None,
                'a resumed release must not clear Z in s1')
        require(read_live_original(device, 'W').origin == origin_w,
                'a resumed release must not clear W from s2')

        require(decide_batch(station, batch_id, 'X').status == 'ready', 'X must be ready to sign')
        signed = publish(station, batch_id, selected_capture_id='X', receipt=receipt)
        require(signed is not None and signed.micrometers == SAMPLE['expected_micrometers'],
                'publish must sign X at 11 μm')
        require(signed.read_location == ('s1', 1), 'the signed read location must be the first receipt')
    finally:
        device.close()
        station.close()

    # Claim from the station database alone, with the device gone.
    station = open_station(station_path)
    try:
        offline = claim(station, batch_id)
    finally:
        station.close()
    require(offline == signed, 'offline claim must reproduce the signed piece')
    require(offline.original.raw == raw, 'claimed bytes must be the sample original')
    require(offline.original.origin.source == sources['X'],
            'claimed origin must keep the first recorded source')
    require((offline.original.origin.slot_id, offline.original.origin.generation) == ('s1', 1),
            'claimed origin must keep the first recorded slot')
    require(offline.read_location == ('s1', 1), 'claimed read address must be the first receipt')
    require(offline.micrometers == SAMPLE['expected_micrometers'], 'claimed value must be 11 μm')

    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'slot-move',
        'input': {'batch': batch_id, 'sample_raw': raw.decode('utf-8'),
                  'zero_offset': 2, 'scale': 1},
        'expected': {'micrometers': 11, 'first_read_location': ['s1', 1],
                     'origin_slot': ['s1', 1], 'move': ['s1', 's2']},
        'actual': {'micrometers': offline.micrometers,
                   'first_read_location': list(offline.read_location),
                   'origin_slot': [offline.original.origin.slot_id,
                                   offline.original.origin.generation],
                   'move': [moved.from_slot, moved.to_slot],
                   'release': [outcome.slot_id, outcome.generation]},
        'downstream_expected': {'capture': 'X', 'source': sources['X'],
                                'same_first_receipt': True},
        'downstream_actual': {'capture': offline.original.origin.capture_id,
                              'source': offline.original.origin.source,
                              'same_first_receipt': offline.first_receipt == signed.first_receipt},
        'code_paths': ['recorder/capture.py', 'recorder/packet.py', 'intake/receipts.py',
                       'intake/adopt.py', 'intake/resume.py', 'delivery/commit.py'],
        'oracle': 'Hand calculation (13 - 2) * 1 = 11 after the delayed s1 packet is adopted.',
    }, ensure_ascii=False))
    print(f'{offline.batch_id}  {offline.original.origin.capture_id}  {offline.micrometers} μm  '
          f'read@{offline.read_location}  origin@{offline.original.origin.slot_id}/'
          f'{offline.original.origin.generation}  {offline.original.origin.source}  '
          f'{offline.first_receipt}')


def main():
    with TemporaryDirectory() as directory:
        run(Path(directory))


if __name__ == '__main__':
    main()
