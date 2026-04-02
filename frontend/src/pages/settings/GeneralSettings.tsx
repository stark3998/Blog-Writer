import { useEffect, useState } from "react";
import { getUserSettings, updateUserSettings } from "../../services/api";
import type { UserSettings } from "../../services/api";
import { toast } from "../../store/toastStore";
import { Loader2, ImageIcon, RefreshCw, HardDrive } from "lucide-react";

const IMAGE_OPTIONS = [
  {
    value: "store_image" as const,
    label: "Store images permanently",
    description: "Download and persist DALL-E images in the database when generated. Images never expire but use storage.",
    icon: HardDrive,
  },
  {
    value: "regenerate_on_share" as const,
    label: "Regenerate on share",
    description: "Generate a fresh image when sharing to LinkedIn if the original has expired. Saves storage but uses an extra API call.",
    icon: RefreshCw,
  },
];

export default function GeneralSettings() {
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getUserSettings()
      .then(setSettings)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleImageHandlingChange = async (value: UserSettings["image_handling"]) => {
    if (!settings || settings.image_handling === value) return;
    setSaving(true);
    try {
      const updated = await updateUserSettings({ image_handling: value });
      setSettings(updated);
      toast.success("Setting saved");
    } catch {
      toast.error("Failed to save setting");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="w-6 h-6 animate-spin text-indigo-500" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Image Handling */}
      <div className="p-5 rounded-2xl bg-white border border-gray-200/60">
        <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2 mb-1">
          <ImageIcon className="w-4 h-4 text-indigo-500" />
          Image Handling
        </h3>
        <p className="text-xs text-gray-400 mb-4">
          Choose how AI-generated hero images are handled when they expire (DALL-E URLs are temporary).
        </p>

        <div className="space-y-2">
          {IMAGE_OPTIONS.map(({ value, label, description, icon: Icon }) => (
            <button
              key={value}
              onClick={() => handleImageHandlingChange(value)}
              disabled={saving}
              className={`w-full text-left p-4 rounded-xl border transition-all duration-200 flex items-start gap-3 ${
                settings?.image_handling === value
                  ? "border-indigo-300 bg-indigo-50/50 ring-1 ring-indigo-200"
                  : "border-gray-200/60 hover:border-gray-300 hover:bg-gray-50"
              }`}
            >
              <div className={`p-2 rounded-lg shrink-0 ${
                settings?.image_handling === value ? "bg-indigo-100 text-indigo-600" : "bg-gray-100 text-gray-400"
              }`}>
                <Icon className="w-4 h-4" />
              </div>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className={`text-sm font-semibold ${
                    settings?.image_handling === value ? "text-indigo-700" : "text-gray-700"
                  }`}>
                    {label}
                  </span>
                  {settings?.image_handling === value && (
                    <span className="px-1.5 py-0.5 rounded-full text-[9px] font-bold bg-indigo-100 text-indigo-600 border border-indigo-200/60">
                      ACTIVE
                    </span>
                  )}
                </div>
                <p className="text-xs text-gray-400 mt-0.5">{description}</p>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
