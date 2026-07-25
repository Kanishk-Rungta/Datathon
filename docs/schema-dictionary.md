# Schema dictionary

Generated from `backend/ksp_cip/infrastructure/db/schema.sql`, which is the
single authoritative definition. The loader conforms every incoming row to
the columns declared here, so a producer cannot widen the schema by accident.


## Raw layer

Landed source records, kept verbatim for replay and lineage.


### `raw_record`

| Column | Definition |
|---|---|
| `raw_id` | INTEGER PRIMARY KEY AUTOINCREMENT |
| `source_table` | TEXT    NOT NULL |
| `source_pk` | TEXT    NOT NULL |
| `payload_json` | TEXT    NOT NULL |
| `_batch_id` | TEXT    NOT NULL |
| `_extracted_at` | TEXT    NOT NULL |
| `_row_hash` | TEXT    NOT NULL |

## Control layer

Batch log, hash ledger, watermarks and data-quality results.


### `ctl_batch_log`

| Column | Definition |
|---|---|
| `batch_id` | TEXT PRIMARY KEY |
| `source_table` | TEXT NOT NULL |
| `row_count` | INTEGER NOT NULL |
| `min_pk` | TEXT |
| `max_pk` | TEXT |
| `content_sha256` | TEXT NOT NULL |
| `status` | TEXT NOT NULL |
| `object_key` | TEXT |
| `received_at` | TEXT NOT NULL |
| `loaded_at` | TEXT |
| `error_detail` | TEXT |

### `ctl_dq_result`

| Column | Definition |
|---|---|
| `dq_id` | INTEGER PRIMARY KEY AUTOINCREMENT |
| `batch_id` | TEXT NOT NULL |
| `source_table` | TEXT NOT NULL |
| `severity` | TEXT NOT NULL |
| `passed` | INTEGER NOT NULL |
| `failed_rows` | INTEGER NOT NULL DEFAULT 0 |
| `total_rows` | INTEGER NOT NULL DEFAULT 0 |
| `detail_json` | TEXT |

### `ctl_job_watermark`

| Column | Definition |
|---|---|
| `job_name` | TEXT PRIMARY KEY |
| `watermark_value` | TEXT |
| `updated_at` | TEXT NOT NULL |
| `detail_json` | TEXT |

### `ctl_row_hash`

| Column | Definition |
|---|---|
| `source_table` | TEXT NOT NULL |
| `source_pk` | TEXT NOT NULL |
| `row_hash` | TEXT NOT NULL |
| `updated_at` | TEXT NOT NULL |

## Curated layer

The organiser's FIR schema, unchanged. CIP-only enrichment columns are prefixed `cip_` and are always nullable.


### `curated_Accused`

| Column | Definition |
|---|---|
| `AccusedMasterID` | INTEGER PRIMARY KEY |
| `CaseMasterID` | INTEGER NOT NULL REFERENCES curated_CaseMaster (CaseMasterID) |
| `AccusedName` | TEXT |
| `AgeYear` | INTEGER |
| `GenderID` | TEXT |
| `PersonID` | TEXT |

### `curated_Act`

| Column | Definition |
|---|---|
| `ActCode` | TEXT PRIMARY KEY |
| `ActDescription` | TEXT NOT NULL |
| `ShortName` | TEXT |
| `Active` | INTEGER NOT NULL DEFAULT 1 |

### `curated_ActSectionAssociation`

| Column | Definition |
|---|---|
| `cip_assoc_id` | INTEGER PRIMARY KEY AUTOINCREMENT |
| `CaseMasterID` | INTEGER NOT NULL REFERENCES curated_CaseMaster (CaseMasterID) |
| `ActID` | TEXT NOT NULL |
| `SectionID` | TEXT NOT NULL |
| `ActOrderID` | INTEGER |
| `SectionOrderID` | INTEGER |

### `curated_ArrestSurrender`

