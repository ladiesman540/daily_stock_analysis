import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { Bookmark, RefreshCcw, Send, Users } from 'lucide-react';
import { AppPage, Badge, Button, Card, EmptyState, InlineAlert } from '../components/common';
import { researchApi, type ResearchIdea, type XStatus } from '../api/research';

const SourcesPage: React.FC = () => {
  const [xStatus, setXStatus] = useState<XStatus | null>(null);
  const [ideas, setIdeas] = useState<ResearchIdea[]>([]);
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [status, nextIdeas] = await Promise.all([
        researchApi.getXStatus(),
        researchApi.listIdeas({ limit: 60 }),
      ]);
      setXStatus(status);
      setIdeas(nextIdeas);
    } catch {
      setError('Failed to load sources.');
    }
  }, []);

  useEffect(() => {
    document.title = 'Sources - DSA';
    void load();
  }, [load]);

  const handleConnect = useCallback(async () => {
    setError(null);
    const start = await researchApi.startXOAuth();
    if (!start.configured || !start.auth_url) {
      setError(start.message || 'X OAuth is not configured');
      return;
    }
    window.location.assign(start.auth_url);
  }, []);

  const handleSync = useCallback(async () => {
    setSyncing(true);
    setError(null);
    setNotice(null);
    try {
      const result = await researchApi.syncXBookmarks(3);
      setNotice(`Synced ${result.imported} bookmark${result.imported === 1 ? '' : 's'} and ${result.mentions} mention${result.mentions === 1 ? '' : 's'}.`);
      await load();
    } catch {
      setError('X bookmark sync failed');
    } finally {
      setSyncing(false);
    }
  }, [load]);

  const handleCreate = useCallback(async () => {
    const trimmed = content.trim();
    if (!trimmed) return;
    setLoading(true);
    setError(null);
    try {
      await researchApi.createIdea({ content: trimmed, title: 'Manual idea' });
      setContent('');
      await load();
    } catch {
      setError('Failed to save the idea.');
    } finally {
      setLoading(false);
    }
  }, [content, load]);

  return (
    <AppPage>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="label-uppercase">Research</p>
          <h1 className="mt-1 text-2xl font-semibold text-foreground">Sources</h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={handleConnect}>
            <Users className="h-4 w-4" />
            Connect X
          </Button>
          <Button onClick={handleSync} isLoading={syncing} loadingText="Syncing" disabled={!xStatus?.connected}>
            <RefreshCcw className="h-4 w-4" />
            Sync bookmarks
          </Button>
        </div>
      </div>

      {error ? <InlineAlert variant="danger" title="Research error" message={error} className="mb-4" /> : null}
      {notice ? <InlineAlert variant="success" title="Sync complete" message={notice} className="mb-4" /> : null}

      <div className="mb-4 grid gap-3 md:grid-cols-3">
        <Card padding="sm" className="rounded-lg">
          <p className="text-xs uppercase tracking-normal text-secondary-text">X OAuth</p>
          <p className="mt-2 text-lg font-semibold text-foreground">{xStatus?.configured ? 'Configured' : 'Missing config'}</p>
        </Card>
        <Card padding="sm" className="rounded-lg">
          <p className="text-xs uppercase tracking-normal text-secondary-text">Accounts</p>
          <p className="mt-2 text-lg font-semibold text-foreground">{xStatus?.account_count ?? 0}</p>
        </Card>
        <Card padding="sm" className="rounded-lg">
          <p className="text-xs uppercase tracking-normal text-secondary-text">Last sync</p>
          <p className="mt-2 text-sm font-medium text-foreground">{xStatus?.last_sync_at ? new Date(xStatus.last_sync_at).toLocaleString() : 'N/A'}</p>
        </Card>
      </div>

      <Card padding="md" className="mb-5 rounded-lg">
        <div className="flex flex-col gap-3">
          <textarea
            value={content}
            onChange={(event) => setContent(event.target.value)}
            className="min-h-28 w-full resize-y rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition focus:border-cyan/60 focus:ring-4 focus:ring-cyan/10"
            placeholder="$NVDA grinding leader with AI capex catalyst"
          />
          <div className="flex justify-end">
            <Button onClick={handleCreate} disabled={!content.trim()} isLoading={loading} loadingText="Saving">
              <Send className="h-4 w-4" />
              Save idea
            </Button>
          </div>
        </div>
      </Card>

      {ideas.length === 0 ? (
        <EmptyState
          icon={<Bookmark className="h-6 w-6" />}
          title="No source items"
          description="Synced bookmarks and saved ideas appear here."
        />
      ) : (
        <div className="grid gap-3 xl:grid-cols-2">
          {ideas.map((idea) => (
            <Card key={idea.id} padding="md" className="rounded-lg">
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <Badge variant={idea.source_type === 'x_bookmark' ? 'info' : 'default'}>{idea.source_type}</Badge>
                {idea.mentions.map((mention) => (
                  <Badge key={mention.id} variant={mention.direction === 'bullish' ? 'success' : mention.direction === 'bearish' ? 'danger' : 'history'}>
                    {mention.asset_symbol} {mention.direction}
                  </Badge>
                ))}
              </div>
              <p className="line-clamp-4 text-sm leading-6 text-foreground">{idea.content}</p>
              {idea.url ? (
                <a href={idea.url} className="mt-3 inline-flex text-sm text-cyan hover:underline" target="_blank" rel="noreferrer">
                  Open source
                </a>
              ) : null}
            </Card>
          ))}
        </div>
      )}
    </AppPage>
  );
};

export default SourcesPage;
