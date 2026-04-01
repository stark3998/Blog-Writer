import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Rss, FileText, Tag, Activity, Clock } from "lucide-react";
import FeedsSettings from "./settings/FeedsSettings";
import PromptsSettings from "./settings/PromptsSettings";
import KeywordsSettings from "./settings/KeywordsSettings";
import DiagnosticsSettings from "./settings/DiagnosticsSettings";
import SchedulerSettings from "./settings/SchedulerSettings";

const TABS = [
  { id: "feeds", label: "Feeds", icon: Rss },
  { id: "scheduler", label: "Scheduler", icon: Clock },
  { id: "prompts", label: "Prompts", icon: FileText },
  { id: "keywords", label: "Keywords", icon: Tag },
  { id: "diagnostics", label: "Diagnostics", icon: Activity },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function Settings() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialTab = (searchParams.get("tab") as TabId) || "feeds";
  const [activeTab, setActiveTab] = useState<TabId>(
    TABS.some((t) => t.id === initialTab) ? initialTab : "feeds"
  );

  const handleTabChange = (tab: TabId) => {
    setActiveTab(tab);
    setSearchParams(tab === "feeds" ? {} : { tab });
  };

  return (
    <div className="max-w-7xl mx-auto px-6 py-8">
      {/* Page header + tabs */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 tracking-tight mb-1">Settings</h1>
        <p className="text-sm text-gray-500">
          Manage feeds, prompts, keywords, and system diagnostics.
        </p>
      </div>

      {/* Tab bar */}
      <div className="flex items-center gap-1 mb-6 border-b border-gray-200/80 pb-px">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => handleTabChange(id)}
            className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium rounded-t-xl border-b-2 transition-all duration-200 -mb-px ${
              activeTab === id
                ? "border-indigo-500 text-indigo-600 bg-indigo-50/50"
                : "border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-50"
            }`}
          >
            <Icon className="w-4 h-4" />
            {label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === "feeds" && <FeedsSettings />}
      {activeTab === "scheduler" && <SchedulerSettings />}
      {activeTab === "prompts" && <PromptsSettings />}
      {activeTab === "keywords" && <KeywordsSettings />}
      {activeTab === "diagnostics" && <DiagnosticsSettings />}
    </div>
  );
}
