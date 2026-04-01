import { useState } from "react";
import { createPortal } from "react-dom";
import {
  connectMedium,
  getMediumStatus,
  publishMediumArticle,
} from "../services/api";
import { Loader2, X, Send, BookOpen } from "lucide-react";

interface Props {
  content: string;
  title?: string;
  excerpt?: string;
  blogUrl?: string;
  tags?: string[];
}

const SESSION_KEY = "medium_session_id";

function getStoredSession(): string | null {
  return localStorage.getItem(SESSION_KEY);
}

function storeSession(id: string) {
  localStorage.setItem(SESSION_KEY, id);
}

export default function MediumButton({ content, title, excerpt, blogUrl, tags }: Props) {
  const [busy, setBusy] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [status, setStatus] = useState("");
  const [showConnect, setShowConnect] = useState(false);
  const [showPublishModal, setShowPublishModal] = useState(false);
  const [token, setToken] = useState("");
  const [publishStatus, setPublishStatus] = useState<"draft" | "public" | "unlisted">("draft");

  const handleClick = async () => {
    if (!content.trim()) return;
    setBusy(true);
    setStatus("");

    try {
      const sessionId = getStoredSession();
      let isConnected = false;

      if (sessionId) {
        try {
          const res = await getMediumStatus(sessionId);
          isConnected = res.connected;
        } catch {
          isConnected = false;
        }
      }

      if (!isConnected) {
        setShowConnect(true);
      } else {
        setShowPublishModal(true);
      }
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Something went wrong");
      setTimeout(() => setStatus(""), 3000);
    } finally {
      setBusy(false);
    }
  };

  const handleConnect = async () => {
    if (!token.trim()) return;
    setBusy(true);
    setStatus("");

    try {
      const result = await connectMedium({
        integration_token: token.trim(),
        session_id: getStoredSession() ?? undefined,
      });
      storeSession(result.session_id);
      setShowConnect(false);
      setToken("");
      setShowPublishModal(true);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Connection failed");
    } finally {
      setBusy(false);
    }
  };

  const handlePublish = async () => {
    setPublishing(true);
    setStatus("");

    try {
      const sid = getStoredSession();
      if (!sid) throw new Error("No Medium session found");

      const result = await publishMediumArticle({
        session_id: sid,
        content,
        title: title || "",
        excerpt: excerpt || "",
        tags: tags || [],
        blog_url: blogUrl || "",
        publish_status: publishStatus,
      });

      if (result.url) {
        window.open(result.url, "_blank", "noopener,noreferrer");
      }
      setShowPublishModal(false);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Publish failed");
      setTimeout(() => setStatus(""), 3000);
    } finally {
      setPublishing(false);
    }
  };

  return (
    <>
      <div className="relative">
        <button
          onClick={handleClick}
          disabled={busy || !content.trim()}
          className="px-3 py-1.5 rounded-xl text-sm font-medium transition-all duration-200 flex items-center gap-1.5 disabled:opacity-30 border border-gray-200/60 text-gray-500 hover:text-green-700 hover:bg-green-50 hover:border-green-200/60"
          title="Publish to Medium"
        >
          {busy ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <BookOpen className="w-3.5 h-3.5" />
          )}
          Medium
        </button>

        {status && !showConnect && !showPublishModal && (
          <div className="absolute right-0 top-full mt-2 w-64 px-3 py-2 rounded-xl bg-white border border-gray-200/80 shadow-xl shadow-gray-900/5 z-50 animate-scale-in">
            <p className="text-xs text-gray-600 flex items-center gap-2">
              {busy && <Loader2 className="w-3 h-3 animate-spin shrink-0" />}
              {status}
            </p>
          </div>
        )}
      </div>

      {/* Connect Modal — Medium uses integration tokens, not OAuth */}
      {showConnect && createPortal(
        <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/30 backdrop-blur-sm">
          <div className="w-full max-w-md mx-4 rounded-2xl bg-white border border-gray-200/80 shadow-2xl shadow-gray-900/10 animate-scale-in">
            <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
              <div className="flex items-center gap-2">
                <BookOpen className="w-4 h-4 text-green-600" />
                <h3 className="text-sm font-bold text-gray-900">Connect to Medium</h3>
              </div>
              <button
                onClick={() => { setShowConnect(false); setToken(""); setStatus(""); }}
                className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="px-5 py-4 space-y-4">
              <p className="text-xs text-gray-500 leading-relaxed">
                Enter your Medium integration token. You can generate one at{" "}
                <a
                  href="https://medium.com/me/settings/security"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-green-600 underline underline-offset-2"
                >
                  Medium Settings &rarr; Security &rarr; Integration tokens
                </a>.
              </p>

              <input
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="Paste your integration token..."
                className="w-full rounded-xl bg-gray-50 border border-gray-200/80 px-4 py-2.5 text-sm text-gray-800 focus:outline-none focus:border-green-300 focus:ring-2 focus:ring-green-500/10 placeholder:text-gray-400"
              />

              {status && (
                <p className="text-xs text-red-500">{status}</p>
              )}
            </div>

            <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-gray-100">
              <button
                onClick={() => { setShowConnect(false); setToken(""); setStatus(""); }}
                className="px-4 py-2 rounded-xl text-sm font-medium text-gray-500 hover:text-gray-900 hover:bg-gray-50 border border-gray-200/60 transition-all duration-200"
              >
                Cancel
              </button>
              <button
                onClick={handleConnect}
                disabled={busy || !token.trim()}
                className="px-4 py-2 rounded-xl bg-gradient-to-r from-green-600 to-emerald-600 hover:from-green-500 hover:to-emerald-500 disabled:from-gray-200 disabled:to-gray-200 disabled:text-gray-400 text-sm font-medium text-white transition-all duration-300 flex items-center gap-1.5 shadow-sm shadow-green-500/15"
              >
                {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
                Connect
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}

      {/* Publish Modal */}
      {showPublishModal && createPortal(
        <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/30 backdrop-blur-sm">
          <div className="w-full max-w-md mx-4 rounded-2xl bg-white border border-gray-200/80 shadow-2xl shadow-gray-900/10 animate-scale-in">
            <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
              <div className="flex items-center gap-2">
                <BookOpen className="w-4 h-4 text-green-600" />
                <h3 className="text-sm font-bold text-gray-900">Publish to Medium</h3>
              </div>
              <button
                onClick={() => setShowPublishModal(false)}
                className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="px-5 py-4 space-y-4">
              <div>
                <label className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Title</label>
                <p className="text-sm text-gray-800 mt-1">{title || "Untitled"}</p>
              </div>

              {excerpt && (
                <div>
                  <label className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Excerpt</label>
                  <p className="text-xs text-gray-600 mt-1 line-clamp-2">{excerpt}</p>
                </div>
              )}

              <div>
                <label className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2 block">Publish Status</label>
                <div className="flex gap-2">
                  {(["draft", "public", "unlisted"] as const).map((s) => (
                    <button
                      key={s}
                      onClick={() => setPublishStatus(s)}
                      className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all border ${
                        publishStatus === s
                          ? "bg-green-50 text-green-700 border-green-200"
                          : "text-gray-500 border-gray-200/60 hover:border-gray-300"
                      }`}
                    >
                      {s.charAt(0).toUpperCase() + s.slice(1)}
                    </button>
                  ))}
                </div>
              </div>

              {status && (
                <p className="text-xs text-red-500">{status}</p>
              )}
            </div>

            <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-gray-100">
              <button
                onClick={() => setShowPublishModal(false)}
                disabled={publishing}
                className="px-4 py-2 rounded-xl text-sm font-medium text-gray-500 hover:text-gray-900 hover:bg-gray-50 border border-gray-200/60 transition-all duration-200 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handlePublish}
                disabled={publishing}
                className="px-4 py-2 rounded-xl bg-gradient-to-r from-green-600 to-emerald-600 hover:from-green-500 hover:to-emerald-500 disabled:from-gray-200 disabled:to-gray-200 disabled:text-gray-400 text-sm font-medium text-white transition-all duration-300 flex items-center gap-1.5 shadow-sm shadow-green-500/15"
              >
                {publishing ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Send className="w-3.5 h-3.5" />
                )}
                {publishing ? "Publishing..." : `Publish as ${publishStatus}`}
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </>
  );
}
