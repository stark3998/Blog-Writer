/**
 * Zustand store — global blog state management.
 */

import { create } from "zustand";
import type { BlogDraft, GenerateResult } from "../types";

export type AppPhase = "idle" | "analyzing" | "generating" | "editing" | "saving";

interface BlogStore {
  // Current draft
  draft: BlogDraft | null;
  setDraft: (draft: BlogDraft | null) => void;

  // Editor content (live, may differ from saved draft)
  content: string;
  setContent: (content: string) => void;

  // SSE streaming accumulator
  streamContent: string;
  appendStream: (chunk: string) => void;
  resetStream: () => void;

  // UI phase
  phase: AppPhase;
  setPhase: (phase: AppPhase) => void;

  // Status / error messages
  statusMessage: string;
  setStatusMessage: (msg: string) => void;
  error: string | null;
  setError: (err: string | null) => void;

  // Populate from generate result
  applyGenerateResult: (result: GenerateResult) => void;

  // Drafts list (home page)
  drafts: BlogDraft[];
  setDrafts: (drafts: BlogDraft[]) => void;
}

export const useBlogStore = create<BlogStore>((set) => ({
  draft: null,
  setDraft: (draft) => set({ draft }),

  content: "",
  setContent: (content) => set({ content }),

  streamContent: "",
  appendStream: (chunk) => set((s) => ({ streamContent: s.streamContent + chunk })),
  resetStream: () => set({ streamContent: "" }),

  phase: "idle",
  setPhase: (phase) => set({ phase }),

  statusMessage: "",
  setStatusMessage: (statusMessage) => set({ statusMessage }),

  error: null,
  setError: (error) => set({ error }),

  applyGenerateResult: (result) =>
    set({
      content: result.mdx_content,
      streamContent: "",
      phase: "idle",
      statusMessage: "Blog generated successfully",
    }),

  drafts: [],
  setDrafts: (drafts) => set({ drafts }),
}));
