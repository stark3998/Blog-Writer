import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import "./index.css";
import Home from "./pages/Home";
import Editor from "./pages/Editor";
import BlogView from "./pages/BlogView";
import Settings from "./pages/Settings";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/editor" element={<Editor />} />
        <Route path="/editor/:id" element={<Editor />} />
        <Route path="/blog/:slug" element={<BlogView />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>
);
