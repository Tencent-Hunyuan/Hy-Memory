# Core 测试文件清单

此文件由 `scripts/generate_core_test_index.py` 生成；每个 Core 测试文件必须有且只有一条记录。

| 测试文件 | 等级 | 一句话说明 |
| --- | --- | --- |
| `test_active_credentials.py` | `contract` | Active credentials are read once per call, never reused after a refusal. |
| `test_active_memory_window.py` | `contract` | 覆盖 active window is bounded independently of pending total、active window does not take the writer when nothing needs deferring、active window rejects non integer limits 等 9 个场景。 |
| `test_active_window_rotation.py` | `contract` | Rule 5: a blocked head must not stop the rest from ever being scheduled. |
| `test_anchor_invalid_rewind.py` | `contract` | Bug 2 real branch: an in-place same-length rewrite invalidates the anchor. |
| `test_architecture_layer_gate.py` | `contract` | A0-01: L0-L4 dependency-direction gate. |
| `test_archive_detection.py` | `contract` | S4.1: what an uploaded export is, decided before anything parses it. |
| `test_archive_extract.py` | `contract` | Unpacking an archive a stranger sent, onto this user's disk. |
| `test_ask_workspace_path.py` | `contract` | Ask must honour the same workspace_path expansion recall uses. |
| `test_async_bridge.py` | `contract` | 覆盖 reader rejects parallel workers that could close during next、cancelling a read stops the worker before the batch finishes、starting a read does not stop the app answering 等 10 个场景。 |
| `test_auth.py` | `integration` | 覆盖 local api requires bearer token、browser origin is checked in addition to token 场景。 |
| `test_background_work.py` | `contract` | 覆盖 a request does not wait behind a catch up、bulk work is kept to a few threads at a time、the pool outlives one hand off 等 4 个场景。 |
| `test_backup_destroy_restore_reconciliation.py` | `contract` | Backup -> destroy -> restore, reconciled row for row. |
| `test_backup_endpoint.py` | `integration` | The 'POST /api/v1/backup' endpoint the CLI's 'backup create' calls. |
| `test_backup_restore_api.py` | `integration` | 覆盖 restore endpoint refuses when runtime owns the core、restore endpoint restores a managed backup、restore endpoint rejects a source outside home 场景。 |
| `test_baseline_probe.py` | `contract` | A0-04: the baseline probe itself must be trustworthy. |
| `test_bug01_workbuddy_root.py` | `contract` | 3925a kernel item 2 (BUG-01, backend half): WorkBuddy import root. |
| `test_bug03_identity_key.py` | `contract` | BUG-03: the hook lane and the file scan must share one identity for one message. |
| `test_bug04_thinking_strip.py` | `contract` | BUG-04: Cursor thinking text must not be stored as the assistant's answer. |
| `test_c1_append_per_source.py` | `contract` | Append, per source, including the SQLite-backed ones. |
| `test_c1_attribution_per_source.py` | `contract` | Attribution per source, asserted on stored Evidence, not just on parsed events. |
| `test_c1_conversation_identity_scope.py` | `contract` | Same-named source sessions must stay separate through the read path. |
| `test_c1_cross_source_session_isolation.py` | `contract` | Same session name in two sources must stay two conversations. |
| `test_c1_damage_per_source.py` | `contract` | Damage and unknown schema, per source: a bad record must not sink the import. |
| `test_c1_deepseek_profiles.py` | `contract` | DeepSeek Harness has two profiles; only the headless session form is importable. |
| `test_c1_import_event_matrix.py` | `contract` | C1 owned matrix: existing history, new file, append, truncate/replace, restart, revoke. |
| `test_c1_import_failure_injection.py` | `contract` | C1 failure injection: atomic receipt, disk-full, read-only source, rotation. |
| `test_c1_import_instrumentation.py` | `contract` | Instrumented import accounting: bytes read, opens, hashes, jobs, duplicates. |
| `test_c1_import_peak_contention.py` | `contract` | Import-peak lock contention is truthful and replayable. |
| `test_c1_import_progress_heartbeat.py` | `contract` | Read progress heartbeats: visible at least once a second, never splitting a batch. |
| `test_c1_import_via_api_path.py` | `integration` | Accept the import through the product's HTTP path, not only through Python objects. |
| `test_c1_imported_index_guard.py` | `contract` | The ordering index must actually carry 'evidence_id', not just look like it does. |
| `test_c1_long_running_incremental.py` | `contract` | Long-running incremental import: many append rounds, no duplicates, no gaps. |
| `test_c1_manual_conversation_contract.py` | `contract` | Manual conversation import keeps original text, role, fences, and unknown time. |
| `test_c1_migration_interruption.py` | `contract` | A half-applied schema migration must resume, not corrupt or duplicate. |
| `test_c1_no_empty_jobs_no_second_reader.py` | `contract` | No empty jobs and no second reader: metadata-first and one writer. |
| `test_c1_openclaw_paused.py` | `contract` | OpenClaw is paused: registered for honesty, but never probed or started. |
| `test_c1_pause_revoke_matrix.py` | `contract` | Pause/resume and revocation, per source, through each source's real parser. |
| `test_c1_per_file_cursors.py` | `contract` | Each file in a source keeps its own cursor, not one cursor for the source. |
| `test_c1_permissions_per_source.py` | `contract` | Read-only and unreadable source files, per source. |
| `test_c1_preview_scope_diagnostic.py` | `contract` | Diagnostic: why does an all-imported preview see zero Evidence? |
| `test_c1_replace_per_source.py` | `contract` | Truncate, replace and rebuild, per source: no duplicates and no stale text. |
| `test_c1_rotation_per_source.py` | `contract` | File rotation, per source: the rotated-away name must not be read anymore. |
| `test_c1_sqlite_damage_per_source.py` | `contract` | SQLite-backed sources: damage and unknown schema must be reported, not swallowed. |
| `test_c1_upgrade_migration_matrix.py` | `contract` | Upgrade/migration matrix: an older database keeps importing incrementally. |
| `test_c4_raw_warmup.py` | `contract` | 覆盖 warm raw read path is side effect free、warm raw read path tolerates missing fts index、warm raw read path never raises or blocks startup 场景。 |
| `test_call_event_api.py` | `integration` | 覆盖 query records only final metadata and trusted host、delivery report is separate from adoption、delivery report rejects hosts and missing events 等 18 个场景。 |
| `test_call_event_recorder.py` | `contract` | 覆盖 denied memory lane is attributed to conversations、requested memory lane stays recall、zero result timeout is recorded separately from unavailable 等 6 个场景。 |
| `test_call_event_references.py` | `contract` | 覆盖 current titles exact contract order and no body in event、grants rechecked without cache and match source access、revision change never returns new or historical title 等 23 个场景。 |
| `test_call_event_store.py` | `contract` | Isolated metadata receipts; never use a user database or start recording. |
| `test_capture_deadline.py` | `integration` | A capture the hook hands off outlives that hook; it has to end itself. |
| `test_change_outbox.py` | `contract` | Evidence imports no longer feed the dormant change outbox. |
| `test_chat_exports.py` | `integration` | 覆盖 chatgpt preserves authorship unknown time and branch identity、claude and unsupported formats are not guessed、message order uses parent links not json object order 等 15 个场景。 |
| `test_claude_adoption_probe.py` | `contract` | The external-use verifier must distinguish a read from merely mentioning it. |
| `test_cli_commands.py` | `contract` | 覆盖 status reports the verified runtime as json、doctor fails when runtime identity cannot be verified、serve and legacy empty command share the same entry 等 4 个场景。 |
| `test_cli_lifecycle.py` | `integration` | 覆盖 cli starts and stops without residual runtime 场景。 |
| `test_cli_restore.py` | `contract` | 覆盖 restore requires a source、restore refuses while the runtime is running、restore previews without confirmation 等 4 个场景。 |
| `test_codex_header_provenance.py` | `contract` | 覆盖 header workspace versions and native ids reach file origin、new adapter rereads header after a real checkpoint、machine and unknown session sources do not establish human or agent sender 等 14 个场景。 |
| `test_connection_lifetime.py` | `integration` | Every SQLite connection a request opens must be closed when the request ends. |
| `test_context_access.py` | `contract` | 覆盖 memory only never reads raw authority、raw permission does not grant memory and removes revoked sources、raw requires all apps and source 等 13 个场景。 |
| `test_context_api.py` | `integration` | 覆盖 no model local query returns real raw and governed items、existing single lane authorization never runs denied reader、authentication and origin remain required 等 14 个场景。 |
| `test_context_endpoint_guard.py` | `contract` | 覆盖 composite route accepts existing read capabilities for later body validation、other capabilities do not enable composite read、composite read does not open other routes 等 5 个场景。 |
| `test_context_hy_primary.py` | `contract` | MemHub_context memory lane: HY decides the order, keywords only when HY does not answer. |
| `test_context_memory.py` | `contract` | 覆盖 permissions precede candidate limit and do not leak counts、scope user status and active source isolation、denied scope does not open database 等 60 个场景。 |
| `test_context_raw.py` | `contract` | 覆盖 empty authority and unrequested path do not open database、permissions precede candidate limit and no model needed、explicit source filter never broadens authority 等 37 个场景。 |
| `test_context_raw_scale_benchmark.py` | `contract` | 覆盖 object scale probe has exact positive control and plan、raw scale probe uses fts and has a nonzero positive control 场景。 |
| `test_context_search.py` | `contract` | 覆盖 authorization and requested paths never invoke other lane、empty partial zero unavailable and safe failures、parallel common deadline and finalizers wait for both reads 等 24 个场景。 |
| `test_context_search_both_paths.py` | `integration` | The objective names 'recall' and requires BOTH paths: raw and memory. |
| `test_context_v2_contract_parity.py` | `unit` | Mechanical parity checks for the shared v2 coverage receipt contract. |
| `test_contract_parity.py` | `contract` | Python 的枚举和 TypeScript 里那份手写的联合类型必须一致。 |
| `test_conversation_search.py` | `integration` | Searching imported conversations before any generation model is configured. |
| `test_conversation_seed_ids.py` | `contract` | 覆盖 seed ids read metadata only without hydrating、seed eligibility keeps person and memory content only、latest activity includes nonseed turns and import time fallback 等 13 个场景。 |
| `test_conversation_turn_grouping.py` | `contract` | A proposal and the turn that adopts it must reach HY as one turn. |
| `test_core_test_reporting.py` | `contract` | 覆盖 report writes sanitized machine readable views、file and marker aggregates are stable、classification suggestions never guess unit contract or integration 等 6 个场景。 |
| `test_credential_responsiveness.py` | `integration` | 覆盖 credentials do not block the event loop、disabled automatic processing does not digest onboarding、answering reads credentials off loop 等 5 个场景。 |
| `test_cursor_default_root_windows.py` | `contract` | R-29 剩余：Cursor 默认根路径不能写死成 macOS。 |
| `test_d93_same_process_load.py` | `integration` | D93 test 2: read latency while pure-Python work holds the GIL. |
| `test_d93_visibility_equivalence.py` | `contract` | D93 test 1: the written-ahead visibility must agree with the exact check. |
| `test_daemon_contract.py` | `unit` | A5-01/A5-03: process ownership, and the three grants kept apart. |
| `test_daemon_scheduler.py` | `contract` | A5-02/A5-04: the daemon's scan scheduler and composed health. |
| `test_data_safety.py` | `contract` | A5-05: backup usability/rotation, crash recovery, log retention. |
| `test_database_contention.py` | `contract` | Writers meet in this one file, and the loser used to lose data. |
| `test_database_file_permissions.py` | `contract` | 覆盖 a new main database is readable by its owner alone、the wal sidecars are not left world readable、a database from an older build is tightened on open 场景。 |
| `test_db_session_utc.py` | `contract` | The MySQL session must be pinned to UTC. |
| `test_delivery_board.py` | `contract` | 覆盖 parses only the marked structured record、verified requires evidence with a positive control、rejects unknown dependency 等 5 个场景。 |
| `test_derived_echo.py` | `contract` | A host that saves our injection back must not mint a new user decision. |
| `test_detailed_logging.py` | `contract` | 覆盖 detailed pipeline logging defaults off and survives restart、live log level applies to full hy and provider call chain、info collects hy info without enabling debug pipeline content 等 7 个场景。 |
| `test_diagnostic_log.py` | `contract` | 覆盖 high frequency success events are summarized but warnings are not、structured log is private correlated and owner only、arbitrary detail and absolute stack are rejected 等 14 个场景。 |
| `test_diagnostic_log_durability.py` | `contract` | 写线程报告的「已落盘」必须是真的。 |
| `test_diagnostic_log_off_loop.py` | `contract` | 诊断日志不得在调用线程上做磁盘 I/O。 |
| `test_diagnostic_payload_keys.py` | `unit` | An instrument must never be able to break the thing it measures. |
| `test_diagnostic_trace.py` | `integration` | 覆盖 mutating request gets trace and never logs path or body、recall request log includes safe host source id、import request trace reaches the background job without original text 等 6 个场景。 |
| `test_diagnostic_trace_api.py` | `integration` | 覆盖 trace lookup is disabled until detailed logging is enabled、trace lookup returns only safe structured steps、log level route applies current process level 等 4 个场景。 |
| `test_disable_thinking.py` | `contract` | 运行期生成请求须按档案可选关闭思考；默认不向厂商下发未知字段。 |
| `test_dual_engine_helper.py` | `contract` | Tests for the dual-engine harness itself. |
| `test_event_loop_blocking.py` | `contract` | Proof, as a test, that blocking work on the loop freezes it — and the fix works. |
| `test_evidence_reconcile_primitive.py` | `integration` | A bounded, read-only discovery primitive for the H23 Evidence reconcile. |
| `test_evidence_store.py` | `contract` | 覆盖 same source message merges origins without duplication、identical text from different sources is not deduplicated、rescanning an unchanged file does not write the message again 等 26 个场景。 |
| `test_export_probe.py` | `contract` | What a probe may say about a stranger's chat history, and what it may not. |
| `test_external_endpoint_scopes.py` | `integration` | Core boundary tests using the broker's envelope, never a host's claimed identity. |
| `test_external_installation_permissions.py` | `integration` | 覆盖 external marker is not an installation grant、external diagnostic trace uses its own read only tool、raw source grants filter before limit and private body loading 场景。 |
| `test_extraction_defer.py` | `contract` | Deferring out-of-window work, and letting it back in a batch at a time. |
| `test_extraction_settings_store.py` | `contract` | The migrated store must behave identically on SQLite and on MySQL. |
| `test_extraction_state.py` | `unit` | Three extraction states: extracted, outside the window, or still queued. |
| `test_extraction_window.py` | `unit` | Extraction window: outside it, evidence stays searchable and out of the queue. |
| `test_fault_matrix.py` | `integration` | A7-03: the fault matrix is complete, and the dangerous outcomes are pinned. |
| `test_file_cursor_import.py` | `contract` | History import is live sync from an empty cursor: stat, skip unchanged, read forward. |
| `test_file_import_operations.py` | `integration` | Bounded synthetic uploads; operation identity is not a content dedup key. |
| `test_file_import_operations_review.py` | `contract` | Upload refusal/legacy compatibility review with bounded synthetic files only. |
| `test_file_publish_job_boundary.py` | `contract` | Staged uploads cannot widen an earlier directory import's uncaptured input. |
| `test_four_app_live_ingest.py` | `contract` | Actual source readers + OS watch + Core routes; no model or native host calls. |
| `test_frozen_core_layout.py` | `contract` | 覆盖 single file layout remains available without private preset、folder layout keeps same core and all native dependencies、unknown layout does not silently build another package 等 5 个场景。 |
| `test_golden_corpus.py` | `contract` | A7-01: the golden corpus comparison. |
| `test_governance_permission_commit.py` | `contract` | 覆盖 permission guard is rechecked inside write transaction and rolls back、guard context does not leak into subsequent manual actions、guard check and commit hold database write authority 场景。 |
| `test_governance_read_path_does_not_take_the_writer.py` | `contract` | The recall read path must not acquire the single global writer. |
| `test_governance_store_root.py` | `contract` | 生产代码里治理 store 只在 Core 启动的组合根建一次。 |
| `test_graph_width_endpoint.py` | `integration` | The width mismatch has to reach the interface, not just the log. |
| `test_graph_width_export.py` | `unit` | EXPORT_FIRST has to actually export. |
| `test_graph_width_guard.py` | `unit` | Graph width mismatch must ask, never silently archive. |
| `test_harness_contract_suite.py` | `contract` | A4-09: one contract, ten harnesses. |
| `test_health.py` | `contract` | 覆盖 a backfill that fails says so rather than disappearing、a failed catch up leaves a traceback behind、configured model without content consent does not start backfill 等 15 个场景。 |
| `test_history_listing_rotation.py` | `contract` | 覆盖 history listing alternates started and untouched files、history listing drains whichever cohort is longer 场景。 |
| `test_home_summary.py` | `integration` | 覆盖 summary counts beyond page limit and excludes ineligible rows、summary revocation excludes whole mixed memory not only citation、latest is bounded and other users revocations do not hide local data 等 6 个场景。 |
| `test_home_summary_permissions.py` | `contract` | 覆盖 bad active link excludes whole latest memory、bad inactive link does not hide memory or enter source set、other user memory and grant are isolated 等 10 个场景。 |
| `test_host_capabilities.py` | `unit` | A plugin does not become realtime on every host or every unknown version. |
| `test_hourly_c1_peak_replay_contract.py` | `contract` | Guard the package peak-replay probe's error accounting. |
| `test_hourly_c1_recall_stage_probe_contract.py` | `contract` | Guard the packaged recall stage probe's command and error semantics. |
| `test_hy_baseline_source.py` | `contract` | 覆盖 first import replay copies raw prefix without selecting message ids、frozen transcript keeps middle user message and excludes later records 场景。 |
| `test_hy_coding_reply.py` | `contract` | R-07: HY's coding-shaped replies read as "no memories at all". |
| `test_hy_log_privacy.py` | `contract` | HY SDK records are not rewritten by One Memory's logging setup. |
| `test_hy_memory_bridge.py` | `contract` | 覆盖 hy client initialization is serial across bridges、native hy clients share one runtime per data directory、ollama profile ignores ambient remote storage and models 等 41 个场景。 |
| `test_hy_metrics_lifecycle.py` | `contract` | What the pinned public SDK's MetricsCollector does, and what that costs us. |
| `test_hy_network_egress.py` | `contract` | Where a real HY client is allowed to send bytes, and where it is not. |
| `test_hy_response_guard.py` | `contract` | 覆盖 invalid response retries same hy provider without changing prompt、repeated empty response fails instead of becoming no op、failure receipt survives a later successful model call 等 23 个场景。 |
| `test_hy_response_shapes.py` | `contract` | HY response shape selection only; no provider, write routing or model calls. |
| `test_hy_runtime_config.py` | `contract` | MEMORY.md is the audited record of what we send HY; keep it answerable. |
| `test_hy_sdk_idempotency_probe.py` | `contract` | A0-03: probe whether the HY SDK can make a write idempotent. |
| `test_hy_source_context.py` | `contract` | 覆盖 bound context rejects a same named conversation in another workspace、bound context rejects a duplicated evidence id、bound context rejects a same conversation from another source 等 9 个场景。 |
| `test_hy_stage_trace.py` | `contract` | 覆盖 observer returns native results without editing or requesting models、observer redacts content without recording prompts or exception details 场景。 |
| `test_hy_step_trace_switch.py` | `contract` | Rule 15: the per-step HY trace is off unless the debug tier asks for it. |
| `test_hy_trial_budget.py` | `contract` | Offline proofs for the real-model acceptance budget (no network, no Keychain). |
| `test_hy_writeback.py` | `integration` | HY 的结果只有一扇门进治理表，而且每次进门都带着调用开始时的证据快照。 |
| `test_import_api.py` | `integration` | 覆盖 resume during cleanup returns conflict without internal error、authorize detect import and revoke api flow、protected api rejects missing token and foreign origin 等 5 个场景。 |
| `test_import_control_races_review.py` | `contract` | Read-only review repro: real runner/store, synthetic delayed adapter cleanup. |
| `test_import_evidence_atomicity.py` | `contract` | 覆盖 counter failure rolls back evidence versions and cursor、same identity cursor rewind is blocked and logged、replaced identity may reset checkpoint 等 20 个场景。 |
| `test_import_oversized_verbatim.py` | `contract` | 覆盖 normal verbatim is unchanged、oversized verbatim is truncated instead of rejected 场景。 |
| `test_import_runner.py` | `contract` | 覆盖 job transitions are explicit、runner keeps per record errors and continues、sync slices reuse the same source listing 等 22 个场景。 |
| `test_import_skip_batching.py` | `contract` | 覆盖 batch limit and tail use fewer commits、batched skips keep their order against the next write、flushes before every nonmatching event 等 14 个场景。 |
| `test_import_snapshots.py` | `contract` | Atomic snapshot publication plus frozen manifest, path, mode, and per-file checksum guards. |
| `test_import_supervisor.py` | `contract` | 覆盖 supervisor starts all granted sources together、supervisor runs enqueued job in background、harness freeze keeps pending job and thaw runs the same row 等 11 个场景。 |
| `test_import_three_scenarios.py` | `contract` | The three import scenarios a user actually meets. |
| `test_import_three_scenarios_real.py` | `contract` | The three import scenarios, on a real Core at a real volume. |
| `test_imported_index_background.py` | `contract` | The ordering index is built off the startup path. |
| `test_ingest_journal.py` | `contract` | 覆盖 baseline and empty initialize are explicit transactional and idempotent、old date new import and revise noop and rollbacks、import metadata changes invalidate without authorizing content 等 12 个场景。 |
| `test_input_sanitization.py` | `unit` | 覆盖 redacts cli and assignment secrets without removing context、redacts bearer known tokens urls and private keys、redacts quoted json assignments and basic authorization 等 8 个场景。 |
| `test_installer.py` | `contract` | 覆盖 installer create verify and rollback is transactional、installer updates owned file and restores backup、installer refuses unowned or changed target 场景。 |
| `test_installer_parent_symlink.py` | `contract` | The owned-file installer must not follow a parent directory swapped for a symlink. |
| `test_intake_dedupe.py` | `contract` | A4-07: hook and scan share one identity, so order cannot duplicate. |
| `test_internal_preset.py` | `contract` | The optional model preset a configured build carries. |
| `test_jsonl_snapshot_tail.py` | `contract` | Bounded EOF handling on synthetic files only, never original host history. |
| `test_library_upgrade.py` | `integration` | 升级前的备份是 Core 启动的第一步：只读地看、照原样备份，之后才有任何东西改库。 |
| `test_live_capture.py` | `integration` | Visible hook text lands in Evidence before the transcript file is flushed. |
| `test_live_sync.py` | `contract` | 覆盖 live sync preempts a reader batch before it emits work、live sync cuts in front of a running history import、live sync does not resume a history import the user paused 等 24 个场景。 |
| `test_live_sync_file_incremental.py` | `contract` | 覆盖 unchanged file metadata avoids a job and content read、changed file content is read once by the import job、workbuddy first live poll baselines history then imports only append 等 6 个场景。 |
| `test_live_sync_job_coalescing.py` | `contract` | A poll that finds nothing must not leave an import job behind. |
| `test_live_watch.py` | `contract` | 覆盖 a new turn lands well inside the ten second target、start prime catches a change that predates the watcher、a run that predates the change is followed by a fresh one 等 15 个场景。 |
| `test_live_watch_grants.py` | `integration` | 覆盖 explicit sync grant starts watch without restart and revocation stops it 场景。 |
| `test_local_embedding.py` | `contract` | Guards for the built-in embedding model. |
| `test_local_embedding_offsets.py` | `contract` | 覆盖 real locked tokenizer offsets are unicode codepoints、known emoji and combining offsets are not utf8 or utf16 units、offsets are not truncated or padded and do not create onnx session 等 9 个场景。 |
| `test_local_onnx_provider.py` | `contract` | Expect local ONNX embedding to be the default and to feed Hy Memory. |
| `test_log_file.py` | `contract` | 覆盖 what the watchdog says survives a dock launch、the log lives where the data does、a machine left running for months does not fill up 等 4 个场景。 |
| `test_log_privacy.py` | `integration` | The file log is shipped to us in bug reports; it must not carry originals. |
| `test_loop_watchdog.py` | `contract` | 覆盖 a blocked loop is reported while it is still blocked、a loop that keeps running is left alone、one stall is reported once rather than every check 等 4 个场景。 |
| `test_loopback_origin_write.py` | `integration` | 覆盖 the web app can write from the loopback it is served on、dev server and desktop shell still pass、a page from anywhere else is still refused 场景。 |
| `test_loopback_proxy_bypass.py` | `contract` | 环回地址不得走 HTTP(S)_PROXY。 |
| `test_main_database_access.py` | `contract` | One process, one write connection, a few readers: the only way into the main database. |
| `test_main_database_entry.py` | `integration` | Nothing in the Core opens the main database except through its access layer. |
| `test_main_database_fairness.py` | `contract` | Waiting for the one database has to be a queue, not a scramble. |
| `test_main_database_isolation.py` | `contract` | What one checkout of the main database can do, and what it leaves for the next. |
| `test_managed_restore.py` | `contract` | 覆盖 restore rejects a source outside the managed backup directory、restore rejects a symlinked source、restore rejects a non one memory database 等 9 个场景。 |
| `test_manual_transcript_import.py` | `integration` | 覆盖 manual json keeps authors unknown time and exact text、marked text keeps code fences and never invents timestamps、ambiguous partial or invalid manual data never leaves partial import 等 4 个场景。 |
| `test_matter_collection.py` | `integration` | 覆盖 hy windows cover every turn including leading and boundary assistants、oversize turn is split without silently dropping content、partial timestamps preserve source sequence 等 8 个场景。 |
| `test_matter_discovery.py` | `contract` | 覆盖 synthesis materializes only known verbatim citations、duplicate claims keep all governed refs and distinct states、synthesis cannot echo the original sixty claim list 等 27 个场景。 |
| `test_matter_hy_integration.py` | `integration` | 覆盖 hy inputs are frozen and outputs keep task and governance links、history supported repeat does not reassign its source to new assistant turn、bound native update preserves memory identity revision and source union 等 19 个场景。 |
| `test_matter_lifecycle_missing_source.py` | `contract` | A HY-backed Matter must fail closed when no lifecycle source is wired. |
| `test_matter_lifecycle_wiring.py` | `integration` | The composition root must give MatterStore a live HY lifecycle source. |
| `test_matter_model_transport.py` | `contract` | 覆盖 transient failure retries identical request once、repeated timeout is bounded and not cached、revocation stops retry before another request 等 8 个场景。 |
| `test_matter_shadow.py` | `contract` | 覆盖 snapshot filters revoked and other users without changing source、snapshot is read only even when a write is attempted、checked claims require exact supplied quote and stay unverified 等 9 个场景。 |
| `test_matter_timeline.py` | `contract` | 覆盖 timeline splits changed phase from unchanged resources with lineage、supported remaining text cannot hide lost meaning、preservation cannot accept duplicate or unrequested group ids 等 7 个场景。 |
| `test_matter_trial_render.py` | `contract` | 覆盖 rejects a result without readonly boundary、checks group members and escapes script endings 场景。 |
| `test_matters.py` | `integration` | 覆盖 unknown message times are not replaced by import times、selection preserves full long messages and numerical order、selection excludes revoked sources and other users 等 26 个场景。 |
| `test_measure_c1_import_scale.py` | `contract` | The import measurement must not report fabricated or conflated counters. |
| `test_measure_c1_loop_blocking_contract.py` | `integration` | Contract guards for the C1 event-loop blocking measurement. |
| `test_memhub_display_identity.py` | `contract` | Display name MemHub must not fork the technical identity. |
| `test_memory_batch_extraction.py` | `contract` | 覆盖 batch extractor keeps only unique known evidence ids、batch extractor maps provider failure to machine error 场景。 |
| `test_memory_citation_query_plan.py` | `contract` | Correlated citation checks must look links up by memory, not scan by 'active'. |
| `test_memory_content_import.py` | `integration` | 覆盖 existing memory preview keeps unknown author and exact original、invalid existing memory records never leave a partial upload、memory content requires explicit mode and rejects vendor guessing 等 5 个场景。 |
| `test_memory_correction_sync.py` | `integration` | R-27: a correction that only lands locally, while HY keeps the old text. |
| `test_memory_deletion_outbox.py` | `contract` | 删除发件箱：一条删除同一时刻只归一个执行者，结果只由持有领取凭证的那个写。 |
| `test_memory_dispatch_lifecycle.py` | `contract` | Exercise the automatic enqueue boundary without producing substitute memories. |
| `test_memory_evidence_freshness.py` | `contract` | 覆盖 fresh then revised and identity only change、any complete fresh proof wins over old proof、other revision and incomplete proof do not hide legacy or correction 等 21 个场景。 |
| `test_memory_freshness_integration_review.py` | `integration` | Independent synthetic read-path review; no lifespan, model or user database. |
| `test_memory_governance.py` | `contract` | 覆盖 correction keeps verbatim and appends history、mutations are revision checked and request idempotent、delete result survives restart and request replay 等 42 个场景。 |
| `test_memory_governance_api.py` | `integration` | 覆盖 memory governance api preserves evidence and reports cleanup、memory validation does not echo content 场景。 |
| `test_memory_graph_api.py` | `integration` | 覆盖 graph labels where every edge came from、a created memory is on the map with its source links、drawing the map does not stop the app answering 等 12 个场景。 |
| `test_memory_language.py` | `contract` | R-03: memory language follows the message, so Kristin's gets written in English. |
| `test_memory_library.py` | `contract` | 覆盖 full library is reachable in stable pages、permission filter precedes count search source and page、query filters before expensive freshness 等 26 个场景。 |
| `test_memory_library_api.py` | `integration` | 覆盖 auth and external policy reject before reading、invalid parameters are 422 before projection、empty unknown source and query boundary are success not failure 等 12 个场景。 |
| `test_memory_mutation_receipts.py` | `integration` | Read-only mutation receipts from real governance history, without model calls. |
| `test_memory_no_candidate.py` | `integration` | 新记忆一经创建即有效；「待确认」「已拒绝」只作为旧库迁移的输入存在。 |
| `test_memory_notes.py` | `integration` | 覆盖 empty library can save a note without a matter or configured model、note requires authentication explicit scope and nonempty content、saved note evidence returns raw and consent reference from same record 等 6 个场景。 |
| `test_memory_processing.py` | `contract` | 覆盖 stale known reason diagnostics never fail dispatch、memory task logs six step durations without content、assistant turns reach hy so references can be resolved 等 95 个场景。 |
| `test_memory_processing_diagnostics.py` | `contract` | 覆盖 conversation prepare failure includes safe message and stack、deletion sync failure includes safe message and stack 场景。 |
| `test_memory_qa.py` | `contract` | 覆盖 search terms keep meaningful single chinese character、streamed answer emits recall then incremental grounded text、governance change during generation stops the old answer 等 14 个场景。 |
| `test_memory_qa_api.py` | `integration` | 覆盖 ask stream endpoint emits recall deltas and final、ask stream endpoint requires the local token 场景。 |
| `test_memory_qa_permissions.py` | `contract` | 覆盖 initial refusal precedes all secret access and network、invalid authorization result fails before secret access、authorization receives exact question and only outbound grounding 等 16 个场景。 |
| `test_memory_qa_scope_candidate_budget.py` | `contract` | The light Q&A path spends its candidate page the same way recall did. |
| `test_memory_queue_recency.py` | `integration` | R-02: the extraction queue has no time dimension. |
| `test_memory_server_config_state.py` | `unit` | 覆盖 sync config defaults off and survives restart、sync config updates one source without losing another、model content permission is one global final state 等 4 个场景。 |
| `test_memory_server_contracts.py` | `unit` | A0-02: freeze the cross-layer write contract (red state first). |
| `test_memory_server_db.py` | `contract` | A1-02: pool configuration, transaction boundary and loop binding. |
| `test_memory_server_durable_queue.py` | `integration` | The unified write entry must be durable, not just an in-memory receipt. |
| `test_memory_server_intake.py` | `contract` | A3-03: the convergence seam every write entry shares. |
| `test_memory_server_job_state.py` | `unit` | A2-05: the job state machine, especially the parts that protect 'unknown'. |
| `test_memory_server_migrations.py` | `contract` | A1-03: migrations apply in order, are recorded, and are retried on failure. |
| `test_memory_server_queue.py` | `contract` | A2-03: the claim contract, verified without a live MySQL. |
| `test_memory_server_scheduler.py` | `contract` | A2-04: scheduling policy, proven without a database. |
| `test_memory_server_submit.py` | `contract` | A3-01/A3-04: one front door, and what "the same write twice" means. |
| `test_memory_server_unified_entry.py` | `contract` | The adapter that makes Evidence-first intake mechanical for every entry. |
| `test_memory_supersede.py` | `integration` | HY 取代一条记忆时，产品里的旧记忆变成「已被取代」并指向新记忆。 |
| `test_memory_task_unknown_retry.py` | `contract` | A task refused before it was sent must come back, not stay unknown forever. |
| `test_memory_tasks.py` | `contract` | 覆盖 task freezes active profile revision and is idempotent、pending by recency compares instants and counts missing created at as backlog、pending by recency excludes failed tasks at the retry ceiling 等 58 个场景。 |
| `test_memory_visibility.py` | `integration` | The stored visibility conclusion must agree with the exact check. |
| `test_memory_visibility_equivalence.py` | `contract` | D93 A, gate 8.1: the stored conclusion must track the exact one, step by step. |
| `test_memory_write_registry.py` | `contract` | A3-01: acceptance and outcome recording for the unified write path. |
| `test_memory_write_routes.py` | `integration` | A3-01: the HTTP write entry, tested against a real FastAPI app. |
| `test_migrate_off_sqlite.py` | `contract` | A1-06: the migration is only done when reconciliation agrees. |
| `test_model_creation_api.py` | `integration` | Normal protected model creation routes with temporary data and in-memory secrets. |
| `test_model_creation_operations.py` | `contract` | 覆盖 lookup without table is read only and legacy create does not initialize、operation commits with profile and version without autoactivation、any replay conflicts before secret put 等 21 个场景。 |
| `test_model_creation_validation_boundary.py` | `integration` | Real ASGI validation must precede creation reservation and secret writes. |
| `test_model_probe.py` | `contract` | 覆盖 probe still measures embedding when structured output fails、probe records the real embedding width on the profile、openai compatible probe checks model and structured output 等 23 个场景。 |
| `test_model_probe_input_revision.py` | `contract` | 覆盖 input revision precedes measured width update、concurrent update still rejects width or capability cas、early returns record the actual starting revision 等 6 个场景。 |
| `test_model_probe_operations.py` | `integration` | Persisted probe identity, real SQLite/API and synthetic transport only. |
| `test_model_profile_api.py` | `integration` | 覆盖 model profile api never returns key and enforces revision、model profile probe route returns only machine results、model profile validation never echoes api key 等 4 个场景。 |
| `test_model_profile_context_reset.py` | `integration` | 覆盖 create context default and inclusive bounds、explicit null clears override and preserves history and secret、omitted or same value is noop with unchanged revision and metadata 等 11 个场景。 |
| `test_model_profile_identity.py` | `contract` | Stable model editing and configured-vs-available credentials; no Keychain/network. |
| `test_model_profile_queue_handover.py` | `contract` | Queued work when the model changes or its configuration is deleted (D75). |
| `test_model_profiles.py` | `contract` | 覆盖 profile uses one model and keeps key out of database、api key updates reuse one stable secret reference、startup cleanup removes only secrets not referenced by current profiles 等 15 个场景。 |
| `test_model_request_audit.py` | `contract` | 覆盖 failure is recorded without error message、missing usage stays unknown in detail and zero in summary、records one physical request with provider usage and no content 等 5 个场景。 |
| `test_model_request_budget.py` | `contract` | 覆盖 persists failed request reservations without content、parallel leases cannot exceed request limit、unknown or excessive output and body are denied 等 6 个场景。 |
| `test_mysql_lifecycle.py` | `contract` | A1-07: the decisions Runtime makes around the bundled MySQL. |
| `test_mysql_platform.py` | `contract` | A1-07: the machine-facing half of managing the bundled MySQL. |
| `test_obs03_health_probe.py` | `contract` | OBS-03: '/health' answered with the web app instead of a health payload. |
| `test_observation_gate.py` | `contract` | A7-02: the gate that stops "nothing changed" from being certified blind. |
| `test_onboarding_profile.py` | `integration` | R-09：首次安装引导把画像录下来，不问用户一句话。 |
| `test_oversized_file_guard.py` | `contract` | One pass over a source file is bounded by a byte budget, not skipped. |
| `test_parent_watchdog.py` | `integration` | A Core that outlives its launcher makes the next launch impossible. |
| `test_policy_api_wiring.py` | `integration` | 覆盖 global model permission off does not backfill、backfill puts history on write jobs without scheduling memory tasks、source pause skips history until the source is reenabled 场景。 |
| `test_policy_production_entry.py` | `contract` | 覆盖 production serve passes only supported app options、maintenance serve does not own the runtime 场景。 |
| `test_precipitation_concurrency_setting.py` | `contract` | Rule 2/9: precipitation concurrency is configurable, not hardcoded to 1. |
| `test_primary_source_adapters.py` | `contract` | 覆盖 pipeline json is host generated even when truncated、workbuddy record parsing never blocks the event loop、cursor store user info envelope is not attributed to the person 等 34 个场景。 |
| `test_probe_code_contract_parity.py` | `contract` | 探测码在三处必须同时存在：python 枚举、TS 契约、界面文案表。 |
| `test_probe_payment_required.py` | `contract` | 覆盖 http 402 without a useful body is still understood as unpaid、a wrong address is still a missing model not an unpaid one 场景。 |
| `test_processing_requests.py` | `contract` | 覆盖 concurrent claim only one snapshot wins、concurrent create same grant has one id and profile、concurrent different profile never reuses another scope 等 14 个场景。 |
| `test_quality_acceptance.py` | `contract` | 覆盖 acceptance pack is deterministic and scores personal boundary、acceptance score fails missing personal memory 场景。 |
| `test_raw_search_without_model.py` | `integration` | Search imported conversations with no generative model configured at all. |
| `test_raw_vector_scale_benchmark.py` | `contract` | 覆盖 vector scale probe uses real 512 dimension filtered search 场景。 |
| `test_read_path_freshness_batch.py` | `contract` | The row-by-row read check must not scale its query count with the page. |
| `test_reader_app.py` | `integration` | The Reader process stays read-only and never blocks a read on its side effects. |
| `test_reader_conversation_hit.py` | `contract` | A search hit carries who wrote the row it matched. |
| `test_real_load_metrics.py` | `contract` | Run the real access layer under a production-like combined load. |
| `test_recall.py` | `contract` | 覆盖 recall is confirmed only grounded and scope bound、wildcard scope means every scope just like it does in qa、recall returns empty instead of unrelated fallback 等 10 个场景。 |
| `test_recall_during_import_probe_contract.py` | `contract` | Guard the source-level recall-during-import probe's sampling semantics. |
| `test_recall_excluded_sources.py` | `contract` | D94: an empty result names the sources this host may not read. |
| `test_recall_fusion_live.py` | `integration` | RCL-04 against a real HY instance instead of a stand-in. |
| `test_recall_hy_primary.py` | `integration` | Host recall: HY search decides the order, the ledger only filters. |
| `test_recall_library_scale.py` | `contract` | 覆盖 recall reaches a memory that is not among the newest 500、semantic recall reaches a memory outside the newest 500、recall still works just below the boundary 等 5 个场景。 |
| `test_recall_log_probe_exclusion.py` | `contract` | A-21：R-10 一旦引入召回日志，「探针不污染」必须自动变红而不是静默失效。 |
| `test_recall_precipitation_harness.py` | `contract` | The precipitation measurement harness must stay loadable and single-sourced. |
| `test_recall_probe.py` | `contract` | The self-check query, and what it must not touch on its way through. |
| `test_recall_revoked_candidate_crowding.py` | `contract` | Revoked-source rows must not crowd a readable recall hit out of the page. |
| `test_recall_scope_candidate_budget.py` | `contract` | 覆盖 other scopes cannot consume the recall candidate budget、internal wildcard still reports real candidate truncation、workspace resolution does not remove shared results 等 9 个场景。 |
| `test_recall_scope_control.py` | `integration` | 覆盖 external scope cannot override shared or host workspace boundary、scope only mutation is versioned without changing status or reindexing 场景。 |
| `test_recall_scope_off_loop.py` | `contract` | The workspace lookup behind an external recall must not run on the loop. |
| `test_recent_first_import.py` | `contract` | Recent-file priority survives restart through a selection-bound frozen copy. |
| `test_retrieval_chunking.py` | `contract` | 覆盖 default 384 body 64 overlap and exact source slices、all prefix and special tokens count toward strict 512 budget、real tokenizer covers long chinese middle and tail object locations 等 20 个场景。 |
| `test_retrieval_eligibility_benchmark.py` | `contract` | 覆盖 scale probe proves composite authority index changes the plan 场景。 |
| `test_retrieval_index_store.py` | `contract` | 覆盖 constructor and reads do not create database or schema、explicit initialization rolls back and never grants model policy、consumer and jobs commit together 等 19 个场景。 |
| `test_retrieval_index_supervisor.py` | `contract` | 覆盖 stop waits for native exit and does not close resources、async cancellation repeatedly waits for native thread、idle no trigger no spin and duplicate start rejected 等 14 个场景。 |
| `test_retrieval_index_worker.py` | `contract` | 覆盖 tick indexes complete original and reopen can search、change during native encoding never publishes、cancel after encode does not publish and is reclaimable 等 10 个场景。 |
| `test_retrieval_lifespan.py` | `integration` | 覆盖 retrieval defaults off and does not create index、enabling retrieval requires runtime ownership、index enabled serve imports without building an unmanaged app 等 6 个场景。 |
| `test_retrieval_mapped_content.py` | `contract` | 覆盖 raw authors keep exact body and do not mutate evidence、raw and author qualification rejects without tokenizing、ambiguous generated and body hygiene are not admitted 等 22 个场景。 |
| `test_retrieval_mapped_integration.py` | `contract` | 覆盖 empty materialization rechecks excluded map、completed excluded missing active binding is not empty success、old empty completion is invalidated when a new indexed record arrives 等 24 个场景。 |
| `test_retrieval_provisioning.py` | `contract` | The index has to come up through the product, not through a test helper. |
| `test_retrieval_query_plan.py` | `contract` | Local QueryPlan: extract URL/path objects. |
| `test_retrieval_raw_candidates.py` | `contract` | 覆盖 positive only current authorized published chunks are searched、empty authority never reads database or vector、unapproved source never enters vector query 等 15 个场景。 |
| `test_retrieval_raw_evidence.py` | `contract` | 覆盖 materialized text is exact current original、stale candidates do not authorize original read、budget does not cut a chunk into fake partial object 等 6 个场景。 |
| `test_retrieval_raw_lane.py` | `contract` | v1 raw lane must not claim complete empty when the derived index is partial. |
| `test_retrieval_runtime.py` | `contract` | 覆盖 constructor and missing start create nothing、stop releases supervisor worker but preserves immutable snapshot、not initialized or invalid configuration never creates index 等 21 个场景。 |
| `test_retrieval_vector_store.py` | `contract` | 覆盖 constructor does no io and unopened calls do not create、open existing missing store or collection never creates、real chroma collection has no embedding function or documents 等 32 个场景。 |
| `test_revocation_recall_boundary.py` | `contract` | Revocation is the node: nothing from that source is read afterwards (D73). |
| `test_runtime.py` | `integration` | 覆盖 runtime writes and removes ownership receipt、runtime rejects second owner for same data directory、nonpublishing maintenance owner still excludes a second core 等 4 个场景。 |
| `test_runtime_client.py` | `contract` | 覆盖 runtime inspection authenticates the discovered instance、runtime inspection rejects untrusted receipts without probing、runtime inspection distinguishes stopped and identity mismatch 场景。 |
| `test_sanitization_source_map.py` | `contract` | 覆盖 mapping matches existing sanitizer and exact original slices、synthetic replacements never claim original locations、repeated identical suffix is mapped by positions not string search 等 6 个场景。 |
| `test_scan_baseline.py` | `contract` | A4-11: the baseline gate, and the ways baselines go wrong. |
| `test_scan_root_store.py` | `contract` | A4-01: scan-root configuration persists, and refuses what it must. |
| `test_scan_roots.py` | `unit` | A4-01: scan root configuration. |
| `test_scanner_batcher.py` | `contract` | A4-06: the five-round rule, and the degenerate cases around it. |
| `test_scanner_classifier.py` | `unit` | A4-04/A4-10: the metadata gate's ten gates, as far as pure logic can prove. |
| `test_scanner_discovery.py` | `contract` | A4-08: discovery produces metadata only, bounded and fair. |
| `test_scanner_incremental.py` | `contract` | A4-04: reading only the tail, and refusing to resume when it is unsafe. |
| `test_scanner_lease_gate.py` | `contract` | A4 gate 7: two scanners, one root — only the lease holder commits. |
| `test_scanner_service.py` | `contract` | A4-04/A4-11: the composed scanner, judged by the numbers D54 demands. |
| `test_scanner_sessionizer.py` | `unit` | A4-05: session and round derivation. |
| `test_scanner_store_contract.py` | `contract` | A4-10: the CursorStore contract, stated once and obeyed by every impl. |
| `test_schema_built_at_import.py` | `contract` | 模型的 schema 必须在 import 时建好，不能拖到运行时在事件循环上建。 |
| `test_schema_gate.py` | `contract` | 覆盖 fresh database records the build schema、future schema refuses to open、legacy database without a ledger is left untouched 场景。 |
| `test_schema_migrations.py` | `contract` | 覆盖 evidence store adds source ordinal to previous schema、import store migrates previous job and error schema、old active job index lets a sync sit beside paused history 等 4 个场景。 |
| `test_schema_ownership_gate.py` | `contract` | A1-03/A1-04: schema ownership, and the hard part of telling DDL from reads. |
| `test_sdk_verbatim_fallback.py` | `contract` | D98: the SDK's verbatim fallback must stay off. |
| `test_search_without_profile.py` | `contract` | What 'semantic_search' should do once the profile that filled the store is gone. |
| `test_secondary_source_adapters.py` | `contract` | 覆盖 pi and qwenwork keep tool and sidechain content out of the persons words、qwen code reads only the transcript beside its bookkeeping、qwen native parts preserve visible text not reasoning or tools 等 22 个场景。 |
| `test_secret_store_error_code_api.py` | `integration` | 3925a kernel item 3: the error code reaches the HTTP boundary. |
| `test_secret_store_error_codes.py` | `integration` | 3925a kernel item 3: keychain failure classification and backoff that heals. |
| `test_secure_storage.py` | `integration` | 覆盖 isolated keychain service namespace、custom keychain service is refused outside isolated tests、packaged keychain budget covers loaded process startup 等 20 个场景。 |
| `test_sedimentation_concurrency.py` | `contract` | Sedimentation runs several harnesses at once, but one harness at a time. |
| `test_sedimentation_switch.py` | `contract` | Stopping model-driven sedimentation without losing the configuration. |
| `test_service_level_pressure_acceptance.py` | `contract` | 覆盖 workbuddy recall remains available during 100k import 场景。 |
| `test_service_levels.py` | `contract` | 覆盖 background never consumes the realtime reservation、realtime is next for the same source after background yields、import history gets one slice after nine realtime admissions 等 8 个场景。 |
| `test_session_inbox.py` | `contract` | A session inbox is not a shared mailbox and a pull is not adoption. |
| `test_session_lease.py` | `contract` | A session file on disk is not proof that the session is online. |
| `test_session_registry.py` | `contract` | Trusted session identity is not a workspace path. |
| `test_snapshot_adapter.py` | `contract` | Selection-only binding reopens checksum-verified frozen copies. |
| `test_snapshot_pool.py` | `contract` | A whole-directory snapshot must not hold a thread everything else is waiting for. |
| `test_soak_probe_effectiveness.py` | `integration` | The soak probe must observe the load it reports on. |
| `test_source_access.py` | `contract` | 覆盖 source root requires explicit user grant、sensitive home directory is rejected without requesting os access、authorized file cannot escape root through a symlink 等 24 个场景。 |
| `test_source_authorization_state.py` | `unit` | Why a source cannot be read, named separately from whether it can. |
| `test_source_candidate_api.py` | `integration` | Candidate location changes must not implicitly grant or import anything. |
| `test_source_candidate_connections.py` | `contract` | 覆盖 empty candidate revisions preserve default fingerprint、persisted candidate requires exact revision、connection resolves candidate in transaction and prior replay never regrants 等 7 个场景。 |
| `test_source_candidates.py` | `contract` | 覆盖 preview is metadata only and cancel changes nothing、select persists only candidate and restarts with revision、compare and swap and aba cannot reuse old preview 等 9 个场景。 |
| `test_source_connection_intents.py` | `integration` | No user data: connection retries must retain the original intent and job. |
| `test_source_discovery.py` | `contract` | 覆盖 discovery is sorted bounded and suffix scoped、discovery does not follow directory or file symlinks 场景。 |
| `test_source_envelope_positions.py` | `unit` | 覆盖 exact body positions match legacy unwrapping、ambiguous envelope retains legacy text but never authorizes a body、repeated body uses actual tag position not first match 等 4 个场景。 |
| `test_source_import_receipts.py` | `integration` | 覆盖 receipt counts saved records without inventing dates or other sources、receipt counts same named cursor sessions per workspace、revocation during receipt query discards the old result 等 6 个场景。 |
| `test_source_lifecycle.py` | `unit` | 覆盖 unavailable source never implies deletion、complete healthy snapshot can reconcile missing evidence 场景。 |
| `test_source_parsing.py` | `contract` | 覆盖 reminders are removed only at message edges、original supported outer context and query envelopes、attributed reminder is opaque before finding the outer query 等 11 个场景。 |
| `test_source_permission_races.py` | `integration` | Permission execution regressions using temporary stores and Core ASGI instances. |
| `test_source_query_preview.py` | `contract` | 覆盖 preserves exact user text and span、supported outer wrapper is stripped without paraphrasing、cursor host prefixes are not shown as the user query 等 8 个场景。 |
| `test_source_read_revocation.py` | `contract` | 覆盖 revocation during model wait prevents late answer、source revocation stops every outward read but keeps other sources 场景。 |
| `test_source_readers.py` | `contract` | 覆盖 jsonl is resumable and does not advance over an incomplete tail、sqlite reader uses monotonic row checkpoint、zstd jsonl reader resumes by completed record 等 7 个场景。 |
| `test_source_registry_and_grants.py` | `contract` | 覆盖 catalog resolves defaults and marks environment overrides、recall hosts that can append join the live sync poller、declarations with no reader are named rather than mistaken for behaviour 等 15 个场景。 |
| `test_source_revocation_parking.py` | `integration` | Revoking a source parks its unfinished extraction instead of failing it (D73). |
| `test_source_root_identity_reboot.py` | `contract` | 覆盖 a grant survives the volume being renumbered after a restart、a replaced directory is still refused after a restart、the identity no longer depends on the device number 场景。 |
| `test_source_session_count_api.py` | `integration` | 3925a kernel item 2 (BUG-01): the session count reaches the authorization page. |
| `test_source_status_latency.py` | `integration` | Source status polling must not rediscover an already authorized history tree. |
| `test_source_sync_idempotent.py` | `integration` | Final-state Harness sync is idempotent and resumes missed source work. |
| `test_source_sync_permissions.py` | `integration` | S1.3 source permission tests use temporary files only, never real host history. |
| `test_source_totals.py` | `contract` | 覆盖 the first ask is answered from the rows、same named sessions in two workspaces count as two conversations、insert during count is not marked as already counted 等 7 个场景。 |
| `test_source_upload_intents.py` | `contract` | Upload identity binds retries without replacing file receipts or directory consent. |
| `test_source_write_switch.py` | `contract` | A frozen Harness produces no extraction tasks. |
| `test_sql_compat.py` | `contract` | The shared compatibility layer, especially 'rowcount'. |
| `test_sqlite_semantics_gate.py` | `contract` | D56: prevent SQLite implementation details spreading outside the data layer. |
| `test_sqlite_write_contention.py` | `contract` | Measure whether SQLite's single writer actually blocks us. |
| `test_storage_ingest_journal.py` | `contract` | 覆盖 legacy put and reopen never initialize journal、initialized put records final origins create revision and noop、attribution propagation journals old rows as maintenance only 等 11 个场景。 |
| `test_store_binding.py` | `contract` | A2: which store is in force, and honest reporting of it. |
| `test_store_binding_ratchet.py` | `contract` | D56: ratchet the retired dual-engine 'store_for' seam out of the Core. |
| `test_store_port.py` | `contract` | A1-04/A2: the store port, and that real implementations satisfy it. |
| `test_subscription_visibility.py` | `unit` | A shared directory is not a read grant. |
| `test_sync_facade.py` | `contract` | A2: the sync facade must keep the sync shape call sites already use. |
| `test_tail_metadata_lane.py` | `contract` | Post-response metadata retries must be bounded and never block the route. |
| `test_terminal_payload_reaping.py` | `contract` | Rule 14: dead-end payloads are actually reaped, not just reapable. |
| `test_two_level_discovery.py` | `contract` | 3925a kernel item 1: two-level discovery. |
| `test_ui_session.py` | `contract` | 覆盖 exchange is single use and creates a session、expired exchange cannot create a session、exchange is bound to the core instance 等 5 个场景。 |
| `test_ui_session_api.py` | `integration` | 覆盖 ui session exchange sets httponly strict cookie、ui session requires bearer for exchange、ui session reports missing web assets 场景。 |
| `test_unanswered_turn.py` | `contract` | BUG-06: which conversations end on a question nobody answered. |
| `test_unified_write_entry_wiring.py` | `integration` | The composition root routes every Evidence-first entry through one queue. |
| `test_vendor_sqlite_audit.py` | `contract` | A1-08: the audit that says which SQLite is MemHub's and which is a vendor's. |
| `test_visibility_background.py` | `integration` | The visibility background task: when it runs, and that it says so. |
| `test_whole_turn_hy_input.py` | `contract` | D74: a turn reaches HY whole; only what HY will read counts toward the ceiling. |
| `test_windows_process_control.py` | `contract` | R-29：Windows 上 'os.kill' 不是发信号，是无条件杀进程。 |
| `test_workbuddy_boundary.py` | `contract` | WorkBuddy only injects on SessionStart and UserPromptSubmit. |
| `test_workbuddy_parsing.py` | `contract` | 覆盖 automation preserves query without claiming fresh human authorship、automation context attributes do not depend on order or quote style、quoted full automation envelope is not scheduled provenance 等 17 个场景。 |
| `test_workbuddy_prompt_backfill.py` | `contract` | When the prompt hook's capture fails, the transcript file is what brings the words in. |
| `test_workspace_recall.py` | `contract` | Workspace memories were unreachable from every host; these pin the way in. |
| `test_write_queue_lease_renewal.py` | `contract` | Rule 8: a lease must outlive the work it covers, and be renewed while it runs. |

共 384 个测试文件。
