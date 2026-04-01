import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  getDashboardStats,
  getDashboardArticles,
  regenerateArticle,
  promoteToLinkedIn,
  bulkGenerateArticles,
  bulkLinkedInArticles,
  listFeeds,
} from "../services/api";
import type { FeedSource } from "../types";
import type { PipelineStats, DashboardArticle } from "../services/api";
import { toast } from "../store/toastStore";
import {
  BarChart3,
  TrendingUp,
  AlertTriangle,
  CheckCircle2,
  Linkedin,
  FileText,
  RefreshCw,
  Loader2,
  ExternalLink,
  ChevronUp,
  ChevronDown,
  Filter,
  Newspaper,
  Zap,
  Eye,
  RotateCw,
  Square,
  CheckSquare,
} from "lucide-react";

const TIME_RANGES = [
  { label: "24h", value: 1 },
  { label: "7d", value: 7 },
  { label: "30d", value: 30 },
  { label: "90d", value: 90 },
  { label: "All", value: 0 },
];

const STATUS_FILTERS = [
  { label: "All", value: "" },
  { label: "Published", value: "published" },
  { label: "Drafted", value: "drafted" },
  { label: "Skipped", value: "skipped" },
  { label: "Ranked Out", value: "skipped_rank" },
  { label: "Errors", value: "error" },
];

type SortField = "relevance_score" | "crawled_at" | "title" | "status" | "feed_name";
type SortDir = "asc" | "desc";

function ScoreBar({ score, width = 80 }: { score: number; width?: number }) {
  const pct = Math.round(score * 100);
  const color =
    score >= 0.8
      ? "bg-emerald-500"
      : score >= 0.6
      ? "bg-green-400"
      : score >= 0.3
      ? "bg-amber-400"
      : "bg-red-400";

  return (
    <div className="flex items-center gap-2">
      <div
        className="h-2 rounded-full bg-gray-100 overflow-hidden"
        style={{ width }}
      >
        <div
          className={`h-full rounded-full ${color} transition-all`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-[11px] font-mono text-gray-500 w-9 text-right">
        {score.toFixed(2)}
      </span>
    </div>
  );
}

function ScoreLabel({ score }: { score: number }) {
  if (score >= 0.8) return <span className="text-emerald-600 font-semibold text-[10px]">Very High</span>;
  if (score >= 0.6) return <span className="text-green-600 font-semibold text-[10px]">High</span>;
  if (score >= 0.3) return <span className="text-amber-600 font-semibold text-[10px]">Medium</span>;
  return <span className="text-red-500 font-semibold text-[10px]">Low</span>;
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    published: "bg-emerald-50 text-emerald-600 border-emerald-200/60",
    drafted: "bg-cyan-50 text-cyan-600 border-cyan-200/60",
    skipped: "bg-gray-50 text-gray-400 border-gray-200/60",
    skipped_rank: "bg-violet-50 text-violet-500 border-violet-200/60",
    error: "bg-red-50 text-red-500 border-red-200/60",
    pending: "bg-amber-50 text-amber-500 border-amber-200/60",
  };
  return (
    <span
      className={`px-2 py-0.5 rounded-full text-[10px] font-semibold border ${
        styles[status] ?? "bg-gray-50 text-gray-400 border-gray-200/60"
      }`}
    >
      {status.replace("_", " ")}
    </span>
  );
}

function StatCard({
  label,
  value,
  icon: Icon,
  color = "text-gray-700",
  sub,
}: {
  label: string;
  value: string | number;
  icon: typeof BarChart3;
  color?: string;
  sub?: string;
}) {
  return (
    <div className="p-4 rounded-2xl bg-white border border-gray-200/60 shadow-sm">
      <div className="flex items-center gap-3 mb-2">
        <div className={`w-9 h-9 rounded-xl flex items-center justify-center bg-gray-50 ${color}`}>
          <Icon className="w-4.5 h-4.5" />
        </div>
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{label}</span>
      </div>
      <p className="text-2xl font-bold text-gray-900">{value}</p>
      {sub && <p className="text-[11px] text-gray-400 mt-0.5">{sub}</p>}
    </div>
  );
}

