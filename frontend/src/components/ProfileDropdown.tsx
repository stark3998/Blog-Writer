import { useEffect, useRef, useState } from "react";
import { useAuth } from "../auth/AuthProvider";
import { getUserProfile } from "../services/api";
import type { UserProfile } from "../services/api";
import {
  User,
  Mail,
  Calendar,
  LogOut,
  Loader2,
  ChevronDown,
  KeyRound,
  Copy,
  Check,
} from "lucide-react";

function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const parts = token.split(".");
    if (parts.length < 2) return null;
    const payload = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const json = atob(payload);
    return JSON.parse(json);
  } catch {
    return null;
  }
}

export default function ProfileDropdown() {
  const { user, logout, isAuthenticated, getAccessToken } = useAuth();
  const [open, setOpen] = useState(false);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(false);
  const [showToken, setShowToken] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [claims, setClaims] = useState<Record<string, unknown> | null>(null);
  const [copied, setCopied] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Load profile when dropdown opens
  useEffect(() => {
    if (open && !profile && isAuthenticated) {
      setLoading(true);
      getUserProfile()
        .then(setProfile)
        .catch(() => {})
        .finally(() => setLoading(false));
    }
  }, [open, profile, isAuthenticated]);

  const handleShowToken = async () => {
    if (showToken) {
      setShowToken(false);
      return;
    }
    try {
      const t = await getAccessToken();
      setToken(t || "(no token available)");
      setClaims(t ? decodeJwtPayload(t) : null);
      setShowToken(true);
    } catch {
      setToken("(failed to acquire token)");
      setShowToken(true);
    }
  };

  const handleCopyToken = () => {
    if (token) {
      navigator.clipboard.writeText(token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const displayName = profile?.name || user?.name || "User";
  const initials = displayName[0]?.toUpperCase() || "?";

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 px-2 py-1.5 rounded-xl text-sm text-gray-600 hover:text-indigo-600 hover:bg-indigo-50 transition-all duration-200"
      >
        <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-indigo-500 to-violet-500 flex items-center justify-center text-white text-xs font-bold shadow-sm">
          {initials}
        </div>
        <span className="hidden sm:inline font-medium truncate max-w-[120px]">
          {displayName}
        </span>
        <ChevronDown className={`w-3.5 h-3.5 text-gray-400 transition-transform duration-200 ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-80 rounded-2xl bg-white border border-gray-200/80 shadow-xl shadow-gray-200/50 z-50 overflow-hidden animate-fade-in-up">
          {/* User header */}
          <div className="p-4 border-b border-gray-100 bg-gradient-to-br from-indigo-50/50 to-violet-50/50">
            {loading ? (
              <div className="flex justify-center py-2">
                <Loader2 className="w-5 h-5 animate-spin text-indigo-500" />
              </div>
            ) : (
              <div className="flex items-center gap-3">
                <div className="w-11 h-11 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 flex items-center justify-center shadow-lg shadow-indigo-500/25">
                  <span className="text-lg text-white font-bold">{initials}</span>
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-bold text-gray-900 truncate">{displayName}</p>
                  <p className="text-xs text-gray-500 truncate">
                    {profile?.email || user?.username || ""}
                  </p>
                </div>
              </div>
            )}
          </div>

          {/* Info rows */}
          {profile && (
            <div className="p-3 border-b border-gray-100 space-y-2">
              <div className="flex items-center gap-2.5 px-2 py-1.5 text-xs">
                <Mail className="w-3.5 h-3.5 text-gray-400 shrink-0" />
                <span className="text-gray-500">Email</span>
                <span className="ml-auto text-gray-700 truncate max-w-[160px]">{profile.email || "—"}</span>
              </div>
              <div className="flex items-center gap-2.5 px-2 py-1.5 text-xs">
                <User className="w-3.5 h-3.5 text-gray-400 shrink-0" />
                <span className="text-gray-500">User ID</span>
                <span className="ml-auto text-gray-700 font-mono truncate max-w-[160px]">{profile.id || "—"}</span>
              </div>
              <div className="flex items-center gap-2.5 px-2 py-1.5 text-xs">
                <Calendar className="w-3.5 h-3.5 text-gray-400 shrink-0" />
                <span className="text-gray-500">Last Login</span>
                <span className="ml-auto text-gray-700">
                  {profile.lastLoginAt ? new Date(profile.lastLoginAt).toLocaleString() : "—"}
                </span>
              </div>
            </div>
          )}

          {/* Token viewer */}
          <div className="p-3 border-b border-gray-100">
            <button
              onClick={handleShowToken}
              className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs font-medium text-gray-600 hover:text-indigo-600 hover:bg-indigo-50 transition-all"
            >
              <KeyRound className="w-3.5 h-3.5" />
              {showToken ? "Hide Token" : "View Token & Claims"}
            </button>

            {showToken && token && (
              <div className="mt-2 space-y-2">
                {/* Raw token */}
                <div className="relative">
                  <div className="rounded-lg bg-gray-900 p-2.5 max-h-20 overflow-y-auto">
                    <code className="text-[10px] text-gray-300 break-all font-mono leading-relaxed">
                      {token}
                    </code>
                  </div>
                  <button
                    onClick={handleCopyToken}
                    className="absolute top-1.5 right-1.5 p-1 rounded text-gray-500 hover:text-white transition-colors"
                    title="Copy token"
                  >
                    {copied ? <Check className="w-3 h-3 text-green-400" /> : <Copy className="w-3 h-3" />}
                  </button>
                </div>

                {/* Decoded claims */}
                {claims && (
                  <div className="rounded-lg bg-gray-50 border border-gray-200/80 p-2.5 max-h-48 overflow-y-auto">
                    <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
                      Decoded Claims
                    </p>
                    <div className="space-y-1">
                      {Object.entries(claims).map(([key, value]) => (
                        <div key={key} className="flex gap-2 text-[11px]">
                          <span className="font-mono font-semibold text-indigo-600 shrink-0">{key}:</span>
                          <span className="text-gray-700 break-all font-mono">
                            {typeof value === "object" ? JSON.stringify(value) : String(value)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Sign out */}
          <div className="p-2">
            <button
              onClick={() => { setOpen(false); logout(); }}
              className="w-full flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium text-red-600 hover:bg-red-50 transition-all"
            >
              <LogOut className="w-3.5 h-3.5" />
              Sign Out
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
