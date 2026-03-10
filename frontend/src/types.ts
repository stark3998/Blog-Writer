export interface BlogDraft {
  id: string;
  title: string;
  slug: string;
  excerpt: string;
  content: string;
  sourceUrl: string;
  sourceType: string;
  createdAt: string;
  updatedAt: string;
}

export interface GenerateResult {
  mdx_content: string;
  slug: string;
  title: string;
  excerpt: string;
  source_url: string;
  source_type: string;
}

export type ExportFormat = "md" | "html" | "pdf" | "docx" | "mdx";
