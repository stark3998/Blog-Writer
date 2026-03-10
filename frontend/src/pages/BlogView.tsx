import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { getPublishedBlog } from "../services/api";
import { ArrowLeft, Loader2, ExternalLink } from "lucide-react";

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
      <div className="min-h-screen flex items-center justify-center bg-white">
        <Loader2 className="w-6 h-6 animate-spin text-indigo-500" />
      </div>
    );
  }

  if (error || !blog) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-white gap-4">
        <p className="text-lg text-gray-600">{error ?? "Blog not found"}</p>
        <Link to="/" className="text-indigo-600 hover:underline flex items-center gap-1">
          <ArrowLeft className="w-4 h-4" /> Back to Home
        </Link>
      </div>
    );
  }

  const displayDate = blog.date || blog.published_at;

  return (
    <div className="min-h-screen bg-white">
      <div className="max-w-3xl mx-auto px-6 py-8">
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-indigo-600 mb-8 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" /> Back
        </Link>

        {/* Meta bar: date, source, tags */}
        <div className="flex flex-wrap items-center gap-3 mb-6 text-sm text-gray-400">
          {displayDate && (
            <span>
              {new Date(displayDate).toLocaleDateString("en-US", {
                year: "numeric",
                month: "long",
                day: "numeric",
              })}
            </span>
          )}

          {blog.source_url && (
            <>
              <span className="text-gray-300">|</span>
              <a
                href={blog.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-indigo-500 hover:text-indigo-600 transition-colors"
              >
                <ExternalLink className="w-3.5 h-3.5" />
                Original Source
              </a>
            </>
          )}
        </div>

        {blog.tags.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-6">
            {blog.tags.map((tag) => (
              <span
                key={tag}
                className="px-2.5 py-0.5 rounded-full text-xs font-medium bg-indigo-50 text-indigo-600 border border-indigo-100"
              >
                {tag}
              </span>
            ))}
          </div>
        )}

        <article
          className="prose prose-lg max-w-none"
          dangerouslySetInnerHTML={{ __html: blog.html_content }}
        />

        {/* Footer with source attribution */}
        {blog.source_url && (
          <div className="mt-12 pt-6 border-t border-gray-200">
            <p className="text-sm text-gray-400">
              Generated from{" "}
              <a
                href={blog.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-indigo-500 hover:underline"
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
