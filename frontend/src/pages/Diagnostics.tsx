import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
    Activity,
    ArrowLeft,
    CheckCircle2,
    AlertTriangle,
    XCircle,
    Clock3,
    RefreshCw,
    KeyRound,
    Sparkles,
} from "lucide-react";
import {
    listDiagnosticsChecks,
    runDiagnostics,
} from "../services/api";
import type {
    DiagnosticsCheckFlags,
    DiagnosticsCheckMetadata,
    DiagnosticsRunResponse,
} from "../types";

const DIAGNOSTICS_KEY_STORAGE = "blog-writer:diagnostics-key";

const DEFAULT_FLAGS: DiagnosticsCheckFlags = {
    linkedin: true,
    foundry_config: true,
    text_generation: true,
    image_generation: true,
    cosmos: true,
    publish_dry_run: true,
};

export default function Diagnostics() {
    const [apiKey, setApiKey] = useState(() => {
        const envKey = (import.meta as any).env?.VITE_DIAGNOSTICS_API_KEY ?? "";
        try {
            const stored = sessionStorage.getItem(DIAGNOSTICS_KEY_STORAGE) ?? "";
            return stored || envKey;
        } catch {
            return envKey;
        }
    });
    const [sessionId, setSessionId] = useState("");
    const [includeBillable, setIncludeBillable] = useState(true);
    const [flags, setFlags] = useState<DiagnosticsCheckFlags>(DEFAULT_FLAGS);
    const [checksMeta, setChecksMeta] = useState<DiagnosticsCheckMetadata[]>([]);
    const [result, setResult] = useState<DiagnosticsRunResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");

    const allSelected = useMemo(() => Object.values(flags).every(Boolean), [flags]);

    const doRun = async () => {
        if (!apiKey.trim()) {
            setError("Diagnostics key is required.");
            return;
        }

        setLoading(true);
        setError("");
        try {
            const response = await runDiagnostics(
                {
                    session_id: sessionId.trim() || undefined,
                    include_billable: includeBillable,
                    checks: flags,
                },
                apiKey.trim()
            );
            setResult(response);
        } catch (err: any) {
            setError(err.message || "Diagnostics run failed");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        try {
            if (apiKey.trim()) {
                sessionStorage.setItem(DIAGNOSTICS_KEY_STORAGE, apiKey.trim());
            } else {
                sessionStorage.removeItem(DIAGNOSTICS_KEY_STORAGE);
            }
        } catch {
            // Ignore storage access errors (private mode / blocked storage)
        }
    }, [apiKey]);

    useEffect(() => {
        if (!apiKey.trim()) {
            return;
        }

        listDiagnosticsChecks(apiKey.trim())
            .then((res) => setChecksMeta(res.checks))
            .catch(() => setChecksMeta([]));

        doRun();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const toggleAll = (value: boolean) => {
        setFlags({
            linkedin: value,
            foundry_config: value,
            text_generation: value,
            image_generation: value,
            cosmos: value,
            publish_dry_run: value,
        });
    };

    const statusChip = (status: string) => {
        if (status === "pass") {
            return <span className="px-2 py-1 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-700">PASS</span>;
        }
        if (status === "warn") {
            return <span className="px-2 py-1 rounded-full text-xs font-semibold bg-amber-100 text-amber-700">WARN</span>;
        }
        if (status === "skip") {
            return <span className="px-2 py-1 rounded-full text-xs font-semibold bg-slate-100 text-slate-600">SKIP</span>;
        }
        return <span className="px-2 py-1 rounded-full text-xs font-semibold bg-rose-100 text-rose-700">FAIL</span>;
    };

    return (
        <div className="min-h-screen bg-[var(--bg-base)]">
            <nav className="relative glass-strong border-b border-indigo-100/60 sticky top-0 z-40">
                <div className="max-w-6xl mx-auto px-6 py-3 flex items-center justify-between">
                    <Link to="/" className="flex items-center gap-3 group">
                        <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-emerald-500 to-teal-500 flex items-center justify-center shadow-lg shadow-emerald-500/25">
                            <Activity className="w-4.5 h-4.5 text-white" />
                        </div>
                        <div className="flex items-baseline gap-2">
                            <span className="text-lg font-bold tracking-tight text-gray-900">Diagnostics</span>
                            <span className="text-[10px] font-semibold text-emerald-600 tracking-widest uppercase">Health</span>
                        </div>
                    </Link>
                    <Link
                        to="/"
                        className="px-4 py-2 rounded-xl text-sm font-semibold text-gray-500 hover:text-emerald-600 hover:bg-emerald-50 border border-gray-200/60 hover:border-emerald-200 transition-all duration-200 flex items-center gap-2"
                    >
                        <ArrowLeft className="w-3.5 h-3.5" />
                        Back
                    </Link>
                </div>
            </nav>

            <main className="max-w-6xl mx-auto px-6 py-8 space-y-6">
                <section className="glass rounded-2xl p-6 border border-gray-200/70">
                    <div className="flex items-center gap-2 mb-4">
                        <KeyRound className="w-4 h-4 text-emerald-600" />
                        <h2 className="text-sm font-semibold text-gray-800">Run Controls</h2>
                    </div>

                    <div className="grid md:grid-cols-3 gap-4">
                        <label className="flex flex-col gap-1 text-sm text-gray-700">
                            Diagnostics API Key
                            <input
                                type="password"
                                value={apiKey}
                                onChange={(e) => setApiKey(e.target.value)}
                                placeholder="X-Diagnostics-Key"
                                className="px-3 py-2 rounded-lg border border-gray-300 focus:outline-none focus:ring-2 focus:ring-emerald-300"
                            />
                        </label>

                        <label className="flex flex-col gap-1 text-sm text-gray-700">
                            LinkedIn Session ID (optional)
                            <input
                                value={sessionId}
                                onChange={(e) => setSessionId(e.target.value)}
                                placeholder="session id for OAuth status check"
                                className="px-3 py-2 rounded-lg border border-gray-300 focus:outline-none focus:ring-2 focus:ring-emerald-300"
                            />
                        </label>

                        <div className="flex flex-col gap-2 justify-end">
                            <label className="inline-flex items-center gap-2 text-sm text-gray-700">
                                <input
                                    type="checkbox"
                                    checked={includeBillable}
                                    onChange={(e) => setIncludeBillable(e.target.checked)}
                                />
                                Enable billable checks
                            </label>
                            <p className="text-xs text-gray-500">Text and image smoke tests can consume model quota.</p>
                        </div>
                    </div>

                    <div className="mt-4 flex flex-wrap items-center gap-2">
                        <button
                            onClick={() => toggleAll(!allSelected)}
                            className="px-3 py-1.5 text-xs rounded-lg border border-gray-300 hover:border-emerald-300 hover:bg-emerald-50"
                        >
                            {allSelected ? "Unselect all checks" : "Select all checks"}
                        </button>

                        {Object.entries(flags).map(([k, v]) => (
                            <label key={k} className="inline-flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg border border-gray-300 bg-white">
                                <input
                                    type="checkbox"
                                    checked={v}
                                    onChange={(e) => setFlags((prev) => ({ ...prev, [k]: e.target.checked }))}
                                />
                                {k}
                            </label>
                        ))}

                        <button
                            onClick={doRun}
                            disabled={loading}
                            className="ml-auto px-4 py-2 rounded-xl text-sm font-semibold text-white bg-gradient-to-r from-emerald-600 to-teal-600 disabled:opacity-60 inline-flex items-center gap-2"
                        >
                            {loading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
                            {loading ? "Running..." : "Run Diagnostics"}
                        </button>
                    </div>

                    {checksMeta.length > 0 && (
                        <div className="mt-4 grid md:grid-cols-2 gap-2">
                            {checksMeta.map((c) => (
                                <div key={c.key} className="text-xs text-gray-600 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
                                    <div className="font-semibold text-gray-700">{c.label} {c.billable ? "(billable)" : ""}</div>
                                    <div>{c.description}</div>
                                </div>
                            ))}
                        </div>
                    )}

                    {error && (
                        <div className="mt-4 p-3 rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-sm">{error}</div>
                    )}
                </section>

                {result && (
                    <section className="glass rounded-2xl p-6 border border-gray-200/70">
                        <div className="flex flex-wrap items-center gap-4 mb-4">
                            <h3 className="text-base font-semibold text-gray-900">Latest Result</h3>
                            <span className="text-xs text-gray-500">{new Date(result.timestamp).toLocaleString()}</span>
                            <span className={`px-2 py-1 rounded-full text-xs font-semibold ${result.overall_status === "healthy"
                                    ? "bg-emerald-100 text-emerald-700"
                                    : result.overall_status === "degraded"
                                        ? "bg-amber-100 text-amber-700"
                                        : "bg-rose-100 text-rose-700"
                                }`}>
                                {result.overall_status.toUpperCase()}
                            </span>
                        </div>

                        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-xs mb-5">
                            <div className="p-2 rounded-lg bg-gray-50 border border-gray-200">Total: {result.summary.total}</div>
                            <div className="p-2 rounded-lg bg-emerald-50 border border-emerald-200">Pass: {result.summary.passed}</div>
                            <div className="p-2 rounded-lg bg-rose-50 border border-rose-200">Fail: {result.summary.failed}</div>
                            <div className="p-2 rounded-lg bg-amber-50 border border-amber-200">Warn: {result.summary.warned}</div>
                            <div className="p-2 rounded-lg bg-slate-50 border border-slate-200">Skip: {result.summary.skipped}</div>
                        </div>

                        <div className="space-y-3">
                            {result.checks.map((check) => (
                                <div key={check.key} className="rounded-xl border border-gray-200 bg-white p-4">
                                    <div className="flex flex-wrap items-center gap-2 mb-2">
                                        {check.status === "pass" && <CheckCircle2 className="w-4 h-4 text-emerald-600" />}
                                        {check.status === "warn" && <AlertTriangle className="w-4 h-4 text-amber-600" />}
                                        {(check.status === "fail") && <XCircle className="w-4 h-4 text-rose-600" />}
                                        {check.status === "skip" && <Clock3 className="w-4 h-4 text-slate-500" />}
                                        <div className="font-semibold text-sm text-gray-800">{check.label}</div>
                                        {statusChip(check.status)}
                                        {check.billable && <span className="px-2 py-1 rounded-full text-xs bg-indigo-100 text-indigo-700">billable</span>}
                                        <span className="ml-auto text-xs text-gray-500">{check.duration_ms}ms</span>
                                    </div>

                                    {check.recommendation && (
                                        <p className="text-xs text-gray-600 mb-2">Recommendation: {check.recommendation}</p>
                                    )}

                                    <pre className="text-xs bg-gray-50 border border-gray-200 rounded-lg p-3 overflow-x-auto text-gray-700">
                                        {JSON.stringify(check.details, null, 2)}
                                    </pre>
                                </div>
                            ))}
                        </div>
                    </section>
                )}
            </main>
        </div>
    );
}
