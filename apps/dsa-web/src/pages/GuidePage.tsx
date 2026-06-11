import type React from 'react';
import { useEffect } from 'react';
import { BookOpenCheck, CheckCircle2, Compass, Crosshair, Newspaper, TrendingUp } from 'lucide-react';
import { AppPage, Badge, Card } from '../components/common';

const WORKFLOW = [
  {
    title: 'Start with Rotation',
    text: 'Find the themes getting attention. This tells you where to hunt, not what to buy.',
    icon: Newspaper,
  },
  {
    title: 'Use Signals for ranking',
    text: 'Look for pass or watchlist names with strong trend, liquidity, and a clear reason they are moving.',
    icon: TrendingUp,
  },
  {
    title: 'Check Positioning before entry',
    text: 'Look for crowding, gamma levels, short pressure, and data gaps before deciding size or timing.',
    icon: Crosshair,
  },
  {
    title: 'Write the trade down',
    text: 'Before entering, know the thesis, entry zone, invalidation, target, and what would prove you wrong.',
    icon: Compass,
  },
];

const DEFINITIONS = [
  ['Bias', 'The app’s plain-English read of positioning. It is a setup filter, not a buy or sell command.'],
  ['Candidate score', 'A ranking score. Higher means the idea passed more of the app’s trend, catalyst, liquidity, and risk checks.'],
  ['Pass', 'The idea cleared the main checklist gates and deserves deeper work.'],
  ['Watchlist', 'The idea is interesting but still needs better confirmation, timing, or data.'],
  ['Reject', 'The idea failed an important gate. It can still move, but the setup is not clean by this system.'],
  ['Crowding risk', 'How packed the trade may be. High crowding means many people may already be on the same side.'],
  ['Gamma regime', 'Whether options hedging is likely to calm price movement or amplify it.'],
  ['Positive gamma', 'A regime where hedging may dampen moves. Price can chop or pin near large options levels.'],
  ['Negative gamma', 'A regime where hedging may chase moves. Breakouts and breakdowns can travel faster.'],
  ['Gamma flip', 'A level where the options hedging regime may change. Treat it as an area, not an exact magic line.'],
  ['Call wall', 'A strike with heavy call exposure. It can act like resistance until price accepts above it.'],
  ['Put wall', 'A strike with heavy put exposure. It can act like support until price loses it.'],
  ['Put/call ratio', 'A rough way to see whether options positioning leans toward calls or puts. Very low can mean call crowding.'],
  ['Short float', 'The percentage of tradable shares reported short. High short float can create squeeze risk.'],
  ['Days to cover', 'How many trading days it could take shorts to cover based on average volume. Higher can mean more squeeze fuel.'],
  ['Fails to deliver', 'Shares that were not delivered on settlement. Elevated FTDs can point to settlement stress, but they are not automatically bullish.'],
  ['COT', 'Commitments of Traders data. It shows futures positioning by participant group and is useful for macro context.'],
  ['Source coverage', 'Which data sources were usable. Missing sources lower trust in the read.'],
  ['Data gaps', 'The app telling you what it could not see. Read this before trusting any signal.'],
  ['Confidence', 'Data-quality confidence. It does not mean the app is certain about the future.'],
];

const RULES = [
  'A bullish read with high crowding is not automatically good. It may already be obvious to everyone.',
  'A low confidence read should be treated as a research prompt, not a signal.',
  'Gamma levels are areas to watch. Do not enter just because price touched one.',
  'Position sizing should shrink when crowding, data gaps, or volatility are high.',
  'The app is decision support. Your job is still to decide if the risk/reward is worth it.',
];

const GuidePage: React.FC = () => {
  useEffect(() => {
    document.title = 'Guide - DSA';
  }, []);

  return (
    <AppPage>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="label-uppercase">Guide</p>
          <h1 className="mt-1 text-2xl font-semibold text-foreground">Plain-English Trading Guide</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-secondary-text">
            Use this page when the app says something like bullish, crowded, negative gamma, or watchlist and you want the normal-human meaning.
          </p>
        </div>
        <Badge variant="info" size="md">decision support only</Badge>
      </div>

      <section className="mb-4 grid gap-3 xl:grid-cols-4">
        {WORKFLOW.map(({ title, text, icon: Icon }) => (
          <Card key={title} padding="md" className="rounded-lg">
            <Icon className="h-5 w-5 text-cyan" />
            <h2 className="mt-3 text-base font-semibold text-foreground">{title}</h2>
            <p className="mt-2 text-sm leading-6 text-secondary-text">{text}</p>
          </Card>
        ))}
      </section>

      <div className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
        <Card title="Definitions" subtitle="Vocabulary" padding="md" className="rounded-lg">
          <div className="grid gap-2 md:grid-cols-2">
            {DEFINITIONS.map(([term, definition]) => (
              <DefinitionRow key={term} term={term} definition={definition} />
            ))}
          </div>
        </Card>

        <div className="space-y-4">
          <Card title="How To Use The App" subtitle="Workflow" padding="md" className="rounded-lg">
            <ol className="space-y-3 text-sm leading-6 text-secondary-text">
              <li><span className="font-medium text-foreground">1.</span> Use Rotation to choose the strongest themes.</li>
              <li><span className="font-medium text-foreground">2.</span> Use Signals to rank individual names.</li>
              <li><span className="font-medium text-foreground">3.</span> Use Positioning to check whether the trade is too crowded.</li>
              <li><span className="font-medium text-foreground">4.</span> Use Sources to track which people or bookmarks actually add value.</li>
              <li><span className="font-medium text-foreground">5.</span> Use Chat for follow-up questions after the data is loaded.</li>
            </ol>
          </Card>

          <Card title="Product Rule" subtitle="No naked verdicts" padding="md" className="rounded-lg">
            <div className="space-y-3 text-sm leading-6 text-secondary-text">
              <div className="flex gap-2">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-cyan" />
                <span>Every important decision must show the decision, why, data used, math, confidence, warnings, and what would change the answer.</span>
              </div>
              <div className="flex gap-2">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-cyan" />
                <span>If the app says bullish, crowded, neutral, pass, watchlist, or reject, it must show its work right next to the claim.</span>
              </div>
              <div className="flex gap-2">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-cyan" />
                <span>If the data is fallback, stale, capped, estimated, or missing, confidence must be downgraded instead of hidden.</span>
              </div>
            </div>
          </Card>

          <Card title="Rules That Keep You Alive" subtitle="Risk" padding="md" className="rounded-lg">
            <div className="space-y-3">
              {RULES.map((rule) => (
                <div key={rule} className="flex gap-2 text-sm leading-6 text-secondary-text">
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-cyan" />
                  <span>{rule}</span>
                </div>
              ))}
            </div>
          </Card>

          <Card title="The One Sentence Version" padding="md" className="rounded-lg">
            <div className="flex gap-3">
              <BookOpenCheck className="mt-0.5 h-5 w-5 shrink-0 text-cyan" />
              <p className="text-sm leading-6 text-foreground">
                Hunt where the strongest themes are, rank the best names, check whether everyone is already piled in, then define your risk before you touch the trade.
              </p>
            </div>
          </Card>
        </div>
      </div>
    </AppPage>
  );
};

const DefinitionRow: React.FC<{ term: string; definition: string }> = ({ term, definition }) => (
  <div className="rounded-lg border border-border/60 bg-elevated/45 p-3">
    <p className="text-sm font-semibold text-foreground">{term}</p>
    <p className="mt-1 text-xs leading-5 text-secondary-text">{definition}</p>
  </div>
);

export default GuidePage;
