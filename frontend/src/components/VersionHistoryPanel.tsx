import { useEffect, useState } from "react";
import { listDraftVersions, getDraftVersion, restoreDraftVersion } from "../services/api";
import type { VersionSummary } from "../services/api";
import { History, RotateCw, Loader2, X, Clock, FileText } from "lucide-react";
import { toast } from "../store/toastStore";

interface Props {
  draftId: string;
  onRestore: (content: string) => void;
  onClose: () => void;
}

export default function VersionHistoryPanel({ draftId, onRestore, onClose }: Props) {
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [restoring, setRestoring] = useState<string | null>(null);
  const [previewContent, setPreviewContent] = useState<string | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  useEffect(() => {
    if (!draftId) return;
    setLoading(true);
    listDraftVersions(draftId)
      .then(setVersions)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [draftId]);

  const handlePreview = async (versionId: string) => {
    if (previewId === versionId) { setPreviewContent(null); setPreviewId(null); return; }
    setPreviewLoading(true);
    try {
      const v = await getDraftVersion(draftId, versionId);
      setPreviewContent(v.content);
      setPreviewId(versionId);
    } catch { toast.error("Failed to load version"); }
    setPreviewLoading(false);
  };

  const handleRestore = async (versionId: string) => {
    setRestoring(versionId);
    try {
      const updated = await restoreDraftVersion(draftId, versionId);
      onRestore(updated.content);
      toast.success("Version restored");
      // Reload versions
      listDraftVersions(draftId).then(setVersions).catch(() => {});
    } catch { toast.error("Failed to restore version"); }
    setRestoring(null);
  };

  const formatDate = (iso: string) => {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) +
      " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  };

  const triggerLabel: Record<string, string> = {
    manual: "Manual save",
    auto_save: "Auto-save",
    pre_restore: "Pre-restore backup",
  };

  return (
    <div className="w-80 h-full flex flex-col animate-slide-in-right">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200/60">
        <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
          <History className="w-4 h-4 text-violet-500" />
          Version History
        </h3>
        <button onClick={onClose} className="p-1 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-all">
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-5 h-5 animate-spin text-violet-500" />
          </div>
        ) : versions.length === 0 ? (
          <div className="p-8 text-center">
            <History className="w-8 h-8 text-gray-200 mx-auto mb-2" />
            <p className="text-xs text-gray-400">No versions yet</p>
            <p className="text-[10px] text-gray-400 mt-1">Versions are saved automatically when you edit</p>
          </div>
        ) : (
          <div className="divide-y divide-gray-50">
            {versions.map((v, i) => (
              <div key={v.id} className="px-4 py-3 hover:bg-gray-50/50 transition-colors">
                <div className="flex items-start gap-3">
                  <div className="mt-1">
                    <div className={`w-2 h-2 rounded-full ${i === 0 ? "bg-violet-500" : "bg-gray-300"}`} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <Clock className="w-3 h-3 text-gray-400" />
                      <span className="text-xs font-medium text-gray-700">{formatDate(v.createdAt)}</span>
                    </div>
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="text-[10px] text-gray-400">
                        {triggerLabel[v.trigger] ?? v.trigger}
                      </span>
                      <span className="text-[10px] text-gray-400">
                        <FileText className="w-2.5 h-2.5 inline" /> {Math.round(v.contentLength / 1024)}KB
                      </span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <button
                        onClick={() => handlePreview(v.id)}
                        className="text-[10px] font-semibold text-indigo-500 hover:text-indigo-700 transition-colors"
                      >
                        {previewLoading && previewId === v.id ? "Loading..." : previewId === v.id ? "Hide" : "Preview"}
                      </button>
                      <button
                        onClick={() => handleRestore(v.id)}
                        disabled={!!restoring}
                        className="inline-flex items-center gap-1 text-[10px] font-semibold text-violet-500 hover:text-violet-700 transition-colors disabled:opacity-40"
                      >
                        {restoring === v.id ? <Loader2 className="w-2.5 h-2.5 animate-spin" /> : <RotateCw className="w-2.5 h-2.5" />}
                        Restore
                      </button>
                    </div>
                    {previewId === v.id && previewContent && (
                      <pre className="mt-2 p-2 rounded-lg bg-gray-50 border border-gray-200/60 text-[10px] text-gray-600 max-h-[200px] overflow-auto whitespace-pre-wrap font-mono">
                        {previewContent.slice(0, 2000)}{previewContent.length > 2000 ? "..." : ""}
                      </pre>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
