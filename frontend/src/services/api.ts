/**
 * API client for the Blog Writer backend.
 * Handles REST calls and SSE streaming connections.
 */

import type {
  BlogDraft,
  GenerateResult,
  ExportFormat,
  FeedSource,
  CrawledArticle,
  CrawlJob,
  CrawlResult,
  FeedDiscoverResult,
  DiagnosticsRunRequest,
  DiagnosticsRunResponse,
  DiagnosticsChecksResponse,
} from "../types";

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
  source_url?: string;
  source_type?: string;
}): Promise<{ blog_url: string; slug: string; title: string }> {
  return json("/publish", { method: "POST", body: JSON.stringify(data) });
}

export async function getPublishedBlog(slug: string): Promise<{
  slug: string;
  title: string;
  excerpt: string;
  html_content: string;
  source_url: string;
  source_type: string;
  tags: string[];
  date: string;
  published_at: string;
}> {
  return json(`/blog/${encodeURIComponent(slug)}`);
}

// ---------- LinkedIn ----------

export interface LinkedInComposeRequest {
  content?: string;
  draft_id?: string;
  title?: string;
  excerpt?: string;
  post_format?: "feed_post" | "long_form";
  additional_context?: string;
  blog_url?: string;
  source_url?: string;
}

export interface ValidationResult {
  is_valid: boolean;
  score: number;
  issues: Array<{
    severity: "error" | "warning" | "info";
    category: string;
    description: string;
    suggestion: string;
  }>;
  corrected_content: string | null;
  summary: string;
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
  image_url: string;
  validation: ValidationResult | null;
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
  image_url?: string;
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

// ---------- Feeds ----------

export async function listFeeds(): Promise<FeedSource[]> {
  return json<FeedSource[]>("/feeds");
}

export async function createFeed(data: {
  base_url: string;
  name?: string;
  topics?: string[];
  crawl_interval_minutes?: number;
  auto_publish_blog?: boolean;
  auto_publish_linkedin?: boolean;
}): Promise<FeedSource> {
  return json<FeedSource>("/feeds", { method: "POST", body: JSON.stringify(data) });
}

export async function discoverFeed(url: string): Promise<FeedDiscoverResult> {
  return json<FeedDiscoverResult>(`/feeds/discover?url=${encodeURIComponent(url)}`);
}

export async function getFeed(id: string): Promise<FeedSource> {
  return json<FeedSource>(`/feeds/${id}`);
}

export async function updateFeed(
  id: string,
  updates: Partial<{
    name: string;
    topics: string[];
    crawl_interval_minutes: number;
    auto_publish_blog: boolean;
    auto_publish_linkedin: boolean;
    enabled: boolean;
  }>
): Promise<FeedSource> {
  return json<FeedSource>(`/feeds/${id}`, { method: "PUT", body: JSON.stringify(updates) });
}

export async function deleteFeed(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/feeds/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) {
    throw new Error(`Delete failed: HTTP ${res.status}`);
  }
}

export async function triggerCrawl(feedId: string): Promise<CrawlResult> {
  return json<CrawlResult>(`/feeds/${feedId}/crawl`, { method: "POST" });
}

// ---------- Crawl SSE Streaming ----------

export interface CrawlSSEEvent {
  type: string;
  data: Record<string, unknown>;
}

export interface CrawlSSECallbacks {
  onCrawlStarted?: (data: { source_name: string; feed_type: string }) => void;
  onFetchingArticles?: (data: { method: string }) => void;
  onArticlesFetched?: (data: { total: number; new: number }) => void;
  onClassifying?: (data: { index: number; total: number; title: string }) => void;
  onClassified?: (data: { index: number; total: number; title: string; is_relevant: boolean; matched_topics: string[]; relevance_score: number }) => void;
  onGenerating?: (data: { index: number; total_relevant: number; title: string }) => void;
  onGenerated?: (data: { index: number; total_relevant: number; title: string; draft_id: string; status: string }) => void;
  onGenerateError?: (data: { title: string; error: string }) => void;
  onSelectingBest?: (data: { candidates: number }) => void;
  onBestSelected?: (data: { selected_index?: number; title?: string; post_id?: string; skipped?: boolean; reason?: string }) => void;
  onComplete?: (data: { job_id: string; articles_found: number; new_articles: number; articles_relevant: number; articles_processed: number; linkedin_published?: string }) => void;
  onError?: (error: string) => void;
}

