import { useState, useRef } from "react";
import { useBlogStore } from "../store/blogStore";
import { editBlogStream } from "../services/api";
import { Sparkles, Send, X, Loader2 } from "lucide-react";

interface Props {
  onClose: () => void;
}

const QUICK_PROMPTS = [
  "Make the tone more casual and friendly",
  "Add a conclusion section",
  "Improve the introduction paragraph",
  "Add code examples where appropriate",
  "Make it more concise",
  "Add SEO-friendly headings",
  "Fix grammar and spelling",
  "Add a table of contents",
];

export default function AIEditPanel({ onClose }: Props) {
  const { content, setContent, setError, setPhase } = useBlogStore();
  const [prompt, setPrompt] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamPreview, setStreamPreview] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  const handleEdit = (editPrompt: string) => {
    if (!editPrompt.trim() || !content.trim()) return;

    setStreaming(true);
    setStreamPreview("");
    setPhase("editing");

    const controller = editBlogStream(content, editPrompt.trim(), {
      onChunk: (chunk) => {
        setStreamPreview((prev) => prev + chunk);
      },
      onComplete: (data) => {
        const result = data as { content: string };
        setContent(result.content);
        setStreamPreview("");
        setStreaming(false);
        setPhase("idle");
        setPrompt("");
      },
      onError: (err) => {
        setError(err);
        setStreaming(false);
        setPhase("idle");
      },
    });

    abortRef.current = controller;
  };

  const handleCancel = () => {
    abortRef.current?.abort();
    setStreaming(false);
    setStreamPreview("");
    setPhase("idle");
  };

  return (
    <div className="h-full flex flex-col bg-[var(--bg-base)]">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200/60">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg bg-indigo-50 border border-indigo-200/60 flex items-center justify-center">
            <Sparkles className="w-3.5 h-3.5 text-indigo-500" />
          </div>
          <span className="text-sm font-bold text-gray-900">AI Editor</span>
        </div>
        <button
          onClick={onClose}
          className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-all duration-200"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Quick prompts */}
      <div className="px-4 py-3 border-b border-gray-200/60 overflow-y-auto max-h-48">
        <p className="text-[11px] text-gray-400 mb-2.5 uppercase tracking-wider font-semibold">Quick Actions</p>
        <div className="flex flex-wrap gap-1.5">
          {QUICK_PROMPTS.map((qp, i) => (
            <button
              key={qp}
              onClick={() => handleEdit(qp)}
              disabled={streaming}
              className="px-3 py-1.5 rounded-full bg-gray-50 border border-gray-200/60 text-xs font-medium text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 hover:border-indigo-200/60 transition-all duration-200 disabled:opacity-30 disabled:cursor-not-allowed animate-fade-in-up"
              style={{ animationDelay: `${i * 40}ms` }}
            >
              {qp}
            </button>
          ))}
        </div>
      </div>

      {/* Streaming preview */}
      {streaming && streamPreview && (
        <div className="px-4 py-3 border-b border-gray-200/60 max-h-40 overflow-y-auto animate-fade-in">
          <p className="text-xs text-indigo-600 mb-2 flex items-center gap-1.5 font-semibold">
            <Loader2 className="w-3 h-3 animate-spin" />
            AI is editing...
          </p>
          <pre className="text-xs text-gray-600 whitespace-pre-wrap font-mono leading-relaxed bg-gray-50 rounded-lg p-3 border border-gray-200/60">
            {streamPreview.slice(0, 500)}
            {streamPreview.length > 500 && "..."}
          </pre>
          <button
            onClick={handleCancel}
            className="mt-2 text-xs text-red-500 hover:text-red-600 font-semibold underline underline-offset-2 transition-colors"
          >
            Cancel
          </button>
        </div>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* Custom prompt input */}
      <div className="px-4 py-3 border-t border-gray-200/60">
        <div className="flex gap-2">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleEdit(prompt);
              }
            }}
            placeholder="Describe how to modify the blog..."
            disabled={streaming}
            rows={2}
            className="flex-1 px-3.5 py-2.5 rounded-xl bg-white border border-gray-200/80 focus:border-indigo-300 focus:ring-2 focus:ring-indigo-500/10 outline-none text-sm text-gray-900 placeholder-gray-400 resize-none disabled:opacity-40 transition-all duration-200"
          />
          <button
            onClick={() => handleEdit(prompt)}
            disabled={streaming || !prompt.trim()}
            className="self-end p-2.5 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 disabled:from-gray-200 disabled:to-gray-200 disabled:text-gray-400 text-white transition-all duration-300 shadow-sm shadow-indigo-500/20 disabled:shadow-none"
          >
            {streaming ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Send className="w-4 h-4" />
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
