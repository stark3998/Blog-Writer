import { useState } from "react";
import {
  composeLinkedInPost,
  startLinkedInOAuth,
  getLinkedInStatus,
} from "../services/api";
import { Loader2, Linkedin } from "lucide-react";

interface Props {
  content: string;
  title?: string;
  excerpt?: string;
}

const SESSION_KEY = "linkedin_session_id";

function getStoredSession(): string | null {
  return localStorage.getItem(SESSION_KEY);
}

function storeSession(id: string) {
  localStorage.setItem(SESSION_KEY, id);
}

function openLinkedInComposer(text: string) {
  const url = `https://www.linkedin.com/feed/?shareActive=true&text=${encodeURIComponent(text)}`;
  window.open(url, "_blank", "noopener,noreferrer");
}

export default function LinkedInButton({ content, title, excerpt }: Props) {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");

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

        // Open OAuth popup and wait for completion
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

          // Also poll in case postMessage doesn't work (popup on different origin)
          const pollTimer = setInterval(() => {
            if (popup && popup.closed) {
              clearInterval(pollTimer);
              window.removeEventListener("message", handleMessage);
              // Check if OAuth completed by rechecking status
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

          // Timeout after 2 minutes
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

      // Step 4: Open LinkedIn with the composed post
      openLinkedInComposer(composed.post_text);
      setStatus("");
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Something went wrong");
      setTimeout(() => setStatus(""), 3000);
    } finally {
      setBusy(false);
    }
  };

  return (
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
      {status && (
        <div className="absolute right-0 top-full mt-2 w-64 px-3 py-2 rounded-lg bg-[#131a2e] border border-white/[0.08] shadow-2xl shadow-black/40 z-50 animate-scale-in">
          <p className="text-xs text-slate-300 flex items-center gap-2">
            {busy && <Loader2 className="w-3 h-3 animate-spin shrink-0" />}
            {status}
          </p>
        </div>
      )}
    </div>
  );
}
