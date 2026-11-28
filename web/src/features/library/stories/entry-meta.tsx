import { Archive, CircleCheck, CircleDashed, CircleX, Eye, EyeOff, ShieldAlert, ShieldCheck, ShieldQuestion } from 'lucide-react';

import { Badge } from '@/components/ui/badge';

import { CONFIDENTIALITY_LABEL, REVIEW_STATUS_LABEL } from '../lib/labels';
import type { ClassifierCheck, Confidentiality, ReviewStatus } from '../lib/types';
import { verdictOf } from './model';

/** The review state with an icon and its word (never colour alone). */
export function ReviewStatusBadge({ status }: { status: ReviewStatus }) {
  switch (status) {
    case 'proposed':
      return (
        <Badge variant="warn">
          <CircleDashed aria-hidden="true" />
          {REVIEW_STATUS_LABEL.proposed}
        </Badge>
      );
    case 'approved':
      return (
        <Badge variant="success">
          <CircleCheck aria-hidden="true" />
          {REVIEW_STATUS_LABEL.approved}
        </Badge>
      );
    case 'rejected':
      return (
        <Badge>
          <CircleX aria-hidden="true" />
          {REVIEW_STATUS_LABEL.rejected}
        </Badge>
      );
    case 'archived':
      return (
        <Badge>
          <Archive aria-hidden="true" />
          {REVIEW_STATUS_LABEL.archived}
        </Badge>
      );
  }
}

export function ConfidentialityBadge({ value }: { value: Confidentiality }) {
  return (
    <Badge variant="outline">
      {value === 'public' ? <Eye aria-hidden="true" /> : <EyeOff aria-hidden="true" />}
      {CONFIDENTIALITY_LABEL[value]}
    </Badge>
  );
}

/** What the confidentiality classifier said: drafts use an entry only once it passed. */
export function VerdictBadge({ check }: { check: ClassifierCheck | undefined }) {
  switch (verdictOf(check)) {
    case 'passed':
      return (
        <Badge variant="success">
          <ShieldCheck aria-hidden="true" />
          Classifier passed
        </Badge>
      );
    case 'flagged':
      return (
        <Badge variant="danger">
          <ShieldAlert aria-hidden="true" />
          Classifier flagged it
        </Badge>
      );
    case 'unchecked':
      return (
        <Badge>
          <ShieldQuestion aria-hidden="true" />
          Not checked yet
        </Badge>
      );
  }
}

/** The classifier's reasons for a flagged entry (messages never quote the text). */
export function ClassifierNotes({ check }: { check: ClassifierCheck | undefined }) {
  const findings = check?.findings ?? [];
  if (verdictOf(check) !== 'flagged' || findings.length === 0) return null;
  return (
    <ul className="list-disc space-y-1 pl-5 text-sm text-danger">
      {findings.map((finding, index) => (
        <li key={index}>{finding.message}</li>
      ))}
    </ul>
  );
}
