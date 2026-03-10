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
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700/50">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-indigo-400" />
          <span className="text-sm font-semibold">AI Editor</span>
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-slate-700 text-slate-400 hover:text-white transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Quick prompts */}
      <div className="px-4 py-3 border-b border-slate-700/50 overflow-y-auto max-h-48">
        <p className="text-xs text-slate-500 mb-2 uppercase tracking-wider">Quick Actions</p>
        <div className="flex flex-wrap gap-1.5">
          {QUICK_PROMPTS.map((qp) => (
            <button
              key={qp}
              onClick={() => handleEdit(qp)}
              disabled={streaming}
              className="px-2.5 py-1 rounded-full bg-slate-700/50 hover:bg-slate-700 text-xs text-slate-300 hover:text-white transition-colors disabled:opacity-50"
            >
              {qp}
            </button>
          ))}
        </div>
      </div>

      {/* Streaming preview */}
      {streaming && streamPreview && (
        <div className="px-4 py-3 border-b border-slate-700/50 max-h-40 overflow-y-auto">
          <p className="text-xs text-indigo-400 mb-1 flex items-center gap-1">
            <Loader2 className="w-3 h-3 animate-spin" />
            AI is editing...
          </p>
          <pre className="text-xs text-slate-400 whitespace-pre-wrap font-mono leading-relaxed">
            {streamPreview.slice(0, 500)}
            {streamPreview.length > 500 && "..."}
          </pre>
          <button
            onClick={handleCancel}
            className="mt-2 text-xs text-red-400 hover:text-red-300 underline"
          >
            Cancel
          </button>
        </div>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* Custom prompt input */}
      <div className="px-4 py-3 border-t border-slate-700/50">
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
            className="flex-1 px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none text-sm text-white placeholder-slate-500 resize-none disabled:opacity-50"
          />
          <button
            onClick={() => handleEdit(prompt)}
            disabled={streaming || !prompt.trim()}
            className="self-end px-3 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-700 disabled:text-slate-500 transition-colors"
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