| Column | Definition |
|---|---|
| `ArrestSurrenderID` | INTEGER PRIMARY KEY |
| `CaseMasterID` | INTEGER NOT NULL REFERENCES curated_CaseMaster (CaseMasterID) |
| `ArrestSurrenderTypeID` | INTEGER |
| `ArrestSurrenderDate` | TEXT |
| `ArrestSurrenderStateId` | INTEGER REFERENCES curated_State (StateID) |
| `ArrestSurrenderDistrictId` | INTEGER REFERENCES curated_District (DistrictID) |
| `PoliceStationID` | INTEGER REFERENCES curated_Unit (UnitID) |
| `IOID` | INTEGER REFERENCES curated_Employee (EmployeeID) |
| `CourtID` | INTEGER REFERENCES curated_Court (CourtID) |
| `AccusedMasterID` | INTEGER REFERENCES curated_Accused (AccusedMasterID) |
| `IsAccused` | INTEGER |
| `IsComplainantAccused` | INTEGER |

### `curated_CaseCategory`

| Column | Definition |
|---|---|
| `CaseCategoryID` | INTEGER PRIMARY KEY |
| `LookupValue` | TEXT NOT NULL |
| `CategoryCode` | INTEGER NOT NULL |

### `curated_CaseMaster`

| Column | Definition |
|---|---|
| `CaseMasterID` | INTEGER PRIMARY KEY |
| `CrimeNo` | TEXT NOT NULL |
| `CaseNo` | TEXT |
| `CrimeRegisteredDate` | TEXT |
| `PolicePersonID` | INTEGER REFERENCES curated_Employee (EmployeeID) |
| `PoliceStationID` | INTEGER REFERENCES curated_Unit (UnitID) |
| `CaseCategoryID` | INTEGER REFERENCES curated_CaseCategory (CaseCategoryID) |
| `GravityOffenceID` | INTEGER REFERENCES curated_GravityOffence (GravityOffenceID) |
| `CrimeMajorHeadID` | INTEGER REFERENCES curated_CrimeHead (CrimeHeadID) |
| `CrimeMinorHeadID` | INTEGER REFERENCES curated_CrimeSubHead (CrimeSubHeadID) |
| `CaseStatusID` | INTEGER REFERENCES curated_CaseStatusMaster (CaseStatusID) |
| `CourtID` | INTEGER REFERENCES curated_Court (CourtID) |
| `IncidentFromDate` | TEXT |
| `IncidentToDate` | TEXT |
| `InfoReceivedPSDate` | TEXT |
| `latitude` | REAL |
| `longitude` | REAL |
| `BriefFacts` | TEXT |
| `cip_brief_facts_kn` | TEXT |
| `cip_dq_flags` | TEXT |

### `curated_CaseStatusMaster`

| Column | Definition |
|---|---|
| `CaseStatusID` | INTEGER PRIMARY KEY |
| `CaseStatusName` | TEXT NOT NULL |

### `curated_CasteMaster`

| Column | Definition |
|---|---|
| `caste_master_id` | INTEGER PRIMARY KEY |
| `caste_master_name` | TEXT NOT NULL |

### `curated_ChargesheetDetails`

| Column | Definition |
|---|---|
| `CSID` | INTEGER PRIMARY KEY |
| `CaseMasterID` | INTEGER NOT NULL REFERENCES curated_CaseMaster (CaseMasterID) |
| `csdate` | TEXT |
| `cstype` | TEXT |
| `PolicePersonID` | INTEGER REFERENCES curated_Employee (EmployeeID) |

### `curated_ComplainantDetails`

| Column | Definition |
|---|---|
| `ComplainantID` | INTEGER PRIMARY KEY |
| `CaseMasterID` | INTEGER NOT NULL REFERENCES curated_CaseMaster (CaseMasterID) |
| `ComplainantName` | TEXT |
| `AgeYear` | INTEGER |
| `OccupationID` | INTEGER REFERENCES curated_OccupationMaster (OccupationID) |
| `ReligionID` | INTEGER REFERENCES curated_ReligionMaster (ReligionID) |
| `CasteID` | INTEGER REFERENCES curated_CasteMaster (caste_master_id) |
| `GenderID` | INTEGER |

