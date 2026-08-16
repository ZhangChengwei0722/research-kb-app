export type WorkspaceOption = {
  option_id: string;
  label: string;
  workspace_id: string;
  domain_profile_id: string;
  domain_name: string;
  domain_version: string;
};

export type CatalogStatus = {
  projection_state: string;
  item_count: number;
  source_watermark?: string | null;
  unknown_record_kinds?: string[];
  operation?: OperationStatus;
};

export type OperationStatus = {
  category: "agent_task" | "discovery" | "intake" | "catalog_rebuild" | "tag" | null;
  state: string;
  job_id: string | null;
  diagnostic_code: string | null;
};

export type DiscoverySearchRequest = {
  request_version: "1.0";
  date_from: string;
  date_until: string;
  title_keywords: string[];
  abstract_keywords: string[];
  keyword_mode: "any" | "all";
  include_preprints: boolean;
  max_results: number;
};

export type DiscoveryResult = {
  result_key: string;
  title: string;
  authors: string[];
  first_publication_date: string;
  journal_or_server: string | null;
  doi: string | null;
  paper_type: string;
  publication_types: string[];
  abstract: string | null;
  matched_keywords: string[];
  match_location: string;
  discovery_sources: Array<{ provider: string; source: string; record_id: string }>;
  full_text_status: string;
  version_relationship: { status: string; related_doi: string | null };
  possible_duplicate_result_keys: string[];
};

export type DiscoveryReport = {
  status: string;
  interface_version: string;
  provider: "europe-pmc";
  provider_api_version: string;
  query: Omit<DiscoverySearchRequest, "request_version">;
  provider_hit_count: number;
  scanned_result_count: number;
  returned_result_count: number;
  truncated: boolean;
  persistent_writes: 0;
  results: DiscoveryResult[];
};

export type DiscoveryCandidate = {
  candidate_id: string;
  result_key: string;
  title: string;
  doi: string | null;
  first_publication_date: string;
  paper_type: string;
  full_text_status: string;
  acquisition_status: string;
  target_question_ids: string[];
  selection_context_count: number;
};

export type DiscoveryCandidateList = {
  status: string;
  candidate_count: number;
  page_size: number;
  candidates: DiscoveryCandidate[];
  next_cursor: string | null;
  persistent_writes: number;
};

export type DiscoveryResolution = {
  status: string;
  candidate_id: string;
  resolution_status: string;
  access_basis: string;
  license_observation: string;
  manual_reason: string | null;
  persistent_writes: number;
};

export type AcquiredCandidateHandoff = {
  status: string;
  candidate_id: string;
  registration: { state: string; paper_ids: string[] };
  persistent_writes: number;
};

export type IntakeBibliography = {
  title: string | null;
  authors: string[];
  year: number | null;
  doi: string | null;
};

export type IntakeSemanticRequest = {
  requested_operation: "basic_paper_card" | "basic_review_memory";
  document_route: "primary" | "review" | null;
  route_reason: "mixed_document" | null;
  bibliography: IntakeBibliography;
};

export type IntakeStartRequest = IntakeSemanticRequest & {
  idempotency_key: string;
};

export type InboxStartRequest = IntakeStartRequest & {
  candidate_token: string;
  min_stable_age_seconds: number;
};

export type IntakeCandidate = {
  candidate_token: string;
  name: string;
  size_bytes: number;
};

export type InboxScanResult = {
  status: string;
  candidates: IntakeCandidate[];
  persistent_writes: number;
};

export type PipelineProjection = {
  job_id: string;
  state_id: string;
  state_digest: string;
  revision: number;
  requested_route: string;
  requested_depth: string;
  current_node: string;
  status: string;
  wait_reason: string | null;
  retry_count: number;
  terminal_receipt: boolean;
  updated_at: string;
  can_resume: boolean;
  can_cancel: boolean;
};

export type IntakeJobList = {
  status: string;
  jobs: PipelineProjection[];
  next_cursor: string | null;
  persistent_writes: number;
};

export type IntakeJobListFilter = {
  requested_route: "local_source";
  requested_depth: "semantic_gate";
};

export type AdequacyCapability = {
  status: "yes" | "no" | "uncertain" | string;
  reasons: string[];
  authority_layers: string[];
};

export type SourceAdequacyProjection = {
  requested_operation: string;
  gate_status: string;
  required_capability: string;
  freshness: string;
  capability_status: string;
  wait_reason: string | null;
  capabilities: Record<string, AdequacyCapability>;
  known_limitations: string[];
  recommended_actions: string[];
};

export type SourceAdequacyResolutionContext = {
  status: "success";
  application_service_interface_version: string;
  resolution_registry_version: string;
  resolution_state:
    | "not_required"
    | "review_required"
    | "accepted_refresh_required"
    | "remediation_refresh_required"
    | "not_resolvable"
    | "stale";
  task: {
    task_id: string;
    state_id: string;
    state_digest: string;
    task_kind: string;
    status: string;
  };
  paper_id: string;
  job_id: string;
  basis_profile_id: string;
  requested_operation: string;
  required_capability: string;
  machine_status: string;
  hard_failure: boolean;
  freshness: string;
  known_limitations: string[];
  recommended_actions: string[];
  allowed_actions: Array<"accept_uncertainty" | "remediation_required">;
  source_review_required: boolean;
  successor_profile_id?: string;
  decision_action?: "accept_uncertainty" | "remediation_required";
  persistent_writes: 0;
  canonical_scientific_write: false;
};

export type SourceAdequacyReviewOpenResult = {
  status: "success";
  task_id: string;
  basis_profile_id: string;
  reader: { provider: string };
  confirmation: { confirmation_id: string; expires_in_seconds: number };
  persistent_writes: 0;
  canonical_scientific_write: false;
};

export type IntakeSourceAdequacyResolutionContext = {
  status: "success";
  application_service_interface_version: string;
  resolution_registry_version: string;
  resolution_state:
    | "review_required"
    | "accepted_continuation_required"
    | "continuation_in_progress"
    | "continued"
    | "remediation_required"
    | "not_resolvable"
    | "stale"
    | "not_required";
  job: {
    job_id: string;
    state_id: string;
    state_digest: string;
    status: string;
    current_node: string;
    wait_reason: string | null;
  };
  paper_id: string;
  basis_profile_id: string;
  requested_operation: string;
  required_capability: string;
  document_route: "primary" | "review";
  route_reason: "mixed_document" | null;
  machine_status: string;
  hard_failure: boolean;
  freshness: string;
  source_availability: string;
  known_limitations: string[];
  recommended_actions: string[];
  allowed_actions: Array<"accept_uncertainty" | "remediation_required">;
  source_review_required: boolean;
  successor_profile_id?: string;
  decision_action?: "accept_uncertainty" | "remediation_required";
  persistent_writes: number;
  canonical_scientific_write: false;
};

export type IntakeSourceAdequacyReviewOpenResult = {
  status: "success";
  job_id: string;
  basis_profile_id: string;
  reader: { provider: string; page_targeting?: string };
  confirmation: { confirmation_id: string; expires_in_seconds: number };
  persistent_writes: 0;
  canonical_scientific_write: false;
};

export type IntakeSourceAdequacyDecisionResult = {
  status: "success";
  resolution_state: IntakeSourceAdequacyResolutionContext["resolution_state"];
  job: IntakeSourceAdequacyResolutionContext["job"];
  paper_id: string;
  requested_operation: string;
  required_capability: string;
  basis_profile_id: string;
  successor_profile_id?: string;
  decision_action: "accept_uncertainty" | "remediation_required";
  document_route: "primary" | "review";
  route_reason: "mixed_document" | null;
  refresh_required?: boolean;
  persistent_writes: number;
  canonical_scientific_write: false;
  operation: OperationStatus;
};

export type IntakeJobDetail = {
  status: string;
  pipeline: PipelineProjection;
  ingress_mode: "upload" | "watched_inbox" | string | null;
  paper_id: string | null;
  requested_operation: "basic_paper_card" | "basic_review_memory" | null;
  document_route: "primary" | "review" | null;
  route_reason: "mixed_document" | null;
  source_adequacy: SourceAdequacyProjection | null;
  persistent_writes: number;
};

