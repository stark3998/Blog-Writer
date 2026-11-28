import type { components } from '@/lib/api/schema';

type MachineView = components['schemas']['MachineView'];

/** Edge tables as tests write them: `{machine: {state: [targets]}}`. */
export type EdgeTables = Readonly<Record<string, Readonly<Record<string, readonly string[]>>>>;

/**
 * `GET /api/meta/machines` exactly as the server answers it (`blogwriter.api.system.machine_views`:
 * `domain.states` machines, `OWNER_EDGES`, `OWNER_WRITES`, `ARTICLE_WRITER`). Regenerate it when
 * a machine or the owner's part in one changes on the server.
 */
export const SERVER_MACHINES = {
  article: {
    initial: 'planned',
    states: ['planned', 'researching', 'interviewing', 'drafting', 'gating', 'in_review', 'approved', 'publishing', 'published', 'publish_failed', 'unpublishing', 'unpublished', 'needs_attention', 'archived'],
    edges: {
      planned: ['archived', 'drafting', 'in_review', 'interviewing', 'researching'],
      researching: ['archived', 'drafting', 'interviewing', 'needs_attention'],
      interviewing: ['archived', 'drafting', 'needs_attention'],
      drafting: ['archived', 'gating', 'in_review', 'needs_attention'],
      gating: ['approved', 'in_review', 'needs_attention'],
      in_review: ['approved', 'archived', 'drafting', 'gating', 'interviewing', 'researching'],
      approved: ['in_review', 'publishing'],
      publishing: ['publish_failed', 'published'],
      published: ['in_review', 'publishing', 'unpublishing'],
      publish_failed: ['in_review', 'publishing', 'unpublishing'],
      unpublishing: ['publish_failed', 'unpublished'],
      unpublished: ['archived', 'in_review'],
      needs_attention: ['archived', 'drafting', 'in_review', 'interviewing', 'researching'],
      archived: ['planned'],
    },
    ownerEdges: {
      planned: ['archived', 'drafting', 'interviewing', 'researching'],
      researching: ['needs_attention'],
      interviewing: ['needs_attention'],
      drafting: ['needs_attention'],
      gating: ['needs_attention'],
      in_review: ['approved', 'archived', 'drafting', 'interviewing', 'researching'],
      approved: ['in_review'],
      publishing: [],
      published: ['in_review', 'unpublishing'],
      publish_failed: ['in_review', 'unpublishing'],
      unpublishing: [],
      unpublished: ['archived', 'in_review'],
      needs_attention: ['archived', 'drafting', 'in_review', 'interviewing', 'researching'],
      archived: ['planned'],
    },
    ownerWrites: ['planned', 'in_review', 'needs_attention'],
    writers: { planned: 'owner', researching: 'pipeline', interviewing: 'pipeline', drafting: 'pipeline', gating: 'pipeline', in_review: 'owner', approved: 'none', publishing: 'publish', published: 'none', publish_failed: 'none', unpublishing: 'publish', unpublished: 'none', needs_attention: 'owner', archived: 'none' },
  },
  post: {
    initial: 'draft',
    states: ['generating', 'draft', 'approved', 'staged', 'publishing', 'posted', 'kit', 'needs_attention', 'missed', 'expired', 'deleted', 'archived'],
    edges: {
      generating: ['archived', 'draft', 'needs_attention'],
      draft: ['approved', 'archived', 'expired', 'generating'],
      approved: ['archived', 'draft', 'expired', 'kit', 'missed', 'publishing', 'staged'],
      staged: ['approved', 'draft', 'expired', 'kit', 'missed', 'publishing'],
      publishing: ['approved', 'needs_attention', 'posted'],
      posted: ['deleted'],
      kit: ['approved', 'archived', 'expired', 'missed', 'posted'],
      needs_attention: ['approved', 'archived', 'draft', 'kit', 'posted'],
      missed: ['approved', 'archived', 'expired', 'kit'],
      expired: ['archived', 'draft'],
      deleted: [],
      archived: ['draft'],
    },
    ownerEdges: {
      generating: ['archived'],
      draft: ['approved', 'archived', 'generating'],
      approved: ['archived', 'draft', 'kit', 'publishing'],
      staged: ['approved', 'draft', 'kit', 'publishing'],
      publishing: [],
      posted: ['deleted'],
      kit: ['approved', 'archived', 'posted'],
      needs_attention: ['approved', 'archived', 'draft', 'posted'],
      missed: ['approved', 'archived'],
      expired: ['archived', 'draft'],
      deleted: [],
      archived: [],
    },
    ownerWrites: ['draft', 'approved', 'staged', 'needs_attention', 'expired'],
    writers: null,
  },
  kit: {
    initial: 'prepared',
    states: ['prepared', 'native_draft_saved', 'natively_scheduled', 'posted'],
    edges: {
      prepared: ['native_draft_saved', 'natively_scheduled', 'posted'],
      native_draft_saved: ['natively_scheduled', 'posted'],
      natively_scheduled: ['native_draft_saved', 'posted'],
      posted: [],
    },
    ownerEdges: {
      prepared: ['native_draft_saved', 'natively_scheduled', 'posted'],
      native_draft_saved: ['natively_scheduled', 'posted'],
      natively_scheduled: ['native_draft_saved', 'posted'],
      posted: [],
    },
    ownerWrites: [],
    writers: null,
  },
  idea: {
    initial: 'new',
    states: ['new', 'accepted', 'snoozed', 'used', 'dismissed', 'expired'],
    edges: {
      new: ['accepted', 'dismissed', 'expired', 'snoozed', 'used'],
      accepted: ['dismissed', 'new', 'used'],
      snoozed: ['dismissed', 'expired', 'new'],
      used: ['accepted'],
      dismissed: ['new'],
      expired: ['new'],
    },
    ownerEdges: {
      new: ['accepted', 'dismissed', 'snoozed'],
      accepted: ['dismissed', 'new'],
      snoozed: ['dismissed', 'new'],
      used: [],
      dismissed: ['new'],
      expired: ['new'],
    },
    ownerWrites: ['new', 'accepted', 'snoozed', 'used', 'dismissed', 'expired'],
    writers: null,
  },
  research_run: {
    initial: 'queued',
    states: ['queued', 'running', 'succeeded', 'failed', 'quarantined', 'cancelled'],
    edges: {
      queued: ['cancelled', 'running'],
      running: ['cancelled', 'failed', 'quarantined', 'succeeded'],
      succeeded: [],
      failed: ['queued'],
      quarantined: [],
      cancelled: [],
    },
    ownerEdges: {
      queued: ['cancelled'],
      running: ['cancelled'],
      succeeded: [],
      failed: [],
      quarantined: [],
      cancelled: [],
    },
    ownerWrites: [],
    writers: null,
  },
  review: {
    initial: 'proposed',
    states: ['proposed', 'approved', 'rejected', 'archived'],
    edges: {
      proposed: ['approved', 'rejected'],
      approved: ['archived'],
      rejected: ['proposed'],
      archived: ['approved'],
    },
    ownerEdges: {
      proposed: ['approved', 'rejected'],
      approved: ['archived'],
      rejected: ['proposed'],
      archived: ['approved'],
    },
    ownerWrites: ['proposed', 'approved', 'rejected', 'archived'],
    writers: null,
  },
  voice_sample: {
    initial: 'active',
    states: ['active', 'excluded'],
    edges: {
      active: ['excluded'],
      excluded: ['active'],
    },
    ownerEdges: {
      active: ['excluded'],
      excluded: ['active'],
    },
    ownerWrites: ['active', 'excluded'],
    writers: null,
  },
  style_guide: {
    initial: 'proposed',
    states: ['proposed', 'active', 'superseded', 'rejected'],
    edges: {
      proposed: ['active', 'rejected'],
      active: ['superseded'],
      superseded: ['active'],
      rejected: [],
    },
    ownerEdges: {
      proposed: ['active', 'rejected'],
      active: [],
      superseded: ['active'],
      rejected: [],
    },
    ownerWrites: [],
    writers: null,
  },
  suggestion: {
    initial: 'proposed',
    states: ['proposed', 'accepted', 'dismissed'],
    edges: {
      proposed: ['accepted', 'dismissed'],
      accepted: [],
      dismissed: [],
    },
    ownerEdges: {
      proposed: ['accepted', 'dismissed'],
      accepted: [],
      dismissed: [],
    },
    ownerWrites: [],
    writers: null,
  },
  interview: {
    initial: 'pending',
    states: ['pending', 'answered', 'skipped', 'expired'],
    edges: {
      pending: ['answered', 'expired', 'skipped'],
      answered: [],
      skipped: [],
      expired: ['answered', 'skipped'],
    },
    ownerEdges: {
      pending: ['answered', 'skipped'],
      answered: [],
      skipped: [],
      expired: ['answered', 'skipped'],
    },
    ownerWrites: ['pending', 'expired'],
    writers: null,
  },
  week_plan: {
    initial: 'generating',
    states: ['generating', 'proposed', 'approved', 'closed'],
    edges: {
      generating: ['proposed'],
      proposed: ['approved', 'generating'],
      approved: ['closed', 'proposed'],
      closed: [],
    },
    ownerEdges: {
      generating: ['proposed'],
      proposed: ['approved'],
      approved: ['proposed'],
      closed: [],
    },
    ownerWrites: ['generating', 'proposed', 'approved'],
    writers: null,
  },
  reminder: {
    initial: 'pending',
    states: ['pending', 'sent', 'cancelled', 'expired'],
    edges: {
      pending: ['cancelled', 'expired', 'sent'],
      sent: [],
      cancelled: [],
      expired: [],
    },
    ownerEdges: {
      pending: ['cancelled'],
      sent: [],
      cancelled: [],
      expired: [],
    },
    ownerWrites: [],
    writers: null,
  },
  engagement_item: {
    initial: 'open',
    states: ['open', 'finalized', 'dismissed', 'expired'],
    edges: {
      open: ['dismissed', 'expired', 'finalized'],
      finalized: [],
      dismissed: [],
      expired: [],
    },
    ownerEdges: {
      open: ['dismissed', 'finalized'],
      finalized: [],
      dismissed: [],
      expired: [],
    },
    ownerWrites: [],
    writers: null,
  },
  experiment: {
    initial: 'draft',
    states: ['draft', 'running', 'concluded', 'abandoned'],
    edges: {
      draft: ['abandoned', 'running'],
      running: ['abandoned', 'concluded'],
      concluded: [],
      abandoned: [],
    },
    ownerEdges: {
      draft: ['abandoned', 'running'],
      running: ['abandoned', 'concluded'],
      concluded: [],
      abandoned: [],
    },
    ownerWrites: ['draft', 'running'],
    writers: null,
  },
  source: {
    initial: 'active',
    states: ['active', 'paused', 'failing'],
    edges: {
      active: ['failing', 'paused'],
      paused: ['active'],
      failing: ['active', 'paused'],
    },
    ownerEdges: {
      active: ['paused'],
      paused: ['active'],
      failing: ['active', 'paused'],
    },
    ownerWrites: ['active', 'paused', 'failing'],
    writers: null,
  },
  linkedin_connection: {
    initial: 'disconnected',
    states: ['disconnected', 'connected', 'needs_reconnect'],
    edges: {
      disconnected: ['connected'],
      connected: ['disconnected', 'needs_reconnect'],
      needs_reconnect: ['connected', 'disconnected'],
    },
    ownerEdges: {
      disconnected: ['connected'],
      connected: ['disconnected'],
      needs_reconnect: ['connected', 'disconnected'],
    },
    ownerWrites: [],
    writers: null,
  },
} satisfies Readonly<Record<string, MachineView>>;

/**
 * A machines answer for a test. Without `tables`: every machine as the server answers it. With
 * `tables`: only those machines, with the given edges; the owner's edges and states are the
 * server's, kept where the given edges still have them.
 */
export function machinesResponse(tables?: EdgeTables): Record<string, MachineView> {
  if (!tables) return { ...SERVER_MACHINES };
  const known: Readonly<Record<string, MachineView | undefined>> = SERVER_MACHINES;
  return Object.fromEntries(
    Object.entries(tables).map(([name, edges]) => {
      const server = known[name];
      const states = Object.keys(edges);
      const ownerEdges = Object.fromEntries(
        states.map((state) => [state, (server?.ownerEdges[state] ?? []).filter((target) => edges[state]?.includes(target))]),
      );
      const view: MachineView = {
        initial: server && states.includes(server.initial) ? server.initial : (states[0] ?? ''),
        states,
        edges: Object.fromEntries(states.map((state) => [state, [...(edges[state] ?? [])]])),
        ownerEdges,
        ownerWrites: (server?.ownerWrites ?? []).filter((state) => states.includes(state)),
        writers: server?.writers ?? null,
      };
      return [name, view];
    }),
  );
}

