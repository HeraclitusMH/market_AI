import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw, AlertCircle, CheckCircle, Clock } from 'lucide-react';
import { api } from '@/lib/api';
import { fmtTs } from '@/lib/formatters';
import { Card, CardHead, CardBody } from '@/components/Card';
import { Button } from '@/components/Button';
import { Badge } from '@/components/Badge';
import type { RefreshHistoryEvent } from '@/types/api';

type ActionKey = 'rankings' | 'sentiment' | 'fundamentals';

interface ActionDef {
  key: ActionKey;
  label: string;
  description: string;
  affects: string[];
  primary?: boolean;
}

const ACTIONS: ActionDef[] = [
  {
    key: 'rankings',
    label: 'Refresh All Rankings',
    description:
      'Re-runs the full ranking pipeline: refreshes routine sentiment, recomputes Technical / Momentum / Risk Penalty from IBKR bars, then writes a new SymbolRanking batch with updated composite scores.',
    affects: ['Technical', 'Momentum', 'Risk Penalty', 'Composite'],
    primary: true,
  },
  {
    key: 'sentiment',
    label: 'Refresh Sentiment',
    description:
      'Refreshes only the news / sentiment pipeline (market, sector, ticker scopes). Run this when you want updated sentiment without re-running the full ranking job.',
    affects: ['Sentiment'],
  },
  {
    key: 'fundamentals',
    label: 'Refresh Fundamentals',
    description:
      'Force-refreshes yfinance fundamentals for every verified universe symbol. The same fetch feeds both the Quality and Growth factors. Composite scores update on the next Rankings refresh.',
    affects: ['Quality', 'Growth'],
  },
];

function lastEventForAction(history: RefreshHistoryEvent[], action: string): RefreshHistoryEvent | undefined {
  return history.find((e) => e.action === action);
}

function statusBadge(status: string) {
  if (status === 'success') return <Badge variant="pos" dot>Success</Badge>;
  if (status === 'error') return <Badge variant="neg" dot>Error</Badge>;
  return <Badge variant="neutral" dot>{status}</Badge>;
}

function fmtDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m ${Math.floor((ms % 60_000) / 1000)}s`;
}

interface ActionCardProps {
  action: ActionDef;
  lastEvent?: RefreshHistoryEvent;
  inFlight: ActionKey | null;
  errorMessage: string | null;
  onRefresh: (key: ActionKey) => void;
}

function ActionCard({ action, lastEvent, inFlight, errorMessage, onRefresh }: ActionCardProps) {
  const isRunning = inFlight === action.key;
  const disabled = inFlight !== null && !isRunning;
  const showError = !!errorMessage && isRunning === false && lastEvent?.status === 'error';

  return (
    <Card>
      <CardHead
        title={action.label}
        right={
          <Button
            size="sm"
            variant={action.primary ? 'primary' : 'ghost'}
            icon={<RefreshCw size={14} />}
            loading={isRunning}
            disabled={disabled}
            onClick={() => onRefresh(action.key)}
          >
            {isRunning ? 'Running…' : 'Refresh now'}
          </Button>
        }
      />
      <CardBody>
        <p style={{ margin: 0, color: 'var(--ink-2)', fontSize: 12.5, lineHeight: 1.5 }}>
          {action.description}
        </p>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
          {action.affects.map((f) => (
            <Badge key={f} variant="info">{f}</Badge>
          ))}
        </div>
        <div
          style={{
            marginTop: 12,
            display: 'flex',
            flexWrap: 'wrap',
            gap: 16,
            alignItems: 'center',
            fontSize: 11.5,
            color: 'var(--ink-4)',
          }}
        >
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <Clock size={12} />
            {lastEvent
              ? <>Last refreshed {fmtTs(lastEvent.timestamp)}</>
              : <>Never refreshed via UI</>}
          </div>
          {lastEvent && (
            <>
              <div>{statusBadge(lastEvent.status)}</div>
              <div>Duration: {fmtDuration(lastEvent.duration_ms)}</div>
              {lastEvent.message && (
                <div className="mono" style={{ color: 'var(--ink-3)' }}>
                  {lastEvent.message}
                </div>
              )}
            </>
          )}
        </div>
        {showError && (
          <div
            style={{
              marginTop: 10,
              padding: '8px 10px',
              border: '1px solid var(--neg)',
              borderRadius: 4,
              background: 'rgba(239, 68, 68, 0.08)',
              color: 'var(--neg)',
              fontSize: 11.5,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            <AlertCircle size={13} />
            <span>{errorMessage}</span>
          </div>
        )}
        {isRunning === false && !showError && lastEvent?.status === 'success' && (
          <div
            style={{
              marginTop: 10,
              fontSize: 11.5,
              color: 'var(--pos)',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            <CheckCircle size={13} /> Previous scores preserved on failure.
          </div>
        )}
      </CardBody>
    </Card>
  );
}

export function ControlsTab() {
  const queryClient = useQueryClient();
  const { data: history = [] } = useQuery({
    queryKey: ['refreshHistory'],
    queryFn: () => api.getRefreshHistory(20),
    refetchInterval: 10_000,
  });

  const [inFlight, setInFlight] = useState<ActionKey | null>(null);
  const [errors, setErrors] = useState<Record<ActionKey, string | null>>({
    rankings: null, sentiment: null, fundamentals: null,
  });

  function clearError(key: ActionKey) {
    setErrors((prev) => ({ ...prev, [key]: null }));
  }
  function setError(key: ActionKey, msg: string) {
    setErrors((prev) => ({ ...prev, [key]: msg }));
  }

  const refreshRankings = useMutation({
    mutationFn: api.refreshRankings,
    onMutate: () => { setInFlight('rankings'); clearError('rankings'); },
    onError: (err) => setError('rankings', err instanceof Error ? err.message : 'Refresh failed'),
    onSettled: async () => {
      setInFlight(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['rankings'] }),
        queryClient.invalidateQueries({ queryKey: ['tradePlans'] }),
        queryClient.invalidateQueries({ queryKey: ['refreshHistory'] }),
      ]);
    },
  });

  const refreshSentiment = useMutation({
    mutationFn: api.refreshSentiment,
    onMutate: () => { setInFlight('sentiment'); clearError('sentiment'); },
    onError: (err) => setError('sentiment', err instanceof Error ? err.message : 'Refresh failed'),
    onSettled: async () => {
      setInFlight(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['sentiment'] }),
        queryClient.invalidateQueries({ queryKey: ['refreshHistory'] }),
      ]);
    },
  });

  const refreshFundamentals = useMutation({
    mutationFn: () => api.refreshFundamentals(),
    onMutate: () => { setInFlight('fundamentals'); clearError('fundamentals'); },
    onError: (err) => setError('fundamentals', err instanceof Error ? err.message : 'Refresh failed'),
    onSettled: async () => {
      setInFlight(null);
      await queryClient.invalidateQueries({ queryKey: ['refreshHistory'] });
    },
  });

  function handleRefresh(key: ActionKey) {
    if (inFlight !== null) return;
    if (key === 'rankings') refreshRankings.mutate();
    if (key === 'sentiment') refreshSentiment.mutate();
    if (key === 'fundamentals') refreshFundamentals.mutate();
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--gap)' }}>
      <Card>
        <CardBody>
          <div style={{ fontSize: 12.5, color: 'var(--ink-2)', lineHeight: 1.5 }}>
            Manually trigger the refresh pipelines that feed the 6-factor composite score.
            Technical, Momentum, and Risk Penalty are recomputed during every Rankings refresh
            (no separate cache); Sentiment and Fundamentals each have their own pipeline you
            can run independently. After any data refresh, click <strong>Refresh All Rankings</strong> to
            recompute composite scores.
          </div>
        </CardBody>
      </Card>

      <div className="grid-2" style={{ alignItems: 'stretch' }}>
        {ACTIONS.map((action) => (
          <ActionCard
            key={action.key}
            action={action}
            lastEvent={lastEventForAction(history, action.key)}
            inFlight={inFlight}
            errorMessage={errors[action.key]}
            onRefresh={handleRefresh}
          />
        ))}
      </div>

      <Card>
        <CardHead title="Refresh history" subtitle={`Last ${history.length} events`} />
        <CardBody flush>
          {history.length === 0 ? (
            <div style={{ padding: 24, textAlign: 'center', color: 'var(--ink-4)', fontSize: 12 }}>
              No refresh events recorded yet.
            </div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Action</th>
                  <th>Status</th>
                  <th className="num">Duration</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {history.map((row) => (
                  <tr key={row.id}>
                    <td className="mono" style={{ fontSize: 11.5 }}>{fmtTs(row.timestamp)}</td>
                    <td>{row.action}</td>
                    <td>{statusBadge(row.status)}</td>
                    <td className="num mono" style={{ fontSize: 11.5 }}>{fmtDuration(row.duration_ms)}</td>
                    <td className="mono" style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>{row.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
