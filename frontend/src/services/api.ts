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
  PublishedBlog,
  DiagnosticsRunRequest,
  DiagnosticsRunResponse,
  DiagnosticsChecksResponse,
} from "../types";

const API_BASE = "/api";

// ---------- auth token ----------

let _getAccessToken: (() => Promise<string>) | null = null;

/** Called once from App init to wire up the MSAL token getter. */
export function setAccessTokenGetter(fn: () => Promise<string>) {
  _getAccessToken = fn;
}

async function authHeaders(): Promise<Record<string, string>> {
  if (!_getAccessToken) return {};
  const token = await _getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// ---------- helpers ----------

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const auth = await authHeaders();
  const res = await fetch(`${API_BASE}${url}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...auth, ...init?.headers },
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

  authHeaders().then((auth) => {
  fetch(`${API_BASE}${url}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...auth },
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
  }); // end authHeaders().then()

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
  updates: Partial<{ title: string; slug: string; excerpt: string; content: string; publishedSlug: string; publishedAt: string; publishedUrl: string }>
): Promise<BlogDraft> {
  return json<BlogDraft>(`/blogs/${id}`, { method: "PUT", body: JSON.stringify(updates) });
}

export interface RelevanceResult {
  is_relevant: boolean;
  relevance_score: number;
  matched_topics: string[];
  matched_keywords: string[];
  method: string;
  reasoning: string;
}

export interface LinkedInPreview {
  post_text: string;
  hashtags: string[];
  word_count: number;
  image_url: string;
}

export interface TestReadinessResponse {
  relevance: RelevanceResult;
  linkedin_preview: LinkedInPreview | null;
}

export async function testDraftReadiness(
  id: string,
  topics: string[] = ["cloud security", "azure", "ai"]
): Promise<TestReadinessResponse> {
  return json<TestReadinessResponse>(`/blogs/${id}/test-readiness`, {
    method: "POST",
    body: JSON.stringify({ topics }),
  });
}

export async function deleteAllDrafts(): Promise<{ count: number }> {
  return json<{ count: number }>("/blogs/all", { method: "DELETE" });
}

export async function deleteDraft(id: string): Promise<void> {
  const auth = await authHeaders();
  const res = await fetch(`${API_BASE}/blogs/${id}`, { method: "DELETE", headers: auth });
  if (!res.ok && res.status !== 204) {
    throw new Error(`Delete failed: HTTP ${res.status}`);
  }
}

// ---------- Export ----------

