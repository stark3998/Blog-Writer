import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useBlogStore } from "../store/blogStore";
import {
  generateBlogStream,
  listDrafts,
  createDraft,
  deleteDraft,
  listRelevantArticles,
  listFeeds,
  getCrawlLog,
  listPublishedBlogs,
} from "../services/api";
import type { GenerateResult, CrawledArticle, FeedSource, CrawlJob, PublishedBlog } from "../types";
import {
  Loader2,
  PenLine,
  Trash2,
  ExternalLink,
  Sparkles,
  FileText,
  Globe,
  Github,
  ArrowRight,
  Rss,
  Activity,
  Tag,
  User,
  TrendingUp,
  Settings,
  Clock,
  BarChart3,
  Newspaper,
  Plus,
  Linkedin,
  CheckCircle2,
  BookOpen,
} from "lucide-react";

export default function Home() {
  const navigate = useNavigate();
  const [url, setUrl] = useState("");
  const {
    drafts,
    setDrafts,
    phase,
    setPhase,
    setContent,
    setError,
    error,
    statusMessage,
    setStatusMessage,
    setDraft,
  } = useBlogStore();
  const abortRef = useRef<AbortController | null>(null);

  const [relevantArticles, setRelevantArticles] = useState<CrawledArticle[]>([]);
  const [feeds, setFeeds] = useState<FeedSource[]>([]);
  const [recentJobs, setRecentJobs] = useState<CrawlJob[]>([]);
  const [publishedBlogs, setPublishedBlogs] = useState<PublishedBlog[]>([]);

  useEffect(() => {
    listDrafts().then(setDrafts).catch(() => {});
    listRelevantArticles().then(setRelevantArticles).catch(() => {});
    listFeeds().then(setFeeds).catch(() => {});
    getCrawlLog(5).then(setRecentJobs).catch(() => {});
    listPublishedBlogs(10).then(setPublishedBlogs).catch(() => {});
  }, [setDrafts]);

  const handleGenerate = () => {
    if (!url.trim()) return;
    setError(null);
    setPhase("analyzing");
    setStatusMessage("Analyzing source URL...");

    const controller = generateBlogStream(url.trim(), {
      onAnalyzing: () => {
        setPhase("analyzing");
        setStatusMessage("Analyzing source...");
      },
      onAnalyzed: (sourceType) => {
        setStatusMessage(`Source analyzed (${sourceType}). Generating blog...`);
      },
      onGenerating: () => {
        setPhase("generating");
        setStatusMessage("AI is writing your blog...");
      },
      onComplete: (data) => {
        const result = data as GenerateResult;
        setContent(result.mdx_content);
        setPhase("idle");
        setStatusMessage("");

        createDraft({
          title: result.title,
          slug: result.slug,
          excerpt: result.excerpt,
          content: result.mdx_content,
          source_url: result.source_url,
          source_type: result.source_type,
        })
          .then((saved) => {
            setDraft(saved);
            navigate(`/editor/${saved.id}`);
          })
          .catch(() => {
            setDraft(null);
            navigate("/editor");
          });
      },
      onError: (err) => {
        setError(err);
        setPhase("idle");
        setStatusMessage("");
      },
    });

    abortRef.current = controller;
  };

  const handleDeleteDraft = async (id: string) => {
    try {
      await deleteDraft(id);
      setDrafts(drafts.filter((d) => d.id !== id));
    } catch {
      /* ignore */
    }
  };

  const busy = phase === "analyzing" || phase === "generating";

  const [originFilter, setOriginFilter] = useState<"all" | "user" | "rss_crawl">("all");
  const [tagFilter, setTagFilter] = useState<string | null>(null);

  const allTags = useMemo(() => {
    const tags = new Set<string>();
    drafts.forEach((d) => (d.tags ?? []).forEach((t) => tags.add(t)));
    return Array.from(tags).sort();
  }, [drafts]);

  const userCount = useMemo(() => drafts.filter((d) => (d.origin || "user") === "user").length, [drafts]);
  const rssCount = useMemo(() => drafts.filter((d) => d.origin === "rss_crawl").length, [drafts]);
  const activeFeeds = useMemo(() => feeds.filter((f) => f.enabled).length, [feeds]);

  const filteredDrafts = useMemo(() => {
    return drafts.filter((d) => {
      const origin = d.origin || "user";
      if (originFilter !== "all" && origin !== originFilter) return false;
      if (tagFilter && !(d.tags ?? []).includes(tagFilter)) return false;
      return true;
    });
  }, [drafts, originFilter, tagFilter]);

  return (
    <div className="min-h-screen bg-[var(--bg-base)]">
      {/* Decorative background */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-[-30%] right-[-10%] w-[800px] h-[800px] bg-indigo-200/30 rounded-full blur-[140px] animate-float" />
        <div className="absolute bottom-[-20%] left-[-5%] w-[600px] h-[600px] bg-violet-200/20 rounded-full blur-[120px] animate-float" style={{ animationDelay: "3s" }} />
      </div>

      {/* Navigation */}
      <nav className="relative glass-strong border-b border-indigo-100/60 animate-fade-in-down sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 py-3 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-3 group">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 flex items-center justify-center shadow-lg shadow-indigo-500/25 group-hover:shadow-indigo-500/40 transition-shadow">
              <Sparkles className="w-4.5 h-4.5 text-white" />
            </div>
            <div className="flex items-baseline gap-2">
              <span className="text-lg font-bold tracking-tight text-gray-900">Blog Writer</span>
              <span className="text-[10px] font-semibold text-indigo-500 tracking-widest uppercase">AI</span>
            </div>
          </Link>
          <div className="flex items-center gap-2">
            <button onClick={() => navigate("/dashboard")} className="p-2.5 rounded-xl text-gray-400 hover:text-emerald-600 hover:bg-emerald-50 transition-all duration-200" title="Pipeline Dashboard">
              <BarChart3 className="w-4.5 h-4.5" />
            </button>
            <button onClick={() => navigate("/settings")} className="p-2.5 rounded-xl text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 transition-all duration-200" title="Feed Settings">
              <Rss className="w-4.5 h-4.5" />
            </button>
            <button onClick={() => navigate("/prompts")} className="p-2.5 rounded-xl text-gray-400 hover:text-purple-600 hover:bg-purple-50 transition-all duration-200" title="Prompt Editor">
              <FileText className="w-4.5 h-4.5" />
            </button>
            <button onClick={() => navigate("/keywords")} className="p-2.5 rounded-xl text-gray-400 hover:text-amber-600 hover:bg-amber-50 transition-all duration-200" title="Keyword Manager">
              <Tag className="w-4.5 h-4.5" />
            </button>
            <button onClick={() => navigate("/settings")} className="p-2.5 rounded-xl text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 transition-all duration-200" title="Settings">
              <Settings className="w-4.5 h-4.5" />
            </button>
            <button onClick={() => navigate("/diagnostics")} className="p-2.5 rounded-xl text-gray-400 hover:text-emerald-600 hover:bg-emerald-50 transition-all duration-200" title="Diagnostics">
              <Activity className="w-4.5 h-4.5" />
            </button>
            <button onClick={() => navigate("/profile")} className="p-2.5 rounded-xl text-gray-400 hover:text-amber-600 hover:bg-amber-50 transition-all duration-200" title="Profile">
              <User className="w-4.5 h-4.5" />
            </button>
            <div className="w-px h-6 bg-gray-200 mx-1" />
            <button
              onClick={() => { setContent(""); setDraft(null); navigate("/editor"); }}
              className="px-4 py-2 rounded-xl text-sm font-semibold text-indigo-600 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200/60 transition-all duration-200 flex items-center gap-2"
            >
              <PenLine className="w-3.5 h-3.5" />
              New Draft
            </button>
          </div>
        </div>
      </nav>

      <main className="relative max-w-7xl mx-auto px-6 pt-8 pb-12">
        {/* Dashboard Header — URL Input */}
        <section className="mb-8 animate-fade-in-up">
          <div className="flex items-center justify-between mb-6">
            <div>
              <h1 className="text-2xl font-bold text-gray-900 tracking-tight">Dashboard</h1>
              <p className="text-sm text-gray-500 mt-1">Generate, manage, and track your blog content</p>
            </div>
          </div>
          <div className="relative group">
            <div className="absolute -inset-1 bg-gradient-to-r from-indigo-500/20 via-violet-500/20 to-purple-500/20 rounded-2xl blur-lg opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
            <div className="relative flex gap-3 p-2 rounded-2xl bg-white border border-gray-200/80 shadow-lg shadow-indigo-500/5">
              <div className="relative flex-1">
                <Globe className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-300" />
                <input
                  type="url"
                  placeholder="Paste a URL to generate a blog post..."
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !busy && handleGenerate()}
                  disabled={busy}
                  className="w-full pl-12 pr-4 py-3 rounded-xl bg-transparent text-gray-900 placeholder-gray-400 outline-none text-[15px] disabled:opacity-50 transition-opacity"
                />
              </div>
              <button
                onClick={handleGenerate}
                disabled={busy || !url.trim()}
                className="px-6 py-3 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 disabled:from-gray-200 disabled:to-gray-200 disabled:text-gray-400 text-white font-semibold text-sm transition-all duration-300 flex items-center gap-2 shadow-lg shadow-indigo-500/25 hover:shadow-indigo-500/40 disabled:shadow-none"
              >
                {busy ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    {phase === "analyzing" ? "Analyzing..." : "Generating..."}
                  </>
                ) : (
                  <>
                    <Sparkles className="w-4 h-4" />
                    Generate
                  </>
                )}
              </button>
            </div>
            {statusMessage && !error && (
              <div className="mt-3 animate-fade-in">
                <span className="inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-medium bg-indigo-50 text-indigo-600 border border-indigo-200/60">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  {statusMessage}
                </span>
              </div>
            )}
            {error && (
              <div className="mt-3 animate-fade-in">
                <span className="inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-medium bg-red-50 text-red-600 border border-red-200/60">
                  {error}
                </span>
              </div>
            )}
          </div>
        </section>

        {/* Stats Cards */}
        <section className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-8 animate-fade-in-up delay-1">
          {[
            { label: "Total Drafts", value: drafts.length, icon: FileText, color: "indigo", onClick: () => {} },
            { label: "My Drafts", value: userCount, icon: User, color: "violet", onClick: () => { setOriginFilter("user"); setTagFilter(null); } },
            { label: "RSS Generated", value: rssCount, icon: Rss, color: "orange", onClick: () => { setOriginFilter("rss_crawl"); setTagFilter(null); } },
            { label: "Published", value: publishedBlogs.length, icon: BookOpen, color: "cyan", onClick: () => {} },
            { label: "Active Feeds", value: activeFeeds, icon: Newspaper, color: "emerald", onClick: () => navigate("/settings") },
          ].map(({ label, value, icon: Icon, color, onClick }) => (
            <button
              key={label}
              onClick={onClick}
              className={`group p-5 rounded-2xl bg-white border border-gray-200/60 hover:border-${color}-200 hover:shadow-lg hover:shadow-${color}-500/5 transition-all duration-300 text-left`}
            >
              <div className="flex items-center justify-between mb-3">
                <div className={`w-10 h-10 rounded-xl bg-${color}-50 border border-${color}-200/60 flex items-center justify-center group-hover:scale-110 transition-transform duration-300`}>
                  <Icon className={`w-5 h-5 text-${color}-500`} />
                </div>
                <span className="text-2xl font-bold text-gray-900">{value}</span>
              </div>
              <p className="text-xs font-medium text-gray-500">{label}</p>
            </button>
          ))}
        </section>

        {/* Two-Column Dashboard */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6 animate-fade-in-up delay-2">
          {/* Left Column — Drafts (3/5 width) */}
          <div className="lg:col-span-3">
            <div className="bg-white rounded-2xl border border-gray-200/60 overflow-hidden">
              {/* Drafts Header */}
              <div className="p-5 border-b border-gray-100">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
                    <FileText className="w-4 h-4 text-gray-400" />
                    Drafts
                    <span className="text-[10px] font-semibold text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">
                      {drafts.length}
                    </span>
                  </h3>
                  <button
                    onClick={() => { setContent(""); setDraft(null); navigate("/editor"); }}
                    className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold text-indigo-600 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200/60 transition-all duration-200"
                  >
                    <Plus className="w-3 h-3" />
                    New
                  </button>
                </div>

                {/* Filter Tabs */}
                <div className="flex flex-wrap items-center gap-2">
                  {([
                    { key: "all" as const, label: "All", count: drafts.length, icon: null },
                    { key: "user" as const, label: "My Drafts", count: userCount, icon: User },
                    { key: "rss_crawl" as const, label: "RSS Feed", count: rssCount, icon: Rss },
                  ] as const).map(({ key, label, count, icon: Icon }) => (
                    <button
                      key={key}
                      onClick={() => { setOriginFilter(key); setTagFilter(null); }}
                      className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all duration-200 border ${
                        originFilter === key && !tagFilter
                          ? "bg-indigo-50 text-indigo-600 border-indigo-200/60"
                          : "bg-white text-gray-500 border-gray-200/60 hover:text-gray-900 hover:border-gray-300"
                      }`}
                    >
                      {Icon && <Icon className="w-3 h-3" />}
                      {label}
                      <span className="text-[10px] font-semibold text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded-full">
                        {count}
                      </span>
                    </button>
                  ))}
                  {allTags.length > 0 && (
                    <>
                      <div className="w-px h-5 bg-gray-200 mx-1" />
                      {allTags.map((tag) => (
                        <button
                          key={tag}
                          onClick={() => { setTagFilter(tagFilter === tag ? null : tag); setOriginFilter("all"); }}
                          className={`inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all duration-200 border ${
                            tagFilter === tag
                              ? "bg-amber-50 text-amber-600 border-amber-200/60"
                              : "bg-white text-gray-400 border-gray-200/60 hover:text-amber-600 hover:border-amber-200"
                          }`}
                        >
                          <Tag className="w-3 h-3" />
                          {tag}
                        </button>
                      ))}
                    </>
                  )}
                </div>
              </div>

              {/* Draft List */}
              <div className="divide-y divide-gray-50 max-h-[600px] overflow-y-auto">
                {drafts.length === 0 && (
                  <div className="p-12 text-center">
                    <FileText className="w-10 h-10 text-gray-200 mx-auto mb-3" />
                    <p className="text-sm font-medium text-gray-400">No drafts yet</p>
                    <p className="text-xs text-gray-400 mt-1">Generate your first blog from a URL above</p>
                  </div>
                )}
                {filteredDrafts.length === 0 && drafts.length > 0 && (
                  <p className="text-sm text-gray-400 py-8 text-center">
                    No drafts match the current filter.
                  </p>
                )}
                {filteredDrafts.map((draft) => (
                  <div
                    key={draft.id}
                    className="group px-5 py-3.5 hover:bg-gray-50/50 transition-colors duration-200 flex items-center justify-between cursor-pointer"
                    onClick={() => navigate(`/editor/${draft.id}`)}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2.5 mb-1">
                        <div className="w-7 h-7 rounded-lg bg-gray-50 border border-gray-200/60 flex items-center justify-center shrink-0">
                          {draft.sourceType === "github" ? (
                            <Github className="w-3.5 h-3.5 text-gray-500" />
                          ) : (
                            <Globe className="w-3.5 h-3.5 text-gray-500" />
                          )}
                        </div>
                        <h4 className="font-semibold text-sm text-gray-900 truncate">{draft.title}</h4>
                        {draft.origin === "rss_crawl" && (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-orange-50 text-orange-500 border border-orange-200/60 shrink-0">
                            <Rss className="w-2.5 h-2.5" />
                            RSS
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 ml-[36px]">
                        <p className="text-xs text-gray-400 line-clamp-1">{draft.excerpt}</p>
                        {(draft.tags ?? []).length > 0 && (
                          <div className="flex items-center gap-1 shrink-0">
                            {draft.tags.map((tag) => (
                              <span key={tag} className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-gray-50 text-gray-400 border border-gray-200/60">
                                {tag}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-1 ml-4 shrink-0">
                      <span className="text-xs text-gray-400 mr-2 hidden sm:inline">
                        {new Date(draft.updatedAt).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                      </span>
                      <button
                        onClick={(e) => { e.stopPropagation(); navigate(`/editor/${draft.id}`); }}
                        className="p-2 rounded-lg text-gray-300 hover:text-indigo-600 hover:bg-indigo-50 transition-all duration-200 opacity-0 group-hover:opacity-100"
                        title="Edit"
                      >
                        <ArrowRight className="w-3.5 h-3.5" />
                      </button>
                      {draft.sourceUrl && (
                        <a
                          href={draft.sourceUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="p-2 rounded-lg text-gray-300 hover:text-indigo-600 hover:bg-indigo-50 transition-all duration-200 opacity-0 group-hover:opacity-100"
                          title="Open source"
                        >
                          <ExternalLink className="w-3.5 h-3.5" />
                        </a>
                      )}
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDeleteDraft(draft.id); }}
                        className="p-2 rounded-lg text-gray-300 hover:text-red-500 hover:bg-red-50 transition-all duration-200 opacity-0 group-hover:opacity-100"
                        title="Delete"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Right Column — Trending + Activity (2/5 width) */}
          <div className="lg:col-span-2 space-y-6">
            {/* Trending Articles */}
            <div className="bg-white rounded-2xl border border-gray-200/60 overflow-hidden">
              <div className="p-5 border-b border-gray-100">
                <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
                  <TrendingUp className="w-4 h-4 text-indigo-500" />
                  Trending Articles
                  {relevantArticles.length > 0 && (
                    <span className="text-[10px] font-semibold text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">
                      {relevantArticles.length}
                    </span>
                  )}
                </h3>
                <p className="text-xs text-gray-400 mt-1">Ranked by relevance from RSS feeds</p>
              </div>

              <div className="divide-y divide-gray-50 max-h-[400px] overflow-y-auto">
                {relevantArticles.length === 0 && (
                  <div className="p-8 text-center">
                    <TrendingUp className="w-8 h-8 text-gray-200 mx-auto mb-2" />
                    <p className="text-xs font-medium text-gray-400">No trending articles yet</p>
                    <p className="text-xs text-gray-400 mt-1">Configure RSS feeds to discover content</p>
                  </div>
                )}
                {relevantArticles.map((article) => (
                  <div
                    key={article.id}
                    className="group px-5 py-3.5 hover:bg-gray-50/50 transition-colors duration-200"
                  >
                    <div className="flex items-start gap-3">
                      {/* Relevance badge */}
                      <div className={`mt-0.5 w-10 h-10 rounded-xl flex flex-col items-center justify-center shrink-0 border ${
                        article.relevance_score >= 0.8
                          ? "bg-emerald-50 border-emerald-200/60"
                          : article.relevance_score >= 0.5
                          ? "bg-amber-50 border-amber-200/60"
                          : "bg-gray-50 border-gray-200/60"
                      }`}>
                        <span className={`text-xs font-bold ${
                          article.relevance_score >= 0.8
                            ? "text-emerald-600"
                            : article.relevance_score >= 0.5
                            ? "text-amber-600"
                            : "text-gray-500"
                        }`}>
                          {Math.round(article.relevance_score * 100)}
                        </span>
                        <span className="text-[8px] text-gray-400">%</span>
                      </div>

                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5 mb-1">
                          <h4 className="text-sm font-semibold text-gray-900 line-clamp-2 leading-snug">{article.title}</h4>
                          {article.linkedin_post_id && (
                            <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold bg-blue-50 text-blue-600 border border-blue-200/60 shrink-0" title="Published to LinkedIn">
                              <Linkedin className="w-2.5 h-2.5" />
                              LI
                            </span>
                          )}
                          {article.status === "drafted" && (
                            <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold bg-cyan-50 text-cyan-600 border border-cyan-200/60 shrink-0">
                              <FileText className="w-2.5 h-2.5" />
                              Draft
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-1.5 flex-wrap">
                          {article.matched_topics.map((topic) => (
                            <span key={topic} className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-indigo-50 text-indigo-500 border border-indigo-200/60">
                              {topic}
                            </span>
                          ))}
                          {article.matched_keywords.slice(0, 2).map((kw) => (
                            <span key={kw} className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-violet-50 text-violet-500 border border-violet-200/60">
                              {kw}
                            </span>
                          ))}
                        </div>
                      </div>

                      {/* Actions */}
                      <div className="flex items-center gap-1 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                        {article.draft_id && (
                          <button
                            onClick={() => navigate(`/editor/${article.draft_id}`)}
                            className="p-1.5 rounded-lg text-gray-300 hover:text-indigo-600 hover:bg-indigo-50 transition-all duration-200"
                            title="Open draft"
                          >
                            <PenLine className="w-3.5 h-3.5" />
                          </button>
                        )}
                        <a
                          href={article.article_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="p-1.5 rounded-lg text-gray-300 hover:text-indigo-600 hover:bg-indigo-50 transition-all duration-200"
                          title="Open article"
                        >
                          <ExternalLink className="w-3.5 h-3.5" />
                        </a>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Published Blogs */}
            <div className="bg-white rounded-2xl border border-gray-200/60 overflow-hidden">
              <div className="p-5 border-b border-gray-100">
                <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
                  <BookOpen className="w-4 h-4 text-cyan-500" />
                  Published
                  {publishedBlogs.length > 0 && (
                    <span className="text-[10px] font-semibold text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">
                      {publishedBlogs.length}
                    </span>
                  )}
                </h3>
              </div>

              <div className="divide-y divide-gray-50 max-h-[300px] overflow-y-auto">
                {publishedBlogs.length === 0 && (
                  <div className="p-8 text-center">
                    <BookOpen className="w-8 h-8 text-gray-200 mx-auto mb-2" />
                    <p className="text-xs font-medium text-gray-400">No published blogs yet</p>
                  </div>
                )}
                {publishedBlogs.map((blog) => (
                  <div
                    key={blog.id}
                    className="group px-5 py-3 hover:bg-gray-50/50 transition-colors duration-200 cursor-pointer"
                    onClick={() => navigate(`/blog/${blog.slug}`)}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                      <h4 className="text-sm font-semibold text-gray-900 truncate">{blog.title}</h4>
                    </div>
                    <div className="flex items-center gap-2 ml-[22px]">
                      <p className="text-xs text-gray-400 line-clamp-1 flex-1">{blog.excerpt}</p>
                      <span className="text-[10px] text-gray-400 shrink-0">
                        {new Date(blog.publishedAt).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Recent Crawl Activity */}
            <div className="bg-white rounded-2xl border border-gray-200/60 overflow-hidden">
              <div className="p-5 border-b border-gray-100">
                <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
                  <BarChart3 className="w-4 h-4 text-emerald-500" />
                  Recent Activity
                </h3>
              </div>

              <div className="divide-y divide-gray-50">
                {recentJobs.length === 0 && (
                  <div className="p-8 text-center">
                    <Clock className="w-8 h-8 text-gray-200 mx-auto mb-2" />
                    <p className="text-xs font-medium text-gray-400">No recent crawls</p>
                  </div>
                )}
                {recentJobs.map((job) => {
                  const feed = feeds.find((f) => f.id === job.feed_source_id);
                  return (
                    <div key={job.id} className="px-5 py-3 flex items-center gap-3">
                      <div className={`w-2 h-2 rounded-full shrink-0 ${
                        job.status === "completed" ? "bg-emerald-400" : job.status === "failed" ? "bg-red-400" : "bg-amber-400"
                      }`} />
                      <div className="flex-1 min-w-0">
                        <p className="text-xs font-medium text-gray-700 truncate">
                          {feed?.name || "Unknown feed"}
                        </p>
                        <p className="text-[10px] text-gray-400">
                          {job.articles_found} found &middot; {job.articles_relevant} relevant &middot; {job.articles_processed} processed
                        </p>
                      </div>
                      <span className="text-[10px] text-gray-400 shrink-0">
                        {job.completed_at
                          ? new Date(job.completed_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })
                          : "In progress"}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <footer className="pt-10 pb-4 text-center">
          <p className="text-xs text-gray-400">
            Built with Azure OpenAI GPT-4o &middot; FastAPI &middot; React
          </p>
        </footer>
      </main>
    </div>
  );
}
