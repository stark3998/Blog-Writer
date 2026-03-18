import { useState } from "react";
import { createPortal } from "react-dom";
import {
  composeTwitterPost,
  publishTwitterPost,
  startTwitterOAuth,
  getTwitterStatus,
} from "../services/api";
import { Loader2, X, Send, Twitter } from "lucide-react";

interface Props {
  content: string;
  title?: string;
  excerpt?: string;
  blogUrl?: string;
}

interface PreviewData {
  tweetText: string;
  hashtags: string[];
  charCount: number;
}

const SESSION_KEY = "twitter_session_id";
const TWEET_CHAR_LIMIT = 280;

function getStoredSession(): string | null {
  return localStorage.getItem(SESSION_KEY);
}

function storeSession(id: string) {
  localStorage.setItem(SESSION_KEY, id);
}

export default function TwitterButton({ content, title, excerpt, blogUrl }: Props) {
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
          const res = await getTwitterStatus(sessionId);
          isConnected = res.connected;
        } catch {
          isConnected = false;
        }
      }

      if (!isConnected) {
        setStatus("Connecting to Twitter/X...");
        const oauthRes = await startTwitterOAuth(sessionId ?? undefined);
        storeSession(oauthRes.session_id);

        const popup = window.open(oauthRes.auth_url, "twitter-oauth", "width=600,height=700");

        await new Promise<void>((resolve, reject) => {
          const handleMessage = (e: MessageEvent) => {
            if (e.data?.type === "twitter-oauth-callback" && e.data.session_id) {
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
                getTwitterStatus(sid)
                  .then((res) => {
                    if (res.connected) resolve();
                    else reject(new Error("Twitter authentication was cancelled"));
                  })
                  .catch(() => reject(new Error("Twitter authentication was cancelled")));
              } else {
                reject(new Error("Twitter authentication was cancelled"));
              }
            }
          }, 500);

          setTimeout(() => {
            clearInterval(pollTimer);
            window.removeEventListener("message", handleMessage);
            reject(new Error("Twitter authentication timed out"));
          }, 120_000);
        });
      }

      setStatus("AI is composing your tweet...");
      const composed = await composeTwitterPost({
        content,
        title,
        excerpt,
        blog_url: blogUrl || undefined,
      });

      setPreview({
        tweetText: composed.tweet_text,
        hashtags: composed.hashtags,
        charCount: composed.char_count,
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
      if (!sid) throw new Error("No Twitter session found");

      const result = await publishTwitterPost({
        session_id: sid,
        tweet_text: preview.tweetText,
      });

      if (result.tweet_id) {
        window.open(
          `https://twitter.com/i/web/status/${result.tweet_id}`,
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

  const charCount = preview?.tweetText.length ?? 0;
  const isOverLimit = charCount > TWEET_CHAR_LIMIT;

  return (
    <>
      <div className="relative">
        <button
          onClick={handleClick}
          disabled={busy || !content.trim()}
          className="px-3 py-1.5 rounded-xl text-sm font-medium transition-all duration-200 flex items-center gap-1.5 disabled:opacity-30 border border-gray-200/60 text-gray-500 hover:text-black hover:bg-gray-50 hover:border-gray-300/60"
          title="Post to Twitter/X"
        >
          {busy ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Twitter className="w-3.5 h-3.5" />
          )}
          Tweet
        </button>

        {status && !preview && (
          <div className="absolute right-0 top-full mt-2 w-64 px-3 py-2 rounded-xl bg-white border border-gray-200/80 shadow-xl shadow-gray-900/5 z-50 animate-scale-in">
            <p className="text-xs text-gray-600 flex items-center gap-2">
              {busy && <Loader2 className="w-3 h-3 animate-spin shrink-0" />}
              {status}
            </p>
          </div>
        )}
      </div>

      {preview && createPortal(
        <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/30 backdrop-blur-sm">
          <div className="w-full max-w-lg mx-4 rounded-2xl bg-white border border-gray-200/80 shadow-2xl shadow-gray-900/10 animate-scale-in">
            {/* Header */}
            <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
              <div className="flex items-center gap-2">
                <Twitter className="w-4 h-4 text-black" />
                <h3 className="text-sm font-bold text-gray-900">Review Tweet</h3>
              </div>
              <button
                onClick={() => setPreview(null)}
                className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Body */}
            <div className="px-5 py-4 space-y-4">
              <textarea
                value={preview.tweetText}
                onChange={(e) =>
                  setPreview({
                    ...preview,
                    tweetText: e.target.value,
                    charCount: e.target.value.length,
                  })
                }
                rows={6}
                className="w-full rounded-xl bg-gray-50 border border-gray-200/80 px-4 py-3 text-sm text-gray-800 leading-relaxed resize-y focus:outline-none focus:border-gray-400 focus:ring-2 focus:ring-gray-500/10 placeholder:text-gray-400"
              />

              <div className="flex items-center justify-between text-xs">
                <span className={isOverLimit ? "text-red-500 font-semibold" : "text-gray-400"}>
                  {preview.tweetText.length}/{TWEET_CHAR_LIMIT}
                </span>
                {preview.hashtags.length > 0 && (
                  <div className="flex gap-1.5 flex-wrap justify-end">
                    {preview.hashtags.map((tag) => (
                      <span key={tag} className="px-2 py-0.5 rounded-md bg-gray-100 text-gray-600 text-[10px] font-semibold">
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
                disabled={publishing || !preview.tweetText.trim() || isOverLimit}
                className="px-4 py-2 rounded-xl bg-black hover:bg-gray-800 disabled:bg-gray-200 disabled:text-gray-400 text-sm font-medium text-white transition-all duration-300 flex items-center gap-1.5 shadow-sm"
              >
                {publishing ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Send className="w-3.5 h-3.5" />
                )}
                {publishing ? "Posting..." : "Post"}
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </>
  );
}
