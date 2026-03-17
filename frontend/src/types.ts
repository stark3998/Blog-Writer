export interface BlogDraft {
  id: string;
  title: string;
  slug: string;
  excerpt: string;
  content: string;
  sourceUrl: string;
  sourceType: string;
  createdAt: string;
  updatedAt: string;
}

export interface GenerateResult {
  mdx_content: string;
  slug: string;
  title: string;
  excerpt: string;
  source_url: string;
  source_type: string;
}

export type ExportFormat = "md" | "html" | "pdf" | "docx" | "mdx";

export interface FeedSource {
  id: string;
  name: string;
  base_url: string;
  feed_url: string;
  feed_type: string;
  topics: string[];
  auto_publish_blog: boolean;
  auto_publish_linkedin: boolean;
  crawl_interval_minutes: number;
  enabled: boolean;
  last_crawled_at: string;
  created_at: string;
  updated_at: string;
}

export interface CrawledArticle {
  id: string;
  feed_source_id: string;
  article_url: string;
  title: string;
  is_relevant: boolean;
  relevance_score: number;
  matched_topics: string[];
  draft_id: string;
  status: string;
  crawled_at: string;
}

export interface CrawlJob {
  id: string;
  feed_source_id: string;
  started_at: string;
  completed_at: string;
  articles_found: number;
  articles_relevant: number;
  articles_processed: number;
  status: string;
  error: string;
}

export interface CrawlResult {
  job_id: string;
  feed_source_id: string;
  articles_found: number;
  new_articles: number;
  articles_relevant: number;
  articles_processed: number;
  status: string;
}

export interface FeedDiscoverResult {
  feed_url: string;
  feed_type: string;
  site_name: string;
}

export type DiagnosticStatus = "pass" | "fail" | "warn" | "skip";

export interface DiagnosticsCheckFlags {
  linkedin: boolean;
  foundry_config: boolean;
  text_generation: boolean;
  image_generation: boolean;
  cosmos: boolean;
  publish_dry_run: boolean;
}

export interface DiagnosticsCheckResult {
  key: string;
  label: string;
  status: DiagnosticStatus;
  severity: "info" | "warning" | "error";
  billable: boolean;
  duration_ms: number;
  recommendation: string;
  details: Record<string, unknown>;
}

export interface DiagnosticsSummary {
  total: number;
  passed: number;
  failed: number;
  warned: number;
  skipped: number;
}

export interface DiagnosticsRunRequest {
  session_id?: string;
  include_billable?: boolean;
  checks?: Partial<DiagnosticsCheckFlags>;
}

export interface DiagnosticsRunResponse {
  timestamp: string;
  overall_status: "healthy" | "degraded" | "unhealthy";
  summary: DiagnosticsSummary;
  checks: DiagnosticsCheckResult[];
}

export interface DiagnosticsCheckMetadata {
  key: string;
  label: string;
  billable: boolean;
  description: string;
}

export interface DiagnosticsChecksResponse {
  checks: DiagnosticsCheckMetadata[];
}
