import { useState } from "react";
import { createPortal } from "react-dom";
import {
  composeLinkedInPost,
  publishLinkedInPost,
  startLinkedInOAuth,
  getLinkedInStatus,
} from "../services/api";
import { Loader2, Linkedin, X, Send } from "lucide-react";

interface Props {
  content: string;
  title?: string;
  excerpt?: string;
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

export default function LinkedInButton({ content, title, excerpt }: Props) {
  const [busy, setBusy] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [status, setStatus] = useState("");
  const [preview, setPreview] = useState<PreviewData | null>(null);

  const handleClick = async () => {
    if (!content.trim()) return;
    setBusy(true);
    setStatus("");

    try {
      // Step 1: Check if we have a valid LinkedIn session
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

      // Step 2: If not connected, start OAuth and wait for callback
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

      // Step 3: Compose the post via AI
      setStatus("AI is composing your post...");
      const composed = await composeLinkedInPost({
        content,
        title,
        excerpt,
        post_format: "feed_post",
      });

      // Step 4: Show preview modal instead of auto-publishing
      setPreview({
        postText: composed.post_text,
        imageUrl: composed.image_url,
        hashtags: composed.hashtags,
      });
      setStatus("");
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Something went wrong");
      setTimeout(() => setStatus(""), 3000);
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

      // Open the published post on LinkedIn
      if (result.post_id) {
        const activityId = result.post_id.replace("urn:li:share:", "");
        window.open(
          `https://www.linkedin.com/feed/update/urn:li:share:${activityId}/`,
          "_blank",
          "noopener,noreferrer"
        );
      }
      setPreview(null);
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
          className="px-3 py-1.5 rounded-lg text-sm font-medium transition-all duration-200 flex items-center gap-1.5 disabled:opacity-30 border border-white/[0.06] text-slate-400 hover:text-[#0a66c2] hover:bg-[#0a66c2]/[0.06] hover:border-[#0a66c2]/20"
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
          <div className="absolute right-0 top-full mt-2 w-64 px-3 py-2 rounded-lg bg-[#131a2e] border border-white/[0.08] shadow-2xl shadow-black/40 z-50 animate-scale-in">
            <p className="text-xs text-slate-300 flex items-center gap-2">
              {busy && <Loader2 className="w-3 h-3 animate-spin shrink-0" />}
              {status}
            </p>
          </div>
        )}
      </div>

      {/* Preview modal — portaled to body to escape toolbar stacking context */}
      {preview && createPortal(
        <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="w-full max-w-lg mx-4 rounded-2xl bg-[#131a2e] border border-white/[0.08] shadow-2xl shadow-black/60 animate-scale-in">
            {/* Header */}
            <div className="flex items-center justify-between px-5 py-4 border-b border-white/[0.06]">
              <div className="flex items-center gap-2">
                <Linkedin className="w-4 h-4 text-[#0a66c2]" />
                <h3 className="text-sm font-semibold text-white">Review LinkedIn Post</h3>
              </div>
              <button
                onClick={() => setPreview(null)}
                className="p-1.5 rounded-lg text-slate-500 hover:text-white hover:bg-white/[0.06] transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Body */}
            <div className="px-5 py-4 space-y-4 max-h-[60vh] overflow-auto">
              {/* Image preview */}
              {preview.imageUrl && (
                <div className="rounded-lg overflow-hidden border border-white/[0.06]">
                  <img
                    src={preview.imageUrl}
                    alt="Post image"
                    className="w-full h-40 object-cover"
                  />
                </div>
              )}

              {/* Editable post text */}
              <textarea
                value={preview.postText}
                onChange={(e) => setPreview({ ...preview, postText: e.target.value })}
                rows={10}
                className="w-full rounded-lg bg-white/[0.04] border border-white/[0.08] px-4 py-3 text-sm text-slate-200 leading-relaxed resize-y focus:outline-none focus:border-indigo-500/40 focus:ring-1 focus:ring-indigo-500/20 placeholder:text-slate-600"
              />

              {/* Meta info */}
              <div className="flex items-center justify-between text-xs text-slate-500">
                <span>{wordCount(preview.postText)} words</span>
                {preview.hashtags.length > 0 && (
                  <div className="flex gap-1.5 flex-wrap justify-end">
                    {preview.hashtags.map((tag) => (
                      <span key={tag} className="px-2 py-0.5 rounded-md bg-[#0a66c2]/10 text-[#0a66c2] text-[10px] font-medium">
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Footer */}
            <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-white/[0.06]">
              {status && (
                <p className="text-xs text-red-400 mr-auto">{status}</p>
              )}
              <button
                onClick={() => setPreview(null)}
                disabled={publishing}
                className="px-4 py-2 rounded-lg text-sm font-medium text-slate-400 hover:text-white hover:bg-white/[0.04] border border-white/[0.06] transition-all duration-200 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handlePublish}
                disabled={publishing || !preview.postText.trim()}
                className="px-4 py-2 rounded-lg bg-gradient-to-r from-[#0a66c2] to-[#0077b5] hover:from-[#0077b5] hover:to-[#0a66c2] disabled:from-slate-700 disabled:to-slate-700 disabled:text-slate-500 text-sm font-medium text-white transition-all duration-300 flex items-center gap-1.5 shadow-sm shadow-[#0a66c2]/15"
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
