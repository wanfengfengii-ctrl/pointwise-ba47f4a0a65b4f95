PRAGMA foreign_keys = ON;
PRAGMA synchronous = FULL;
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS shift_policies (
    shift_id TEXT PRIMARY KEY NOT NULL,
    zero_offset TEXT NOT NULL,
    micrometers_per_count TEXT NOT NULL,
    bad_quality_mask TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_batches (
    batch_id TEXT PRIMARY KEY NOT NULL,
    shift_id TEXT NOT NULL REFERENCES shift_policies(shift_id),
    UNIQUE (batch_id, shift_id)
);

CREATE TABLE IF NOT EXISTS announced_origins (
    capture_id TEXT PRIMARY KEY NOT NULL,
    batch_id TEXT NOT NULL REFERENCES review_batches(batch_id),
    source TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation > 0),
    digest TEXT NOT NULL,
    UNIQUE (batch_id, capture_id)
);

CREATE TABLE IF NOT EXISTS capture_receipts (
    capture_id TEXT PRIMARY KEY NOT NULL REFERENCES announced_origins(capture_id),
    raw BLOB NOT NULL,
    micrometers TEXT,
    reason TEXT,
    CHECK ((micrometers IS NULL) <> (reason IS NULL))
);

CREATE TABLE IF NOT EXISTS preservations (
    capture_id TEXT PRIMARY KEY NOT NULL REFERENCES announced_origins(capture_id),
    method TEXT NOT NULL CHECK (method IN ('station', 'device')),
    reference TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS release_intents (
    operation_id TEXT PRIMARY KEY NOT NULL,
    capture_id TEXT NOT NULL UNIQUE REFERENCES preservations(capture_id),
    slot_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation > 0),
    confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1))
);

CREATE TABLE IF NOT EXISTS batch_candidates (
    batch_id TEXT NOT NULL,
    capture_id TEXT NOT NULL,
    PRIMARY KEY (batch_id, capture_id),
    FOREIGN KEY (batch_id, capture_id) REFERENCES announced_origins(batch_id, capture_id)
);

CREATE TABLE IF NOT EXISTS preview_choices (
    batch_id TEXT PRIMARY KEY NOT NULL,
    capture_id TEXT NOT NULL,
    FOREIGN KEY (batch_id, capture_id) REFERENCES batch_candidates(batch_id, capture_id)
);

CREATE TABLE IF NOT EXISTS formal_decisions (
    batch_id TEXT PRIMARY KEY NOT NULL,
    capture_id TEXT NOT NULL,
    FOREIGN KEY (batch_id, capture_id) REFERENCES batch_candidates(batch_id, capture_id)
);

CREATE TABLE IF NOT EXISTS first_receipts (
    batch_id TEXT PRIMARY KEY NOT NULL REFERENCES published_pieces(batch_id) DEFERRABLE INITIALLY DEFERRED,
    token TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS published_pieces (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL UNIQUE REFERENCES first_receipts(batch_id),
    shift_id TEXT NOT NULL,
    capture_id TEXT NOT NULL,
    source TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    digest TEXT NOT NULL,
    raw BLOB NOT NULL,
    micrometers TEXT NOT NULL,
    FOREIGN KEY (batch_id, shift_id) REFERENCES review_batches(batch_id, shift_id)
);

CREATE INDEX IF NOT EXISTS pieces_by_shift ON published_pieces (shift_id, sequence);
COMMIT;
