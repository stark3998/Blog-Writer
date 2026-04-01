import { useState, useRef, useEffect } from "react";
import { Share2 } from "lucide-react";

interface Props {
  children: React.ReactNode; // The existing LinkedIn, Twitter, Medium buttons
}

export default function DistributeDropdown({ children }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={`p-2 rounded-xl transition-all duration-200 ${
          open
            ? "bg-blue-50 text-blue-600 border border-blue-200/60"
            : "text-gray-400 hover:text-blue-600 hover:bg-blue-50 border border-transparent"
        }`}
        title="Distribute to social platforms"
      >
        <Share2 className="w-4 h-4" />
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1.5 w-auto min-w-[200px] rounded-xl bg-white border border-gray-200/60 shadow-xl p-2 z-50 animate-scale-in">
          <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider px-2 py-1 mb-1">
            Distribute
          </p>
          <div className="flex flex-col gap-1">
            {children}
          </div>
        </div>
      )}
    </div>
  );
}
