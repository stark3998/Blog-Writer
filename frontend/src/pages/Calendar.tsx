import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listDrafts, listPublishedBlogs, listRelevantArticles } from "../services/api";
import type { BlogDraft, PublishedBlog, CrawledArticle } from "../types";
import {
  Calendar as CalendarIcon,
  ChevronLeft,
  ChevronRight,
  FileText,
  BookOpen,
  TrendingUp,
  Loader2,
} from "lucide-react";

interface CalendarItem {
  id: string;
  title: string;
  type: "draft" | "published" | "article";
  date: string; // YYYY-MM-DD
  draftId?: string;
  slug?: string;
}

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

export default function ContentCalendar() {
  const navigate = useNavigate();
  const [drafts, setDrafts] = useState<BlogDraft[]>([]);
  const [published, setPublished] = useState<PublishedBlog[]>([]);
  const [articles, setArticles] = useState<CrawledArticle[]>([]);
  const [loading, setLoading] = useState(true);
  const [currentDate, setCurrentDate] = useState(new Date());

  useEffect(() => {
    setLoading(true);
    Promise.all([
      listDrafts(200),
      listPublishedBlogs(200),
      listRelevantArticles(100),
    ]).then(([d, p, a]) => {
      setDrafts(d);
      setPublished(p);
      setArticles(a);
    }).catch(() => {})
    .finally(() => setLoading(false));
  }, []);

  const items = useMemo(() => {
    const all: CalendarItem[] = [];
    drafts.forEach((d) => {
      const date = d.updatedAt?.slice(0, 10) || d.createdAt?.slice(0, 10);
      if (date) all.push({ id: d.id, title: d.title, type: "draft", date, draftId: d.id });
    });
    published.forEach((p) => {
      const date = p.publishedAt?.slice(0, 10);
      if (date) all.push({ id: `pub-${p.id}`, title: p.title, type: "published", date, slug: p.slug });
    });
    articles.forEach((a) => {
      const date = a.crawled_at?.slice(0, 10);
      if (date && a.is_relevant && !a.draft_id) {
        all.push({ id: `art-${a.id}`, title: a.title, type: "article", date });
      }
    });
    return all;
  }, [drafts, published, articles]);

  const year = currentDate.getFullYear();
  const month = currentDate.getMonth();

  const firstDay = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const today = new Date().toISOString().slice(0, 10);

  const calendarDays = useMemo(() => {
    const days: (number | null)[] = [];
    for (let i = 0; i < firstDay; i++) days.push(null);
    for (let i = 1; i <= daysInMonth; i++) days.push(i);
    return days;
  }, [firstDay, daysInMonth]);

  const getItemsForDay = (day: number) => {
    const dateStr = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    return items.filter((item) => item.date === dateStr);
  };

  const prevMonth = () => setCurrentDate(new Date(year, month - 1, 1));
  const nextMonth = () => setCurrentDate(new Date(year, month + 1, 1));
  const goToday = () => setCurrentDate(new Date());

  const handleItemClick = (item: CalendarItem) => {
    if (item.type === "draft" && item.draftId) navigate(`/editor/${item.draftId}`);
    if (item.type === "published" && item.slug) navigate(`/blog/${item.slug}`);
  };

  const typeStyles: Record<string, string> = {
    draft: "bg-cyan-50 text-cyan-700 border-cyan-200/60",
    published: "bg-emerald-50 text-emerald-700 border-emerald-200/60",
    article: "bg-amber-50 text-amber-700 border-amber-200/60",
  };

  const typeIcons: Record<string, typeof FileText> = {
    draft: FileText,
    published: BookOpen,
    article: TrendingUp,
  };

  // Count items per type for this month
  const monthStr = `${year}-${String(month + 1).padStart(2, "0")}`;
  const monthItems = items.filter((i) => i.date.startsWith(monthStr));
  const draftCount = monthItems.filter((i) => i.type === "draft").length;
  const pubCount = monthItems.filter((i) => i.type === "published").length;
  const artCount = monthItems.filter((i) => i.type === "article").length;

  if (loading) {
    return (
      <div className="flex justify-center py-20">
        <Loader2 className="w-6 h-6 animate-spin text-indigo-500" />
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold text-gray-900 flex items-center gap-3">
            <CalendarIcon className="w-6 h-6 text-indigo-500" />
            Content Calendar
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            Visualize your content pipeline across time
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* Legend */}
          <div className="flex items-center gap-3 mr-4">
            <div className="flex items-center gap-1.5">
              <div className="w-2.5 h-2.5 rounded bg-cyan-400" />
              <span className="text-[10px] text-gray-500 font-medium">Drafts ({draftCount})</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-2.5 h-2.5 rounded bg-emerald-400" />
              <span className="text-[10px] text-gray-500 font-medium">Published ({pubCount})</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-2.5 h-2.5 rounded bg-amber-400" />
              <span className="text-[10px] text-gray-500 font-medium">Queued ({artCount})</span>
            </div>
          </div>

          <button onClick={prevMonth} className="p-2 rounded-xl text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 border border-gray-200/60 transition-all">
            <ChevronLeft className="w-4 h-4" />
          </button>
          <button onClick={goToday} className="px-3 py-1.5 rounded-xl text-xs font-semibold text-indigo-600 bg-indigo-50 border border-indigo-200/60 hover:bg-indigo-100 transition-all">
            Today
          </button>
          <button onClick={nextMonth} className="p-2 rounded-xl text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 border border-gray-200/60 transition-all">
            <ChevronRight className="w-4 h-4" />
          </button>
          <span className="text-lg font-bold text-gray-900 ml-2">
            {MONTHS[month]} {year}
          </span>
        </div>
      </div>

      {/* Calendar Grid */}
      <div className="rounded-2xl bg-white border border-gray-200/60 shadow-sm overflow-hidden">
        {/* Day headers */}
        <div className="grid grid-cols-7 border-b border-gray-100">
          {DAYS.map((d) => (
            <div key={d} className="px-2 py-2.5 text-center text-[10px] font-bold text-gray-500 uppercase tracking-wide">
              {d}
            </div>
          ))}
        </div>

        {/* Calendar cells */}
        <div className="grid grid-cols-7">
          {calendarDays.map((day, idx) => {
            const dateStr = day ? `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}` : "";
            const dayItems = day ? getItemsForDay(day) : [];
            const isToday = dateStr === today;

            return (
              <div
                key={idx}
                className={`min-h-[100px] border-b border-r border-gray-100 p-1.5 ${
                  day ? "bg-white hover:bg-gray-50/50" : "bg-gray-50/30"
                } transition-colors`}
              >
                {day && (
                  <>
                    <div className={`text-xs font-semibold mb-1 w-6 h-6 flex items-center justify-center rounded-lg ${
                      isToday ? "bg-indigo-500 text-white" : "text-gray-500"
                    }`}>
                      {day}
                    </div>
                    <div className="space-y-0.5">
                      {dayItems.slice(0, 3).map((item) => {
                        const Icon = typeIcons[item.type];
                        return (
                          <button
                            key={item.id}
                            onClick={() => handleItemClick(item)}
                            className={`w-full text-left px-1.5 py-0.5 rounded text-[9px] font-medium border truncate flex items-center gap-1 hover:opacity-80 transition-opacity ${typeStyles[item.type]}`}
                            title={item.title}
                          >
                            <Icon className="w-2.5 h-2.5 shrink-0" />
                            <span className="truncate">{item.title}</span>
                          </button>
                        );
                      })}
                      {dayItems.length > 3 && (
                        <span className="text-[9px] text-gray-400 font-medium px-1">+{dayItems.length - 3} more</span>
                      )}
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <footer className="py-10 text-center">
        <p className="text-xs text-gray-400">
          Content Calendar &middot; Drafts, published blogs, and queued articles
        </p>
      </footer>
    </div>
  );
}
