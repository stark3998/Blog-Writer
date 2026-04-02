import { useEffect, useState, useCallback } from "react";
import { getDraftRaw, updateDraftRaw } from "../services/api";
import { toast } from "../store/toastStore";
import { Database, Loader2, Save, RotateCcw, X, AlertTriangle } from "lucide-react";

interface CosmosDataPanelProps {
  draftId: string;
  onClose: () => void;
}

const SYSTEM_KEYS = new Set(["_rid", "_self", "_etag", "_attachments", "_ts"]);

export default function CosmosDataPanel({ draftId, onClose }: CosmosDataPanelProps) {
  const [rawData, setRawData] = useState<Record<string, unknown> | null>(null);
  const [editText, setEditText] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [parseError, setParseError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  const fetchRaw = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getDraftRaw(draftId);
      setRawData(data);
      setEditText(JSON.stringify(data, null, 2));
      setParseError(null);
      setDirty(false);
    } catch (err) {
      toast.error("Failed to load raw data");
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [draftId]);

  useEffect(() => {
    fetchRaw();
  }, [fetchRaw]);

  const handleTextChange = (value: string) => {
    setEditText(value);
    setDirty(true);
    try {
      JSON.parse(value);
      setParseError(null);
    } catch (e) {
      setParseError((e as Error).message);
    }
  };

  const handleSave = async () => {
    if (parseError) return;
    setSaving(true);
    try {
      const parsed = JSON.parse(editText);
      // Strip system keys — backend does this too, but be safe
      const cleaned: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(parsed)) {
        if (!SYSTEM_KEYS.has(k)) cleaned[k] = v;
      }
      const result = await updateDraftRaw(draftId, cleaned);
      setRawData(result);
      setEditText(JSON.stringify(result, null, 2));
      setDirty(false);
      toast.success("Cosmos DB document updated");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    if (rawData) {
      setEditText(JSON.stringify(rawData, null, 2));
      setParseError(null);
      setDirty(false);
    }
  };

  return (
    <div className="w-[480px] h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200/60 shrink-0">
        <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
          <Database className="w-4 h-4 text-cyan-500" />
          Cosmos DB Document
        </h3>
        <div className="flex items-center gap-1">
          <button
            onClick={fetchRaw}
            disabled={loading}
            className="px-2.5 py-1 rounded-lg text-xs font-medium text-cyan-600 hover:bg-cyan-50 border border-cyan-200/60 transition-all disabled:opacity-40 flex items-center gap-1"
            title="Refresh from DB"
          >
            {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : <RotateCcw className="w-3 h-3" />}
            Refresh
          </button>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-all"
            title="Close panel"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Warning banner */}
      <div className="px-4 py-2 bg-amber-50 border-b border-amber-200/60 flex items-start gap-2 shrink-0">
        <AlertTriangle className="w-3.5 h-3.5 text-amber-500 shrink-0 mt-0.5" />
        <p className="text-[11px] text-amber-700 leading-relaxed">
          Editing raw data directly. Invalid changes may break the draft. The editor view won't auto-refresh — reload the page after saving.
        </p>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-hidden flex flex-col">
        {loading && !rawData ? (
          <div className="flex-1 flex items-center justify-center">
            <Loader2 className="w-6 h-6 animate-spin text-cyan-500" />
          </div>
        ) : (
          <>
            {/* Parse error indicator */}
            {parseError && (
              <div className="px-4 py-2 bg-red-50 border-b border-red-200/60 shrink-0">
                <p className="text-[11px] text-red-600 font-mono truncate" title={parseError}>
                  JSON Error: {parseError}
                </p>
              </div>
            )}

            {/* JSON editor */}
            <textarea
              value={editText}
              onChange={(e) => handleTextChange(e.target.value)}
              spellCheck={false}
              aria-label="Raw Cosmos DB JSON document"
              placeholder="Loading document..."
              className="flex-1 w-full p-4 font-mono text-xs leading-relaxed text-gray-700 bg-gray-50 resize-none focus:outline-none focus:bg-white transition-colors"
              style={{ tabSize: 2 }}
            />
          </>
        )}
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between px-4 py-3 border-t border-gray-200/60 shrink-0 bg-white">
        <div className="flex items-center gap-2">
          {dirty && (
            <span className="text-[10px] font-semibold text-amber-500 flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
              Unsaved changes
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {dirty && (
            <button
              onClick={handleReset}
              className="px-3 py-1.5 rounded-lg text-xs font-medium text-gray-500 hover:text-gray-700 hover:bg-gray-100 border border-gray-200/60 transition-all"
            >
              Discard
            </button>
          )}
          <button
            onClick={handleSave}
            disabled={saving || !!parseError || !dirty}
            className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-cyan-600 text-white hover:bg-cyan-700 disabled:opacity-40 disabled:cursor-not-allowed transition-all flex items-center gap-1.5"
          >
            {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
            Save to Cosmos
          </button>
        </div>
      </div>
    </div>
  );
}
