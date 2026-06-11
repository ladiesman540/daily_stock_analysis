import type React from 'react';
import { AlertTriangle, CheckCircle2, HelpCircle, Sigma } from 'lucide-react';
import { cn } from '../../utils/cn';
import { Badge } from './Badge';

type EvidenceItem = {
  label: string;
  value: string;
  source?: string;
};

type MathItem = {
  formula: string;
  result?: string;
};

type ConfidenceItem = {
  level: string;
  reason: string;
};

export type DecisionExplanationData = {
  title?: string;
  decision: string;
  plainEnglish: string;
  evidence?: EvidenceItem[];
  math?: MathItem[];
  confidence?: ConfidenceItem;
  warnings?: string[];
  whatWouldChange?: string[];
  guardrails?: string[];
};

type DecisionExplanationProps = {
  data: DecisionExplanationData;
  className?: string;
  compact?: boolean;
};

const confidenceVariant = (level: string): 'success' | 'warning' | 'danger' | 'info' | 'default' => {
  const normalized = level.toLowerCase();
  if (normalized.includes('high')) return 'success';
  if (normalized.includes('medium')) return 'warning';
  if (normalized.includes('low')) return 'danger';
  if (normalized.includes('unknown')) return 'default';
  return 'info';
};

export const DecisionExplanation: React.FC<DecisionExplanationProps> = ({ data, className, compact = false }) => {
  const evidence = data.evidence?.filter((item) => item.label || item.value) ?? [];
  const math = data.math?.filter((item) => item.formula || item.result) ?? [];
  const warnings = data.warnings?.filter(Boolean) ?? [];
  const whatWouldChange = data.whatWouldChange?.filter(Boolean) ?? [];
  const guardrails = data.guardrails?.filter(Boolean) ?? [];

  return (
    <div className={cn('rounded-lg border border-border/60 bg-elevated/45 p-3', className)}>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-normal text-secondary-text">{data.title || 'Why this says that'}</p>
          <h3 className="mt-1 text-base font-semibold text-foreground">{data.decision}</h3>
        </div>
        {data.confidence ? (
          <Badge variant={confidenceVariant(data.confidence.level)}>{data.confidence.level} confidence</Badge>
        ) : null}
      </div>

      <p className="text-sm leading-6 text-foreground">{data.plainEnglish}</p>

      {evidence.length > 0 ? (
        <div className={cn('mt-3 grid gap-2', compact ? 'md:grid-cols-2' : 'md:grid-cols-3')}>
          {evidence.map((item) => (
            <div key={`${item.label}-${item.value}-${item.source || ''}`} className="rounded-lg border border-border/50 bg-card/60 px-3 py-2">
              <p className="text-[11px] uppercase tracking-normal text-secondary-text">{item.label}</p>
              <p className="mt-1 break-words text-sm font-semibold text-foreground">{item.value}</p>
              {item.source ? <p className="mt-1 break-words text-[11px] text-muted-foreground">{item.source}</p> : null}
            </div>
          ))}
        </div>
      ) : null}

      {math.length > 0 ? (
        <div className="mt-3 rounded-lg border border-cyan/20 bg-cyan/10 px-3 py-2">
          <div className="mb-2 flex items-center gap-2">
            <Sigma className="h-4 w-4 text-cyan" />
            <p className="text-xs uppercase tracking-normal text-cyan">Calculation</p>
          </div>
          <div className="space-y-1.5">
            {math.map((item) => (
              <div key={`${item.formula}-${item.result || ''}`} className="text-xs leading-5 text-cyan">
                <span className="font-medium">{item.formula}</span>
                {item.result ? <span className="text-cyan/80">: {item.result}</span> : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {data.confidence ? (
        <div className="mt-3 flex gap-2 rounded-lg border border-border/50 bg-card/60 px-3 py-2 text-xs leading-5 text-secondary-text">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-cyan" />
          <span>{data.confidence.reason}</span>
        </div>
      ) : null}

      {whatWouldChange.length > 0 ? (
        <ListBlock title="What would change this" icon={<HelpCircle className="h-4 w-4 text-cyan" />} items={whatWouldChange} />
      ) : null}

      {warnings.length > 0 ? (
        <ListBlock title="Warnings" icon={<AlertTriangle className="h-4 w-4 text-warning" />} items={warnings} tone="warning" />
      ) : null}

      {guardrails.length > 0 ? (
        <ListBlock title="Do not misread this" icon={<AlertTriangle className="h-4 w-4 text-warning" />} items={guardrails} tone="warning" />
      ) : null}
    </div>
  );
};

const ListBlock: React.FC<{
  title: string;
  icon: React.ReactNode;
  items: string[];
  tone?: 'default' | 'warning';
}> = ({ title, icon, items, tone = 'default' }) => (
  <div className={cn(
    'mt-3 rounded-lg border px-3 py-2',
    tone === 'warning' ? 'border-warning/25 bg-warning/10' : 'border-border/50 bg-card/60',
  )}>
    <div className="mb-2 flex items-center gap-2">
      {icon}
      <p className={cn(
        'text-xs uppercase tracking-normal',
        tone === 'warning' ? 'text-warning' : 'text-secondary-text',
      )}>
        {title}
      </p>
    </div>
    <div className="space-y-1.5">
      {items.map((item) => (
        <div key={item} className={cn(
          'flex gap-2 text-xs leading-5',
          tone === 'warning' ? 'text-warning' : 'text-secondary-text',
        )}>
          <CheckCircle2 className={cn(
            'mt-0.5 h-3.5 w-3.5 shrink-0',
            tone === 'warning' ? 'text-warning' : 'text-cyan',
          )} />
          <span>{item}</span>
        </div>
      ))}
    </div>
  </div>
);
