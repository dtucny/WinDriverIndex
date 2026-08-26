-- Schema v1 — entities per doc/spec.md §3.
-- Boards and artefacts are deliberately separate; board_artefact carries the
-- per-listing date that the lag metric is computed from.

CREATE TABLE IF NOT EXISTS family (
    family_id       INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    silicon_vendor  TEXT NOT NULL,   -- amd | intel | realtek | mediatek | ite | asmedia | oem
    component       TEXT NOT NULL,   -- chipset | graphics | lan | wlan | bluetooth | audio |
                                     -- storage | usb | npu | utility
    hwids           TEXT NOT NULL DEFAULT '[]'   -- JSON array, populated from INF extraction
);

CREATE TABLE IF NOT EXISTS board (
    board_id           INTEGER PRIMARY KEY,
    vendor             TEXT NOT NULL,   -- msi | gigabyte | asrock | asus
    vendor_product_id  TEXT NOT NULL,
    name               TEXT NOT NULL,
    slug               TEXT,
    revision           TEXT,
    chipset            TEXT,
    socket             TEXT,
    release_date       TEXT,
    support_url        TEXT,
    first_seen         TEXT NOT NULL,
    last_seen          TEXT NOT NULL,
    UNIQUE (vendor, vendor_product_id)
);

CREATE TABLE IF NOT EXISTS artefact (
    artefact_id         INTEGER PRIMARY KEY,
    vendor              TEXT NOT NULL,
    vendor_artefact_id  TEXT NOT NULL,  -- MSI download_id, Gigabyte fileName,
                                        -- ASRock download URL, ASUS id
    kind                TEXT NOT NULL DEFAULT 'driver',  -- driver | bios | utility | firmware | other
    family_id           INTEGER REFERENCES family (family_id),
    component_hint      TEXT,            -- vendor-published categorisation, aids family assignment
    version_raw         TEXT,
    version_normalised  TEXT,            -- JSON int array from versions.parse, NULL if unparseable
    release_date        TEXT,
    file_size           INTEGER,
    url                 TEXT,
    sha256              TEXT,            -- vendor-published, or computed after Tier 2 fetch
    md5                 TEXT,            -- Gigabyte ?v= (verified = payload MD5), or computed
    os_raw              TEXT,
    is_beta             INTEGER NOT NULL DEFAULT 0,
    description_raw     TEXT,
    description_text    TEXT,
    first_seen          TEXT NOT NULL,
    last_seen           TEXT NOT NULL,
    UNIQUE (vendor, vendor_artefact_id)
);

CREATE TABLE IF NOT EXISTS board_artefact (
    board_id     INTEGER NOT NULL REFERENCES board (board_id),
    artefact_id  INTEGER NOT NULL REFERENCES artefact (artefact_id),
    listed_date  TEXT,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    PRIMARY KEY (board_id, artefact_id)
);

-- One row per `winidx crawl` invocation; snapshots on disk are keyed by run_date.
CREATE TABLE IF NOT EXISTS crawl_run (
    run_id      INTEGER PRIMARY KEY,
    vendor      TEXT NOT NULL,
    run_date    TEXT NOT NULL,
    started     TEXT NOT NULL,
    finished    TEXT,
    boards      INTEGER,
    artefacts   INTEGER,
    notes       TEXT
);

-- Tier 2: contents of fetched payloads. Only identity-relevant files are
-- recorded (.inf and the binaries they ship with); INF+SYS hash sets are the
-- cross-vendor identity (spec §6.2).
CREATE TABLE IF NOT EXISTS payload_file (
    payload_sha256  TEXT NOT NULL,
    path            TEXT NOT NULL,   -- within the archive, nested layers joined with '!'
    file_sha256     TEXT NOT NULL,
    size            INTEGER NOT NULL,
    PRIMARY KEY (payload_sha256, path)
);

CREATE TABLE IF NOT EXISTS inf (
    payload_sha256  TEXT NOT NULL,
    path            TEXT NOT NULL,
    inf_sha256      TEXT NOT NULL,
    provider        TEXT,
    class           TEXT,
    driver_date     TEXT,            -- from DriverVer, ISO
    driver_ver      TEXT,
    hwids           TEXT NOT NULL DEFAULT '[]',   -- JSON array
    PRIMARY KEY (payload_sha256, path)
);

CREATE INDEX IF NOT EXISTS idx_payload_file_hash ON payload_file (file_sha256);
CREATE INDEX IF NOT EXISTS idx_inf_hash ON inf (inf_sha256);

CREATE INDEX IF NOT EXISTS idx_artefact_family ON artefact (family_id);
CREATE INDEX IF NOT EXISTS idx_artefact_md5 ON artefact (md5);
CREATE INDEX IF NOT EXISTS idx_artefact_sha256 ON artefact (sha256);
