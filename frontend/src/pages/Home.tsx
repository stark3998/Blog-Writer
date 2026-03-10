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
      .catch(() => {
        /* Cosmos not configured — that's fine */
      });
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

        // Auto-save to Cosmos if available
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
            // Cosmos not configured — go to editor without saving
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
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white">
      {/* Header */}
      <header className="border-b border-slate-700/50 backdrop-blur-sm">
        <div className="max-w-5xl mx-auto px-6 py-4 flex items-center gap-3">
          <Sparkles className="w-6 h-6 text-indigo-400" />
          <h1 className="text-xl font-bold tracking-tight">Blog Writer</h1>
          <span className="text-xs text-slate-400 ml-1">AI-Powered</span>
        </div>
      </header>

      {/* Hero */}
      <main className="max-w-5xl mx-auto px-6 py-16">
        <div className="text-center mb-12">
          <h2 className="text-4xl font-extrabold tracking-tight mb-4 bg-gradient-to-r from-indigo-400 to-cyan-400 bg-clip-text text-transparent">
            Generate blogs from any URL
          </h2>
          <p className="text-slate-400 text-lg max-w-2xl mx-auto">
            Paste a GitHub repo or webpage link. AI analyzes the source and writes a polished blog
            post you can edit, preview, and export.
          </p>
        </div>

        {/* URL input */}
        <div className="max-w-2xl mx-auto mb-8">
          <div className="flex gap-3">
            <div className="relative flex-1">
              <Globe className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-500" />
              <input
                type="url"
                placeholder="https://github.com/user/repo or any webpage URL"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && !busy && handleGenerate()}
                disabled={busy}
                className="w-full pl-11 pr-4 py-3 rounded-lg bg-slate-800 border border-slate-600 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none transition-all text-white placeholder-slate-500 disabled:opacity-50"
              />
            </div>
            <button
              onClick={handleGenerate}
              disabled={busy || !url.trim()}
              className="px-6 py-3 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-700 disabled:text-slate-500 font-semibold transition-colors flex items-center gap-2"
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
            <p className="mt-3 text-sm text-indigo-300 text-center">{statusMessage}</p>
          )}
          {error && <p className="mt-3 text-sm text-red-400 text-center">{error}</p>}
        </div>

        {/* Quick actions */}
        <div className="flex justify-center gap-4 mb-16">
          <button
            onClick={() => {
              setContent("");
              setDraft(null);
              navigate("/editor");
            }}
            className="px-4 py-2 rounded-lg bg-slate-800 border border-slate-700 hover:border-slate-500 text-sm text-slate-300 transition-colors flex items-center gap-2"
          >
            <PenLine className="w-4 h-4" />
            Start from scratch
          </button>
        </div>

        {/* Saved Drafts */}
        {drafts.length > 0 && (
          <section>
            <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
              <FileText className="w-5 h-5 text-slate-400" />
              Your Drafts
            </h3>
            <div className="grid gap-3">
              {drafts.map((draft) => (
                <div
                  key={draft.id}
                  className="p-4 rounded-lg bg-slate-800/50 border border-slate-700/50 hover:border-slate-600 transition-colors flex items-start justify-between group"
                >
                  <div
                    className="flex-1 cursor-pointer"
                    onClick={() => navigate(`/editor/${draft.id}`)}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      {draft.sourceType === "github" ? (
                        <Github className="w-4 h-4 text-slate-400" />
                      ) : (
                        <Globe className="w-4 h-4 text-slate-400" />
                      )}
                      <h4 className="font-medium">{draft.title}</h4>
                    </div>
                    <p className="text-sm text-slate-400 line-clamp-1">{draft.excerpt}</p>
                    <p className="text-xs text-slate-500 mt-1">
                      {new Date(draft.updatedAt).toLocaleDateString()}
                    </p>
                  </div>
                  <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={() => navigate(`/editor/${draft.id}`)}
                      className="p-2 rounded hover:bg-slate-700 text-slate-400 hover:text-white transition-colors"
                      title="Edit"
                    >
                      <PenLine className="w-4 h-4" />
                    </button>
                    {draft.sourceUrl && (
                      <a
                        href={draft.sourceUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="p-2 rounded hover:bg-slate-700 text-slate-400 hover:text-white transition-colors"
                        title="Open source"
                      >
                        <ExternalLink className="w-4 h-4" />
                      </a>
                    )}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDeleteDraft(draft.id);
                      }}
                      className="p-2 rounded hover:bg-slate-700 text-slate-400 hover:text-red-400 transition-colors"
                      title="Delete"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
