import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useBlogStore } from "../store/blogStore";
import { generateBlogStream, listDrafts, createDraft, deleteDraft } from "../services/api";
import type { GenerateResult } from "../types";
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
  Zap,
  Eye,
  Download,
  Settings,
  Rss,
  Activity,
  Tag,
  User,
} from "lucide-react";

export default function Home() {
  const navigate = useNavigate();
  const [url, setUrl] = useState("");
  const { drafts, setDrafts, phase, setPhase, setContent, setError, error, statusMessage, setStatusMessage, setDraft } =
    useBlogStore();
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    listDrafts()
      .then(setDrafts)
      .catch(() => {});
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
        <div className="absolute top-[20%] left-[50%] w-[400px] h-[400px] bg-cyan-200/15 rounded-full blur-[100px] animate-float" style={{ animationDelay: "1.5s" }} />
      </div>

      {/* Navigation */}
      <nav className="relative glass-strong border-b border-indigo-100/60 animate-fade-in-down sticky top-0 z-40">
        <div className="max-w-6xl mx-auto px-6 py-3 flex items-center justify-between">
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
            <button
              onClick={() => navigate("/settings")}
              className="p-2.5 rounded-xl text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 transition-all duration-200"
              title="Feed Settings"
            >
              <Rss className="w-4.5 h-4.5" />
            </button>
            <button
              onClick={() => navigate("/prompts")}
              className="p-2.5 rounded-xl text-gray-400 hover:text-purple-600 hover:bg-purple-50 transition-all duration-200"
              title="Prompt Editor"
            >
              <FileText className="w-4.5 h-4.5" />
            </button>
            <button
              onClick={() => navigate("/keywords")}
              className="p-2.5 rounded-xl text-gray-400 hover:text-amber-600 hover:bg-amber-50 transition-all duration-200"
              title="Keyword Manager"
            >
              <Tag className="w-4.5 h-4.5" />
            </button>
            <button
              onClick={() => navigate("/settings")}
              className="p-2.5 rounded-xl text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 transition-all duration-200"
              title="Settings"
            >
              <Settings className="w-4.5 h-4.5" />
            </button>
            <button
              onClick={() => navigate("/diagnostics")}
              className="p-2.5 rounded-xl text-gray-400 hover:text-emerald-600 hover:bg-emerald-50 transition-all duration-200"
              title="Diagnostics"
            >
              <Activity className="w-4.5 h-4.5" />
            </button>
            <div className="w-px h-6 bg-gray-200 mx-1" />
            <button
              onClick={() => {
                setContent("");
                setDraft(null);
                navigate("/editor");
              }}
              className="px-4 py-2 rounded-xl text-sm font-semibold text-indigo-600 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200/60 transition-all duration-200 flex items-center gap-2"
            >
              <PenLine className="w-3.5 h-3.5" />
              New Draft
            </button>
          </div>
        </div>
      </nav>

      <main className="relative max-w-6xl mx-auto px-6">
        {/* Hero Section */}
        <section className="pt-24 pb-20">
          <div className="max-w-3xl mx-auto text-center">
            <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-indigo-50 border border-indigo-200/60 text-indigo-600 text-xs font-semibold mb-8 animate-fade-in-up">
              <Zap className="w-3.5 h-3.5" />
              Powered by GPT-4o
            </div>
            <h2 className="text-5xl sm:text-6xl font-extrabold tracking-tight leading-[1.08] mb-6 animate-fade-in-up delay-1">
              <span className="text-gray-900">Generate blogs</span>
              <br />
              <span className="bg-gradient-to-r from-indigo-600 via-violet-500 to-purple-500 bg-clip-text text-transparent animate-gradient">
                from any URL
              </span>
            </h2>
            <p className="text-lg text-gray-500 leading-relaxed max-w-xl mx-auto animate-fade-in-up delay-2">
              Paste a GitHub repo or webpage link. AI analyzes the source and writes
              a polished, publication-ready blog post in seconds.
            </p>
          </div>

          {/* URL Input */}
          <div className="max-w-2xl mx-auto mt-12 animate-fade-in-up delay-3">
            <div className="relative group">
              <div className="absolute -inset-1 bg-gradient-to-r from-indigo-500/20 via-violet-500/20 to-purple-500/20 rounded-2xl blur-lg opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
              <div className="relative flex gap-3 p-2 rounded-2xl bg-white border border-gray-200/80 shadow-xl shadow-indigo-500/5">
                <div className="relative flex-1">
                  <Globe className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-300" />
                  <input
                    type="url"
                    placeholder="https://github.com/user/repo or any webpage URL"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && !busy && handleGenerate()}
                    disabled={busy}
                    className="w-full pl-12 pr-4 py-3.5 rounded-xl bg-transparent text-gray-900 placeholder-gray-400 outline-none text-[15px] disabled:opacity-50 transition-opacity"
                  />
                </div>
                <button
                  onClick={handleGenerate}
                  disabled={busy || !url.trim()}
                  className="px-7 py-3.5 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 disabled:from-gray-200 disabled:to-gray-200 disabled:text-gray-400 text-white font-semibold text-sm transition-all duration-300 flex items-center gap-2 shadow-lg shadow-indigo-500/25 hover:shadow-indigo-500/40 disabled:shadow-none hover:-translate-y-[1px] active:translate-y-0"
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
            </div>

            {/* Status / Error */}
            {statusMessage && !error && (
              <div className="mt-5 flex justify-center animate-fade-in">
                <span className="inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-medium bg-indigo-50 text-indigo-600 border border-indigo-200/60">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  {statusMessage}
                </span>
              </div>
            )}
            {error && (
              <div className="mt-5 flex justify-center animate-fade-in">
                <span className="inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-medium bg-red-50 text-red-600 border border-red-200/60">
                  {error}
                </span>
              </div>
            )}
          </div>
        </section>

        {/* Features */}
        <section className="py-16 border-t border-gray-100 animate-fade-in-up delay-4">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
            {[
              {
                icon: Zap,
                title: "Smart Analysis",
                desc: "Auto-detects GitHub repos vs. webpages and extracts key content, structure, and metadata.",
                color: "indigo",
              },
              {
                icon: Eye,
                title: "Live Preview",
                desc: "Split-pane Monaco editor with real-time Markdown preview, Mermaid diagrams, and syntax highlighting.",
                color: "violet",
              },
              {
                icon: Download,
                title: "Multi-Format Export",
                desc: "Export as Markdown, HTML, PDF, DOCX, or MDX. Publish directly to GitHub as a PR.",
                color: "purple",
              },
            ].map(({ icon: Icon, title, desc, color }) => (
              <div
                key={title}
                className="group p-6 rounded-2xl bg-white border border-gray-200/60 hover:border-indigo-200 hover:shadow-lg hover:shadow-indigo-500/5 transition-all duration-300"
              >
                <div className={`w-11 h-11 rounded-xl bg-${color}-50 border border-${color}-200/60 flex items-center justify-center mb-4 group-hover:scale-110 transition-transform duration-300`}>
                  <Icon className={`w-5 h-5 text-${color}-500`} />
                </div>
                <h3 className="text-sm font-bold text-gray-900 mb-1.5">{title}</h3>
                <p className="text-sm text-gray-500 leading-relaxed">{desc}</p>
              </div>
            ))}
          </div>
        </section>

        {/* Saved Drafts */}
        {drafts.length > 0 && (
          <section className="py-12 border-t border-gray-100 animate-fade-in-up delay-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-base font-bold text-gray-900 flex items-center gap-2.5">
                <FileText className="w-4.5 h-4.5 text-gray-400" />
                Your Drafts
                <span className="text-xs font-semibold text-gray-400 bg-gray-100 px-2.5 py-0.5 rounded-full">
                  {drafts.length}
                </span>
              </h3>
            </div>

            {/* Filter Tabs */}
            <div className="flex flex-wrap items-center gap-2 mb-5">
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
                      onClick={() => {
                        setTagFilter(tagFilter === tag ? null : tag);
                        setOriginFilter("all");
                      }}
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

            {/* Draft List */}
            <div className="grid gap-2">
              {filteredDrafts.length === 0 && (
                <p className="text-sm text-gray-400 py-8 text-center">
                  No drafts match the current filter.
                </p>
              )}
              {filteredDrafts.map((draft, i) => (
                <div
                  key={draft.id}
                  className="group p-4 rounded-xl bg-white border border-gray-200/60 hover:border-indigo-200 hover:shadow-md hover:shadow-indigo-500/5 transition-all duration-300 flex items-center justify-between animate-fade-in-up"
                  style={{ animationDelay: `${i * 60}ms` }}
                >
                  <div
                    className="flex-1 cursor-pointer min-w-0"
                    onClick={() => navigate(`/editor/${draft.id}`)}
                  >
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
                            <span
                              key={tag}
                              className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-gray-50 text-gray-400 border border-gray-200/60"
                            >
                              {tag}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 ml-4 shrink-0">
                    <span className="text-xs text-gray-400 mr-2 hidden sm:inline">
                      {new Date(draft.updatedAt).toLocaleDateString(undefined, {
                        month: "short",
                        day: "numeric",
                      })}
                    </span>
                    <button
                      onClick={() => navigate(`/editor/${draft.id}`)}
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
                        className="p-2 rounded-lg text-gray-300 hover:text-indigo-600 hover:bg-indigo-50 transition-all duration-200 opacity-0 group-hover:opacity-100"
                        title="Open source"
                      >
                        <ExternalLink className="w-3.5 h-3.5" />
                      </a>
                    )}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDeleteDraft(draft.id);
                      }}
                      className="p-2 rounded-lg text-gray-300 hover:text-red-500 hover:bg-red-50 transition-all duration-200 opacity-0 group-hover:opacity-100"
                      title="Delete"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Footer */}
        <footer className="py-10 border-t border-gray-100 text-center">
          <p className="text-xs text-gray-400">
            Built with Azure OpenAI GPT-4o &middot; FastAPI &middot; React
          </p>
        </footer>
      </main>
    </div>
  );
}
