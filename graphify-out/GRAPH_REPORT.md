# Graph Report - .  (2026-07-21)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 2287 nodes · 5328 edges · 101 communities (98 shown, 3 thin omitted)
- Extraction: 83% EXTRACTED · 17% INFERRED · 0% AMBIGUOUS · INFERRED: 911 edges (avg confidence: 0.72)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `c69de34b`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- recovery.py
- PreFlightChecker
- run_pre_filter
- PdfRenderer
- BridgeContext
- runner.py
- plan_exports
- cluster.py
- analyzer.py
- test_geometry.py
- ReviewStateStore
- __init__.py
- OutlineLoader
- submission.py
- GeometryRelation
- check_vlm_asset
- test_export_planner.py
- AssetLocalizer
- Any
- ProjectWorkspace
- OutlineMatcher
- MinerUAPIError
- DownloadResult
- Outline
- build_book_view
- renderer.py
- planner.py
- create_workspace
- vlm.py
- test_bridges.py
- figure_roles.py
- test_figure_roles.py
- test_mineru_submission.py
- match_project
- classify_noise
- test_rerun_late_stages.py
- test_classify_image_roles.py
- adapt_mineru_layout
- Path
- classify_figure_roles
- Item
- is_stage_completed
- properties
- FigureRoleOverrideStore
- pre_filter_runtime.py
- find_template_decor_assets
- InboxLoader
- properties
- builder.py
- properties
- strategy
- properties
- run_prefilter_dry_run
- FigureRoleAnnotator
- properties
- properties
- _dir_hash
- outline.schema.json
- export-plan.schema.json
- GeometryFigure
- VlmRelationCandidate
- SubmissionHandle
- project-manifest.schema.json
- outline_used
- properties
- main
- items
- applies_to
- review.py
- MockFigureRoleProvider
- enum
- required
- sha256
- init_inbox_meta.py
- BridgeProvider
- _stub_workspace
- properties
- _first_sentence
- topic
- additionalProperties
- properties
- build_image_to_local_contexts
- enum
- vocabulary
- chapter_stopwords
- children
- _diag_dhash_template.py
- output_filename
- plan_id
- title
- unclassified_count
- type
- _block_order_with_chapter
- __init__.py
- conftest.py
- pdf-to-deeptutor

## God Nodes (most connected - your core abstractions)
1. `ProjectWorkspace` - 116 edges
2. `GeometryRelation` - 50 edges
3. `GeometryFigure` - 40 edges
4. `plan_exports()` - 39 edges
5. `AssetLocalizer` - 37 edges
6. `ExportPlanner` - 36 edges
7. `PdfRenderer` - 36 edges
8. `OutlineLoader` - 35 edges
9. `classify_figure_roles()` - 35 edges
10. `Item` - 34 edges

## Surprising Connections (you probably didn't know these)
- `mirror()` --calls--> `LocalMirrorDownloader`  [INFERRED]
  tests/test_asset_localizer.py → src/pdf2dt/assets/downloader.py
- `test_relation_key_stable_and_case_insensitive()` --calls--> `relation_key()`  [INFERRED]
  tests/test_geometry.py → src/pdf2dt/geometry/models.py
- `MockFigureRoleProvider` --uses--> `GeometryVlmProvider`  [INFERRED]
  scripts/classify_image_roles.py → src/pdf2dt/geometry/vlm.py
- `MockFigureRoleProvider` --uses--> `MiniMaxM3Provider`  [INFERRED]
  scripts/classify_image_roles.py → src/pdf2dt/geometry/vlm.py
- `MockFigureRoleProvider` --uses--> `SenseNovaProvider`  [INFERRED]
  scripts/classify_image_roles.py → src/pdf2dt/geometry/vlm.py

## Import Cycles
- None detected.

## Communities (101 total, 3 thin omitted)

### Community 0 - "recovery.py"
Cohesion: 0.06
Nodes (77): deque, align_markdown_to_layout(), AlignedMarkdownBlock, AlignmentRelation, AlignmentSummary, _anchor_distance_key(), _derive_relations(), _image_identity() (+69 more)