export async function exportBlog(content: string, format: ExportFormat): Promise<Blob> {
  const auth = await authHeaders();
  const res = await fetch(`${API_BASE}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...auth },
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
  draft_id?: string;
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

export async function listPublishedBlogs(limit = 50): Promise<PublishedBlog[]> {
  return json<PublishedBlog[]>(`/blogs/published?limit=${limit}`);
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
  image_included: boolean;
  image_failed: boolean;
}

export async function composeLinkedInPost(
  data: LinkedInComposeRequest
): Promise<LinkedInComposeResponse> {
  return json("/linkedin/compose", { method: "POST", body: JSON.stringify(data) });
}

export async function generateLinkedInImage(data: {
  title: string;
  excerpt?: string;
  topics?: string[];
}): Promise<{ image_url: string }> {
  return json("/linkedin/generate-image", { method: "POST", body: JSON.stringify(data) });
}

export interface HashtagResult {
  topics: string[];
  hashtags: { tag: string; category: string; reason: string }[];
  final_tags: string[];
}

export async function regenerateHashtags(data: {
  content: string;
  title?: string;
  excerpt?: string;
}): Promise<HashtagResult> {
  return json("/linkedin/hashtags", { method: "POST", body: JSON.stringify(data) });
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
  const auth = await authHeaders();
  const res = await fetch(`${API_BASE}/linkedin/disconnect?session_id=${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
    headers: auth,
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

// ---------- Twitter/X ----------

export interface TwitterComposeRequest {
  content?: string;
  draft_id?: string;
  title?: string;
  excerpt?: string;
  blog_url?: string;
  additional_context?: string;
}

export interface TwitterComposeResponse {
  tweet_text: string;
  hashtags: string[];
  char_count: number;
  title: string;
  excerpt: string;
}

export interface TwitterOAuthStartResponse {
  session_id: string;
  state: string;
  auth_url: string;
}

export interface TwitterStatusResponse {
  connected: boolean;
  session_id: string;
  username?: string;
  expires_at?: number;
}

export interface TwitterPublishRequest {
  session_id: string;
  tweet_text?: string;
  content?: string;
  draft_id?: string;
  title?: string;
  excerpt?: string;
  blog_url?: string;
}

export interface TwitterPublishResponse {
  session_id: string;
  tweet_id: string;
  text: string;
  status_code: number;
  composed: boolean;
}

export async function composeTwitterPost(
  data: TwitterComposeRequest
): Promise<TwitterComposeResponse> {
  return json("/twitter/compose", { method: "POST", body: JSON.stringify(data) });
}

export async function startTwitterOAuth(
  sessionId?: string
): Promise<TwitterOAuthStartResponse> {
  const suffix = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  return json(`/twitter/oauth/start${suffix}`);
}

export async function getTwitterStatus(sessionId: string): Promise<TwitterStatusResponse> {
  return json(`/twitter/status?session_id=${encodeURIComponent(sessionId)}`);
}

export async function disconnectTwitter(sessionId: string): Promise<{ status: string; session_id: string }> {
  const auth = await authHeaders();
  const res = await fetch(`${API_BASE}/twitter/disconnect?session_id=${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
    headers: auth,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export async function publishTwitterPost(
  data: TwitterPublishRequest
): Promise<TwitterPublishResponse> {
  return json("/twitter/publish", { method: "POST", body: JSON.stringify(data) });
}

// ---------- Medium ----------

export interface MediumConnectRequest {
  integration_token: string;
  session_id?: string;
}

export interface MediumConnectResponse {
  session_id: string;
  author_id: string;
  username: string;
  name: string;
}

export interface MediumStatusResponse {
  connected: boolean;
  session_id: string;
  username?: string;
  author_id?: string;
}

export interface MediumPublishRequest {
  session_id: string;
  content?: string;
  draft_id?: string;
  title?: string;
  excerpt?: string;
  tags?: string[];
  blog_url?: string;
  publish_status?: string;
}

export interface MediumPublishResponse {
  session_id: string;
  post_id: string;
  url: string;
  title: string;
  publish_status: string;
  status_code: number;
}

export async function connectMedium(
  data: MediumConnectRequest
): Promise<MediumConnectResponse> {
  return json("/medium/connect", { method: "POST", body: JSON.stringify(data) });
}

export async function getMediumStatus(sessionId: string): Promise<MediumStatusResponse> {
  return json(`/medium/status?session_id=${encodeURIComponent(sessionId)}`);
}

export async function disconnectMedium(sessionId: string): Promise<{ status: string; session_id: string }> {
  const auth = await authHeaders();
  const res = await fetch(`${API_BASE}/medium/disconnect?session_id=${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
    headers: auth,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export async function publishMediumArticle(
  data: MediumPublishRequest
): Promise<MediumPublishResponse> {
  return json("/medium/publish", { method: "POST", body: JSON.stringify(data) });
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
  max_article_age_days?: number;
  max_articles_to_generate?: number;
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
    base_url: string;
    feed_url: string;
    topics: string[];
    crawl_interval_minutes: number;
    auto_publish_blog: boolean;
    auto_publish_linkedin: boolean;
    max_article_age_days: number;
    max_articles_to_generate: number;
    enabled: boolean;
  }>
): Promise<FeedSource> {
  return json<FeedSource>(`/feeds/${id}`, { method: "PUT", body: JSON.stringify(updates) });
}

export async function deleteFeed(id: string): Promise<void> {
  const auth = await authHeaders();
  const res = await fetch(`${API_BASE}/feeds/${id}`, { method: "DELETE", headers: auth });
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
  onArticlesFetched?: (data: { total: number; new: number; after_age_filter?: number; max_age_days?: number }) => void;
  onClassifying?: (data: { index: number; total: number; title: string }) => void;
  onClassified?: (data: { index: number; total: number; title: string; is_relevant: boolean; matched_topics: string[]; relevance_score: number }) => void;
  onRanking?: (data: { relevant_count: number; max_to_generate: number }) => void;
  onRanked?: (data: { top_count: number; skipped_count: number; top_titles: string[] }) => void;
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

  authHeaders().then((auth) => {
  fetch(`${API_BASE}/feeds/${feedId}/crawl/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...auth },
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
                case "ranking": callbacks.onRanking?.(data); break;
                case "ranked": callbacks.onRanked?.(data); break;
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
  }); // end authHeaders().then()

  return controller;
}

// ---------- Crawl All Feeds SSE Streaming ----------

export interface CrawlAllSSECallbacks extends CrawlSSECallbacks {
  onRunStarted?: (data: { total_feeds: number; feed_names: string[] }) => void;
  onFeedStarted?: (data: { index: number; total_feeds: number; feed_id: string; feed_name: string }) => void;
  onFeedError?: (data: { feed_id: string; feed_name: string; error: string }) => void;
  onRunComplete?: (data: { feeds_processed: number; total_found: number; total_relevant: number; total_processed: number; errors: Array<{ feed: string; error: string }> }) => void;
}

export function streamCrawlAll(callbacks: CrawlAllSSECallbacks): AbortController {
  const controller = new AbortController();

  authHeaders().then((auth) => {
  fetch(`${API_BASE}/feeds/crawl-all/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...auth },
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
                case "run_started": callbacks.onRunStarted?.(data); break;
                case "feed_started": callbacks.onFeedStarted?.(data); break;
                case "feed_error": callbacks.onFeedError?.(data); break;
                case "run_complete": callbacks.onRunComplete?.(data); break;
                // Delegate per-feed events to the standard crawl callbacks
                case "crawl_started": callbacks.onCrawlStarted?.(data); break;
                case "fetching_articles": callbacks.onFetchingArticles?.(data); break;
                case "articles_fetched": callbacks.onArticlesFetched?.(data); break;
                case "classifying": callbacks.onClassifying?.(data); break;
                case "classified": callbacks.onClassified?.(data); break;
                case "ranking": callbacks.onRanking?.(data); break;
                case "ranked": callbacks.onRanked?.(data); break;
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
  });

  return controller;
}

export async function deleteFeedArticle(feedId: string, articleId: string): Promise<void> {
  const auth = await authHeaders();
  const res = await fetch(`${API_BASE}/feeds/${feedId}/articles/${articleId}`, { method: "DELETE", headers: auth });
  if (!res.ok && res.status !== 204) {
    throw new Error(`Delete failed: HTTP ${res.status}`);
  }
}

export async function deleteAllFeedArticles(feedId: string): Promise<{ count: number }> {
  return json<{ count: number }>(`/feeds/${feedId}/articles`, { method: "DELETE" });
}

export async function deleteAllCrawledArticles(): Promise<{ count: number }> {
  return json<{ count: number }>("/feeds/articles/all", { method: "DELETE" });
}

export async function listFeedArticles(
  feedId: string,
  opts: { limit?: number; search?: string; status?: string; topic?: string; keyword?: string } = {},
): Promise<CrawledArticle[]> {
  const params = new URLSearchParams();
  params.set("limit", String(opts.limit ?? 50));
  if (opts.search) params.set("search", opts.search);
  if (opts.status) params.set("status", opts.status);
  if (opts.topic) params.set("topic", opts.topic);
  if (opts.keyword) params.set("keyword", opts.keyword);
  return json<CrawledArticle[]>(`/feeds/${feedId}/articles?${params.toString()}`);
}

export async function listRelevantArticles(limit = 30): Promise<CrawledArticle[]> {
  return json<CrawledArticle[]>(`/feeds/articles/relevant?limit=${limit}`);
}

export async function getCrawlLog(limit = 50): Promise<CrawlJob[]> {
  return json<CrawlJob[]>(`/feeds/crawl-log?limit=${limit}`);
}

// ---------- Dashboard ----------

export interface PipelineStats {
  total_articles: number;
  relevant_articles: number;
  irrelevant_articles: number;
  drafted: number;
  published: number;
  errors: number;
  skipped_rank: number;
  linkedin_posts: number;
  relevance_rate: number;
  success_rate: number;
  feeds_active: number;
  feeds_total: number;
  crawl_jobs_total: number;
  crawl_jobs_failed: number;
  avg_relevance_score: number;
  top_topics: Array<{ topic: string; count: number }>;
  daily_activity: Array<{ date: string; total: number; relevant: number; processed: number }>;
}

export interface DashboardArticle {
  id: string;
  feed_source_id: string;
  feed_name: string;
  article_url: string;
  title: string;
  is_relevant: boolean;
  relevance_score: number;
  matched_topics: string[];
  matched_keywords: string[];
  draft_id: string;
  linkedin_post_id: string;
  status: string;
  crawled_at: string;
  hero_image_url: string;
  retry_count: number;
  last_error: string;
}

export interface ArticleActionResult {
  article_id: string;
  status: string;
  message: string;
  draft_id?: string;
  linkedin_post_id?: string;
}

export async function getDashboardStats(days = 7): Promise<PipelineStats> {
  return json<PipelineStats>(`/dashboard/stats?days=${days}`);
}

export async function getDashboardArticles(params: {
  days?: number;
  status?: string;
  feed_id?: string;
  relevant_only?: boolean;
  search?: string;
  topic?: string;
  keyword?: string;
  limit?: number;
} = {}): Promise<DashboardArticle[]> {
  const qs = new URLSearchParams();
  if (params.days !== undefined) qs.set("days", String(params.days));
  if (params.status) qs.set("status", params.status);
  if (params.feed_id) qs.set("feed_id", params.feed_id);
  if (params.relevant_only) qs.set("relevant_only", "true");
  if (params.search) qs.set("search", params.search);
  if (params.topic) qs.set("topic", params.topic);
  if (params.keyword) qs.set("keyword", params.keyword);
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  return json<DashboardArticle[]>(`/dashboard/articles?${qs.toString()}`);
}

export async function regenerateArticle(articleId: string): Promise<ArticleActionResult> {
  return json<ArticleActionResult>(`/dashboard/articles/${articleId}/regenerate`, { method: "POST" });
}

export async function promoteToLinkedIn(articleId: string): Promise<ArticleActionResult> {
  return json<ArticleActionResult>(`/dashboard/articles/${articleId}/linkedin`, { method: "POST" });
}

// ---------- Version History ----------

export interface VersionSummary {
  id: string;
  draftId: string;
  title: string;
  contentLength: number;
  trigger: string;
  createdAt: string;
}

export interface VersionFull extends VersionSummary {
  content: string;
}

export async function listDraftVersions(draftId: string, limit = 20): Promise<VersionSummary[]> {
  return json<VersionSummary[]>(`/blogs/${draftId}/versions?limit=${limit}`);
}

export async function getDraftVersion(draftId: string, versionId: string): Promise<VersionFull> {
  return json<VersionFull>(`/blogs/${draftId}/versions/${versionId}`);
}

export async function createDraftVersion(draftId: string): Promise<VersionSummary> {
  return json<VersionSummary>(`/blogs/${draftId}/versions`, { method: "POST" });
}

export async function restoreDraftVersion(draftId: string, versionId: string): Promise<BlogDraft> {
  return json<BlogDraft>(`/blogs/${draftId}/versions/${versionId}/restore`, { method: "POST" });
}

// ---------- Scheduler Status ----------

export interface ScheduledJob {
  id: string;
  feed_name: string;
  feed_id: string;
  interval_minutes: number;
  next_run: string;
  enabled: boolean;
}

export interface SchedulerStatus {
  running: boolean;
  jobs: ScheduledJob[];
}

export async function getSchedulerStatus(): Promise<SchedulerStatus> {
  return json<SchedulerStatus>("/dashboard/scheduler");
}

// ---------- Feed Health ----------

export interface FeedHealthItem {
  feed_id: string;
  feed_name: string;
  enabled: boolean;
  last_crawled_at: string;
  total_articles: number;
  relevant_articles: number;
  error_articles: number;
  relevance_rate: number;
  last_error: string;
  crawl_success_rate: number;
  avg_articles_per_crawl: number;
}

export async function getFeedHealth(): Promise<FeedHealthItem[]> {
  return json<FeedHealthItem[]>("/dashboard/feed-health");
}

// ---------- Bulk Actions ----------

export interface BulkActionResult {
  total: number;
  succeeded: number;
  failed: number;
  results: ArticleActionResult[];
}

export async function bulkGenerateArticles(articleIds: string[]): Promise<BulkActionResult> {
  return json<BulkActionResult>("/dashboard/articles/bulk-generate", {
    method: "POST",
    body: JSON.stringify({ article_ids: articleIds }),
  });
}

export async function bulkLinkedInArticles(articleIds: string[]): Promise<BulkActionResult> {
  return json<BulkActionResult>("/dashboard/articles/bulk-linkedin", {
    method: "POST",
    body: JSON.stringify({ article_ids: articleIds }),
  });
}

// ---------- Diagnostics ----------

export interface EnvVar {
  name: string;
  value: string;
  is_set: boolean;
  is_secret: boolean;
}

export interface EnvGroup {
  category: string;
  vars: EnvVar[];
}

export interface EnvConfigResponse {
  groups: EnvGroup[];
}

export async function getEnvConfig(apiKey: string): Promise<EnvConfigResponse> {
  return json<EnvConfigResponse>("/diagnostics/env", {
    headers: {
      "Content-Type": "application/json",
      "X-Diagnostics-Key": apiKey,
    },
  });
}

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

// ---------- Keywords ----------

export interface TopicKeywordsInfo {
  topic: string;
  keywords: string[];
  keyword_count: number;
  is_customized: boolean;
}

export interface TopicKeywordsDetail {
  topic: string;
  keywords: string[];
  default_keywords: string[];
  keyword_count: number;
  is_customized: boolean;
}

export async function listKeywords(): Promise<TopicKeywordsInfo[]> {
  return json<TopicKeywordsInfo[]>("/keywords");
}

export async function getTopicKeywords(topic: string): Promise<TopicKeywordsDetail> {
  return json<TopicKeywordsDetail>(`/keywords/${encodeURIComponent(topic)}`);
}

export async function updateTopicKeywords(
  topic: string,
  keywords: string[]
): Promise<TopicKeywordsDetail> {
  return json<TopicKeywordsDetail>(`/keywords/${encodeURIComponent(topic)}`, {
    method: "PUT",
    body: JSON.stringify({ keywords }),
  });
}

export async function resetTopicKeywords(
  topic: string
): Promise<{ status: string; topic: string }> {
  return json(`/keywords/${encodeURIComponent(topic)}`, { method: "DELETE" });
}

export async function addTopicKeywords(
  topic: string,
  keywords: string[]
): Promise<TopicKeywordsDetail> {
  return json<TopicKeywordsDetail>(`/keywords/${encodeURIComponent(topic)}/add`, {
    method: "POST",
    body: JSON.stringify({ keywords }),
  });
}

// ---------- User Profile ----------

export interface UserSettings {
  image_handling: "store_image" | "regenerate_on_share";
  blog_base_url: string;
}

export interface UserProfile {
  id: string;
  name: string;
  email: string;
  linkedinSessionId: string;
  settings: UserSettings;
  createdAt: string;
  lastLoginAt: string;
}

export async function getUserProfile(): Promise<UserProfile> {
  return json<UserProfile>("/user/me");
}

export async function getUserSettings(): Promise<UserSettings> {
  return json<UserSettings>("/user/settings");
}

export async function updateUserSettings(settings: Partial<UserSettings>): Promise<UserSettings> {
  return json<UserSettings>("/user/settings", { method: "PUT", body: JSON.stringify(settings) });
}

// ---------- Content Scheduling ----------

export interface ScheduledPublish {
  id: string;
  draftId: string;
  scheduledAt: string;
  platforms: string[];
  status: "pending" | "completed" | "failed" | "cancelled";
  createdAt: string;
  completedAt: string;
  error: string;
}

export async function createScheduledPublish(data: {
  draft_id: string;
  scheduled_at: string;
  platforms: string[];
}): Promise<ScheduledPublish> {
  return json("/schedule", { method: "POST", body: JSON.stringify(data) });
}

export async function listScheduledPublishes(status?: string): Promise<ScheduledPublish[]> {
  const qs = status ? `?status=${status}` : "";
  return json<ScheduledPublish[]>(`/schedule${qs}`);
}

export async function cancelScheduledPublish(id: string): Promise<{ status: string }> {
  const auth = await authHeaders();
  const res = await fetch(`${API_BASE}/schedule/${id}`, { method: "DELETE", headers: auth });
  if (!res.ok) throw new Error(`Cancel failed: HTTP ${res.status}`);
  return res.json();
}

// ---------- Voice Profiles ----------

export interface VoiceProfile {
  id: string;
  userId: string;
  name: string;
  description: string;
  tone: string;
  styleNotes: string;
  sampleText: string;
  isDefault: boolean;
  createdAt: string;
  updatedAt: string;
}

export async function listVoiceProfiles(): Promise<VoiceProfile[]> {
  return json<VoiceProfile[]>("/voice-profiles");
}

export async function createVoiceProfile(data: {
  name: string;
  description: string;
  tone: string;
  style_notes: string;
  sample_text: string;
  is_default?: boolean;
}): Promise<VoiceProfile> {
  return json("/voice-profiles", { method: "POST", body: JSON.stringify(data) });
}

export async function updateVoiceProfile(
  id: string,
  updates: Partial<{ name: string; description: string; tone: string; style_notes: string; sample_text: string }>
): Promise<VoiceProfile> {
  return json(`/voice-profiles/${id}`, { method: "PUT", body: JSON.stringify(updates) });
}

export async function deleteVoiceProfile(id: string): Promise<void> {
  const auth = await authHeaders();
  const res = await fetch(`${API_BASE}/voice-profiles/${id}`, { method: "DELETE", headers: auth });
  if (!res.ok) throw new Error(`Delete failed: HTTP ${res.status}`);
}

export async function setDefaultVoiceProfile(id: string): Promise<VoiceProfile> {
  return json(`/voice-profiles/${id}/default`, { method: "POST" });
}

// ---------- Content Templates ----------

export interface ContentTemplate {
  id: string;
  name: string;
  description: string;
  category: string;
  content: string;
  tags: string[];
  isBuiltIn: boolean;
  createdAt: string;
  updatedAt: string;
}

export async function listTemplates(category?: string): Promise<ContentTemplate[]> {
  const qs = category ? `?category=${encodeURIComponent(category)}` : "";
  return json<ContentTemplate[]>(`/templates${qs}`);
}

export async function createTemplate(data: {
  name: string;
  description: string;
  category: string;
  content: string;
  tags?: string[];
}): Promise<ContentTemplate> {
  return json("/templates", { method: "POST", body: JSON.stringify(data) });
}

export async function updateTemplate(
  id: string,
  updates: Partial<{ name: string; description: string; category: string; content: string; tags: string[] }>
): Promise<ContentTemplate> {
  return json(`/templates/${id}`, { method: "PUT", body: JSON.stringify(updates) });
}

export async function deleteTemplate(id: string): Promise<void> {
  const auth = await authHeaders();
  const res = await fetch(`${API_BASE}/templates/${id}`, { method: "DELETE", headers: auth });
  if (!res.ok) throw new Error(`Delete failed: HTTP ${res.status}`);
}

// ---------- Bulk Import ----------

export interface ImportResult {
  total: number;
  succeeded: number;
  failed: number;
  draft_ids: string[];
  errors: Array<{ index: number; error: string }>;
}

export async function importMarkdown(entries: Array<{
  title: string;
  content: string;
  source_url?: string;
  tags?: string[];
}>): Promise<ImportResult> {
  return json("/import/markdown", { method: "POST", body: JSON.stringify({ entries }) });
}

export async function importFromUrls(urls: string[]): Promise<ImportResult> {
  return json("/import/urls", { method: "POST", body: JSON.stringify({ urls }) });
}

export async function importWordpress(xml_content: string): Promise<ImportResult> {
  return json("/import/wordpress", { method: "POST", body: JSON.stringify({ xml_content }) });
}

// ---------- Post Analytics ----------

export interface PostAnalytics {
  slug: string;
  days: number;
  events: Record<string, number>;
}

export interface AnalyticsOverviewItem {
  slug: string;
  events: Record<string, number>;
}

export async function trackEvent(slug: string, eventType: string, platform = "blog"): Promise<void> {
  await json("/analytics/event", {
    method: "POST",
    body: JSON.stringify({ slug, event_type: eventType, platform }),
  });
}

export async function getPostAnalytics(slug: string, days = 30): Promise<PostAnalytics> {
  return json<PostAnalytics>(`/analytics/post/${encodeURIComponent(slug)}?days=${days}`);
}

export async function getAnalyticsOverview(days = 30): Promise<AnalyticsOverviewItem[]> {
  return json<AnalyticsOverviewItem[]>(`/analytics/overview?days=${days}`);
}

// ---------- SEO Tracking ----------

export interface SEOSnapshot {
  id: string;
  slug: string;
  data: Record<string, unknown>;
  createdAt: string;
}

export async function analyzeSEO(slug: string): Promise<{ slug: string; data: Record<string, unknown>; snapshot_id: string }> {
  return json(`/seo/analyze/${encodeURIComponent(slug)}`, { method: "POST" });
}

export async function getSEOHistory(slug: string, limit = 20): Promise<SEOSnapshot[]> {
  return json<SEOSnapshot[]>(`/seo/history/${encodeURIComponent(slug)}?limit=${limit}`);
}

export async function getSEOOverview(): Promise<SEOSnapshot[]> {
  return json<SEOSnapshot[]>("/seo/overview");
}

// ---------- Comments ----------

export interface Comment {
  id: string;
  draftId: string;
  userId: string;
  userName: string;
  content: string;
  lineNumber: number | null;
  parentId: string;
  resolved: boolean;
  createdAt: string;
  updatedAt: string;
}

export async function listComments(draftId: string): Promise<Comment[]> {
  return json<Comment[]>(`/comments/${draftId}`);
}

export async function createComment(data: {
  draft_id: string;
  content: string;
  line_number?: number;
  parent_id?: string;
}): Promise<Comment> {
  return json("/comments", { method: "POST", body: JSON.stringify(data) });
}

export async function updateComment(
  id: string,
  updates: { content?: string; resolved?: boolean }
): Promise<Comment> {
  return json(`/comments/${id}`, { method: "PUT", body: JSON.stringify(updates) });
}

export async function deleteComment(id: string): Promise<void> {
  const auth = await authHeaders();
  const res = await fetch(`${API_BASE}/comments/${id}`, { method: "DELETE", headers: auth });
  if (!res.ok) throw new Error(`Delete failed: HTTP ${res.status}`);
}

// ---------- Newsletter ----------

export interface NewsletterPreview {
  subject: string;
  html_body: string;
  plain_text: string;
}

export async function previewNewsletter(draftId: string): Promise<NewsletterPreview> {
  return json("/newsletter/preview", { method: "POST", body: JSON.stringify({ draft_id: draftId }) });
}

export async function sendNewsletter(data: {
  draft_id: string;
  provider: "mailchimp" | "convertkit" | "smtp";
  config: Record<string, string>;
}): Promise<{ status: string; provider_response: Record<string, unknown> }> {
  return json("/newsletter/send", { method: "POST", body: JSON.stringify(data) });
}
