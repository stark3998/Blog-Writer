import { Circle, CircleCheck, CircleDashed, CircleX, TriangleAlert, type LucideIcon } from 'lucide-react';

import { Badge } from '@/components/ui/badge';

import type { StatusLabel } from '../lib/labels';

const ICONS: Record<StatusLabel['tone'], LucideIcon> = {
  neutral: Circle,
  success: CircleCheck,
  warn: TriangleAlert,
  danger: CircleX,
};

interface StatusBadgeProps {
  status: StatusLabel;
  /** Work in progress ("Writing", "Being planned"): a dashed circle, the job timeline's "in progress". */
  busy?: boolean;
  className?: string;
}

/** A state in words with its icon: the colour only repeats what the words say. */
export function StatusBadge({ status, busy = false, className }: StatusBadgeProps) {
  const Icon = busy ? CircleDashed : ICONS[status.tone];
  return (
    <Badge variant={status.tone} className={className}>
      <Icon aria-hidden="true" />
      {status.label}
    </Badge>
  );
}
