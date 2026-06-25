import { useState, useEffect } from "react";
import { ExternalLink, Tag, Clock } from "lucide-react";

interface NewsPost {
  slug: string;
  title: string;
  excerpt: string;
  source_url: string;
  published_at: string;
  updated_at: string;
  tags: string[];
}

interface NewsResponse {
  posts: NewsPost[];
  count: number;
  limit: number;
  offset: number;
}

function formatDate(iso: string): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString("en-AU", {
      day: "numeric",
      month: "short",
      year: "numeric",
    });
  } catch {
    return iso.slice(0, 10);
  }
}

function timeAgo(iso: string): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const hours = Math.floor(diff / 3_600_000);
  if (hours < 1) return "just now";
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return formatDate(iso);
}

const POPULAR_TAGS = ["azure", "security", "entra", "ai", "updates", "microsoft365", "foundry"];

export default function NewsHub() {
  const [posts, setPosts] = useState<NewsPost[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTag, setActiveTag] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const limit = 20;

  const fetchPosts = async (tag: string | null, newOffset: number) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: String(limit), offset: String(newOffset) });
      if (tag) params.set("tag", tag);
      const res = await fetch(`/api/news?${params}`);
      const data: NewsResponse = await res.json();
      if (newOffset === 0) {
        setPosts(data.posts);
      } else {
        setPosts((prev) => [...prev, ...data.posts]);
      }
    } catch {
      // fail silently — empty state handles it
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPosts(activeTag, 0);
    setOffset(0);
  }, [activeTag]);

  const loadMore = () => {
    const next = offset + limit;
    fetchPosts(activeTag, next);
    setOffset(next);
  };

  return (
    <>
      {/* Inline SEO metadata via document head manipulation */}
      <title>Azure & Microsoft News Hub</title>

      <div className="min-h-screen bg-gray-50 dark:bg-gray-950 text-gray-900 dark:text-gray-100">
        {/* Simple public header */}
        <header className="border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 sticky top-0 z-10">
          <div className="max-w-4xl mx-auto px-4 py-4 flex items-center justify-between">
            <div>
              <h1 className="text-lg font-semibold text-gray-900 dark:text-white">
                Azure & Microsoft News
              </h1>
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                Daily updates on Azure, Entra ID, AI Foundry, and the Microsoft ecosystem
              </p>
            </div>
            <a
              href="/"
              className="text-sm text-indigo-600 dark:text-indigo-400 hover:underline"
            >
              ← Back to blog
            </a>
          </div>
        </header>

        <main className="max-w-4xl mx-auto px-4 py-8">
          {/* Tag filter strip */}
          <div className="flex gap-2 flex-wrap mb-6">
            <button
              onClick={() => setActiveTag(null)}
              className={`px-3 py-1 rounded-full text-sm font-medium transition-colors ${
                activeTag === null
                  ? "bg-indigo-600 text-white"
                  : "bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:border-indigo-400"
              }`}
            >
              All
            </button>
            {POPULAR_TAGS.map((tag) => (
              <button
                key={tag}
                onClick={() => setActiveTag(activeTag === tag ? null : tag)}
                className={`px-3 py-1 rounded-full text-sm font-medium transition-colors ${
                  activeTag === tag
                    ? "bg-indigo-600 text-white"
                    : "bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:border-indigo-400"
                }`}
              >
                {tag}
              </button>
            ))}
          </div>

          {/* Post list */}
          {loading && posts.length === 0 ? (
            <div className="space-y-4">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="bg-white dark:bg-gray-900 rounded-xl p-5 border border-gray-200 dark:border-gray-800 animate-pulse">
                  <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-3/4 mb-3" />
                  <div className="h-3 bg-gray-200 dark:bg-gray-700 rounded w-full mb-2" />
                  <div className="h-3 bg-gray-200 dark:bg-gray-700 rounded w-2/3" />
                </div>
              ))}
            </div>
          ) : posts.length === 0 ? (
            <div className="text-center py-16 text-gray-500 dark:text-gray-400">
              <p className="text-lg font-medium">No news posts yet</p>
              <p className="text-sm mt-1">Check back soon — the crawler runs hourly.</p>
            </div>
          ) : (
            <div className="space-y-4">
              {posts.map((post) => (
                <article
                  key={post.slug}
                  className="bg-white dark:bg-gray-900 rounded-xl p-5 border border-gray-200 dark:border-gray-800 hover:border-indigo-300 dark:hover:border-indigo-600 transition-colors"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1 min-w-0">
                      <a
                        href={`/blog/${post.slug}`}
                        className="block text-base font-semibold text-gray-900 dark:text-white hover:text-indigo-600 dark:hover:text-indigo-400 mb-1 leading-snug"
                      >
                        {post.title}
                      </a>
                      {post.excerpt && (
                        <p className="text-sm text-gray-600 dark:text-gray-400 line-clamp-2 mb-3">
                          {post.excerpt}
                        </p>
                      )}
                      <div className="flex items-center gap-3 flex-wrap">
                        <span className="flex items-center gap-1 text-xs text-gray-400 dark:text-gray-500">
                          <Clock size={12} />
                          {timeAgo(post.published_at)}
                        </span>
                        {post.tags.slice(0, 3).map((tag) => (
                          <button
                            key={tag}
                            onClick={() => setActiveTag(tag)}
                            className="flex items-center gap-1 text-xs text-indigo-600 dark:text-indigo-400 hover:underline"
                          >
                            <Tag size={10} />
                            {tag}
                          </button>
                        ))}
                        {post.source_url && (
                          <a
                            href={post.source_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="flex items-center gap-1 text-xs text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 ml-auto"
                          >
                            Source
                            <ExternalLink size={10} />
                          </a>
                        )}
                      </div>
                    </div>
                    <a
                      href={`/blog/${post.slug}`}
                      className="shrink-0 text-xs font-medium text-indigo-600 dark:text-indigo-400 border border-indigo-200 dark:border-indigo-700 rounded-lg px-3 py-1.5 hover:bg-indigo-50 dark:hover:bg-indigo-900/30 transition-colors"
                    >
                      Read
                    </a>
                  </div>
                </article>
              ))}
            </div>
          )}

          {/* Load more */}
          {!loading && posts.length >= limit + offset && (
            <div className="text-center mt-8">
              <button
                onClick={loadMore}
                className="px-6 py-2 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg text-sm font-medium text-gray-700 dark:text-gray-300 hover:border-indigo-400 transition-colors"
              >
                Load more
              </button>
            </div>
          )}
          {loading && posts.length > 0 && (
            <div className="text-center mt-6 text-sm text-gray-400">Loading...</div>
          )}
        </main>

        <footer className="border-t border-gray-200 dark:border-gray-800 mt-12 py-6 text-center text-xs text-gray-400 dark:text-gray-500">
          Updated hourly from official Microsoft and Azure sources.
        </footer>
      </div>
    </>
  );
}