export type TrustedParsePrepareRequest = {
  expected_state_id: string;
  expected_state_digest: string;
};

export type TrustedParsePreparation = {
  status: "success";
  interface_version?: string;
  lease_token: string;
  paper_id: string;
  source: {
    display_name: string;
    size_bytes: number;
    identity_status: "current";
  };
  parser: {
    adapter: string;
    version: string;
  };
  parser_profile_id: string;
  policy_version: string;
  allowed_operation: "parse_run";
  expires_at: string;
  limited_trust_warning: string;
  supervised_reparse_required: boolean;
  aggregate_preview_digest: string;
  persistent_writes: 0;
};

export type TrustedParseApproveRequest = {
  lease_token: string;
  aggregate_preview_digest: string;
};

export type IntakeAccepted = {
  status: "accepted";
  operation: OperationStatus;
  cancel_outcome?: "accepted" | "too_late";
};

export type ResumeIntakeRequest = IntakeSemanticRequest & {
  expected_state_id: string;
  expected_state_digest: string;
};

export type CancelIntakeRequest = {
  expected_state_id: string;
  expected_state_digest: string;
};

export type AgentTaskKind =
  | "document_route_resolution"
  | "primary_semantic_processing"
  | "review_semantic_processing"
  | "knowledge_query_report"
  | "organization_proposal"
  | "research_synthesis_drafting"
  | "question_screening_criteria_proposal"
  | "question_screening_decision_proposal";

export type KnowledgeQueryType =
  | "single_paper_explanation"
  | "seven_section_overview"
  | "methods"
  | "selected_paper_comparison"
  | "trend_problem_discussion"
  | "evidence_find";

export type AgentTaskKindDefinition = {
  task_kind: string;
  required_content_classes: string[];
  optional_content_classes: string[];
  result_contract: string;
  runtime_status: string;
  max_items: number;
  max_payload_bytes: number;
  max_excerpt_bytes: number;
};

export type AgentExecutorDefinition = {
  executor_id: "codex_cli" | "claude_code_cli" | string;
  execution_scope: string;
  allowed_content_classes: string[];
  launch_mode: string;
};

export type AgentRegistry = {
  status: string;
  registry_version: string;
  content_classes: string[];
  task_kinds: AgentTaskKindDefinition[];
  executors: AgentExecutorDefinition[];
  embedded_agent_runtime: boolean;
  workspace_policy: {
    registry_version: string;
    allowed_content_classes: string[];
    execution_scope: string;
    max_prompt_bytes: number;
    max_result_bytes: number;
  } | null;
};

export type AgentTaskProjection = {
  task_id: string;
  state_id: string;
  state_digest: string;
  revision: number;
  task_kind: AgentTaskKind | string;
  result_contract: string;
  executor_id: string;
  execution_scope: string;
  effective_content_classes: string[];
  input_basis_digest: string;
  paper_id: string | null;
  paper_ids?: string[];
  job_id: string | null;
  query_type?: KnowledgeQueryType;
  question_id?: string;
  candidate_type?: ResearchSynthesisCandidateType;
  maintenance_intent?: ResearchSynthesisMaintenanceIntent;
  target_candidate_id?: string | null;
  retention_class?: string;
  lineage: { [key: string]: JsonValue } | null;
  status: string;
  terminal_receipt: boolean;
  created_at: string;
  updated_at: string;
};

export type AgentTaskList = {
  status: string;
  tasks: AgentTaskProjection[];
  next_cursor: string | null;
};

export type AgentTaskDetail = {
  status: string;
  current_task: AgentTaskProjection;
  history: AgentTaskProjection[];
};

export type AgentExpectedState = {
  expected_state_id: string;
  expected_state_digest: string;
};

export type AgentTaskCreateRequest = {
  paper_id: string;
  task_kind: AgentTaskKind;
  executor_id: "codex_cli" | "claude_code_cli";
  approved_content_classes: string[];
  idempotency_key: string;
};

export type KnowledgeQueryCreateRequest = {
  query_type: KnowledgeQueryType;
  query_text: string;
  paper_ids: string[];
  include_review_background: boolean;
  include_routing_context: boolean;
  executor_id: "codex_cli" | "claude_code_cli";
  approved_content_classes: string[];
  idempotency_key: string;
};

export type OrganizationTargetKind = "direction" | "field_map_entry" | "question";

export type OrganizationTargetSummary = { [key: string]: JsonValue };

export type OrganizationTargetList = {
  status: string;
  directions?: OrganizationTargetSummary[];
  field_map_entries?: OrganizationTargetSummary[];
  questions?: OrganizationTargetSummary[];
  next_cursor: string | null;
  persistent_writes: number;
};

export type OrganizationTargetDetail = {
  status: string;
  direction?: OrganizationTargetSummary;
  field_map_entry?: OrganizationTargetSummary;
  question?: OrganizationTargetSummary;
  persistent_writes: number;
};

export type OrganizationPaperContext = {
  status: string;
  paper_id: string;
  [key: string]: JsonValue;
};

export type OrganizationProposalCreateRequest = {
  target_kind: OrganizationTargetKind;
  target_id: string | null;
  proposal_goal: string;
  paper_ids: string[];
  include_review_background: boolean;
  executor_id: "codex_cli" | "claude_code_cli";
  approved_content_classes: string[];
  idempotency_key: string;
};

export type ResearchSynthesisCandidateType = "synthesis" | "review_angle" | "insight" | "cross_view";
export type ResearchSynthesisMaintenanceIntent = "append" | "replace";

export type ResearchSynthesisCandidateSummary = {
  candidate_id: string;
  candidate_type: ResearchSynthesisCandidateType;
  question_id: string;
  title: string;
  candidate_status: string;
  not_fact: boolean;
  review_status: string;
  automation_status: string;
  freshness: { state: "current" | "stale" | string; reasons: string[]; [key: string]: JsonValue };
  updated_at: string;
  [key: string]: JsonValue;
};

export type ResearchSynthesisCandidateList = {
  status: string;
  candidates: ResearchSynthesisCandidateSummary[];
  next_cursor: string | null;
  persistent_writes: number;
};

export type ResearchSynthesisCandidateDetail = {
  status: string;
  candidate: { [key: string]: JsonValue };
  persistent_writes: number;
};

export type ResearchSynthesisQuestionContext = {
  status: string;
  question: { question_id: string; question_text: string; [key: string]: JsonValue };
  candidate_counts: Record<ResearchSynthesisCandidateType, number>;
  candidate_count: number;
  stale_candidate_count: number;
  persistent_writes: number;
};

export type ResearchSynthesisLimits = {
  status: string;
  max_page_size: number;
  candidate_types: ResearchSynthesisCandidateType[];
  maintenance_intents: ResearchSynthesisMaintenanceIntent[];
};

export type ResearchSynthesisProposalCreateRequest = {
  question_id: string;
  candidate_type: ResearchSynthesisCandidateType;
  maintenance_intent: ResearchSynthesisMaintenanceIntent;
  target_candidate_id: string | null;
  maintenance_goal: string;
  include_review_background: boolean;
  executor_id: "codex_cli" | "claude_code_cli";
  approved_content_classes: string[];
  idempotency_key: string;
};

export type ObsidianOptionalTable = "library_summary" | "question_coverage";

export type ObsidianViewEntry = {
  logical_path: string;
  view_kind: string;
  view_id: string;
  freshness: "current" | "stale_upstream" | string;
  freshness_reasons: string[];
  rendered_at: string | null;
};

export type ObsidianStatus = {
  status: string;
  projection_state: "missing" | "ready" | string;
  integrity_state: "intact" | "edited_managed_file" | string;
  generation_id: string | null;
  optional_tables: ObsidianOptionalTable[];
  file_count: number;
  current_count: number;
  stale_count: number;
  edited_paths: string[];
  edited_paths_truncated: boolean;
  entries: ObsidianViewEntry[];
  next_cursor: string | null;
  persistent_writes: number;
  canonical_scientific_write: false;
};

export type ObsidianTarget = { target_id: string; label: string };

export type ObsidianTargets = {
  status: string;
  targets: ObsidianTarget[];
  preview_ttl_seconds: number;
  persistent_writes: number;
  canonical_scientific_write: false;
};