### `curated_Court`

| Column | Definition |
|---|---|
| `CourtID` | INTEGER PRIMARY KEY |
| `CourtName` | TEXT NOT NULL |
| `DistrictID` | INTEGER REFERENCES curated_District (DistrictID) |
| `StateID` | INTEGER REFERENCES curated_State (StateID) |
| `Active` | INTEGER NOT NULL DEFAULT 1 |

### `curated_CrimeHead`

| Column | Definition |
|---|---|
| `CrimeHeadID` | INTEGER PRIMARY KEY |
| `CrimeGroupName` | TEXT NOT NULL |
| `Active` | INTEGER NOT NULL DEFAULT 1 |

### `curated_CrimeHeadActSection`

| Column | Definition |
|---|---|
| `CrimeHeadID` | INTEGER NOT NULL REFERENCES curated_CrimeHead (CrimeHeadID) |
| `ActCode` | TEXT NOT NULL |
| `SectionCode` | TEXT NOT NULL |

### `curated_CrimeSubHead`

| Column | Definition |
|---|---|
| `CrimeSubHeadID` | INTEGER PRIMARY KEY |
| `CrimeHeadID` | INTEGER REFERENCES curated_CrimeHead (CrimeHeadID) |
| `CrimeHeadName` | TEXT NOT NULL |
| `SeqID` | INTEGER |

### `curated_Designation`

| Column | Definition |
|---|---|
| `DesignationID` | INTEGER PRIMARY KEY |
| `DesignationName` | TEXT NOT NULL |
| `Active` | INTEGER NOT NULL DEFAULT 1 |
| `SortOrder` | INTEGER |

### `curated_District`

| Column | Definition |
|---|---|
| `DistrictID` | INTEGER PRIMARY KEY |
| `DistrictName` | TEXT NOT NULL |
| `StateID` | INTEGER REFERENCES curated_State (StateID) |
| `Active` | INTEGER NOT NULL DEFAULT 1 |

### `curated_Employee`

| Column | Definition |
|---|---|
| `EmployeeID` | INTEGER PRIMARY KEY |
| `DistrictID` | INTEGER REFERENCES curated_District (DistrictID) |
| `UnitID` | INTEGER REFERENCES curated_Unit (UnitID) |
| `RankID` | INTEGER REFERENCES curated_Rank (RankID) |
| `DesignationID` | INTEGER REFERENCES curated_Designation (DesignationID) |
| `KGID` | TEXT |
| `FirstName` | TEXT |
| `EmployeeDOB` | TEXT |
| `GenderID` | INTEGER |
| `BloodGroupID` | INTEGER |
| `PhysicallyChallenged` | INTEGER |
| `AppointmentDate` | TEXT |

### `curated_GravityOffence`

| Column | Definition |
|---|---|
| `GravityOffenceID` | INTEGER PRIMARY KEY |
| `LookupValue` | TEXT NOT NULL |

### `curated_OccupationMaster`

| Column | Definition |
|---|---|
| `OccupationID` | INTEGER PRIMARY KEY |
| `OccupationName` | TEXT NOT NULL |

### `curated_Rank`

| Column | Definition |
|---|---|
| `RankID` | INTEGER PRIMARY KEY |
| `RankName` | TEXT NOT NULL |
| `Hierarchy` | INTEGER |
| `Active` | INTEGER NOT NULL DEFAULT 1 |

### `curated_ReligionMaster`

| Column | Definition |
|---|---|
| `ReligionID` | INTEGER PRIMARY KEY |
| `ReligionName` | TEXT NOT NULL |

### `curated_Section`

