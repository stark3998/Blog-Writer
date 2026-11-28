/**
 * Intake: the shared raw text -> server redaction and checks -> owner-confirmed public-safe
 * wording flow (contract W-A3, "Intake flow in the UI"). Studio's interview uses it per question;
 * Library's stories and POVs reuse it. Raw text stays in component state or another in-memory
 * store only (contract 1.9 rule 9).
 */
export { IntakeFlow, useIntakeState, CHECK_DELAY_MS, POLICY_SETTINGS_PATH, type IntakeFlowProps } from './intake-flow';
export {
  EMPTY_INTAKE,
  backToWriting,
  hasCurrentReview,
  hasUnsavedText,
  intakeProgress,
  reopenReview,
  startingWording,
  withConfirmed,
  withRaw,
  withRedaction,
  withWording,
  type ConfirmedWording,
  type IntakeProgress,
  type IntakeReview,
  type IntakeStage,
  type IntakeState,
} from './intake-state';
export { checkIntake, redactIntake, type RedactRequest } from './api';
export {
  FINDING_CODES,
  codePointOffsets,
  countFindings,
  describeFindings,
  findingExcerpt,
  findingLabel,
  findingSpan,
  isBlocking,
  markFindings,
  maskBlocking,
  redactionCount,
  segmentText,
  type MarkedFinding,
  type TextSegment,
  type TextSpan,
} from './findings';
export { MarkedText, type MarkedTextProps } from './components/marked-text';
export { FindingList, type FindingListProps } from './components/finding-list';
export { ActionBar, type ActionBarProps } from './components/action-bar';
export { DictationButton, onDeviceRecognition, type DictationButtonProps } from './components/dictation-button';
export {
  INTAKE_MAX_CHARS,
  type CheckResult,
  type Finding,
  type IntakePurpose,
  type ProposalReason,
  type RedactionProposal,
} from './types';
