import { useEffect, useState, useCallback } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import MonacoEditorWrapper from "../components/MonacoEditor";
import MarkdownPreview from "../components/MarkdownPreview";
import AIEditPanel from "../components/AIEditPanel";
import ExportDropdown from "../components/ExportDropdown";
import { useBlogStore } from "../store/blogStore";
import { getDraft, updateDraft, createDraft, publishBlog } from "../services/api";
import {
  ArrowLeft,
  Save,
  Sparkles,
  PanelLeftOpen,
  Eye,
  Code,
  Upload,
  Loader2,
  Check,
} from "lucide-react";

type ViewMode = "split" | "editor" | "preview";

export default function Editor() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { content, setContent, draft, setDraft, error, setError } = useBlogStore();

  const [viewMode, setViewMode] = useState<ViewMode>("split");
  const [showAI, setShowAI] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saved" | "error">("idle");
  const [publishing, setPublishing] = useState(false);
  const [publishResult, setPublishResult] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    getDraft(id)
      .then((d) => {
        setDraft(d);
        setContent(d.content);
      })
      .catch(() => setError("Failed to load draft"));
  }, [id, setDraft, setContent, setError]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    setSaveStatus("idle");
    try {
      if (draft?.id) {
        const updated = await updateDraft(draft.id, { content });
        setDraft(updated);
      } else {
        const titleMatch = content.match(/title:\s*["']?(.+?)["']?\s*$/m);
        const slugMatch = content.match(/slug:\s*["']?(.+?)["']?\s*$/m);
        const excerptMatch = content.match(/excerpt:\s*["']?(.+?)["']?\s*$/m);

        const saved = await createDraft({
          title: titleMatch?.[1] ?? "Untitled",
          slug: slugMatch?.[1] ?? `blog-${Date.now()}`,
          excerpt: excerptMatch?.[1] ?? "",
          content,
          source_url: "",
          source_type: "manual",
        });
        setDraft(saved);
        navigate(`/editor/${saved.id}`, { replace: true });
      }
      setSaveStatus("saved");
      setTimeout(() => setSaveStatus("idle"), 2000);
    } catch {
      setSaveStatus("error");
      setTimeout(() => setSaveStatus("idle"), 3000);
    } finally {
      setSaving(false);
    }
  }, [content, draft, setDraft, navigate]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        handleSave();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [handleSave]);

  const handlePublish = async () => {
    setPublishing(true);
    setPublishResult(null);
    try {
      const titleMatch = content.match(/title:\s*["']?(.+?)["']?\s*$/m);
      const slugMatch = content.match(/slug:\s*["']?(.+?)["']?\s*$/m);
      const excerptMatch = content.match(/excerpt:\s*["']?(.+?)["']?\s*$/m);

      const result = await publishBlog({
        content,
        slug: slugMatch?.[1] ?? `blog-${Date.now()}`,
        title: titleMatch?.[1] ?? "Untitled",
        excerpt: excerptMatch?.[1] ?? "",
      });
      setPublishResult(result.pr_url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Publish failed");
    } finally {
      setPublishing(false);
    }
  };

  return (
    <div className="h-screen flex flex-col bg-[#0b0f1a] text-white">
      {/* Toolbar */}
      <header className="flex items-center justify-between px-4 py-2.5 border-b border-white/[0.06] bg-[#0b0f1a]/90 backdrop-blur-md shrink-0 animate-fade-in-down">
        <div className="flex items-center gap-3">
          <Link
            to="/"
            className="p-2 rounded-lg text-slate-500 hover:text-white hover:bg-white/[0.06] transition-all duration-200"
            title="Back to Home"
          >
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div className="w-px h-5 bg-white/[0.06]" />
          <span className="text-sm text-slate-400 truncate max-w-xs font-medium">
            {draft?.title ?? "Untitled Draft"}
          </span>
        </div>

        <div className="flex items-center gap-1.5">
          {/* View mode toggles */}
          <div className="flex bg-white/[0.04] rounded-lg p-0.5 mr-1 border border-white/[0.06]">
            {([
              { mode: "editor" as ViewMode, icon: Code, label: "Editor only" },
              { mode: "split" as ViewMode, icon: PanelLeftOpen, label: "Split view" },
              { mode: "preview" as ViewMode, icon: Eye, label: "Preview only" },
            ]).map(({ mode, icon: Icon, label }) => (
              <button
                key={mode}
                onClick={() => setViewMode(mode)}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all duration-200 ${
                  viewMode === mode
                    ? "bg-white/[0.08] text-white shadow-sm"
                    : "text-slate-500 hover:text-slate-300"
                }`}
                title={label}
              >
                <Icon className="w-3.5 h-3.5" />
              </button>
            ))}
          </div>

          {/* AI Toggle */}
          <button
            onClick={() => setShowAI(!showAI)}
            className={`p-2 rounded-lg transition-all duration-200 ${
              showAI
                ? "bg-indigo-500/15 text-indigo-400 border border-indigo-500/25"
                : "text-slate-500 hover:text-white hover:bg-white/[0.06] border border-transparent"
            }`}
            title="AI Editor"
          >
            <Sparkles className="w-4 h-4" />
          </button>

          {/* Export */}
          <ExportDropdown content={content} />

          <div className="w-px h-5 bg-white/[0.06] mx-1" />

          {/* Save */}
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-3.5 py-1.5 rounded-lg text-sm font-medium transition-all duration-200 flex items-center gap-1.5 disabled:opacity-50 border border-white/[0.06] text-slate-400 hover:text-white hover:bg-white/[0.04] hover:border-white/[0.1]"
            title="Save (Ctrl+S)"
          >
            {saving ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : saveStatus === "saved" ? (
              <Check className="w-3.5 h-3.5 text-emerald-400" />
            ) : (
              <Save className="w-3.5 h-3.5" />
            )}
            {saveStatus === "saved" ? "Saved" : "Save"}
          </button>

          {/* Publish */}
          <button
            onClick={handlePublish}
            disabled={publishing || !content.trim()}
            className="px-3.5 py-1.5 rounded-lg bg-gradient-to-r from-indigo-600 to-indigo-500 hover:from-indigo-500 hover:to-indigo-400 disabled:from-slate-700 disabled:to-slate-700 disabled:text-slate-500 text-sm font-medium transition-all duration-300 flex items-center gap-1.5 shadow-sm shadow-indigo-500/15 hover:shadow-indigo-500/25 disabled:shadow-none"
          >
            {publishing ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Upload className="w-3.5 h-3.5" />
            )}
            Publish
          </button>
        </div>
      </header>

      {/* Error / PR banner */}
      {error && (
        <div className="px-4 py-2.5 bg-red-500/[0.08] text-red-300 text-sm border-b border-red-500/20 flex items-center animate-fade-in-down">
          <span className="flex-1">{error}</span>
          <button onClick={() => setError(null)} className="text-red-400 hover:text-red-300 text-xs font-medium ml-4 underline underline-offset-2 transition-colors">
            dismiss
          </button>
        </div>
      )}
      {publishResult && (
        <div className="px-4 py-2.5 bg-emerald-500/[0.08] text-emerald-300 text-sm border-b border-emerald-500/20 flex items-center animate-fade-in-down">
          <span className="flex-1">
            Published!{" "}
            <a href={publishResult} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 font-medium hover:text-emerald-200 transition-colors">
              View PR
            </a>
          </span>
          <button onClick={() => setPublishResult(null)} className="text-emerald-400 hover:text-emerald-300 text-xs font-medium ml-4 underline underline-offset-2 transition-colors">
            dismiss
          </button>
        </div>
      )}

      {/* Main content area */}
      <div className="flex-1 flex overflow-hidden">
        {/* Editor pane */}
        {viewMode !== "preview" && (
          <div
            className={`${viewMode === "split" ? "w-1/2" : "w-full"} h-full border-r border-white/[0.06] transition-all duration-300 animate-fade-in`}
          >
            <MonacoEditorWrapper
              value={content}
              onChange={setContent}
            />
          </div>
        )}

        {/* Preview pane */}
        {viewMode !== "editor" && (
          <div
            className={`${viewMode === "split" ? "w-1/2" : "w-full"} h-full overflow-auto transition-all duration-300 animate-fade-in`}
          >
            <MarkdownPreview content={content} />
          </div>
        )}

        {/* AI Panel (slides in from right) */}
        <div
          className={`shrink-0 border-l border-white/[0.06] bg-[#0d1220]/80 backdrop-blur-md overflow-hidden transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
            showAI ? "w-80 opacity-100" : "w-0 opacity-0"
          }`}
        >
          {showAI && (
            <div className="w-80 h-full animate-slide-in-right">
              <AIEditPanel onClose={() => setShowAI(false)} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
