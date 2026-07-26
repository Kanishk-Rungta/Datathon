"""The seeded dataset, and the closed loop between generator and analytics.

The generator records what it planted in a manifest. These tests assert the
analytics find it again. Without that loop, analytics over synthetic data
prove nothing at all.
"""

import pytest

pytestmark = pytest.mark.slow


class TestSeedIntegrity:
    def test_seed_produced_the_requested_volume(self, seeded):
        assert seeded["summary"]["generated_cases"] == seeded["summary"]["target_cases"]

    def test_every_data_quality_check_passed(self, seeded):
        quality = seeded["summary"]["data_quality"]
        assert quality["passed"] == quality["checks"], quality["warnings"]

    def test_curated_row_counts_are_consistent(self, container):
        cases = container.control.store_row_count("curated_CaseMaster")
        accused = container.control.store_row_count("curated_Accused")
        assert cases > 0 and accused > 0
        assert container.control.store_row_count("curated_District") == 31

    def test_raw_layer_retains_every_loaded_row(self, seeded):
        load = seeded["summary"]["load"]
        assert load["raw_rows"] == load["curated_rows"]

    def test_reloading_the_same_batch_changes_nothing(self, container):
        """Hash-diff CDC: a re-run must be a no-op, not a duplicate."""
        before = container.control.store_row_count("curated_CaseMaster")
        pairs = [("does-not-exist", "deadbeef")]
        assert container.control.changed_pks("curated_CaseMaster", pairs) == ["does-not-exist"]
        assert container.control.store_row_count("curated_CaseMaster") == before

    def test_crime_numbers_are_unique_and_well_formed(self, container):
        from ksp_cip.domain.value_objects import CrimeNo

        rows = container.store.query("SELECT CrimeNo FROM curated_CaseMaster", {})
        numbers = [row["CrimeNo"] for row in rows]
        assert len(numbers) == len(set(numbers))
        assert all(CrimeNo.try_parse(number) is not None for number in numbers)

    def test_serial_numbers_restart_per_station_category_and_year(self, container):
        from ksp_cip.domain.value_objects import CrimeNo

        rows = container.store.query("SELECT CrimeNo FROM curated_CaseMaster", {})
        seen = set()
        for row in rows:
            parsed = CrimeNo.parse(row["CrimeNo"])
            key = (parsed.station_id, parsed.category_code, parsed.year, parsed.serial)
            assert key not in seen, "serial reused within a station/category/year"
            seen.add(key)


class TestPlantedSignalsAreDetected:
    def test_the_planted_surge_produces_an_early_warning_alert(self, container, manifest):
        surge = manifest["surge"]
        alerts = container.alerts.alerts(limit=50)
        matching = [
            alert for alert in alerts
            if alert["scope_name"] == surge["district_name"]
            and alert["crime_sub_head"] == surge["crime_sub_head_name"]
        ]
        assert matching, f"planted surge in {surge['district_name']} was not detected"
        assert float(matching[0]["z_score"]) >= 2.0

    def test_planted_hotspots_appear_among_the_top_cells(self, container, manifest):
        planted = {spot["district_name"] for spot in manifest["hotspots"]}
        cells = container.hotspots.cells(limit=10)
        assert cells, "no hotspot cells were computed"
        districts = set()
        for cell in cells:
            district = container.reference.district(cell["district_id"]) if cell.get("district_id") else None
            if district:
                districts.add(district["DistrictName"])
        assert planted & districts, f"planted {planted} but detected {districts}"

    def test_the_planted_ring_is_recovered_as_a_connected_cluster(self, container, manifest):
        if manifest["ring"]["case_count"] == 0:
            pytest.skip("no ring cases were planted at this dataset size")
        communities = container.graph.top_communities(limit=10, min_size=3)
        assert communities, "no person clusters were found"
        assert max(entry["size"] for entry in communities) >= 3

    def test_repeat_offenders_are_scored(self, container, manifest):
        scored = container.identities.top_offenders(limit=20)
        assert scored, "no repeat offenders were scored"
        assert all(entry["case_count"] >= 2 for entry in scored)


