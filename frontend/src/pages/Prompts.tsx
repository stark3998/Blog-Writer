import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import Editor from "@monaco-editor/react";
import {
  listPrompts,
  getPrompt,
  updatePrompt,
  resetPrompt,
  testPrompt,
} from "../services/api";
import type { PromptInfo, PromptDetail, PromptTestResponse } from "../services/api";
import {
  ArrowLeft,
  Save,
  RotateCcw,
  Play,
  Loader2,
  FileText,
  Check,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
} from "lucide-react";

export default function Prompts() {
  const [promptList, setPromptList] = useState<PromptInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<PromptDetail | null>(null);
  const [editorContent, setEditorContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [resetting, setResetting] = useState(false);

  // Test panel
  const [testOpen, setTestOpen] = useState(false);
  const [testInput, setTestInput] = useState("");
  const [testResult, setTestResult] = useState<PromptTestResponse | null>(null);
  const [testing, setTesting] = useState(false);
  const [testError, setTestError] = useState("");

  const [error, setError] = useState("");

  const isDirty = detail ? editorContent !== detail.content : false;

  useEffect(() => {
    loadPrompts();
  }, []);

  async function loadPrompts() {
    setLoading(true);
    try {
      const list = await listPrompts();
      setPromptList(list);
      if (list.length > 0 && !selected) {
        selectPrompt(list[0].name);
      }
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function selectPrompt(name: string) {
    setSelected(name);
    setTestResult(null);
    setTestError("");
    try {
      const d = await getPrompt(name);
      setDetail(d);
      setEditorContent(d.content);
    } catch (err: any) {
      setError(err.message);
    }
  }

  async function handleSave() {
    if (!selected || !editorContent.trim()) return;
    setSaving(true);
    setSaved(false);
    try {
      const d = await updatePrompt(selected, editorContent);
      setDetail(d);
      setEditorContent(d.content);
      setPromptList((prev) =>
        prev.map((p) =>
          p.name === selected ? { ...p, is_customized: true, updated_at: d.updated_at } : p
        )
      );
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleReset() {
    if (!selected || !detail) return;
    if (!confirm("Reset this prompt to its default? Your customizations will be lost.")) return;
    setResetting(true);
    try {
      await resetPrompt(selected);
      const d = await getPrompt(selected);
      setDetail(d);
      setEditorContent(d.content);
      setPromptList((prev) =>
        prev.map((p) =>
          p.name === selected ? { ...p, is_customized: false, updated_at: null } : p
        )
      );
    } catch (err: any) {
      setError(err.message);
    } finally {
      setResetting(false);
    }
  }

  async function handleTest() {
    if (!selected || !testInput.trim()) return;
    setTesting(true);
    setTestResult(null);
    setTestError("");
    try {
      const result = await testPrompt({
        prompt_name: selected,
        test_input: testInput,
        content_override: isDirty ? editorContent : undefined,
      });
      setTestResult(result);
    } catch (err: any) {
      setTestError(err.message);
    } finally {
      setTesting(false);
    }
  }

  const promptLabels: Record<string, string> = {
    system_prompt: "Blog Generation",
    editor_prompt: "AI Editor",
    linkedin_post_prompt: "LinkedIn Post",
    validation_agent_prompt: "Validation Agent",
    post_selector_prompt: "Post Selector",
  };

  const testPlaceholders: Record<string, string> = {
    system_prompt:
      "Paste a sample URL analysis or article content to test blog generation...",
    editor_prompt:
      'Paste blog content followed by an edit instruction, e.g.:\n\n---CONTENT---\n# My Blog Post\n...\n---INSTRUCTION---\nMake the tone more casual',
    linkedin_post_prompt:
      "Paste blog content to test LinkedIn post generation...",
    validation_agent_prompt:
      'Paste content to validate, e.g.:\n\ncontent_type: linkedin_post\nblog_url: https://myblog.com/post\nsource_url: https://source.com/article\n\n--- GENERATED CONTENT ---\nYour post text here...\n\n--- SOURCE MATERIAL ---\nOriginal article text...',
    post_selector_prompt:
      'Paste a JSON array of candidate posts, e.g.:\n[{"index":0,"title":"Post A","post_text":"...","article_url":"https://..."},{"index":1,"title":"Post B","post_text":"...","article_url":"https://..."}]',
  };

  return (
    <div className="min-h-screen bg-[var(--bg-base)]">
      {/* Decorative background */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-[-20%] right-[-10%] w-[600px] h-[600px] bg-purple-200/20 rounded-full blur-[120px]" />
        <div className="absolute bottom-[-10%] left-[10%] w-[500px] h-[500px] bg-violet-200/15 rounded-full blur-[100px]" />
      </div>

      {/* Navigation */}
      <nav className="relative glass-strong border-b border-purple-100/60 animate-fade-in-down sticky top-0 z-40">
        <div className="max-w-6xl mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link
              to="/"
              className="p-2 rounded-xl text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-all"
            >
              <ArrowLeft size={20} />
            </Link>
            <div className="flex items-center gap-2">
              <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-purple-500 to-violet-500 flex items-center justify-center shadow-lg shadow-purple-500/25">
                <FileText className="w-4.5 h-4.5 text-white" />
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-lg font-bold tracking-tight text-gray-900">
                  Prompt Editor
                </span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {detail?.is_customized && (
              <button
                onClick={handleReset}
                disabled={resetting}
                className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm bg-purple-50 text-purple-600 hover:bg-purple-100 border border-purple-200/60 transition-all disabled:opacity-50"
              >
                {resetting ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <RotateCcw size={14} />
                )}
                Reset to Default
              </button>
            )}
            <button
              onClick={handleSave}
              disabled={saving || !isDirty}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-purple-500 hover:bg-purple-400 text-white transition-all disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {saving ? (
                <Loader2 size={14} className="animate-spin" />
              ) : saved ? (
                <Check size={14} />
              ) : (
                <Save size={14} />
              )}
              {saved ? "Saved!" : "Save"}
            </button>
          </div>
        </div>
      </nav>

      <main className="relative max-w-6xl mx-auto px-6 py-8">
        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-50 border border-red-200/60 text-red-600 text-sm flex items-center gap-2">
            <AlertTriangle size={14} />
            {error}
            <button onClick={() => setError("")} className="ml-auto text-red-400 hover:text-red-600">
              &times;
            </button>
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 size={24} className="animate-spin text-purple-500" />
          </div>
        ) : (
          <div className="flex gap-6 h-[calc(100vh-10rem)]">
            {/* Sidebar — prompt list */}
            <div className="w-52 flex-shrink-0 space-y-1.5">
              {promptList.map((p) => (
                <button
                  key={p.name}
                  onClick={() => selectPrompt(p.name)}
                  className={`w-full text-left px-4 py-3 rounded-xl text-sm transition-all flex items-center gap-2 ${
                    selected === p.name
                      ? "bg-purple-50 text-purple-700 border border-purple-200/60 shadow-sm"
                      : "text-gray-500 hover:text-gray-900 hover:bg-gray-50 border border-transparent"
                  }`}
                >
                  <FileText size={14} className="shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="font-semibold truncate">{promptLabels[p.name] ?? p.name}</div>
                  </div>
                  {p.is_customized && (
                    <span className="flex-shrink-0 w-1.5 h-1.5 rounded-full bg-purple-400" title="Customized" />
                  )}
                </button>
              ))}
            </div>

            {/* Main editor area */}
            <div className="flex-1 flex flex-col min-w-0">
              {detail && (
                <>
                  <div className="flex items-center gap-2 mb-3">
                    <span className="text-sm text-gray-500">{detail.description}</span>
                    {detail.is_customized && (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-purple-50 text-purple-600 border border-purple-200/60 font-semibold">
                        Customized
                      </span>
                    )}
                    {isDirty && (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-blue-50 text-blue-500 border border-blue-200/60 font-semibold">
                        Unsaved changes
                      </span>
                    )}
                  </div>
                  <div className="flex-1 rounded-xl overflow-hidden border border-gray-200/60 bg-white shadow-sm">
                    <Editor
                      height="100%"
                      language="markdown"
                      theme="vs-dark"
                      value={editorContent}
                      onChange={(v) => setEditorContent(v ?? "")}
                      options={{
                        minimap: { enabled: false },
                        wordWrap: "on",
                        fontSize: 13,
                        lineNumbers: "on",
                        scrollBeyondLastLine: false,
                        padding: { top: 12 },
                      }}
                    />
                  </div>

                  {/* Test panel */}
                  <div className="mt-3 rounded-xl border border-gray-200/60 bg-white shadow-sm">
                    <button
                      onClick={() => setTestOpen(!testOpen)}
                      className="w-full flex items-center justify-between px-4 py-2.5 text-sm text-gray-500 hover:text-gray-900 transition-colors"
                    >
                      <span className="flex items-center gap-2 font-semibold">
                        <Play size={14} />
                        Test Prompt
                      </span>
                      {testOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    </button>

                    {testOpen && (
                      <div className="px-4 pb-4 space-y-3 border-t border-gray-100">
                        <textarea
                          value={testInput}
                          onChange={(e) => setTestInput(e.target.value)}
                          placeholder={testPlaceholders[selected ?? ""] ?? "Enter test input..."}
                          rows={4}
                          className="w-full mt-3 rounded-xl bg-gray-50 border border-gray-200/80 px-3 py-2.5 text-sm text-gray-900 placeholder-gray-400 focus:border-purple-300 focus:ring-2 focus:ring-purple-500/10 outline-none resize-y"
                        />
                        <div className="flex items-center gap-2">
                          <button
                            onClick={handleTest}
                            disabled={testing || !testInput.trim()}
                            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-emerald-500 hover:bg-emerald-400 text-white transition-all disabled:opacity-40 disabled:cursor-not-allowed"
                          >
                            {testing ? (
                              <Loader2 size={14} className="animate-spin" />
                            ) : (
                              <Play size={14} />
                            )}
                            {testing ? "Running..." : "Run Test"}
                          </button>
                          {isDirty && (
                            <span className="text-xs text-blue-500 font-medium">
                              Testing with unsaved editor content
                            </span>
                          )}
                        </div>

                        {testError && (
                          <div className="p-3 rounded-lg bg-red-50 border border-red-200/60 text-red-600 text-sm">
                            {testError}
                          </div>
                        )}

                        {testResult && (
                          <div className="space-y-2">
                            <div className="flex items-center gap-2 text-xs text-gray-400">
                              <span>Model: {testResult.model}</span>
                            </div>
                            <div className="rounded-xl bg-gray-900 border border-gray-800 p-3 max-h-64 overflow-y-auto">
                              <pre className="text-sm text-gray-200 whitespace-pre-wrap font-mono">
                                {testResult.output}
                              </pre>
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
