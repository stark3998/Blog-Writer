import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
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

  return (
    <div className="min-h-screen bg-[#0b0f1a] text-white">
      {/* Ambient background glow */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-[-20%] left-[20%] w-[600px] h-[600px] bg-indigo-600/[0.04] rounded-full blur-[120px]" />
        <div className="absolute bottom-[-10%] right-[15%] w-[500px] h-[500px] bg-cyan-600/[0.03] rounded-full blur-[100px]" />
      </div>

      {/* Header */}
      <header className="relative border-b border-white/[0.06] backdrop-blur-md bg-[#0b0f1a]/80 animate-fade-in-down">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-indigo-500/20">
              <Sparkles className="w-4 h-4 text-white" />
            </div>
            <div className="flex items-baseline gap-2">
              <h1 className="text-lg font-semibold tracking-tight text-white">Blog Writer</h1>
              <span className="text-[11px] font-medium text-indigo-400/70 tracking-wide uppercase">AI-Powered</span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => navigate("/settings")}
              className="p-2 rounded-lg text-slate-400 border border-white/[0.08] hover:border-white/[0.15] hover:text-white hover:bg-white/[0.04] transition-all duration-200"
              title="Settings"
            >
              <Settings className="w-4 h-4" />
            </button>
            <button
              onClick={() => {
                setContent("");
                setDraft(null);
                navigate("/editor");
              }}
              className="px-4 py-2 rounded-lg text-sm font-medium text-slate-400 border border-white/[0.08] hover:border-white/[0.15] hover:text-white hover:bg-white/[0.04] transition-all duration-200"
            >
              <PenLine className="w-3.5 h-3.5 inline mr-2" />
              New Draft
            </button>
          </div>
        </div>
      </header>

      <main className="relative max-w-6xl mx-auto px-6">
        {/* Hero Section */}
        <section className="pt-20 pb-16">
          <div className="max-w-3xl mx-auto text-center">
            <h2 className="text-5xl font-extrabold tracking-tight leading-[1.1] mb-6 animate-fade-in-up">
              <span className="text-white">Generate blogs </span>
              <span className="bg-gradient-to-r from-indigo-400 via-indigo-300 to-cyan-400 bg-clip-text text-transparent animate-gradient">
                from any URL
              </span>
            </h2>
            <p className="text-lg text-slate-400 leading-relaxed max-w-xl mx-auto animate-fade-in-up delay-1">
              Paste a GitHub repo or webpage link. AI analyzes the source and writes
              a polished, publication-ready blog post.
            </p>
          </div>

          {/* URL Input */}
          <div className="max-w-2xl mx-auto mt-12 animate-fade-in-up delay-2">
            <div className="p-1 rounded-2xl bg-gradient-to-r from-indigo-500/20 via-transparent to-cyan-500/20">
              <div className="flex gap-3 p-2 rounded-xl bg-[#0f1629] border border-white/[0.06]">
                <div className="relative flex-1">
                  <Globe className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-500" />
                  <input
                    type="url"
                    placeholder="https://github.com/user/repo or any webpage URL"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && !busy && handleGenerate()}
                    disabled={busy}
                    className="w-full pl-12 pr-4 py-3.5 rounded-lg bg-transparent text-white placeholder-slate-500 outline-none text-[15px] disabled:opacity-50 transition-opacity"
                  />
                </div>
                <button
                  onClick={handleGenerate}
                  disabled={busy || !url.trim()}
                  className="px-7 py-3.5 rounded-lg bg-gradient-to-r from-indigo-600 to-indigo-500 hover:from-indigo-500 hover:to-indigo-400 disabled:from-slate-700 disabled:to-slate-700 disabled:text-slate-500 font-semibold text-sm transition-all duration-300 flex items-center gap-2 shadow-lg shadow-indigo-500/20 hover:shadow-indigo-500/30 disabled:shadow-none hover:-translate-y-[1px] active:translate-y-0"
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
              <div className="mt-4 flex justify-center animate-fade-in">
                <span className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full text-sm font-medium bg-indigo-500/10 text-indigo-300 border border-indigo-500/20">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  {statusMessage}
                </span>
              </div>
            )}
            {error && (
              <div className="mt-4 flex justify-center animate-fade-in">
                <span className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full text-sm font-medium bg-red-500/10 text-red-300 border border-red-500/20">
                  {error}
                </span>
              </div>
            )}
          </div>
        </section>

        {/* Features */}
        <section className="py-12 border-t border-white/[0.04] animate-fade-in-up delay-3">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {[
              {
                icon: Zap,
                title: "Smart Analysis",
                desc: "Auto-detects GitHub repos vs. webpages and extracts key content, structure, and metadata.",
              },
              {
                icon: Eye,
                title: "Live Preview",
                desc: "Split-pane Monaco editor with real-time Markdown preview, Mermaid diagrams, and syntax highlighting.",
              },
              {
                icon: Download,
                title: "Multi-Format Export",
                desc: "Export as Markdown, HTML, PDF, DOCX, or MDX. Publish directly to GitHub as a PR.",
              },
            ].map(({ icon: Icon, title, desc }) => (
              <div
                key={title}
                className="group p-6 rounded-xl bg-white/[0.02] border border-white/[0.06] hover:border-white/[0.1] hover:bg-white/[0.04] transition-all duration-300"
              >
                <div className="w-10 h-10 rounded-lg bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center mb-4 group-hover:bg-indigo-500/15 transition-colors duration-300">
                  <Icon className="w-5 h-5 text-indigo-400" />
                </div>
                <h3 className="text-sm font-semibold text-white mb-1.5">{title}</h3>
                <p className="text-sm text-slate-400 leading-relaxed">{desc}</p>
              </div>
            ))}
          </div>
        </section>

        {/* Saved Drafts */}
        {drafts.length > 0 && (
          <section className="py-12 border-t border-white/[0.04] animate-fade-in-up delay-4">
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-base font-semibold text-white flex items-center gap-2.5">
                <FileText className="w-4.5 h-4.5 text-slate-500" />
                Your Drafts
                <span className="text-xs font-medium text-slate-500 bg-white/[0.04] px-2 py-0.5 rounded-full">
                  {drafts.length}
                </span>
              </h3>
            </div>
            <div className="grid gap-2">
              {drafts.map((draft, i) => (
                <div
                  key={draft.id}
                  className="group p-4 rounded-xl bg-white/[0.02] border border-white/[0.06] hover:border-white/[0.12] hover:bg-white/[0.04] transition-all duration-300 flex items-center justify-between animate-fade-in-up"
                  style={{ animationDelay: `${i * 60}ms` }}
                >
                  <div
                    className="flex-1 cursor-pointer min-w-0"
                    onClick={() => navigate(`/editor/${draft.id}`)}
                  >
                    <div className="flex items-center gap-2.5 mb-1">
                      <div className="w-6 h-6 rounded-md bg-white/[0.04] flex items-center justify-center shrink-0">
                        {draft.sourceType === "github" ? (
                          <Github className="w-3.5 h-3.5 text-slate-400" />
                        ) : (
                          <Globe className="w-3.5 h-3.5 text-slate-400" />
                        )}
                      </div>
                      <h4 className="font-medium text-sm text-white truncate">{draft.title}</h4>
                    </div>
                    <p className="text-xs text-slate-500 line-clamp-1 ml-[34px]">{draft.excerpt}</p>
                  </div>
                  <div className="flex items-center gap-1 ml-4 shrink-0">
                    <span className="text-xs text-slate-600 mr-2 hidden sm:inline">
                      {new Date(draft.updatedAt).toLocaleDateString(undefined, {
                        month: "short",
                        day: "numeric",
                      })}
                    </span>
                    <button
                      onClick={() => navigate(`/editor/${draft.id}`)}
                      className="p-2 rounded-lg text-slate-500 hover:text-white hover:bg-white/[0.06] transition-all duration-200 opacity-0 group-hover:opacity-100"
                      title="Edit"
                    >
                      <ArrowRight className="w-3.5 h-3.5" />
                    </button>
                    {draft.sourceUrl && (
                      <a
                        href={draft.sourceUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="p-2 rounded-lg text-slate-500 hover:text-white hover:bg-white/[0.06] transition-all duration-200 opacity-0 group-hover:opacity-100"
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
                      className="p-2 rounded-lg text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-all duration-200 opacity-0 group-hover:opacity-100"
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
        <footer className="py-8 border-t border-white/[0.04] text-center">
          <p className="text-xs text-slate-600">
            Built with Azure OpenAI GPT-4o &middot; FastAPI &middot; React
          </p>
        </footer>
      </main>
    </div>
  );
}
