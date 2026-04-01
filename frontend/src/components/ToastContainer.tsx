import { useToastStore, type ToastType } from "../store/toastStore";
import { X, CheckCircle2, AlertTriangle, Info, AlertCircle } from "lucide-react";

const ICON_MAP: Record<ToastType, typeof CheckCircle2> = {
  success: CheckCircle2,
  error: AlertCircle,
  warning: AlertTriangle,
  info: Info,
};

const STYLE_MAP: Record<ToastType, string> = {
  success: "bg-emerald-50 border-emerald-200/60 text-emerald-800",
  error: "bg-red-50 border-red-200/60 text-red-800",
  warning: "bg-amber-50 border-amber-200/60 text-amber-800",
  info: "bg-indigo-50 border-indigo-200/60 text-indigo-800",
};

const ICON_STYLE_MAP: Record<ToastType, string> = {
  success: "text-emerald-500",
  error: "text-red-500",
  warning: "text-amber-500",
  info: "text-indigo-500",
};

export default function ToastContainer() {
  const { toasts, removeToast } = useToastStore();

  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 max-w-sm">
      {toasts.map((t) => {
        const Icon = ICON_MAP[t.type];
        return (
          <div
            key={t.id}
            className={`flex items-start gap-3 px-4 py-3 rounded-xl border shadow-lg animate-slide-in-right ${STYLE_MAP[t.type]}`}
          >
            <Icon className={`w-5 h-5 shrink-0 mt-0.5 ${ICON_STYLE_MAP[t.type]}`} />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold">{t.title}</p>
              {t.message && <p className="text-xs opacity-80 mt-0.5">{t.message}</p>}
            </div>
            <button
              onClick={() => removeToast(t.id)}
              className="p-0.5 rounded-lg hover:bg-black/5 transition-colors shrink-0"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
