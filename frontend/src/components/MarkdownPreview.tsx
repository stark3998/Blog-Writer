import { useEffect, useId, useState } from "react";
import mermaid from "mermaid";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

interface Props {
  content: string;
}

let mermaidInitialized = false;

function ensureMermaidInitialized() {
  if (mermaidInitialized) return;
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: "default",
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
      <pre className="overflow-auto rounded-xl border border-gray-200/60 bg-gray-50 p-4 text-gray-600">
        <code>{chart}</code>
      </pre>
    );
  }

  if (!svg) {
    return (
      <div className="rounded-xl border border-gray-200/60 bg-gray-50 p-4 text-gray-400 text-sm animate-fade-in">
        Rendering diagram...
      </div>
    );
  }

  return (
    <div
      className="my-4 overflow-auto rounded-xl border border-gray-200/60 bg-white p-4 animate-fade-in"
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

export default function MarkdownPreview({ content }: Props) {
  const cleaned = stripNonMarkdown(content);

  return (
    <div className="h-full overflow-auto px-10 py-8 bg-white">
      <article className="prose prose-slate max-w-none prose-headings:text-gray-900 prose-headings:font-bold prose-headings:tracking-tight prose-p:text-gray-600 prose-p:leading-relaxed prose-a:text-indigo-600 prose-a:underline-offset-2 prose-a:decoration-indigo-300/50 hover:prose-a:decoration-indigo-500 prose-code:text-indigo-600 prose-code:bg-indigo-50 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded-md prose-code:text-[0.85em] prose-code:font-semibold prose-pre:bg-gray-50 prose-pre:border prose-pre:border-gray-200/60 prose-pre:rounded-xl prose-blockquote:border-indigo-400 prose-blockquote:bg-indigo-50/50 prose-blockquote:rounded-r-xl prose-blockquote:py-0.5 prose-strong:text-gray-900 prose-li:text-gray-600 prose-hr:border-gray-200 prose-th:text-gray-700 prose-td:text-gray-600 prose-table:border-gray-200 prose-img:rounded-xl prose-img:shadow-md">
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
            <div className="w-12 h-12 rounded-xl bg-gray-50 border border-gray-200/60 flex items-center justify-center mx-auto mb-4">
              <svg className="w-6 h-6 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
              </svg>
            </div>
            <p className="text-base text-gray-400 font-semibold">No content yet</p>
            <p className="text-sm text-gray-300 mt-1">Start typing in the editor or generate a blog from a URL</p>
          </div>
        )}
      </article>
    </div>
  );
}