export type ObsidianRenderPreview = {
  status: string;
  projection_state: string;
  integrity_state: string;
  generation_id: string | null;
  optional_tables: ObsidianOptionalTable[];
  proposed_file_count: number;
  changed_file_count: number;
  removed_file_count: number;
  changed_paths: string[];
  changed_paths_truncated: boolean;
  removed_paths: string[];
  removed_paths_truncated: boolean;
  edited_paths: string[];
  edited_paths_truncated: boolean;
  preview_token: string;
  preview_ttl_seconds: number;
  persistent_writes: number;
  canonical_scientific_write: false;
};

export type ObsidianRenderResult = {
  status: string;
  result: "committed" | "no_change" | string;
  generation_id: string | null;
  file_count: number;
  changed_file_count: number;
  removed_file_count: number;
  persistent_writes: number;
  canonical_scientific_write: false;
};

export type ObsidianSyncPreview = {
  status: string;
  target_id: string;
  target_label: string;
  source_generation_id: string;
  source_file_count: number;
  source_byte_count: number;
  destination_state: "missing" | "empty" | "current" | "edited" | "collision" | string;
  create_count: number;
  update_count: number;
  no_change_count: number;
  remove_count: number;
  edited_count: number;
  missing_count: number;
  unknown_count: number;
  collision_count: number;
  changed_paths: string[];
  changed_paths_truncated: boolean;
  conflict_paths: string[];
  conflict_paths_truncated: boolean;
  preview_token: string;
  preview_ttl_seconds: number;
  persistent_writes: number;
  canonical_scientific_write: false;
};

export type ObsidianSyncContinuation =
  | "sync"
  | "discard_managed_edits"
  | "export_personal_copy_then_sync";

export type ObsidianSyncResult = {
  status: string;
  result: "committed" | "no_change" | string;
  target_id: string;
  source_generation_id: string;
  file_count: number;
  byte_count: number;
  continuation: ObsidianSyncContinuation;
  personal_copy: null | {
    export_id: string;
    file_count: number;
    missing_count: number;
    receipt_digest: string;
  };
  persistent_writes: number;
  canonical_scientific_write: false;
};

export type ScreeningCriterion = { criterion_id: string; text: string };
export type ScreeningCriterionInput = { criterion_id?: string; text: string };
export type ScreeningDisposition = {
  criterion_id: string;
  disposition: "met" | "not_met" | "not_applicable" | "uncertain";
  rationale: string;
};
export type ScreeningCriteria = {
  criteria_id: string;
  question_id: string;
  title: string;
  scope: string;
  inclusion_criteria: ScreeningCriterion[];
  exclusion_criteria: ScreeningCriterion[];
  notes: string;
  status: "active" | "archived";
  revision_id: string;
  criteria_digest: string;
};
export type ScreeningDecision = {
  decision_id: string;
  question_id: string;
  paper_id: string;
  outcome: "included" | "excluded";
  criteria_revision_id: string;
  criteria_digest: string;
  criterion_dispositions: ScreeningDisposition[];
  basis_scope: "metadata" | "available_abstract" | "paper_card" | "user_full_text_review" | "mixed";
  rationale: string;
  known_limitations: string[];
  revision_id: string;
  freshness?: { state: string; [key: string]: JsonValue };
};
export type ScreeningCriteriaList = { status: string; criteria: ScreeningCriteria[]; next_cursor: string | null; persistent_writes: number };
export type ScreeningDecisionList = { status: string; decisions: ScreeningDecision[]; next_cursor: string | null; persistent_writes: number };
export type ScreeningCriteriaPromoteRequest = {
  criteria_id?: string;
  question_id: string;
  title: string;
  scope: string;
  inclusion_criteria: ScreeningCriterionInput[];
  exclusion_criteria: ScreeningCriterionInput[];
  notes: string;
  status: "active" | "archived";
  expected_revision_id?: string;
};
export type ScreeningDecisionPromoteRequest = {
  decision_id?: string;
  question_id: string;
  paper_id: string;
  outcome: "included" | "excluded";
  criteria_revision_id: string;
  criteria_digest: string;
  criterion_dispositions: ScreeningDisposition[];
  basis_scope: ScreeningDecision["basis_scope"];
  rationale: string;
  known_limitations: string[];
  expected_revision_id?: string;
};
export type ScreeningMutationResult = {
  status: string;
  result: "committed" | "no_change";
  criteria?: ScreeningCriteria;
  decision?: ScreeningDecision;
  persistent_writes: number;
  canonical_scientific_write: boolean;
  operation?: OperationStatus;
};
export type ScreeningCriteriaProposalCreateRequest = {
  question_id: string;
  criteria_id: string | null;
  proposal_goal: string;
  executor_id: "codex_cli" | "claude_code_cli";
  approved_content_classes: string[];
  idempotency_key: string;
};
export type ScreeningDecisionProposalCreateRequest = {
  question_id: string;
  paper_id: string;
  basis_scope: "metadata" | "paper_card" | "mixed";
  include_paper_card: boolean;
  executor_id: "codex_cli" | "claude_code_cli";
  approved_content_classes: string[];
  idempotency_key: string;
};

export type AgentHandoffPreview = {
  manifest_version: string;
  executor_id: string;
  result_contract: string;
  effective_content_classes: string[];
  payload: { [key: string]: JsonValue };
  payload_digest: string;
  prompt_bytes: number;
};

export type AgentHandoff = {
  manifest_version: string;
  task_id: string;
  task_kind: string;
  executor_id: string;
  result_contract: string;
  result_contract_schema: { [key: string]: JsonValue };
  input_basis_digest: string;
  effective_content_classes: string[];
  payload: { [key: string]: JsonValue };
  prompt: string;
};

export type AgentMutationResult = {
  status: string;
  task: AgentTaskProjection;
  persistent_writes: number;
  canonical_scientific_write: boolean;
  operation?: OperationStatus;
  successor_task?: AgentTaskProjection;
  review_bundle?: { [key: string]: JsonValue };
  primary_bundle?: { [key: string]: JsonValue };
  pipeline?: { [key: string]: JsonValue };
  screening?: { [key: string]: JsonValue };
  research_synthesis?: { [key: string]: JsonValue };
  source_adequacy?: {
    requested_operation: string;
    required_capability: string;
    freshness: string;
    capability_status: string | null;
    pipeline_status: string | null;
    wait_reason: string | null;
  };
  resolution?: {
    resolution_state: SourceAdequacyResolutionContext["resolution_state"];
    decision_action: "accept_uncertainty" | "remediation_required";
    successor_profile_id: string;
    persistent_writes: number;
  };
};

export type AgentInspectResult = AgentMutationResult & {
  handoff_preview: AgentHandoffPreview;
};

export type AgentHandoffResult = AgentMutationResult & {
  handoff: AgentHandoff;
};

export type AgentPreviewResult = {
  status: string;
  task: AgentTaskProjection;
  candidate: { [key: string]: JsonValue };
};

export type CatalogItem = {
  item_id: string;
  item_kind: string;
  authority_layer: "canonical" | "operational" | string;
  record_kind: string;
  record_id: string;
  child_id: string | null;
  paper_id: string | null;
  question_id: string | null;
  title: string;
  summary: string;
  status_labels: string[];
  sort_key: string;
  source_record_digest: string;
  adapter_version: string;
  tags: Array<{ tag_id: string; name: string }>;
};

export type TagStatus = "active" | "archived";
export type TagTargetKind = "paper" | "direction" | "field_map_entry" | "question";
export type TagAssignmentState = "assigned" | "removed";

export type Tag = {
  tag_id: string;
  name: string;
  normalized_name: string;
  description: string;
  aliases: string[];
  status: TagStatus;
  revision_id: string;
  assignment_count?: number;
};

export type TagAssignment = {
  tag_link_id: string;
  tag_id: string;
  target_kind: TagTargetKind;
  target_id: string;
  state: TagAssignmentState;
  revision_id: string;
  target_availability?: string;
};

export type TagList = {
  status: string;
  tags: Tag[];
  next_cursor: string | null;
  persistent_writes: number;
  canonical_scientific_write: boolean;
};

