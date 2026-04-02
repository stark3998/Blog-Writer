import { useEffect, useState } from "react";
import { getEnvConfig } from "../../services/api";
import type { EnvGroup } from "../../services/api";
import { toast } from "../../store/toastStore";
import { Loader2, CheckCircle2, XCircle, Eye, EyeOff, RefreshCw, Copy, KeyRound } from "lucide-react";

const DIAGNOSTICS_KEY_STORAGE = "blog-writer:diagnostics-key";

export default function EnvSettings() {
  const [apiKey, setApiKey] = useState(() => {
    const envKey = (import.meta as any).env?.VITE_DIAGNOSTICS_API_KEY ?? "";
    try {
      return sessionStorage.getItem(DIAGNOSTICS_KEY_STORAGE) ?? envKey;
    } catch {
      return envKey;
    }
  });
  const [groups, setGroups] = useState<EnvGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [showSecrets, setShowSecrets] = useState(false);
  const [error, setError] = useState("");

  const load = () => {
    const key = apiKey.trim();
    if (!key) {
      setError("Diagnostics API key is required to view environment settings.");
      return;
    }
    setLoading(true);
    setError("");
    getEnvConfig(key)
      .then((r) => setGroups(r.groups))
      .catch((err) => {
        const msg = err?.message || "";
        if (msg.includes("401")) setError("Invalid diagnostics API key.");
        else if (msg.includes("503")) setError("Diagnostics API key is not configured on the server.");
        else setError("Failed to load environment config.");
        setGroups([]);
      })
      .finally(() => setLoading(false));
  };

  // Persist key in sessionStorage
  useEffect(() => {
    try {
      if (apiKey.trim()) sessionStorage.setItem(DIAGNOSTICS_KEY_STORAGE, apiKey.trim());
      else sessionStorage.removeItem(DIAGNOSTICS_KEY_STORAGE);
    } catch {}
  }, [apiKey]);

  // Auto-load if key is available
  useEffect(() => {
    if (apiKey.trim()) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const totalVars = groups.reduce((n, g) => n + g.vars.length, 0);
  const setVars = groups.reduce((n, g) => n + g.vars.filter((v) => v.is_set).length, 0);

  const copyAll = () => {
    const lines = groups.flatMap((g) => [
      `# ${g.category}`,
      ...g.vars.map((v) => `${v.name}=${v.is_secret && !showSecrets ? "(secret)" : v.value || "(not set)"}`),
      "",
    ]);
    navigator.clipboard.writeText(lines.join("\n"));
    toast.success("Copied to clipboard");
  };

  return (
    <div className="space-y-6">
      {/* API Key input */}
      <div className="rounded-xl border border-gray-200/80 bg-white p-5">
        <div className="flex items-center gap-3">
          <KeyRound className="w-4 h-4 text-gray-400 shrink-0" />
          <input
            type="password"
            placeholder="Diagnostics API Key"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && load()}
            className="flex-1 px-3 py-2 rounded-lg bg-gray-50 border border-gray-200/80 text-sm text-gray-700 outline-none focus:border-indigo-300 focus:ring-2 focus:ring-indigo-500/10 font-mono"
          />
          <button
            onClick={load}
            disabled={loading || !apiKey.trim()}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-indigo-500 text-white hover:bg-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed transition flex items-center gap-2"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            {groups.length ? "Refresh" : "Load"}
          </button>
        </div>
        {error && <p className="mt-2 text-sm text-red-500">{error}</p>}
      </div>

      {loading && !groups.length && (
        <div className="flex items-center justify-center py-16 text-gray-400">
          <Loader2 className="w-5 h-5 animate-spin mr-2" />
          Loading environment config…
        </div>
      )}

      {!loading && !groups.length && !error && (
        <div className="text-center py-16 text-gray-400 text-sm">
          Enter your diagnostics API key to view environment settings.
        </div>
      )}

      {groups.length > 0 && (
        <>
      {/* Header bar */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-gray-500">
            {setVars} of {totalVars} variables configured
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowSecrets((s) => !s)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 transition"
          >
            {showSecrets ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
            {showSecrets ? "Hide secrets" : "Show secrets"}
          </button>
          <button
            onClick={copyAll}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 transition"
          >
            <Copy className="w-3.5 h-3.5" />
            Copy all
          </button>
          <button
            onClick={load}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 transition"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Refresh
          </button>
        </div>
      </div>

      {/* Groups */}
      {groups.map((group) => (
        <div key={group.category} className="rounded-xl border border-gray-200/80 bg-white overflow-hidden">
          <div className="px-5 py-3 bg-gray-50/80 border-b border-gray-200/60">
            <h3 className="text-sm font-semibold text-gray-700">{group.category}</h3>
          </div>
          <div className="divide-y divide-gray-100">
            {group.vars.map((v) => (
              <div key={v.name} className="flex items-center gap-3 px-5 py-3 hover:bg-gray-50/50 transition">
                {v.is_set ? (
                  <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />
                ) : (
                  <XCircle className="w-4 h-4 text-red-400 shrink-0" />
                )}
                <span className="text-sm font-mono text-gray-800 w-72 shrink-0 truncate" title={v.name}>
                  {v.name}
                </span>
                <span
                  className={`text-sm font-mono truncate ${
                    v.is_set ? "text-gray-600" : "text-gray-300 italic"
                  }`}
                  title={v.is_set ? v.value : "Not set"}
                >
                  {!v.is_set
                    ? "—"
                    : v.is_secret && !showSecrets
                      ? v.value
                      : v.value}
                </span>
              </div>
            ))}
          </div>
        </div>
      ))}
        </>
      )}
    </div>
  );
}
