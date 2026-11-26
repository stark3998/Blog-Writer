/** Moves focus to the page's h1 (it has tabIndex=-1), so screen readers announce the new page. */
export function focusPageHeading(): void {
  document.querySelector<HTMLElement>('#main h1')?.focus({ preventScroll: true });
}
