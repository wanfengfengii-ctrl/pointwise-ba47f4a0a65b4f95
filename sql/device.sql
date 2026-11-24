PRAGMA foreign_keys = ON;
PRAGMA synchronous = FULL;
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS device_slots (
    slot_id TEXT PRIMARY KEY NOT NULL,
    generation INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
    capture_id TEXT UNIQUE REFERENCES device_origins(capture_id) DEFERRABLE INITIALLY DEFERRED,
    raw BLOB,
    CHECK ((capture_id IS NULL) = (raw IS NULL))
);

CREATE TABLE IF NOT EXISTS device_origins (
    capture_id TEXT PRIMARY KEY NOT NULL,
    batch_id TEXT NOT NULL,
    source TEXT NOT NULL,
    slot_id TEXT NOT NULL REFERENCES device_slots(slot_id),
    generation INTEGER NOT NULL CHECK (generation > 0),
    digest TEXT NOT NULL,
    movable INTEGER NOT NULL DEFAULT 0 CHECK (movable IN (0, 1)),
    UNIQUE (slot_id, generation)
);

CREATE TABLE IF NOT EXISTS device_moves (
    operation_id TEXT PRIMARY KEY NOT NULL,
    capture_id TEXT NOT NULL UNIQUE REFERENCES device_origins(capture_id),
    from_slot TEXT NOT NULL REFERENCES device_slots(slot_id),
    from_generation INTEGER NOT NULL CHECK (from_generation > 0),
    to_slot TEXT NOT NULL REFERENCES device_slots(slot_id),
    to_generation INTEGER NOT NULL CHECK (to_generation > 0)
);

CREATE TABLE IF NOT EXISTS retained_originals (
    capture_id TEXT PRIMARY KEY NOT NULL REFERENCES device_origins(capture_id),
    raw BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS device_releases (
    operation_id TEXT PRIMARY KEY NOT NULL,
    capture_id TEXT NOT NULL UNIQUE REFERENCES device_origins(capture_id),
    slot_id TEXT NOT NULL REFERENCES device_slots(slot_id),
    generation INTEGER NOT NULL CHECK (generation > 0)
);

INSERT INTO device_slots (slot_id) VALUES ('s1'), ('s2') ON CONFLICT(slot_id) DO NOTHING;
COMMIT;