export type TagDetail = {
  status: string;
  tag: Tag;
  assignments: TagAssignment[];
  persistent_writes: number;
  canonical_scientific_write: boolean;
};

export type TargetTags = {
  status: string;
  target_kind: TagTargetKind;
  target_id: string;
  tags: Tag[];
  persistent_writes: number;
  canonical_scientific_write: boolean;
};

export type TagPromoteRequest = {
  tag_id?: string;
  name?: string;
  description?: string;
  aliases?: string[];
  status?: TagStatus;
  expected_revision_id?: string;
};

export type TagMutationResult = {
  status: string;
  result: "committed" | "no_change";
  tag: Tag;
  persistent_writes: number;
  canonical_scientific_write: boolean;
  operation?: OperationStatus;
};

export type TagAssignmentRequest = {
  tag_id: string;
  target_kind: TagTargetKind;
  target_id: string;
  state: TagAssignmentState;
  expected_revision_id?: string;
};

export type TagAssignmentMutationResult = {
  status: string;
  result: "committed" | "no_change";
  assignment: TagAssignment | null;
  persistent_writes: number;
  canonical_scientific_write: boolean;
  operation?: OperationStatus;
};

export type CatalogSearchResult = {
  status: string;
  query: string;
  item_kinds: string[];
  page_size: number;
  items: CatalogItem[];
  next_cursor: string | null;
  has_more: boolean;
  projection_state: string;
  source_watermark: string;
};

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export type CatalogDetail = {
  status: string;
  projection_state: string;
  current_record_status: "current" | "changed" | "missing" | string;
  item: CatalogItem;
  detail: { [key: string]: JsonValue } | null;
};

export type ReadingBibliography = {
  title: string;
  authors: string[];
  year: number | null;
  doi: string | null;
};

export type ReadingSourcePage = {
  pdf_page: number;
  printed_page: string | null;
  section: string | null;
  figure_or_table: string | null;
};

export type ReadingSourceState = {
  source_availability: string;
  source_currentness: string;
  trace_back_available: boolean;
};

export type ReadingParseState = {
  bound_parse_run_id: string | null;
  materialized_parse_run_id: string | null;
  binding_state: string;
  materialized_page_count: number;
  materialized_parser: { adapter: string; version: string } | null;
};

export type ReadingAdequacy = {
  requested_operation: string;
  freshness: string;
  capability_status: string | null;
};

export type PaperCardUnit = {
  unit_id: string;
  section_id: string;
  statement: string;
  statement_type: string;
  grounding_status: string;
  evidence_ids: string[];
  boundary_refs: string[];
  source_page: ReadingSourcePage | null;
  confidence: string;
};

export type PaperCardSection = {
  section_id: string;
  units: PaperCardUnit[];
};

export type UnitAdmissibility = {
  unit_id: string;
  section_id: string;
  grounding_status: string;
  factual_support_eligible: boolean;
  evidence_ids: string[];
  boundary_refs: string[];
};

export type ReviewSourceNote = ReadingSourcePage & {
  note_type: string;
  text: string;
  locator: string | null;
  reopen_priority: string;
};

export type ReviewUnit = {
  review_unit_id: string;
  section_id: string;
  unit_type: string;
  content: string;
  source_notes: ReviewSourceNote[];
  workflow_impacts: Array<{ target: string; action: string }>;
  background_only: boolean;
  can_enter_canonical_evidence: boolean;
  not_fact: boolean;
};

export type ReviewSection = {
  section_id: string;
  units: ReviewUnit[];
};

export type ReadingQuestionContext = {
  question_id: string;
  question_text: string;
  scope: string;
  mapping_status: string;
  freshness: string;
  link: { [key: string]: JsonValue };
};

export type ReadingPaper = {
  status: string;
  interface_version: string;
  application_service_interface_version: string;
  paper: {
    paper_id: string;
    bibliography: ReadingBibliography;
    screening_status: string;
    review_status: string;
    automation_status: string;
    created_at: string;
    updated_at: string;
  };
  document_route: "primary" | "review" | "unprocessed" | string;
  primary: {
    authority_mode: string;
    revision_id: string | null;
    revision_number: number | null;
    revision_status: string;
    paper_card: { sections: PaperCardSection[] };
    unit_admissibility: UnitAdmissibility[];
  } | null;
  review: {
    authority_mode: string;
    revision_id: string | null;
    revision_number: number | null;
    revision_status: string;
    review_memory: {
      background_only: boolean;
      can_enter_canonical_evidence: boolean;
      memory_value: { status: string; reason: string };
      coverage_limits: {
        unread_sections: string[];
        weakly_read_sections: string[];
        reason: string;
      };
      sections: ReviewSection[];
      non_reusable_notes?: Array<{ content: string; reason: string }>;
    };
    factual_support_eligible: boolean;
  } | null;
  source: ReadingSourceState;
  parse: ReadingParseState;
  adequacy: ReadingAdequacy[];
  questions: ReadingQuestionContext[];
  persistent_writes: number;
  canonical_scientific_write: boolean;
};

export type ReadingComparison = {
  status: string;
  interface_version: string;
  application_service_interface_version: string;
  papers: ReadingPaper[];
  semantic_comparison: null;
  persistent_writes: number;
  canonical_scientific_write: boolean;
};

export type EvidenceTrace = {
  status: string;
  interface_version: string;
  application_service_interface_version: string;
  evidence: {
    evidence_id: string;
    paper_id: string;
    claim: string;
    evidence_type: string;
    quote: string;
    source_page: ReadingSourcePage;
    locator: string;
    support_scope: string;
    what_it_does_not_support: string[];
    review_status: string;
    automation_status: string;
    created_at: string;
    updated_at: string;
  };
  primary_revision: {
    authority_mode: string;
    revision_id: string | null;
    revision_number: number | null;
    revision_status: string;
  };
  source: ReadingSourceState;
  parse: ReadingParseState;
  factual_support_eligible: boolean;
  persistent_writes: number;
  canonical_scientific_write: boolean;
};

export type EvidencePdfHandle = {
  status: "success";
  handle_id: string;
  evidence_id: string;
  pdf_page: number;
  expires_in_seconds: number;
};

export type ExternalPdfReaderResult = {
  status: "success";
  reader: "updf" | "system";
  page_targeting: "manual";
  pdf_page: number;
  locator: string;
};

export type CapabilityResult = {
  status: string;
  app: { [key: string]: JsonValue };
  core: { [key: string]: JsonValue };
  catalog: { [key: string]: JsonValue };
};

export type HealthResult = {
  status: string;
  process_ready: boolean;
  core_compatible: boolean;
  workspace_selected: boolean;
  projection_state: string;
  operation: OperationStatus;
};

export type CatalogSearchOptions = {
  query?: string;
  itemKinds?: string[];
  paperId?: string;
  questionId?: string;
  tagId?: string;
  pageSize?: number;
  cursor?: string | null;
};

export type ExchangeScope = "paper" | "question" | "direction" | "workspace";

export type ExchangeCapabilities = {
  status: string;
  bundle_format: string;
  selectors: ExchangeScope[];
  source_inclusion_available: boolean;
  import_available: boolean;
  safe_reader_profile: { profile_id: string; max_archive_bytes: number; [key: string]: JsonValue };
  browser_paths_accepted: false;
  external_records_are_local_facts: false;
  lease_ttl_seconds: number;
};

export type ExchangeExportPreview = {
  status: string;
  bundle_format: string;
  selection: { scope: ExchangeScope; selector_id?: string };
  record_count: number;
  record_kind_counts: Record<string, number>;
  structured_bytes: number;
  estimated_archive_bytes: number;
  source_count: number;
  pdf_count: number;
  missing_source_count: number;
  rights_status: string;
  preview_token: string;
  preview_ttl_seconds: number;
};

export type ExchangeExportBuild = {
  status: string;
  result: string;
  export_id: string;
  selection: { scope: ExchangeScope; selector_id?: string };
  record_count: number;
  source_count: number;
  archive_sha256: string;
  archive_bytes: number;
  download_token: string;
  download_filename: string;
  download_ttl_seconds: number;
};

