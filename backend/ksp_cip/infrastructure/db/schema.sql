-- =====================================================================
-- KSP-CIP physical schema (SQLite dialect; Catalyst Data Store DDL is
-- generated from this file by scripts/generate_zcql_ddl.py)
--
-- Layering follows the medallion model of architecture §6.2:
--   raw_*       batch-faithful landing, source column names verbatim
--   curated_*   analysis-ready mirror of the source ER document, 1:1,
--               column names and semantics UNCHANGED (hard requirement)
--   cip_*       platform-derived structures (graph, vectors, scores)
--   ext_*       SYNTHETIC EXTENSIONS not present in the source schema
--   ctl_*       control plane (batches, watermarks, data quality)
--   audit_*     append-only audit trail
--
-- Naming rule: anything prefixed ext_ is NOT part of the organiser's
-- schema and is surfaced to users as an explicitly marked extension.
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------------ raw
CREATE TABLE IF NOT EXISTS raw_record (
    raw_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_table      TEXT    NOT NULL,
    source_pk         TEXT    NOT NULL,
    payload_json      TEXT    NOT NULL,
    _batch_id         TEXT    NOT NULL,
    _extracted_at     TEXT    NOT NULL,
    _row_hash         TEXT    NOT NULL,
    UNIQUE (source_table, source_pk, _batch_id)
);
CREATE INDEX IF NOT EXISTS ix_raw_record_table ON raw_record (source_table, source_pk);
CREATE INDEX IF NOT EXISTS ix_raw_record_batch ON raw_record (_batch_id);

-- Schema version ledger. Declared here, not only in migrations.py, so it
-- reaches the provisioning manifest like every other table: it is created
-- imperatively by migrations._ensure_version_table() for SQLite (which must
-- run before this file is applied), but a backend with no executescript --
-- Catalyst -- could never obtain it that way, so the version check it exists
-- to support was impossible there. Both paths use CREATE TABLE IF NOT EXISTS,
-- so declaring it twice is a no-op rather than a conflict.
CREATE TABLE IF NOT EXISTS ctl_schema_version (
    version           INTEGER PRIMARY KEY,
    description       TEXT NOT NULL,
    applied_at        TEXT NOT NULL
);

-- Hash ledger enabling the architecture's hash-diff CDC strategy (§5.4).
CREATE TABLE IF NOT EXISTS ctl_row_hash (
    source_table      TEXT NOT NULL,
    source_pk         TEXT NOT NULL,
    row_hash          TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    PRIMARY KEY (source_table, source_pk)
);

CREATE TABLE IF NOT EXISTS ctl_batch_log (
    batch_id          TEXT PRIMARY KEY,
    source_table      TEXT NOT NULL,
    row_count         INTEGER NOT NULL,
    min_pk            TEXT,
    max_pk            TEXT,
    content_sha256    TEXT NOT NULL,
    status            TEXT NOT NULL,
    object_key        TEXT,
    received_at       TEXT NOT NULL,
    loaded_at         TEXT,
    error_detail      TEXT
);
CREATE INDEX IF NOT EXISTS ix_ctl_batch_status ON ctl_batch_log (status, source_table);

CREATE TABLE IF NOT EXISTS ctl_job_watermark (
    job_name          TEXT PRIMARY KEY,
    watermark_value   TEXT,
    updated_at        TEXT NOT NULL,
    detail_json       TEXT
);

