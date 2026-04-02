import { useEffect, useRef, useState } from "react";
import {
  listFeeds,
  createFeed,
  updateFeed,
  deleteFeed,
  discoverFeed,
  listFeedArticles,
  deleteFeedArticle,
  deleteAllFeedArticles,
  deleteAllDrafts,
  deleteAllCrawledArticles,
  getCrawlLog,
  streamCrawl,
  streamCrawlAll,
} from "../../services/api";
import type { FeedSource, CrawledArticle, CrawlJob, FeedDiscoverResult } from "../../types";
import {
  Plus,
  Trash2,
  RefreshCw,
  Loader2,
  Rss,
  Globe,
  ChevronDown,
  ChevronUp,
  Check,
  X,
  ExternalLink,
  AlertTriangle,
  Play,
  Square,
  Search,
} from "lucide-react";

interface CrawlLogEntry {
  time: string;
  level: "info" | "success" | "warn" | "error";
  message: string;
}

const TOPIC_OPTIONS = ["cloud security", "azure", "ai"];
const INTERVAL_OPTIONS = [
  { label: "30 min", value: 30 },
  { label: "1 hour", value: 60 },
  { label: "4 hours", value: 240 },
  { label: "12 hours", value: 720 },
  { label: "24 hours", value: 1440 },
];

