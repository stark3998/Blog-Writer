import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import "./index.css";
import Home from "./pages/Home";
import Editor from "./pages/Editor";
import BlogView from "./pages/BlogView";
import Settings from "./pages/Settings";
import Prompts from "./pages/Prompts";
import Diagnostics from "./pages/Diagnostics";
import Keywords from "./pages/Keywords";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/editor" element={<Editor />} />
        <Route path="/editor/:id" element={<Editor />} />
        <Route path="/blog/:slug" element={<BlogView />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/prompts" element={<Prompts />} />
        <Route path="/keywords" element={<Keywords />} />
        <Route path="/diagnostics" element={<Diagnostics />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>
);
