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

  // Close on outside click
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
        className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-50 text-sm font-medium transition-colors flex items-center gap-1.5"
      >
        <Download className="w-3.5 h-3.5" />
        Export
        <ChevronDown className="w-3 h-3" />
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1 w-48 rounded-lg bg-slate-800 border border-slate-700 shadow-xl z-50 py-1 overflow-hidden">
          {FORMATS.map(({ value, label, icon: Icon }) => (
            <button
              key={value}
              onClick={() => handleExport(value)}
              disabled={exporting !== null}
              className="w-full px-4 py-2 text-left text-sm hover:bg-slate-700 transition-colors flex items-center gap-2 disabled:opacity-50"
            >
              <Icon className="w-4 h-4 text-slate-400" />
              {exporting === value ? "Exporting..." : label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
