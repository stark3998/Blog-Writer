/** The three content pillars. Their colours are the `agents`, `identity` and `governance` tokens. */
export type PillarId = 'agents' | 'identity' | 'governance';

export interface Pillar {
  id: PillarId;
  name: string;
  topics: string;
}

export const PILLARS: readonly Pillar[] = [
  {
    id: 'agents',
    name: 'AI agents & security',
    topics: 'MCP, multi-agent SecOps, prompt injection, agent identity',
  },
  {
    id: 'identity',
    name: 'Identity modernization',
    topics: 'Okta to Entra, a 2,000-app migration, CIAM',
  },
  {
    id: 'governance',
    name: 'AI governance',
    topics: 'Risk-tiered agent lifecycles, NIST AI RMF, OWASP, MITRE ATLAS',
  },
];

/** Static class names, so Tailwind sees every one of them at build time. */
export const PILLAR_THREAD_CLASS: Record<PillarId, string> = {
  agents: 'bg-agents',
  identity: 'bg-identity',
  governance: 'bg-governance',
};
