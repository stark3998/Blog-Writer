import { useEffect, useId, useState } from "react";
import mermaid from "mermaid";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

interface Props {
  content: string;
}

interface SourceVisual {
  url: string;
  alt: string;
  type: string;
}

let mermaidInitialized = false;

function ensureMermaidInitialized() {
  if (mermaidInitialized) return;
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: "dark",
  });
  mermaidInitialized = true;
}

function MermaidBlock({ chart }: { chart: string }) {
  const [svg, setSvg] = useState<string>("");
  const [error, setError] = useState<string>("");
  const renderId = useId().replace(/:/g, "-");

  useEffect(() => {
    let active = true;
    ensureMermaidInitialized();

    const run = async () => {
      try {
        const { svg: rendered } = await mermaid.render(
          `mermaid-${renderId}-${Date.now()}`,
          chart
        );
        if (active) {
          setSvg(rendered);
          setError("");
        }
      } catch {
        if (active) {
          setSvg("");
          setError("Unable to render Mermaid diagram");
        }
      }
    };

    run();
    return () => {
      active = false;
    };
  }, [chart, renderId]);

  if (error) {
    return (
      <pre className="overflow-auto rounded-lg border border-white/[0.06] bg-white/[0.02] p-4 text-slate-300">
        <code>{chart}</code>
      </pre>
    );
  }

  if (!svg) {
    return (
      <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-4 text-slate-500 text-sm animate-fade-in">
        Rendering diagram...
      </div>
    );
  }

  return (
    <div
      className="my-4 overflow-auto rounded-lg border border-white/[0.06] bg-[#0b0f1a] p-4 animate-fade-in"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}

function stripNonMarkdown(raw: string): string {
  let text = raw.replace(/^---[\s\S]*?---\n*/m, "");
  text = text.replace(/^import\s.+$/gm, "");
  text = text.replace(/^<[A-Z]\w+[^>]*\/>\s*$/gm, "");
  return text.trim();
}

function extractSourceVisuals(markdown: string): SourceVisual[] {
  const visuals: SourceVisual[] = [];
  const seen = new Set<string>();

  const addVisual = (url: string, alt: string, type: string) => {
    const trimmedUrl = url.trim();
    if (!trimmedUrl) return;
    if (!/^https?:\/\//i.test(trimmedUrl)) return;
    if (seen.has(trimmedUrl)) return;
    seen.add(trimmedUrl);
    visuals.push({ url: trimmedUrl, alt: alt.trim(), type: type.trim() || "image" });
  };

  const markdownImageRegex = /!\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)/g;
  for (const match of markdown.matchAll(markdownImageRegex)) {
    addVisual(match[2], match[1] || "", "image");
  }

  const sourceSectionMatch = markdown.match(
    /^###\s+Source Images & Diagrams\s*$([\s\S]*?)(?=^#{1,3}\s+|\Z)/im
  );

  if (sourceSectionMatch) {
    const section = sourceSectionMatch[1];

    const typedBulletRegex = /^\s*[-*]\s+\[(diagram|image)\]\s+(https?:\/\/\S+)(?:\s+\(alt:\s*(.+?)\))?\s*$/gim;
    for (const match of section.matchAll(typedBulletRegex)) {
      addVisual(match[2], match[3] || "", match[1]);
    }

    const plainUrlRegex = /(https?:\/\/[^\s)]+\.(?:png|jpg|jpeg|gif|webp|svg))/gim;
    for (const match of section.matchAll(plainUrlRegex)) {
      addVisual(match[1], "", "image");
    }
  }

  return visuals;
}

export default function MarkdownPreview({ content }: Props) {
  const cleaned = stripNonMarkdown(content);
  const sourceVisuals = extractSourceVisuals(cleaned);

  return (
    <div className="h-full overflow-auto px-10 py-8 bg-[#0b0f1a]">
      {sourceVisuals.length > 0 && (
        <details className="mb-8 rounded-xl border border-white/[0.06] bg-white/[0.02] animate-fade-in" open>
          <summary className="cursor-pointer select-none px-5 py-3 text-sm font-semibold text-slate-300 hover:text-white transition-colors">
            Source visuals ({sourceVisuals.length})
          </summary>
          <div className="grid gap-4 border-t border-white/[0.06] p-5 sm:grid-cols-2">
            {sourceVisuals.map((visual) => (
              <figure key={visual.url} className="overflow-hidden rounded-lg border border-white/[0.06] bg-[#0b0f1a]">
                <a href={visual.url} target="_blank" rel="noreferrer" className="block">
                  <img
                    src={visual.url}
                    alt={visual.alt || `${visual.type} from source`}
                    loading="lazy"
                    className="h-48 w-full object-cover"
                  />
                </a>
                <figcaption className="px-3 py-2 text-xs text-slate-400">
                  <span className="mr-2 rounded-md bg-white/[0.04] px-2 py-0.5 uppercase tracking-wide text-slate-500 text-[10px] font-semibold">
                    {visual.type}
                  </span>
                  {visual.alt || visual.url}
                </figcaption>
              </figure>
            ))}
          </div>
        </details>
      )}
      <article className="prose prose-invert prose-slate max-w-none prose-headings:text-slate-100 prose-headings:font-semibold prose-headings:tracking-tight prose-p:text-slate-300 prose-p:leading-relaxed prose-a:text-indigo-400 prose-a:underline-offset-2 prose-a:decoration-indigo-400/30 hover:prose-a:decoration-indigo-400/60 prose-code:text-indigo-300 prose-code:bg-indigo-500/[0.08] prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded-md prose-code:text-[0.85em] prose-code:font-medium prose-pre:bg-[#0f1629] prose-pre:border prose-pre:border-white/[0.06] prose-pre:rounded-xl prose-blockquote:border-indigo-500/40 prose-blockquote:bg-indigo-500/[0.03] prose-blockquote:rounded-r-lg prose-blockquote:py-0.5 prose-strong:text-white prose-li:text-slate-300 prose-hr:border-white/[0.06] prose-th:text-slate-300 prose-td:text-slate-400 prose-table:border-white/[0.06]">
        {cleaned ? (
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeHighlight]}
            components={{
              code({ className, children, ...props }: any) {
                const language = (className || "").replace("language-", "").toLowerCase();
                const chart = String(children).replace(/\n$/, "");

                if (language === "mermaid") {
                  return <MermaidBlock chart={chart} />;
                }

                return (
                  <code className={className} {...props}>
                    {children}
                  </code>
                );
              },
            }}
          >
            {cleaned}
          </ReactMarkdown>
        ) : (
          <div className="text-center mt-24 animate-fade-in-up">
            <div className="w-12 h-12 rounded-xl bg-white/[0.04] border border-white/[0.06] flex items-center justify-center mx-auto mb-4">
              <svg className="w-6 h-6 text-slate-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
              </svg>
            </div>
            <p className="text-base text-slate-500 font-medium">No content yet</p>
            <p className="text-sm text-slate-600 mt-1">Start typing in the editor or generate a blog from a URL</p>
          </div>
        )}
      </article>
    </div>
  );
}
