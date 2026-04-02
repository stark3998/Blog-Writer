import { useEffect, useState, useCallback } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import MonacoEditorWrapper from "../components/MonacoEditor";
import MarkdownPreview from "../components/MarkdownPreview";
import AIEditPanel from "../components/AIEditPanel";
import ExportDropdown from "../components/ExportDropdown";
import LinkedInButton from "../components/LinkedInButton";
import TwitterButton from "../components/TwitterButton";
import MediumButton from "../components/MediumButton";
import { useBlogStore } from "../store/blogStore";
import {
  getDraft,
  updateDraft,
  createDraft,
  publishBlog,
  getPublishedBlog,
  testDraftReadiness,
} from "../services/api";
import type { TestReadinessResponse } from "../services/api";
import SEOPanel from "../components/SEOPanel";
import VersionHistoryPanel from "../components/VersionHistoryPanel";
import DistributeDropdown from "../components/DistributeDropdown";
import { toast } from "../store/toastStore";
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
  ExternalLink,
  FlaskConical,
  X,
  Copy,
  Search,
  History,
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
  const [showTest, setShowTest] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestReadinessResponse | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [copiedPost, setCopiedPost] = useState(false);
  const [showSEO, setShowSEO] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  useEffect(() => {
    if (!id) return;
    getDraft(id)
      .then(async (d) => {
        setContent(d.content);

        // If the draft already has publish info, use it directly
        if (d.publishedSlug) {
          setDraft(d);
          return;
        }

        // Otherwise, check if a published blog exists with the same slug (pre-migration drafts)
        try {
          const pub = await getPublishedBlog(d.slug);
          if (pub) {
            const blogUrl = `${window.location.origin}/blog/${pub.slug}`;
            d.publishedSlug = pub.slug;
            d.publishedAt = pub.published_at;
            d.publishedUrl = blogUrl;

            // Backfill publish info on the draft so future loads are instant
            updateDraft(d.id, {
              publishedSlug: pub.slug,
              publishedAt: pub.published_at,
              publishedUrl: blogUrl,
            }).catch(() => {}); // best-effort backfill
          }
        } catch {
          // No published blog found — that's fine, it's a new draft
        }

        setDraft(d);
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
      toast.success("Draft saved");
      setTimeout(() => setSaveStatus("idle"), 2000);
    } catch {
      setSaveStatus("error");
      toast.error("Save failed");
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

  const isPublished = !!(draft?.publishedSlug || publishResult);
  const publishedUrl = publishResult || draft?.publishedUrl || "";

  const handlePublish = async () => {
    setPublishing(true);
    setPublishResult(null);
    try {
      const titleMatch = content.match(/title:\s*["']?(.+?)["']?\s*$/m);
      const slugMatch = content.match(/slug:\s*["']?(.+?)["']?\s*$/m);
      const excerptMatch = content.match(/excerpt:\s*["']?(.+?)["']?\s*$/m);

      const result = await publishBlog({
        content,
        slug: draft?.publishedSlug || slugMatch?.[1] || `blog-${Date.now()}`,
        title: titleMatch?.[1] ?? "Untitled",
        excerpt: excerptMatch?.[1] ?? "",
        source_url: draft?.sourceUrl ?? "",
        source_type: draft?.sourceType ?? "",
        draft_id: draft?.id,
      });
      setPublishResult(result.blog_url);

      // Update draft state with publish info so the editor stays aware
      if (draft) {
        setDraft({
          ...draft,
          publishedSlug: result.slug,
          publishedAt: new Date().toISOString(),
          publishedUrl: result.blog_url,
        });
      }

      toast.success(isPublished ? "Blog updated!" : "Blog published!", result.blog_url);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Publish failed";
      setError(msg);
      toast.error("Publish failed", msg);
    } finally {
      setPublishing(false);
    }
  };

  const handleTestReadiness = async () => {
    if (!draft?.id) return;
    setTesting(true);
    setTestResult(null);
    setTestError(null);
    try {
      const result = await testDraftReadiness(draft.id);
      setTestResult(result);
    } catch (err) {
      setTestError(err instanceof Error ? err.message : "Test failed");
    } finally {
      setTesting(false);
    }
  };

  const handleCopyPost = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedPost(true);
    setTimeout(() => setCopiedPost(false), 2000);
  };

  return (
    <div className="h-[calc(100vh-3.25rem)] flex flex-col">
      {/* Toolbar */}
      <header className="flex items-center justify-between px-4 py-2.5 border-b border-gray-200/80 glass-strong shrink-0 animate-fade-in-down overflow-visible z-50 relative">
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <Link
            to="/"
            className="p-2 rounded-xl text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 transition-all duration-200 shrink-0"
            title="Back to Home"
          >
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div className="w-px h-5 bg-gray-200 shrink-0 self-stretch my-1" />
          <div className="min-w-0 flex flex-col">
            <span className="text-sm text-gray-600 truncate font-semibold">
              {draft?.title ?? "Untitled Draft"}
            </span>
            {(draft?.sourceUrl || publishedUrl) && (
              <div className="flex items-center gap-3 text-[11px]">
                {draft?.sourceUrl && (
                  <a
                    href={draft.sourceUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-gray-400 hover:text-indigo-600 transition-colors truncate max-w-[300px] flex items-center gap-1"
                    title={draft.sourceUrl}
                  >
                    <ExternalLink className="w-3 h-3 shrink-0" />
                    {draft.sourceUrl.replace(/^https?:\/\//, "")}
                  </a>
                )}
                {draft?.sourceUrl && publishedUrl && (
                  <span className="text-gray-300">·</span>
                )}
                {publishedUrl && (
                  <a
                    href={publishedUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-emerald-500 hover:text-emerald-600 transition-colors truncate max-w-[300px] flex items-center gap-1"
                    title={publishedUrl}
                  >
                    <ExternalLink className="w-3 h-3 shrink-0" />
                    Published
                  </a>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          {/* View mode toggles */}
          <div className="flex bg-gray-100 rounded-xl p-0.5 mr-1 border border-gray-200/60">
            {([
              { mode: "editor" as ViewMode, icon: Code, label: "Editor only" },
              { mode: "split" as ViewMode, icon: PanelLeftOpen, label: "Split view" },
              { mode: "preview" as ViewMode, icon: Eye, label: "Preview only" },
            ]).map(({ mode, icon: Icon, label }) => (
              <button
                key={mode}
                onClick={() => setViewMode(mode)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-200 ${
                  viewMode === mode
                    ? "bg-white text-gray-900 shadow-sm"
                    : "text-gray-400 hover:text-gray-600"
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
            className={`p-2 rounded-xl transition-all duration-200 ${
              showAI
                ? "bg-indigo-50 text-indigo-600 border border-indigo-200/60"
                : "text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 border border-transparent"
            }`}
            title="AI Editor"
          >
            <Sparkles className="w-4 h-4" />
          </button>

          {/* Export */}
          <ExportDropdown content={content} />

          {/* Distribute (LinkedIn, Twitter, Medium) */}
          <DistributeDropdown>
            <LinkedInButton
              content={content}
              title={draft?.title}
              excerpt={draft?.excerpt}
              blogUrl={publishedUrl || undefined}
            />
            <TwitterButton
              content={content}
              title={draft?.title}
              excerpt={draft?.excerpt}
              blogUrl={publishedUrl || undefined}
            />
            <MediumButton
              content={content}
              title={draft?.title}
              excerpt={draft?.excerpt}
              blogUrl={publishedUrl || undefined}
            />
          </DistributeDropdown>

          {/* SEO Score */}
          <button
            onClick={() => { setShowSEO(!showSEO); if (showSEO) return; setShowAI(false); setShowTest(false); setShowHistory(false); }}
            className={`p-2 rounded-xl transition-all duration-200 ${
              showSEO
                ? "bg-emerald-50 text-emerald-600 border border-emerald-200/60"
                : "text-gray-400 hover:text-emerald-600 hover:bg-emerald-50 border border-transparent"
            }`}
            title="SEO Score"
          >
            <Search className="w-4 h-4" />
          </button>

          {/* Version History */}
          <button
            onClick={() => { setShowHistory(!showHistory); if (showHistory) return; setShowAI(false); setShowTest(false); setShowSEO(false); }}
            className={`p-2 rounded-xl transition-all duration-200 ${
              showHistory
                ? "bg-violet-50 text-violet-600 border border-violet-200/60"
                : "text-gray-400 hover:text-violet-600 hover:bg-violet-50 border border-transparent"
            }`}
            title="Version History"
          >
            <History className="w-4 h-4" />
          </button>

          {/* Test Readiness */}
          <button
            onClick={() => {
              setShowTest(!showTest);
              if (!showTest && draft?.id && !testResult) handleTestReadiness();
            }}
            className={`p-2 rounded-xl transition-all duration-200 ${
              showTest
                ? "bg-emerald-50 text-emerald-600 border border-emerald-200/60"
                : "text-gray-400 hover:text-emerald-600 hover:bg-emerald-50 border border-transparent"
            }`}
            title="Test Readiness"
          >
            <FlaskConical className="w-4 h-4" />
          </button>

          <div className="w-px h-5 bg-gray-200 mx-1" />

          {/* Save */}
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-3.5 py-1.5 rounded-xl text-sm font-medium transition-all duration-200 flex items-center gap-1.5 disabled:opacity-50 border border-gray-200/60 text-gray-500 hover:text-gray-900 hover:bg-gray-50 hover:border-gray-300"
            title="Save (Ctrl+S)"
          >
            {saving ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : saveStatus === "saved" ? (
              <Check className="w-3.5 h-3.5 text-emerald-500" />
            ) : (
              <Save className="w-3.5 h-3.5" />
            )}
            {saveStatus === "saved" ? "Saved" : "Save"}
          </button>

          {/* Publish / Update */}
          <button
            onClick={handlePublish}
            disabled={publishing || !content.trim()}
            className={`px-3.5 py-1.5 rounded-xl bg-gradient-to-r ${
              isPublished
                ? "from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 shadow-emerald-500/20 hover:shadow-emerald-500/30"
                : "from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 shadow-indigo-500/20 hover:shadow-indigo-500/30"
            } disabled:from-gray-200 disabled:to-gray-200 disabled:text-gray-400 text-white text-sm font-medium transition-all duration-300 flex items-center gap-1.5 shadow-sm disabled:shadow-none`}
          >
            {publishing ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Upload className="w-3.5 h-3.5" />
            )}
            {isPublished ? "Update" : "Publish"}
          </button>
        </div>
      </header>

      {/* Error / PR banner */}
      {error && (
        <div className="px-4 py-2.5 bg-red-50 text-red-600 text-sm border-b border-red-200/60 flex items-center animate-fade-in-down">
          <span className="flex-1">{error}</span>
          <button onClick={() => setError(null)} className="text-red-500 hover:text-red-700 text-xs font-semibold ml-4 underline underline-offset-2 transition-colors">
            dismiss
          </button>
        </div>
      )}
      {publishedUrl && (
        <div className="px-4 py-2.5 bg-emerald-50 text-emerald-700 text-sm border-b border-emerald-200/60 flex items-center animate-fade-in-down">
          <span className="flex-1 flex items-center gap-2">
            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-100 text-emerald-700 border border-emerald-200/60">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
              PUBLISHED
            </span>
            <a href={publishedUrl} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 font-semibold hover:text-emerald-800 transition-colors">
              View Blog
            </a>
            {draft?.publishedAt && (
              <span className="text-xs text-emerald-500">
                · Last published {new Date(draft.publishedAt).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
              </span>
            )}
          </span>
          {publishResult && (
            <button onClick={() => setPublishResult(null)} className="text-emerald-500 hover:text-emerald-700 text-xs font-semibold ml-4 underline underline-offset-2 transition-colors">
              dismiss
            </button>
          )}
        </div>
      )}

      {/* Main content area */}
      <div className="flex-1 flex overflow-hidden">
        {/* Editor pane */}
        {viewMode !== "preview" && (
          <div
            className={`${viewMode === "split" ? "w-1/2" : "w-full"} h-full border-r border-gray-200/60 transition-all duration-300 animate-fade-in`}
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
          className={`shrink-0 border-l border-gray-200/60 glass-strong overflow-hidden transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
            showAI ? "w-80 opacity-100" : "w-0 opacity-0"
          }`}
        >
          {showAI && (
            <div className="w-80 h-full animate-slide-in-right">
              <AIEditPanel onClose={() => setShowAI(false)} />
            </div>
          )}
        </div>

        {/* SEO Panel */}
        <div
          className={`shrink-0 border-l border-gray-200/60 glass-strong overflow-hidden transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
            showSEO ? "w-72 opacity-100" : "w-0 opacity-0"
          }`}
        >
          {showSEO && (
            <div className="animate-slide-in-right">
              <SEOPanel content={content} onClose={() => setShowSEO(false)} />
            </div>
          )}
        </div>

        {/* Version History Panel */}
        <div
          className={`shrink-0 border-l border-gray-200/60 glass-strong overflow-hidden transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
            showHistory ? "w-80 opacity-100" : "w-0 opacity-0"
          }`}
        >
          {showHistory && draft?.id && (
            <VersionHistoryPanel
              draftId={draft.id}
              onRestore={(restoredContent) => { setContent(restoredContent); setShowHistory(false); }}
              onClose={() => setShowHistory(false)}
            />
          )}
          {showHistory && !draft?.id && (
            <div className="w-80 h-full flex items-center justify-center p-4">
              <p className="text-sm text-gray-400 text-center">Save the draft first to see version history.</p>
            </div>
          )}
        </div>

        {/* Test Readiness Panel */}
        <div
          className={`shrink-0 border-l border-gray-200/60 glass-strong overflow-hidden transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
            showTest ? "w-96 opacity-100" : "w-0 opacity-0"
          }`}
        >
          {showTest && (
            <div className="w-96 h-full flex flex-col animate-slide-in-right">
              {/* Panel header */}
              <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200/60">
                <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
                  <FlaskConical className="w-4 h-4 text-emerald-500" />
                  Test Readiness
                </h3>
                <div className="flex items-center gap-1">
                  <button
                    onClick={handleTestReadiness}
                    disabled={testing || !draft?.id}
                    className="px-2.5 py-1 rounded-lg text-xs font-medium text-emerald-600 hover:bg-emerald-50 border border-emerald-200/60 transition-all disabled:opacity-40 flex items-center gap-1"
                  >
                    {testing ? <Loader2 className="w-3 h-3 animate-spin" /> : <FlaskConical className="w-3 h-3" />}
                    Re-test
                  </button>
                  <button
                    onClick={() => setShowTest(false)}
                    className="p-1 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-all"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              </div>

              {/* Panel body */}
              <div className="flex-1 overflow-y-auto p-4 space-y-5">
                {testing && !testResult && (
                  <div className="flex flex-col items-center justify-center py-12 gap-3">
                    <Loader2 className="w-6 h-6 animate-spin text-emerald-500" />
                    <p className="text-sm text-gray-400">Analyzing relevance & composing LinkedIn preview...</p>
                  </div>
                )}

                {testError && (
                  <div className="px-3 py-2 rounded-xl bg-red-50 border border-red-200/60 text-red-600 text-xs">
                    {testError}
                  </div>
                )}

                {testResult && (
                  <>
                    {/* Relevance Score */}
                    <div>
                      <h4 className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-3">Technical Relevance</h4>
                      <div className="p-4 rounded-xl bg-white border border-gray-200/60">
                        {/* Score bar */}
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-sm font-bold text-gray-900">
                            Score: {(testResult.relevance.relevance_score * 100).toFixed(0)}%
                          </span>
                          <span
                            className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${
                              testResult.relevance.is_relevant
                                ? "bg-emerald-50 text-emerald-600 border border-emerald-200/60"
                                : "bg-red-50 text-red-500 border border-red-200/60"
                            }`}
                          >
                            {testResult.relevance.is_relevant ? "RELEVANT" : "NOT RELEVANT"}
                          </span>
                        </div>
                        <div className="w-full h-2 bg-gray-100 rounded-full overflow-hidden mb-3">
                          <div
                            className={`h-full rounded-full transition-all duration-500 ${
                              testResult.relevance.relevance_score >= 0.7
                                ? "bg-emerald-500"
                                : testResult.relevance.relevance_score >= 0.4
                                ? "bg-amber-500"
                                : "bg-red-400"
                            }`}
                            style={{ width: `${Math.max(testResult.relevance.relevance_score * 100, 2)}%` }}
                          />
                        </div>

                        {/* Method */}
                        <div className="flex items-center gap-2 mb-2">
                          <span className="text-[10px] font-semibold text-gray-400 uppercase">Method</span>
                          <span className="text-xs text-gray-600">{testResult.relevance.method}</span>
                        </div>

                        {/* Matched topics */}
                        {testResult.relevance.matched_topics.length > 0 && (
                          <div className="mb-2">
                            <span className="text-[10px] font-semibold text-gray-400 uppercase">Matched Topics</span>
                            <div className="flex flex-wrap gap-1 mt-1">
                              {testResult.relevance.matched_topics.map((t) => (
                                <span key={t} className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-indigo-50 text-indigo-600 border border-indigo-200/60">
                                  {t}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Matched keywords */}
                        {testResult.relevance.matched_keywords.length > 0 && (
                          <div className="mb-2">
                            <span className="text-[10px] font-semibold text-gray-400 uppercase">Matched Keywords</span>
                            <div className="flex flex-wrap gap-1 mt-1">
                              {testResult.relevance.matched_keywords.map((kw) => (
                                <span key={kw} className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-amber-50 text-amber-600 border border-amber-200/60">
                                  {kw}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Reasoning */}
                        {testResult.relevance.reasoning && (
                          <div>
                            <span className="text-[10px] font-semibold text-gray-400 uppercase">AI Reasoning</span>
                            <p className="text-xs text-gray-600 mt-1 leading-relaxed">{testResult.relevance.reasoning}</p>
                          </div>
                        )}
                      </div>
                    </div>

                    {/* LinkedIn Preview */}
                    {testResult.linkedin_preview && (
                      <div>
                        <h4 className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-3">LinkedIn Post Preview</h4>
                        <div className="p-4 rounded-xl bg-white border border-gray-200/60">
                          <div className="flex items-center justify-between mb-2">
                            <span className="text-xs text-gray-400">
                              {testResult.linkedin_preview.word_count} words
                            </span>
                            <button
                              onClick={() => handleCopyPost(testResult.linkedin_preview!.post_text)}
                              className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 border border-gray-200/60 hover:border-indigo-200/60 transition-all"
                            >
                              {copiedPost ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
                              {copiedPost ? "Copied" : "Copy"}
                            </button>
                          </div>
                          <pre className="text-xs text-gray-700 whitespace-pre-wrap leading-relaxed font-sans max-h-[300px] overflow-y-auto">
                            {testResult.linkedin_preview.post_text}
                          </pre>
                          {testResult.linkedin_preview.hashtags.length > 0 && (
                            <div className="flex flex-wrap gap-1 mt-3 pt-3 border-t border-gray-100">
                              {testResult.linkedin_preview.hashtags.map((tag) => (
                                <span key={tag} className="text-[10px] font-semibold text-blue-500">
                                  {tag}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </>
                )}

                {!testing && !testResult && !testError && !draft?.id && (
                  <p className="text-sm text-gray-400 text-center py-8">
                    Save the draft first to test readiness.
                  </p>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
