import { ChevronDown } from 'lucide-react';
import { Accordion as AccordionPrimitive } from 'radix-ui';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

/**
 * Sections on hairlines that open in place: FAQs, a gate's findings, a version's details.
 * `type="single" collapsible` opens one at a time; `type="multiple"` lets several stay open.
 * Each trigger sits in a heading (`headingLevel`, 3 by default) so the outline stays navigable.
 */
export function Accordion({ className, ...props }: ComponentProps<typeof AccordionPrimitive.Root>) {
  return <AccordionPrimitive.Root data-slot="accordion" className={cn('border-t border-line', className)} {...props} />;
}

export function AccordionItem({ className, ...props }: ComponentProps<typeof AccordionPrimitive.Item>) {
  return <AccordionPrimitive.Item data-slot="accordion-item" className={cn('border-b border-line', className)} {...props} />;
}

const HEADINGS = { 2: 'h2', 3: 'h3', 4: 'h4' } as const;

export function AccordionTrigger({
  className,
  children,
  headingLevel = 3,
  ...props
}: ComponentProps<typeof AccordionPrimitive.Trigger> & { headingLevel?: keyof typeof HEADINGS }) {
  const Heading = HEADINGS[headingLevel];
  return (
    <AccordionPrimitive.Header asChild>
      <Heading className="flex">
        <AccordionPrimitive.Trigger
          data-slot="accordion-trigger"
          className={cn(
            'flex flex-1 items-center justify-between gap-4 py-4 text-left text-base font-semibold text-ink',
            'transition-colors duration-150 hover:text-ink-muted disabled:pointer-events-none disabled:opacity-50',
            '[&[data-state=open]>svg]:rotate-180',
            className,
          )}
          {...props}
        >
          {children}
          <ChevronDown
            aria-hidden="true"
            className="size-4 shrink-0 text-ink-subtle transition-transform duration-200 ease-standard"
          />
        </AccordionPrimitive.Trigger>
      </Heading>
    </AccordionPrimitive.Header>
  );
}

export function AccordionContent({ className, children, ...props }: ComponentProps<typeof AccordionPrimitive.Content>) {
  return (
    <AccordionPrimitive.Content
      data-slot="accordion-content"
      className="overflow-hidden text-ink-muted data-[state=closed]:animate-collapse data-[state=open]:animate-expand"
      {...props}
    >
      <div className={cn('pb-4', className)}>{children}</div>
    </AccordionPrimitive.Content>
  );
}
