import { useEffect, useRef, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
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
} from "../services/api";
import type { FeedSource, CrawledArticle, CrawlJob, FeedDiscoverResult } from "../types";
import {
  Sparkles,
  ArrowLeft,
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
  Settings as SettingsIcon,
  AlertTriangle,
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

export default function Settings() {
  const navigate = useNavigate();
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

  // Add form state
  const [newUrl, setNewUrl] = useState("");
  const [newName, setNewName] = useState("");
  const [newTopics, setNewTopics] = useState<string[]>(["cloud security", "azure", "ai"]);
  const [newInterval, setNewInterval] = useState(60);
  const [newAutoPublishBlog, setNewAutoPublishBlog] = useState(false);
  const [newAutoPublishLinkedIn, setNewAutoPublishLinkedIn] = useState(false);
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
    setTimeout(() => crawlLogEndRef.current[feedId]?.scrollIntoView({ behavior: "smooth" }), 50);
  };

  const handleCrawlNow = (feedId: string) => {
    setCrawlingFeeds((prev) => new Set(prev).add(feedId));
    setLiveCrawlLog((prev) => ({ ...prev, [feedId]: [] }));
    setExpandedFeed(feedId);

    streamCrawl(feedId, {
      onCrawlStarted: (d) => appendLog(feedId, "info", `Crawl started: ${d.source_name} (${d.feed_type})`),
      onFetchingArticles: (d) => appendLog(feedId, "info", `Fetching articles via ${d.method}...`),
      onArticlesFetched: (d) => appendLog(feedId, "info", `Found ${d.total} articles (${d.new} new)`),
      onClassifying: (d) => appendLog(feedId, "info", `[${d.index}/${d.total}] Classifying: ${d.title}`),
      onClassified: (d) => {
        if (d.is_relevant) {
          appendLog(feedId, "success", `[${d.index}/${d.total}] Relevant (${d.relevance_score.toFixed(1)}): ${d.title} — ${d.matched_topics.join(", ")}`);
        } else {
          appendLog(feedId, "warn", `[${d.index}/${d.total}] Skipped (not relevant): ${d.title}`);
        }
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
        listFeedArticles(feedId).then((articles) =>
          setFeedArticles((prev) => ({ ...prev, [feedId]: articles }))
        ).catch(() => {});
      },
      onError: (err) => {
        appendLog(feedId, "error", `Error: ${err}`);
        setCrawlingFeeds((prev) => { const next = new Set(prev); next.delete(feedId); return next; });
      },
    });
  };

  const handleExpandFeed = async (feedId: string) => {
    if (expandedFeed === feedId) {
      setExpandedFeed(null);
      return;
    }
    setExpandedFeed(feedId);
    if (!feedArticles[feedId]) {
      try {
        const articles = await listFeedArticles(feedId);
        setFeedArticles((prev) => ({ ...prev, [feedId]: articles }));
      } catch {}
    }
  };

  const handleUpdateFeed = async (
    feedId: string,
    updates: Partial<{
      auto_publish_blog: boolean;
      auto_publish_linkedin: boolean;
      crawl_interval_minutes: number;
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
      // Refresh article lists
      for (const f of feeds) {
        if (expandedFeed === f.id) {
          const arts = await listFeedArticles(f.id);
          setFeedArticles((prev) => ({ ...prev, [f.id]: arts }));
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
    <div className="min-h-screen bg-[var(--bg-base)]">
      {/* Decorative background */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-[-20%] right-[-10%] w-[600px] h-[600px] bg-indigo-200/20 rounded-full blur-[120px]" />
        <div className="absolute bottom-[-10%] left-[10%] w-[500px] h-[500px] bg-violet-200/15 rounded-full blur-[100px]" />
      </div>

      {/* Navigation */}
      <nav className="relative glass-strong border-b border-indigo-100/60 animate-fade-in-down sticky top-0 z-40">
        <div className="max-w-6xl mx-auto px-6 py-3 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-3 group">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 flex items-center justify-center shadow-lg shadow-indigo-500/25">
              <Sparkles className="w-4.5 h-4.5 text-white" />
            </div>
            <div className="flex items-baseline gap-2">
              <span className="text-lg font-bold tracking-tight text-gray-900">Blog Writer</span>
              <span className="text-[10px] font-semibold text-indigo-500 tracking-widest uppercase">Settings</span>
            </div>
          </Link>
          <button
            onClick={() => navigate("/")}
            className="px-4 py-2 rounded-xl text-sm font-semibold text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 border border-gray-200/60 hover:border-indigo-200 transition-all duration-200 flex items-center gap-2"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            Back
          </button>
        </div>
      </nav>

      <main className="relative max-w-6xl mx-auto px-6 py-8">
        {/* Page Title */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h2 className="text-2xl font-bold text-gray-900 flex items-center gap-3">
              <SettingsIcon className="w-6 h-6 text-indigo-500" />
              Feed Sources
            </h2>
            <p className="text-sm text-gray-500 mt-1">
              Configure blog URLs to crawl for new articles. Relevant articles are auto-drafted.
            </p>
          </div>
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
            <div className="flex gap-6 mb-6">
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
                onClick={() => {
                  setShowAddForm(false);
                  resetAddForm();
                }}
                className="px-4 py-2.5 rounded-xl text-sm text-gray-500 hover:text-gray-900 border border-gray-200/60 hover:border-gray-300 transition-all"
              >
                Cancel
              </button>
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
                  <div
                    className="flex-1 cursor-pointer min-w-0"
                    onClick={() => handleExpandFeed(feed.id)}
                  >
                    <div className="flex items-center gap-2.5 mb-1">
                      {feed.feed_type === "rss" ? (
                        <Rss className="w-4 h-4 text-orange-500 shrink-0" />
                      ) : (
                        <Globe className="w-4 h-4 text-gray-400 shrink-0" />
                      )}
                      <h4 className="font-semibold text-sm text-gray-900 truncate">{feed.name}</h4>
                      <span
                        className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                          feed.enabled
                            ? "bg-emerald-50 text-emerald-600 border border-emerald-200/60"
                            : "bg-gray-100 text-gray-400 border border-gray-200/60"
                        }`}
                      >
                        {feed.enabled ? "Active" : "Paused"}
                      </span>
                    </div>
                    <div className="flex items-center gap-4 text-xs text-gray-400 ml-[26px]">
                      <span className="truncate max-w-[300px]">{feed.base_url}</span>
                      <span>Every {feed.crawl_interval_minutes}m</span>
                      {feed.last_crawled_at && (
                        <span>
                          Last crawl:{" "}
                          {new Date(feed.last_crawled_at).toLocaleDateString(undefined, {
                            month: "short",
                            day: "numeric",
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </span>
                      )}
                    </div>
                    <div className="flex gap-1.5 mt-2 ml-[26px]">
                      {feed.topics.map((t) => (
                        <span
                          key={t}
                          className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-indigo-50 text-indigo-600 border border-indigo-200/60"
                        >
                          {t}
                        </span>
                      ))}
                      {feed.auto_publish_blog && (
                        <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-cyan-50 text-cyan-600 border border-cyan-200/60">
                          auto-blog
                        </span>
                      )}
                      {feed.auto_publish_linkedin && (
                        <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-blue-50 text-blue-600 border border-blue-200/60">
                          auto-linkedin
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 ml-4 shrink-0">
                    <button
                      onClick={() => handleCrawlNow(feed.id)}
                      disabled={crawlingFeeds.has(feed.id)}
                      className="p-2 rounded-xl text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 transition-all disabled:opacity-40"
                      title="Crawl Now"
                    >
                      {crawlingFeeds.has(feed.id) ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <RefreshCw className="w-4 h-4" />
                      )}
                    </button>
                    <button
                      onClick={() => handleToggleEnabled(feed)}
                      className={`p-2 rounded-xl transition-all ${
                        feed.enabled
                          ? "text-emerald-500 hover:text-amber-500 hover:bg-amber-50"
                          : "text-gray-400 hover:text-emerald-500 hover:bg-emerald-50"
                      }`}
                      title={feed.enabled ? "Pause" : "Enable"}
                    >
                      {feed.enabled ? <Check className="w-4 h-4" /> : <X className="w-4 h-4" />}
                    </button>
                    <button
                      onClick={() => handleDeleteFeed(feed.id)}
                      className="p-2 rounded-xl text-gray-300 hover:text-red-500 hover:bg-red-50 transition-all"
                      title="Delete"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleExpandFeed(feed.id)}
                      className="p-2 rounded-xl text-gray-400 hover:text-gray-700 hover:bg-gray-50 transition-all"
                    >
                      {expandedFeed === feed.id ? (
                        <ChevronUp className="w-4 h-4" />
                      ) : (
                        <ChevronDown className="w-4 h-4" />
                      )}
                    </button>
                  </div>
                </div>

                {/* Expanded: Feed settings + articles */}
                {expandedFeed === feed.id && (
                  <div className="border-t border-gray-100 p-4">
                    {/* Quick settings */}
                    <div className="flex flex-wrap gap-4 mb-4">
                      <label className="flex items-center gap-2 cursor-pointer">
                        <button
                          onClick={() =>
                            handleUpdateFeed(feed.id, { auto_publish_blog: !feed.auto_publish_blog })
                          }
                          className={`w-9 h-5 rounded-full transition-all relative ${
                            feed.auto_publish_blog ? "bg-indigo-500" : "bg-gray-200"
                          }`}
                        >
                          <span
                            className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-all ${
                              feed.auto_publish_blog ? "left-[18px]" : "left-0.5"
                            }`}
                          />
                        </button>
                        <span className="text-xs text-gray-600">Auto-publish blog</span>
                      </label>
                      <label className="flex items-center gap-2 cursor-pointer">
                        <button
                          onClick={() =>
                            handleUpdateFeed(feed.id, {
                              auto_publish_linkedin: !feed.auto_publish_linkedin,
                            })
                          }
                          className={`w-9 h-5 rounded-full transition-all relative ${
                            feed.auto_publish_linkedin ? "bg-indigo-500" : "bg-gray-200"
                          }`}
                        >
                          <span
                            className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-all ${
                              feed.auto_publish_linkedin ? "left-[18px]" : "left-0.5"
                            }`}
                          />
                        </button>
                        <span className="text-xs text-gray-600">Auto-publish LinkedIn</span>
                      </label>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-gray-500">Interval:</span>
                        <select
                          value={feed.crawl_interval_minutes}
                          onChange={(e) =>
                            handleUpdateFeed(feed.id, {
                              crawl_interval_minutes: parseInt(e.target.value),
                            })
                          }
                          className="px-2 py-1 rounded-lg bg-gray-50 border border-gray-200/80 text-xs text-gray-600 outline-none"
                        >
                          {INTERVAL_OPTIONS.map((opt) => (
                            <option key={opt.value} value={opt.value}>
                              {opt.label}
                            </option>
                          ))}
                        </select>
                      </div>
                    </div>

                    {/* Live Crawl Log */}
                    {(liveCrawlLog[feed.id]?.length ?? 0) > 0 && (
                      <div className="mb-4">
                        <h5 className="text-xs font-semibold text-gray-500 mb-2">
                          Crawl Progress
                          {crawlingFeeds.has(feed.id) && (
                            <Loader2 className="w-3 h-3 animate-spin inline ml-2 text-indigo-500" />
                          )}
                        </h5>
                        <div className="max-h-[200px] overflow-y-auto rounded-xl bg-gray-900 border border-gray-800 p-3 font-mono text-[11px] leading-relaxed">
                          {liveCrawlLog[feed.id].map((entry, i) => (
                            <div key={i} className="flex gap-2">
                              <span className="text-gray-500 shrink-0">{entry.time}</span>
                              <span
                                className={
                                  entry.level === "success"
                                    ? "text-green-400"
                                    : entry.level === "error"
                                    ? "text-red-400"
                                    : entry.level === "warn"
                                    ? "text-amber-400"
                                    : "text-gray-400"
                                }
                              >
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
                        <button
                          onClick={() => handleDeleteAllArticles(feed.id)}
                          className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold text-red-400 hover:text-red-600 hover:bg-red-50 border border-transparent hover:border-red-200/60 transition-all"
                        >
                          <Trash2 className="w-3 h-3" />
                          Clear All
                        </button>
                      )}
                    </div>
                    {!feedArticles[feed.id] ? (
                      <div className="flex justify-center py-4">
                        <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
                      </div>
                    ) : feedArticles[feed.id].length === 0 ? (
                      <p className="text-xs text-gray-400 py-2">
                        No articles crawled yet. Click "Crawl Now" to start.
                      </p>
                    ) : (
                      <div className="space-y-1.5 max-h-[300px] overflow-y-auto">
                        {feedArticles[feed.id].map((article) => (
                          <div
                            key={article.id}
                            className="flex items-center justify-between px-3 py-2 rounded-xl bg-gray-50 text-xs"
                          >
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <span
                                  className={`w-2 h-2 rounded-full shrink-0 ${
                                    article.is_relevant ? "bg-emerald-400" : "bg-gray-300"
                                  }`}
                                />
                                <span className="text-gray-700 truncate">{article.title}</span>
                              </div>
                              <div className="flex items-center gap-2 mt-0.5 ml-4 flex-wrap">
                                {article.matched_topics.map((t) => (
                                  <span key={t} className="text-[10px] text-indigo-500 font-medium">
                                    {t}
                                  </span>
                                ))}
                                {article.matched_keywords?.length > 0 &&
                                  article.matched_keywords.map((kw) => (
                                    <span
                                      key={kw}
                                      className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-amber-50 text-amber-600 border border-amber-200/60"
                                    >
                                      {kw}
                                    </span>
                                  ))}
                                <span
                                  className={`text-[10px] font-medium ${
                                    article.status === "published"
                                      ? "text-emerald-500"
                                      : article.status === "drafted"
                                      ? "text-cyan-500"
                                      : article.status === "error"
                                      ? "text-red-500"
                                      : "text-gray-400"
                                  }`}
                                >
                                  {article.status}
                                </span>
                              </div>
                            </div>
                            <div className="flex items-center gap-0.5 shrink-0">
                              <a
                                href={article.article_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="p-1 text-gray-400 hover:text-indigo-600"
                              >
                                <ExternalLink className="w-3 h-3" />
                              </a>
                              <button
                                onClick={() => handleDeleteArticle(feed.id, article.id)}
                                className="p-1 text-gray-300 hover:text-red-500 transition-colors"
                                title="Delete article"
                              >
                                <Trash2 className="w-3 h-3" />
                              </button>
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
          <button
            onClick={() => setShowCrawlLog(!showCrawlLog)}
            className="flex items-center gap-2 text-sm font-semibold text-gray-500 hover:text-gray-900 transition-colors mb-4"
          >
            {showCrawlLog ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            Crawl Log
            <span className="text-xs font-semibold text-gray-400 bg-gray-100 px-2.5 py-0.5 rounded-full">
              {crawlLog.length}
            </span>
          </button>
          {showCrawlLog && crawlLog.length > 0 && (
            <div className="space-y-1.5">
              {crawlLog.map((job) => {
                const feedName = feeds.find((f) => f.id === job.feed_source_id)?.name || job.feed_source_id;
                return (
                  <div
                    key={job.id}
                    className="flex items-center justify-between px-4 py-2.5 rounded-xl bg-white border border-gray-200/60 text-xs"
                  >
                    <div className="flex items-center gap-3">
                      <span
                        className={`w-2 h-2 rounded-full ${
                          job.status === "completed"
                            ? "bg-emerald-400"
                            : job.status === "running"
                            ? "bg-amber-400 animate-pulse"
                            : "bg-red-400"
                        }`}
                      />
                      <span className="text-gray-700 font-semibold">{feedName}</span>
                      <span className="text-gray-400">
                        Found {job.articles_found} | Relevant {job.articles_relevant} | Processed{" "}
                        {job.articles_processed}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 text-gray-400">
                      {job.error && <span className="text-red-500 truncate max-w-[200px]">{job.error}</span>}
                      <span>
                        {new Date(job.started_at).toLocaleDateString(undefined, {
                          month: "short",
                          day: "numeric",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </span>
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
            <button
              onClick={handleDeleteAllDrafts}
              disabled={deletingAllDrafts}
              className="px-4 py-2 rounded-xl text-sm font-semibold text-red-600 bg-white border border-red-200 hover:bg-red-50 hover:border-red-300 transition-all disabled:opacity-50 flex items-center gap-2 shrink-0"
            >
              {deletingAllDrafts ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Trash2 className="w-3.5 h-3.5" />
              )}
              Delete All Drafts
            </button>
          </div>
          <div className="flex items-center justify-between mt-4 pt-4 border-t border-red-100">
            <div>
              <p className="text-sm text-gray-700 font-medium">Delete all crawled articles</p>
              <p className="text-xs text-gray-400 mt-0.5">Remove all crawled article records. The next crawl will re-fetch and re-analyze everything.</p>
            </div>
            <button
              onClick={handleDeleteAllCrawledArticles}
              disabled={deletingAllArticles}
              className="px-4 py-2 rounded-xl text-sm font-semibold text-red-600 bg-white border border-red-200 hover:bg-red-50 hover:border-red-300 transition-all disabled:opacity-50 flex items-center gap-2 shrink-0"
            >
              {deletingAllArticles ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Trash2 className="w-3.5 h-3.5" />
              )}
              Delete All Articles
            </button>
          </div>
        </div>

        {/* Footer */}
        <footer className="py-10 border-t border-gray-100 text-center mt-10">
          <p className="text-xs text-gray-400">
            Auto-crawl powered by APScheduler &middot; Articles classified by GPT-4o
          </p>
        </footer>
      </main>
    </div>
  );
}
