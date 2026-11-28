interface NavigatorWithUAData extends Navigator {
  userAgentData?: { platform?: string };
}

/** True on macOS and iOS, where the command key is the modifier. */
export function isApplePlatform(nav: Navigator = navigator): boolean {
  const platform = (nav as NavigatorWithUAData).userAgentData?.platform ?? nav.userAgent;
  return /mac|iphone|ipad|ipod/i.test(platform);
}

/** The label for a modifier shortcut, for example "⌘K" or "Ctrl K". */
export function shortcutLabel(key: string, nav: Navigator = navigator): string {
  return isApplePlatform(nav) ? `⌘${key}` : `Ctrl ${key}`;
}
