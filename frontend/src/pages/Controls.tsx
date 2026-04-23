import { useEffect, useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useBotStore } from '@/store/botStore';
import { Card, CardHead, CardBody } from '@/components/Card';
import { Button } from '@/components/Button';
import { Badge } from '@/components/Badge';
import { queryClient } from '@/lib/queryClient';

interface ControlCardProps {
  title: string;
  description: string;
  active: boolean;
  activeLabel: string;
  inactiveLabel: string;
  activateAction: string;
  deactivateAction: string;
  activateVariant?: 'success' | 'danger' | 'warn';
  deactivateVariant?: 'ghost' | 'danger' | 'warn';
  activeVariant?: 'pos' | 'neg' | 'warn';
}

function ControlCard({
  title, description, active,
  activeLabel, inactiveLabel,
  activateAction, deactivateAction,
  activateVariant = 'success',
  deactivateVariant = 'ghost',
  activeVariant = 'pos',
}: ControlCardProps) {
  const setBot = useBotStore((s) => s.setBot);

  const mutate = useMutation({
    mutationFn: (action: string) => api.postControl(action),
    onSuccess: (data) => {
      setBot(data.bot);
      queryClient.invalidateQueries({ queryKey: ['overview'] });
    },
  });

  return (
    <Card>
      <CardHead
        title={title}
        right={<Badge variant={active ? activeVariant : 'neutral'} dot>{active ? activeLabel : inactiveLabel}</Badge>}
      />
      <CardBody>
        <p style={{ fontSize: 12.5, color: 'var(--ink-4)', marginBottom: 14, marginTop: 0 }}>{description}</p>
        <div style={{ display: 'flex', gap: 8 }}>
          <Button
            variant={activateVariant}
            disabled={active}
            loading={mutate.isPending}
            onClick={() => mutate.mutate(activateAction)}
            aria-label={`Enable ${title}`}
          >
            {activateAction.includes('on') || activateAction.includes('enable') || activateAction.includes('resume') ? 'Enable' : 'Activate'}
          </Button>
          <Button
            variant={deactivateVariant as 'ghost'}
            disabled={!active}
            loading={mutate.isPending}
            onClick={() => mutate.mutate(deactivateAction)}
            aria-label={`Disable ${title}`}
          >
            {deactivateAction.includes('off') || deactivateAction.includes('disable') || deactivateAction.includes('pause') ? 'Disable' : 'Deactivate'}
          </Button>
        </div>
        {mutate.isError && (
          <p style={{ marginTop: 8, fontSize: 12, color: 'var(--neg)' }}>
            {String((mutate.error as Error).message)}
          </p>
        )}
      </CardBody>
    </Card>
  );
}

export function Controls() {
  const { data, isLoading } = useQuery({ queryKey: ['overview'], queryFn: api.getOverview, refetchInterval: 10_000 });
  const setBot = useBotStore((s) => s.setBot);
  const bot = useBotStore((s) => s.bot);
  const [closeConfirm, setCloseConfirm] = useState(false);

  useEffect(() => { if (data?.bot) setBot(data.bot); }, [data?.bot, setBot]);

  const closeAll = useMutation({
    mutationFn: () => api.postControl('close_all'),
    onSuccess: (data) => { setBot(data.bot); setCloseConfirm(false); },
  });

  if (isLoading && !bot) return <div className="loading-state">Loading controls…</div>;

  const b = bot ?? data?.bot;
  if (!b) return null;

  return (
    <div>
      <h1 className="page-title">Controls</h1>

      <div className="grid-2">
        <ControlCard
          title="Trading"
          description="Pause or resume the trading engine. Pausing prevents new orders but does not close existing positions."
          active={!b.paused}
          activeLabel="Running"
          inactiveLabel="Paused"
          activateAction="resume"
          deactivateAction="pause"
          activateVariant="success"
          deactivateVariant="warn"
          activeVariant="pos"
        />
        <ControlCard
          title="Kill Switch"
          description="Emergency stop. Activating the kill switch halts all trading immediately and signals the bot to close positions."
          active={b.kill_switch}
          activeLabel="ACTIVE"
          inactiveLabel="Off"
          activateAction="kill/on"
          deactivateAction="kill/off"
          activateVariant="danger"
          deactivateVariant="ghost"
          activeVariant="neg"
        />
        <ControlCard
          title="Options Trading"
          description="Enable or disable the OptionsSwingBot. When disabled, no new options debit spreads will be placed."
          active={b.options_enabled}
          activeLabel="Enabled"
          inactiveLabel="Disabled"
          activateAction="options/enable"
          deactivateAction="options/disable"
          activateVariant="success"
          deactivateVariant="warn"
          activeVariant="pos"
        />
        <ControlCard
          title="Approve Mode"
          description="When on, all signals are saved as pending_approval and must be manually approved before orders are submitted."
          active={b.approve_mode}
          activeLabel="On"
          inactiveLabel="Off"
          activateAction="approve_mode/on"
          deactivateAction="approve_mode/off"
          activateVariant="warn"
          deactivateVariant="ghost"
          activeVariant="warn"
        />
      </div>

      <Card style={{ borderColor: 'var(--neg-bg)' }}>
        <CardHead title="Close All Positions" right={<Badge variant="neg">Destructive</Badge>} />
        <CardBody>
          <p style={{ fontSize: 12.5, color: 'var(--ink-4)', marginBottom: 14, marginTop: 0 }}>
            Activates the kill switch and signals the bot to close all open positions.
            This action cannot be undone automatically — confirm before proceeding.
          </p>
          {!closeConfirm ? (
            <Button variant="danger" onClick={() => setCloseConfirm(true)}>
              Close All Positions
            </Button>
          ) : (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <Button
                variant="danger"
                loading={closeAll.isPending}
                onClick={() => closeAll.mutate()}
              >
                Confirm — Close All
              </Button>
              <Button variant="ghost" onClick={() => setCloseConfirm(false)}>Cancel</Button>
            </div>
          )}
          {closeAll.isError && (
            <p style={{ marginTop: 8, fontSize: 12, color: 'var(--neg)' }}>
              {String((closeAll.error as Error).message)}
            </p>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