export function streamCrawl(feedId: string, callbacks: CrawlSSECallbacks): AbortController {
  const controller = new AbortController();

  fetch(`${API_BASE}/feeds/${feedId}/crawl/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        let currentEvent = "message";
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            currentEvent = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              switch (currentEvent) {
                case "crawl_started": callbacks.onCrawlStarted?.(data); break;
                case "fetching_articles": callbacks.onFetchingArticles?.(data); break;
                case "articles_fetched": callbacks.onArticlesFetched?.(data); break;
                case "classifying": callbacks.onClassifying?.(data); break;
                case "classified": callbacks.onClassified?.(data); break;
                case "generating": callbacks.onGenerating?.(data); break;
                case "generated": callbacks.onGenerated?.(data); break;
                case "generate_error": callbacks.onGenerateError?.(data); break;
                case "selecting_best": callbacks.onSelectingBest?.(data); break;
                case "best_selected": callbacks.onBestSelected?.(data); break;
                case "complete": callbacks.onComplete?.(data); break;
                case "error": callbacks.onError?.(data.error); break;
              }
            } catch { /* ignore malformed JSON */ }
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

export async function listFeedArticles(feedId: string, limit = 50): Promise<CrawledArticle[]> {
  return json<CrawledArticle[]>(`/feeds/${feedId}/articles?limit=${limit}`);
}

export async function getCrawlLog(limit = 50): Promise<CrawlJob[]> {
  return json<CrawlJob[]>(`/feeds/crawl-log?limit=${limit}`);
}

// ---------- Diagnostics ----------

export async function listDiagnosticsChecks(apiKey: string): Promise<DiagnosticsChecksResponse> {
  return json<DiagnosticsChecksResponse>("/diagnostics/checks", {
    headers: {
      "Content-Type": "application/json",
      "X-Diagnostics-Key": apiKey,
    },
  });
}

export async function runDiagnostics(
  request: DiagnosticsRunRequest,
  apiKey: string
): Promise<DiagnosticsRunResponse> {
  return json<DiagnosticsRunResponse>("/diagnostics/run", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Diagnostics-Key": apiKey,
    },
    body: JSON.stringify(request),
  });
}

// ---------- Prompts ----------

export interface PromptInfo {
  name: string;
  description: string;
  is_customized: boolean;
  updated_at: string | null;
}

export interface PromptDetail {
  name: string;
  description: string;
  content: string;
  default_content: string;
  is_customized: boolean;
  updated_at: string | null;
}

export interface PromptTestResponse {
  output: string;
  model: string;
  prompt_name: string;
}

export async function listPrompts(): Promise<PromptInfo[]> {
  return json<PromptInfo[]>("/prompts");
}

export async function getPrompt(name: string): Promise<PromptDetail> {
  return json<PromptDetail>(`/prompts/${encodeURIComponent(name)}`);
}

export async function updatePrompt(name: string, content: string): Promise<PromptDetail> {
  return json<PromptDetail>(`/prompts/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
}

export async function resetPrompt(name: string): Promise<{ status: string; name: string }> {
  return json(`/prompts/${encodeURIComponent(name)}`, { method: "DELETE" });
}

export async function testPrompt(data: {
  prompt_name: string;
  test_input: string;
  content_override?: string;
}): Promise<PromptTestResponse> {
  return json<PromptTestResponse>("/prompts/test", {
    method: "POST",
    body: JSON.stringify(data),
  });
}
