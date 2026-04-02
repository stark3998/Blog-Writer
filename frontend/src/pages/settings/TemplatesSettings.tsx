import { useEffect, useState } from "react";
import { listTemplates, createTemplate, deleteTemplate } from "../../services/api";
import type { ContentTemplate } from "../../services/api";
import { toast } from "../../store/toastStore";
import { Loader2, Plus, Trash2, FileCode, Copy } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useBlogStore } from "../../store/blogStore";

const CATEGORIES = ["tutorial", "how-to", "review", "comparison", "news", "opinion", "case-study", "other"];

export default function TemplatesSettings() {
  const navigate = useNavigate();
  const { setContent, setDraft } = useBlogStore();
  const [templates, setTemplates] = useState<ContentTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [categoryFilter, setCategoryFilter] = useState("");
  const [form, setForm] = useState({ name: "", description: "", category: "tutorial", content: "", tags: "" });

  useEffect(() => {
    listTemplates(categoryFilter || undefined).then(setTemplates).catch(() => {}).finally(() => setLoading(false));
  }, [categoryFilter]);

  const handleCreate = async () => {
    if (!form.name.trim() || !form.content.trim()) return;
    setSaving(true);
    try {
      const template = await createTemplate({
        ...form,
        tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
      });
      setTemplates([template, ...templates]);
      setShowForm(false);
      setForm({ name: "", description: "", category: "tutorial", content: "", tags: "" });
      toast.success("Template created");
    } catch { toast.error("Failed to create template"); }
    setSaving(false);
  };

  const handleDelete = async (id: string, isBuiltIn: boolean) => {
    if (isBuiltIn) { toast.warning("Built-in templates cannot be deleted"); return; }
    try {
      await deleteTemplate(id);
      setTemplates(templates.filter((t) => t.id !== id));
      toast.success("Template deleted");
    } catch { toast.error("Failed to delete"); }
  };

  const handleUse = (template: ContentTemplate) => {
    setContent(template.content);
    setDraft(null);
    navigate("/editor");
    toast.success(`Template "${template.name}" loaded`);
  };

  if (loading) return <div className="flex justify-center py-12"><Loader2 className="w-6 h-6 animate-spin text-indigo-500" /></div>;

  return (
    <div className="space-y-6">
      <div className="p-5 rounded-2xl bg-white border border-gray-200/60">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
              <FileCode className="w-4 h-4 text-indigo-500" />
              Content Templates
            </h3>
            <p className="text-xs text-gray-400 mt-1">Reusable blog post templates for different content types.</p>
          </div>
          <button
            onClick={() => setShowForm(!showForm)}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold text-indigo-600 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200/60 transition-all"
          >
            <Plus className="w-3 h-3" />
            New Template
          </button>
        </div>

        {/* Category filter */}
        <div className="flex items-center gap-2 mb-4">
          <button
            onClick={() => setCategoryFilter("")}
            className={`px-2.5 py-1 rounded-lg text-[10px] font-semibold border transition-all ${!categoryFilter ? "bg-indigo-50 border-indigo-200 text-indigo-600" : "bg-white border-gray-200/60 text-gray-400"}`}
          >
            All
          </button>
          {CATEGORIES.map((c) => (
            <button
              key={c}
              onClick={() => setCategoryFilter(c)}
              className={`px-2.5 py-1 rounded-lg text-[10px] font-semibold border transition-all capitalize ${categoryFilter === c ? "bg-indigo-50 border-indigo-200 text-indigo-600" : "bg-white border-gray-200/60 text-gray-400"}`}
            >
              {c}
            </button>
          ))}
        </div>

        {showForm && (
          <div className="mb-4 p-4 rounded-xl bg-gray-50 border border-gray-200/60 space-y-3">
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Template name"
              className="w-full px-3 py-2 rounded-lg bg-white border border-gray-200/60 text-sm outline-none focus:border-indigo-300"
            />
            <input
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="Short description"
              className="w-full px-3 py-2 rounded-lg bg-white border border-gray-200/60 text-sm outline-none focus:border-indigo-300"
            />
            <select
              value={form.category}
              onChange={(e) => setForm({ ...form, category: e.target.value })}
              className="w-full px-3 py-2 rounded-lg bg-white border border-gray-200/60 text-sm outline-none"
            >
              {CATEGORIES.map((c) => <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>)}
            </select>
            <textarea
              value={form.content}
              onChange={(e) => setForm({ ...form, content: e.target.value })}
              placeholder="Template content (MDX/Markdown)..."
              rows={8}
              className="w-full px-3 py-2 rounded-lg bg-white border border-gray-200/60 text-sm outline-none focus:border-indigo-300 resize-none font-mono"
            />
            <input
              value={form.tags}
              onChange={(e) => setForm({ ...form, tags: e.target.value })}
              placeholder="Tags (comma-separated)"
              className="w-full px-3 py-2 rounded-lg bg-white border border-gray-200/60 text-sm outline-none focus:border-indigo-300"
            />
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowForm(false)} className="px-3 py-1.5 rounded-lg text-xs font-medium text-gray-500">Cancel</button>
              <button
                onClick={handleCreate}
                disabled={saving || !form.name.trim() || !form.content.trim()}
                className="px-4 py-1.5 rounded-lg text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 transition-all"
              >
                {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : "Create"}
              </button>
            </div>
          </div>
        )}

        <div className="space-y-2">
          {templates.length === 0 && (
            <p className="text-sm text-gray-400 text-center py-6">No templates yet.</p>
          )}
          {templates.map((t) => (
            <div key={t.id} className="p-4 rounded-xl border border-gray-200/60 hover:border-gray-300 transition-all">
              <div className="flex items-center justify-between">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-gray-900">{t.name}</span>
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-gray-100 text-gray-500 capitalize">{t.category}</span>
                    {t.isBuiltIn && (
                      <span className="px-1.5 py-0.5 rounded-full text-[9px] font-bold bg-emerald-50 text-emerald-600 border border-emerald-200/60">BUILT-IN</span>
                    )}
                  </div>
                  <p className="text-xs text-gray-400 mt-1 truncate">{t.description}</p>
                </div>
                <div className="flex items-center gap-1 ml-3">
                  <button onClick={() => handleUse(t)} className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[10px] font-semibold text-indigo-600 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200/60 transition-all">
                    <Copy className="w-3 h-3" />
                    Use
                  </button>
                  {!t.isBuiltIn && (
                    <button onClick={() => handleDelete(t.id, t.isBuiltIn)} className="p-1.5 rounded-lg text-gray-300 hover:text-red-500 hover:bg-red-50 transition-all">
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
