import { useMemo } from "react";
import { Search, CheckCircle2, AlertTriangle, AlertCircle, Type, Hash, Link2, Image, FileText } from "lucide-react";

interface SEOScore {
  label: string;
  score: number; // 0-100
  status: "good" | "warning" | "error";
  tip: string;
  icon: typeof Search;
}

function analyzeSEO(content: string): { overall: number; checks: SEOScore[] } {
  const lines = content.split("\n");
  const text = content.replace(/^---[\s\S]*?---\n*/m, ""); // strip frontmatter
  const words = text.split(/\s+/).filter(Boolean);
  const wordCount = words.length;

  // Extract frontmatter
  const titleMatch = content.match(/title:\s*["']?(.+?)["']?\s*$/m);
  const excerptMatch = content.match(/excerpt:\s*["']?(.+?)["']?\s*$/m);
  const title = titleMatch?.[1] ?? "";
  const excerpt = excerptMatch?.[1] ?? "";

  // Count headings
  const h1s = lines.filter((l) => /^#\s/.test(l)).length;
  const h2s = lines.filter((l) => /^##\s/.test(l)).length;
  const h3s = lines.filter((l) => /^###\s/.test(l)).length;

  // Count links and images
  const links = (text.match(/\[.*?\]\(.*?\)/g) || []).length;
  const images = (text.match(/!\[.*?\]\(.*?\)/g) || []).length;

  // Readability: avg sentence length
  const sentences = text.split(/[.!?]+/).filter((s) => s.trim().length > 5);
  const avgSentenceLen = sentences.length ? Math.round(words.length / sentences.length) : 0;

  const checks: SEOScore[] = [];

  // Title check
  const titleLen = title.length;
  checks.push({
    label: "Title Length",
    score: titleLen >= 30 && titleLen <= 60 ? 100 : titleLen > 0 ? 60 : 0,
    status: titleLen >= 30 && titleLen <= 60 ? "good" : titleLen > 0 ? "warning" : "error",
    tip: titleLen === 0 ? "Add a title" : titleLen < 30 ? `Title is short (${titleLen} chars). Aim for 30-60.` : titleLen > 60 ? `Title is long (${titleLen} chars). Keep under 60.` : `Good length (${titleLen} chars)`,
    icon: Type,
  });

  // Meta description (excerpt)
  const excLen = excerpt.length;
  checks.push({
    label: "Meta Description",
    score: excLen >= 120 && excLen <= 160 ? 100 : excLen > 0 ? 60 : 0,
    status: excLen >= 120 && excLen <= 160 ? "good" : excLen > 0 ? "warning" : "error",
    tip: excLen === 0 ? "Add an excerpt for meta description" : excLen < 120 ? `Short (${excLen} chars). Aim for 120-160.` : excLen > 160 ? `Long (${excLen} chars). Keep under 160.` : `Good length (${excLen} chars)`,
    icon: FileText,
  });

  // Word count
  checks.push({
    label: "Content Length",
    score: wordCount >= 800 ? 100 : wordCount >= 300 ? 70 : 30,
    status: wordCount >= 800 ? "good" : wordCount >= 300 ? "warning" : "error",
    tip: `${wordCount} words. ${wordCount < 300 ? "Aim for 800+ for SEO." : wordCount < 800 ? "Good start, 800+ is ideal." : "Great length!"}`,
    icon: Hash,
  });

  // Heading structure
  const headingScore = h2s >= 2 ? 100 : h2s >= 1 ? 70 : 30;
  checks.push({
    label: "Heading Structure",
    score: headingScore,
    status: headingScore >= 100 ? "good" : headingScore >= 70 ? "warning" : "error",
    tip: `${h1s} H1, ${h2s} H2, ${h3s} H3. ${h2s < 2 ? "Add more H2 subheadings." : "Good structure!"}`,
    icon: Type,
  });

  // Internal/external links
  checks.push({
    label: "Links",
    score: links >= 3 ? 100 : links >= 1 ? 60 : 20,
    status: links >= 3 ? "good" : links >= 1 ? "warning" : "error",
    tip: `${links} links found. ${links < 1 ? "Add relevant links." : links < 3 ? "Consider adding more." : "Good linking!"}`,
    icon: Link2,
  });

  // Images
  checks.push({
    label: "Images",
    score: images >= 1 ? 100 : 20,
    status: images >= 1 ? "good" : "warning",
    tip: `${images} images. ${images < 1 ? "Add at least one image." : "Has images."}`,
    icon: Image,
  });

  // Readability
  const readScore = avgSentenceLen <= 20 ? 100 : avgSentenceLen <= 25 ? 70 : 40;
  checks.push({
    label: "Readability",
    score: readScore,
    status: readScore >= 100 ? "good" : readScore >= 70 ? "warning" : "error",
    tip: `Avg ${avgSentenceLen} words/sentence. ${avgSentenceLen > 25 ? "Shorten sentences." : avgSentenceLen > 20 ? "Slightly long." : "Easy to read!"}`,
    icon: Search,
  });

  const overall = Math.round(checks.reduce((sum, c) => sum + c.score, 0) / checks.length);

  return { overall, checks };
}

export default function SEOPanel({ content, onClose }: { content: string; onClose: () => void }) {
  const { overall, checks } = useMemo(() => analyzeSEO(content), [content]);

  const overallColor =
    overall >= 80 ? "text-emerald-500" : overall >= 50 ? "text-amber-500" : "text-red-500";
  const overallBg =
    overall >= 80 ? "bg-emerald-500" : overall >= 50 ? "bg-amber-500" : "bg-red-500";

  return (
    <div className="w-72 h-full flex flex-col">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200/60">
        <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
          <Search className="w-4 h-4 text-indigo-500" />
          SEO Score
        </h3>
        <button onClick={onClose} className="p-1 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-all">
          <span className="text-lg leading-none">&times;</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Overall score */}
        <div className="flex flex-col items-center py-4">
          <div className="relative w-20 h-20">
            <svg className="w-20 h-20 -rotate-90" viewBox="0 0 36 36">
              <circle cx="18" cy="18" r="15.9" fill="none" stroke="#e5e7eb" strokeWidth="2.5" />
              <circle
                cx="18" cy="18" r="15.9" fill="none"
                className={overallBg.replace("bg-", "stroke-")}
                strokeWidth="2.5"
                strokeDasharray={`${overall} ${100 - overall}`}
                strokeLinecap="round"
              />
            </svg>
            <span className={`absolute inset-0 flex items-center justify-center text-lg font-bold ${overallColor}`}>
              {overall}
            </span>
          </div>
          <span className="text-xs text-gray-500 mt-2">
            {overall >= 80 ? "Great SEO!" : overall >= 50 ? "Needs improvement" : "Needs work"}
          </span>
        </div>

        {/* Checks */}
        <div className="space-y-2.5">
          {checks.map((check) => {
            const StatusIcon = check.status === "good" ? CheckCircle2 : check.status === "warning" ? AlertTriangle : AlertCircle;
            const statusColor = check.status === "good" ? "text-emerald-500" : check.status === "warning" ? "text-amber-500" : "text-red-500";
            return (
              <div key={check.label} className="p-3 rounded-xl bg-white border border-gray-200/60">
                <div className="flex items-center gap-2 mb-1">
                  <check.icon className="w-3.5 h-3.5 text-gray-400" />
                  <span className="text-xs font-semibold text-gray-700 flex-1">{check.label}</span>
                  <StatusIcon className={`w-3.5 h-3.5 ${statusColor}`} />
                </div>
                <p className="text-[11px] text-gray-500 leading-relaxed">{check.tip}</p>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
