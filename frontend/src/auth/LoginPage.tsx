import { useAuth } from "./AuthProvider";
import { LogIn, Loader2 } from "lucide-react";

export default function LoginPage() {
  const { login, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="min-h-screen bg-[var(--bg-base)] flex items-center justify-center">
        <Loader2 size={32} className="animate-spin text-amber-500" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[var(--bg-base)] flex items-center justify-center">
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-[-20%] right-[-10%] w-[600px] h-[600px] bg-amber-200/20 rounded-full blur-[120px]" />
        <div className="absolute bottom-[-10%] left-[10%] w-[500px] h-[500px] bg-orange-200/15 rounded-full blur-[100px]" />
      </div>

      <div className="relative glass-strong rounded-2xl border border-amber-100/60 p-10 max-w-md w-full mx-4 text-center">
        <div className="w-16 h-16 mx-auto rounded-2xl bg-gradient-to-br from-amber-500 to-orange-500 flex items-center justify-center shadow-lg shadow-amber-500/25 mb-6">
          <span className="text-2xl text-white font-bold">B</span>
        </div>

        <h1 className="text-2xl font-bold text-gray-900 mb-2">Blog Writer</h1>
        <p className="text-gray-500 text-sm mb-8">
          Sign in with your Microsoft account to continue.
        </p>

        <button
          onClick={login}
          className="w-full flex items-center justify-center gap-2 px-6 py-3 rounded-xl text-sm font-medium bg-amber-500 hover:bg-amber-400 text-white transition-all shadow-lg shadow-amber-500/25"
        >
          <LogIn size={18} />
          Sign in with Microsoft
        </button>
      </div>
    </div>
  );
}