| Column | Definition |
|---|---|
| `ActCode` | TEXT NOT NULL REFERENCES curated_Act (ActCode) |
| `SectionCode` | TEXT NOT NULL |
| `SectionDescription` | TEXT |
| `Active` | INTEGER NOT NULL DEFAULT 1 |

### `curated_State`

| Column | Definition |
|---|---|
| `StateID` | INTEGER PRIMARY KEY |
| `StateName` | TEXT NOT NULL |
| `NationalityID` | INTEGER |
| `Active` | INTEGER NOT NULL DEFAULT 1 |

### `curated_Unit`

| Column | Definition |
|---|---|
| `UnitID` | INTEGER PRIMARY KEY |
| `UnitName` | TEXT NOT NULL |
| `TypeID` | INTEGER REFERENCES curated_UnitType (UnitTypeID) |
| `ParentUnit` | INTEGER |
| `NationalityID` | INTEGER |
| `StateID` | INTEGER REFERENCES curated_State (StateID) |
| `DistrictID` | INTEGER REFERENCES curated_District (DistrictID) |
| `Active` | INTEGER NOT NULL DEFAULT 1 |
| `cip_latitude` | REAL |
| `cip_longitude` | REAL |

### `curated_UnitType`

| Column | Definition |
|---|---|
| `UnitTypeID` | INTEGER PRIMARY KEY |
| `UnitTypeName` | TEXT NOT NULL |
| `CityDistState` | TEXT |
| `Hierarchy` | INTEGER |
| `Active` | INTEGER NOT NULL DEFAULT 1 |

### `curated_Victim`

| Column | Definition |
|---|---|
| `VictimMasterID` | INTEGER PRIMARY KEY |
| `CaseMasterID` | INTEGER NOT NULL REFERENCES curated_CaseMaster (CaseMasterID) |
| `VictimName` | TEXT |
| `AgeYear` | INTEGER |
| `GenderID` | TEXT |
| `VictimPolice` | TEXT |

## Intelligence layer

Derived by the platform. Everything here is reproducible from the curated layer.


### `cip_case_priority`

| Column | Definition |
|---|---|
| `case_master_id` | INTEGER PRIMARY KEY REFERENCES curated_CaseMaster (CaseMasterID) |
| `crime_no` | TEXT NOT NULL |
| `score` | REAL NOT NULL |
| `band` | TEXT NOT NULL |
| `components_json` | TEXT NOT NULL |
| `computed_at` | TEXT NOT NULL |

### `cip_conversation_turn`

| Column | Definition |
|---|---|
| `session_id` | TEXT NOT NULL |
| `turn_seq` | INTEGER NOT NULL |
| `user_id` | TEXT NOT NULL |
| `role` | TEXT NOT NULL |
| `created_at` | TEXT NOT NULL |
| `expires_at` | TEXT NOT NULL |
| `user_text_original` | TEXT NOT NULL |
| `user_text_english` | TEXT NOT NULL |
| `user_language` | TEXT NOT NULL |
| `answer_text_english` | TEXT NOT NULL |
| `answer_text_display` | TEXT NOT NULL |
| `intent` | TEXT NOT NULL |
| `evidence_json` | TEXT NOT NULL DEFAULT '[]' |
| `pinned_json` | TEXT NOT NULL DEFAULT '{}' |
| `payload_type` | TEXT NOT NULL DEFAULT 'none' |

### `cip_early_warning_alert`

| Column | Definition |
|---|---|
| `alert_id` | TEXT PRIMARY KEY |
| `scope_type` | TEXT NOT NULL,         -- district \| unit |
| `scope_id` | INTEGER NOT NULL |
| `scope_name` | TEXT NOT NULL |
| `crime_sub_head_id` | INTEGER |
| `crime_sub_head` | TEXT |
| `window_start` | TEXT NOT NULL |
| `window_end` | TEXT NOT NULL |
| `observed_count` | INTEGER NOT NULL |
| `baseline_mean` | REAL NOT NULL |
| `baseline_stddev` | REAL NOT NULL |
| `z_score` | REAL NOT NULL |
| `severity` | TEXT NOT NULL |
| `case_ids` | TEXT NOT NULL |
| `explanation_json` | TEXT NOT NULL |
| `computed_at` | TEXT NOT NULL |

