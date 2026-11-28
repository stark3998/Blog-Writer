import {
  Ban,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  CircleDot,
  CircleHelp,
  CircleMinus,
  CirclePause,
  CircleX,
  Clock,
  OctagonAlert,
  type LucideIcon,
} from 'lucide-react';

import { Badge, type BadgeProps } from '@/components/ui/badge';
import type { JobStatus } from '@/lib/events/events';

import type { Schemas, ServiceView } from '../queries';

type Tone = NonNullable<BadgeProps['variant']>;

export interface StateLook {
  readonly label: string;
  readonly tone: Tone;
  readonly icon: LucideIcon;
}

/** A badge with an icon and words, so a state never rests on colour alone. */
export function StateBadge({ look, className }: { look: StateLook; className?: string }) {
  const Icon = look.icon;
  return (
    <Badge variant={look.tone} className={className}>
      <Icon aria-hidden="true" />
      {look.label}
    </Badge>
  );
}

export const JOB_STATUS_LOOKS: Readonly<Record<JobStatus, StateLook>> = {
  queued: { label: 'Queued', tone: 'neutral', icon: Clock },
  running: { label: 'Running', tone: 'outline', icon: CircleDashed },
  waiting_input: { label: 'Waiting for you', tone: 'warn', icon: CircleAlert },
  succeeded: { label: 'Finished', tone: 'success', icon: CircleCheck },
  failed: { label: 'Failed', tone: 'danger', icon: CircleX },
  quarantined: { label: 'Stopped', tone: 'danger', icon: OctagonAlert },
  cancelled: { label: 'Cancelled', tone: 'neutral', icon: Ban },
};

export function JobStatusBadge({ status }: { status: JobStatus }) {
  return <StateBadge look={JOB_STATUS_LOOKS[status]} />;
}

type ServiceState = Schemas['ServiceState'];

export const SERVICE_STATE_LOOKS: Readonly<Record<ServiceState, StateLook>> = {
  on: { label: 'On', tone: 'success', icon: CircleCheck },
  off: { label: 'Off', tone: 'neutral', icon: CircleMinus },
  degraded: { label: 'Needs attention', tone: 'warn', icon: CircleAlert },
  unknown: { label: 'Unknown', tone: 'neutral', icon: CircleHelp },
};

/** Owner-facing sentences for the status reason codes (`services.status.ServiceView.reason`). */
const SERVICE_REASONS: Readonly<Record<string, string>> = {
  not_configured: 'Not set up on this server.',
  chromium_missing: 'The renderer’s browser is missing, so carousels and images can’t render.',
  circuit_open: 'Failing for now. Requests use the direct fetcher until it recovers.',
  needs_reconnect: 'The LinkedIn token needs reconnecting.',
  disconnected: 'LinkedIn isn’t connected.',
  no_subscriptions: 'No device has notifications turned on.',
  unreadable: 'Its state couldn’t be read.',
  no_probe: 'Nothing reports its state yet.',
  probe_failed: 'Its status check failed.',
  unavailable: 'Not available right now.',
  unreachable: 'Storage isn’t answering. Uploads, media and renders wait until it does; nothing is lost.',
};

/** One sentence for a service's state: its reason when it has one, else its state. */
export function serviceSentence(view: ServiceView): string {
  if (view.reason) return SERVICE_REASONS[view.reason] ?? `Reported “${view.reason}”.`;
  switch (view.state) {
    case 'on':
      return 'Working.';
    case 'off':
      return 'Not set up on this server.';
    case 'degraded':
      return 'Set up, but not working right now.';
    case 'unknown':
      return 'Its state isn’t known yet.';
  }
}

export function ServiceBadge({ view }: { view: Pick<ServiceView, 'state'> }) {
  return <StateBadge look={SERVICE_STATE_LOOKS[view.state]} />;
}

/** Generic "on/off" for switches that describe a setting rather than a service. */
export const ON_LOOK: StateLook = { label: 'On', tone: 'success', icon: CircleCheck };
export const OFF_LOOK: StateLook = { label: 'Off', tone: 'neutral', icon: CircleMinus };
export const PAUSED_LOOK: StateLook = { label: 'Paused', tone: 'warn', icon: CirclePause };
export const ACTIVE_LOOK: StateLook = { label: 'Active', tone: 'success', icon: CircleDot };
