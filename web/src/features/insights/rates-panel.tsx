import { ChartColumn, Table2 } from 'lucide-react';
import { useState } from 'react';

import { EmptyState } from '@/components/shell/empty-state';
import { Button } from '@/components/ui/button';
import { Field, Fieldset } from '@/components/ui/field';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { StatTile } from '@/components/ui/stat-tile';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { ProblemAlert } from '@/features/data/problem-alert';
import { cn } from '@/lib/cn';

import { BlockLoading } from '../settings/components/settings-block';
import { formatDate, formatNumber } from '../settings/format';
import { pillarNames, usePillars } from '../settings/queries';
import { dimensionRows } from './chart-math';
import { DIMENSIONS, RATE_KEYS, dimensionLabel, rateLabel, valueLabel, type InsightDimension, type RateKey } from './labels';
import { useOverview } from './queries';
import { RatesChart, RatesTable } from './rates-chart';

const WINDOWS: readonly { weeks: number; label: string }[] = [
  { weeks: 4, label: '4 weeks' },
  { weeks: 12, label: '12 weeks' },
  { weeks: 26, label: '26 weeks' },
  { weeks: 52, label: 'A year' },
];

/**
 * How posts do, per 1,000 impressions, against the owner's own trailing median: by pillar,
 * format, hook, lane, go-live mode and whether the post drew on the owner's stories. One filter
 * row (window and metric) scopes the figures and the chart below it.
 */
export function RatesPanel() {
  const [weeks, setWeeks] = useState(12);
  const [metric, setMetric] = useState<RateKey>('engagement');
  const [dimension, setDimension] = useState<InsightDimension>('pillar');
  const [asTable, setAsTable] = useState(false);
  const overview = useOverview(weeks);
  const pillars = usePillars();
  const names = pillarNames(pillars.data?.item.pillars);

  const filters = (
    <div className="flex flex-wrap items-end gap-4">
      <Fieldset legend="Window">
        {(group) => (
          <SegmentedControl
            {...group}
            value={String(weeks)}
            onValueChange={(value) => {
              const next = WINDOWS.find((item) => String(item.weeks) === value);
              if (next) setWeeks(next.weeks);
            }}
          >
            {WINDOWS.map((item) => (
              <SegmentedControlItem key={item.weeks} value={String(item.weeks)}>
                {item.label}
              </SegmentedControlItem>
            ))}
          </SegmentedControl>
        )}
      </Fieldset>
      <Field label="Measure">
        {(control) => (
          <Select
            value={metric}
            onValueChange={(value) => {
              const next = RATE_KEYS.find((item) => item.value === value);
              if (next) setMetric(next.value);
            }}
          >
            <SelectTrigger {...control} className="min-w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {RATE_KEYS.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </Field>
    </div>
  );

  if (overview.isPending) {
    return (
      <div className="grid gap-6">
        {filters}
        <BlockLoading label="Loading the rates" lines={5} />
      </div>
    );
  }
  if (overview.isError) {
    return (
      <div className="grid gap-6">
        {filters}
        <ProblemAlert
          error={overview.error}
          title="Couldn’t load the rates."
          onRetry={() => void overview.refetch()}
          retryLabel="Try loading the rates again"
        />
      </div>
    );
  }
  const data = overview.data;
  const labelOf = (value: string) => valueLabel(dimension, value, names);
  const rows = dimensionRows(data, dimension, metric);
  const median = data.ownerMedian[metric] ?? null;
  const title = `${rateLabel(metric)} by ${dimensionLabel(dimension).toLocaleLowerCase()}, last ${String(data.weeks)} weeks`;

  return (
    <div className="grid gap-6">
      {filters}
      {data.posts === 0 ? (
        <EmptyState
          title="No insights yet."
          yourPart="Log a 30-second snapshot at 48 hours and at 7 days after each post. Rates need the impressions."
        >
          <p>
            Rates per 1,000 impressions are compared with your own trailing median, by pillar, format, hook, lane and go-live
            mode. Insights only suggest changes. The planner never adjusts itself.
          </p>
          <p>Insights moves to its own area once there are about eight weeks of data.</p>
        </EmptyState>
      ) : (
        <div className={cn('grid gap-6 transition-opacity', overview.isPlaceholderData && 'opacity-60')}>
          <div className="grid gap-3 sm:grid-cols-3">
            <StatTile
              label="Posts measured"
              value={data.posts}
              caption={`Live since ${formatDate(data.since)}, with impressions.`}
            />
            <StatTile
              label={`Your median, ${rateLabel(metric).toLocaleLowerCase()}`}
              value={median}
              format={(value) => formatNumber(value)}
              unit="per 1K"
              caption="The middle post in the window: the line every row is compared with."
            />
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <ToggleGroup
              type="single"
              variant="outline"
              size="sm"
              aria-label="Compare by"
              value={dimension}
              onValueChange={(value) => {
                const next = DIMENSIONS.find((item) => item.value === value);
                if (next) setDimension(next.value);
              }}
            >
              {DIMENSIONS.map((item) => (
                <ToggleGroupItem key={item.value} value={item.value}>
                  {item.label}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
            <Button
              variant="ghost"
              size="sm"
              aria-pressed={asTable}
              onClick={() => {
                setAsTable((current) => !current);
              }}
            >
              {asTable ? <ChartColumn aria-hidden="true" /> : <Table2 aria-hidden="true" />}
              {asTable ? 'Show the chart' : 'Show as a table'}
            </Button>
          </div>
          {rows.length === 0 ? (
            <p className="text-ink-muted">No measured posts for this dimension yet.</p>
          ) : asTable ? (
            <RatesTable
              rows={data.rows.filter((row) => row.dimension === dimension)}
              metric={metric}
              labelOf={labelOf}
              title={title}
            />
          ) : (
            <RatesChart rows={rows} metric={metric} labelOf={labelOf} title={title} />
          )}
        </div>
      )}
    </div>
  );
}
