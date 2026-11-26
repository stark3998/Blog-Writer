import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

import { fieldClass } from './input';

export function Textarea({ className, ...props }: ComponentProps<'textarea'>) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(fieldClass, 'flex min-h-24 py-2 field-sizing-content', className)}
      {...props}
    />
  );
}
