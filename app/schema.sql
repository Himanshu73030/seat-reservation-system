CREATE TABLE IF NOT EXISTS shows (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    price_paise BIGINT NOT NULL CHECK (price_paise >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reservations (
    id UUID PRIMARY KEY,
    show_id UUID NOT NULL REFERENCES shows(id),
    user_id TEXT NOT NULL CHECK (length(user_id) BETWEEN 1 AND 200),
    amount_paise BIGINT NOT NULL CHECK (amount_paise >= 0),
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    cancelled_at TIMESTAMPTZ,
    UNIQUE (id, show_id)
);

CREATE TABLE IF NOT EXISTS seats (
    show_id UUID NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
    seat_id TEXT NOT NULL CHECK (length(seat_id) BETWEEN 1 AND 32),
    status TEXT NOT NULL DEFAULT 'available'
        CHECK (status IN ('available', 'held', 'confirmed')),
    reservation_id UUID,
    PRIMARY KEY (show_id, seat_id),
    FOREIGN KEY (reservation_id, show_id)
        REFERENCES reservations(id, show_id),
    CHECK ((status = 'confirmed') = (reservation_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS reservation_seats (
    reservation_id UUID NOT NULL,
    show_id UUID NOT NULL,
    seat_id TEXT NOT NULL,
    PRIMARY KEY (reservation_id, seat_id),
    FOREIGN KEY (reservation_id, show_id)
        REFERENCES reservations(id, show_id),
    FOREIGN KEY (show_id, seat_id)
        REFERENCES seats(show_id, seat_id)
);

CREATE TABLE IF NOT EXISTS user_show_locks (
    show_id UUID NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    PRIMARY KEY (show_id, user_id)
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    user_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200),
    request_hash CHAR(64) NOT NULL,
    status_code SMALLINT,
    response_payload JSONB,
    outcome_reason TEXT,
    replay_count BIGINT NOT NULL DEFAULT 0 CHECK (replay_count >= 0),
    mismatch_count BIGINT NOT NULL DEFAULT 0 CHECK (mismatch_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (user_id, idempotency_key),
    CHECK ((status_code IS NULL) = (response_payload IS NULL)),
    CHECK ((status_code IS NULL) = (completed_at IS NULL))
);

ALTER TABLE idempotency_keys
    ADD COLUMN IF NOT EXISTS replay_count BIGINT NOT NULL DEFAULT 0;
ALTER TABLE idempotency_keys
    ADD COLUMN IF NOT EXISTS mismatch_count BIGINT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS reservations_active_user_show_idx
    ON reservations(show_id, user_id) WHERE status = 'confirmed';
CREATE INDEX IF NOT EXISTS idempotency_outcomes_idx
    ON idempotency_keys(outcome_reason, status_code);
