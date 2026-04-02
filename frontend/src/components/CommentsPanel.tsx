import { useEffect, useState } from "react";
import { listComments, createComment, updateComment, deleteComment } from "../services/api";
import type { Comment } from "../services/api";
import { toast } from "../store/toastStore";
import {
  MessageSquare,
  Send,
  Trash2,
  CheckCircle2,
  Circle,
  Loader2,
  X,
  Reply,
} from "lucide-react";

interface Props {
  draftId: string;
  onClose: () => void;
}

export default function CommentsPanel({ draftId, onClose }: Props) {
  const [comments, setComments] = useState<Comment[]>([]);
  const [loading, setLoading] = useState(true);
  const [newComment, setNewComment] = useState("");
  const [lineNumber, setLineNumber] = useState("");
  const [replyTo, setReplyTo] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    listComments(draftId)
      .then(setComments)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [draftId]);

  const handleSubmit = async () => {
    if (!newComment.trim()) return;
    setSending(true);
    try {
      const comment = await createComment({
        draft_id: draftId,
        content: newComment.trim(),
        line_number: lineNumber ? parseInt(lineNumber, 10) : undefined,
        parent_id: replyTo || undefined,
      });
      setComments([comment, ...comments]);
      setNewComment("");
      setLineNumber("");
      setReplyTo(null);
      toast.success("Comment added");
    } catch {
      toast.error("Failed to add comment");
    }
    setSending(false);
  };

  const handleResolve = async (id: string, resolved: boolean) => {
    try {
      const updated = await updateComment(id, { resolved: !resolved });
      setComments(comments.map((c) => (c.id === id ? updated : c)));
    } catch {
      toast.error("Failed to update");
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteComment(id);
      setComments(comments.filter((c) => c.id !== id));
      toast.success("Comment deleted");
    } catch {
      toast.error("Failed to delete");
    }
  };

  const topLevel = comments.filter((c) => !c.parentId);
  const getReplies = (parentId: string) =>
    comments.filter((c) => c.parentId === parentId);

  return (
    <div className="w-80 h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200/60">
        <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
          <MessageSquare className="w-4 h-4 text-indigo-500" />
          Comments
          {comments.length > 0 && (
            <span className="text-[10px] font-semibold text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">
              {comments.length}
            </span>
          )}
        </h3>
        <button
          onClick={onClose}
          className="p-1 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-all"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* New comment form */}
      <div className="px-4 py-3 border-b border-gray-100 space-y-2">
        {replyTo && (
          <div className="flex items-center gap-2 text-[10px] text-indigo-500 font-medium">
            <Reply className="w-3 h-3" />
            Replying to comment
            <button onClick={() => setReplyTo(null)} className="text-gray-400 hover:text-gray-600">
              <X className="w-3 h-3" />
            </button>
          </div>
        )}
        <textarea
          value={newComment}
          onChange={(e) => setNewComment(e.target.value)}
          placeholder="Add a comment..."
          rows={2}
          className="w-full px-3 py-2 rounded-lg bg-gray-50 border border-gray-200/60 text-xs outline-none focus:border-indigo-300 resize-none"
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) handleSubmit();
          }}
        />
        <div className="flex items-center gap-2">
          <input
            value={lineNumber}
            onChange={(e) => setLineNumber(e.target.value.replace(/\D/g, ""))}
            placeholder="Line #"
            className="w-16 px-2 py-1 rounded-lg bg-gray-50 border border-gray-200/60 text-[10px] outline-none focus:border-indigo-300"
          />
          <div className="flex-1" />
          <button
            onClick={handleSubmit}
            disabled={sending || !newComment.trim()}
            className="inline-flex items-center gap-1 px-3 py-1 rounded-lg text-[10px] font-semibold text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 transition-all"
          >
            {sending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}
            Send
          </button>
        </div>
      </div>

      {/* Comments list */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {loading ? (
          <div className="flex justify-center py-8">
            <Loader2 className="w-5 h-5 animate-spin text-indigo-500" />
          </div>
        ) : topLevel.length === 0 ? (
          <p className="text-xs text-gray-400 text-center py-8">No comments yet.</p>
        ) : (
          topLevel.map((c) => (
            <CommentItem
              key={c.id}
              comment={c}
              replies={getReplies(c.id)}
              onResolve={handleResolve}
              onDelete={handleDelete}
              onReply={(id) => setReplyTo(id)}
            />
          ))
        )}
      </div>
    </div>
  );
}

function CommentItem({
  comment,
  replies,
  onResolve,
  onDelete,
  onReply,
}: {
  comment: Comment;
  replies: Comment[];
  onResolve: (id: string, resolved: boolean) => void;
  onDelete: (id: string) => void;
  onReply: (id: string) => void;
}) {
  return (
    <div className={`rounded-xl border p-3 transition-all ${comment.resolved ? "border-emerald-200/60 bg-emerald-50/30" : "border-gray-200/60 bg-white"}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[11px] font-semibold text-gray-700">{comment.userName}</span>
            {comment.lineNumber && (
              <span className="text-[9px] font-mono text-indigo-500 bg-indigo-50 px-1.5 py-0.5 rounded">
                L{comment.lineNumber}
              </span>
            )}
            <span className="text-[9px] text-gray-400">
              {new Date(comment.createdAt).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
            </span>
          </div>
          <p className="text-xs text-gray-600 leading-relaxed whitespace-pre-wrap">{comment.content}</p>
        </div>
        <div className="flex items-center gap-0.5 shrink-0">
          <button
            onClick={() => onResolve(comment.id, comment.resolved)}
            className={`p-1 rounded-lg transition-all ${comment.resolved ? "text-emerald-500 hover:text-emerald-600" : "text-gray-300 hover:text-emerald-500"}`}
            title={comment.resolved ? "Unresolve" : "Resolve"}
          >
            {comment.resolved ? <CheckCircle2 className="w-3.5 h-3.5" /> : <Circle className="w-3.5 h-3.5" />}
          </button>
          <button
            onClick={() => onReply(comment.id)}
            className="p-1 rounded-lg text-gray-300 hover:text-indigo-500 transition-all"
            title="Reply"
          >
            <Reply className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => onDelete(comment.id)}
            className="p-1 rounded-lg text-gray-300 hover:text-red-500 transition-all"
            title="Delete"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Replies */}
      {replies.length > 0 && (
        <div className="mt-2 ml-3 pl-3 border-l-2 border-gray-100 space-y-2">
          {replies.map((r) => (
            <div key={r.id} className="flex items-start justify-between gap-2">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-[10px] font-semibold text-gray-600">{r.userName}</span>
                  <span className="text-[9px] text-gray-400">
                    {new Date(r.createdAt).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                  </span>
                </div>
                <p className="text-[11px] text-gray-500 leading-relaxed">{r.content}</p>
              </div>
              <button
                onClick={() => onDelete(r.id)}
                className="p-1 rounded-lg text-gray-300 hover:text-red-500 transition-all shrink-0"
              >
                <Trash2 className="w-3 h-3" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
