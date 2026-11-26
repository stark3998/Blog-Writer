import { clsx, type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

/** tailwind-merge needs to know the token names that are not t-shirt sizes. */
const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      shadow: ['overlay'],
      spacing: ['sidebar', 'rail'],
      container: ['measure', 'page'],
      ease: ['standard'],
      leading: ['prose'],
      animate: [
        'fade-in',
        'pop-in',
        'slide-in-left',
        'slide-in-right',
        'slide-in-top',
        'slide-in-bottom',
        'progress',
        'pulse-soft',
      ],
    },
  },
});

/** Joins class names and resolves Tailwind conflicts (the last one wins). */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