### `cip_embedding_index`

| Column | Definition |
|---|---|
| `doc_id` | TEXT PRIMARY KEY |
| `source_table` | TEXT NOT NULL |
| `source_pk` | TEXT NOT NULL |
| `case_master_id` | INTEGER |
| `unit_id` | INTEGER |
| `lang` | TEXT NOT NULL DEFAULT 'en' |
| `text_snippet` | TEXT NOT NULL |
| `embedding` | TEXT NOT NULL |
| `model_name` | TEXT NOT NULL |
| `created_at` | TEXT NOT NULL |

### `cip_embedding_stats`

| Column | Definition |
|---|---|
| `model_name` | TEXT PRIMARY KEY |
| `dimensions` | INTEGER NOT NULL |
| `doc_count` | INTEGER NOT NULL |
| `idf_json` | TEXT NOT NULL |
| `built_at` | TEXT NOT NULL |

### `cip_entity_resolution_link`

| Column | Definition |
|---|---|
| `link_id` | TEXT PRIMARY KEY |
| `left_accused_id` | INTEGER NOT NULL |
| `right_accused_id` | INTEGER NOT NULL |
| `score` | REAL NOT NULL |
| `decision` | TEXT NOT NULL,          -- auto_link \| review \| rejected |
| `review_state` | TEXT NOT NULL DEFAULT 'pending' |
| `features_json` | TEXT NOT NULL |
| `reviewed_by` | TEXT |
| `reviewed_at` | TEXT |
| `created_at` | TEXT NOT NULL |

### `cip_graph_edge`

| Column | Definition |
|---|---|
| `edge_id` | TEXT PRIMARY KEY |
| `src_type` | TEXT NOT NULL |
| `src_id` | TEXT NOT NULL |
| `dst_type` | TEXT NOT NULL |
| `dst_id` | TEXT NOT NULL |
| `edge_type` | TEXT NOT NULL |
| `weight` | REAL NOT NULL DEFAULT 1.0 |
| `case_ids` | TEXT NOT NULL DEFAULT '[]' |
| `unit_ids` | TEXT NOT NULL DEFAULT '[]' |
| `provenance` | TEXT NOT NULL DEFAULT 'inferred' |
| `detail_json` | TEXT |
| `created_at` | TEXT NOT NULL |

### `cip_hotspot_cell`

| Column | Definition |
|---|---|
| `cell_id` | TEXT PRIMARY KEY |
| `grid_row` | INTEGER NOT NULL |
| `grid_col` | INTEGER NOT NULL |
| `centroid_lat` | REAL NOT NULL |
| `centroid_lon` | REAL NOT NULL |
| `district_id` | INTEGER |
| `unit_id` | INTEGER |
| `window_start` | TEXT NOT NULL |
| `window_end` | TEXT NOT NULL |
| `case_count` | INTEGER NOT NULL |
| `baseline_mean` | REAL NOT NULL DEFAULT 0.0 |
| `intensity` | REAL NOT NULL DEFAULT 0.0 |
| `top_crime_sub_head` | TEXT |
| `case_ids` | TEXT NOT NULL |
| `computed_at` | TEXT NOT NULL |

### `cip_kv`

| Column | Definition |
|---|---|
| `namespace` | TEXT NOT NULL |
| `key` | TEXT NOT NULL |
| `value_json` | TEXT NOT NULL |
| `expires_at` | TEXT |
| `updated_at` | TEXT NOT NULL |

### `cip_person_identity`

