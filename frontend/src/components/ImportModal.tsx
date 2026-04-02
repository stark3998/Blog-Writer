import { useState } from "react";
import { importMarkdown, importFromUrls, importWordpress } from "../services/api";
import type { ImportResult } from "../services/api";
import { toast } from "../store/toastStore";
import { Upload, Link2, FileText, Loader2, X, Check, AlertTriangle } from "lucide-react";

type ImportTab = "markdown" | "urls" | "wordpress";

interface Props {
  onClose: () => void;
  onImported?: () => void;
}

export default function ImportModal({ onClose, onImported }: Props) {
  const [tab, setTab] = useState<ImportTab>("markdown");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);

  // Markdown state
  const [mdEntries, setMdEntries] = useState([{ title: "", content: "" }]);

  // URLs state
  const [urls, setUrls] = useState("");

  // WordPress state
  const [wpXml, setWpXml] = useState("");

  const handleImport = async () => {
    setLoading(true);
    setResult(null);
    try {
      let res: ImportResult;
      if (tab === "markdown") {
        const entries = mdEntries.filter((e) => e.title.trim() && e.content.trim());
        if (entries.length === 0) { toast.warning("Add at least one entry"); setLoading(false); return; }
        res = await importMarkdown(entries);
      } else if (tab === "urls") {
        const urlList = urls.split("\n").map((u) => u.trim()).filter(Boolean);
        if (urlList.length === 0) { toast.warning("Enter at least one URL"); setLoading(false); return; }
        res = await importFromUrls(urlList);
      } else {
        if (!wpXml.trim()) { toast.warning("Paste WordPress XML"); setLoading(false); return; }
        res = await importWordpress(wpXml);
      }
      setResult(res);
      if (res.succeeded > 0) {
        toast.success(`Imported ${res.succeeded} drafts`);
        onImported?.();
      }
    } catch (err) {
      toast.error("Import failed", err instanceof Error ? err.message : undefined);
    }
    setLoading(false);
  };

  const addMdEntry = () => setMdEntries([...mdEntries, { title: "", content: "" }]);
  const updateMdEntry = (idx: number, field: "title" | "content", val: string) => {
    setMdEntries(mdEntries.map((e, i) => (i === idx ? { ...e, [field]: val } : e)));
  };
  const removeMdEntry = (idx: number) => {
    if (mdEntries.length <= 1) return;
    setMdEntries(mdEntries.filter((_, i) => i !== idx));
  };

  const tabs: { id: ImportTab; label: string; icon: typeof FileText }[] = [
    { id: "markdown", label: "Markdown", icon: FileText },
    { id: "urls", label: "URLs", icon: Link2 },
    { id: "wordpress", label: "WordPress", icon: Upload },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-2xl max-h-[85vh] rounded-2xl bg-white border border-gray-200/60 shadow-2xl flex flex-col animate-scale-in">
        {/* Header */}
        <div className="flex items-center justify-between px-6 pt-5 pb-3">
          <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
            <Upload className="w-4 h-4 text-indigo-500" />
            Bulk Import
          </h3>
          <button onClick={onClose} className="p-1 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-all">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-1 px-6 mb-4">
          {tabs.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => { setTab(id); setResult(null); }}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border transition-all ${
                tab === id
                  ? "bg-indigo-50 border-indigo-200 text-indigo-600"
                  : "bg-white border-gray-200/60 text-gray-400 hover:border-gray-300"
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 pb-4 space-y-3">
          {tab === "markdown" && (
            <>
              <p className="text-xs text-gray-400">Paste markdown content to create drafts.</p>
              {mdEntries.map((entry, idx) => (
                <div key={idx} className="p-3 rounded-xl bg-gray-50 border border-gray-200/60 space-y-2">
                  <div className="flex items-center gap-2">
                    <input
                      value={entry.title}
                      onChange={(e) => updateMdEntry(idx, "title", e.target.value)}
                      placeholder="Post title"
                      className="flex-1 px-3 py-1.5 rounded-lg bg-white border border-gray-200/60 text-sm outline-none focus:border-indigo-300"
                    />
                    {mdEntries.length > 1 && (
                      <button onClick={() => removeMdEntry(idx)} className="p-1 text-gray-400 hover:text-red-500">
                        <X className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                  <textarea
                    value={entry.content}
                    onChange={(e) => updateMdEntry(idx, "content", e.target.value)}
                    placeholder="Markdown content..."
                    rows={4}
                    className="w-full px-3 py-2 rounded-lg bg-white border border-gray-200/60 text-xs outline-none focus:border-indigo-300 resize-none font-mono"
                  />
                </div>
              ))}
              <button
                onClick={addMdEntry}
                className="text-xs font-semibold text-indigo-500 hover:text-indigo-600"
              >
                + Add another entry
              </button>
            </>
          )}

          {tab === "urls" && (
            <>
              <p className="text-xs text-gray-400">Enter article URLs (one per line) to scrape and import as drafts.</p>
              <textarea
                value={urls}
                onChange={(e) => setUrls(e.target.value)}
                placeholder={"https://example.com/article-1\nhttps://example.com/article-2"}
                rows={8}
                className="w-full px-3 py-2 rounded-lg bg-gray-50 border border-gray-200/60 text-sm outline-none focus:border-indigo-300 resize-none font-mono"
              />
            </>
          )}

          {tab === "wordpress" && (
            <>
              <p className="text-xs text-gray-400">
                Export your WordPress site (Tools → Export → All content) and paste the XML below.
              </p>
              <textarea
                value={wpXml}
                onChange={(e) => setWpXml(e.target.value)}
                placeholder="Paste WordPress XML export..."
                rows={10}
                className="w-full px-3 py-2 rounded-lg bg-gray-50 border border-gray-200/60 text-xs outline-none focus:border-indigo-300 resize-none font-mono"
              />
            </>
          )}

          {/* Result */}
          {result && (
            <div className={`p-4 rounded-xl border ${result.failed > 0 ? "bg-amber-50 border-amber-200/60" : "bg-emerald-50 border-emerald-200/60"}`}>
              <div className="flex items-center gap-2 mb-2">
                {result.failed > 0 ? (
                  <AlertTriangle className="w-4 h-4 text-amber-500" />
                ) : (
                  <Check className="w-4 h-4 text-emerald-500" />
                )}
                <span className="text-sm font-semibold text-gray-900">
                  {result.succeeded} of {result.total} imported{result.failed > 0 ? `, ${result.failed} failed` : ""}
                </span>
              </div>
              {result.errors.length > 0 && (
                <ul className="text-[10px] text-red-500 space-y-0.5 mt-1">
                  {result.errors.map((e, i) => (
                    <li key={i}>#{e.index}: {e.error}</li>
                  ))}
                </ul>
              )}
              {result.draft_ids.length > 0 && (
                <p className="text-[10px] text-gray-500 mt-2">
                  Created {result.draft_ids.length} new draft(s)
                </p>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 px-6 py-4 border-t border-gray-100">
          <button onClick={onClose} className="px-4 py-2 rounded-lg text-xs font-medium text-gray-500 hover:text-gray-700">
            Close
          </button>
          <button
            onClick={handleImport}
            disabled={loading}
            className="px-5 py-2 rounded-lg text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 transition-all flex items-center gap-1.5"
          >
            {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
            Import
          </button>
        </div>
      </div>
    </div>
  );
}
