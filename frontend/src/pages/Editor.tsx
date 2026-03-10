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

  // Load draft from Cosmos if ID present
  useEffect(() => {
    if (!id) return;
    getDraft(id)
      .then((d) => {
        setDraft(d);
        setContent(d.content);
      })
      .catch(() => setError("Failed to load draft"));
  }, [id, setDraft, setContent, setError]);

  // Save handler
  const handleSave = useCallback(async () => {
    setSaving(true);
    setSaveStatus("idle");
    try {
      if (draft?.id) {
        const updated = await updateDraft(draft.id, { content });
        setDraft(updated);
      } else {
        // Extract title from frontmatter
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

  // Ctrl+S
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

  // Publish to GitHub
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
    <div className="h-screen flex flex-col bg-slate-900 text-white">
      {/* Toolbar */}
      <header className="flex items-center justify-between px-4 py-2 border-b border-slate-700/50 bg-slate-900/90 backdrop-blur-sm shrink-0">
        <div className="flex items-center gap-3">
          <Link
            to="/"
            className="p-2 rounded hover:bg-slate-800 text-slate-400 hover:text-white transition-colors"
            title="Back to Home"
          >
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <span className="text-sm text-slate-400 truncate max-w-xs">
            {draft?.title ?? "Untitled Draft"}
          </span>
        </div>

        <div className="flex items-center gap-2">
          {/* View mode toggles */}
          <div className="flex bg-slate-800 rounded-lg p-0.5 mr-2">
            <button
              onClick={() => setViewMode("editor")}
              className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                viewMode === "editor" ? "bg-slate-700 text-white" : "text-slate-400 hover:text-white"
              }`}
              title="Editor only"
            >
              <Code className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => setViewMode("split")}
              className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                viewMode === "split" ? "bg-slate-700 text-white" : "text-slate-400 hover:text-white"
              }`}
              title="Split view"
            >
              <PanelLeftOpen className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => setViewMode("preview")}
              className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                viewMode === "preview" ? "bg-slate-700 text-white" : "text-slate-400 hover:text-white"
              }`}
              title="Preview only"
            >
              <Eye className="w-3.5 h-3.5" />
            </button>
          </div>

          {/* AI Toggle */}
          <button
            onClick={() => setShowAI(!showAI)}
            className={`p-2 rounded transition-colors ${
              showAI
                ? "bg-indigo-600 text-white"
                : "bg-slate-800 text-slate-400 hover:text-white"
            }`}
            title="AI Editor"
          >
            <Sparkles className="w-4 h-4" />
          </button>

          {/* Export */}
          <ExportDropdown content={content} />

          {/* Save */}
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-sm font-medium transition-colors flex items-center gap-1.5 disabled:opacity-50"
            title="Save (Ctrl+S)"
          >
            {saving ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : saveStatus === "saved" ? (
              <Check className="w-3.5 h-3.5 text-green-400" />
            ) : (
              <Save className="w-3.5 h-3.5" />
            )}
            {saveStatus === "saved" ? "Saved" : "Save"}
          </button>

          {/* Publish */}
          <button
            onClick={handlePublish}
            disabled={publishing || !content.trim()}
            className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-700 disabled:text-slate-500 text-sm font-medium transition-colors flex items-center gap-1.5"
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
        <div className="px-4 py-2 bg-red-900/50 text-red-300 text-sm border-b border-red-800/50">
          {error}
          <button onClick={() => setError(null)} className="ml-2 underline">
            dismiss
          </button>
        </div>
      )}
      {publishResult && (
        <div className="px-4 py-2 bg-green-900/50 text-green-300 text-sm border-b border-green-800/50">
          Published!{" "}
          <a href={publishResult} target="_blank" rel="noopener noreferrer" className="underline">
            View PR
          </a>
          <button onClick={() => setPublishResult(null)} className="ml-2 underline">
            dismiss
          </button>
        </div>
      )}

      {/* Main content area */}
      <div className="flex-1 flex overflow-hidden">
        {/* Editor pane */}
        {viewMode !== "preview" && (
          <div className={`${viewMode === "split" ? "w-1/2" : "w-full"} h-full border-r border-slate-700/50`}>
            <MonacoEditorWrapper
              value={content}
              onChange={setContent}
            />
          </div>
        )}

        {/* Preview pane */}
        {viewMode !== "editor" && (
          <div className={`${viewMode === "split" ? "w-1/2" : "w-full"} h-full overflow-auto`}>
            <MarkdownPreview content={content} />
          </div>
        )}

        {/* AI Panel (slides in from right) */}
        {showAI && (
          <div className="w-80 shrink-0 border-l border-slate-700/50 bg-slate-800/50">
            <AIEditPanel onClose={() => setShowAI(false)} />
          </div>
        )}
      </div>
    </div>
  );
}
