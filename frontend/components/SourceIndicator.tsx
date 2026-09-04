'use client';

/** A deliberately quiet switch: source choice is context, not a dashboard. */
export function SourceIndicator({
  source,
  onChange,
}: {
  source: 'yahoo' | 'demo';
  onChange: (source: 'yahoo' | 'demo') => void;
}) {
  return (
    <label className="flex items-center gap-1.5 text-xs text-ink-faint">
      <span className="hidden sm:inline">Data source</span>
      <select
        aria-label="Data source"
        value={source}
        onChange={(event) => onChange(event.target.value as 'yahoo' | 'demo')}
        className="border-b border-rule-strong bg-transparent py-1 text-xs font-medium text-ink-soft hover:border-ink-soft focus:border-accent"
      >
        <option value="yahoo">Yahoo Finance {'\u00b7'} Live</option>
        <option value="demo">Demo {'\u00b7'} All states</option>
      </select>
    </label>
  );
}