export default function FeedsSettings() {
  const [feeds, setFeeds] = useState<FeedSource[]>([]);
  const [crawlLog, setCrawlLog] = useState<CrawlJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAddForm, setShowAddForm] = useState(false);
  const [expandedFeed, setExpandedFeed] = useState<string | null>(null);
  const [feedArticles, setFeedArticles] = useState<Record<string, CrawledArticle[]>>({});
  const [crawlingFeeds, setCrawlingFeeds] = useState<Set<string>>(new Set());
  const [showCrawlLog, setShowCrawlLog] = useState(false);
  const [liveCrawlLog, setLiveCrawlLog] = useState<Record<string, CrawlLogEntry[]>>({});
  const crawlLogEndRef = useRef<Record<string, HTMLDivElement | null>>({});

  // Article search & filter state (per feed)
  const [articleSearch, setArticleSearch] = useState<Record<string, string>>({});
  const [articleStatusFilter, setArticleStatusFilter] = useState<Record<string, string>>({});
  const [articleTopicFilter, setArticleTopicFilter] = useState<Record<string, string>>({});
  const [articleKeywordFilter, setArticleKeywordFilter] = useState<Record<string, string>>({});
  const searchTimerRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  // Add form state
  const [newUrl, setNewUrl] = useState("");
  const [newName, setNewName] = useState("");
  const [newTopics, setNewTopics] = useState<string[]>(["cloud security", "azure", "ai"]);
  const [newInterval, setNewInterval] = useState(60);
  const [newAutoPublishBlog, setNewAutoPublishBlog] = useState(false);
  const [newAutoPublishLinkedIn, setNewAutoPublishLinkedIn] = useState(false);
  const [newMaxAgeDays, setNewMaxAgeDays] = useState(7);
  const [newMaxArticles, setNewMaxArticles] = useState(1);
  const [discovering, setDiscovering] = useState(false);
  const [discoveryResult, setDiscoveryResult] = useState<FeedDiscoverResult | null>(null);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const [feedsData, logData] = await Promise.all([listFeeds(), getCrawlLog(20)]);
      setFeeds(feedsData);
      setCrawlLog(logData);
    } catch {
      // Silently handle
    }
    setLoading(false);
  };

  const handleDiscover = async () => {
    if (!newUrl.trim()) return;
    setDiscovering(true);
    setError(null);
    setDiscoveryResult(null);
    try {
      const result = await discoverFeed(newUrl.trim());
      setDiscoveryResult(result);
      if (!newName && result.site_name) {
        setNewName(result.site_name);
      }
    } catch (err: any) {
      setError(err.message || "Discovery failed");
    }
    setDiscovering(false);
  };

  const handleAddFeed = async () => {
    if (!newUrl.trim()) return;
    setAdding(true);
    setError(null);
    try {
      const created = await createFeed({
        base_url: newUrl.trim(),
        name: newName.trim() || undefined,
        topics: newTopics,
        crawl_interval_minutes: newInterval,
        auto_publish_blog: newAutoPublishBlog,
        auto_publish_linkedin: newAutoPublishLinkedIn,
        max_article_age_days: newMaxAgeDays,
        max_articles_to_generate: newMaxArticles,
      });
      setFeeds((prev) => [created, ...prev]);
      setShowAddForm(false);
      resetAddForm();
    } catch (err: any) {
      setError(err.message || "Failed to add feed");
    }
    setAdding(false);
  };

  const resetAddForm = () => {
    setNewUrl("");
    setNewName("");
    setNewTopics(["cloud security", "azure", "ai"]);
    setNewInterval(60);
    setNewAutoPublishBlog(false);
    setNewAutoPublishLinkedIn(false);
    setNewMaxAgeDays(7);
    setNewMaxArticles(1);
    setDiscoveryResult(null);
    setError(null);
  };

  const handleToggleEnabled = async (feed: FeedSource) => {
    try {
      const updated = await updateFeed(feed.id, { enabled: !feed.enabled });
      setFeeds((prev) => prev.map((f) => (f.id === feed.id ? updated : f)));
    } catch {}
  };

  const handleDeleteFeed = async (id: string) => {
    try {
      await deleteFeed(id);
      setFeeds((prev) => prev.filter((f) => f.id !== id));
    } catch {}
  };

  const appendLog = (feedId: string, level: CrawlLogEntry["level"], message: string) => {
    const entry: CrawlLogEntry = {
      time: new Date().toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
      level,
      message,
    };
    setLiveCrawlLog((prev) => ({
      ...prev,
      [feedId]: [...(prev[feedId] ?? []), entry],
    }));
    setTimeout(() => {
      const el = crawlLogEndRef.current[feedId]?.parentElement;
      if (el) el.scrollTop = el.scrollHeight;
    }, 50);
  };

  const handleCrawlNow = (feedId: string) => {
    setCrawlingFeeds((prev) => new Set(prev).add(feedId));
    setLiveCrawlLog((prev) => ({ ...prev, [feedId]: [] }));
    setExpandedFeed(feedId);

    streamCrawl(feedId, {
      onCrawlStarted: (d) => appendLog(feedId, "info", `Crawl started: ${d.source_name} (${d.feed_type})`),
      onFetchingArticles: (d) => appendLog(feedId, "info", `Fetching articles via ${d.method}...`),
      onArticlesFetched: (d) => {
        let msg = `Found ${d.total} articles (${d.new} new)`;
        if (d.after_age_filter !== undefined && d.after_age_filter < d.new) {
          msg += ` \u2192 ${d.after_age_filter} within ${d.max_age_days}d window`;
        }
        appendLog(feedId, "info", msg);
      },
      onClassifying: (d) => appendLog(feedId, "info", `[${d.index}/${d.total}] Classifying: ${d.title}`),
      onClassified: (d) => {
        if (d.is_relevant) {
          appendLog(feedId, "success", `[${d.index}/${d.total}] Relevant (${d.relevance_score.toFixed(1)}): ${d.title} — ${d.matched_topics.join(", ")}`);
        } else {
          appendLog(feedId, "warn", `[${d.index}/${d.total}] Skipped (not relevant): ${d.title}`);
        }
      },
      onRanking: (d) => appendLog(feedId, "info", `Ranking ${d.relevant_count} relevant articles by technicality (picking top ${d.max_to_generate})...`),
      onRanked: (d) => {
        appendLog(feedId, "success", `Top ${d.top_count} selected${d.skipped_count > 0 ? ` (${d.skipped_count} skipped)` : ""}`);
        d.top_titles.forEach((t: string, i: number) => appendLog(feedId, "info", `  #${i + 1}: ${t}`));
      },
      onGenerating: (d) => appendLog(feedId, "info", `Generating blog #${d.index}: ${d.title}`),
      onGenerated: (d) => appendLog(feedId, "success", `Blog ${d.status}: ${d.title} (draft: ${d.draft_id.slice(0, 8)}...)`),
      onGenerateError: (d) => appendLog(feedId, "error", `Generation failed for "${d.title}": ${d.error}`),
      onSelectingBest: (d) => appendLog(feedId, "info", `Selecting best LinkedIn post from ${d.candidates} candidate(s)...`),
      onBestSelected: (d) => {
        if (d.skipped) {
          const reasons: Record<string, string> = {
            daily_limit: "Already posted to LinkedIn today",
            auto_publish_disabled: "Auto-publish LinkedIn is disabled for this feed",
            no_linkedin_session: "No active LinkedIn session found",
            publish_failed: "LinkedIn publish failed",
          };
          appendLog(feedId, "warn", `LinkedIn skipped: ${reasons[d.reason ?? ""] ?? d.reason}`);
        } else {
          appendLog(feedId, "success", `LinkedIn post published: ${d.title} (${d.post_id})`);
        }
      },
      onComplete: (d) => {
        appendLog(feedId, "success", `Crawl complete — ${d.articles_found} found, ${d.articles_relevant} relevant, ${d.articles_processed} processed`);
        setCrawlingFeeds((prev) => { const next = new Set(prev); next.delete(feedId); return next; });
        loadData();
        loadFeedArticles(feedId);
      },
      onError: (err) => {
        appendLog(feedId, "error", `Error: ${err}`);
        setCrawlingFeeds((prev) => { const next = new Set(prev); next.delete(feedId); return next; });
      },
    });
  };

  const loadFeedArticles = async (feedId: string) => {
    try {
      const articles = await listFeedArticles(feedId, {
        search: articleSearch[feedId] || undefined,
        status: articleStatusFilter[feedId] || undefined,
        topic: articleTopicFilter[feedId] || undefined,
        keyword: articleKeywordFilter[feedId] || undefined,
      });
      setFeedArticles((prev) => ({ ...prev, [feedId]: articles }));
    } catch {}
  };

  const handleArticleFilterChange = (feedId: string) => {
    // Debounce search, immediate for dropdowns
    clearTimeout(searchTimerRef.current[feedId]);
    searchTimerRef.current[feedId] = setTimeout(() => loadFeedArticles(feedId), 300);
  };

  const handleExpandFeed = async (feedId: string) => {
    if (expandedFeed === feedId) {
      setExpandedFeed(null);
      return;
    }
    setExpandedFeed(feedId);
    if (!feedArticles[feedId]) {
      await loadFeedArticles(feedId);
    }
  };

  const handleUpdateFeed = async (
    feedId: string,
    updates: Partial<{
      name: string;
      base_url: string;
      feed_url: string;
      auto_publish_blog: boolean;
      auto_publish_linkedin: boolean;
      crawl_interval_minutes: number;
      max_article_age_days: number;
      max_articles_to_generate: number;
      topics: string[];
    }>
  ) => {
    try {
      const updated = await updateFeed(feedId, updates);
      setFeeds((prev) => prev.map((f) => (f.id === feedId ? updated : f)));
    } catch {}
  };

  const handleDeleteArticle = async (feedId: string, articleId: string) => {
    try {
      await deleteFeedArticle(feedId, articleId);
      setFeedArticles((prev) => ({
        ...prev,
        [feedId]: (prev[feedId] ?? []).filter((a) => a.id !== articleId),
      }));
    } catch {}
  };

  const handleDeleteAllArticles = async (feedId: string) => {
    if (!confirm("Delete all crawled articles for this feed? This cannot be undone.")) return;
    try {
      await deleteAllFeedArticles(feedId);
      setFeedArticles((prev) => ({ ...prev, [feedId]: [] }));
    } catch {}
  };

  // Test All Feeds state
  const [runningAll, setRunningAll] = useState(false);
  const [runAllLog, setRunAllLog] = useState<CrawlLogEntry[]>([]);
  const [showRunAllLog, setShowRunAllLog] = useState(false);
  const runAllAbortRef = useRef<AbortController | null>(null);
  const runAllLogEndRef = useRef<HTMLDivElement | null>(null);

  const appendRunAllLog = (level: CrawlLogEntry["level"], message: string) => {
    const entry: CrawlLogEntry = {
      time: new Date().toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
      level,
      message,
    };
    setRunAllLog((prev) => [...prev, entry]);
    setTimeout(() => {
      const el = runAllLogEndRef.current?.parentElement;
      if (el) el.scrollTop = el.scrollHeight;
    }, 50);
  };

  const handleTestAllFeeds = () => {
    setRunningAll(true);
    setRunAllLog([]);
    setShowRunAllLog(true);

    const controller = streamCrawlAll({
      onRunStarted: (d) => appendRunAllLog("info", `Starting pipeline for ${d.total_feeds} feed(s): ${d.feed_names.join(", ")}`),
      onFeedStarted: (d) => appendRunAllLog("info", `\n[${d.index}/${d.total_feeds}] === ${d.feed_name} ===`),
      onCrawlStarted: (d) => appendRunAllLog("info", `  Crawl started: ${d.source_name} (${d.feed_type})`),
      onFetchingArticles: (d) => appendRunAllLog("info", `  Fetching articles via ${d.method}...`),
      onArticlesFetched: (d) => {
        let msg = `  Found ${d.total} articles (${d.new} new)`;
        if (d.after_age_filter !== undefined && d.after_age_filter < d.new) {
          msg += ` -> ${d.after_age_filter} within ${d.max_age_days}d window`;
        }
        appendRunAllLog("info", msg);
      },
      onClassifying: (d) => appendRunAllLog("info", `  [${d.index}/${d.total}] Classifying: ${d.title}`),
      onClassified: (d) => {
        if (d.is_relevant) {
          appendRunAllLog("success", `  [${d.index}/${d.total}] Relevant (${d.relevance_score.toFixed(1)}): ${d.title}`);
        } else {
          appendRunAllLog("warn", `  [${d.index}/${d.total}] Skipped: ${d.title}`);
        }
      },
      onRanking: (d) => appendRunAllLog("info", `  Ranking ${d.relevant_count} articles (picking top ${d.max_to_generate})...`),
      onRanked: (d) => {
        appendRunAllLog("success", `  Top ${d.top_count} selected${d.skipped_count > 0 ? ` (${d.skipped_count} skipped)` : ""}`);
        d.top_titles.forEach((t: string, i: number) => appendRunAllLog("info", `    #${i + 1}: ${t}`));
      },
      onGenerating: (d) => appendRunAllLog("info", `  Generating blog #${d.index}: ${d.title}`),
      onGenerated: (d) => appendRunAllLog("success", `  Blog ${d.status}: ${d.title}`),
      onGenerateError: (d) => appendRunAllLog("error", `  Generation failed: "${d.title}" - ${d.error}`),
      onSelectingBest: (d) => appendRunAllLog("info", `  Selecting best LinkedIn post from ${d.candidates} candidate(s)...`),
      onBestSelected: (d) => {
        if (d.skipped) {
          const reasons: Record<string, string> = {
            daily_limit: "Already posted today",
            auto_publish_disabled: "Auto-publish disabled",
            no_linkedin_session: "No active session",
            publish_failed: "Publish failed",
          };
          appendRunAllLog("warn", `  LinkedIn skipped: ${reasons[d.reason ?? ""] ?? d.reason}`);
        } else {
          appendRunAllLog("success", `  LinkedIn published: ${d.title} (${d.post_id})`);
        }
      },
      onComplete: (d) => appendRunAllLog("success", `  Feed done -- ${d.articles_found} found, ${d.articles_relevant} relevant, ${d.articles_processed} processed`),
      onFeedError: (d) => appendRunAllLog("error", `  FEED ERROR (${d.feed_name}): ${d.error}`),
      onRunComplete: (d) => {
        appendRunAllLog("success", `\nAll done! ${d.feeds_processed} feeds | ${d.total_found} found | ${d.total_relevant} relevant | ${d.total_processed} processed`);
        if (d.errors.length > 0) {
          d.errors.forEach((e: any) => appendRunAllLog("error", `  Failed: ${e.feed} - ${e.error}`));
        }
        setRunningAll(false);
        loadData();
      },
      onError: (err) => {
        appendRunAllLog("error", `Error: ${err}`);
        setRunningAll(false);
      },
    });

    runAllAbortRef.current = controller;
  };

  const handleStopTestAll = () => {
    runAllAbortRef.current?.abort();
    setRunningAll(false);
    appendRunAllLog("warn", "Pipeline aborted by user.");
  };

  const [deletingAllDrafts, setDeletingAllDrafts] = useState(false);
  const [deletingAllArticles, setDeletingAllArticles] = useState(false);

  const handleDeleteAllDrafts = async () => {
    if (!confirm("Delete ALL blog drafts? This cannot be undone.")) return;
    setDeletingAllDrafts(true);
    try {
      const result = await deleteAllDrafts();
      alert(`Deleted ${result.count} draft(s).`);
    } catch {}
    setDeletingAllDrafts(false);
  };

  const handleDeleteAllCrawledArticles = async () => {
    if (!confirm("Delete ALL crawled articles across all feeds? This resets deduplication so the next crawl will re-process everything. This cannot be undone.")) return;
    setDeletingAllArticles(true);
    try {
      const result = await deleteAllCrawledArticles();
      alert(`Deleted ${result.count} crawled article(s).`);
      for (const f of feeds) {
        if (expandedFeed === f.id) {
          await loadFeedArticles(f.id);
        }
      }
    } catch {}
    setDeletingAllArticles(false);
  };

  const toggleTopic = (topic: string) => {
    setNewTopics((prev) =>
      prev.includes(topic) ? prev.filter((t) => t !== topic) : [...prev, topic]
    );
  };

  return (
    <>
      {/* Header actions */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-lg font-bold text-gray-900 flex items-center gap-2">
            <Rss className="w-5 h-5 text-indigo-500" />
            Feed Sources
          </h2>
          <p className="text-sm text-gray-500 mt-0.5">
            Configure blog URLs to crawl for new articles. Relevant articles are auto-drafted.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {runningAll ? (
            <button
              onClick={handleStopTestAll}
              className="px-5 py-2.5 rounded-xl bg-red-500 hover:bg-red-600 text-white font-semibold text-sm transition-all duration-300 flex items-center gap-2 shadow-lg shadow-red-500/25"
            >
              <Square className="w-4 h-4" />
              Stop
            </button>
          ) : (
            <button
              onClick={handleTestAllFeeds}
              disabled={feeds.filter((f) => f.enabled).length === 0}
              className="px-5 py-2.5 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 disabled:from-gray-200 disabled:to-gray-200 disabled:text-gray-400 text-white font-semibold text-sm transition-all duration-300 flex items-center gap-2 shadow-lg shadow-emerald-500/25"
              title="Run the full pipeline on all enabled feeds"
            >
              <Play className="w-4 h-4" />
              Test All Feeds
            </button>
          )}
          <button
            onClick={() => {
              setShowAddForm(!showAddForm);
              if (!showAddForm) resetAddForm();
            }}
            className="px-5 py-2.5 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 text-white font-semibold text-sm transition-all duration-300 flex items-center gap-2 shadow-lg shadow-indigo-500/25"
          >
            <Plus className="w-4 h-4" />
            Add Source
          </button>
        </div>
      </div>

      {/* Add Feed Form */}
      {showAddForm && (
        <div className="mb-8 p-6 rounded-2xl bg-white border border-gray-200/80 shadow-sm">
          <h3 className="text-base font-bold text-gray-900 mb-4">Add Blog Source</h3>

          {/* URL + Discover */}
          <div className="flex gap-3 mb-4">
            <div className="relative flex-1">
              <Globe className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="url"
                placeholder="https://blog.example.com"
                value={newUrl}
                onChange={(e) => setNewUrl(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleDiscover()}
                className="w-full pl-10 pr-4 py-2.5 rounded-xl bg-gray-50 border border-gray-200/80 text-gray-900 placeholder-gray-400 outline-none text-sm focus:border-indigo-300 focus:ring-2 focus:ring-indigo-500/10"
              />
            </div>
            <button
              onClick={handleDiscover}
              disabled={!newUrl.trim() || discovering}
              className="px-4 py-2.5 rounded-xl text-sm font-medium bg-gray-50 border border-gray-200/80 text-gray-600 hover:bg-gray-100 hover:border-gray-300 disabled:opacity-40 transition-all flex items-center gap-2"
            >
              {discovering ? <Loader2 className="w-4 h-4 animate-spin" /> : <Rss className="w-4 h-4" />}
              Discover Feed
            </button>
          </div>

          {/* Discovery Result */}
          {discoveryResult && (
            <div className="mb-4 px-4 py-3 rounded-xl bg-gray-50 border border-gray-200/60 text-sm">
              <div className="flex items-center gap-2">
                {discoveryResult.feed_type === "rss" ? (
                  <Rss className="w-4 h-4 text-green-500" />
                ) : (
                  <Globe className="w-4 h-4 text-amber-500" />
                )}
                <span className="text-gray-700">
                  {discoveryResult.feed_type === "rss" ? "RSS feed found" : "No RSS feed — will use HTML scraping"}
                </span>
              </div>
              {discoveryResult.feed_url && (
                <p className="text-xs text-gray-400 mt-1 truncate">{discoveryResult.feed_url}</p>
              )}
              {discoveryResult.site_name && (
                <p className="text-xs text-gray-400 mt-0.5">Site: {discoveryResult.site_name}</p>
              )}
            </div>
          )}

          {error && (
            <div className="mb-4 px-4 py-2 rounded-xl bg-red-50 border border-red-200/60 text-sm text-red-600">
              {error}
            </div>
          )}

          {/* Name */}
          <div className="mb-4">
            <label className="block text-xs font-semibold text-gray-500 mb-1.5">Name</label>
            <input
              type="text"
              placeholder="Auto-filled from site title"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              className="w-full px-3 py-2 rounded-xl bg-gray-50 border border-gray-200/80 text-gray-900 placeholder-gray-400 outline-none text-sm focus:border-indigo-300 focus:ring-2 focus:ring-indigo-500/10"
            />
          </div>

          {/* Topics */}
          <div className="mb-4">
            <label className="block text-xs font-semibold text-gray-500 mb-1.5">Filter Topics</label>
            <div className="flex flex-wrap gap-2">
              {TOPIC_OPTIONS.map((topic) => (
                <button
                  key={topic}
                  onClick={() => toggleTopic(topic)}
                  className={`px-3 py-1.5 rounded-full text-xs font-semibold border transition-all ${
                    newTopics.includes(topic)
                      ? "bg-indigo-50 border-indigo-200 text-indigo-600"
                      : "bg-gray-50 border-gray-200/60 text-gray-400 hover:border-gray-300"
                  }`}
                >
                  {newTopics.includes(topic) && <Check className="w-3 h-3 inline mr-1" />}
                  {topic}
                </button>
              ))}
            </div>
          </div>

          {/* Crawl Interval */}
          <div className="mb-4">
            <label className="block text-xs font-semibold text-gray-500 mb-1.5">Crawl Interval</label>
            <div className="flex flex-wrap gap-2">
              {INTERVAL_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => setNewInterval(opt.value)}
                  className={`px-3 py-1.5 rounded-xl text-xs font-semibold border transition-all ${
                    newInterval === opt.value
                      ? "bg-indigo-50 border-indigo-200 text-indigo-600"
                      : "bg-gray-50 border-gray-200/60 text-gray-400 hover:border-gray-300"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {/* Auto-publish toggles */}
          <div className="flex gap-6 mb-4">
            <label className="flex items-center gap-2 cursor-pointer">
              <button
                onClick={() => setNewAutoPublishBlog(!newAutoPublishBlog)}
                className={`w-9 h-5 rounded-full transition-all relative ${
                  newAutoPublishBlog ? "bg-indigo-500" : "bg-gray-200"
                }`}
              >
                <span
                  className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-all ${
                    newAutoPublishBlog ? "left-[18px]" : "left-0.5"
                  }`}
                />
              </button>
              <span className="text-sm text-gray-600">Auto-publish blog</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <button
                onClick={() => setNewAutoPublishLinkedIn(!newAutoPublishLinkedIn)}
                className={`w-9 h-5 rounded-full transition-all relative ${
                  newAutoPublishLinkedIn ? "bg-indigo-500" : "bg-gray-200"
                }`}
              >
                <span
                  className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-all ${
                    newAutoPublishLinkedIn ? "left-[18px]" : "left-0.5"
                  }`}
                />
              </button>
              <span className="text-sm text-gray-600">Auto-publish LinkedIn</span>
            </label>
          </div>

          {/* Article selection settings */}
          <div className="grid grid-cols-2 gap-4 mb-6">
            <div>
              <label className="block text-xs font-semibold text-gray-500 mb-1.5">Max Article Age (days)</label>
              <input
                type="number"
                min={1}
                max={90}
                value={newMaxAgeDays}
                onChange={(e) => setNewMaxAgeDays(Math.max(1, parseInt(e.target.value) || 7))}
                className="w-full px-3 py-2 rounded-xl bg-gray-50 border border-gray-200/80 text-gray-900 outline-none text-sm focus:border-indigo-300 focus:ring-2 focus:ring-indigo-500/10"
              />
              <p className="text-[10px] text-gray-400 mt-1">Only process articles published within this window</p>
            </div>
            <div>
              <label className="block text-xs font-semibold text-gray-500 mb-1.5">Max Articles to Generate</label>
              <input
                type="number"
                min={1}
                max={10}
                value={newMaxArticles}
                onChange={(e) => setNewMaxArticles(Math.max(1, parseInt(e.target.value) || 1))}
                className="w-full px-3 py-2 rounded-xl bg-gray-50 border border-gray-200/80 text-gray-900 outline-none text-sm focus:border-indigo-300 focus:ring-2 focus:ring-indigo-500/10"
              />
              <p className="text-[10px] text-gray-400 mt-1">Generate blogs for the top N most technical articles per crawl</p>
            </div>
          </div>

          {/* Actions */}
          <div className="flex gap-3">
            <button
              onClick={handleAddFeed}
              disabled={!newUrl.trim() || adding}
              className="px-5 py-2.5 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 disabled:from-gray-200 disabled:to-gray-200 disabled:text-gray-400 text-white font-semibold text-sm transition-all flex items-center gap-2"
            >
              {adding ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
              Add Source
            </button>
            <button
              onClick={() => { setShowAddForm(false); resetAddForm(); }}
              className="px-4 py-2.5 rounded-xl text-sm text-gray-500 hover:text-gray-900 border border-gray-200/60 hover:border-gray-300 transition-all"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Test All Feeds Log */}
      {showRunAllLog && runAllLog.length > 0 && (
        <div className="mb-8 p-5 rounded-2xl bg-white border border-gray-200/80 shadow-sm">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
              <Play className="w-4 h-4 text-emerald-500" />
              Pipeline Test Run
              {runningAll && <Loader2 className="w-3.5 h-3.5 animate-spin text-emerald-500" />}
            </h3>
            {!runningAll && (
              <button onClick={() => setShowRunAllLog(false)} className="p-1 text-gray-400 hover:text-gray-700 transition-colors" title="Close">
                <X className="w-4 h-4" />
              </button>
            )}
          </div>
          <div className="max-h-[400px] overflow-y-auto rounded-xl bg-gray-900 border border-gray-800 p-4 font-mono text-[11px] leading-relaxed">
            {runAllLog.map((entry, i) => (
              <div key={i} className="flex gap-2">
                <span className="text-gray-500 shrink-0">{entry.time}</span>
                <span
                  className={
                    entry.level === "success" ? "text-green-400"
                      : entry.level === "error" ? "text-red-400"
                      : entry.level === "warn" ? "text-amber-400"
                      : "text-gray-400"
                  }
                  style={{ whiteSpace: "pre-wrap" }}
                >
                  {entry.message}
                </span>
              </div>
            ))}
            <div ref={runAllLogEndRef} />
          </div>
        </div>
      )}

      {/* Feed Sources List */}
      {loading ? (
        <div className="flex justify-center py-20">
          <Loader2 className="w-6 h-6 animate-spin text-indigo-500" />
        </div>
      ) : feeds.length === 0 ? (
        <div className="text-center py-20">
          <Rss className="w-12 h-12 text-gray-300 mx-auto mb-4" />
          <p className="text-gray-500 text-sm font-medium">No feed sources configured yet.</p>
          <p className="text-gray-400 text-xs mt-1">Add a blog URL to start auto-crawling for articles.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {feeds.map((feed) => (
            <div
              key={feed.id}
              className="rounded-2xl bg-white border border-gray-200/60 hover:border-indigo-200 hover:shadow-md hover:shadow-indigo-500/5 transition-all"
            >
              {/* Feed Header */}
              <div className="p-4 flex items-center justify-between">
                <div className="flex-1 cursor-pointer min-w-0" onClick={() => handleExpandFeed(feed.id)}>
                  <div className="flex items-center gap-2.5 mb-1">
                    {feed.feed_type === "rss" ? <Rss className="w-4 h-4 text-orange-500 shrink-0" /> : <Globe className="w-4 h-4 text-gray-400 shrink-0" />}
                    <h4 className="font-semibold text-sm text-gray-900 truncate">{feed.name}</h4>
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${feed.enabled ? "bg-emerald-50 text-emerald-600 border border-emerald-200/60" : "bg-gray-100 text-gray-400 border border-gray-200/60"}`}>
                      {feed.enabled ? "Active" : "Paused"}
                    </span>
                  </div>
                  <div className="flex items-center gap-4 text-xs text-gray-400 ml-[26px]">
                    <span className="truncate max-w-[300px]">{feed.base_url}</span>
                    <span>Every {feed.crawl_interval_minutes}m</span>
                    {feed.last_crawled_at && (
                      <span>Last crawl: {new Date(feed.last_crawled_at).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
                    )}
                  </div>
                  <div className="flex gap-1.5 mt-2 ml-[26px]">
                    {feed.topics.map((t) => (
                      <span key={t} className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-indigo-50 text-indigo-600 border border-indigo-200/60">{t}</span>
                    ))}
                    {feed.auto_publish_blog && <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-cyan-50 text-cyan-600 border border-cyan-200/60">auto-blog</span>}
                    {feed.auto_publish_linkedin && <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-blue-50 text-blue-600 border border-blue-200/60">auto-linkedin</span>}
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-gray-50 text-gray-500 border border-gray-200/60">{feed.max_article_age_days}d / top {feed.max_articles_to_generate}</span>
                  </div>
                </div>
                <div className="flex items-center gap-1 ml-4 shrink-0">
                  <button onClick={() => handleCrawlNow(feed.id)} disabled={crawlingFeeds.has(feed.id)} className="p-2 rounded-xl text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 transition-all disabled:opacity-40" title="Crawl Now">
                    {crawlingFeeds.has(feed.id) ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                  </button>
                  <button onClick={() => handleToggleEnabled(feed)} className={`p-2 rounded-xl transition-all ${feed.enabled ? "text-emerald-500 hover:text-amber-500 hover:bg-amber-50" : "text-gray-400 hover:text-emerald-500 hover:bg-emerald-50"}`} title={feed.enabled ? "Pause" : "Enable"}>
                    {feed.enabled ? <Check className="w-4 h-4" /> : <X className="w-4 h-4" />}
                  </button>
                  <button onClick={() => handleDeleteFeed(feed.id)} className="p-2 rounded-xl text-gray-300 hover:text-red-500 hover:bg-red-50 transition-all" title="Delete">
                    <Trash2 className="w-4 h-4" />
                  </button>
                  <button onClick={() => handleExpandFeed(feed.id)} className="p-2 rounded-xl text-gray-400 hover:text-gray-700 hover:bg-gray-50 transition-all">
                    {expandedFeed === feed.id ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              {/* Expanded: Feed settings + articles */}
              {expandedFeed === feed.id && (
                <div className="border-t border-gray-100 p-4">
                  {/* Editable URLs */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
                    <div>
                      <label className="block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1">Source URL</label>
                      <input type="url" defaultValue={feed.base_url} onBlur={(e) => { const val = e.target.value.trim(); if (val && val !== feed.base_url) handleUpdateFeed(feed.id, { base_url: val }); }} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} className="w-full px-3 py-1.5 rounded-lg bg-gray-50 border border-gray-200/80 text-xs text-gray-700 outline-none focus:border-indigo-300 focus:ring-2 focus:ring-indigo-500/10" />
                    </div>
                    <div>
                      <label className="block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1">Feed URL</label>
                      <input type="url" defaultValue={feed.feed_url} onBlur={(e) => { const val = e.target.value.trim(); if (val !== feed.feed_url) handleUpdateFeed(feed.id, { feed_url: val }); }} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} className="w-full px-3 py-1.5 rounded-lg bg-gray-50 border border-gray-200/80 text-xs text-gray-700 outline-none focus:border-indigo-300 focus:ring-2 focus:ring-indigo-500/10" placeholder="Auto-discovered RSS/Atom feed URL" />
                    </div>
                  </div>

                  {/* Quick settings */}
                  <div className="flex flex-wrap gap-4 mb-4">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <button onClick={() => handleUpdateFeed(feed.id, { auto_publish_blog: !feed.auto_publish_blog })} className={`w-9 h-5 rounded-full transition-all relative ${feed.auto_publish_blog ? "bg-indigo-500" : "bg-gray-200"}`}>
                        <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-all ${feed.auto_publish_blog ? "left-[18px]" : "left-0.5"}`} />
                      </button>
                      <span className="text-xs text-gray-600">Auto-publish blog</span>
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <button onClick={() => handleUpdateFeed(feed.id, { auto_publish_linkedin: !feed.auto_publish_linkedin })} className={`w-9 h-5 rounded-full transition-all relative ${feed.auto_publish_linkedin ? "bg-indigo-500" : "bg-gray-200"}`}>
                        <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-all ${feed.auto_publish_linkedin ? "left-[18px]" : "left-0.5"}`} />
                      </button>
                      <span className="text-xs text-gray-600">Auto-publish LinkedIn</span>
                    </label>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-gray-500">Interval:</span>
                      <select value={feed.crawl_interval_minutes} onChange={(e) => handleUpdateFeed(feed.id, { crawl_interval_minutes: parseInt(e.target.value) })} className="px-2 py-1 rounded-lg bg-gray-50 border border-gray-200/80 text-xs text-gray-600 outline-none">
                        {INTERVAL_OPTIONS.map((opt) => (<option key={opt.value} value={opt.value}>{opt.label}</option>))}
                      </select>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-gray-500">Max age:</span>
                      <input type="number" min={1} max={90} defaultValue={feed.max_article_age_days} onBlur={(e) => { const val = Math.max(1, parseInt(e.target.value) || 7); if (val !== feed.max_article_age_days) handleUpdateFeed(feed.id, { max_article_age_days: val }); }} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} className="w-14 px-2 py-1 rounded-lg bg-gray-50 border border-gray-200/80 text-xs text-gray-600 outline-none" />
                      <span className="text-xs text-gray-400">days</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-gray-500">Top N:</span>
                      <input type="number" min={1} max={10} defaultValue={feed.max_articles_to_generate} onBlur={(e) => { const val = Math.max(1, parseInt(e.target.value) || 1); if (val !== feed.max_articles_to_generate) handleUpdateFeed(feed.id, { max_articles_to_generate: val }); }} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} className="w-14 px-2 py-1 rounded-lg bg-gray-50 border border-gray-200/80 text-xs text-gray-600 outline-none" />
                      <span className="text-xs text-gray-400">articles</span>
                    </div>
                  </div>

                  {/* Live Crawl Log */}
                  {(liveCrawlLog[feed.id]?.length ?? 0) > 0 && (
                    <div className="mb-4">
                      <h5 className="text-xs font-semibold text-gray-500 mb-2">
                        Crawl Progress
                        {crawlingFeeds.has(feed.id) && <Loader2 className="w-3 h-3 animate-spin inline ml-2 text-indigo-500" />}
                      </h5>
                      <div className="max-h-[200px] overflow-y-auto rounded-xl bg-gray-900 border border-gray-800 p-3 font-mono text-[11px] leading-relaxed">
                        {liveCrawlLog[feed.id].map((entry, i) => (
                          <div key={i} className="flex gap-2">
                            <span className="text-gray-500 shrink-0">{entry.time}</span>
                            <span className={entry.level === "success" ? "text-green-400" : entry.level === "error" ? "text-red-400" : entry.level === "warn" ? "text-amber-400" : "text-gray-400"}>
                              {entry.message}
                            </span>
                          </div>
                        ))}
                        <div ref={(el) => { crawlLogEndRef.current[feed.id] = el; }} />
                      </div>
                    </div>
                  )}

                  {/* Crawled articles */}
                  <div className="flex items-center justify-between mb-2">
                    <h5 className="text-xs font-semibold text-gray-500">Crawled Articles</h5>
                    {(feedArticles[feed.id]?.length ?? 0) > 0 && (
                      <button onClick={() => handleDeleteAllArticles(feed.id)} className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold text-red-400 hover:text-red-600 hover:bg-red-50 border border-transparent hover:border-red-200/60 transition-all">
                        <Trash2 className="w-3 h-3" />
                        Clear All
                      </button>
                    )}
                  </div>

                  {/* Search & Filter Bar */}
                  <div className="flex flex-wrap items-center gap-2 mb-3">
                    <div className="relative flex-1 min-w-[180px]">
                      <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
                      <input
                        type="text"
                        placeholder="Search articles..."
                        value={articleSearch[feed.id] ?? ""}
                        onChange={(e) => {
                          const val = e.target.value;
                          setArticleSearch((prev) => ({ ...prev, [feed.id]: val }));
                          handleArticleFilterChange(feed.id);
                        }}
                        className="w-full pl-8 pr-3 py-1.5 rounded-lg bg-gray-50 border border-gray-200/80 text-xs text-gray-700 outline-none placeholder-gray-400 focus:border-indigo-300 focus:ring-2 focus:ring-indigo-500/10"
                      />
                    </div>
                    <select
                      value={articleStatusFilter[feed.id] ?? ""}
                      onChange={(e) => {
                        setArticleStatusFilter((prev) => ({ ...prev, [feed.id]: e.target.value }));
                        setTimeout(() => loadFeedArticles(feed.id), 0);
                      }}
                      className="px-2 py-1.5 rounded-lg bg-gray-50 border border-gray-200/80 text-xs text-gray-600 outline-none"
                    >
                      <option value="">All statuses</option>
                      <option value="published">Published</option>
                      <option value="drafted">Drafted</option>
                      <option value="skipped">Skipped</option>
                      <option value="skipped_rank">Skipped (rank)</option>
                      <option value="error">Error</option>
                      <option value="pending">Pending</option>
                    </select>
                    {feed.topics.length > 0 && (
                      <select
                        value={articleTopicFilter[feed.id] ?? ""}
                        onChange={(e) => {
                          setArticleTopicFilter((prev) => ({ ...prev, [feed.id]: e.target.value }));
                          setTimeout(() => loadFeedArticles(feed.id), 0);
                        }}
                        className="px-2 py-1.5 rounded-lg bg-gray-50 border border-gray-200/80 text-xs text-gray-600 outline-none"
                      >
                        <option value="">All topics</option>
                        {feed.topics.map((t) => (
                          <option key={t} value={t}>{t}</option>
                        ))}
                      </select>
                    )}
                    {/* Collect unique keywords from loaded articles */}
                    {(() => {
                      const allKw = Array.from(
                        new Set(
                          (feedArticles[feed.id] ?? []).flatMap((a) => a.matched_keywords ?? [])
                        )
                      ).sort();
                      return allKw.length > 0 ? (
                        <select
                          value={articleKeywordFilter[feed.id] ?? ""}
                          onChange={(e) => {
                            setArticleKeywordFilter((prev) => ({ ...prev, [feed.id]: e.target.value }));
                            setTimeout(() => loadFeedArticles(feed.id), 0);
                          }}
                          className="px-2 py-1.5 rounded-lg bg-gray-50 border border-gray-200/80 text-xs text-gray-600 outline-none"
                        >
                          <option value="">All keywords</option>
                          {allKw.map((kw) => (
                            <option key={kw} value={kw}>{kw}</option>
                          ))}
                        </select>
                      ) : null;
                    })()}
                    {(articleSearch[feed.id] || articleStatusFilter[feed.id] || articleTopicFilter[feed.id] || articleKeywordFilter[feed.id]) && (
                      <button
                        onClick={() => {
                          setArticleSearch((prev) => ({ ...prev, [feed.id]: "" }));
                          setArticleStatusFilter((prev) => ({ ...prev, [feed.id]: "" }));
                          setArticleTopicFilter((prev) => ({ ...prev, [feed.id]: "" }));
                          setArticleKeywordFilter((prev) => ({ ...prev, [feed.id]: "" }));
                          setTimeout(() => loadFeedArticles(feed.id), 0);
                        }}
                        className="px-2 py-1.5 rounded-lg text-[10px] font-semibold text-gray-500 hover:text-gray-700 hover:bg-gray-100 border border-gray-200/60 transition-all flex items-center gap-1"
                      >
                        <X className="w-3 h-3" />
                        Clear
                      </button>
                    )}
                  </div>
                  {!feedArticles[feed.id] ? (
                    <div className="flex justify-center py-4"><Loader2 className="w-4 h-4 animate-spin text-gray-400" /></div>
                  ) : feedArticles[feed.id].length === 0 ? (
                    <p className="text-xs text-gray-400 py-2">No articles crawled yet. Click "Crawl Now" to start.</p>
                  ) : (
                    <div className="space-y-1.5 max-h-[300px] overflow-y-auto">
                      {feedArticles[feed.id].map((article) => (
                        <div key={article.id} className="flex items-center justify-between px-3 py-2 rounded-xl bg-gray-50 text-xs">
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <span className={`w-2 h-2 rounded-full shrink-0 ${article.is_relevant ? "bg-emerald-400" : "bg-gray-300"}`} />
                              <span className="text-gray-700 truncate">{article.title}</span>
                            </div>
                            <div className="flex items-center gap-2 mt-0.5 ml-4 flex-wrap">
                              {article.matched_topics.map((t) => (<span key={t} className="text-[10px] text-indigo-500 font-medium">{t}</span>))}
                              {article.matched_keywords?.length > 0 && article.matched_keywords.map((kw) => (<span key={kw} className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-amber-50 text-amber-600 border border-amber-200/60">{kw}</span>))}
                              <span className={`text-[10px] font-medium ${article.status === "published" ? "text-emerald-500" : article.status === "drafted" ? "text-cyan-500" : article.status === "error" ? "text-red-500" : "text-gray-400"}`}>
                                {article.status}
                              </span>
                            </div>
                          </div>
                          <div className="flex items-center gap-0.5 shrink-0">
                            <a href={article.article_url} target="_blank" rel="noopener noreferrer" className="p-1 text-gray-400 hover:text-indigo-600"><ExternalLink className="w-3 h-3" /></a>
                            <button onClick={() => handleDeleteArticle(feed.id, article.id)} className="p-1 text-gray-300 hover:text-red-500 transition-colors" title="Delete article"><Trash2 className="w-3 h-3" /></button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Crawl Log */}
      <div className="mt-10">
        <button onClick={() => setShowCrawlLog(!showCrawlLog)} className="flex items-center gap-2 text-sm font-semibold text-gray-500 hover:text-gray-900 transition-colors mb-4">
          {showCrawlLog ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          Crawl Log
          <span className="text-xs font-semibold text-gray-400 bg-gray-100 px-2.5 py-0.5 rounded-full">{crawlLog.length}</span>
        </button>
        {showCrawlLog && crawlLog.length > 0 && (
          <div className="space-y-1.5">
            {crawlLog.map((job) => {
              const feedName = feeds.find((f) => f.id === job.feed_source_id)?.name || job.feed_source_id;
              return (
                <div key={job.id} className="flex items-center justify-between px-4 py-2.5 rounded-xl bg-white border border-gray-200/60 text-xs">
                  <div className="flex items-center gap-3">
                    <span className={`w-2 h-2 rounded-full ${job.status === "completed" ? "bg-emerald-400" : job.status === "running" ? "bg-amber-400 animate-pulse" : "bg-red-400"}`} />
                    <span className="text-gray-700 font-semibold">{feedName}</span>
                    <span className="text-gray-400">Found {job.articles_found} | Relevant {job.articles_relevant} | Processed {job.articles_processed}</span>
                  </div>
                  <div className="flex items-center gap-3 text-gray-400">
                    {job.error && <span className="text-red-500 truncate max-w-[200px]">{job.error}</span>}
                    <span>{new Date(job.started_at).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Danger Zone */}
      <div className="mt-10 p-5 rounded-2xl border border-red-200/60 bg-red-50/30">
        <h3 className="text-sm font-bold text-red-600 flex items-center gap-2 mb-3">
          <AlertTriangle className="w-4 h-4" />
          Danger Zone
        </h3>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-gray-700 font-medium">Delete all blog drafts</p>
            <p className="text-xs text-gray-400 mt-0.5">Permanently remove every draft from the database. This cannot be undone.</p>
          </div>
          <button onClick={handleDeleteAllDrafts} disabled={deletingAllDrafts} className="px-4 py-2 rounded-xl text-sm font-semibold text-red-600 bg-white border border-red-200 hover:bg-red-50 hover:border-red-300 transition-all disabled:opacity-50 flex items-center gap-2 shrink-0">
            {deletingAllDrafts ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
            Delete All Drafts
          </button>
        </div>
        <div className="flex items-center justify-between mt-4 pt-4 border-t border-red-100">
          <div>
            <p className="text-sm text-gray-700 font-medium">Delete all crawled articles</p>
            <p className="text-xs text-gray-400 mt-0.5">Remove all crawled article records. The next crawl will re-fetch and re-analyze everything.</p>
          </div>
          <button onClick={handleDeleteAllCrawledArticles} disabled={deletingAllArticles} className="px-4 py-2 rounded-xl text-sm font-semibold text-red-600 bg-white border border-red-200 hover:bg-red-50 hover:border-red-300 transition-all disabled:opacity-50 flex items-center gap-2 shrink-0">
            {deletingAllArticles ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
            Delete All Articles
          </button>
        </div>
      </div>
    </>
  );
}