export type ExchangeImportPreview = {
  status: string;
  compatibility: "supported" | "newer_but_safe_read_only" | "migration_required" | "unknown_or_incompatible";
  safe_reader_profile_id: string;
  archive_bytes: number;
  canonical_serialization: boolean;
  import_id: string | null;
  existing_import_id: string | null;
  origin_workspace_id: string | null;
  selection: { scope: ExchangeScope; selector_id?: string } | null;
  record_count: number;
  record_kind_counts: Record<string, number>;
  source_count: number;
  include_sources: boolean;
  rights_assertion: string | null;
  trust_projection: string | null;
  conflict_counts: Record<string, number>;
  conflicts: Array<{
    origin_workspace_id: string;
    origin_record_id: string;
    record_kind: string;
    revision_digest: string;
    classification: string;
    local_record_id: string | null;
    local_admissibility: string;
  }>;
  conflicts_truncated: boolean;
  preview_token: string | null;
  preview_ttl_seconds: number;
};

export type ExchangeImportReceipt = {
  import_id: string;
  local_workspace_id: string;
  origin_workspace_id: string;
  export_id: string;
  record_count: number;
  source_count: number;
  conflict_counts: Record<string, number>;
  local_review_status: "unreviewed";
  trust_projection: "unsigned_external_claims";
  created_at: string;
};

export type ExchangeImports = {
  status: string;
  imports: ExchangeImportReceipt[];
};

export type ExchangeImportDetail = {
  status: string;
  import: ExchangeImportReceipt;
  selection: { scope: ExchangeScope; selector_id?: string };
  record_kind_counts: Record<string, number>;
  include_sources: boolean;
  rights_assertion: string | null;
  records: Array<{
    origin_workspace_id: string;
    origin_record_id: string;
    record_kind: string;
    revision_digest: string;
    label: string;
    local_admissibility: "external_unreviewed";
    trust_projection: "unsigned_external_claims";
  }>;
  records_truncated: boolean;
};

export type SetupMode = "explicit_config" | "first_run" | "ready" | "recovery";

export type SetupStatus = {
  status: "success";
  interface_version: string;
  mode: SetupMode;
  profile_id?: string;
  current_revision_id?: string | null;
  recovery_available: boolean;
};

export type SetupFolderPurpose =
  | "workspace_parent"
  | "existing_workspace_config"
  | "source_root"
  | "local_inbox"
  | "obsidian_vault"
  | "task_package_destination"
  | "backup_destination";

export type SetupSelection = {
  lease_id: string;
  purpose: SetupFolderPurpose;
  display_label: string;
  capability_facts: {
    filesystem?: string;
    local?: boolean;
    reparse_free?: boolean;
    acl_secure?: boolean;
    accepted?: boolean;
  };
  expires_in_seconds: number;
};

export type SetupFolderResult = {
  status: "success" | "cancelled";
  interface_version: string;
  selection?: SetupSelection;
};

export type WorkspaceSetupPreview = {
  status: "success";
  interface_version: string;
  proposal_token: string;
  preview_digest: string;
  preview: {
    workspace_label: string;
    workspace_name: string;
    source_root_ids: string[];
    external_source_root_count: number;
    local_inbox: string;
    expires_at: string;
  };
};

export type WorkspaceSetupResult = {
  status: "success";
  interface_version: string;
  workspace_id: string;
  profile_revision_id: string;
  restart_required: boolean;
  result?: string;
};

export type WorkspaceAdoptionPreview = {
  status: "success";
  interface_version: string;
  adoption_token: string;
  preview_digest: string;
  preview: { workspace_id: string; action: "add_profile_reference_only" };
};

export type SetupRecovery = {
  status: "success";
  interface_version: string;
  profile_state: string;
  current_revision_id: string | null;
  recoverable_revision_ids: string[];
  workspace_setup_operations: Array<{
    operation_id: string;
    workspace_label: string;
    state: string;
    actions: Array<"resume_workspace_setup" | "discard_workspace_staging" | "restart_workspace_setup">;
  }>;
};

export type EgressPolicy = {
  status: "success";
  policy_id: string;
  routes: Record<string, string>;
  clipboard: { policy_id: string; history: "enabled" | "disabled" | "unknown"; cloud_sync: "enabled" | "disabled" | "unknown" };
};

export type ClipboardCopyResult = {
  status: "success";
  route: "clipboard";
  content_sha256: string;
  timed_clear_scheduled: boolean;
  timed_clear_completed: boolean;
  timed_clear_status: string;
  clipboard: EgressPolicy["clipboard"];
};

export type ClipboardCopyRequest =
  | {
      action: "agent_handoff";
      task_id: string;
      expected_state_id: string;
      expected_state_digest: string;
      executor_id: "codex_cli" | "claude_code_cli";
    }
  | {
      action: "knowledge_query_answer";
      task_id: string;
      expected_state_id: string;
      expected_state_digest: string;
    }
  | {
      action: "metadata_only";
      task_id: string;
      expected_state_id: string;
      expected_state_digest: string;
      metadata_disclosure_accepted: boolean;
    };

export type AgentTaskPackageExportResult = {
  status: "success";
  route: "local_agent_package";
  filename: string;
  content_sha256: string;
  content_utf8_bytes: number;
};

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string,
  ) {
    super(message);
  }
}

let csrfToken: string | null = null;

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.method === "POST") {
    if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    if (csrfToken) headers.set("X-RKB-CSRF", csrfToken);
  }
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers,
  });
  const payload = (await response.json()) as Record<string, unknown>;
  if (!response.ok) {
    const diagnostic = (payload.diagnostic ?? {}) as Record<string, unknown>;
    throw new ApiError(
      typeof diagnostic.message === "string" ? diagnostic.message : "请求失败",
      response.status,
      typeof diagnostic.code === "string" ? diagnostic.code : "RKBAPP-UNKNOWN",
    );
  }
  return payload as T;
}

export async function bootstrap(startupToken: string): Promise<void> {
  await request<{ status: string }>("/api/session/bootstrap", {
    method: "POST",
    body: JSON.stringify({ startup_token: startupToken }),
  });
  const result = await request<{ csrf_token: string }>("/api/session/csrf");
  csrfToken = result.csrf_token;
}

export function getSetupStatus(): Promise<SetupStatus> {
  return request<SetupStatus>("/api/setup/status");
}

export function selectSetupFolder(
  purpose: SetupFolderPurpose,
  options: { allowNewChild?: boolean; initialLocationId?: "home" | "documents" | "local_app_data" } = {},
): Promise<SetupFolderResult> {
  return request<SetupFolderResult>("/api/setup/select-folder", {
    method: "POST",
    body: JSON.stringify({
      purpose,
      allow_new_child: options.allowNewChild ?? false,
      initial_location_id: options.initialLocationId ?? null,
    }),
  });
}

