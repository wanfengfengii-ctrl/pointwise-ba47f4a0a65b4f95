import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from intake.adopt import adopt
from intake.evidence import announce_origin, open_station
from measurement.policy import open_shift
from recorder.capture import open_device, record
from review.batch import open_batch, register_candidate


X_RAW = b'{"count":17,"quality":0}'
Y_RAW = b'{"count":13,"quality":0}'
Z_RAW = b'{"quality":0, "count":13}'


class StationCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.device_path = Path(directory.name) / 'device.sqlite'
        self.station_path = Path(directory.name) / 'station.sqlite'
        self.device = self.connect_device()
        self.station = self.connect_station()
        open_shift(self.station, 'S', 2, 1, 1)
        open_batch(self.station, 'B', 'S')

    def connect_device(self):
        connection = open_device(self.device_path)
        self.addCleanup(connection.close)
        return connection

    def connect_station(self):
        connection = open_station(self.station_path)
        self.addCleanup(connection.close)
        return connection

    def reopen(self):
        self.station.close()
        self.device.close()
        self.station = self.connect_station()
        self.device = self.connect_device()

    def capture(self, capture_id, *, batch='B', slot='s1', raw=None, candidate=True):
        if raw is None:
            raw = {'X': X_RAW, 'Z': Z_RAW}.get(capture_id, Y_RAW)
        origin = record(self.device, batch, capture_id, raw, f'head/strip/{capture_id}', slot_id=slot)
        announce_origin(self.station, origin)
        if candidate:
            register_candidate(self.station, batch, capture_id)
        return origin

    def adopt(self, capture_id, *, method='station', lose_reply=False):
        return adopt(self.station, self.device, capture_id, f'R-{capture_id}',
                     method=method, lose_reply=lose_reply)
