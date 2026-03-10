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
      <pre className="overflow-auto rounded border border-slate-700 bg-slate-800 p-4 text-slate-300">
        <code>{chart}</code>
      </pre>
    );
  }

  if (!svg) {
    return (
      <div className="rounded border border-slate-700 bg-slate-800 p-4 text-slate-400">
        Rendering diagram...
      </div>
    );
  }

  return (
    <div
      className="my-4 overflow-auto rounded border border-slate-700 bg-slate-900 p-4"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}

/**
 * Strips YAML frontmatter and MDX import/component lines
 * so react-markdown can render the body cleanly.
 */
function stripNonMarkdown(raw: string): string {
  // Remove frontmatter
  let text = raw.replace(/^---[\s\S]*?---\n*/m, "");
  // Remove MDX import lines
  text = text.replace(/^import\s.+$/gm, "");
  // Remove JSX-style self-closing components like <Component ... />
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
    <div className="h-full overflow-auto px-8 py-6 bg-slate-950">
      {sourceVisuals.length > 0 && (
        <details className="mb-6 rounded-lg border border-slate-700 bg-slate-900/80" open>
          <summary className="cursor-pointer select-none px-4 py-3 text-sm font-semibold text-slate-200">
            Source visuals ({sourceVisuals.length})
          </summary>
          <div className="grid gap-4 border-t border-slate-700 p-4 sm:grid-cols-2">
            {sourceVisuals.map((visual) => (
              <figure key={visual.url} className="overflow-hidden rounded border border-slate-700 bg-slate-950">
                <a href={visual.url} target="_blank" rel="noreferrer" className="block">
                  <img
                    src={visual.url}
                    alt={visual.alt || `${visual.type} from source`}
                    loading="lazy"
                    className="h-48 w-full object-cover"
                  />
                </a>
                <figcaption className="px-3 py-2 text-xs text-slate-300">
                  <span className="mr-2 rounded bg-slate-800 px-2 py-0.5 uppercase tracking-wide text-slate-400">
                    {visual.type}
                  </span>
                  {visual.alt || visual.url}
                </figcaption>
              </figure>
            ))}
          </div>
        </details>
      )}
      <article className="prose prose-invert prose-slate max-w-none prose-headings:text-slate-100 prose-p:text-slate-300 prose-a:text-indigo-400 prose-code:text-indigo-300 prose-pre:bg-slate-800 prose-pre:border prose-pre:border-slate-700 prose-blockquote:border-indigo-500 prose-strong:text-white prose-li:text-slate-300">
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
          <div className="text-slate-500 text-center mt-20">
            <p className="text-lg">No content yet</p>
            <p className="text-sm mt-1">Start typing in the editor or generate a blog from a URL</p>
          </div>
        )}
      </article>
    </div>
  );
}