export function prepareWorkspaceSetup(payload: {
  workspace_parent_lease_id: string;
  source_roots: Array<{ root_id: string; selection_lease_id: string }>;
  local_inbox_lease_id: string;
  workspace_name: string;
  workspace_label: string;
  idempotency_key: string;
}): Promise<WorkspaceSetupPreview> {
  return request<WorkspaceSetupPreview>("/api/setup/prepare-workspace", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function commitWorkspaceSetup(
  proposalToken: string,
  previewDigest: string,
): Promise<WorkspaceSetupResult> {
  return request<WorkspaceSetupResult>("/api/setup/commit-workspace", {
    method: "POST",
    body: JSON.stringify({ proposal_token: proposalToken, preview_digest: previewDigest }),
  });
}

export function previewWorkspaceAdoption(selectionLeaseId: string): Promise<WorkspaceAdoptionPreview> {
  return request<WorkspaceAdoptionPreview>("/api/setup/adopt-workspace", {
    method: "POST",
    body: JSON.stringify({ action: "preview", selection_lease_id: selectionLeaseId }),
  });
}

export function commitWorkspaceAdoption(
  adoptionToken: string,
  previewDigest: string,
  label: string,
): Promise<WorkspaceSetupResult> {
  return request<WorkspaceSetupResult>("/api/setup/adopt-workspace", {
    method: "POST",
    body: JSON.stringify({
      action: "commit",
      adoption_token: adoptionToken,
      preview_digest: previewDigest,
      label,
    }),
  });
}

export function getSetupRecovery(): Promise<SetupRecovery> {
  return request<SetupRecovery>("/api/setup/recovery");
}

export function runSetupRecoveryAction(payload: {
  action: "select_profile_revision" | "resume_workspace_setup" | "discard_workspace_staging" | "restart_workspace_setup";
  revision_id?: string;
  operation_id?: string;
}): Promise<WorkspaceSetupResult> {
  return request<WorkspaceSetupResult>("/api/setup/recovery/action", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getEgressPolicy(): Promise<EgressPolicy> {
  return request<EgressPolicy>("/api/egress/policy");
}

export function copyToClipboard(payload: ClipboardCopyRequest): Promise<ClipboardCopyResult> {
  return request<ClipboardCopyResult>("/api/egress/clipboard", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function exportAgentTaskPackage(
  taskId: string,
  payload: AgentExpectedState & {
    executor_id: "codex_cli" | "claude_code_cli";
    selection_lease_id: string;
  },
): Promise<AgentTaskPackageExportResult> {
  return request<AgentTaskPackageExportResult>(
    `/api/egress/agent-task-package/${encodeURIComponent(taskId)}`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export async function listWorkspaces(): Promise<WorkspaceOption[]> {
  const result = await request<{ workspaces: WorkspaceOption[] }>("/api/workspaces");
  return result.workspaces;
}

export async function openWorkspace(optionId: string): Promise<CatalogStatus> {
  const result = await request<{ catalog: CatalogStatus }>("/api/workspaces/open", {
    method: "POST",
    body: JSON.stringify({ option_id: optionId }),
  });
  return result.catalog;
}

export function getCatalogStatus(): Promise<CatalogStatus> {
  return request<CatalogStatus>("/api/catalog/status");
}

export async function rebuildCatalog(): Promise<void> {
  await request<{ status: string }>("/api/catalog/rebuild", {
    method: "POST",
    body: "{}",
  });
}

export function getExchangeCapabilities(): Promise<ExchangeCapabilities> {
  return request<ExchangeCapabilities>("/api/exchange/capabilities");
}

export function previewExchangeExport(payload: {
  scope: ExchangeScope;
  selector_id: string | null;
  include_sources: boolean;
  rights_asserted: boolean;
}): Promise<ExchangeExportPreview> {
  return request<ExchangeExportPreview>("/api/exchange/export/preview", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function buildExchangeExport(previewToken: string): Promise<ExchangeExportBuild> {
  return request<ExchangeExportBuild>("/api/exchange/export/build", {
    method: "POST",
    body: JSON.stringify({ preview_token: previewToken }),
  });
}

export function exchangeDownloadUrl(downloadToken: string): string {
  return `/api/exchange/export/download/${encodeURIComponent(downloadToken)}`;
}

export function uploadExchangeImport(file: File): Promise<{
  status: string;
  upload_token: string;
  archive_bytes: number;
  upload_ttl_seconds: number;
}> {
  return request("/api/exchange/import/upload", {
    method: "POST",
    headers: { "Content-Type": "application/vnd.research-kb.exchange+zip" },
    body: file,
  });
}

export function previewExchangeImport(uploadToken: string): Promise<ExchangeImportPreview> {
  return request<ExchangeImportPreview>("/api/exchange/import/preview", {
    method: "POST",
    body: JSON.stringify({ upload_token: uploadToken }),
  });
}

export function applyExchangeImport(previewToken: string): Promise<{
  status: string;
  result: "imported" | "no_change";
  import_id: string;
  origin_workspace_id: string;
  record_count: number;
  source_count: number;
  trust_projection: "unsigned_external_claims";
  canonical_scientific_write: false;
}> {
  return request("/api/exchange/import/apply", {
    method: "POST",
    body: JSON.stringify({ preview_token: previewToken }),
  });
}

export function listExchangeImports(): Promise<ExchangeImports> {
  return request<ExchangeImports>("/api/exchange/imports");
}

export function getExchangeImport(importId: string): Promise<ExchangeImportDetail> {
  return request<ExchangeImportDetail>(`/api/exchange/imports/${encodeURIComponent(importId)}`);
}

export function searchDiscovery(payload: DiscoverySearchRequest): Promise<DiscoveryReport> {
  return request<DiscoveryReport>("/api/discovery/search", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function selectDiscovery(report: DiscoveryReport, resultKeys: string[]): Promise<void> {
  return request("/api/discovery/select", {
    method: "POST",
    body: JSON.stringify({ report, result_keys: resultKeys }),
  });
}

export function listDiscoveryCandidates(pageSize = 25, cursor?: string | null): Promise<DiscoveryCandidateList> {
  const params = new URLSearchParams({ page_size: String(pageSize) });
  if (cursor) params.set("cursor", cursor);
  return request<DiscoveryCandidateList>(`/api/discovery/candidates?${params.toString()}`);
}

export function resolveDiscoveryCandidate(candidateId: string): Promise<DiscoveryResolution> {
  return request<DiscoveryResolution>(`/api/discovery/candidates/${encodeURIComponent(candidateId)}/resolve`, {
    method: "POST",
    body: "{}",
  });
}

export function acquireDiscoveryCandidate(candidateId: string): Promise<void> {
  return request(`/api/discovery/candidates/${encodeURIComponent(candidateId)}/acquire`, {
    method: "POST",
    body: "{}",
  });
}

export function getAcquiredCandidateHandoff(candidateId: string): Promise<AcquiredCandidateHandoff> {
  return request<AcquiredCandidateHandoff>(`/api/discovery/candidates/${encodeURIComponent(candidateId)}/intake-handoff`);
}

export function listCatalogItems(options: CatalogSearchOptions = {}): Promise<CatalogSearchResult> {
  const search = new URLSearchParams();
  if (options.query) search.set("query", options.query);
  for (const itemKind of options.itemKinds ?? []) search.append("item_kinds", itemKind);
  if (options.paperId) search.set("paper_id", options.paperId);
  if (options.questionId) search.set("question_id", options.questionId);
  if (options.tagId) search.set("tag_id", options.tagId);
  search.set("page_size", String(options.pageSize ?? 20));
  if (options.cursor) search.set("cursor", options.cursor);
  return request<CatalogSearchResult>(`/api/catalog/items?${search.toString()}`);
}

export function getCatalogItem(itemId: string): Promise<CatalogDetail> {
  return request<CatalogDetail>(`/api/catalog/items/${encodeURIComponent(itemId)}`);
}

export function listTags(
  includeArchived = false,
  pageSize = 50,
  cursor?: string | null,
): Promise<TagList> {
  const search = new URLSearchParams({
    include_archived: String(includeArchived),
    page_size: String(pageSize),
  });
  if (cursor) search.set("cursor", cursor);
  return request<TagList>(`/api/tags?${search.toString()}`);
}

export function getTag(tagId: string): Promise<TagDetail> {
  return request<TagDetail>(`/api/tags/${encodeURIComponent(tagId)}`);
}

export function listTargetTags(targetKind: TagTargetKind, targetId: string): Promise<TargetTags> {
  return request<TargetTags>(`/api/tag-targets/${targetKind}/${encodeURIComponent(targetId)}`);
}

export function promoteTag(payload: TagPromoteRequest): Promise<TagMutationResult> {
  return request<TagMutationResult>("/api/tags/promote", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function setTagAssignment(payload: TagAssignmentRequest): Promise<TagAssignmentMutationResult> {
  return request<TagAssignmentMutationResult>("/api/tag-assignments", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getReadingPaper(paperId: string): Promise<ReadingPaper> {
  return request<ReadingPaper>(`/api/reading/papers/${encodeURIComponent(paperId)}`);
}

export function compareReadingPapers(paperIds: string[]): Promise<ReadingComparison> {
  return request<ReadingComparison>("/api/reading/compare", {
    method: "POST",
    body: JSON.stringify({ paper_ids: paperIds }),
  });
}

export function getEvidenceTrace(evidenceId: string): Promise<EvidenceTrace> {
  return request<EvidenceTrace>(`/api/reading/evidence/${encodeURIComponent(evidenceId)}`);
}

export function createEvidencePdfHandle(evidenceId: string): Promise<EvidencePdfHandle> {
  return request<EvidencePdfHandle>(`/api/reading/evidence/${encodeURIComponent(evidenceId)}/source-handle`, {
    method: "POST",
    body: "{}",
  });
}

export function evidencePdfUrl(handleId: string): string {
  return `/api/reading/pdf/${encodeURIComponent(handleId)}`;
}

export function openEvidencePdfExternally(handleId: string): Promise<ExternalPdfReaderResult> {
  return request<ExternalPdfReaderResult>(`/api/reading/pdf/${encodeURIComponent(handleId)}/open`, {
    method: "POST",
    body: "{}",
  });
}

export function getCapabilities(): Promise<CapabilityResult> {
  return request<CapabilityResult>("/api/capabilities");
}

export function getHealth(): Promise<HealthResult> {
  return request<HealthResult>("/api/health");
}

export function listInboxCandidates(
  maxEntries = 20,
  minStableAgeSeconds = 5,
): Promise<InboxScanResult> {
  const search = new URLSearchParams({
    max_entries: String(maxEntries),
    min_stable_age_seconds: String(minStableAgeSeconds),
  });
  return request<InboxScanResult>(`/api/intake/inbox?${search.toString()}`);
}

export function startUploadIntake(file: File, payload: IntakeStartRequest): Promise<IntakeAccepted> {
  const boundary = `research-kb-${crypto.randomUUID()}`;
  const body = new Blob(
    [
      `--${boundary}\r\nContent-Disposition: form-data; name="metadata"\r\nContent-Type: application/json\r\n\r\n`,
      JSON.stringify(payload),
      `\r\n--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="source.pdf"\r\nContent-Type: application/pdf\r\n\r\n`,
      file,
      `\r\n--${boundary}--\r\n`,
    ],
    { type: `multipart/form-data; boundary=${boundary}` },
  );
  return request<IntakeAccepted>("/api/intake/upload", {
    method: "POST",
    headers: { "Content-Type": body.type },
    body,
  });
}

export function startInboxIntake(payload: InboxStartRequest): Promise<IntakeAccepted> {
  return request<IntakeAccepted>("/api/intake/inbox/start", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listIntakeJobs(
  pageSize = 20,
  cursor?: string | null,
  filter?: IntakeJobListFilter,
): Promise<IntakeJobList> {
  const search = new URLSearchParams({ page_size: String(pageSize) });
  if (cursor) search.set("cursor", cursor);
  if (filter?.requested_route) search.set("requested_route", filter.requested_route);
  if (filter?.requested_depth) search.set("requested_depth", filter.requested_depth);
  return request<IntakeJobList>(`/api/intake/jobs?${search.toString()}`);
}

export function getIntakeJob(jobId: string): Promise<IntakeJobDetail> {
  return request<IntakeJobDetail>(`/api/intake/jobs/${encodeURIComponent(jobId)}`);
}

export function resumeIntakeJob(
  jobId: string,
  payload: ResumeIntakeRequest,
): Promise<IntakeAccepted> {
  return request<IntakeAccepted>(`/api/intake/jobs/${encodeURIComponent(jobId)}/resume`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function cancelIntakeJob(
  jobId: string,
  payload: CancelIntakeRequest,
): Promise<IntakeAccepted> {
  return request<IntakeAccepted>(`/api/intake/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getIntakeSourceAdequacyResolution(
  jobId: string,
): Promise<IntakeSourceAdequacyResolutionContext> {
  return request<IntakeSourceAdequacyResolutionContext>(
    `/api/intake/jobs/${encodeURIComponent(jobId)}/source-adequacy-resolution`,
  );
}

export function openIntakeSourceAdequacyReview(
  jobId: string,
  payload: AgentExpectedState,
): Promise<IntakeSourceAdequacyReviewOpenResult> {
  return request<IntakeSourceAdequacyReviewOpenResult>(
    `/api/intake/jobs/${encodeURIComponent(jobId)}/source-adequacy-resolution/open`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export function decideIntakeSourceAdequacyResolution(
  jobId: string,
  payload: AgentExpectedState & {
    action: "accept_uncertainty" | "remediation_required";
    confirmation_id?: string;
  },
): Promise<IntakeSourceAdequacyDecisionResult> {
  return request<IntakeSourceAdequacyDecisionResult>(
    `/api/intake/jobs/${encodeURIComponent(jobId)}/source-adequacy-resolution/decide`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export function prepareTrustedParse(
  jobId: string,
  payload: TrustedParsePrepareRequest,
): Promise<TrustedParsePreparation> {
  return request<TrustedParsePreparation>(
    `/api/intake/jobs/${encodeURIComponent(jobId)}/trusted-parse/prepare`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export function approveTrustedParse(
  jobId: string,
  payload: TrustedParseApproveRequest,
): Promise<IntakeAccepted> {
  return request<IntakeAccepted>(
    `/api/intake/jobs/${encodeURIComponent(jobId)}/trusted-parse/approve`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export function getAgentRegistry(): Promise<AgentRegistry> {
  return request<AgentRegistry>("/api/agent/registry");
}

export function listAgentTasks(pageSize = 20, cursor?: string | null): Promise<AgentTaskList> {
  const search = new URLSearchParams({ page_size: String(pageSize) });
  if (cursor) search.set("cursor", cursor);
  return request<AgentTaskList>(`/api/agent/tasks?${search.toString()}`);
}

export function getAgentTask(taskId: string): Promise<AgentTaskDetail> {
  return request<AgentTaskDetail>(`/api/agent/tasks/${encodeURIComponent(taskId)}`);
}

export function createAgentTask(
  jobId: string,
  payload: AgentTaskCreateRequest,
): Promise<AgentMutationResult> {
  return request<AgentMutationResult>(`/api/intake/jobs/${encodeURIComponent(jobId)}/agent-tasks`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function createKnowledgeQuery(
  payload: KnowledgeQueryCreateRequest,
): Promise<AgentMutationResult> {
  return request<AgentMutationResult>("/api/knowledge-queries", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listOrganizationTargets(
  targetKind: OrganizationTargetKind,
  pageSize = 50,
): Promise<OrganizationTargetList> {
  const path = targetKind === "direction"
    ? "directions"
    : targetKind === "field_map_entry" ? "field-map-entries" : "questions";
  return request<OrganizationTargetList>(`/api/organization/${path}?page_size=${pageSize}`);
}

export function getOrganizationTarget(
  targetKind: OrganizationTargetKind,
  targetId: string,
): Promise<OrganizationTargetDetail> {
  const path = targetKind === "direction"
    ? "directions"
    : targetKind === "field_map_entry" ? "field-map-entries" : "questions";
  return request<OrganizationTargetDetail>(`/api/organization/${path}/${encodeURIComponent(targetId)}`);
}

export function getPaperOrganizationContext(paperId: string): Promise<OrganizationPaperContext> {
  return request<OrganizationPaperContext>(`/api/organization/papers/${encodeURIComponent(paperId)}/context`);
}

export function createOrganizationProposal(
  payload: OrganizationProposalCreateRequest,
): Promise<AgentMutationResult> {
  return request<AgentMutationResult>("/api/organization/proposals", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function approveOrganizationProposal(
  taskId: string,
  payload: AgentExpectedState,
): Promise<AgentMutationResult> {
  return request<AgentMutationResult>(`/api/organization/proposals/${encodeURIComponent(taskId)}/approve`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getResearchSynthesisLimits(): Promise<ResearchSynthesisLimits> {
  return request<ResearchSynthesisLimits>("/api/research-synthesis/limits");
}

export function listResearchSynthesisCandidates(options: {
  questionId?: string;
  candidateType?: ResearchSynthesisCandidateType;
  freshness?: "current" | "stale";
  pageSize?: number;
  cursor?: string | null;
} = {}): Promise<ResearchSynthesisCandidateList> {
  const search = new URLSearchParams({ page_size: String(options.pageSize ?? 50) });
  if (options.questionId) search.set("question_id", options.questionId);
  if (options.candidateType) search.set("candidate_type", options.candidateType);
  if (options.freshness) search.set("freshness", options.freshness);
  if (options.cursor) search.set("cursor", options.cursor);
  return request<ResearchSynthesisCandidateList>(`/api/research-synthesis/candidates?${search.toString()}`);
}

export function getResearchSynthesisCandidate(
  candidateId: string,
): Promise<ResearchSynthesisCandidateDetail> {
  return request<ResearchSynthesisCandidateDetail>(
    `/api/research-synthesis/candidates/${encodeURIComponent(candidateId)}`,
  );
}

export function getResearchSynthesisQuestionContext(
  questionId: string,
): Promise<ResearchSynthesisQuestionContext> {
  return request<ResearchSynthesisQuestionContext>(
    `/api/research-synthesis/questions/${encodeURIComponent(questionId)}/context`,
  );
}

export function createResearchSynthesisProposal(
  payload: ResearchSynthesisProposalCreateRequest,
): Promise<AgentMutationResult> {
  return request<AgentMutationResult>("/api/research-synthesis/proposals", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function approveResearchSynthesisProposal(
  taskId: string,
  payload: AgentExpectedState,
): Promise<AgentMutationResult> {
  return request<AgentMutationResult>(
    `/api/research-synthesis/proposals/${encodeURIComponent(taskId)}/approve`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export function getObsidianStatus(pageSize = 20, cursor?: string | null): Promise<ObsidianStatus> {
  const search = new URLSearchParams({ page_size: String(pageSize) });
  if (cursor) search.set("cursor", cursor);
  return request<ObsidianStatus>(`/api/obsidian/status?${search.toString()}`);
}

export function getObsidianTargets(): Promise<ObsidianTargets> {
  return request<ObsidianTargets>("/api/obsidian/targets");
}

export function previewObsidianRender(
  optionalTables: ObsidianOptionalTable[],
): Promise<ObsidianRenderPreview> {
  return request<ObsidianRenderPreview>("/api/obsidian/render/preview", {
    method: "POST",
    body: JSON.stringify({ optional_tables: optionalTables }),
  });
}

export function applyObsidianRender(payload: {
  preview_token: string;
  optional_tables: ObsidianOptionalTable[];
  continuation: "render" | "discard_managed_edits";
}): Promise<ObsidianRenderResult> {
  return request<ObsidianRenderResult>("/api/obsidian/render/apply", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function previewObsidianSync(targetId: string): Promise<ObsidianSyncPreview> {
  return request<ObsidianSyncPreview>("/api/obsidian/sync/preview", {
    method: "POST",
    body: JSON.stringify({ target_id: targetId }),
  });
}

export function applyObsidianSync(payload: {
  target_id: string;
  preview_token: string;
  continuation: ObsidianSyncContinuation;
}): Promise<ObsidianSyncResult> {
  return request<ObsidianSyncResult>("/api/obsidian/sync/apply", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listScreeningCriteria(questionId?: string): Promise<ScreeningCriteriaList> {
  const search = new URLSearchParams({ page_size: "100" });
  if (questionId) search.set("question_id", questionId);
  return request<ScreeningCriteriaList>(`/api/screening/criteria?${search.toString()}`);
}

export function promoteScreeningCriteria(payload: ScreeningCriteriaPromoteRequest): Promise<ScreeningMutationResult> {
  return request<ScreeningMutationResult>("/api/screening/criteria", { method: "POST", body: JSON.stringify(payload) });
}

export function listScreeningDecisions(questionId: string, paperId?: string): Promise<ScreeningDecisionList> {
  const search = new URLSearchParams({ question_id: questionId, page_size: "100" });
  if (paperId) search.set("paper_id", paperId);
  return request<ScreeningDecisionList>(`/api/screening/decisions?${search.toString()}`);
}

export function promoteScreeningDecision(payload: ScreeningDecisionPromoteRequest): Promise<ScreeningMutationResult> {
  return request<ScreeningMutationResult>("/api/screening/decisions", { method: "POST", body: JSON.stringify(payload) });
}

export function createScreeningCriteriaProposal(payload: ScreeningCriteriaProposalCreateRequest): Promise<AgentMutationResult> {
  return request<AgentMutationResult>("/api/screening/proposals/criteria", { method: "POST", body: JSON.stringify(payload) });
}

export function createScreeningDecisionProposal(payload: ScreeningDecisionProposalCreateRequest): Promise<AgentMutationResult> {
  return request<AgentMutationResult>("/api/screening/proposals/decisions", { method: "POST", body: JSON.stringify(payload) });
}

export function approveScreeningProposal(taskId: string, payload: AgentExpectedState): Promise<AgentMutationResult> {
  return request<AgentMutationResult>(`/api/screening/proposals/${encodeURIComponent(taskId)}/approve`, { method: "POST", body: JSON.stringify(payload) });
}

export function inspectAgentHandoff(
  taskId: string,
  payload: AgentExpectedState & { executor_id: "codex_cli" | "claude_code_cli" },
): Promise<AgentInspectResult> {
  return request<AgentInspectResult>(`/api/agent/tasks/${encodeURIComponent(taskId)}/inspect-handoff`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function prepareAgentHandoff(
  taskId: string,
  payload: AgentExpectedState & { executor_id: "codex_cli" | "claude_code_cli" },
): Promise<AgentHandoffResult> {
  return request<AgentHandoffResult>(`/api/agent/tasks/${encodeURIComponent(taskId)}/handoff`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function submitAgentResult(
  taskId: string,
  payload: AgentExpectedState & { result: { [key: string]: JsonValue } },
): Promise<AgentMutationResult> {
  return request<AgentMutationResult>(`/api/agent/tasks/${encodeURIComponent(taskId)}/submit`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getAgentPreview(taskId: string): Promise<AgentPreviewResult> {
  return request<AgentPreviewResult>(`/api/agent/tasks/${encodeURIComponent(taskId)}/preview`);
}

export function requestAgentRevision(
  taskId: string,
  payload: AgentExpectedState & { feedback: string },
): Promise<AgentMutationResult> {
  return request<AgentMutationResult>(`/api/agent/tasks/${encodeURIComponent(taskId)}/request-revision`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function refreshAgentTask(
  taskId: string,
  payload: AgentExpectedState,
): Promise<AgentMutationResult> {
  return request<AgentMutationResult>(`/api/agent/tasks/${encodeURIComponent(taskId)}/refresh`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getSourceAdequacyResolution(
  taskId: string,
): Promise<SourceAdequacyResolutionContext> {
  return request<SourceAdequacyResolutionContext>(
    `/api/agent/tasks/${encodeURIComponent(taskId)}/source-adequacy-resolution`,
  );
}

export function openSourceAdequacyReview(
  taskId: string,
  payload: AgentExpectedState,
): Promise<SourceAdequacyReviewOpenResult> {
  return request<SourceAdequacyReviewOpenResult>(
    `/api/agent/tasks/${encodeURIComponent(taskId)}/source-adequacy-resolution/open`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export function decideSourceAdequacyResolution(
  taskId: string,
  payload: AgentExpectedState & {
    action: "accept_uncertainty" | "remediation_required";
    confirmation_id?: string;
  },
): Promise<AgentMutationResult> {
  return request<AgentMutationResult>(
    `/api/agent/tasks/${encodeURIComponent(taskId)}/source-adequacy-resolution/decide`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export function rejectAgentResult(
  taskId: string,
  payload: AgentExpectedState & { reason_code: "user_rejected" },
): Promise<AgentMutationResult> {
  return request<AgentMutationResult>(`/api/agent/tasks/${encodeURIComponent(taskId)}/reject`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function approveAgentResult(
  taskId: string,
  payload: AgentExpectedState,
): Promise<AgentMutationResult> {
  return request<AgentMutationResult>(`/api/agent/tasks/${encodeURIComponent(taskId)}/approve`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function acceptKnowledgeQueryReport(
  taskId: string,
  payload: AgentExpectedState,
): Promise<AgentMutationResult> {
  return request<AgentMutationResult>(`/api/knowledge-queries/${encodeURIComponent(taskId)}/accept-report`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function shutdown(): Promise<void> {
  await request<{ status: string }>("/api/shutdown", {
    method: "POST",
    body: "{}",
  });
  csrfToken = null;
}

export function clearClientSecurityState(): void {
  csrfToken = null;
}
