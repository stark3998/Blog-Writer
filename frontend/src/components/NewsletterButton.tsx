import { useState } from "react";
import { previewNewsletter, sendNewsletter } from "../services/api";
import { toast } from "../store/toastStore";
import { Mail, Loader2, Send, Eye, X } from "lucide-react";

const PROVIDERS = [
  { id: "mailchimp" as const, label: "Mailchimp", fields: ["api_key", "list_id"] },
  { id: "convertkit" as const, label: "ConvertKit", fields: ["api_secret", "sequence_id"] },
  { id: "smtp" as const, label: "SMTP", fields: ["host", "port", "username", "password", "from_email", "to_list"] },
];

interface Props {
  draftId?: string;
}

export default function NewsletterButton({ draftId }: Props) {
  const [open, setOpen] = useState(false);
  const [provider, setProvider] = useState<"mailchimp" | "convertkit" | "smtp">("mailchimp");
  const [config, setConfig] = useState<Record<string, string>>({});
  const [sending, setSending] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<{ subject: string; html_body: string } | null>(null);

  if (!draftId) return null;

  const currentProvider = PROVIDERS.find((p) => p.id === provider)!;

  const handlePreview = async () => {
    setPreviewing(true);
    try {
      const res = await previewNewsletter(draftId);
      setPreview(res);
    } catch {
      toast.error("Failed to generate preview");
    }
    setPreviewing(false);
  };

  const handleSend = async () => {
    setSending(true);
    try {
      await sendNewsletter({ draft_id: draftId, provider, config });
      toast.success("Newsletter sent!");
      setOpen(false);
    } catch (err) {
      toast.error("Send failed", err instanceof Error ? err.message : undefined);
    }
    setSending(false);
  };

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-gray-600 hover:bg-gray-50 transition-all text-left"
      >
        <Mail className="w-4 h-4 text-orange-500" />
        <span className="font-medium">Newsletter</span>
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm animate-fade-in" onClick={(e) => { if (e.target === e.currentTarget) setOpen(false); }}>
          <div className="w-full max-w-lg max-h-[80vh] rounded-2xl bg-white border border-gray-200/60 shadow-2xl flex flex-col animate-scale-in">
            {/* Header */}
            <div className="flex items-center justify-between px-5 pt-4 pb-3">
              <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
                <Mail className="w-4 h-4 text-orange-500" />
                Send as Newsletter
              </h3>
              <button onClick={() => setOpen(false)} className="p-1 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-all">
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-5 pb-4 space-y-4">
              {/* Provider select */}
              <div>
                <label className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">Provider</label>
                <div className="flex gap-2">
                  {PROVIDERS.map((p) => (
                    <button
                      key={p.id}
                      onClick={() => { setProvider(p.id); setConfig({}); setPreview(null); }}
                      className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-all ${
                        provider === p.id
                          ? "bg-indigo-50 border-indigo-200 text-indigo-600"
                          : "bg-white border-gray-200/60 text-gray-400"
                      }`}
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Config fields */}
              <div className="space-y-2">
                {currentProvider.fields.map((field) => (
                  <div key={field}>
                    <label className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide block mb-1">
                      {field.replace(/_/g, " ")}
                    </label>
                    <input
                      type={field.includes("password") || field.includes("secret") || field.includes("key") ? "password" : "text"}
                      value={config[field] || ""}
                      onChange={(e) => setConfig({ ...config, [field]: e.target.value })}
                      placeholder={field.replace(/_/g, " ")}
                      className="w-full px-3 py-2 rounded-lg bg-gray-50 border border-gray-200/60 text-sm outline-none focus:border-indigo-300"
                    />
                  </div>
                ))}
              </div>

              {/* Preview */}
              {preview && (
                <div className="p-3 rounded-xl bg-gray-50 border border-gray-200/60">
                  <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-1">Subject</p>
                  <p className="text-sm font-medium text-gray-900 mb-2">{preview.subject}</p>
                  <div className="max-h-40 overflow-y-auto text-xs text-gray-600 border-t border-gray-200/60 pt-2" dangerouslySetInnerHTML={{ __html: preview.html_body }} />
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="flex justify-end gap-2 px-5 py-3 border-t border-gray-100">
              <button
                onClick={handlePreview}
                disabled={previewing}
                className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium text-gray-500 hover:text-gray-700 border border-gray-200/60 transition-all"
              >
                {previewing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Eye className="w-3 h-3" />}
                Preview
              </button>
              <button
                onClick={handleSend}
                disabled={sending || currentProvider.fields.some((f) => !config[f]?.trim())}
                className="inline-flex items-center gap-1 px-4 py-1.5 rounded-lg text-xs font-semibold text-white bg-orange-500 hover:bg-orange-400 disabled:opacity-40 transition-all"
              >
                {sending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}
                Send
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
