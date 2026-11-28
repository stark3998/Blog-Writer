import { CircleCheck, CircleHelp, CircleX } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/cn';

import { BRAND_COLOR_INFO, checkPairs, formatRatio, normalizeHex, type BrandColorToken } from '../lib/contrast';
import type { ErrorMap } from '../lib/errors';
import { plural } from '../lib/format';
import type { BrandColorsDraft } from './model';

export interface ColorFieldProps {
  token: BrandColorToken;
  value: string;
  /** The saved colour, for the picker while the typed value is not a colour yet. */
  fallback: string | undefined;
  onChange: (value: string) => void;
  error?: string | undefined;
}

/** One colour token: the system colour picker (also the swatch) and the hex value as text. */
export function ColorField({ token, value, fallback, onChange, error }: ColorFieldProps) {
  const info = BRAND_COLOR_INFO[token];
  const valid = normalizeHex(value);
  const picker = (valid ?? normalizeHex(fallback ?? '') ?? '').toLowerCase();
  return (
    <Field label={info.label} description={info.role} error={error}>
      {(control) => (
        <div className="flex items-center gap-2">
          <input
            type="color"
            aria-label={`Pick ${info.label.toLowerCase()} from the system colour picker`}
            value={picker}
            onChange={(event) => {
              onChange(event.target.value.toUpperCase());
            }}
            className="h-10 w-12 shrink-0 cursor-pointer rounded-md border border-control bg-sheet p-1 pointer-coarse:h-11"
          />
          <Input
            {...control}
            value={value}
            maxLength={7}
            spellCheck={false}
            autoCapitalize="characters"
            autoComplete="off"
            className="w-32 font-mono uppercase"
            onChange={(event) => {
              onChange(event.target.value);
            }}
            onBlur={() => {
              if (valid !== undefined && valid !== value) onChange(valid);
            }}
          />
        </div>
      )}
    </Field>
  );
}

/**
 * Every pair of colours that carries text on a slide, with its contrast ratio and verdict, as
 * the owner types. The specimen shows the pair; the words and the icon carry the verdict.
 */
export function ContrastList({ colors, serverFindings }: { colors: BrandColorsDraft; serverFindings: ErrorMap }) {
  const checks = checkPairs(colors);
  const failing = checks.filter((check) => check.passes === false || (check.passes !== undefined && check.id in serverFindings)).length;
  const unknown = checks.filter((check) => check.passes === undefined).length;
  const summary =
    unknown > 0
      ? 'Some colours aren’t complete yet, so their contrast can’t be checked.'
      : failing === 0
        ? `All ${String(checks.length)} text pairs reach 4.5:1.`
        : `${plural(failing, 'pair is', 'pairs are')} below 4.5:1.`;
  return (
    <div className="space-y-3">
      <p role="status" className={cn('text-sm', failing > 0 ? 'font-medium text-danger' : 'text-ink-muted')}>
        {summary}
      </p>
      <ul className="grid gap-2 sm:grid-cols-2">
        {checks.map((check) => {
          const fg = normalizeHex(colors[check.foreground]);
          const bg = normalizeHex(colors[check.background]);
          const serverSays = serverFindings[check.id];
          const fails = check.passes === false || serverSays !== undefined;
          return (
            <li key={check.id} className="flex items-center gap-3 rounded-md border border-line px-3 py-2">
              <span
                aria-hidden="true"
                className="flex size-10 shrink-0 items-center justify-center rounded-sm border border-line text-base font-semibold"
                style={fg && bg ? { color: fg, backgroundColor: bg } : undefined}
              >
                Aa
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm text-ink">{check.label}</span>
                <span className="block text-xs tabular-nums text-ink-muted">
                  {check.ratio === undefined ? 'Not checked' : formatRatio(check.ratio)}
                </span>
              </span>
              {check.passes === undefined ? (
                <Badge>
                  <CircleHelp aria-hidden="true" />
                  Incomplete
                </Badge>
              ) : fails ? (
                <Badge variant="danger">
                  <CircleX aria-hidden="true" />
                  Too low
                </Badge>
              ) : (
                <Badge variant="success">
                  <CircleCheck aria-hidden="true" />
                  Passes
                </Badge>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
