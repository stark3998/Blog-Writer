import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  listKeywords,
  getTopicKeywords,
  updateTopicKeywords,
  resetTopicKeywords,
} from "../services/api";
import type { TopicKeywordsInfo, TopicKeywordsDetail } from "../services/api";
import {
  Tag,
  ArrowLeft,
  Save,
  RotateCcw,
  Loader2,
  Check,
  AlertTriangle,
  X,
  Plus,
  Sparkles,
} from "lucide-react";

export default function Keywords() {
  const [topicList, setTopicList] = useState<TopicKeywordsInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<TopicKeywordsDetail | null>(null);
  const [editedKeywords, setEditedKeywords] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [newKeyword, setNewKeyword] = useState("");
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");

  const isDirty = detail
    ? JSON.stringify(editedKeywords.slice().sort()) !== JSON.stringify(detail.keywords.slice().sort())
    : false;

  useEffect(() => {
    loadTopics();
  }, []);

  async function loadTopics() {
    setLoading(true);
    try {
      const list = await listKeywords();
      setTopicList(list);
      if (list.length > 0 && !selected) {
        selectTopic(list[0].topic);
      }
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function selectTopic(topic: string) {
    setSelected(topic);
    setFilter("");
    try {
      const d = await getTopicKeywords(topic);
      setDetail(d);
      setEditedKeywords([...d.keywords]);
    } catch (err: any) {
      setError(err.message);
    }
  }

  async function handleSave() {
    if (!selected || editedKeywords.length === 0) return;
    setSaving(true);
    setSaved(false);
    try {
      const d = await updateTopicKeywords(selected, editedKeywords);
      setDetail(d);
      setEditedKeywords([...d.keywords]);
      setTopicList((prev) =>
        prev.map((t) =>
          t.topic === selected
            ? { ...t, keywords: d.keywords, keyword_count: d.keyword_count, is_customized: true }
            : t
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
    if (!confirm("Reset this topic's keywords to defaults? Your customizations will be lost."))
      return;
    setResetting(true);
    try {
      await resetTopicKeywords(selected);
      const d = await getTopicKeywords(selected);
      setDetail(d);
      setEditedKeywords([...d.keywords]);
      setTopicList((prev) =>
        prev.map((t) =>
          t.topic === selected
            ? { ...t, keywords: d.keywords, keyword_count: d.keyword_count, is_customized: false }
            : t
        )
      );
    } catch (err: any) {
      setError(err.message);
    } finally {
      setResetting(false);
    }
  }

  function handleAddKeyword() {
    const kw = newKeyword.trim().toLowerCase();
    if (!kw) return;
    if (editedKeywords.includes(kw)) {
      setNewKeyword("");
      return;
    }
    setEditedKeywords((prev) => [...prev, kw].sort());
    setNewKeyword("");
  }

  function handleRemoveKeyword(kw: string) {
    setEditedKeywords((prev) => prev.filter((k) => k !== kw));
  }

  const filteredKeywords = filter
    ? editedKeywords.filter((kw) => kw.includes(filter.toLowerCase()))
    : editedKeywords;

  const topicLabels: Record<string, string> = {
    "cloud security": "Cloud Security",
    azure: "Azure",
    ai: "AI / ML",
  };

  return (
    <div className="min-h-screen bg-[var(--bg-base)]">
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-[-20%] right-[-10%] w-[600px] h-[600px] bg-amber-200/20 rounded-full blur-[120px]" />
        <div className="absolute bottom-[-10%] left-[10%] w-[500px] h-[500px] bg-orange-200/15 rounded-full blur-[100px]" />
      </div>

      <nav className="relative glass-strong border-b border-amber-100/60 animate-fade-in-down sticky top-0 z-40">
        <div className="max-w-6xl mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link
              to="/"
              className="p-2 rounded-xl text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-all"
            >
              <ArrowLeft size={20} />
            </Link>
            <div className="flex items-center gap-2">
              <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-amber-500 to-orange-500 flex items-center justify-center shadow-lg shadow-amber-500/25">
                <Tag className="w-4.5 h-4.5 text-white" />
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-lg font-bold tracking-tight text-gray-900">
                  Keyword Manager
                </span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {detail?.is_customized && (
              <button
                onClick={handleReset}
                disabled={resetting}
                className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm bg-amber-50 text-amber-600 hover:bg-amber-100 border border-amber-200/60 transition-all disabled:opacity-50"
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
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-amber-500 hover:bg-amber-400 text-white transition-all disabled:opacity-40 disabled:cursor-not-allowed"
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
            <Loader2 size={24} className="animate-spin text-amber-500" />
          </div>
        ) : (
          <div className="flex gap-6">
            {/* Sidebar — topic list */}
            <div className="w-52 flex-shrink-0 space-y-1.5">
              {topicList.map((t) => (
                <button
                  key={t.topic}
                  onClick={() => selectTopic(t.topic)}
                  className={`w-full text-left px-4 py-3 rounded-xl text-sm transition-all flex items-center justify-between ${
                    selected === t.topic
                      ? "bg-amber-50 text-amber-700 border border-amber-200/60 shadow-sm"
                      : "text-gray-500 hover:text-gray-900 hover:bg-gray-50 border border-transparent"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <Tag size={14} />
                    <span className="font-semibold">
                      {topicLabels[t.topic] ?? t.topic}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs font-semibold text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">
                      {t.keyword_count}
                    </span>
                    {t.is_customized && (
                      <span className="w-1.5 h-1.5 rounded-full bg-amber-400" title="Customized" />
                    )}
                  </div>
                </button>
              ))}
            </div>

            {/* Main area */}
            <div className="flex-1">
              {detail && (
                <>
                  <div className="flex items-center gap-3 mb-4">
                    <h3 className="text-lg font-bold text-gray-900">
                      {topicLabels[detail.topic] ?? detail.topic}
                    </h3>
                    {detail.is_customized && (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-600 border border-amber-200/60 font-semibold">
                        Customized
                      </span>
                    )}
                    {isDirty && (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-blue-50 text-blue-500 border border-blue-200/60 font-semibold">
                        Unsaved changes
                      </span>
                    )}
                    <span className="text-xs text-gray-400">
                      {editedKeywords.length} keywords
                    </span>
                  </div>

                  {/* Add keyword input */}
                  <div className="flex gap-2 mb-4">
                    <div className="relative flex-1 max-w-md">
                      <Plus className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-300" />
                      <input
                        type="text"
                        placeholder="Add keyword..."
                        value={newKeyword}
                        onChange={(e) => setNewKeyword(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleAddKeyword();
                        }}
                        className="w-full pl-10 pr-4 py-2.5 rounded-xl bg-white border border-gray-200/80 text-gray-900 placeholder-gray-400 outline-none text-sm focus:border-amber-300 focus:ring-2 focus:ring-amber-500/10"
                      />
                    </div>
                    <button
                      onClick={handleAddKeyword}
                      disabled={!newKeyword.trim()}
                      className="px-4 py-2.5 rounded-xl text-sm font-medium bg-amber-50 border border-amber-200/60 text-amber-600 hover:bg-amber-100 disabled:opacity-40 transition-all"
                    >
                      Add
                    </button>
                  </div>

                  {/* Filter */}
                  {editedKeywords.length > 20 && (
                    <div className="mb-4">
                      <input
                        type="text"
                        placeholder="Filter keywords..."
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                        className="w-full max-w-md px-3 py-2 rounded-xl bg-gray-50 border border-gray-200/80 text-gray-900 placeholder-gray-400 outline-none text-sm focus:border-amber-300"
                      />
                    </div>
                  )}

                  {/* Keyword chips */}
                  <div className="flex flex-wrap gap-2 max-h-[calc(100vh-20rem)] overflow-y-auto p-1">
                    {filteredKeywords.map((kw) => (
                      <span
                        key={kw}
                        className="group inline-flex items-center gap-1 px-3 py-1.5 rounded-full text-xs font-medium bg-white border border-gray-200/80 text-gray-700 hover:border-amber-200 hover:bg-amber-50/50 transition-all"
                      >
                        {kw}
                        <button
                          onClick={() => handleRemoveKeyword(kw)}
                          className="ml-0.5 p-0.5 rounded-full text-gray-300 hover:text-red-500 hover:bg-red-50 transition-all opacity-0 group-hover:opacity-100"
                        >
                          <X size={10} />
                        </button>
                      </span>
                    ))}
                  </div>

                  {filter && filteredKeywords.length === 0 && (
                    <p className="text-sm text-gray-400 mt-4">
                      No keywords match "{filter}"
                    </p>
                  )}
                </>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
