import { useState } from "react";
import { createPortal } from "react-dom";
import {
  composeLinkedInPost,
  publishLinkedInPost,
  startLinkedInOAuth,
  getLinkedInStatus,
} from "../services/api";
import { Loader2, Linkedin, X, Send } from "lucide-react";
import { toast } from "../store/toastStore";

interface Props {
  content: string;
  title?: string;
  excerpt?: string;
  blogUrl?: string;
}

interface PreviewData {
  postText: string;
  imageUrl: string;
  hashtags: string[];
}

const SESSION_KEY = "linkedin_session_id";

function getStoredSession(): string | null {
  return localStorage.getItem(SESSION_KEY);
}

function storeSession(id: string) {
  localStorage.setItem(SESSION_KEY, id);
}

function wordCount(text: string): number {
  return text.trim().split(/\s+/).filter(Boolean).length;
}

export default function LinkedInButton({ content, title, excerpt, blogUrl }: Props) {
  const [busy, setBusy] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [status, setStatus] = useState("");
  const [preview, setPreview] = useState<PreviewData | null>(null);

  const handleClick = async () => {
    if (!content.trim()) return;
    setBusy(true);
    setStatus("");

    try {
      setStatus("Checking connection...");
      const sessionId = getStoredSession();
      let isConnected = false;

      if (sessionId) {
        try {
          const res = await getLinkedInStatus(sessionId);
          isConnected = res.connected;
        } catch {
          isConnected = false;
        }
      }

      if (!isConnected) {
        setStatus("Connecting to LinkedIn...");
        const oauthRes = await startLinkedInOAuth(sessionId ?? undefined);
        storeSession(oauthRes.session_id);

        const popup = window.open(oauthRes.auth_url, "linkedin-oauth", "width=600,height=700");

        await new Promise<void>((resolve, reject) => {
          const handleMessage = (e: MessageEvent) => {
            if (e.data?.type === "linkedin-oauth-callback" && e.data.session_id) {
              storeSession(e.data.session_id);
              window.removeEventListener("message", handleMessage);
              clearInterval(pollTimer);
              resolve();
            }
          };
          window.addEventListener("message", handleMessage);

          const pollTimer = setInterval(() => {
            if (popup && popup.closed) {
              clearInterval(pollTimer);
              window.removeEventListener("message", handleMessage);
              const sid = getStoredSession();
              if (sid) {
                getLinkedInStatus(sid)
                  .then((res) => {
                    if (res.connected) resolve();
                    else reject(new Error("LinkedIn authentication was cancelled"));
                  })
                  .catch(() => reject(new Error("LinkedIn authentication was cancelled")));
              } else {
                reject(new Error("LinkedIn authentication was cancelled"));
              }
            }
          }, 500);

          setTimeout(() => {
            clearInterval(pollTimer);
            window.removeEventListener("message", handleMessage);
            reject(new Error("LinkedIn authentication timed out"));
          }, 120_000);
        });
      }

      setStatus("AI is composing your post...");
      const composed = await composeLinkedInPost({
        content,
        title,
        excerpt,
        post_format: "feed_post",
        blog_url: blogUrl || undefined,
      });

      setPreview({
        postText: composed.post_text,
        imageUrl: composed.image_url,
        hashtags: composed.hashtags,
      });
      setStatus("");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Something went wrong";
      setStatus(message);
      toast.error("LinkedIn error", message);
      setTimeout(() => setStatus(""), 5000);
    } finally {
      setBusy(false);
    }
  };

  const handlePublish = async () => {
    if (!preview) return;
    setPublishing(true);

    try {
      const sid = getStoredSession();
      if (!sid) throw new Error("No LinkedIn session found");

      const result = await publishLinkedInPost({
        session_id: sid,
        post_text: preview.postText,
        image_url: preview.imageUrl,
      });

      setPreview(null);
      toast.success("Published to LinkedIn!");

      if (result.post_id) {
        const postUrn = result.post_id;
        // Handle both urn:li:share:XXX and urn:li:ugcPost:XXX formats
        const activityId = postUrn.replace(/^urn:li:(share|ugcPost):/, "");
        window.open(
          `https://www.linkedin.com/feed/update/urn:li:share:${activityId}/`,
          "_blank",
          "noopener,noreferrer"
        );
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Publish failed";
      setStatus(message);
      toast.error("LinkedIn publish failed", message);
      setTimeout(() => setStatus(""), 5000);
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
          className="px-3 py-1.5 rounded-xl text-sm font-medium transition-all duration-200 flex items-center gap-1.5 disabled:opacity-30 border border-gray-200/60 text-gray-500 hover:text-[#0a66c2] hover:bg-blue-50 hover:border-blue-200/60"
          title="Post to LinkedIn"
        >
          {busy ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Linkedin className="w-3.5 h-3.5" />
          )}
          LinkedIn
        </button>

        {/* Status tooltip */}
        {status && !preview && (
          <div className="absolute right-0 top-full mt-2 w-64 px-3 py-2 rounded-xl bg-white border border-gray-200/80 shadow-xl shadow-gray-900/5 z-50 animate-scale-in">
            <p className="text-xs text-gray-600 flex items-center gap-2">
              {busy && <Loader2 className="w-3 h-3 animate-spin shrink-0" />}
              {status}
            </p>
          </div>
        )}
      </div>

      {/* Preview modal */}
      {preview && createPortal(
        <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/30 backdrop-blur-sm">
          <div className="w-full max-w-lg mx-4 rounded-2xl bg-white border border-gray-200/80 shadow-2xl shadow-gray-900/10 animate-scale-in">
            {/* Header */}
            <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
              <div className="flex items-center gap-2">
                <Linkedin className="w-4 h-4 text-[#0a66c2]" />
                <h3 className="text-sm font-bold text-gray-900">Review LinkedIn Post</h3>
              </div>
              <button
                onClick={() => setPreview(null)}
                className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Body */}
            <div className="px-5 py-4 space-y-4 max-h-[60vh] overflow-auto">
              {preview.imageUrl && (
                <div className="rounded-xl overflow-hidden border border-gray-200/60">
                  <img
                    src={preview.imageUrl}
                    alt="Post image"
                    className="w-full h-40 object-cover"
                  />
                </div>
              )}

              <textarea
                value={preview.postText}
                onChange={(e) => setPreview({ ...preview, postText: e.target.value })}
                rows={10}
                className="w-full rounded-xl bg-gray-50 border border-gray-200/80 px-4 py-3 text-sm text-gray-800 leading-relaxed resize-y focus:outline-none focus:border-indigo-300 focus:ring-2 focus:ring-indigo-500/10 placeholder:text-gray-400"
              />

              <div className="flex items-center justify-between text-xs text-gray-400">
                <span>{wordCount(preview.postText)} words</span>
                {preview.hashtags.length > 0 && (
                  <div className="flex gap-1.5 flex-wrap justify-end">
                    {preview.hashtags.map((tag) => (
                      <span key={tag} className="px-2 py-0.5 rounded-md bg-blue-50 text-[#0a66c2] text-[10px] font-semibold">
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Footer */}
            <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-gray-100">
              {status && (
                <p className="text-xs text-red-500 mr-auto">{status}</p>
              )}
              <button
                onClick={() => setPreview(null)}
                disabled={publishing}
                className="px-4 py-2 rounded-xl text-sm font-medium text-gray-500 hover:text-gray-900 hover:bg-gray-50 border border-gray-200/60 transition-all duration-200 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handlePublish}
                disabled={publishing || !preview.postText.trim()}
                className="px-4 py-2 rounded-xl bg-gradient-to-r from-[#0a66c2] to-[#0077b5] hover:from-[#0077b5] hover:to-[#0a66c2] disabled:from-gray-200 disabled:to-gray-200 disabled:text-gray-400 text-sm font-medium text-white transition-all duration-300 flex items-center gap-1.5 shadow-sm shadow-blue-500/15"
              >
                {publishing ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Send className="w-3.5 h-3.5" />
                )}
                {publishing ? "Publishing..." : "Publish"}
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </>
  );
}
