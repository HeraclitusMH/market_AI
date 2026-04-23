import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'number') return String(v);
  return String(v);
}

export function Config() {
  const { data, isLoading } = useQuery({ queryKey: ['config'], queryFn: api.getConfig, staleTime: 60_000 });

  if (isLoading) return <div className="loading-state">Loading config…</div>;
  if (!data) return null;

  const entries = Object.entries(data.sections);

  return (
    <div>
      <h1 className="page-title">Config</h1>
      <p style={{ fontSize: 12.5, color: 'var(--ink-4)', marginBottom: 'var(--gap)', marginTop: -8 }}>
        Read-only view of the active configuration. Edit <code style={{ fontFamily: 'JetBrains Mono', fontSize: 11.5, background: 'var(--bg-4)', padding: '1px 5px', borderRadius: 3 }}>config.yaml</code> and restart to apply changes.
      </p>

      <div className="grid-3">
        {entries.map(([section, kv]) => (
          <div key={section} className="config-section">
            <div className="config-section-title">{section}</div>
            {Object.entries(kv).map(([k, v]) => (
              <div key={k} className="config-row">
                <span className="config-key" title={k}>{k.replace(/_/g, ' ')}</span>
                <span className="config-val">{formatValue(v)}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
