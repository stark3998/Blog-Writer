/**
 * API client for the Blog Writer backend.
 * Handles REST calls and SSE streaming connections.
 */

import type { BlogDraft, GenerateResult, ExportFormat } from "../types";

const API_BASE = "/api";

// ---------- helpers ----------

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

// ---------- SSE Streaming helper ----------

export interface SSECallbacks {
  onChunk?: (content: string) => void;
  onAnalyzing?: () => void;
  onAnalyzed?: (sourceType: string) => void;
  onGenerating?: () => void;
  onComplete?: (data: GenerateResult | { content: string }) => void;
  onError?: (error: string) => void;
}

export function streamSSE(url: string, body: object, callbacks: SSECallbacks): AbortController {
  const controller = new AbortController();

  fetch(`${API_BASE}${url}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        callbacks.onError?.(errBody.detail ?? `HTTP ${res.status}`);
        return;
      }

      const reader = res.body?.getReader();
      if (!reader) return;

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Parse SSE lines
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        let currentEvent = "message";
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            currentEvent = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            const rawData = line.slice(6);
            try {
              const data = JSON.parse(rawData);

              switch (currentEvent) {
                case "analyzing":
                  callbacks.onAnalyzing?.();
                  break;
                case "analyzed":
                  callbacks.onAnalyzed?.(data.source_type);
                  break;
                case "generating":
                  callbacks.onGenerating?.();
                  break;
                case "chunk":
                  callbacks.onChunk?.(data.content);
                  break;
                case "complete":
                  callbacks.onComplete?.(data);
                  break;
                case "error":
                  callbacks.onError?.(data.error);
                  break;
              }
            } catch {
              // ignore malformed JSON lines
            }
            currentEvent = "message";
          }
        }
      }
    })
    .catch((err) => {
      if (err.name !== "AbortError") {
        callbacks.onError?.(err.message);
      }
    });

  return controller;
}

// ---------- Generate ----------

export function generateBlogStream(url: string, callbacks: SSECallbacks): AbortController {
  return streamSSE("/generate/stream", { url }, callbacks);
}

export async function generateBlog(url: string): Promise<GenerateResult> {
  return json<GenerateResult>("/generate", { method: "POST", body: JSON.stringify({ url }) });
}

// ---------- Edit ----------

export function editBlogStream(
  content: string,
  prompt: string,
  callbacks: SSECallbacks
): AbortController {
  return streamSSE("/edit/stream", { content, prompt }, callbacks);
}

// ---------- Blogs CRUD ----------

export async function listDrafts(limit = 50): Promise<BlogDraft[]> {
  return json<BlogDraft[]>(`/blogs?limit=${limit}`);
}

export async function getDraft(id: string): Promise<BlogDraft> {
  return json<BlogDraft>(`/blogs/${id}`);
}

export async function createDraft(draft: {
  title: string;
  slug: string;
  excerpt: string;
  content: string;
  source_url: string;
  source_type: string;
}): Promise<BlogDraft> {
  return json<BlogDraft>("/blogs", { method: "POST", body: JSON.stringify(draft) });
}

export async function updateDraft(
  id: string,
  updates: Partial<{ title: string; slug: string; excerpt: string; content: string }>
): Promise<BlogDraft> {
  return json<BlogDraft>(`/blogs/${id}`, { method: "PUT", body: JSON.stringify(updates) });
}

export async function deleteDraft(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/blogs/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) {
    throw new Error(`Delete failed: HTTP ${res.status}`);
  }
}

// ---------- Export ----------

export async function exportBlog(content: string, format: ExportFormat): Promise<Blob> {
  const res = await fetch(`${API_BASE}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, format }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `Export failed: HTTP ${res.status}`);
  }
  return res.blob();
}

// ---------- Publish ----------

export async function publishBlog(data: {
  content: string;
  slug: string;
  title: string;
  excerpt: string;
}): Promise<{ pr_url: string; branch: string; file_path: string }> {
  return json("/publish", { method: "POST", body: JSON.stringify(data) });
}

// ---------- LinkedIn ----------

export interface LinkedInComposeRequest {
  content?: string;
  draft_id?: string;
  title?: string;
  excerpt?: string;
  post_format?: "feed_post" | "long_form";
  additional_context?: string;
}

export interface LinkedInComposeResponse {
  format: string;
  title: string;
  excerpt: string;
  summary: string;
  insights: string[];
  my_2_cents: string;
  hashtags: string[];
  post_text: string;
  word_count: number;
}

export interface LinkedInOAuthStartResponse {
  session_id: string;
  state: string;
  auth_url: string;
}

export interface LinkedInOAuthCallbackResponse {
  session_id: string;
  person_urn: string;
  expires_at: number;
}

export interface LinkedInStatusResponse {
  connected: boolean;
  session_id: string;
  person_urn?: string;
  expires_at?: number;
}

export interface LinkedInPublishRequest {
  session_id: string;
  post_text?: string;
  content?: string;
  draft_id?: string;
  title?: string;
  excerpt?: string;
  post_format?: "feed_post" | "long_form";
  additional_context?: string;
  visibility?: "PUBLIC" | "CONNECTIONS";
}

export interface LinkedInPublishResponse {
  session_id: string;
  post_id: string;
  visibility: string;
  status_code: number;
  composed: boolean;
  post_text: string;
}

export async function composeLinkedInPost(
  data: LinkedInComposeRequest
): Promise<LinkedInComposeResponse> {
  return json("/linkedin/compose", { method: "POST", body: JSON.stringify(data) });
}

export async function startLinkedInOAuth(
  sessionId?: string
): Promise<LinkedInOAuthStartResponse> {
  const suffix = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  return json(`/linkedin/oauth/start${suffix}`);
}

export async function completeLinkedInOAuth(data: {
  code: string;
  state: string;
}): Promise<LinkedInOAuthCallbackResponse> {
  return json("/linkedin/oauth/callback", { method: "POST", body: JSON.stringify(data) });
}

export async function getLinkedInStatus(sessionId: string): Promise<LinkedInStatusResponse> {
  return json(`/linkedin/status?session_id=${encodeURIComponent(sessionId)}`);
}

export async function disconnectLinkedIn(sessionId: string): Promise<{ status: string; session_id: string }> {
  const res = await fetch(`${API_BASE}/linkedin/disconnect?session_id=${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export async function publishLinkedInPost(
  data: LinkedInPublishRequest
): Promise<LinkedInPublishResponse> {
  return json("/linkedin/publish", { method: "POST", body: JSON.stringify(data) });
}
