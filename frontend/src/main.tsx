import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import "./index.css";
import { AppAuthProvider } from "./auth/AuthProvider";
import AuthGuard from "./auth/AuthGuard";
import Layout from "./components/Layout";
import Home from "./pages/Home";
import Editor from "./pages/Editor";
import BlogView from "./pages/BlogView";
import Settings from "./pages/Settings";
import Dashboard from "./pages/Dashboard";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppAuthProvider>
      <BrowserRouter>
        <AuthGuard>
          <Layout>
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/editor" element={<Editor />} />
              <Route path="/editor/:id" element={<Editor />} />
              <Route path="/blog/:slug" element={<BlogView />} />
              <Route path="/analytics" element={<Dashboard />} />
              <Route path="/settings" element={<Settings />} />
            </Routes>
          </Layout>
        </AuthGuard>
      </BrowserRouter>
    </AppAuthProvider>
  </StrictMode>
);
