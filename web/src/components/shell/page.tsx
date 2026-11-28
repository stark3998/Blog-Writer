import type { ReactNode } from 'react';

import { documentTitle } from '@/app/nav';
import { cn } from '@/lib/cn';

/** The page column inside the sheet. Content stays left-aligned within it. */
export function Page({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('mx-auto w-full max-w-page px-5 pb-20 pt-8 sm:px-8 md:pt-12 lg:px-12 lg:pt-16', className)}>
      {children}
    </div>
  );
}

interface PageHeaderProps {
  title: string;
  /** One or two sentences under the title, in the editorial sense: what this page is for. */
  standfirst?: ReactNode;
  /** Sets the document title. Defaults to the page title. */
  documentTitle?: string;
  children?: ReactNode;
}

/** Serif title and standfirst. The h1 takes focus after navigation, so screen readers announce the page. */
export function PageHeader({ title, standfirst, documentTitle: docTitle, children }: PageHeaderProps) {
  return (
    <div className="mb-10 max-w-measure md:mb-12">
      <title>{documentTitle(docTitle ?? title)}</title>
      <h1 tabIndex={-1} className="font-serif text-3xl font-medium text-ink outline-none md:text-4xl">
        {title}
      </h1>
      {standfirst ? <p className="mt-3 text-lg text-ink-muted">{standfirst}</p> : null}
      {children}
    </div>
  );
}