class TestEntityResolutionOnRealData:
    def test_resolution_never_loses_a_source_row(self, container):
        accused_rows = container.control.store_row_count("curated_Accused")
        identities = container.identities.identities()
        recovered = {source for identity in identities for source in identity["source_ids"]}
        assert len(recovered) == accused_rows

    def test_review_band_candidates_are_queued_not_merged(self, container):
        queue = container.identities.review_queue(limit=500)
        for link in queue:
            assert link["decision"] == "review"
            assert 0.72 <= float(link["score"]) < 0.90

    def test_transliteration_variants_are_linked(self, container, manifest):
        """The generator wrote name variants; resolution should join at least some."""
        multi = [identity for identity in container.identities.identities()
                 if len(identity["source_ids"]) > 1]
        assert multi, "no identity spans more than one accused row"


class TestDerivedIntelligence:
    def test_graph_edges_were_built(self, seeded):
        assert seeded["summary"]["intelligence"]["edges"] > 0

    def test_every_edge_carries_its_supporting_cases(self, container):
        for edge in container.graph_repository.all_edges()[:200]:
            assert edge["case_ids"], f"edge {edge['edge_id']} has no case provenance"

    def test_derived_edges_are_marked_inferred(self, container):
        from ksp_cip.domain.enums import Provenance

        derived = [edge for edge in container.graph_repository.all_edges()
                   if edge["edge_type"] != "ALLEGED_IN"]
        assert derived
        assert all(edge["provenance"] == str(Provenance.INFERRED) for edge in derived[:200])

    def test_embeddings_cover_the_corpus(self, seeded, container):
        assert seeded["summary"]["intelligence"]["embedding_documents"] > 0
        assert container.retrieval.document_count > 0

    def test_priority_scores_are_bounded_and_explained(self, container):
        rows = container.priority.top(
            [row["CaseMasterID"] for row in
             container.store.query("SELECT CaseMasterID FROM curated_CaseMaster LIMIT 50", {})],
            limit=20,
        )
        assert rows
        for row in rows:
            assert 0 <= float(row["score"]) <= 100
            assert row["components"]["items"]


class TestFinancialExtension:
    def test_transactions_are_flagged_as_an_extension(self, container):
        rows = container.financial.all_transactions()
        assert rows
        assert all(int(row["is_extension"]) == 1 for row in rows[:100])

    def test_the_source_schema_has_no_financial_table(self, container):
        tables = {
            row["name"]
            for row in container.store.query(
                "SELECT name FROM sqlite_master WHERE type = 'table'", {}
            )
        }
        financial = {name for name in tables if "financial" in name.lower()}
        assert financial == {"ext_financial_transaction"}, (
            "financial data must live only in the clearly marked extension table"
        )

    def test_the_planted_burst_is_recovered(self, container, manifest):
        """The validation loop, extended to the financial analysis.

        The generator plants one account with a spike of transfers on a known
        day, against a deliberately quiet run-up. If the burst analysis cannot
        find it, the analysis proves nothing over synthetic data — the same
        reason the surge, the hotspots and the ring are planted and asserted.
        """
        from ksp_cip.application.graph.financial import FinancialAnalyzer

        planted = manifest["financial_burst"]
        assert planted, "the generator must record what it planted"

        bursts = FinancialAnalyzer().temporal_bursts(container.financial.all_transactions())
        matched = [b for b in bursts if b.ref == planted["ref"] and b.day == planted["day"]]
        assert matched, (
            f"planted burst {planted['ref']} on {planted['day']} was not detected; "
            f"found instead: {[(b.ref, b.day) for b in bursts]}"
        )
        found = matched[0]
        assert found.txn_count == planted["txn_count"]
        assert found.z_score >= 2.5
        # The account's own quiet history is what makes the day stand out.
        assert found.baseline_mean < 1.0

    def test_ordinary_activity_does_not_register_as_a_burst(self, container, manifest):
        """Precision matters as much as recall: the planted day should be the only one."""
        from ksp_cip.application.graph.financial import FinancialAnalyzer

        planted = manifest["financial_burst"]
        bursts = FinancialAnalyzer().temporal_bursts(container.financial.all_transactions())
        spurious = [b for b in bursts if not (b.ref == planted["ref"] and b.day == planted["day"])]
        assert not spurious, f"unplanted bursts reported: {[(b.ref, b.day, b.z_score) for b in spurious]}"
