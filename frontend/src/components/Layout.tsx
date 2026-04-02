import { type ReactNode, useState, useEffect } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Sparkles, PenLine, Home, BarChart3, Settings, Moon, Sun, Calendar, Menu, X } from "lucide-react";
import ProfileDropdown from "./ProfileDropdown";
import ToastContainer from "./ToastContainer";
import { useBlogStore } from "../store/blogStore";
import { useThemeStore } from "../store/themeStore";

const NAV_LINKS = [
  { to: "/", label: "Home", icon: Home },
  { to: "/analytics", label: "Analytics", icon: BarChart3 },
  { to: "/calendar", label: "Calendar", icon: Calendar },
  { to: "/settings", label: "Settings", icon: Settings },
];

export default function Layout({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { setContent, setDraft } = useBlogStore();
  const { theme, toggleTheme } = useThemeStore();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  // Close mobile menu on navigation
  useEffect(() => {
    setMobileMenuOpen(false);
  }, [location.pathname]);

  const isActive = (path: string) => {
    if (path === "/") return location.pathname === "/";
    return location.pathname.startsWith(path);
  };

  return (
    <div className="min-h-screen bg-[var(--bg-base)] text-[var(--text-primary)] transition-colors duration-300">
      {/* Decorative background */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-[-30%] right-[-10%] w-[800px] h-[800px] bg-indigo-200/30 rounded-full blur-[140px] animate-float" />
        <div
          className="absolute bottom-[-20%] left-[-5%] w-[600px] h-[600px] bg-violet-200/20 rounded-full blur-[120px] animate-float"
          style={{ animationDelay: "3s" }}
        />
      </div>

      {/* Navigation */}
      <nav className="relative glass-strong border-b border-indigo-100/60 animate-fade-in-down sticky top-0 z-40">
        <div className="px-4 sm:px-6 py-2.5 flex items-center justify-between">
          {/* Left: Logo + Nav Links */}
          <div className="flex items-center gap-4 sm:gap-6">
            {/* Mobile hamburger */}
            <button
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              className="sm:hidden p-2 rounded-xl text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 transition-all duration-200"
            >
              {mobileMenuOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
            </button>

            <Link to="/" className="flex items-center gap-2 sm:gap-3 group shrink-0">
              <div className="w-8 h-8 sm:w-9 sm:h-9 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 flex items-center justify-center shadow-lg shadow-indigo-500/25 group-hover:shadow-indigo-500/40 transition-shadow">
                <Sparkles className="w-4 h-4 sm:w-4.5 sm:h-4.5 text-white" />
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-base sm:text-lg font-bold tracking-tight text-gray-900">Blog Writer</span>
                <span className="text-[10px] font-semibold text-indigo-500 tracking-widest uppercase">AI</span>
              </div>
            </Link>

            <div className="hidden sm:flex items-center gap-1">
              {NAV_LINKS.map(({ to, label, icon: Icon }) => (
                <Link
                  key={to}
                  to={to}
                  className={`flex items-center gap-2 px-3.5 py-2 rounded-xl text-sm font-medium transition-all duration-200 ${
                    isActive(to)
                      ? "bg-indigo-50 text-indigo-600 border border-indigo-200/60"
                      : "text-gray-500 hover:text-gray-900 hover:bg-gray-50 border border-transparent"
                  }`}
                >
                  <Icon className="w-4 h-4" />
                  {label}
                </Link>
              ))}
            </div>
          </div>

          {/* Right: New Draft + Profile */}
          <div className="flex items-center gap-1.5 sm:gap-2">
            <button
              onClick={() => {
                setContent("");
                setDraft(null);
                navigate("/editor");
              }}
              className="px-3 sm:px-4 py-2 rounded-xl text-sm font-semibold text-indigo-600 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200/60 transition-all duration-200 flex items-center gap-2"
            >
              <PenLine className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">New Draft</span>
            </button>
            <button
              onClick={toggleTheme}
              className="p-2 rounded-xl text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-gray-700 transition-all duration-200"
              title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            >
              {theme === "dark" ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
            </button>
            <div className="w-px h-6 bg-gray-200 dark:bg-gray-700 mx-1 hidden sm:block" />
            <div className="hidden sm:block">
              <ProfileDropdown />
            </div>
          </div>
        </div>

        {/* Mobile menu */}
        {mobileMenuOpen && (
          <div className="sm:hidden border-t border-gray-200/60 bg-white/95 backdrop-blur-lg animate-fade-in-down">
            <div className="px-4 py-3 space-y-1">
              {NAV_LINKS.map(({ to, label, icon: Icon }) => (
                <Link
                  key={to}
                  to={to}
                  className={`flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-medium transition-all duration-200 ${
                    isActive(to)
                      ? "bg-indigo-50 text-indigo-600 border border-indigo-200/60"
                      : "text-gray-600 hover:text-gray-900 hover:bg-gray-50 border border-transparent"
                  }`}
                >
                  <Icon className="w-4.5 h-4.5" />
                  {label}
                </Link>
              ))}
              <div className="pt-2 border-t border-gray-100 mt-2">
                <div className="flex items-center justify-between px-4 py-2">
                  <ProfileDropdown />
                </div>
              </div>
            </div>
          </div>
        )}
      </nav>

      {/* Page content */}
      <main className="relative">{children}</main>

      {/* Toast notifications */}
      <ToastContainer />
    </div>
  );
}
