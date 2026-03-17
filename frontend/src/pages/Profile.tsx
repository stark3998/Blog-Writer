import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { getUserProfile } from "../services/api";
import type { UserProfile } from "../services/api";
import {
  ArrowLeft,
  User,
  Mail,
  Calendar,
  LogOut,
  Loader2,
  AlertTriangle,
} from "lucide-react";

export default function Profile() {
  const { user, logout, isAuthenticated } = useAuth();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isAuthenticated) return;
    loadProfile();
  }, [isAuthenticated]);

  async function loadProfile() {
    try {
      const p = await getUserProfile();
      setProfile(p);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-[var(--bg-base)]">
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-[-20%] right-[-10%] w-[600px] h-[600px] bg-amber-200/20 rounded-full blur-[120px]" />
        <div className="absolute bottom-[-10%] left-[10%] w-[500px] h-[500px] bg-orange-200/15 rounded-full blur-[100px]" />
      </div>

      <nav className="relative glass-strong border-b border-amber-100/60 animate-fade-in-down sticky top-0 z-40">
        <div className="max-w-4xl mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link
              to="/"
              className="p-2 rounded-xl text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-all"
            >
              <ArrowLeft size={20} />
            </Link>
            <div className="flex items-center gap-2">
              <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-amber-500 to-orange-500 flex items-center justify-center shadow-lg shadow-amber-500/25">
                <User className="w-4.5 h-4.5 text-white" />
              </div>
              <span className="text-lg font-bold tracking-tight text-gray-900">
                Profile
              </span>
            </div>
          </div>
          <button
            onClick={logout}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-red-50 text-red-600 hover:bg-red-100 border border-red-200/60 transition-all"
          >
            <LogOut size={14} />
            Sign Out
          </button>
        </div>
      </nav>

      <main className="relative max-w-4xl mx-auto px-6 py-8">
        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-50 border border-red-200/60 text-red-600 text-sm flex items-center gap-2">
            <AlertTriangle size={14} />
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 size={24} className="animate-spin text-amber-500" />
          </div>
        ) : (
          <div className="space-y-6">
            {/* User Info Card */}
            <div className="glass-strong rounded-2xl border border-amber-100/60 p-8">
              <div className="flex items-center gap-4 mb-6">
                <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-amber-500 to-orange-500 flex items-center justify-center shadow-lg shadow-amber-500/25">
                  <span className="text-2xl text-white font-bold">
                    {(profile?.name || user?.name || "?")[0]?.toUpperCase()}
                  </span>
                </div>
                <div>
                  <h2 className="text-xl font-bold text-gray-900">
                    {profile?.name || user?.name || "User"}
                  </h2>
                  <p className="text-sm text-gray-500">
                    {profile?.email || user?.username || ""}
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="flex items-center gap-3 p-4 rounded-xl bg-gray-50/80 border border-gray-200/60">
                  <Mail size={18} className="text-gray-400" />
                  <div>
                    <p className="text-xs text-gray-400 font-medium">Email</p>
                    <p className="text-sm text-gray-900">
                      {profile?.email || "Not available"}
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-3 p-4 rounded-xl bg-gray-50/80 border border-gray-200/60">
                  <User size={18} className="text-gray-400" />
                  <div>
                    <p className="text-xs text-gray-400 font-medium">User ID</p>
                    <p className="text-sm text-gray-900 font-mono truncate max-w-[200px]">
                      {profile?.id || "—"}
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-3 p-4 rounded-xl bg-gray-50/80 border border-gray-200/60">
                  <Calendar size={18} className="text-gray-400" />
                  <div>
                    <p className="text-xs text-gray-400 font-medium">Member Since</p>
                    <p className="text-sm text-gray-900">
                      {profile?.createdAt
                        ? new Date(profile.createdAt).toLocaleDateString()
                        : "—"}
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-3 p-4 rounded-xl bg-gray-50/80 border border-gray-200/60">
                  <Calendar size={18} className="text-gray-400" />
                  <div>
                    <p className="text-xs text-gray-400 font-medium">Last Login</p>
                    <p className="text-sm text-gray-900">
                      {profile?.lastLoginAt
                        ? new Date(profile.lastLoginAt).toLocaleString()
                        : "—"}
                    </p>
                  </div>
                </div>
              </div>
            </div>

            {/* LinkedIn Connection */}
            <div className="glass-strong rounded-2xl border border-amber-100/60 p-8">
              <h3 className="text-lg font-bold text-gray-900 mb-3">
                LinkedIn Connection
              </h3>
              <p className="text-sm text-gray-500">
                {profile?.linkedinSessionId
                  ? "LinkedIn account is connected."
                  : "No LinkedIn account connected. Connect via Settings."}
              </p>
              {!profile?.linkedinSessionId && (
                <Link
                  to="/settings"
                  className="inline-flex items-center gap-1.5 mt-3 px-4 py-2 rounded-lg text-sm font-medium bg-amber-50 text-amber-600 hover:bg-amber-100 border border-amber-200/60 transition-all"
                >
                  Go to Settings
                </Link>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
