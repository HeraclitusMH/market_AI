import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { SegmentedControl } from '@/components/SegmentedControl';

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'number') return String(v);
  return String(v);
}

export function Config() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ['config'], queryFn: api.getConfig, staleTime: 60_000 });
  const setProvider = useMutation({
    mutationFn: api.setSentimentProvider,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['config'] });
      queryClient.invalidateQueries({ queryKey: ['sentiment'] });
      queryClient.invalidateQueries({ queryKey: ['overview'] });
    },
  });

  if (isLoading) return <div className="loading-state">Loading config…</div>;
  if (!data) return null;

  const entries = Object.entries(data.sections);
  const activeProvider = String(data.sections.Sentiment?.provider ?? 'rss_lexicon');

  return (
    <div>
      <h1 className="page-title">Config</h1>
      <p style={{ fontSize: 12.5, color: 'var(--ink-4)', marginBottom: 'var(--gap)', marginTop: -8 }}>
        Active runtime configuration. Persistent defaults still live in <code style={{ fontFamily: 'JetBrains Mono', fontSize: 11.5, background: 'var(--bg-4)', padding: '1px 5px', borderRadius: 3 }}>config.yaml</code>.
      </p>

      <div className="config-toolbar">
        <span className="config-toolbar-label">Sentiment evaluation</span>
        <SegmentedControl
          aria-label="Sentiment evaluation provider"
          value={activeProvider}
          onChange={(provider) => setProvider.mutate(provider)}
          options={[
            { value: 'rss_lexicon', label: 'RSS' },
            { value: 'claude_llm', label: 'Claude LLM' },
            { value: 'claude_routine', label: 'Routine' },
            { value: 'mock', label: 'Mock' },
          ]}
        />
        {setProvider.isPending && <span className="config-toolbar-status">Saving...</span>}
        {setProvider.isError && <span className="config-toolbar-error">Update failed</span>}
      </div>

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
