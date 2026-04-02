import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Rss, FileText, Tag, Activity, Clock, Settings2, Mic, FileCode } from "lucide-react";
import GeneralSettings from "./settings/GeneralSettings";
import FeedsSettings from "./settings/FeedsSettings";
import PromptsSettings from "./settings/PromptsSettings";
import KeywordsSettings from "./settings/KeywordsSettings";
import DiagnosticsSettings from "./settings/DiagnosticsSettings";
import SchedulerSettings from "./settings/SchedulerSettings";
import VoiceProfilesSettings from "./settings/VoiceProfilesSettings";
import TemplatesSettings from "./settings/TemplatesSettings";

const TABS = [
  { id: "general", label: "General", icon: Settings2 },
  { id: "feeds", label: "Feeds", icon: Rss },
  { id: "scheduler", label: "Scheduler", icon: Clock },
  { id: "prompts", label: "Prompts", icon: FileText },
  { id: "keywords", label: "Keywords", icon: Tag },
  { id: "voice", label: "Voice", icon: Mic },
  { id: "templates", label: "Templates", icon: FileCode },
  { id: "diagnostics", label: "Diagnostics", icon: Activity },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function Settings() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialTab = (searchParams.get("tab") as TabId) || "general";
  const [activeTab, setActiveTab] = useState<TabId>(
    TABS.some((t) => t.id === initialTab) ? initialTab : "feeds"
  );

  const handleTabChange = (tab: TabId) => {
    setActiveTab(tab);
    setSearchParams(tab === "feeds" ? {} : { tab });
  };

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6 sm:py-8">
      {/* Page header + tabs */}
      <div className="mb-6">
        <h1 className="text-xl sm:text-2xl font-bold text-gray-900 tracking-tight mb-1">Settings</h1>
        <p className="text-sm text-gray-500">
          Manage feeds, prompts, keywords, and system diagnostics.
        </p>
      </div>

      {/* Tab bar — scrollable on mobile */}
      <div className="overflow-x-auto -mx-4 px-4 sm:mx-0 sm:px-0 mb-6 scrollbar-hide">
        <div className="flex items-center gap-1 border-b border-gray-200/80 pb-px min-w-max">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => handleTabChange(id)}
              className={`flex items-center gap-2 px-3 sm:px-4 py-2.5 text-sm font-medium rounded-t-xl border-b-2 transition-all duration-200 -mb-px shrink-0 ${
                activeTab === id
                  ? "border-indigo-500 text-indigo-600 bg-indigo-50/50"
                  : "border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-50"
              }`}
            >
              <Icon className="w-4 h-4" />
              <span className="hidden sm:inline">{label}</span>
              <span className="sm:hidden text-xs">{label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      {activeTab === "general" && <GeneralSettings />}
      {activeTab === "feeds" && <FeedsSettings />}
      {activeTab === "scheduler" && <SchedulerSettings />}
      {activeTab === "prompts" && <PromptsSettings />}
      {activeTab === "keywords" && <KeywordsSettings />}
      {activeTab === "voice" && <VoiceProfilesSettings />}
      {activeTab === "templates" && <TemplatesSettings />}
      {activeTab === "diagnostics" && <DiagnosticsSettings />}
    </div>
  );
}
