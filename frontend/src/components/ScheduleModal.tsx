import { useState } from "react";
import { createScheduledPublish } from "../services/api";
import { toast } from "../store/toastStore";
import { Calendar, Clock, Loader2, X } from "lucide-react";

const PLATFORMS = [
  { id: "blog", label: "Blog" },
  { id: "linkedin", label: "LinkedIn" },
  { id: "twitter", label: "Twitter" },
  { id: "medium", label: "Medium" },
];

interface Props {
  draftId: string;
  draftTitle: string;
  onClose: () => void;
  onScheduled?: () => void;
}

export default function ScheduleModal({ draftId, draftTitle, onClose, onScheduled }: Props) {
  const [date, setDate] = useState("");
  const [time, setTime] = useState("09:00");
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>(["blog"]);
  const [saving, setSaving] = useState(false);

  const togglePlatform = (id: string) => {
    setSelectedPlatforms((prev) =>
      prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]
    );
  };

  const handleSchedule = async () => {
    if (!date || selectedPlatforms.length === 0) return;
    setSaving(true);
    try {
      const scheduledAt = new Date(`${date}T${time}:00`).toISOString();
      await createScheduledPublish({
        draft_id: draftId,
        platforms: selectedPlatforms,
        scheduled_at: scheduledAt,
      });
      toast.success("Publish scheduled!");
      onScheduled?.();
      onClose();
    } catch {
      toast.error("Failed to schedule");
    }
    setSaving(false);
  };

  // Default to tomorrow
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  const minDate = tomorrow.toISOString().slice(0, 10);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-md rounded-2xl bg-white border border-gray-200/60 shadow-2xl p-6 animate-scale-in">
        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
            <Clock className="w-4 h-4 text-indigo-500" />
            Schedule Publish
          </h3>
          <button onClick={onClose} className="p-1 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-all">
            <X className="w-4 h-4" />
          </button>
        </div>

        <p className="text-xs text-gray-500 mb-4 truncate">
          Scheduling: <span className="font-semibold text-gray-700">{draftTitle}</span>
        </p>

        {/* Date & Time */}
        <div className="grid grid-cols-2 gap-3 mb-4">
          <div>
            <label className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">Date</label>
            <div className="relative">
              <Calendar className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400 pointer-events-none" />
              <input
                type="date"
                value={date}
                min={minDate}
                onChange={(e) => setDate(e.target.value)}
                className="w-full pl-8 pr-3 py-2 rounded-lg bg-gray-50 border border-gray-200/60 text-sm outline-none focus:border-indigo-300"
              />
            </div>
          </div>
          <div>
            <label className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">Time</label>
            <div className="relative">
              <Clock className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400 pointer-events-none" />
              <input
                type="time"
                value={time}
                onChange={(e) => setTime(e.target.value)}
                className="w-full pl-8 pr-3 py-2 rounded-lg bg-gray-50 border border-gray-200/60 text-sm outline-none focus:border-indigo-300"
              />
            </div>
          </div>
        </div>

        {/* Platform selection */}
        <div className="mb-5">
          <label className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide block mb-2">Platforms</label>
          <div className="flex flex-wrap gap-2">
            {PLATFORMS.map((p) => (
              <button
                key={p.id}
                onClick={() => togglePlatform(p.id)}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-all ${
                  selectedPlatforms.includes(p.id)
                    ? "bg-indigo-50 border-indigo-200 text-indigo-600"
                    : "bg-white border-gray-200/60 text-gray-400 hover:border-gray-300"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        {/* Actions */}
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 rounded-lg text-xs font-medium text-gray-500 hover:text-gray-700">
            Cancel
          </button>
          <button
            onClick={handleSchedule}
            disabled={saving || !date || selectedPlatforms.length === 0}
            className="px-5 py-2 rounded-lg text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 transition-all flex items-center gap-1.5"
          >
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Clock className="w-3.5 h-3.5" />}
            Schedule
          </button>
        </div>
      </div>
    </div>
  );
}
