import { cva, type VariantProps } from 'class-variance-authority';
import { CircleCheck, CircleX, Info, TriangleAlert, type LucideIcon } from 'lucide-react';
import type { ComponentProps, ReactNode } from 'react';

import { cn } from '@/lib/cn';

const alertVariants = cva(
  [
    'grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 gap-y-1 rounded-lg border px-4 py-3 text-sm',
    '[&>svg]:mt-0.5 [&>svg]:size-4 [&>svg]:shrink-0',
  ],
  {
    variants: {
      variant: {
        /** Something to know. No state colour: info is the ink, on the sunken paper. */
        info: 'border-line bg-sunken [&>svg]:text-ink-subtle',
        success: 'border-transparent bg-success-soft [&>svg]:text-success',
        warn: 'border-transparent bg-warn-soft [&>svg]:text-warn',
        error: 'border-transparent bg-danger-soft [&>svg]:text-danger',
      },
    },
    defaultVariants: { variant: 'info' },
  },
);

type AlertVariant = NonNullable<VariantProps<typeof alertVariants>['variant']>;

const ICONS: Record<AlertVariant, LucideIcon> = {
  info: Info,
  success: CircleCheck,
  warn: TriangleAlert,
  error: CircleX,
};

export interface AlertProps extends ComponentProps<'div'>, VariantProps<typeof alertVariants> {
  /** Replaces the variant's icon; `null` for none. The icon is decorative: the words carry the meaning. */
  icon?: ReactNode;
}

/**
 * A message inside the page: info, success, warn or error, each with its icon, never colour alone.
 *
 * An `error` alert is `role="alert"`, so it is read out when it appears; put it on screen when the
 * error happens, not before. Others are plain content: pass `role="status"` for one that appears
 * after an action, or `role={undefined}` for a standing error banner.
 */
export function Alert({ className, variant, icon, children, ...props }: AlertProps) {
  const tone: AlertVariant = variant ?? 'info';
  const Icon = ICONS[tone];
  return (
    <div
      data-slot="alert"
      data-variant={tone}
      role={tone === 'error' ? 'alert' : undefined}
      className={cn(alertVariants({ variant: tone }), icon === null && 'gap-x-0', className)}
      {...props}
    >
      {icon === undefined ? <Icon aria-hidden="true" /> : (icon ?? <span aria-hidden="true" />)}
      {children}
    </div>
  );
}

export function AlertTitle({ className, ...props }: ComponentProps<'p'>) {
  return <p data-slot="alert-title" className={cn('col-start-2 font-semibold text-ink', className)} {...props} />;
}

export function AlertDescription({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div
      data-slot="alert-description"
      className={cn('col-start-2 space-y-2 text-ink-muted', className)}
      {...props}
    />
  );
}

/** Buttons that act on the alert ("Reconnect", "Try again"). */
export function AlertActions({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div data-slot="alert-actions" className={cn('col-start-2 mt-2 flex flex-wrap gap-2', className)} {...props} />
  );
}
