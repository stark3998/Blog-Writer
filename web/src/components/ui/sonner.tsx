import { Toaster as Sonner, type ToasterProps } from 'sonner';

import { useTheme } from '@/theme/theme-provider';

/**
 * Toasts in the app's tokens. Sonner's own CSS is bundled by index.css, so it
 * works without the style tag Sonner tries to inject at runtime.
 */
export function Toaster(props: ToasterProps) {
  const { resolved } = useTheme();
  return (
    <Sonner
      theme={resolved}
      position="bottom-right"
      offset={{ bottom: 'var(--bw-toast-offset-bottom)', right: '1.5rem' }}
      mobileOffset={{ bottom: 'var(--bw-toast-offset-bottom)', left: '1rem', right: '1rem' }}
      containerAriaLabel="Notifications"
      toastOptions={{
        unstyled: true,
        classNames: {
          // Width only above Sonner's 600px phone breakpoint; below it, Sonner sizes
          // the toast to the screen minus the side offsets.
          toast:
            'flex items-start gap-3 rounded-lg border border-line bg-raised p-4 text-sm text-ink shadow-overlay min-[601px]:w-(--width)',
          title: 'font-semibold',
          description: 'mt-0.5 text-ink-muted',
          icon: 'mt-0.5 [&_svg]:size-4',
          actionButton:
            'ml-auto inline-flex h-8 shrink-0 items-center rounded-md bg-ink px-3 text-sm font-medium text-ink-inverse',
          cancelButton: 'inline-flex h-8 shrink-0 items-center rounded-md px-3 text-sm font-medium text-ink-muted',
          success: '[&_[data-icon]]:text-success',
          error: '[&_[data-icon]]:text-danger',
          warning: '[&_[data-icon]]:text-warn',
        },
      }}
      {...props}
    />
  );
}
