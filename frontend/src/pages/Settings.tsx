import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  listFeeds,
  createFeed,
  updateFeed,
  deleteFeed,
  discoverFeed,
  listFeedArticles,
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
      // Silently handle - feeds may not be set up yet
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
    // Auto-scroll
    setTimeout(() => crawlLogEndRef.current[feedId]?.scrollIntoView({ behavior: "smooth" }), 50);
  };

  const handleCrawlNow = (feedId: string) => {
    setCrawlingFeeds((prev) => new Set(prev).add(feedId));
    setLiveCrawlLog((prev) => ({ ...prev, [feedId]: [] }));
    // Auto-expand so user sees the log
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
      onComplete: (d) => {
        appendLog(feedId, "success", `Crawl complete — ${d.articles_found} found, ${d.articles_relevant} relevant, ${d.articles_processed} processed`);
        setCrawlingFeeds((prev) => { const next = new Set(prev); next.delete(feedId); return next; });
        loadData();
        // Refresh articles for this feed
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

  const toggleTopic = (topic: string) => {
    setNewTopics((prev) =>
      prev.includes(topic) ? prev.filter((t) => t !== topic) : [...prev, topic]
    );
  };

  return (
    <div className="min-h-screen bg-[#0b0f1a] text-white">
      {/* Ambient background glow */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-[-20%] left-[20%] w-[600px] h-[600px] bg-indigo-600/[0.04] rounded-full blur-[120px]" />
        <div className="absolute bottom-[-10%] right-[15%] w-[500px] h-[500px] bg-cyan-600/[0.03] rounded-full blur-[100px]" />
      </div>

      {/* Header */}
      <header className="relative border-b border-white/[0.06] backdrop-blur-md bg-[#0b0f1a]/80">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-indigo-500/20">
              <Sparkles className="w-4 h-4 text-white" />
            </div>
            <div className="flex items-baseline gap-2">
              <h1 className="text-lg font-semibold tracking-tight text-white">Blog Writer</h1>
              <span className="text-[11px] font-medium text-indigo-400/70 tracking-wide uppercase">Settings</span>
            </div>
          </div>
          <button
            onClick={() => navigate("/")}
            className="px-4 py-2 rounded-lg text-sm font-medium text-slate-400 border border-white/[0.08] hover:border-white/[0.15] hover:text-white hover:bg-white/[0.04] transition-all duration-200"
          >
            <ArrowLeft className="w-3.5 h-3.5 inline mr-2" />
            Back
          </button>
        </div>
      </header>

      <main className="relative max-w-6xl mx-auto px-6 py-8">
        {/* Page Title */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h2 className="text-2xl font-bold text-white flex items-center gap-3">
              <SettingsIcon className="w-6 h-6 text-indigo-400" />
              Feed Sources
            </h2>
            <p className="text-sm text-slate-400 mt-1">
              Configure blog URLs to crawl for new articles. Relevant articles are auto-drafted.
            </p>
          </div>
          <button
            onClick={() => {
              setShowAddForm(!showAddForm);
              if (!showAddForm) resetAddForm();
            }}
            className="px-5 py-2.5 rounded-lg bg-gradient-to-r from-indigo-600 to-indigo-500 hover:from-indigo-500 hover:to-indigo-400 font-semibold text-sm transition-all duration-300 flex items-center gap-2 shadow-lg shadow-indigo-500/20"
          >
            <Plus className="w-4 h-4" />
            Add Source
          </button>
        </div>

        {/* Add Feed Form */}
        {showAddForm && (
          <div className="mb-8 p-6 rounded-xl bg-white/[0.03] border border-white/[0.08]">
            <h3 className="text-base font-semibold text-white mb-4">Add Blog Source</h3>

            {/* URL + Discover */}
            <div className="flex gap-3 mb-4">
              <div className="relative flex-1">
                <Globe className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                <input
                  type="url"
                  placeholder="https://blog.example.com"
                  value={newUrl}
                  onChange={(e) => setNewUrl(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleDiscover()}
                  className="w-full pl-10 pr-4 py-2.5 rounded-lg bg-white/[0.04] border border-white/[0.08] text-white placeholder-slate-500 outline-none text-sm focus:border-indigo-500/50"
                />
              </div>
              <button
                onClick={handleDiscover}
                disabled={!newUrl.trim() || discovering}
                className="px-4 py-2.5 rounded-lg text-sm font-medium bg-white/[0.06] border border-white/[0.08] hover:bg-white/[0.1] disabled:opacity-40 transition-all flex items-center gap-2"
              >
                {discovering ? <Loader2 className="w-4 h-4 animate-spin" /> : <Rss className="w-4 h-4" />}
                Discover Feed
              </button>
            </div>

            {/* Discovery Result */}
            {discoveryResult && (
              <div className="mb-4 px-4 py-3 rounded-lg bg-white/[0.04] border border-white/[0.06] text-sm">
                <div className="flex items-center gap-2">
                  {discoveryResult.feed_type === "rss" ? (
                    <Rss className="w-4 h-4 text-green-400" />
                  ) : (
                    <Globe className="w-4 h-4 text-amber-400" />
                  )}
                  <span className="text-slate-300">
                    {discoveryResult.feed_type === "rss" ? "RSS feed found" : "No RSS feed — will use HTML scraping"}
                  </span>
                </div>
                {discoveryResult.feed_url && (
                  <p className="text-xs text-slate-500 mt-1 truncate">{discoveryResult.feed_url}</p>
                )}
                {discoveryResult.site_name && (
                  <p className="text-xs text-slate-500 mt-0.5">Site: {discoveryResult.site_name}</p>
                )}
              </div>
            )}

            {error && (
              <div className="mb-4 px-4 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-sm text-red-300">
                {error}
              </div>
            )}

            {/* Name */}
            <div className="mb-4">
              <label className="block text-xs font-medium text-slate-400 mb-1.5">Name</label>
              <input
                type="text"
                placeholder="Auto-filled from site title"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                className="w-full px-3 py-2 rounded-lg bg-white/[0.04] border border-white/[0.08] text-white placeholder-slate-500 outline-none text-sm focus:border-indigo-500/50"
              />
            </div>

            {/* Topics */}
            <div className="mb-4">
              <label className="block text-xs font-medium text-slate-400 mb-1.5">Filter Topics</label>
              <div className="flex flex-wrap gap-2">
                {TOPIC_OPTIONS.map((topic) => (
                  <button
                    key={topic}
                    onClick={() => toggleTopic(topic)}
                    className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all ${
                      newTopics.includes(topic)
                        ? "bg-indigo-500/20 border-indigo-500/40 text-indigo-300"
                        : "bg-white/[0.03] border-white/[0.08] text-slate-400 hover:border-white/[0.15]"
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
              <label className="block text-xs font-medium text-slate-400 mb-1.5">Crawl Interval</label>
              <div className="flex flex-wrap gap-2">
                {INTERVAL_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => setNewInterval(opt.value)}
                    className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${
                      newInterval === opt.value
                        ? "bg-indigo-500/20 border-indigo-500/40 text-indigo-300"
                        : "bg-white/[0.03] border-white/[0.08] text-slate-400 hover:border-white/[0.15]"
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
                    newAutoPublishBlog ? "bg-indigo-500" : "bg-white/[0.1]"
                  }`}
                >
                  <span
                    className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${
                      newAutoPublishBlog ? "left-[18px]" : "left-0.5"
                    }`}
                  />
                </button>
                <span className="text-sm text-slate-300">Auto-publish blog</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <button
                  onClick={() => setNewAutoPublishLinkedIn(!newAutoPublishLinkedIn)}
                  className={`w-9 h-5 rounded-full transition-all relative ${
                    newAutoPublishLinkedIn ? "bg-indigo-500" : "bg-white/[0.1]"
                  }`}
                >
                  <span
                    className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${
                      newAutoPublishLinkedIn ? "left-[18px]" : "left-0.5"
                    }`}
                  />
                </button>
                <span className="text-sm text-slate-300">Auto-publish LinkedIn</span>
              </label>
            </div>

            {/* Actions */}
            <div className="flex gap-3">
              <button
                onClick={handleAddFeed}
                disabled={!newUrl.trim() || adding}
                className="px-5 py-2.5 rounded-lg bg-gradient-to-r from-indigo-600 to-indigo-500 hover:from-indigo-500 hover:to-indigo-400 disabled:from-slate-700 disabled:to-slate-700 disabled:text-slate-500 font-semibold text-sm transition-all flex items-center gap-2"
              >
                {adding ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                Add Source
              </button>
              <button
                onClick={() => {
                  setShowAddForm(false);
                  resetAddForm();
                }}
                className="px-4 py-2.5 rounded-lg text-sm text-slate-400 hover:text-white border border-white/[0.08] hover:border-white/[0.15] transition-all"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* Feed Sources List */}
        {loading ? (
          <div className="flex justify-center py-20">
            <Loader2 className="w-6 h-6 animate-spin text-indigo-400" />
          </div>
        ) : feeds.length === 0 ? (
          <div className="text-center py-20">
            <Rss className="w-12 h-12 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400 text-sm">No feed sources configured yet.</p>
            <p className="text-slate-500 text-xs mt-1">Add a blog URL to start auto-crawling for articles.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {feeds.map((feed) => (
              <div
                key={feed.id}
                className="rounded-xl bg-white/[0.02] border border-white/[0.06] hover:border-white/[0.1] transition-all"
              >
                {/* Feed Header */}
                <div className="p-4 flex items-center justify-between">
                  <div
                    className="flex-1 cursor-pointer min-w-0"
                    onClick={() => handleExpandFeed(feed.id)}
                  >
                    <div className="flex items-center gap-2.5 mb-1">
                      {feed.feed_type === "rss" ? (
                        <Rss className="w-4 h-4 text-orange-400 shrink-0" />
                      ) : (
                        <Globe className="w-4 h-4 text-slate-400 shrink-0" />
                      )}
                      <h4 className="font-medium text-sm text-white truncate">{feed.name}</h4>
                      <span
                        className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${
                          feed.enabled
                            ? "bg-green-500/15 text-green-400 border border-green-500/25"
                            : "bg-slate-500/15 text-slate-400 border border-slate-500/25"
                        }`}
                      >
                        {feed.enabled ? "Active" : "Paused"}
                      </span>
                    </div>
                    <div className="flex items-center gap-4 text-xs text-slate-500 ml-[26px]">
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
                          className="px-2 py-0.5 rounded-full text-[10px] bg-indigo-500/10 text-indigo-300 border border-indigo-500/20"
                        >
                          {t}
                        </span>
                      ))}
                      {feed.auto_publish_blog && (
                        <span className="px-2 py-0.5 rounded-full text-[10px] bg-cyan-500/10 text-cyan-300 border border-cyan-500/20">
                          auto-blog
                        </span>
                      )}
                      {feed.auto_publish_linkedin && (
                        <span className="px-2 py-0.5 rounded-full text-[10px] bg-blue-500/10 text-blue-300 border border-blue-500/20">
                          auto-linkedin
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 ml-4 shrink-0">
                    <button
                      onClick={() => handleCrawlNow(feed.id)}
                      disabled={crawlingFeeds.has(feed.id)}
                      className="p-2 rounded-lg text-slate-400 hover:text-indigo-400 hover:bg-indigo-500/10 transition-all disabled:opacity-40"
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
                      className={`p-2 rounded-lg transition-all ${
                        feed.enabled
                          ? "text-green-400 hover:text-amber-400 hover:bg-amber-500/10"
                          : "text-slate-500 hover:text-green-400 hover:bg-green-500/10"
                      }`}
                      title={feed.enabled ? "Pause" : "Enable"}
                    >
                      {feed.enabled ? <Check className="w-4 h-4" /> : <X className="w-4 h-4" />}
                    </button>
                    <button
                      onClick={() => handleDeleteFeed(feed.id)}
                      className="p-2 rounded-lg text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-all"
                      title="Delete"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleExpandFeed(feed.id)}
                      className="p-2 rounded-lg text-slate-500 hover:text-white hover:bg-white/[0.06] transition-all"
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
                  <div className="border-t border-white/[0.06] p-4">
                    {/* Quick settings */}
                    <div className="flex flex-wrap gap-4 mb-4">
                      <label className="flex items-center gap-2 cursor-pointer">
                        <button
                          onClick={() =>
                            handleUpdateFeed(feed.id, { auto_publish_blog: !feed.auto_publish_blog })
                          }
                          className={`w-9 h-5 rounded-full transition-all relative ${
                            feed.auto_publish_blog ? "bg-indigo-500" : "bg-white/[0.1]"
                          }`}
                        >
                          <span
                            className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${
                              feed.auto_publish_blog ? "left-[18px]" : "left-0.5"
                            }`}
                          />
                        </button>
                        <span className="text-xs text-slate-300">Auto-publish blog</span>
                      </label>
                      <label className="flex items-center gap-2 cursor-pointer">
                        <button
                          onClick={() =>
                            handleUpdateFeed(feed.id, {
                              auto_publish_linkedin: !feed.auto_publish_linkedin,
                            })
                          }
                          className={`w-9 h-5 rounded-full transition-all relative ${
                            feed.auto_publish_linkedin ? "bg-indigo-500" : "bg-white/[0.1]"
                          }`}
                        >
                          <span
                            className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${
                              feed.auto_publish_linkedin ? "left-[18px]" : "left-0.5"
                            }`}
                          />
                        </button>
                        <span className="text-xs text-slate-300">Auto-publish LinkedIn</span>
                      </label>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-slate-400">Interval:</span>
                        <select
                          value={feed.crawl_interval_minutes}
                          onChange={(e) =>
                            handleUpdateFeed(feed.id, {
                              crawl_interval_minutes: parseInt(e.target.value),
                            })
                          }
                          className="px-2 py-1 rounded-md bg-white/[0.04] border border-white/[0.08] text-xs text-slate-300 outline-none"
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
                        <h5 className="text-xs font-medium text-slate-400 mb-2">
                          Crawl Progress
                          {crawlingFeeds.has(feed.id) && (
                            <Loader2 className="w-3 h-3 animate-spin inline ml-2 text-indigo-400" />
                          )}
                        </h5>
                        <div className="max-h-[200px] overflow-y-auto rounded-lg bg-[#0a0e17] border border-white/[0.06] p-3 font-mono text-[11px] leading-relaxed">
                          {liveCrawlLog[feed.id].map((entry, i) => (
                            <div key={i} className="flex gap-2">
                              <span className="text-slate-600 shrink-0">{entry.time}</span>
                              <span
                                className={
                                  entry.level === "success"
                                    ? "text-green-400"
                                    : entry.level === "error"
                                    ? "text-red-400"
                                    : entry.level === "warn"
                                    ? "text-amber-400"
                                    : "text-slate-400"
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
                    <h5 className="text-xs font-medium text-slate-400 mb-2">Crawled Articles</h5>
                    {!feedArticles[feed.id] ? (
                      <div className="flex justify-center py-4">
                        <Loader2 className="w-4 h-4 animate-spin text-slate-500" />
                      </div>
                    ) : feedArticles[feed.id].length === 0 ? (
                      <p className="text-xs text-slate-500 py-2">
                        No articles crawled yet. Click "Crawl Now" to start.
                      </p>
                    ) : (
                      <div className="space-y-1.5 max-h-[300px] overflow-y-auto">
                        {feedArticles[feed.id].map((article) => (
                          <div
                            key={article.id}
                            className="flex items-center justify-between px-3 py-2 rounded-lg bg-white/[0.02] text-xs"
                          >
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <span
                                  className={`w-2 h-2 rounded-full shrink-0 ${
                                    article.is_relevant ? "bg-green-400" : "bg-slate-500"
                                  }`}
                                />
                                <span className="text-slate-300 truncate">{article.title}</span>
                              </div>
                              <div className="flex items-center gap-2 mt-0.5 ml-4">
                                {article.matched_topics.map((t) => (
                                  <span key={t} className="text-[10px] text-indigo-400">
                                    {t}
                                  </span>
                                ))}
                                <span
                                  className={`text-[10px] ${
                                    article.status === "published"
                                      ? "text-green-400"
                                      : article.status === "drafted"
                                      ? "text-cyan-400"
                                      : article.status === "error"
                                      ? "text-red-400"
                                      : "text-slate-500"
                                  }`}
                                >
                                  {article.status}
                                </span>
                              </div>
                            </div>
                            <a
                              href={article.article_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="p-1 text-slate-500 hover:text-white"
                            >
                              <ExternalLink className="w-3 h-3" />
                            </a>
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
            className="flex items-center gap-2 text-sm font-medium text-slate-400 hover:text-white transition-colors mb-4"
          >
            {showCrawlLog ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            Crawl Log
            <span className="text-xs text-slate-500 bg-white/[0.04] px-2 py-0.5 rounded-full">
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
                    className="flex items-center justify-between px-4 py-2.5 rounded-lg bg-white/[0.02] border border-white/[0.04] text-xs"
                  >
                    <div className="flex items-center gap-3">
                      <span
                        className={`w-2 h-2 rounded-full ${
                          job.status === "completed"
                            ? "bg-green-400"
                            : job.status === "running"
                            ? "bg-amber-400 animate-pulse"
                            : "bg-red-400"
                        }`}
                      />
                      <span className="text-slate-300 font-medium">{feedName}</span>
                      <span className="text-slate-500">
                        Found {job.articles_found} | Relevant {job.articles_relevant} | Processed{" "}
                        {job.articles_processed}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 text-slate-500">
                      {job.error && <span className="text-red-400 truncate max-w-[200px]">{job.error}</span>}
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

        {/* Footer */}
        <footer className="py-8 border-t border-white/[0.04] text-center mt-10">
          <p className="text-xs text-slate-600">
            Auto-crawl powered by APScheduler &middot; Articles classified by GPT-4o
          </p>
        </footer>
      </main>
    </div>
  );
}
