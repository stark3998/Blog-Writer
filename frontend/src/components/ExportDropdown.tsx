import { useState, useRef, useEffect } from "react";
import { exportBlog } from "../services/api";
import type { ExportFormat } from "../types";
import { Download, ChevronDown, FileText, FileCode, FileType } from "lucide-react";

interface Props {
  content: string;
}

const FORMATS: { value: ExportFormat; label: string; icon: typeof FileText }[] = [
  { value: "md", label: "Markdown (.md)", icon: FileText },
  { value: "html", label: "HTML (.html)", icon: FileCode },
  { value: "pdf", label: "PDF (.pdf)", icon: FileType },
  { value: "docx", label: "Word (.docx)", icon: FileType },
  { value: "mdx", label: "MDX (.mdx)", icon: FileText },
];

export default function ExportDropdown({ content }: Props) {
  const [open, setOpen] = useState(false);
  const [exporting, setExporting] = useState<ExportFormat | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const handleExport = async (format: ExportFormat) => {
    setExporting(format);
    try {
      const blob = await exportBlog(content, format);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `blog.${format}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setOpen(false);
    } catch (err) {
      console.error("Export failed:", err);
    } finally {
      setExporting(null);
    }
  };

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setOpen(!open)}
        disabled={!content.trim()}
        className="px-3 py-1.5 rounded-xl text-sm font-medium transition-all duration-200 flex items-center gap-1.5 disabled:opacity-30 border border-gray-200/60 text-gray-500 hover:text-gray-900 hover:bg-gray-50 hover:border-gray-300"
      >
        <Download className="w-3.5 h-3.5" />
        Export
        <ChevronDown className={`w-3 h-3 transition-transform duration-200 ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-52 rounded-xl bg-white border border-gray-200/80 shadow-xl shadow-gray-900/5 z-50 py-1.5 overflow-hidden animate-scale-in">
          <div className="px-3 py-1.5 mb-1">
            <p className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">Export format</p>
          </div>
          {FORMATS.map(({ value, label, icon: Icon }, i) => (
            <button
              key={value}
              onClick={() => handleExport(value)}
              disabled={exporting !== null}
              className="w-full px-3 py-2.5 text-left text-sm hover:bg-indigo-50 transition-all duration-150 flex items-center gap-2.5 disabled:opacity-40 text-gray-600 hover:text-indigo-600 animate-fade-in-up"
              style={{ animationDelay: `${i * 40}ms` }}
            >
              <Icon className="w-4 h-4 text-gray-400" />
              <span className="font-medium">{exporting === value ? "Exporting..." : label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
