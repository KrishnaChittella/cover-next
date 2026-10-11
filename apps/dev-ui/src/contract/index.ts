// The artifact contract as the UI sees it. Every type here is generated from the
// checked-in JSON Schemas; see scripts/generate-contract.mjs.
export type {
  CurrentArtifact,
  LeaderboardEntry,
  UnrankedEntry,
  SourceCoverage,
} from "./types/current";
export type {
  GameArtifact,
  ScoreView,
  ScoreComponentsView,
  ConfidenceView,
  ConfidenceFactorsView,
  FeaturesView,
  ExplanationView,
  ReasonView,
  HistorySeries,
  HistoryPoint,
  RelationshipView,
  EventView,
  EventIntelligenceView,
  EventContributionView,
  MethodologyRef,
} from "./types/game";
export type { Manifest, ManifestPaths } from "./types/manifest";
export type {
  MethodologyArtifact,
  TermView,
  FeatureDefinitionView,
  MetricView,
  SourceMethodology,
  SourcePolicyView,
  EventHeatMethodology,
  EventTypeRuleView,
} from "./types/methodology";
export type { RadarArtifact, RadarItemView } from "./types/radar";
export type {
  ValidationArtifact,
  ValidationReportView,
  StrategyResultView,
  OutcomeSummaryView,
  StratumView,
  InspectionDayView,
  InspectionEntryView,
  EvidenceView,
} from "./types/validation";
export type {
  SourceHealthArtifact,
  SourceHealthEntry,
  SourceFailure,
} from "./types/source-health";

export type ArtifactKind =
  | "manifest"
  | "current"
  | "game"
  | "source-health"
  | "methodology"
  | "radar"
  | "validation";
