import { useEffect, useState } from "react";
import { listVoiceProfiles, createVoiceProfile, deleteVoiceProfile, setDefaultVoiceProfile } from "../../services/api";
import type { VoiceProfile } from "../../services/api";
import { toast } from "../../store/toastStore";
import { Loader2, Plus, Trash2, Star, Mic } from "lucide-react";

const TONES = ["professional", "casual", "technical", "conversational", "academic", "witty"];

export default function VoiceProfilesSettings() {
  const [profiles, setProfiles] = useState<VoiceProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    name: "",
    description: "",
    tone: "professional",
    style_notes: "",
    sample_text: "",
  });

  useEffect(() => {
    listVoiceProfiles().then(setProfiles).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const handleCreate = async () => {
    if (!form.name.trim()) return;
    setSaving(true);
    try {
      const profile = await createVoiceProfile(form);
      setProfiles([profile, ...profiles]);
      setShowForm(false);
      setForm({ name: "", description: "", tone: "professional", style_notes: "", sample_text: "" });
      toast.success("Voice profile created");
    } catch { toast.error("Failed to create profile"); }
    setSaving(false);
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteVoiceProfile(id);
      setProfiles(profiles.filter((p) => p.id !== id));
      toast.success("Profile deleted");
    } catch { toast.error("Failed to delete"); }
  };

  const handleSetDefault = async (id: string) => {
    try {
      await setDefaultVoiceProfile(id);
      setProfiles(profiles.map((p) => ({ ...p, isDefault: p.id === id })));
      toast.success("Default voice set");
    } catch { toast.error("Failed to set default"); }
  };

  if (loading) return <div className="flex justify-center py-12"><Loader2 className="w-6 h-6 animate-spin text-indigo-500" /></div>;

  return (
    <div className="space-y-6">
      <div className="p-5 rounded-2xl bg-white border border-gray-200/60">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
              <Mic className="w-4 h-4 text-indigo-500" />
              Voice Profiles
            </h3>
            <p className="text-xs text-gray-400 mt-1">Define your writing voice for AI-generated content.</p>
          </div>
          <button
            onClick={() => setShowForm(!showForm)}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold text-indigo-600 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200/60 transition-all"
          >
            <Plus className="w-3 h-3" />
            New Profile
          </button>
        </div>

        {showForm && (
          <div className="mb-4 p-4 rounded-xl bg-gray-50 border border-gray-200/60 space-y-3">
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Profile name (e.g. 'My Blog Voice')"
              className="w-full px-3 py-2 rounded-lg bg-white border border-gray-200/60 text-sm outline-none focus:border-indigo-300"
            />
            <input
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="Short description"
              className="w-full px-3 py-2 rounded-lg bg-white border border-gray-200/60 text-sm outline-none focus:border-indigo-300"
            />
            <select
              value={form.tone}
              onChange={(e) => setForm({ ...form, tone: e.target.value })}
              className="w-full px-3 py-2 rounded-lg bg-white border border-gray-200/60 text-sm outline-none"
            >
              {TONES.map((t) => <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>)}
            </select>
            <textarea
              value={form.style_notes}
              onChange={(e) => setForm({ ...form, style_notes: e.target.value })}
              placeholder="Style notes (e.g. 'Use short sentences, avoid jargon, include humor')"
              rows={3}
              className="w-full px-3 py-2 rounded-lg bg-white border border-gray-200/60 text-sm outline-none focus:border-indigo-300 resize-none"
            />
            <textarea
              value={form.sample_text}
              onChange={(e) => setForm({ ...form, sample_text: e.target.value })}
              placeholder="Paste a sample of your writing so AI can match your style..."
              rows={4}
              className="w-full px-3 py-2 rounded-lg bg-white border border-gray-200/60 text-sm outline-none focus:border-indigo-300 resize-none"
            />
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowForm(false)} className="px-3 py-1.5 rounded-lg text-xs font-medium text-gray-500 hover:text-gray-700">Cancel</button>
              <button
                onClick={handleCreate}
                disabled={saving || !form.name.trim()}
                className="px-4 py-1.5 rounded-lg text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 transition-all"
              >
                {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : "Create"}
              </button>
            </div>
          </div>
        )}

        <div className="space-y-2">
          {profiles.length === 0 && !showForm && (
            <p className="text-sm text-gray-400 text-center py-6">No voice profiles yet. Create one to personalize AI output.</p>
          )}
          {profiles.map((p) => (
            <div key={p.id} className={`p-4 rounded-xl border transition-all ${p.isDefault ? "border-indigo-300 bg-indigo-50/50" : "border-gray-200/60 hover:border-gray-300"}`}>
              <div className="flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-gray-900">{p.name}</span>
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-gray-100 text-gray-500">{p.tone}</span>
                    {p.isDefault && (
                      <span className="px-1.5 py-0.5 rounded-full text-[9px] font-bold bg-indigo-100 text-indigo-600 border border-indigo-200/60">DEFAULT</span>
                    )}
                  </div>
                  <p className="text-xs text-gray-400 mt-1">{p.description}</p>
                </div>
                <div className="flex items-center gap-1">
                  {!p.isDefault && (
                    <button onClick={() => handleSetDefault(p.id)} className="p-1.5 rounded-lg text-gray-300 hover:text-amber-500 hover:bg-amber-50 transition-all" title="Set as default">
                      <Star className="w-3.5 h-3.5" />
                    </button>
                  )}
                  <button onClick={() => handleDelete(p.id)} className="p-1.5 rounded-lg text-gray-300 hover:text-red-500 hover:bg-red-50 transition-all" title="Delete">
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
