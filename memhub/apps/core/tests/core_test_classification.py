"""Reviewed file defaults for the Core pytest architecture.

Explicit test/module markers always win. The inventory hash makes a new or
removed test file a review event instead of silently assigning it a convenient
default. Files not listed as unit or integration conservatively default to
contract and therefore remain in the PR gate.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

PRIMARY_MARKERS = ("unit", "contract", "integration", "performance", "live")
EXPECTED_TEST_FILE_COUNT = 384
EXPECTED_TEST_FILES_SHA256 = "33cef58fc5a4eb505da9d519149e0444bf581a336232d47a3d70f3aade0ef8ed"

# Pure transformations, validation models, parity checks and state machines.
# This list is intentionally small: uncertain files default to contract.
UNIT_FILES = frozenset(
    {
        "test_context_v2_contract_parity.py",
        "test_daemon_contract.py",
        "test_diagnostic_payload_keys.py",
        "test_extraction_state.py",
        "test_extraction_window.py",
        "test_graph_width_export.py",
        "test_graph_width_guard.py",
        "test_host_capabilities.py",
        "test_input_sanitization.py",
        "test_memory_server_config_state.py",
        "test_memory_server_contracts.py",
        "test_memory_server_job_state.py",
        "test_scan_roots.py",
        "test_scanner_classifier.py",
        "test_source_authorization_state.py",
        "test_scanner_sessionizer.py",
        "test_source_envelope_positions.py",
        "test_source_lifecycle.py",
        "test_subscription_visibility.py",
    }
)

# Strong multi-component evidence: app lifespan, service/runtime subprocess,
# durable drain, or an HTTP client driving a composed application. Explicit
# live/performance markers on a file take precedence over this default.
INTEGRATION_FILES = frozenset(
    {
        "test_auth.py",
        "test_backup_endpoint.py",
        "test_backup_restore_api.py",
        "test_c1_import_via_api_path.py",
        "test_call_event_api.py",
        "test_capture_deadline.py",
        "test_chat_exports.py",
        "test_cli_lifecycle.py",
        "test_connection_lifetime.py",
        "test_context_api.py",
        "test_context_search_both_paths.py",
        "test_conversation_search.py",
        "test_credential_responsiveness.py",
        "test_d93_same_process_load.py",
        "test_diagnostic_trace.py",
        "test_diagnostic_trace_api.py",
        "test_evidence_reconcile_primitive.py",
        "test_external_endpoint_scopes.py",
        "test_external_installation_permissions.py",
        "test_fault_matrix.py",
        "test_file_import_operations.py",
        "test_graph_width_endpoint.py",
        "test_home_summary.py",
        "test_hy_writeback.py",
        "test_import_api.py",
        "test_library_upgrade.py",
        "test_live_capture.py",
        "test_live_watch_grants.py",
        "test_log_privacy.py",
        "test_loopback_origin_write.py",
        "test_main_database_entry.py",
        "test_manual_transcript_import.py",
        "test_matter_collection.py",
        "test_matter_hy_integration.py",
        "test_matter_lifecycle_wiring.py",
        "test_matters.py",
        "test_measure_c1_loop_blocking_contract.py",
        "test_memory_content_import.py",
        "test_memory_correction_sync.py",
        "test_memory_freshness_integration_review.py",
        "test_memory_governance_api.py",
        "test_memory_graph_api.py",
        "test_memory_library_api.py",
        "test_memory_mutation_receipts.py",
        "test_memory_no_candidate.py",
        "test_memory_notes.py",
        "test_memory_qa_api.py",
        "test_memory_queue_recency.py",
        "test_memory_server_durable_queue.py",
        "test_memory_supersede.py",
        "test_memory_visibility.py",
        "test_memory_write_routes.py",
        "test_model_creation_api.py",
        "test_model_creation_validation_boundary.py",
        "test_model_probe_operations.py",
        "test_model_profile_api.py",
        "test_model_profile_context_reset.py",
        "test_onboarding_profile.py",
        "test_parent_watchdog.py",
        "test_policy_api_wiring.py",
        "test_raw_search_without_model.py",
        "test_reader_app.py",
        "test_recall_fusion_live.py",
        "test_recall_hy_primary.py",
        "test_recall_scope_control.py",
        "test_retrieval_lifespan.py",
        "test_runtime.py",
        "test_secret_store_error_code_api.py",
        "test_secret_store_error_codes.py",
        "test_secure_storage.py",
        "test_soak_probe_effectiveness.py",
        "test_source_candidate_api.py",
        "test_source_connection_intents.py",
        "test_source_import_receipts.py",
        "test_source_permission_races.py",
        "test_source_revocation_parking.py",
        "test_source_session_count_api.py",
        "test_source_status_latency.py",
        "test_source_sync_idempotent.py",
        "test_source_sync_permissions.py",
        "test_ui_session_api.py",
        "test_unified_write_entry_wiring.py",
        "test_visibility_background.py",
    }
)


def test_file_names(tests_root: Path) -> tuple[str, ...]:
    return tuple(sorted(path.name for path in tests_root.glob("test_*.py")))


def inventory_sha256(names: tuple[str, ...]) -> str:
    return sha256(("\n".join(names) + "\n").encode()).hexdigest()


def validate_inventory(tests_root: Path) -> tuple[str, ...]:
    names = test_file_names(tests_root)
    digest = inventory_sha256(names)
    if len(names) != EXPECTED_TEST_FILE_COUNT or digest != EXPECTED_TEST_FILES_SHA256:
        raise pytest.UsageError(
            "Core test file inventory changed; review and update core_test_classification.py: "
            f"count={len(names)} sha256={digest}"
        )
    unknown = (UNIT_FILES | INTEGRATION_FILES) - set(names)
    overlap = UNIT_FILES & INTEGRATION_FILES
    if unknown or overlap:
        raise pytest.UsageError(
            "Core test classification manifest is invalid: "
            f"unknown={sorted(unknown)} overlap={sorted(overlap)}"
        )
    return names


def default_primary(file_name: str) -> str:
    if file_name in UNIT_FILES:
        return "unit"
    if file_name in INTEGRATION_FILES:
        return "integration"
    return "contract"


def classify_items(config: pytest.Config, items: list[pytest.Item]) -> None:
    tests_root = Path(__file__).resolve().parent
    validate_inventory(tests_root)
    for item in items:
        explicit = [name for name in PRIMARY_MARKERS if item.get_closest_marker(name)]
        if len(explicit) > 1:
            raise pytest.UsageError(
                f"Core test has multiple primary markers: {item.nodeid}: {explicit}"
            )
        if not explicit:
            primary = default_primary(Path(str(item.fspath)).name)
            item.add_marker(getattr(pytest.mark, primary))
        else:
            primary = explicit[0]
        if primary in {"integration", "performance", "live"} and not item.get_closest_marker(
            "serial"
        ):
            item.add_marker(pytest.mark.serial)