| Column | Definition |
|---|---|
| `identity_id` | TEXT PRIMARY KEY |
| `canonical_name` | TEXT NOT NULL |
| `normalized_name` | TEXT NOT NULL |
| `phonetic_key` | TEXT NOT NULL |
| `age_estimate` | INTEGER |
| `gender_id` | TEXT |
| `district_ids` | TEXT NOT NULL DEFAULT '[]' |
| `source_ids` | TEXT NOT NULL DEFAULT '[]' |
| `case_ids` | TEXT NOT NULL DEFAULT '[]' |
| `member_count` | INTEGER NOT NULL DEFAULT 1 |
| `created_at` | TEXT NOT NULL |

### `cip_repeat_offender_score`

| Column | Definition |
|---|---|
| `identity_id` | TEXT PRIMARY KEY |
| `canonical_name` | TEXT NOT NULL |
| `case_count` | INTEGER NOT NULL |
| `distinct_crime_heads` | INTEGER NOT NULL |
| `recency_days` | INTEGER |
| `gravity_escalation` | REAL NOT NULL DEFAULT 0.0 |
| `network_centrality` | REAL NOT NULL DEFAULT 0.0 |
| `score` | REAL NOT NULL |
| `band` | TEXT NOT NULL |
| `components_json` | TEXT NOT NULL |
| `case_ids` | TEXT NOT NULL |
| `district_ids` | TEXT NOT NULL DEFAULT '[]' |
| `unit_ids` | TEXT NOT NULL DEFAULT '[]' |
| `computed_at` | TEXT NOT NULL |

### `cip_unit_closure`

| Column | Definition |
|---|---|
| `ancestor_unit_id` | INTEGER NOT NULL |
| `descendant_unit_id` | INTEGER NOT NULL |
| `depth` | INTEGER NOT NULL |

### `cip_user_account`

| Column | Definition |
|---|---|
| `user_id` | TEXT PRIMARY KEY |
| `username` | TEXT NOT NULL UNIQUE |
| `display_name` | TEXT NOT NULL |
| `role` | TEXT NOT NULL |
| `home_unit_id` | INTEGER |
| `district_id` | INTEGER |
| `password_salt` | TEXT NOT NULL |
| `password_hash` | TEXT NOT NULL |
| `active` | INTEGER NOT NULL DEFAULT 1 |
| `created_at` | TEXT NOT NULL |

## Synthetic extension

NOT part of the organiser's schema. Clearly marked at every layer.


### `ext_financial_transaction`

| Column | Definition |
|---|---|
| `txn_id` | TEXT PRIMARY KEY |
| `case_master_id` | INTEGER REFERENCES curated_CaseMaster (CaseMasterID) |
| `from_kind` | TEXT NOT NULL,          -- accused \| entity |
| `from_ref` | TEXT NOT NULL |
| `from_label` | TEXT NOT NULL |
| `to_kind` | TEXT NOT NULL |
| `to_ref` | TEXT NOT NULL |
| `to_label` | TEXT NOT NULL |
| `amount` | REAL NOT NULL |
| `currency` | TEXT NOT NULL DEFAULT 'INR' |
| `txn_date` | TEXT NOT NULL |
| `channel` | TEXT NOT NULL |
| `is_extension` | INTEGER NOT NULL DEFAULT 1 |
| `created_at` | TEXT NOT NULL |

## Audit

Append-only. Never purged by retention jobs.


### `audit_event`

| Column | Definition |
|---|---|
| `event_id` | TEXT PRIMARY KEY |
| `occurred_at` | TEXT NOT NULL |
| `actor_user_id` | TEXT |
| `actor_role` | TEXT |
| `scope_summary` | TEXT |
| `purpose_code` | TEXT NOT NULL |
| `action` | TEXT NOT NULL |
| `agent` | TEXT |
| `object_type` | TEXT |
| `object_ids` | TEXT NOT NULL DEFAULT '[]' |
| `request_hash` | TEXT |
| `outcome` | TEXT NOT NULL |
| `latency_ms` | INTEGER |
| `correlation_id` | TEXT |
| `detail_json` | TEXT |
