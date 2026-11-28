/**
 * `[name="value"]`, with the value escaped as a CSS string (quotes and backslashes), for finding
 * an element by a server id after a re-render moved it.
 */
export function attributeSelector(name: string, value: string): string {
  return `[${name}="${value.replace(/["\\]/g, '\\$&')}"]`;
}