### Community 1 - "PreFlightChecker"
Cohesion: 0.07
Nodes (45): InboxTask, check_task(), PreFlightChecker, PreFlightError, Exception, LoadedMinerU, Path, Pre-flight checker — validates MinerU output before pipeline processing.  This m (+37 more)

### Community 2 - "run_pre_filter"
Cohesion: 0.06
Nodes (60): _aspect_evidence(), Candidate, _candidate_sort_key(), _canonical_json(), compute_asset_content_hash(), compute_inputs_hash(), compute_rules_config_hash(), Evidence (+52 more)

### Community 3 - "PdfRenderer"
Cohesion: 0.08
Nodes (45): FPDF, _find_cjk_font(), _PdfDoc, PdfRenderer, Any, Path, Render export plans to self-contained PDFs., Load the geometry queue produced by Stage 5.          We index figures by ``as (+37 more)

### Community 4 - "BridgeContext"
Cohesion: 0.06
Nodes (41): BridgeContext, BridgeProviderContext, GeometryBridgeProvider, MockBridgeProvider, OutlineBridgeProvider, Any, Path, Caller-supplied per-run context passed to :meth:`BridgeProvider.attach_context`. (+33 more)

### Community 5 - "runner.py"
Cohesion: 0.08
Nodes (36): Pipeline orchestration — runs the full processing pipeline., AssetLocalizationError, PipelineResult, PipelineRunner, PreFlightFailureError, Any, Path, RuntimeError (+28 more)

### Community 6 - "plan_exports"
Cohesion: 0.10
Nodes (46): plan_exports(), Run Stage 4c and persist the export plan collection., load_workspace(), Open an existing project workspace. Raises if no manifest is found., _build_demo_workspace(), _demo_workspace_has_min_two_plans(), Path, Smoke tests for Stage 4c export planner and Stage 7 PDF renderer. (+38 more)

### Community 7 - "cluster.py"
Cohesion: 0.08
Nodes (48): AssetDescriptor, _AssetFingerprint, build_cluster_planner(), ClusterDecision, ClusterPlanner, _dhash(), _fingerprint(), _hamming() (+40 more)

### Community 8 - "analyzer.py"
Cohesion: 0.07
Nodes (44): analyze_geometry(), _asset_paths_by_id(), _confidence(), _entities_for_rule(), _extract_points(), _extract_segments(), _figure_id_for(), GeometryExtractionReport (+36 more)

### Community 9 - "test_geometry.py"
Cohesion: 0.08
Nodes (46): GeometryAnalyzer, Deterministic rule-based analyzer for one figure-bound item.      The class is s, build_geometry_analyzer(), HybridGeometryAnalyzer, MiniMaxM3Provider, MiniMax-M3 over its Anthropic-compatible Messages endpoint., Rules-first analyzer that adds review-only VLM candidates.      Selection cont, Build a rules-only or hybrid analyzer from a CLI-safe provider name. (+38 more)

### Community 10 - "ReviewStateStore"
Cohesion: 0.09
Nodes (40): Stable string key for a relation.      The key is type + case-folded, sorted ent, relation_key(), Review state — Stage 6.  Stage 6 turns the geometry queue produced by Stage 5, apply_review(), load_review_state(), PromotionError, Any, Enum (+32 more)

### Community 11 - "__init__.py"
Cohesion: 0.11
Nodes (42): MinerU submission adapter — Stage 0a provider module.  This package wraps the, check_quota(), estimate_pdf_pages(), load_quota_state(), _now_iso(), Any, Path, quota_path() (+34 more)

### Community 12 - "OutlineLoader"
Cohesion: 0.12
Nodes (38): load_outline(), OutlineLoader, OutlineLoadError, Any, Path, ValueError, Load and validate an outline YAML file., Convenience wrapper around :meth:`OutlineLoader.load`. (+30 more)

### Community 13 - "submission.py"
Cohesion: 0.09
Nodes (36): BatchResult, FileSpec, MinerUQuotaError, Snapshot of one task inside a batch., Result of polling a batch. Always carries one entry per file., Raised when the API itself reports the daily quota is exhausted., One file to be uploaded as part of a batch., TaskInfo (+28 more)

