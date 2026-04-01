import { useEffect, useState } from "react";
import { getSchedulerStatus, getFeedHealth } from "../../services/api";
import type { SchedulerStatus, FeedHealthItem } from "../../services/api";
import {
  Clock,
  Loader2,
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  AlertCircle,
  Activity,
  Rss,
} from "lucide-react";

export default function SchedulerSettings() {
  const [scheduler, setScheduler] = useState<SchedulerStatus | null>(null);
  const [feedHealth, setFeedHealth] = useState<FeedHealthItem[]>([]);
  const [loading, setLoading] = useState(true);

  const loadData = async () => {
    setLoading(true);
    try {
      const [s, fh] = await Promise.all([getSchedulerStatus(), getFeedHealth()]);
      setScheduler(s);
      setFeedHealth(fh);
    } catch {
      // silently handle
    }
    setLoading(false);
  };

  useEffect(() => { loadData(); }, []);

  const formatNextRun = (iso: string) => {
    if (!iso) return "Not scheduled";
    const d = new Date(iso);
    const now = new Date();
    const diffMs = d.getTime() - now.getTime();
    if (diffMs < 0) return "Overdue";
    const mins = Math.round(diffMs / 60000);
    if (mins < 60) return `in ${mins}m`;
    const hrs = Math.round(mins / 60);
    return `in ${hrs}h ${mins % 60}m`;
  };

  const healthColor = (rate: number) =>
    rate >= 0.8 ? "text-emerald-500" : rate >= 0.5 ? "text-amber-500" : "text-red-500";

  const healthBg = (rate: number) =>
    rate >= 0.8 ? "bg-emerald-500" : rate >= 0.5 ? "bg-amber-500" : "bg-red-500";

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="w-6 h-6 animate-spin text-indigo-500" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Scheduler Status */}
      <div className="p-5 rounded-2xl bg-white border border-gray-200/60">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
            <Clock className="w-4 h-4 text-indigo-500" />
            Scheduler
          </h3>
          <div className="flex items-center gap-2">
            <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-bold ${
              scheduler?.running ? "bg-emerald-50 text-emerald-600 border border-emerald-200/60" : "bg-red-50 text-red-500 border border-red-200/60"
            }`}>
              <span className={`w-1.5 h-1.5 rounded-full ${scheduler?.running ? "bg-emerald-500" : "bg-red-500"}`} />
              {scheduler?.running ? "Running" : "Stopped"}
            </span>
            <button onClick={loadData} className="p-1.5 rounded-lg text-gray-400 hover:text-indigo-500 hover:bg-indigo-50 transition-all">
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {scheduler?.jobs.length === 0 ? (
          <p className="text-xs text-gray-400 py-4 text-center">No scheduled jobs. Enable feeds to start scheduling.</p>
        ) : (
          <div className="space-y-2">
            {scheduler?.jobs.map((job) => (
              <div key={job.id} className="flex items-center gap-3 p-3 rounded-xl bg-gray-50 border border-gray-200/60">
                <Rss className="w-4 h-4 text-indigo-400 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-gray-700 truncate">{job.feed_name}</p>
                  <p className="text-[10px] text-gray-400">Every {job.interval_minutes}m</p>
                </div>
                <div className="text-right shrink-0">
                  <p className="text-xs font-medium text-indigo-600">{formatNextRun(job.next_run)}</p>
                  <p className="text-[10px] text-gray-400">next crawl</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Feed Health */}
      <div className="p-5 rounded-2xl bg-white border border-gray-200/60">
        <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2 mb-4">
          <Activity className="w-4 h-4 text-emerald-500" />
          Feed Health
        </h3>

        {feedHealth.length === 0 ? (
          <p className="text-xs text-gray-400 py-4 text-center">No feed sources configured.</p>
        ) : (
          <div className="space-y-3">
            {feedHealth.map((fh) => (
              <div key={fh.feed_id} className="p-4 rounded-xl bg-gray-50 border border-gray-200/60">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full ${fh.enabled ? "bg-emerald-400" : "bg-gray-300"}`} />
                    <span className="text-xs font-semibold text-gray-700">{fh.feed_name}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    {fh.crawl_success_rate >= 0.8 ? (
                      <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
                    ) : fh.crawl_success_rate >= 0.5 ? (
                      <AlertTriangle className="w-3.5 h-3.5 text-amber-500" />
                    ) : (
                      <AlertCircle className="w-3.5 h-3.5 text-red-500" />
                    )}
                  </div>
                </div>

                <div className="grid grid-cols-4 gap-3 text-center">
                  <div>
                    <p className="text-lg font-bold text-gray-900">{fh.total_articles}</p>
                    <p className="text-[10px] text-gray-400">Articles</p>
                  </div>
                  <div>
                    <p className="text-lg font-bold text-emerald-600">{fh.relevant_articles}</p>
                    <p className="text-[10px] text-gray-400">Relevant</p>
                  </div>
                  <div>
                    <p className={`text-lg font-bold ${healthColor(fh.relevance_rate)}`}>
                      {(fh.relevance_rate * 100).toFixed(0)}%
                    </p>
                    <p className="text-[10px] text-gray-400">Rel. Rate</p>
                  </div>
                  <div>
                    <p className={`text-lg font-bold ${fh.error_articles > 0 ? "text-red-500" : "text-gray-400"}`}>
                      {fh.error_articles}
                    </p>
                    <p className="text-[10px] text-gray-400">Errors</p>
                  </div>
                </div>

                {/* Crawl success bar */}
                <div className="mt-3 flex items-center gap-2">
                  <span className="text-[10px] text-gray-400 w-16">Crawl rate</span>
                  <div className="flex-1 h-1.5 bg-gray-200 rounded-full overflow-hidden">
                    <div className={`h-full rounded-full ${healthBg(fh.crawl_success_rate)}`} style={{ width: `${fh.crawl_success_rate * 100}%` }} />
                  </div>
                  <span className="text-[10px] font-mono text-gray-500">{(fh.crawl_success_rate * 100).toFixed(0)}%</span>
                </div>

                {fh.last_crawled_at && (
                  <p className="text-[10px] text-gray-400 mt-2">
                    Last crawled: {new Date(fh.last_crawled_at).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                  </p>
                )}

                {fh.last_error && (
                  <p className="text-[10px] text-red-400 mt-1 truncate" title={fh.last_error}>
                    Last error: {fh.last_error}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