function MiniBarChart({ data }: { data: Array<{ date: string; total: number; relevant: number; processed: number }> }) {
  if (!data.length) return <p className="text-xs text-gray-400 py-4 text-center">No activity data</p>;

  const maxVal = Math.max(...data.map((d) => d.total), 1);
  const barW = Math.max(8, Math.min(24, Math.floor(400 / data.length)));

  return (
    <div className="flex items-end gap-1 h-[100px] px-2 overflow-x-auto">
      {data.map((d) => (
        <div key={d.date} className="flex flex-col items-center gap-0.5" style={{ minWidth: barW }}>
          <div className="flex flex-col-reverse w-full gap-px" style={{ height: 80 }}>
            <div
              className="w-full bg-gray-200 rounded-t"
              style={{ height: `${(d.total / maxVal) * 100}%` }}
              title={`${d.date}: ${d.total} total`}
            />
            <div
              className="w-full bg-emerald-400 rounded-t absolute bottom-0"
              style={{ height: `${(d.relevant / maxVal) * 100}%`, position: "relative" }}
              title={`${d.relevant} relevant`}
            />
          </div>
          <span className="text-[8px] text-gray-400 -rotate-45 origin-top-left whitespace-nowrap">
            {d.date.slice(5)}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function Dashboard() {
  const [stats, setStats] = useState<PipelineStats | null>(null);
  const [articles, setArticles] = useState<DashboardArticle[]>([]);
  const [feeds, setFeeds] = useState<FeedSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(7);
  const [statusFilter, setStatusFilter] = useState("");
  const [feedFilter, setFeedFilter] = useState("");
  const [relevantOnly, setRelevantOnly] = useState(false);
  const [sortField, setSortField] = useState<SortField>("relevance_score");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [actionLoading, setActionLoading] = useState<Record<string, string>>({});
  const [expandedArticle, setExpandedArticle] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkLoading, setBulkLoading] = useState(false);

  const loadData = async () => {
    setLoading(true);
    try {
      const [s, a, f] = await Promise.all([
        getDashboardStats(days),
        getDashboardArticles({ days, status: statusFilter, feed_id: feedFilter, relevant_only: relevantOnly, limit: 500 }),
        listFeeds(),
      ]);
      setStats(s);
      setArticles(a);
      setFeeds(f);
    } catch {
      // silently handle
    }
    setLoading(false);
  };

  useEffect(() => {
    loadData();
  }, [days, statusFilter, feedFilter, relevantOnly]);

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortField(field);
      setSortDir("desc");
    }
  };

  const sortedArticles = [...articles].sort((a, b) => {
    const dir = sortDir === "asc" ? 1 : -1;
    switch (sortField) {
      case "relevance_score":
        return (a.relevance_score - b.relevance_score) * dir;
      case "crawled_at":
        return a.crawled_at.localeCompare(b.crawled_at) * dir;
      case "title":
        return a.title.localeCompare(b.title) * dir;
      case "status":
        return a.status.localeCompare(b.status) * dir;
      case "feed_name":
        return a.feed_name.localeCompare(b.feed_name) * dir;
      default:
        return 0;
    }
  });

  const handleRegenerate = async (id: string) => {
    setActionLoading((prev) => ({ ...prev, [id]: "regenerate" }));
    try {
      const result = await regenerateArticle(id);
      setArticles((prev) =>
        prev.map((a) =>
          a.id === id ? { ...a, status: result.status, draft_id: result.draft_id ?? a.draft_id } : a
        )
      );
      toast.success("Blog generated", result.message);
    } catch (err) {
      toast.error("Generation failed", err instanceof Error ? err.message : "Unknown error");
    }
    setActionLoading((prev) => { const n = { ...prev }; delete n[id]; return n; });
  };

  const handleLinkedIn = async (id: string) => {
    setActionLoading((prev) => ({ ...prev, [id]: "linkedin" }));
    try {
      const result = await promoteToLinkedIn(id);
      setArticles((prev) =>
        prev.map((a) =>
          a.id === id
            ? { ...a, status: result.status, linkedin_post_id: result.linkedin_post_id ?? a.linkedin_post_id }
            : a
        )
      );
      toast.success("LinkedIn post published", result.message);
    } catch (err) {
      toast.error("LinkedIn publish failed", err instanceof Error ? err.message : "Unknown error");
    }
    setActionLoading((prev) => { const n = { ...prev }; delete n[id]; return n; });
  };

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selected.size === sortedArticles.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(sortedArticles.map((a) => a.id)));
    }
  };

  const handleBulkGenerate = async () => {
    const ids = [...selected].filter((id) => {
      const a = articles.find((x) => x.id === id);
      return a && !a.draft_id && (a.status === "skipped" || a.status === "skipped_rank" || a.status === "error");
    });
    if (!ids.length) { toast.warning("No eligible articles", "Select skipped/error articles without drafts"); return; }
    setBulkLoading(true);
    try {
      const result = await bulkGenerateArticles(ids);
      toast.success(`Generated ${result.succeeded} blogs`, result.failed > 0 ? `${result.failed} failed` : undefined);
      loadData();
      setSelected(new Set());
    } catch { toast.error("Bulk generate failed"); }
    setBulkLoading(false);
  };

  const handleBulkLinkedIn = async () => {
    const ids = [...selected].filter((id) => {
      const a = articles.find((x) => x.id === id);
      return a && a.draft_id && !a.linkedin_post_id;
    });
    if (!ids.length) { toast.warning("No eligible articles", "Select articles with drafts but no LinkedIn post"); return; }
    setBulkLoading(true);
    try {
      const result = await bulkLinkedInArticles(ids);
      toast.success(`Posted ${result.succeeded} to LinkedIn`, result.failed > 0 ? `${result.failed} failed` : undefined);
      loadData();
      setSelected(new Set());
    } catch { toast.error("Bulk LinkedIn failed"); }
    setBulkLoading(false);
  };

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return <ChevronDown className="w-3 h-3 text-gray-300" />;
    return sortDir === "asc" ? (
      <ChevronUp className="w-3 h-3 text-indigo-500" />
    ) : (
      <ChevronDown className="w-3 h-3 text-indigo-500" />
    );
  };

  return (
      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* Header with time range */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-2xl font-bold text-gray-900 flex items-center gap-3">
              <BarChart3 className="w-6 h-6 text-indigo-500" />
              Analytics
            </h2>
            <p className="text-sm text-gray-500 mt-1">
              Article ratings, relevance scores, and pipeline health at a glance.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {TIME_RANGES.map((r) => (
              <button
                key={r.value}
                onClick={() => setDays(r.value)}
                className={`px-3 py-1.5 rounded-xl text-xs font-semibold border transition-all ${
                  days === r.value
                    ? "bg-indigo-50 border-indigo-200 text-indigo-600"
                    : "bg-white border-gray-200/60 text-gray-400 hover:border-gray-300"
                }`}
              >
                {r.label}
              </button>
            ))}
            <button
              onClick={loadData}
              disabled={loading}
              className="p-2 rounded-xl text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 border border-gray-200/60 transition-all ml-2"
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            </button>
          </div>
        </div>

        {loading && !stats ? (
          <div className="flex justify-center py-20">
            <Loader2 className="w-6 h-6 animate-spin text-indigo-500" />
          </div>
        ) : stats ? (
          <>
            {/* Stats Grid */}
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 mb-6">
              <StatCard
                label="Total Articles"
                value={stats.total_articles}
                icon={Newspaper}
                color="text-indigo-500"
              />
              <StatCard
                label="Relevant"
                value={stats.relevant_articles}
                icon={TrendingUp}
                color="text-emerald-500"
                sub={`${(stats.relevance_rate * 100).toFixed(1)}% rate`}
              />
              <StatCard
                label="Published"
                value={stats.published}
                icon={CheckCircle2}
                color="text-green-500"
              />
              <StatCard
                label="Drafted"
                value={stats.drafted}
                icon={FileText}
                color="text-cyan-500"
              />
              <StatCard
                label="LinkedIn"
                value={stats.linkedin_posts}
                icon={Linkedin}
                color="text-blue-600"
              />
              <StatCard
                label="Errors"
                value={stats.errors}
                icon={AlertTriangle}
                color={stats.errors > 0 ? "text-red-500" : "text-gray-400"}
                sub={stats.crawl_jobs_failed > 0 ? `${stats.crawl_jobs_failed} crawl failures` : undefined}
              />
            </div>

            {/* Pipeline Health + Activity */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-8">
              {/* Health overview */}
              <div className="p-5 rounded-2xl bg-white border border-gray-200/60 shadow-sm">
                <h3 className="text-sm font-bold text-gray-900 mb-4 flex items-center gap-2">
                  <Zap className="w-4 h-4 text-indigo-500" />
                  Pipeline Health
                </h3>
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-gray-500">Success Rate</span>
                    <div className="flex items-center gap-2">
                      <div className="w-24 h-2 rounded-full bg-gray-100 overflow-hidden">
                        <div
                          className={`h-full rounded-full ${stats.success_rate >= 0.8 ? "bg-emerald-500" : stats.success_rate >= 0.5 ? "bg-amber-400" : "bg-red-400"}`}
                          style={{ width: `${stats.success_rate * 100}%` }}
                        />
                      </div>
                      <span className="text-xs font-mono text-gray-600">{(stats.success_rate * 100).toFixed(0)}%</span>
                    </div>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-gray-500">Avg Relevance</span>
                    <div className="flex items-center gap-2">
                      <ScoreBar score={stats.avg_relevance_score} width={96} />
                    </div>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-gray-500">Active Feeds</span>
                    <span className="text-xs font-semibold text-gray-700">{stats.feeds_active} / {stats.feeds_total}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-gray-500">Crawl Jobs</span>
                    <span className="text-xs font-semibold text-gray-700">{stats.crawl_jobs_total}
                      {stats.crawl_jobs_failed > 0 && <span className="text-red-500 ml-1">({stats.crawl_jobs_failed} failed)</span>}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-gray-500">Ranked Out</span>
                    <span className="text-xs font-semibold text-violet-500">{stats.skipped_rank}</span>
                  </div>
                </div>
              </div>

              {/* Top Topics */}
              <div className="p-5 rounded-2xl bg-white border border-gray-200/60 shadow-sm">
                <h3 className="text-sm font-bold text-gray-900 mb-4 flex items-center gap-2">
                  <Filter className="w-4 h-4 text-indigo-500" />
                  Top Topics
                </h3>
                {stats.top_topics.length > 0 ? (
                  <div className="space-y-2">
                    {stats.top_topics.slice(0, 8).map((t) => {
                      const maxCount = stats.top_topics[0]?.count || 1;
                      return (
                        <div key={t.topic} className="flex items-center gap-2">
                          <span className="text-xs text-gray-600 w-28 truncate">{t.topic}</span>
                          <div className="flex-1 h-2 rounded-full bg-gray-100 overflow-hidden">
                            <div
                              className="h-full rounded-full bg-indigo-400"
                              style={{ width: `${(t.count / maxCount) * 100}%` }}
                            />
                          </div>
                          <span className="text-[10px] font-mono text-gray-400 w-6 text-right">{t.count}</span>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <p className="text-xs text-gray-400 py-4 text-center">No topic data yet</p>
                )}
              </div>

              {/* Daily Activity */}
              <div className="p-5 rounded-2xl bg-white border border-gray-200/60 shadow-sm">
                <h3 className="text-sm font-bold text-gray-900 mb-4 flex items-center gap-2">
                  <BarChart3 className="w-4 h-4 text-indigo-500" />
                  Daily Activity
                </h3>
                <MiniBarChart data={stats.daily_activity} />
                <div className="flex items-center justify-center gap-4 mt-3">
                  <div className="flex items-center gap-1">
                    <div className="w-2 h-2 rounded-full bg-gray-200" />
                    <span className="text-[10px] text-gray-400">Total</span>
                  </div>
                  <div className="flex items-center gap-1">
                    <div className="w-2 h-2 rounded-full bg-emerald-400" />
                    <span className="text-[10px] text-gray-400">Relevant</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Bulk Action Bar */}
            {selected.size > 0 && (
              <div className="mb-4 flex items-center gap-3 px-4 py-3 rounded-2xl bg-indigo-50 border border-indigo-200/60 animate-fade-in-down">
                <span className="text-sm font-semibold text-indigo-700">{selected.size} selected</span>
                <button
                  onClick={handleBulkGenerate}
                  disabled={bulkLoading}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold text-cyan-700 bg-cyan-50 hover:bg-cyan-100 border border-cyan-200/60 transition-all disabled:opacity-40"
                >
                  {bulkLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RotateCw className="w-3.5 h-3.5" />}
                  Generate Blogs
                </button>
                <button
                  onClick={handleBulkLinkedIn}
                  disabled={bulkLoading}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold text-blue-700 bg-blue-50 hover:bg-blue-100 border border-blue-200/60 transition-all disabled:opacity-40"
                >
                  {bulkLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Linkedin className="w-3.5 h-3.5" />}
                  Post to LinkedIn
                </button>
                <button
                  onClick={() => setSelected(new Set())}
                  className="ml-auto text-xs font-medium text-indigo-500 hover:text-indigo-700 transition-colors"
                >
                  Clear selection
                </button>
              </div>
            )}

            {/* Articles Table */}
            <div className="rounded-2xl bg-white border border-gray-200/60 shadow-sm overflow-hidden">
              {/* Table Header / Filters */}
              <div className="p-4 border-b border-gray-100 flex items-center justify-between flex-wrap gap-3">
                <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
                  <Eye className="w-4 h-4 text-indigo-500" />
                  Crawled Articles
                  <span className="text-xs font-semibold text-gray-400 bg-gray-100 px-2.5 py-0.5 rounded-full">
                    {articles.length}
                  </span>
                </h3>
                <div className="flex items-center gap-2 flex-wrap">
                  {/* Status filter */}
                  <div className="flex items-center gap-1">
                    {STATUS_FILTERS.map((sf) => (
                      <button
                        key={sf.value}
                        onClick={() => setStatusFilter(sf.value)}
                        className={`px-2.5 py-1 rounded-lg text-[10px] font-semibold border transition-all ${
                          statusFilter === sf.value
                            ? "bg-indigo-50 border-indigo-200 text-indigo-600"
                            : "bg-white border-gray-200/60 text-gray-400 hover:border-gray-300"
                        }`}
                      >
                        {sf.label}
                      </button>
                    ))}
                  </div>
                  {/* Feed filter */}
                  <select
                    value={feedFilter}
                    onChange={(e) => setFeedFilter(e.target.value)}
                    className="px-2 py-1 rounded-lg bg-white border border-gray-200/60 text-xs text-gray-600 outline-none"
                  >
                    <option value="">All Feeds</option>
                    {feeds.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.name}
                      </option>
                    ))}
                  </select>
                  {/* Relevant only toggle */}
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <button
                      onClick={() => setRelevantOnly(!relevantOnly)}
                      className={`w-8 h-4 rounded-full transition-all relative ${
                        relevantOnly ? "bg-indigo-500" : "bg-gray-200"
                      }`}
                    >
                      <span
                        className={`absolute top-0.5 w-3 h-3 rounded-full bg-white shadow-sm transition-all ${
                          relevantOnly ? "left-[16px]" : "left-0.5"
                        }`}
                      />
                    </button>
                    <span className="text-[10px] text-gray-500 font-medium">Relevant only</span>
                  </label>
                </div>
              </div>

              {/* Table */}
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="bg-gray-50/80 text-gray-500 uppercase tracking-wide text-[10px]">
                      <th className="px-2 py-2.5 w-8">
                        <button onClick={toggleSelectAll} className="text-gray-400 hover:text-indigo-500 transition-colors">
                          {selected.size === sortedArticles.length && sortedArticles.length > 0 ? (
                            <CheckSquare className="w-4 h-4 text-indigo-500" />
                          ) : (
                            <Square className="w-4 h-4" />
                          )}
                        </button>
                      </th>
                      <th className="px-4 py-2.5 text-left font-semibold w-[340px]">
                        <button className="flex items-center gap-1" onClick={() => handleSort("title")}>
                          Article <SortIcon field="title" />
                        </button>
                      </th>
                      <th className="px-3 py-2.5 text-left font-semibold">
                        <button className="flex items-center gap-1" onClick={() => handleSort("feed_name")}>
                          Feed <SortIcon field="feed_name" />
                        </button>
                      </th>
                      <th className="px-3 py-2.5 text-left font-semibold">
                        <button className="flex items-center gap-1" onClick={() => handleSort("relevance_score")}>
                          Relevance <SortIcon field="relevance_score" />
                        </button>
                      </th>
                      <th className="px-3 py-2.5 text-left font-semibold">Topics</th>
                      <th className="px-3 py-2.5 text-left font-semibold">
                        <button className="flex items-center gap-1" onClick={() => handleSort("status")}>
                          Status <SortIcon field="status" />
                        </button>
                      </th>
                      <th className="px-3 py-2.5 text-left font-semibold">
                        <button className="flex items-center gap-1" onClick={() => handleSort("crawled_at")}>
                          Crawled <SortIcon field="crawled_at" />
                        </button>
                      </th>
                      <th className="px-3 py-2.5 text-center font-semibold">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50">
                    {sortedArticles.length === 0 ? (
                      <tr>
                        <td colSpan={8} className="px-4 py-12 text-center text-gray-400 text-sm">
                          No articles found for the selected filters.
                        </td>
                      </tr>
                    ) : (
                      sortedArticles.map((a) => (
                        <>
                          <tr
                            key={a.id}
                            className="hover:bg-indigo-50/30 transition-colors cursor-pointer group"
                            onClick={() => setExpandedArticle(expandedArticle === a.id ? null : a.id)}
                          >
                            <td className="px-2 py-3">
                              <button
                                onClick={(e) => { e.stopPropagation(); toggleSelect(a.id); }}
                                className="text-gray-400 hover:text-indigo-500 transition-colors"
                              >
                                {selected.has(a.id) ? (
                                  <CheckSquare className="w-4 h-4 text-indigo-500" />
                                ) : (
                                  <Square className="w-4 h-4" />
                                )}
                              </button>
                            </td>
                            <td className="px-4 py-3">
                              <div className="flex items-center gap-2">
                                <span
                                  className={`w-2 h-2 rounded-full shrink-0 ${
                                    a.is_relevant ? "bg-emerald-400" : "bg-gray-300"
                                  }`}
                                />
                                <span className="text-gray-800 font-medium truncate max-w-[300px]" title={a.title}>
                                  {a.title}
                                </span>
                              </div>
                            </td>
                            <td className="px-3 py-3">
                              <span className="text-gray-500 truncate max-w-[120px] block">{a.feed_name}</span>
                            </td>
                            <td className="px-3 py-3">
                              <div className="flex items-center gap-2">
                                <ScoreBar score={a.relevance_score} width={60} />
                                <ScoreLabel score={a.relevance_score} />
                              </div>
                            </td>
                            <td className="px-3 py-3">
                              <div className="flex flex-wrap gap-1">
                                {a.matched_topics.slice(0, 3).map((t) => (
                                  <span
                                    key={t}
                                    className="px-1.5 py-0.5 rounded text-[9px] font-semibold bg-indigo-50 text-indigo-500 border border-indigo-200/60"
                                  >
                                    {t}
                                  </span>
                                ))}
                                {a.matched_keywords.slice(0, 2).map((kw) => (
                                  <span
                                    key={kw}
                                    className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-amber-50 text-amber-600 border border-amber-200/60"
                                  >
                                    {kw}
                                  </span>
                                ))}
                              </div>
                            </td>
                            <td className="px-3 py-3">
                              <StatusBadge status={a.status} />
                            </td>
                            <td className="px-3 py-3 text-gray-400">
                              {a.crawled_at
                                ? new Date(a.crawled_at).toLocaleDateString(undefined, {
                                    month: "short",
                                    day: "numeric",
                                    hour: "2-digit",
                                    minute: "2-digit",
                                  })
                                : "—"}
                            </td>
                            <td className="px-3 py-3">
                              <div className="flex items-center justify-center gap-1">
                                {/* Open source article */}
                                <a
                                  href={a.article_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="p-1.5 rounded-lg text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 transition-all"
                                  title="Open source article"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  <ExternalLink className="w-3.5 h-3.5" />
                                </a>
                                {/* Open blog in editor */}
                                {a.draft_id && (
                                  <Link
                                    to={`/editor/${a.draft_id}`}
                                    className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold text-indigo-600 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200/60 transition-all"
                                    title="Open blog in editor"
                                    onClick={(e) => e.stopPropagation()}
                                  >
                                    <FileText className="w-3 h-3" />
                                    Blog
                                  </Link>
                                )}
                                {/* Generate blog (for skipped/error) */}
                                {!a.draft_id && (a.status === "skipped" || a.status === "skipped_rank" || a.status === "error") && (
                                  <button
                                    onClick={(e) => { e.stopPropagation(); handleRegenerate(a.id); }}
                                    disabled={!!actionLoading[a.id]}
                                    className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold text-cyan-600 bg-cyan-50 hover:bg-cyan-100 border border-cyan-200/60 transition-all disabled:opacity-40"
                                    title="Generate blog from this article"
                                  >
                                    {actionLoading[a.id] === "regenerate" ? (
                                      <Loader2 className="w-3 h-3 animate-spin" />
                                    ) : (
                                      <RotateCw className="w-3 h-3" />
                                    )}
                                    Generate
                                  </button>
                                )}
                                {/* Post to LinkedIn */}
                                {a.draft_id && !a.linkedin_post_id && (
                                  <button
                                    onClick={(e) => { e.stopPropagation(); handleLinkedIn(a.id); }}
                                    disabled={!!actionLoading[a.id]}
                                    className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold text-blue-600 bg-blue-50 hover:bg-blue-100 border border-blue-200/60 transition-all disabled:opacity-40"
                                    title="Compose and publish to LinkedIn"
                                  >
                                    {actionLoading[a.id] === "linkedin" ? (
                                      <Loader2 className="w-3 h-3 animate-spin" />
                                    ) : (
                                      <Linkedin className="w-3 h-3" />
                                    )}
                                    LinkedIn
                                  </button>
                                )}
                                {/* LinkedIn posted badge */}
                                {a.linkedin_post_id && (
                                  <span
                                    className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold text-blue-500 bg-blue-50/50 border border-blue-200/40"
                                    title={`Posted: ${a.linkedin_post_id}`}
                                  >
                                    <Linkedin className="w-3 h-3" />
                                    Posted
                                  </span>
                                )}
                              </div>
                            </td>
                          </tr>
                          {/* Expanded row */}
                          {expandedArticle === a.id && (
                            <tr key={`${a.id}-detail`}>
                              <td colSpan={8} className="px-6 py-3 bg-gray-50/50 border-b border-gray-100">
                                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-[11px]">
                                  <div>
                                    <span className="text-gray-400 font-semibold uppercase tracking-wide block mb-0.5">Article URL</span>
                                    <a
                                      href={a.article_url}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      className="text-indigo-500 hover:underline truncate block max-w-[250px]"
                                    >
                                      {a.article_url}
                                    </a>
                                  </div>
                                  <div>
                                    <span className="text-gray-400 font-semibold uppercase tracking-wide block mb-0.5">Draft ID</span>
                                    <span className="text-gray-600">{a.draft_id || "—"}</span>
                                  </div>
                                  <div>
                                    <span className="text-gray-400 font-semibold uppercase tracking-wide block mb-0.5">LinkedIn Post</span>
                                    <span className="text-gray-600">{a.linkedin_post_id || "—"}</span>
                                  </div>
                                  <div>
                                    <span className="text-gray-400 font-semibold uppercase tracking-wide block mb-0.5">Retries</span>
                                    <span className="text-gray-600">{a.retry_count}</span>
                                  </div>
                                  {a.last_error && (
                                    <div className="col-span-full">
                                      <span className="text-red-400 font-semibold uppercase tracking-wide block mb-0.5">Last Error</span>
                                      <span className="text-red-500 font-mono text-[10px]">{a.last_error}</span>
                                    </div>
                                  )}
                                  {a.matched_keywords.length > 0 && (
                                    <div className="col-span-full">
                                      <span className="text-gray-400 font-semibold uppercase tracking-wide block mb-0.5">Matched Keywords</span>
                                      <div className="flex flex-wrap gap-1">
                                        {a.matched_keywords.map((kw) => (
                                          <span key={kw} className="px-2 py-0.5 rounded text-[10px] font-medium bg-amber-50 text-amber-600 border border-amber-200/60">{kw}</span>
                                        ))}
                                      </div>
                                    </div>
                                  )}
                                </div>
                              </td>
                            </tr>
                          )}
                        </>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        ) : null}

        {/* Footer */}
        <footer className="py-10 border-t border-gray-100 text-center mt-10">
          <p className="text-xs text-gray-400">
            Analytics &middot; Articles classified by GPT-4o &middot; Ranked by technical depth
          </p>
        </footer>
      </div>
  );
}