### Community 14 - "GeometryRelation"
Cohesion: 0.12
Nodes (40): describe_figure(), describe_figure_block(), detect_locale(), _detect_locale_for_figure(), _format(), format_relation_bullets(), _includable_relations(), _join_entities() (+32 more)

### Community 15 - "check_vlm_asset"
Cohesion: 0.09
Nodes (40): check_vlm_asset(), Path, Pre-flight resource gates for VLM submissions.  Both VLM providers base64-encode, Outcome of one asset pre-flight check., Validate a local asset before it is sent to a VLM provider.      The check never, VlmGateResult, Path, Tests for the VLM asset resource gate (P1 #2).  The gate is the deterministic (+32 more)

### Community 16 - "test_export_planner.py"
Cohesion: 0.06
Nodes (41): _book_view_with(), Any, Path, Tests for the planner's text-noise defence., When one plan's item set is a proper subset of another, the     subset plan is, Two plans with identical item sets are NOT dropped — only     *proper* subsets, Stage 4c may remove duplicate exports only with decisive Stage 4b scores., Wrap a list of item dicts in a minimal BookView shape. (+33 more)

### Community 17 - "AssetLocalizer"
Cohesion: 0.09
Nodes (22): AssetId, AssetLocalizer, localize_loaded_task(), LoadedMinerU, Path, Asset localizer — Stage 2 pipeline step., Convenience entry point., Download, validate, hash, deduplicate, persist, and rewrite assets. (+14 more)

### Community 18 - "Any"
Cohesion: 0.10
Nodes (24): AssetRef, _bbox_from_block(), _bbox_union(), BookItem, BookViewBuilder, Chapter, _inject_assets(), Any (+16 more)

### Community 19 - "ProjectWorkspace"
Cohesion: 0.09
Nodes (16): ProjectWorkspace, Any, Path, Copy the original PDF into source/ for immutable preservation., Copy a MinerU task directory's contents into providers/mineru/raw/.          If, A single project's on-disk workspace., Resolve a subdirectory under the project root, ensuring it exists., Resolve a file path under the project root (parent dirs auto-created). (+8 more)

### Community 20 - "OutlineMatcher"
Cohesion: 0.09
Nodes (34): OutlineMatcher, Assign items to outline leaves using vocabulary scoring., assignments(), matcher(), outline(), Path, End-to-end tests for :class:`OutlineMatcher` and ``match_project``.  The match, The 12.5 小结 section (item-0020) bundles a 鸡兔同笼 杂题     together with heavy geome (+26 more)

### Community 21 - "MinerUAPIError"
Cohesion: 0.11
Nodes (22): _envelop_check(), MinerUAPIError, MinerUAuthError, MinerUClient, _parse_batch_result(), _parse_upload_grant(), Any, Client (+14 more)

### Community 22 - "DownloadResult"
Cohesion: 0.11
Nodes (23): main(), End-to-end CLI for the MVP pipeline (Stages 0-2, optional Stages 3 / 4b / 4c / 7, AssetDownloader, HttpxDownloader, LocalFirstDownloader, LocalMirrorDownloader, Path, Downloader abstraction with an httpx default implementation. (+15 more)

### Community 23 - "Outline"
Cohesion: 0.09
Nodes (24): Pattern, Outline-driven content matching for Stage 4b.  Public surface:  * :class:`Ou, Pre-compile the positive and negative regex patterns per leaf., _collect_ancestors(), Outline, Outline model and YAML loader.  An outline is a user-supplied taxonomy (see ``do, A loaded outline ready for matching., All leaves across the topic tree (preserving declaration order). (+16 more)

### Community 24 - "build_book_view"
Cohesion: 0.11
Nodes (34): BookView, build_book_view(), Build a :class:`BookView` and persist it to the workspace., _all_items(), built_book(), demo_workspace(), _iter_items(), Path (+26 more)

### Community 25 - "renderer.py"
Cohesion: 0.11
Nodes (24): Bridge, PlanAccessor, Read-only view of one plan that a provider can inspect.      Kept separate from, One transition paragraph between two adjacent plans.      A bridge *belongs* to, Stage 4c export planning and Stage 7 PDF rendering.  Public surface:  * :class:`, ExportPlan, ExportPlanCollection, PlanError (+16 more)

### Community 26 - "planner.py"
Cohesion: 0.11
Nodes (20): backfill_inline_asset_refs(), _basename(), ExportPlanner, is_intro_item(), _load_assets_basename_index(), _now(), Any, Enum (+12 more)

### Community 27 - "create_workspace"
Cohesion: 0.10
Nodes (24): create_workspace(), Create a new project workspace and seed its manifest., mirror(), Path, End-to-end test for the pipeline runner against the synthetic fixture., Re-running against the same project_root skips completed stages., End-to-end tests running Stages 0→7 with an outline., Run the full pipeline (Stage 0 through 7) with an outline. (+16 more)

### Community 28 - "vlm.py"
Cohesion: 0.11
Nodes (23): _coerce_confidence(), _extract_message_text(), _extract_sensenova_text(), _file_sha256(), GeometryVlmProvider, _parse_response(), Any, Client (+15 more)

### Community 29 - "test_bridges.py"
Cohesion: 0.09
Nodes (26): main(), Re-run Stages 3 / 4b / 4c / 7 against an existing workspace.  Skips Stages 0-2, known_bridge_providers(), NoOpBridgeProvider, Mode C bridge generation.  A *bridge* is one transition paragraph that the plann, Provider that never inserts anything.      Useful for tests that want to assert, Look up a provider by name, pass through instances, or     fall back to the defa, Register a custom provider.      Used by future LLM-backed implementations to in (+18 more)

### Community 30 - "figure_roles.py"
Cohesion: 0.12
Nodes (22): Load figure role classifications and user overrides.          Missing or malfo, _coerce_role(), effective_role(), effective_role_for_use(), _FigureCandidate, FigureRole, FigureRoleRecord, FigureRoleStore (+14 more)

### Community 31 - "test_figure_roles.py"
Cohesion: 0.17
Nodes (28): _make_png(), Any, MonkeyPatch, Path, Tests for the figure role classifier and override store., Provider whose response is set per call from a script queue., Build a minimal workspace with book_view + assets_registry., --no-cache must actually disable on-disk caching; otherwise     iterating on th (+20 more)

### Community 32 - "test_mineru_submission.py"
Cohesion: 0.17
Nodes (29): Request, _build_client(), _build_submission(), _build_zip_bytes(), _happy_handler(), _make_pdf(), _make_real_pdf(), _ok() (+21 more)

### Community 33 - "match_project"
Cohesion: 0.11
Nodes (20): leaves_and_order(), match_project(), MatchDetail, MatchReport, _now(), Any, Path, OutlineMatcher — assigns BookView items to outline leaves.  Pipeline integrati (+12 more)

### Community 34 - "classify_noise"
Cohesion: 0.11
Nodes (27): classify_noise(), is_noise_item(), NoiseVerdict, partition_items(), Heuristics for recognising items that should never be exported.  These are tex, Boolean shortcut over :func:`classify_noise`., Split ``items`` into (kept, dropped) by noise classification.      The dropped, Why we flagged (or didn't) an item.      ``reason`` is human-readable; ``is_no (+19 more)

### Community 35 - "test_rerun_late_stages.py"
Cohesion: 0.15
Nodes (26): CompletedProcess, Path, Tests for scripts/rerun_late_stages.py new flags., A JSON object (not array) must be rejected with code 11., A list with a non-object element must be rejected (code 12)., A complete workspace running `--geometry` should record     stage5_geometry as, ``--help`` lists the new flags so users can discover them.      Pure smoke tes, Help text must explain forced modes and list built-in providers. (+18 more)

### Community 36 - "test_classify_image_roles.py"
Cohesion: 0.08
Nodes (19): _load_script_module(), Tests for the mock figure-role provider in ``scripts/classify_image_roles.py``., A figure bound to a 本讲知识点汇总 heading with a long body     (real problem text) is, A 90x90 PNG is a decorative icon, not a math problem., A 1200x100 banner PNG is a publisher template bar., An asset in the pre-computed template-decor set must be     flagged decor regar, The mock must look up the template-decor set by image file     stem, not by ful, Watermark is rule 1, template-decor is rule 3. When both     could fire, waterm (+11 more)

### Community 37 - "adapt_mineru_layout"
Cohesion: 0.12
Nodes (23): adapt_mineru_layout(), _bbox(), _collect_text(), _first_image_url(), is_mineru_layout(), iter_images(), Any, MinerU ``pdf_info[]`` schema adapter.  BookView's ``_load_layout`` understands t (+15 more)

### Community 38 - "Path"
Cohesion: 0.12
Nodes (11): assets_dir(), mirror(), Path, Tests for Stage 2 asset localization using the synthetic fixture., Regression: layout.json may contain relative image paths like         ``images/, Same-name assets must not be selected by registry insertion order., TestConvenienceFunction, TestDedup (+3 more)

### Community 39 - "classify_figure_roles"
Cohesion: 0.13
Nodes (22): _build_provider(), _distribution(), main(), Classify figure roles for an existing project workspace.  Reads ``book_view/bo, build_prefilter_candidates(), Build deterministic candidates and return ``(candidates, has_registry)``., classify_figure_roles(), FigureRoleError (+14 more)

### Community 40 - "Item"
Cohesion: 0.14
Nodes (20): extract_items(), extract_items_from_file(), Item, iter_chapters(), Path, BookView item extraction from normalized markdown.  This is a deliberately small, Convenience wrapper that reads a markdown file., Group items under their chapter heading for diagnostic output. (+12 more)

### Community 41 - "is_stage_completed"
Cohesion: 0.14
Nodes (18): dict, Project workspace — Stage 0.  A project is a directory on disk that owns the ful, get_stage_status(), is_stage_completed(), _now(), Any, Enum, str (+10 more)

### Community 42 - "properties"
Cohesion: 0.10
Nodes (21): completed, failed, pending, running, skipped, properties, format, type (+13 more)

### Community 43 - "FigureRoleOverrideStore"
Cohesion: 0.16
Nodes (14): _load_content_figure_ids(), Return locally reviewed figure/asset IDs that must survive filtering., apply_figure_role_overrides(), FigureRoleDecision, FigureRoleOverrideStore, _now(), Path, One explicit human override for a figure's role.      A role decision does not (+6 more)

### Community 44 - "pre_filter_runtime.py"
Cohesion: 0.12
Nodes (15): PreFilterRunResult, The pure in-memory result of one dry-run.      This is a dry-run artifact only, Per-rule count of candidates on which the rule's         predicate fired, regar, Number of ``PreFilterDecision`` records whose         ``decision == "decor"`` —, Informational projection: the number of DISTINCT         ``(asset_id, item_id,, Read-only, deterministically-ordered view of the         per-candidate fired-ru, build_prefilter_report(), build_template_decor_audit() (+7 more)

### Community 45 - "find_template_decor_assets"
Cohesion: 0.16
Nodes (18): _dhash(), find_template_decor_assets(), _hamming(), Path, Identify template / banner decor assets by perceptual-hash clustering.  For ev, Return asset_ids that look like repeated template decorations.      The dhash, Unit tests for the template-decor perceptual-hash clusterer., Write a 248x92 banner with a unique but reproducible pattern.      seed contro (+10 more)

### Community 46 - "InboxLoader"
Cohesion: 0.14
Nodes (6): InboxLoader, loader(), Tests for the MinerU inbox loader using the synthetic fixture., TestLoadTask, TestScan, TestValidateTaskDir

### Community 47 - "properties"
Cohesion: 0.13
Nodes (18): properties, type, description, items, type, description, items, type (+10 more)

### Community 48 - "builder.py"
Cohesion: 0.24
Nodes (16): BookViewBuildError, _load_assets_registry(), _load_assignments(), _load_json(), _load_layout(), _load_structure_context(), _now(), _patched_init() (+8 more)

### Community 49 - "properties"
Cohesion: 0.12
Nodes (16): description, minLength, type, description, minLength, type, properties, name (+8 more)

### Community 50 - "strategy"
Cohesion: 0.16
Nodes (15): enum, description, enum, A, B, C, additionalProperties, type (+7 more)

### Community 51 - "properties"
Cohesion: 0.13
Nodes (15): format, type, type, minLength, type, properties, created_at, exports (+7 more)

### Community 52 - "run_prefilter_dry_run"
Cohesion: 0.35
Nodes (14): Run the safe dry-run and refuse to overwrite an existing report., run_prefilter_dry_run(), MonkeyPatch, Path, Workspace/report contract tests for the Phase 1.5 dry-run adapter., test_cluster_audit_is_separate_from_savings(), test_dry_run_report_is_provider_free_and_candidate_granular(), test_existing_report_is_not_overwritten() (+6 more)

### Community 53 - "FigureRoleAnnotator"
Cohesion: 0.20
Nodes (8): _coerce_confidence(), _extract_json_object(), FigureRoleAnnotator, Path, Pull the first JSON object out of a model response., Run an LLM provider across figure-bound book items.      The annotator never r, Classify a single figure. Always returns a FigureRole., _sha256_file()

### Community 54 - "properties"
Cohesion: 0.16
Nodes (14): item_id, items, type, description, type, additionalProperties, description, items (+6 more)

### Community 55 - "properties"
Cohesion: 0.14
Nodes (14): type, description, pattern, type, minLength, type, description, minimum (+6 more)

### Community 56 - "_dir_hash"
Cohesion: 0.23
Nodes (13): _dir_hash(), Compute a combined SHA-256 over all files under *directory*., Path, Tests for :class:`ProjectWorkspace` low-level helpers., Same directory contents must hash identically across runs.      The hash is us, Regression: the call must encode file size in big-endian.      On Python 3.10, Empty directory still produces a stable hash (no crash)., Only file contents contribute; bare directories do not. (+5 more)

### Community 57 - "outline.schema.json"
Cohesion: 0.15
Nodes (12): applies_to, name, topics, version, additionalProperties, description, $id, outline_id (+4 more)

### Community 58 - "export-plan.schema.json"
Cohesion: 0.15
Nodes (12): items, mode, outline_used, output_filename, plan_id, additionalProperties, description, $id (+4 more)

### Community 59 - "GeometryFigure"
Cohesion: 0.21
Nodes (8): GeometryFigure, Any, One figure and its structured interpretation., Decide whether a hybrid analyzer should pay for one VLM call.      Rules alrea, should_call_vlm(), test_geometry_figure_round_trip(), test_geometry_relation_round_trip(), test_should_call_vlm_decisions()

### Community 60 - "VlmRelationCandidate"
Cohesion: 0.19
Nodes (8): A relation proposed by a model before evidence-safe merging., VlmRelationCandidate, _FakeVlmProvider, In-memory VLM provider that never touches the network.      The audit calls fo, The runner must accept a pre-built analyzer and apply it on Stage 5.      We i, ``force_geometry=True`` on the runner must run Stage 5 through the     injected, test_pipeline_runner_accepts_injected_geometry_analyzer(), test_pipeline_runner_force_geometry_re_extends_with_injected_analyzer()

### Community 61 - "SubmissionHandle"
Cohesion: 0.23
Nodes (10): _extract_page_count(), load_handle(), Any, Write ``.mineru_handle.json`` under ``inbox_dir``., Read ``.mineru_handle.json`` from ``inbox_dir``., Handle returned by :meth:`MinerUSubmission.submit`.      Persisted to ``inbox/, save_handle(), SubmissionHandle (+2 more)

### Community 62 - "project-manifest.schema.json"
Cohesion: 0.17
Nodes (11): created_at, project_id, source, stages, title, additionalProperties, $id, required (+3 more)

### Community 63 - "outline_used"
Cohesion: 0.17
Nodes (12): object, null, outline_id, string, additionalProperties, description, required, type (+4 more)

### Community 64 - "properties"
Cohesion: 0.17
Nodes (12): type, type, additionalProperties, properties, type, book, grade, metadata (+4 more)

### Community 65 - "main"
Cohesion: 0.26
Nodes (10): main(), Poll a previously-submitted MinerU task and download the result.  Resume helpe, _read_token(), main(), _print_success(), Path, Submit a local PDF to the MinerU cloud API.  This is the Stage 0a CLI. It wrap, Append ``minerU.result_expires_at`` to the inbox meta.json. (+2 more)

### Community 66 - "items"
Cohesion: 0.18
Nodes (11): blocked, ready, warning, type, items, additionalProperties, properties, type (+3 more)

### Community 67 - "applies_to"
Cohesion: 0.18
Nodes (11): subject, additionalProperties, properties, required, type, applies_to, stage, subject (+3 more)

### Community 68 - "review.py"
Cohesion: 0.42
Nodes (9): Namespace, _cmd_apply(), _cmd_list(), _cmd_report(), _load_workspace(), main(), Path, Stage 6 review CLI.  This script is the user-facing entry point for managing g (+1 more)

### Community 69 - "MockFigureRoleProvider"
Cohesion: 0.27
Nodes (4): MockFigureRoleProvider, Bulk-set the set of asset_ids that are template decorations.          Replaces, Return text *after* the metadata block — i.e. the         title + surrounding m, Module-level mock so tests can import and assert on its behavior.      The ori

### Community 70 - "enum"
Cohesion: 0.22
Nodes (9): chapter_summary, definition, exercise, method, other, solution, theorem, worked_example (+1 more)

### Community 71 - "required"
Cohesion: 0.25
Nodes (9): export_id, path, sha256, validation_status, required, source, additionalProperties, required (+1 more)

### Community 72 - "sha256"
Cohesion: 0.22
Nodes (9): minimum, type, type, page_count, path, sha256, pattern, type (+1 more)

### Community 73 - "init_inbox_meta.py"
Cohesion: 0.42
Nodes (8): _build_meta(), main(), _page_count_hint(), Path, Initialize a meta.json for a MinerU task dropped into the inbox.  You drop the, Best-effort page count: prefer PDF, fall back to layout.json length., _sha256_file(), _slugify()

### Community 74 - "BridgeProvider"
Cohesion: 0.22
Nodes (6): BridgeProvider, Protocol, Receive a one-time context for the current plan run.          Default implementa, Pluggable bridge generator.      Implementations must be deterministic given the, Every built-in provider must satisfy the BridgeProvider     protocol so the pla, test_protocol_satisfied_by_all_built_in_providers()

### Community 75 - "_stub_workspace"
Cohesion: 0.31
Nodes (9): Path, Build a ProjectWorkspace on tmp_path without a manifest., The renderer must write a description paragraph when at     least one confirmed, When every relation is unreviewed, no description is     written.  This preserv, A malformed geometry entry must not crash the renderer., _stub_workspace(), test_renderer_skips_description_on_malformed_queue(), test_renderer_skips_description_when_no_includable() (+1 more)

### Community 76 - "properties"
Cohesion: 0.25
Nodes (8): type, properties, outline_id, sha256, version, pattern, type, type

### Community 77 - "_first_sentence"
Cohesion: 0.25
Nodes (8): _first_sentence(), _normalize_structure_text(), _normalize_text(), Collapse ``![alt](url)`` → ``alt`` for matching purposes., Lowercase, strip whitespace and markdown noise for fingerprint matching., Normalize Markdown source text for exact sidecar-to-item matching., Return the first non-trivial sentence / chunk for matching., _strip_markdown_image_alt()

### Community 78 - "topic"
Cohesion: 0.29
Nodes (7): id, label, $defs, topic, additionalProperties, required, type

### Community 79 - "additionalProperties"
Cohesion: 0.29
Nodes (7): status, additionalProperties, required, type, stages, additionalProperties, type

### Community 80 - "properties"
Cohesion: 0.29
Nodes (7): type, properties, minimum, type, item_id, item_type, position

### Community 81 - "build_image_to_local_contexts"
Cohesion: 0.33
Nodes (7): build_image_to_local_contexts(), _local_image_context(), _normalized_image_path(), Normalize a local or Markdown image path for marker matching., Return bounded OCR text around the candidate's Markdown image marker.      Min, Build bounded OCR context keyed by asset id from normalized Markdown.      Boo, test_local_contexts_fall_back_to_normalized_full_markdown()

### Community 82 - "enum"
Cohesion: 0.33
Nodes (6): A, B, C, description, enum, mode

### Community 83 - "vocabulary"
Cohesion: 0.33
Nodes (6): additionalProperties, type, vocabulary, additionalProperties, description, type

### Community 84 - "chapter_stopwords"
Cohesion: 0.40
Nodes (5): description, items, type, minLength, chapter_stopwords

### Community 85 - "children"
Cohesion: 0.40
Nodes (5): items, type, $ref, children, items

### Community 86 - "_diag_dhash_template.py"
Cohesion: 0.40
Nodes (3): dhash(), Path, Find figures that look visually like known banner templates.  Use a perceptual

### Community 87 - "output_filename"
Cohesion: 0.50
Nodes (4): description, pattern, type, output_filename

### Community 88 - "plan_id"
Cohesion: 0.50
Nodes (4): description, minLength, type, plan_id

### Community 89 - "title"
Cohesion: 0.50
Nodes (4): title, description, minLength, type

### Community 90 - "unclassified_count"
Cohesion: 0.50
Nodes (4): unclassified_count, description, minimum, type

### Community 91 - "type"
Cohesion: 0.50
Nodes (4): type, null, string, error

### Community 92 - "_block_order_with_chapter"
Cohesion: 0.50
Nodes (4): _block_id_sort_key(), _block_order_with_chapter(), Stable ordering of blocks by (chapter_path, block_id)., Sort ``p001-b007`` → (1, 7, 'p001-b007').

## Knowledge Gaps
- **176 isolated node(s):** `pdf-to-deeptutor`, `$schema`, `$id`, `title`, `description` (+171 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ProjectWorkspace` connect `ProjectWorkspace` to `PdfRenderer`, `runner.py`, `plan_exports`, `analyzer.py`, `test_geometry.py`, `ReviewStateStore`, `build_book_view`, `renderer.py`, `planner.py`, `create_workspace`, `test_bridges.py`, `figure_roles.py`, `test_figure_roles.py`, `match_project`, `test_rerun_late_stages.py`, `classify_figure_roles`, `is_stage_completed`, `FigureRoleOverrideStore`, `pre_filter_runtime.py`, `builder.py`, `run_prefilter_dry_run`, `FigureRoleAnnotator`, `review.py`, `_stub_workspace`, `build_image_to_local_contexts`?**
  _High betweenness centrality (0.179) - this node is a cross-community bridge._
- **Why does `PreFlightChecker` connect `PreFlightChecker` to `runner.py`, `InboxLoader`?**
  _High betweenness centrality (0.059) - this node is a cross-community bridge._
- **Why does `recover_document_structure()` connect `recovery.py` to `build_book_view`, `runner.py`, `adapt_mineru_layout`?**
  _High betweenness centrality (0.037) - this node is a cross-community bridge._
- **Are the 9 inferred relationships involving `ProjectWorkspace` (e.g. with `main()` and `StageRecord`) actually correct?**
  _`ProjectWorkspace` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 14 inferred relationships involving `GeometryRelation` (e.g. with `GeometryAnalyzer` and `GeometryExtractionReport`) actually correct?**
  _`GeometryRelation` has 14 INFERRED edges - model-reasoned connections that need verification._
- **Are the 13 inferred relationships involving `GeometryFigure` (e.g. with `GeometryAnalyzer` and `GeometryExtractionReport`) actually correct?**
  _`GeometryFigure` has 13 INFERRED edges - model-reasoned connections that need verification._
- **Are the 19 inferred relationships involving `plan_exports()` (e.g. with `main()` and `main()`) actually correct?**
  _`plan_exports()` has 19 INFERRED edges - model-reasoned connections that need verification._