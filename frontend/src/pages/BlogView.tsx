import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { getPublishedBlog } from "../services/api";
import { ArrowLeft, Loader2, ExternalLink, Sparkles } from "lucide-react";

interface BlogData {
  title: string;
  excerpt: string;
  html_content: string;
  source_url: string;
  source_type: string;
  tags: string[];
  date: string;
  published_at: string;
}

export default function BlogView() {
  const { slug } = useParams<{ slug: string }>();
  const [blog, setBlog] = useState<BlogData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!slug) return;
    setLoading(true);
    getPublishedBlog(slug)
      .then(setBlog)
      .catch((err) => setError(err instanceof Error ? err.message : "Blog not found"))
      .finally(() => setLoading(false));
  }, [slug]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--bg-base)]">
        <Loader2 className="w-6 h-6 animate-spin text-indigo-500" />
      </div>
    );
  }

  if (error || !blog) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-[var(--bg-base)] gap-4">
        <p className="text-lg text-gray-500">{error ?? "Blog not found"}</p>
        <Link to="/" className="text-indigo-600 hover:text-indigo-700 font-semibold flex items-center gap-1.5 transition-colors">
          <ArrowLeft className="w-4 h-4" /> Back to Home
        </Link>
      </div>
    );
  }

  const displayDate = blog.date || blog.published_at;

  return (
    <div className="min-h-screen bg-[var(--bg-base)]">
      {/* Nav */}
      <nav className="glass-strong border-b border-indigo-100/60 sticky top-0 z-40">
        <div className="max-w-3xl mx-auto px-6 py-3 flex items-center justify-between">
          <Link
            to="/"
            className="flex items-center gap-2 text-sm text-gray-400 hover:text-indigo-600 font-medium transition-colors"
          >
            <ArrowLeft className="w-4 h-4" /> Back
          </Link>
          <Link to="/" className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-indigo-500 to-violet-500 flex items-center justify-center shadow-sm">
              <Sparkles className="w-3.5 h-3.5 text-white" />
            </div>
            <span className="text-sm font-bold text-gray-900">Blog Writer</span>
          </Link>
        </div>
      </nav>

      <div className="max-w-3xl mx-auto px-6 py-12">
        {/* Meta bar */}
        <div className="flex flex-wrap items-center gap-3 mb-6 text-sm text-gray-400">
          {displayDate && (
            <span className="font-medium">
              {new Date(displayDate).toLocaleDateString("en-US", {
                year: "numeric",
                month: "long",
                day: "numeric",
              })}
            </span>
          )}

          {blog.source_url && (
            <>
              <span className="text-gray-200">|</span>
              <a
                href={blog.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-indigo-500 hover:text-indigo-600 font-medium transition-colors"
              >
                <ExternalLink className="w-3.5 h-3.5" />
                Original Source
              </a>
            </>
          )}
        </div>

        {blog.tags.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-8">
            {blog.tags.map((tag) => (
              <span
                key={tag}
                className="px-3 py-1 rounded-full text-xs font-semibold bg-indigo-50 text-indigo-600 border border-indigo-200/60"
              >
                {tag}
              </span>
            ))}
          </div>
        )}

        <article
          className="prose prose-lg prose-slate max-w-none prose-headings:text-gray-900 prose-headings:font-bold prose-a:text-indigo-600 prose-code:text-indigo-600 prose-code:bg-indigo-50 prose-pre:bg-gray-50 prose-pre:border prose-pre:border-gray-200/60 prose-pre:rounded-xl prose-blockquote:border-indigo-400 prose-img:rounded-xl prose-img:shadow-md"
          dangerouslySetInnerHTML={{ __html: blog.html_content }}
        />

        {/* Footer */}
        {blog.source_url && (
          <div className="mt-16 pt-6 border-t border-gray-200">
            <p className="text-sm text-gray-400">
              Generated from{" "}
              <a
                href={blog.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-indigo-500 hover:text-indigo-600 font-medium hover:underline"
              >
                {blog.source_url}
              </a>
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
