import { useState } from 'react';
import { ControlsTab } from './score-control/ControlsTab';
import { DocsTab } from './score-control/DocsTab';
import { RankingsTab } from './score-control/RankingsTab';

type Tab = 'controls' | 'docs' | 'rankings';

const TABS: { key: Tab; label: string }[] = [
  { key: 'controls', label: 'Controls' },
  { key: 'docs', label: 'Documentation' },
  { key: 'rankings', label: 'Rankings' },
];

export function ScoreControl() {
  const [tab, setTab] = useState<Tab>('controls');

  return (
    <div>
      <h1 className="page-title">Score Control Panel</h1>
      <div style={{ marginBottom: 'var(--gap)', color: 'var(--ink-4)', fontSize: 12 }}>
        Inspect and manually trigger the 6-factor composite scoring pipeline.
      </div>

      <div style={{ marginBottom: 'var(--gap)' }}>
        <div className="seg-ctrl" role="tablist" aria-label="Score control sections">
          {TABS.map((t) => (
            <button
              key={t.key}
              role="tab"
              aria-selected={tab === t.key}
              className={`seg-ctrl-item${tab === t.key ? ' active' : ''}`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {tab === 'controls' && <ControlsTab />}
      {tab === 'docs' && <DocsTab />}
      {tab === 'rankings' && <RankingsTab />}
    </div>
  );
}