CREATE TABLE IF NOT EXISTS ctl_dq_result (
    dq_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id          TEXT NOT NULL,
    source_table      TEXT NOT NULL,
    check_name        TEXT NOT NULL,
    severity          TEXT NOT NULL,
    passed            INTEGER NOT NULL,
    failed_rows       INTEGER NOT NULL DEFAULT 0,
    total_rows        INTEGER NOT NULL DEFAULT 0,
    detail_json       TEXT,
    checked_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ctl_dq_batch ON ctl_dq_result (batch_id);

-- =====================================================================
-- curated_* : verbatim mirror of the source ER document
-- =====================================================================

CREATE TABLE IF NOT EXISTS curated_State (
    StateID           INTEGER PRIMARY KEY,
    StateName         TEXT NOT NULL,
    NationalityID     INTEGER,
    Active            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS curated_District (
    DistrictID        INTEGER PRIMARY KEY,
    DistrictName      TEXT NOT NULL,
    StateID           INTEGER REFERENCES curated_State (StateID),
    Active            INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_district_state ON curated_District (StateID);

CREATE TABLE IF NOT EXISTS curated_UnitType (
    UnitTypeID        INTEGER PRIMARY KEY,
    UnitTypeName      TEXT NOT NULL,
    CityDistState     TEXT,
    Hierarchy         INTEGER,
    Active            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS curated_Unit (
    UnitID            INTEGER PRIMARY KEY,
    UnitName          TEXT NOT NULL,
    TypeID            INTEGER REFERENCES curated_UnitType (UnitTypeID),
    ParentUnit        INTEGER,
    NationalityID     INTEGER,
    StateID           INTEGER REFERENCES curated_State (StateID),
    DistrictID        INTEGER REFERENCES curated_District (DistrictID),
    Active            INTEGER NOT NULL DEFAULT 1,
    -- CIP-only enrichment: station coordinates used for hotspot maths.
    -- Not part of the source schema; nullable, never required by joins.
    cip_latitude      REAL,
    cip_longitude     REAL
);
CREATE INDEX IF NOT EXISTS ix_unit_district ON curated_Unit (DistrictID);
CREATE INDEX IF NOT EXISTS ix_unit_parent ON curated_Unit (ParentUnit);

-- Closure table over Unit.ParentUnit for O(1) subtree ACL checks (§6.2).
CREATE TABLE IF NOT EXISTS cip_unit_closure (
    ancestor_unit_id  INTEGER NOT NULL,
    descendant_unit_id INTEGER NOT NULL,
    depth             INTEGER NOT NULL,
    PRIMARY KEY (ancestor_unit_id, descendant_unit_id)
);
CREATE INDEX IF NOT EXISTS ix_closure_descendant ON cip_unit_closure (descendant_unit_id);

CREATE TABLE IF NOT EXISTS curated_Rank (
    RankID            INTEGER PRIMARY KEY,
    RankName          TEXT NOT NULL,
    Hierarchy         INTEGER,
    Active            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS curated_Designation (
    DesignationID     INTEGER PRIMARY KEY,
    DesignationName   TEXT NOT NULL,
    Active            INTEGER NOT NULL DEFAULT 1,
    SortOrder         INTEGER
);

CREATE TABLE IF NOT EXISTS curated_Employee (
    EmployeeID        INTEGER PRIMARY KEY,
    DistrictID        INTEGER REFERENCES curated_District (DistrictID),
    UnitID            INTEGER REFERENCES curated_Unit (UnitID),
    RankID            INTEGER REFERENCES curated_Rank (RankID),
    DesignationID     INTEGER REFERENCES curated_Designation (DesignationID),
    KGID              TEXT,
    FirstName         TEXT,
    EmployeeDOB       TEXT,
    GenderID          INTEGER,
    BloodGroupID      INTEGER,
    PhysicallyChallenged INTEGER,
    AppointmentDate   TEXT
);
CREATE INDEX IF NOT EXISTS ix_employee_unit ON curated_Employee (UnitID);

CREATE TABLE IF NOT EXISTS curated_Court (
    CourtID           INTEGER PRIMARY KEY,
    CourtName         TEXT NOT NULL,
    DistrictID        INTEGER REFERENCES curated_District (DistrictID),
    StateID           INTEGER REFERENCES curated_State (StateID),
    Active            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS curated_CaseCategory (
    CaseCategoryID    INTEGER PRIMARY KEY,
    LookupValue       TEXT NOT NULL,
    -- category code digit used to build CrimeNo (1=FIR, 3=UDR, 4=PAR, 8=Zero FIR)
    CategoryCode      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS curated_GravityOffence (
    GravityOffenceID  INTEGER PRIMARY KEY,
    LookupValue       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS curated_CaseStatusMaster (
    CaseStatusID      INTEGER PRIMARY KEY,
    CaseStatusName    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS curated_CasteMaster (
    caste_master_id   INTEGER PRIMARY KEY,
    caste_master_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS curated_ReligionMaster (
    ReligionID        INTEGER PRIMARY KEY,
    ReligionName      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS curated_OccupationMaster (
    OccupationID      INTEGER PRIMARY KEY,
    OccupationName    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS curated_CrimeHead (
    CrimeHeadID       INTEGER PRIMARY KEY,
    CrimeGroupName    TEXT NOT NULL,
    Active            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS curated_CrimeSubHead (
    CrimeSubHeadID    INTEGER PRIMARY KEY,
    CrimeHeadID       INTEGER REFERENCES curated_CrimeHead (CrimeHeadID),
    CrimeHeadName     TEXT NOT NULL,
    SeqID             INTEGER
);
CREATE INDEX IF NOT EXISTS ix_subhead_head ON curated_CrimeSubHead (CrimeHeadID);

CREATE TABLE IF NOT EXISTS curated_Act (
    ActCode           TEXT PRIMARY KEY,
    ActDescription    TEXT NOT NULL,
    ShortName         TEXT,
    Active            INTEGER NOT NULL DEFAULT 1
);

-- Source declares no PK on Section; architecture §15/S3 prescribes a curated
-- composite unique. Compatibility preserved: no column added or renamed.
CREATE TABLE IF NOT EXISTS curated_Section (
    ActCode           TEXT NOT NULL REFERENCES curated_Act (ActCode),
    SectionCode       TEXT NOT NULL,
    SectionDescription TEXT,
    Active            INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (ActCode, SectionCode)
);

CREATE TABLE IF NOT EXISTS curated_CrimeHeadActSection (
    CrimeHeadID       INTEGER NOT NULL REFERENCES curated_CrimeHead (CrimeHeadID),
    ActCode           TEXT NOT NULL,
    SectionCode       TEXT NOT NULL,
    PRIMARY KEY (CrimeHeadID, ActCode, SectionCode)
);

CREATE TABLE IF NOT EXISTS curated_CaseMaster (
    CaseMasterID      INTEGER PRIMARY KEY,
    CrimeNo           TEXT NOT NULL,
    CaseNo            TEXT,
    CrimeRegisteredDate TEXT,
    PolicePersonID    INTEGER REFERENCES curated_Employee (EmployeeID),
    PoliceStationID   INTEGER REFERENCES curated_Unit (UnitID),
    CaseCategoryID    INTEGER REFERENCES curated_CaseCategory (CaseCategoryID),
    GravityOffenceID  INTEGER REFERENCES curated_GravityOffence (GravityOffenceID),
    CrimeMajorHeadID  INTEGER REFERENCES curated_CrimeHead (CrimeHeadID),
    CrimeMinorHeadID  INTEGER REFERENCES curated_CrimeSubHead (CrimeSubHeadID),
    CaseStatusID      INTEGER REFERENCES curated_CaseStatusMaster (CaseStatusID),
    CourtID           INTEGER REFERENCES curated_Court (CourtID),
    IncidentFromDate  TEXT,
    IncidentToDate    TEXT,
    InfoReceivedPSDate TEXT,
    latitude          REAL,
    longitude         REAL,
    BriefFacts        TEXT,
    -- CIP-only: Kannada rendering of BriefFacts produced by the language
    -- service at generation time. Never overwrites the source column.
    cip_brief_facts_kn TEXT,
    cip_dq_flags      TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_case_crimeno ON curated_CaseMaster (CrimeNo);
CREATE INDEX IF NOT EXISTS ix_case_station_date ON curated_CaseMaster (PoliceStationID, CrimeRegisteredDate);
CREATE INDEX IF NOT EXISTS ix_case_minor_head ON curated_CaseMaster (CrimeMinorHeadID, CrimeRegisteredDate);
CREATE INDEX IF NOT EXISTS ix_case_status ON curated_CaseMaster (CaseStatusID);
CREATE INDEX IF NOT EXISTS ix_case_registered ON curated_CaseMaster (CrimeRegisteredDate);

CREATE TABLE IF NOT EXISTS curated_ComplainantDetails (
    ComplainantID     INTEGER PRIMARY KEY,
    CaseMasterID      INTEGER NOT NULL REFERENCES curated_CaseMaster (CaseMasterID),
    ComplainantName   TEXT,
    AgeYear           INTEGER,
    OccupationID      INTEGER REFERENCES curated_OccupationMaster (OccupationID),
    ReligionID        INTEGER REFERENCES curated_ReligionMaster (ReligionID),
    CasteID           INTEGER REFERENCES curated_CasteMaster (caste_master_id),
    GenderID          INTEGER
);
CREATE INDEX IF NOT EXISTS ix_complainant_case ON curated_ComplainantDetails (CaseMasterID);

CREATE TABLE IF NOT EXISTS curated_Victim (
    VictimMasterID    INTEGER PRIMARY KEY,
    CaseMasterID      INTEGER NOT NULL REFERENCES curated_CaseMaster (CaseMasterID),
    VictimName        TEXT,
    AgeYear           INTEGER,
    GenderID          TEXT,
    VictimPolice      TEXT
);
CREATE INDEX IF NOT EXISTS ix_victim_case ON curated_Victim (CaseMasterID);

CREATE TABLE IF NOT EXISTS curated_Accused (
    AccusedMasterID   INTEGER PRIMARY KEY,
    CaseMasterID      INTEGER NOT NULL REFERENCES curated_CaseMaster (CaseMasterID),
    AccusedName       TEXT,
    AgeYear           INTEGER,
    GenderID          TEXT,
    PersonID          TEXT
);
CREATE INDEX IF NOT EXISTS ix_accused_case ON curated_Accused (CaseMasterID);
CREATE INDEX IF NOT EXISTS ix_accused_name ON curated_Accused (AccusedName);

CREATE TABLE IF NOT EXISTS curated_ActSectionAssociation (
    -- Surrogate key: architecture §15/S2 remedy. Source has no PK; the
    -- natural set-key (CaseMasterID, ActID, SectionID) is enforced unique.
    cip_assoc_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    CaseMasterID      INTEGER NOT NULL REFERENCES curated_CaseMaster (CaseMasterID),
    ActID             TEXT NOT NULL,
    SectionID         TEXT NOT NULL,
    ActOrderID        INTEGER,
    SectionOrderID    INTEGER,
    UNIQUE (CaseMasterID, ActID, SectionID)
);
CREATE INDEX IF NOT EXISTS ix_actsection_case ON curated_ActSectionAssociation (CaseMasterID);

CREATE TABLE IF NOT EXISTS curated_ArrestSurrender (
    ArrestSurrenderID INTEGER PRIMARY KEY,
    CaseMasterID      INTEGER NOT NULL REFERENCES curated_CaseMaster (CaseMasterID),
    ArrestSurrenderTypeID INTEGER,
    ArrestSurrenderDate TEXT,
    ArrestSurrenderStateId INTEGER REFERENCES curated_State (StateID),
    ArrestSurrenderDistrictId INTEGER REFERENCES curated_District (DistrictID),
    PoliceStationID   INTEGER REFERENCES curated_Unit (UnitID),
    IOID              INTEGER REFERENCES curated_Employee (EmployeeID),
    CourtID           INTEGER REFERENCES curated_Court (CourtID),
    AccusedMasterID   INTEGER REFERENCES curated_Accused (AccusedMasterID),
    IsAccused         INTEGER,
    IsComplainantAccused INTEGER
);
CREATE INDEX IF NOT EXISTS ix_arrest_case ON curated_ArrestSurrender (CaseMasterID);
CREATE INDEX IF NOT EXISTS ix_arrest_accused ON curated_ArrestSurrender (AccusedMasterID);

CREATE TABLE IF NOT EXISTS curated_ChargesheetDetails (
    CSID              INTEGER PRIMARY KEY,
    CaseMasterID      INTEGER NOT NULL REFERENCES curated_CaseMaster (CaseMasterID),
    csdate            TEXT,
    cstype            TEXT,
    PolicePersonID    INTEGER REFERENCES curated_Employee (EmployeeID)
);
CREATE INDEX IF NOT EXISTS ix_chargesheet_case ON curated_ChargesheetDetails (CaseMasterID);

-- =====================================================================
-- cip_* : platform-derived intelligence structures
-- =====================================================================

CREATE TABLE IF NOT EXISTS cip_graph_edge (
    edge_id           TEXT PRIMARY KEY,
    src_type          TEXT NOT NULL,
    src_id            TEXT NOT NULL,
    dst_type          TEXT NOT NULL,
    dst_id            TEXT NOT NULL,
    edge_type         TEXT NOT NULL,
    weight            REAL NOT NULL DEFAULT 1.0,
    case_ids          TEXT NOT NULL DEFAULT '[]',
    unit_ids          TEXT NOT NULL DEFAULT '[]',
    provenance        TEXT NOT NULL DEFAULT 'inferred',
    detail_json       TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_edge_src ON cip_graph_edge (src_type, src_id);
CREATE INDEX IF NOT EXISTS ix_edge_dst ON cip_graph_edge (dst_type, dst_id);
CREATE INDEX IF NOT EXISTS ix_edge_type ON cip_graph_edge (edge_type);

CREATE TABLE IF NOT EXISTS cip_embedding_index (
    doc_id            TEXT PRIMARY KEY,
    source_table      TEXT NOT NULL,
    source_pk         TEXT NOT NULL,
    case_master_id    INTEGER,
    unit_id           INTEGER,
    lang              TEXT NOT NULL DEFAULT 'en',
    text_snippet      TEXT NOT NULL,
    embedding         TEXT NOT NULL,
    model_name        TEXT NOT NULL,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_embedding_case ON cip_embedding_index (case_master_id);
CREATE INDEX IF NOT EXISTS ix_embedding_model ON cip_embedding_index (model_name);

-- Vocabulary/IDF statistics for the deterministic local embedding model.
CREATE TABLE IF NOT EXISTS cip_embedding_stats (
    model_name        TEXT PRIMARY KEY,
    dimensions        INTEGER NOT NULL,
    doc_count         INTEGER NOT NULL,
    idf_json          TEXT NOT NULL,
    built_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cip_person_identity (
    identity_id       TEXT PRIMARY KEY,
    canonical_name    TEXT NOT NULL,
    normalized_name   TEXT NOT NULL,
    phonetic_key      TEXT NOT NULL,
    age_estimate      INTEGER,
    gender_id         TEXT,
    district_ids      TEXT NOT NULL DEFAULT '[]',
    source_ids        TEXT NOT NULL DEFAULT '[]',
    case_ids          TEXT NOT NULL DEFAULT '[]',
    member_count      INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_identity_norm ON cip_person_identity (normalized_name);
CREATE INDEX IF NOT EXISTS ix_identity_phonetic ON cip_person_identity (phonetic_key);

-- The two accused references are declared, not implied: the review queue
-- joins both back to curated_Accused, and on Catalyst a join is only possible
-- through a declared relationship. Left undeclared, the review endpoint failed
-- with "No relationship between tables la and l".
CREATE TABLE IF NOT EXISTS cip_entity_resolution_link (
    link_id           TEXT PRIMARY KEY,
    left_accused_id   INTEGER NOT NULL REFERENCES curated_Accused (AccusedMasterID),
    right_accused_id  INTEGER NOT NULL REFERENCES curated_Accused (AccusedMasterID),
    score             REAL NOT NULL,
    decision          TEXT NOT NULL,          -- auto_link | review | rejected
    review_state      TEXT NOT NULL DEFAULT 'pending',
    features_json     TEXT NOT NULL,
    reviewed_by       TEXT,
    reviewed_at       TEXT,
    created_at        TEXT NOT NULL,
    UNIQUE (left_accused_id, right_accused_id)
);
CREATE INDEX IF NOT EXISTS ix_er_decision ON cip_entity_resolution_link (decision, review_state);

CREATE TABLE IF NOT EXISTS cip_repeat_offender_score (
    identity_id       TEXT PRIMARY KEY,
    canonical_name    TEXT NOT NULL,
    case_count        INTEGER NOT NULL,
    distinct_crime_heads INTEGER NOT NULL,
    recency_days      INTEGER,
    gravity_escalation REAL NOT NULL DEFAULT 0.0,
    network_centrality REAL NOT NULL DEFAULT 0.0,
    score             REAL NOT NULL,
    band              TEXT NOT NULL,
    components_json   TEXT NOT NULL,
    case_ids          TEXT NOT NULL,
    district_ids      TEXT NOT NULL DEFAULT '[]',
    unit_ids          TEXT NOT NULL DEFAULT '[]',
    computed_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_offender_score ON cip_repeat_offender_score (score DESC);

CREATE TABLE IF NOT EXISTS cip_hotspot_cell (
    cell_id           TEXT PRIMARY KEY,
    grid_row          INTEGER NOT NULL,
    grid_col          INTEGER NOT NULL,
    centroid_lat      REAL NOT NULL,
    centroid_lon      REAL NOT NULL,
    district_id       INTEGER,
    unit_id           INTEGER,
    window_start      TEXT NOT NULL,
    window_end        TEXT NOT NULL,
    case_count        INTEGER NOT NULL,
    baseline_mean     REAL NOT NULL DEFAULT 0.0,
    intensity         REAL NOT NULL DEFAULT 0.0,
    top_crime_sub_head TEXT,
    case_ids          TEXT NOT NULL,
    computed_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_hotspot_district ON cip_hotspot_cell (district_id, intensity DESC);

CREATE TABLE IF NOT EXISTS cip_early_warning_alert (
    alert_id          TEXT PRIMARY KEY,
    scope_type        TEXT NOT NULL,         -- district | unit
    scope_id          INTEGER NOT NULL,
    scope_name        TEXT NOT NULL,
    crime_sub_head_id INTEGER,
    crime_sub_head    TEXT,
    window_start      TEXT NOT NULL,
    window_end        TEXT NOT NULL,
    observed_count    INTEGER NOT NULL,
    baseline_mean     REAL NOT NULL,
    baseline_stddev   REAL NOT NULL,
    z_score           REAL NOT NULL,
    severity          TEXT NOT NULL,
    case_ids          TEXT NOT NULL,
    explanation_json  TEXT NOT NULL,
    computed_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_alert_scope ON cip_early_warning_alert (scope_type, scope_id, computed_at);

CREATE TABLE IF NOT EXISTS cip_case_priority (
    case_master_id    INTEGER PRIMARY KEY REFERENCES curated_CaseMaster (CaseMasterID),
    crime_no          TEXT NOT NULL,
    score             REAL NOT NULL,
    band              TEXT NOT NULL,
    components_json   TEXT NOT NULL,
    computed_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_priority_score ON cip_case_priority (score DESC);

CREATE TABLE IF NOT EXISTS cip_user_account (
    user_id           TEXT PRIMARY KEY,
    username          TEXT NOT NULL UNIQUE,
    display_name      TEXT NOT NULL,
    role              TEXT NOT NULL,
    home_unit_id      INTEGER,
    district_id       INTEGER,
    password_salt     TEXT NOT NULL,
    password_hash     TEXT NOT NULL,
    active            INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cip_conversation_turn (
    session_id        TEXT NOT NULL,
    turn_seq          INTEGER NOT NULL,
    user_id           TEXT NOT NULL,
    role              TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    expires_at        TEXT NOT NULL,
    user_text_original TEXT NOT NULL,
    user_text_english TEXT NOT NULL,
    user_language     TEXT NOT NULL,
    answer_text_english TEXT NOT NULL,
    answer_text_display TEXT NOT NULL,
    intent            TEXT NOT NULL,
    evidence_json     TEXT NOT NULL DEFAULT '[]',
    pinned_json       TEXT NOT NULL DEFAULT '{}',
    payload_type      TEXT NOT NULL DEFAULT 'none',
    PRIMARY KEY (session_id, turn_seq)
);
CREATE INDEX IF NOT EXISTS ix_conversation_user ON cip_conversation_turn (user_id, created_at);

-- `kv_key`, not `key`: Catalyst Data Store rejects `key` as a reserved
-- keyword ("Column name cannot contain reserved keywords"), found when
-- provisioning the live schema. Renamed here rather than mapped per-backend
-- so both backends keep one column name. See migration 4.
CREATE TABLE IF NOT EXISTS cip_kv (
    namespace         TEXT NOT NULL,
    kv_key            TEXT NOT NULL,
    value_json        TEXT NOT NULL,
    expires_at        TEXT,
    updated_at        TEXT NOT NULL,
    PRIMARY KEY (namespace, kv_key)
);
CREATE INDEX IF NOT EXISTS ix_kv_expiry ON cip_kv (expires_at);

-- =====================================================================
-- ext_* : SYNTHETIC EXTENSIONS — not present in the organiser's schema
-- =====================================================================

CREATE TABLE IF NOT EXISTS ext_financial_transaction (
    txn_id            TEXT PRIMARY KEY,
    case_master_id    INTEGER REFERENCES curated_CaseMaster (CaseMasterID),
    from_kind         TEXT NOT NULL,          -- accused | entity
    from_ref          TEXT NOT NULL,
    from_label        TEXT NOT NULL,
    to_kind           TEXT NOT NULL,
    to_ref            TEXT NOT NULL,
    to_label          TEXT NOT NULL,
    amount            REAL NOT NULL,
    currency          TEXT NOT NULL DEFAULT 'INR',
    txn_date          TEXT NOT NULL,
    channel           TEXT NOT NULL,
    is_extension      INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ext_txn_case ON ext_financial_transaction (case_master_id);
CREATE INDEX IF NOT EXISTS ix_ext_txn_from ON ext_financial_transaction (from_ref);
CREATE INDEX IF NOT EXISTS ix_ext_txn_to ON ext_financial_transaction (to_ref);

-- =====================================================================
-- audit_* : append-only. No application role holds DELETE on this table.
-- =====================================================================

CREATE TABLE IF NOT EXISTS audit_event (
    event_id          TEXT PRIMARY KEY,
    occurred_at       TEXT NOT NULL,
    actor_user_id     TEXT,
    actor_role        TEXT,
    scope_summary     TEXT,
    purpose_code      TEXT NOT NULL,
    action            TEXT NOT NULL,
    agent             TEXT,
    object_type       TEXT,
    object_ids        TEXT NOT NULL DEFAULT '[]',
    request_hash      TEXT,
    outcome           TEXT NOT NULL,
    latency_ms        INTEGER,
    correlation_id    TEXT,
    detail_json       TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_actor ON audit_event (actor_user_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_audit_action ON audit_event (action, occurred_at);
CREATE INDEX IF NOT EXISTS ix_audit_correlation ON audit_event (correlation_id);
