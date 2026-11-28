import {
  Archive,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  CircleDot,
  CircleX,
  Globe,
  LoaderCircle,
  PencilLine,
  type LucideIcon,
} from 'lucide-react';

import { Badge, type BadgeProps } from '@/components/ui/badge';

import { STATUS_LABELS } from '../model/status';
import type { ArticleStatus } from '../model/types';

const LOOK: Readonly<Record<ArticleStatus, { icon: LucideIcon; variant: NonNullable<BadgeProps['variant']> }>> = {
  planned: { icon: CircleDashed, variant: 'neutral' },
  researching: { icon: LoaderCircle, variant: 'outline' },
  interviewing: { icon: CircleDot, variant: 'outline' },
  drafting: { icon: PencilLine, variant: 'outline' },
  gating: { icon: LoaderCircle, variant: 'outline' },
  in_review: { icon: CircleAlert, variant: 'warn' },
  approved: { icon: CircleCheck, variant: 'success' },
  publishing: { icon: LoaderCircle, variant: 'outline' },
  published: { icon: Globe, variant: 'success' },
  publish_failed: { icon: CircleX, variant: 'danger' },
  unpublishing: { icon: LoaderCircle, variant: 'outline' },
  unpublished: { icon: CircleDashed, variant: 'neutral' },
  needs_attention: { icon: CircleAlert, variant: 'danger' },
  archived: { icon: Archive, variant: 'neutral' },
};

/** The article's status, in the glossary's words with an icon (never colour alone). */
export function StatusBadge({ status }: { status: ArticleStatus }) {
  const { icon: Icon, variant } = LOOK[status];
  return (
    <Badge variant={variant} className="text-sm">
      <Icon aria-hidden="true" className="size-3.5!" />
      <span className="sr-only">Status: </span>
      {STATUS_LABELS[status]}
    </Badge>
  );
}
