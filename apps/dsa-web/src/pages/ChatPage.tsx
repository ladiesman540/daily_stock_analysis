import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { cn } from '../utils/cn';
import { agentApi } from '../api/agent';
import { ApiErrorAlert, Badge, Button, ConfirmDialog, EmptyState, InlineAlert, ScrollArea, Tooltip } from '../components/common';
import { getParsedApiError } from '../api/error';
import type { SkillInfo } from '../api/agent';
import { DashboardStateBlock } from '../components/dashboard';
import {
  useAgentChatStore,
  type Message,
  type ProgressStep,
} from '../stores/agentChatStore';
import { downloadSession, formatSessionAsMarkdown } from '../utils/chatExport';
import type { ChatFollowUpContext } from '../utils/chatFollowUp';
import {
  buildFollowUpPrompt,
  parseFollowUpRecordId,
  resolveChatFollowUpContext,
  sanitizeFollowUpStockCode,
  sanitizeFollowUpStockName,
} from '../utils/chatFollowUp';
import { isNearBottom } from '../utils/chatScroll';
import { getReportText } from '../utils/reportLanguage';

// Quick question examples shown on empty state
const QUICK_QUESTIONS = [
  { label: 'AAPL trend', skill: 'bull_trend' },
  { label: 'NVDA breakout', skill: 'volume_breakout' },
  { label: 'TSLA pullback', skill: 'shrink_pullback' },
  { label: 'COIN range', skill: 'box_oscillation' },
  { label: 'AVGO leader', skill: 'dragon_head' },
  { label: 'MSTR sentiment', skill: 'emotion_cycle' },
];

const SKILL_LABELS_BY_ID: Record<string, string> = {
  bull_trend: 'Default Bull Trend',
  ma_golden_cross: 'Moving Average Golden Cross',
  volume_breakout: 'Volume Breakout',
  shrink_pullback: 'Low-Volume Pullback',
  box_oscillation: 'Range Trading',
  bottom_volume: 'Bottoming Volume',
  chan_theory: 'Chan Theory',
  wave_theory: 'Wave Theory',
  dragon_head: 'Leadership Strategy',
  emotion_cycle: 'Sentiment Cycle',
  one_yang_three_yin: 'One Strong Candle Setup',
};

const SKILL_LABELS_BY_NAME: Record<string, string> = {
  默认多头趋势: 'Default Bull Trend',
  趋势分析: 'Trend Analysis',
  均线金叉: 'Moving Average Golden Cross',
  放量突破: 'Volume Breakout',
  缩量回踩: 'Low-Volume Pullback',
  箱体震荡: 'Range Trading',
  底部放量: 'Bottoming Volume',
  缠论: 'Chan Theory',
  波浪理论: 'Wave Theory',
  龙头策略: 'Leadership Strategy',
  情绪周期: 'Sentiment Cycle',
  一阳夹三阴: 'One Strong Candle Setup',
};

const SKILL_DESCRIPTIONS_BY_ID: Record<string, string> = {
  bull_trend: 'Default stock-analysis strategy for bullish alignment, trend continuation, and pullback entries.',
  ma_golden_cross: 'Looks for moving-average crossovers confirmed by volume.',
  volume_breakout: 'Looks for high-volume breaks through resistance.',
  shrink_pullback: 'Looks for low-volume pullbacks into rising moving-average support.',
  box_oscillation: 'Looks for range-bound setups near the bottom or top of a box.',
  bottom_volume: 'Looks for high-volume basing after a prolonged decline.',
  chan_theory: 'Uses Chan structure to reason about trend level, buy/sell points, and divergence.',
  wave_theory: 'Uses Elliott Wave structure to estimate the current wave and potential target.',
  dragon_head: 'Looks for sector leaders when rotation or catalysts are emerging.',
  emotion_cycle: 'Uses market sentiment, turnover, and price-volume structure to spot fear and euphoria extremes.',
  one_yang_three_yin: 'Looks for a consolidation pattern after one strong bullish candle.',
};

const getSkillDisplayName = (id?: string, name?: string): string => {
  if (id && SKILL_LABELS_BY_ID[id]) return SKILL_LABELS_BY_ID[id];
  if (name && SKILL_LABELS_BY_NAME[name]) return SKILL_LABELS_BY_NAME[name];
  return name || id || 'General';
};

const getSkillDescription = (skill: SkillInfo): string =>
  SKILL_DESCRIPTIONS_BY_ID[skill.id] || skill.description;

const containsHanText = (value?: string | null): boolean =>
  /[\u3400-\u9fff]/.test(value || '');

// Chat content is never hidden — an untranslated reply is still the user's
// data. A one-line notice marks it; REPORT_LANGUAGE=en keeps new replies
// in English.
const UNTRANSLATED_NOTICE =
  '_(Untranslated reply — ask again and the agent will answer in English.)_\n\n';

const getVisibleMessageContent = (msg: Message): string => {
  if (containsHanText(msg.content) && msg.role === 'assistant') {
    return UNTRANSLATED_NOTICE + msg.content;
  }
  return msg.content;
};

const getVisibleSessionTitle = (title: string): string =>
  containsHanText(title) ? 'Legacy non-English chat' : title;

const getVisibleProgressText = (value?: string | null): string | null =>
  value && !containsHanText(value) ? value : null;

const ChatPage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const [input, setInput] = useState('');
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<string>('');
  const [showSkillDesc, setShowSkillDesc] = useState<string | null>(null);
  const [expandedThinking, setExpandedThinking] = useState<Set<string>>(new Set());
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const [isFollowUpContextLoading, setIsFollowUpContextLoading] = useState(false);
  const [sendToast, setSendToast] = useState<{
    type: 'success' | 'error';
    message: string;
  } | null>(null);
  const [copiedMessages, setCopiedMessages] = useState<Set<string>>(new Set());
  const [showJumpToBottom, setShowJumpToBottom] = useState(false);
  const copyResetTimerRef = useRef<Partial<Record<string, number>>>({});
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const messagesViewportRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const isMountedRef = useRef(true);
  const sendToastTimerRef = useRef<number | null>(null);
  const followUpHydrationTokenRef = useRef(0);
  const followUpContextRef = useRef<ChatFollowUpContext | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const pendingScrollBehaviorRef = useRef<ScrollBehavior>('auto');

  const text = getReportText('en');

  // Cleanup timers on unmount
  useEffect(() => {
    const timers = copyResetTimerRef.current;
    return () => {
      if (sendToastTimerRef.current !== null) {
        window.clearTimeout(sendToastTimerRef.current);
      }
      Object.values(timers).forEach((timerId) => {
        if (timerId !== undefined) {
          window.clearTimeout(timerId);
        }
      });
    };
  }, []);

  // Set page title
  useEffect(() => {
    document.title = 'Chat - DSA';
  }, []);

  useEffect(() => () => {
    isMountedRef.current = false;
  }, []);

  const {
    messages,
    loading,
    progressSteps,
    sessionId,
    sessions,
    sessionsLoading,
    chatError,
    pendingPrompt,
    loadSessions,
    loadInitialSession,
    switchSession,
    startStream,
    clearCompletionBadge,
  } = useAgentChatStore();

  // Prompt staged by another page (e.g. the Today page "Ask about today" input).
  useEffect(() => {
    if (pendingPrompt) {
      setInput(pendingPrompt);
      useAgentChatStore.getState().setPendingPrompt(null);
    }
  }, [pendingPrompt]);

  // Auto-grow the composer for typed and prefilled (handoff) text; collapses
  // back to a single row when the input is cleared after send.
  useEffect(() => {
    const textarea = composerRef.current;
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
  }, [input]);

  const syncScrollState = useCallback(() => {
    const viewport = messagesViewportRef.current;
    if (!viewport) return;
    const nearBottom = isNearBottom({
      scrollTop: viewport.scrollTop,
      clientHeight: viewport.clientHeight,
      scrollHeight: viewport.scrollHeight,
    });
    shouldStickToBottomRef.current = nearBottom;
    setShowJumpToBottom((prev) => (nearBottom ? false : prev));
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'auto') => {
    messagesEndRef.current?.scrollIntoView({ behavior });
  }, []);

  const requestScrollToBottom = useCallback((behavior: ScrollBehavior = 'auto') => {
    shouldStickToBottomRef.current = true;
    pendingScrollBehaviorRef.current = behavior;
    setShowJumpToBottom(false);
  }, []);

  const handleMessagesScroll = useCallback(() => {
    syncScrollState();
  }, [syncScrollState]);

  useEffect(() => {
    syncScrollState();
  }, [syncScrollState, sessionId]);

  useEffect(() => {
    const behavior = pendingScrollBehaviorRef.current;
    const shouldAutoScroll = shouldStickToBottomRef.current;
    if (!shouldAutoScroll) {
      if (messages.length > 0 || progressSteps.length > 0 || loading) {
        setShowJumpToBottom(true);
      }
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      scrollToBottom(behavior);
      pendingScrollBehaviorRef.current = loading ? 'auto' : 'smooth';
    });

    return () => window.cancelAnimationFrame(frame);
  }, [messages, progressSteps, loading, sessionId, scrollToBottom]);

  useEffect(() => {
    if (!loading) {
      pendingScrollBehaviorRef.current = 'smooth';
    }
  }, [loading]);

  useEffect(() => {
    clearCompletionBadge();
  }, [clearCompletionBadge]);

  useEffect(() => {
    loadInitialSession();
  }, [loadInitialSession]);

  useEffect(() => {
    agentApi.getSkills()
      .then((res) => {
        setSkills(res.skills);
        const defaultId =
          res.default_skill_id ||
          res.skills[0]?.id ||
          '';
        setSelectedSkill(defaultId);
      })
      .catch((error) => {
        console.error('Failed to load chat skills:', error);
      });
  }, []);

  const availableSkillIds = new Set(skills.map((skill) => skill.id));
  const quickQuestions = QUICK_QUESTIONS.filter((question) => availableSkillIds.size === 0 || availableSkillIds.has(question.skill));

  const handleStartNewChat = useCallback(() => {
    followUpContextRef.current = null;
    requestScrollToBottom('auto');
    useAgentChatStore.getState().startNewChat();
    setSidebarOpen(false);
  }, [requestScrollToBottom]);

  const handleSwitchSession = useCallback((targetSessionId: string) => {
    requestScrollToBottom('auto');
    switchSession(targetSessionId);
    setSidebarOpen(false);
  }, [requestScrollToBottom, switchSession]);

  const confirmDelete = useCallback(() => {
    if (!deleteConfirmId) return;
    agentApi.deleteChatSession(deleteConfirmId)
      .then(() => {
        loadSessions();
        if (deleteConfirmId === sessionId) {
          handleStartNewChat();
        }
      })
      .catch((error) => {
        console.error('Failed to delete chat session:', error);
      });
    setDeleteConfirmId(null);
  }, [deleteConfirmId, sessionId, loadSessions, handleStartNewChat]);

  // Handle follow-up from report page: ?stock=600519&name=贵州茅台&recordId=xxx
  useEffect(() => {
    const stock = sanitizeFollowUpStockCode(searchParams.get('stock'));
    const name = sanitizeFollowUpStockName(searchParams.get('name'));
    const recordId = parseFollowUpRecordId(searchParams.get('recordId'));

    if (!stock) {
      setSearchParams({}, { replace: true });
      return;
    }

    const hydrationToken = ++followUpHydrationTokenRef.current;
    setInput(buildFollowUpPrompt(stock, name));
    followUpContextRef.current = {
      stock_code: stock,
      stock_name: name,
    };
    if (recordId !== undefined) {
      setIsFollowUpContextLoading(true);
    }
    void resolveChatFollowUpContext({
      stockCode: stock,
      stockName: name,
      recordId,
    }).then((context) => {
      if (!isMountedRef.current || followUpHydrationTokenRef.current !== hydrationToken) {
        return;
      }
      followUpContextRef.current = context;
    }).finally(() => {
      if (isMountedRef.current && followUpHydrationTokenRef.current === hydrationToken) {
        setIsFollowUpContextLoading(false);
      }
    });
    setSearchParams({}, { replace: true });
  }, [searchParams, setSearchParams]);

  const handleSend = useCallback(
    async (overrideMessage?: string, overrideSkill?: string) => {
      const msgText = overrideMessage || input.trim();
      if (!msgText || loading) return;
      const usedSkill = overrideSkill || selectedSkill;
      const usedSkillName =
        getSkillDisplayName(
          usedSkill,
          skills.find((s) => s.id === usedSkill)?.name,
        );

      const payload = {
        message: msgText,
        session_id: sessionId,
        skills: usedSkill ? [usedSkill] : undefined,
        context: followUpContextRef.current ?? undefined,
      };
      followUpHydrationTokenRef.current += 1;
      followUpContextRef.current = null;
      setIsFollowUpContextLoading(false);

      setInput('');
      requestScrollToBottom('smooth');
      await startStream(payload, { skillName: usedSkillName });
    },
    [input, loading, requestScrollToBottom, selectedSkill, skills, sessionId, startStream],
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleQuickQuestion = (q: (typeof QUICK_QUESTIONS)[0]) => {
    setSelectedSkill(q.skill);
    handleSend(q.label, q.skill);
  };

  const showSendFeedback = useCallback((nextToast: { type: 'success' | 'error'; message: string }, durationMs: number) => {
    if (sendToastTimerRef.current !== null) {
      window.clearTimeout(sendToastTimerRef.current);
    }
    setSendToast(nextToast);
    sendToastTimerRef.current = window.setTimeout(() => {
      setSendToast(null);
      sendToastTimerRef.current = null;
    }, durationMs);
  }, []);

  const toggleThinking = (msgId: string) => {
    setExpandedThinking((prev) => {
      const next = new Set(prev);
      if (next.has(msgId)) next.delete(msgId);
      else next.add(msgId);
      return next;
    });
  };

  const copyMessageToClipboard = async (msgId: string, content: string) => {
    try {
      await navigator.clipboard.writeText(content);
      setCopiedMessages((prev) => new Set(prev).add(msgId));
      const existingTimer = copyResetTimerRef.current[msgId];
      if (existingTimer !== undefined) {
        window.clearTimeout(existingTimer);
      }
      copyResetTimerRef.current[msgId] = window.setTimeout(() => {
        setCopiedMessages((prev) => {
          const next = new Set(prev);
          next.delete(msgId);
          return next;
        });
        delete copyResetTimerRef.current[msgId];
      }, 2000);
    } catch (err) {
      console.error('Copy failed:', err);
    }
  };

  const downloadMessageAsMarkdown = useCallback((msg: Message) => {
    const heading = msg.role === 'user' ? '# User Message' : `# AI Reply${msg.skillName ? ` - ${msg.skillName}` : ''}`;
    const content = [heading, '', getVisibleMessageContent(msg)].join('\n');
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${msg.role === 'user' ? 'user' : 'assistant'}-message-${msg.id}.md`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  }, []);

  const getCurrentStage = (steps: ProgressStep[]): string => {
    if (steps.length === 0) return 'Connecting...';
    const last = steps[steps.length - 1];
    if (last.type === 'thinking') return getVisibleProgressText(last.message) || 'AI is thinking...';
    if (last.type === 'tool_start')
      return `${last.display_name || last.tool}...`;
    if (last.type === 'tool_done')
      return `${last.display_name || last.tool} completed`;
    if (last.type === 'generating')
      return getVisibleProgressText(last.message) || 'Generating final analysis...';
    return 'Working...';
  };

  const renderThinkingBlock = (msg: Message) => {
    if (!msg.thinkingSteps || msg.thinkingSteps.length === 0) return null;
    const isExpanded = expandedThinking.has(msg.id);
    const toolSteps = msg.thinkingSteps.filter((s) => s.type === 'tool_done');
    const totalDuration = toolSteps.reduce(
      (sum, s) => sum + (s.duration || 0),
      0,
    );
    const summary = `${toolSteps.length} tool calls - ${totalDuration.toFixed(1)}s`;

    return (
      <button
        onClick={() => toggleThinking(msg.id)}
        className="flex items-center gap-2 text-xs text-muted-text hover:text-secondary-text transition-colors mb-2 w-full text-left"
      >
        <svg
          className={`w-3 h-3 transition-transform flex-shrink-0 ${isExpanded ? 'rotate-90' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M9 5l7 7-7 7"
          />
        </svg>
        <span className="flex items-center gap-1.5">
          <span className="opacity-60">Reasoning trace</span>
          <span className="text-muted-text/50">·</span>
          <span className="opacity-50">{summary}</span>
        </span>
      </button>
    );
  };

  const renderThinkingDetails = (steps: ProgressStep[]) => (
    <div className="mb-3 pl-5 border-l border-border/40 space-y-1.5 animate-fade-in">
      {steps.map((step, idx) => {
        let statusClass = 'chat-progress-item-muted';
        let iconClass = 'chat-progress-dot-muted';
        let text = '';
        if (step.type === 'thinking') {
          text = getVisibleProgressText(step.message) || `Step ${step.step}: thinking`;
          statusClass = 'chat-progress-item-thinking';
          iconClass = 'chat-progress-dot-thinking';
        } else if (step.type === 'tool_start') {
          text = `${step.display_name || step.tool}...`;
          statusClass = 'chat-progress-item-tool';
          iconClass = 'chat-progress-dot-tool';
        } else if (step.type === 'tool_done') {
          text = `${step.display_name || step.tool} (${step.duration}s)`;
          statusClass = step.success ? 'chat-progress-item-success' : 'chat-progress-item-danger';
          iconClass = step.success ? 'chat-progress-dot-success' : 'chat-progress-dot-danger';
        } else if (step.type === 'generating') {
          text = getVisibleProgressText(step.message) || 'Generating analysis';
          statusClass = 'chat-progress-item-generating';
          iconClass = 'chat-progress-dot-generating';
        }
        return (
          <div
            key={idx}
            className={cn('chat-progress-item', statusClass)}
          >
            <span className={cn('chat-progress-dot', iconClass)} />
            <span className="leading-relaxed">{text}</span>
          </div>
        );
      })}
    </div>
  );

  const sidebarContent = (
    <>
      <div className="flex items-center justify-between border-b border-white/5 bg-white/2 p-3.5">
        <h2 className="text-sm font-semibold text-cyan uppercase tracking-[0.2em] flex items-center gap-2">
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          Chat History
        </h2>
        <button
          onClick={handleStartNewChat}
          className="rounded-lg p-1.5 text-muted-text transition-all hover:bg-white/10 hover:text-foreground"
          aria-label="Start new chat"
        >
          <svg
            className="w-4 h-4"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M12 4v16m8-8H4"
            />
          </svg>
        </button>
      </div>
      <ScrollArea testId="chat-session-list-scroll" viewportClassName="p-3">
        {sessionsLoading ? (
          <DashboardStateBlock
            loading
            compact
            title="Loading chats..."
            className="rounded-2xl border border-dashed border-border/50 bg-surface/30"
          />
        ) : sessions.length === 0 ? (
          <DashboardStateBlock
            compact
            title="No chat history"
            description="Ask a question and saved sessions will appear here."
            className="rounded-2xl border border-dashed border-border/50 bg-surface/30"
          />
        ) : (
          <div className="space-y-2">
            {sessions.map((s) => (
              <div key={s.session_id} className="session-item-row">
                <button
                  type="button"
                  onClick={() => handleSwitchSession(s.session_id)}
                  className={`session-item ${s.session_id === sessionId ? 'active' : ''}`}
                  aria-label={`Switch to chat ${getVisibleSessionTitle(s.title)}`}
                  aria-current={s.session_id === sessionId ? 'page' : undefined}
                >
                  <div className="indicator" />
                  <div className="content">
                    <span className="title">{getVisibleSessionTitle(s.title)}</span>
                    <div className="mt-0.5 flex items-center gap-2">
                      <span className="meta">
                        {s.message_count} messages
                      </span>
                      {s.last_active && (
                        <>
                          <span className="separator" />
                          <span className="meta">
                            {new Date(s.last_active).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                          </span>
                        </>
                      )}
                    </div>
                  </div>
                </button>
                <button
                  type="button"
                  className="delete-btn"
                  onClick={() => {
                    setDeleteConfirmId(s.session_id);
                  }}
                  aria-label={`Delete chat ${getVisibleSessionTitle(s.title)}`}
                >
                  <svg
                    className="w-3.5 h-3.5"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                    />
                  </svg>
                </button>
              </div>
            ))}
          </div>
        )}
      </ScrollArea>
    </>
  );

  return (
    <div
      data-testid="chat-workspace"
      className="flex h-[calc(100vh-5rem)] w-full min-w-0 gap-4 overflow-hidden sm:h-[calc(100vh-5.5rem)] lg:h-[calc(100vh-2rem)]"
    >
      {/* Desktop sidebar */}
      <div className="hidden h-full w-64 flex-shrink-0 flex-col overflow-hidden rounded-[1.25rem] border border-white/8 bg-card/82 shadow-soft-card md:flex">
        {sidebarContent}
      </div>

      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 md:hidden"
          onClick={() => setSidebarOpen(false)}
        >
          <div className="page-drawer-overlay absolute inset-0" />
          <div
            className="absolute left-0 top-0 bottom-0 w-72 flex flex-col glass-card overflow-hidden border-r border-white/10 bg-card/90 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            {sidebarContent}
          </div>
        </div>
      )}

      {/* Delete confirmation dialog */}
      <ConfirmDialog
        isOpen={Boolean(deleteConfirmId)}
        title="Delete Chat"
        message="This chat cannot be restored after deletion."
        confirmText="Delete"
        cancelText="Cancel"
        isDanger
        onConfirm={confirmDelete}
        onCancel={() => setDeleteConfirmId(null)}
      />

      {/* Main chat area */}
      <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
        <header className="mb-3 flex-shrink-0 space-y-2 md:mb-4 md:space-y-3">
          <div className="flex items-start justify-between gap-4">
            <h1 className="flex items-center gap-2 text-xl font-bold text-foreground md:text-2xl">
              <button
                onClick={() => setSidebarOpen(true)}
                className="md:hidden -ml-1 rounded-lg p-1.5 text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
                aria-label="Chat history"
              >
                <svg
                  className="w-5 h-5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M4 6h16M4 12h16M4 18h16"
                  />
                </svg>
              </button>
              <svg
                className="w-6 h-6 text-cyan"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
                />
              </svg>
              Chat
            </h1>
            {messages.length > 0 && (
              <div className="flex flex-shrink-0 flex-wrap items-center justify-end gap-2">
                <Tooltip content="Export conversation as Markdown">
                  <span className="inline-flex">
                    <Button
                      variant="action-primary"
                      size="sm"
                      onClick={() => downloadSession(messages)}
                      aria-label="Export conversation as Markdown"
                    >
                      <svg
                        className="w-4 h-4"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
                        />
                      </svg>
                      Export
                    </Button>
                  </span>
                </Tooltip>
                <Tooltip content="Send to configured notification channels">
                  <span className="inline-flex">
                    <Button
                      variant="action-primary"
                      size="sm"
                      disabled={sending}
                      onClick={async () => {
                        if (sending) return;
                        setSending(true);
                        setSendToast(null);
                        try {
                          const content = formatSessionAsMarkdown(messages);
                          await agentApi.sendChat(content);
                          showSendFeedback({ type: 'success', message: 'Sent to notification channels.' }, 3000);
                        } catch (err) {
                          const parsed = getParsedApiError(err);
                          showSendFeedback({
                            type: 'error',
                            message: parsed.message || 'Send failed',
                          }, 5000);
                        } finally {
                          setSending(false);
                        }
                      }}
                      aria-label="Send to configured notification channels"
                    >
                      {sending ? (
                        <svg
                          className="w-4 h-4 animate-spin"
                          fill="none"
                          viewBox="0 0 24 24"
                        >
                          <circle
                            className="opacity-25"
                            cx="12"
                            cy="12"
                            r="10"
                            stroke="currentColor"
                            strokeWidth="4"
                          />
                          <path
                            className="opacity-75"
                            fill="currentColor"
                            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                          />
                        </svg>
                      ) : (
                        <svg
                          className="w-4 h-4"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={2}
                            d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"
                          />
                        </svg>
                      )}
                      Send
                    </Button>
                  </span>
                </Tooltip>
              </div>
            )}
          </div>
          <p className="text-sm text-secondary-text">
            Ask the AI about tickers, trend quality, catalysts, risk, and trade planning.
          </p>
          {sendToast ? (
            <InlineAlert
              variant={sendToast.type === 'success' ? 'success' : 'danger'}
              title={sendToast.type === 'success' ? 'Sent' : 'Send Failed'}
              message={sendToast.message}
              className="max-w-md rounded-xl px-3 py-2 text-xs shadow-none"
            />
          ) : null}
        </header>

        <div className="relative z-10 flex min-h-0 flex-1 flex-col overflow-hidden border border-white/6 bg-card/78 glass-card">
          {/* Messages */}
          <ScrollArea
            className="relative z-10 flex-1"
            viewportRef={messagesViewportRef}
            onScroll={handleMessagesScroll}
            viewportClassName="space-y-4 p-3 md:space-y-6 md:p-6"
            testId="chat-message-scroll"
          >
            {messages.length === 0 && !loading ? (
              <div className="flex min-h-full items-start justify-center py-2 sm:items-center sm:py-6">
                <EmptyState
                  title="Start a Chat"
                  description={'Ask about a ticker, setup, catalyst, or risk. The AI will use market data and strategy context.'}
                  className="w-full max-w-2xl border-dashed bg-card/55 px-4 py-5 sm:px-6 sm:py-10"
                  action={(
                    <div className="grid w-full max-w-lg grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:justify-center">
                      {quickQuestions.map((q, i) => (
                        <button
                          key={i}
                          onClick={() => handleQuickQuestion(q)}
                          className="quick-question-btn"
                        >
                          {q.label}
                        </button>
                      ))}
                    </div>
                  )}
                />
              </div>
            ) : (
              messages.map((msg) => (
                <div
                  key={msg.id}
                  className={`flex gap-4 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}
                >
                  <div
                    className={cn(
                      'flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[10px] font-bold shadow-sm transition-all',
                      msg.role === 'user' ? 'chat-avatar-user' : 'chat-avatar-ai'
                    )}
                  >
                    {msg.role === 'user' ? 'U' : 'AI'}
                  </div>
                  <div
                    className={cn(
                      'group/message min-w-0 w-fit max-w-[min(100%,48rem)] overflow-hidden px-5 py-3.5 transition-colors',
                      msg.role === 'user' ? 'chat-bubble-user' : 'chat-bubble-ai'
                    )}
                  >
                    {msg.role === 'assistant' && msg.skillName && (
                      <div className="mb-2">
                        <Badge variant="info" className="chat-skill-badge shadow-none" aria-label={`Skill ${msg.skillName}`}>
                          <svg
                            className="w-3 h-3"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              strokeWidth={2}
                              d="M13 10V3L4 14h7v7l9-11h-7z"
                            />
                          </svg>
                          {msg.skillName}
                        </Badge>
                      </div>
                    )}
                    {msg.role === 'assistant' && renderThinkingBlock(msg)}
                    {msg.role === 'assistant' &&
                      expandedThinking.has(msg.id) &&
                      msg.thinkingSteps &&
                      renderThinkingDetails(msg.thinkingSteps)}
                    {msg.role === 'assistant' ? (
                      <div className="relative">
                        <div className="chat-message-actions">
                          <button
                            type="button"
                            onClick={() => copyMessageToClipboard(msg.id, getVisibleMessageContent(msg))}
                            className="chat-copy-btn"
                            aria-label={copiedMessages.has(msg.id) ? text.copied : text.copy}
                          >
                            {copiedMessages.has(msg.id) ? text.copied : text.copy}
                          </button>
                          <button
                            type="button"
                            onClick={() => downloadMessageAsMarkdown(msg)}
                            className="chat-copy-btn"
                            aria-label="Export this message as Markdown"
                          >
                            Export
                          </button>
                        </div>
                        <div className="chat-prose pr-20 sm:pr-24">
                          <Markdown remarkPlugins={[remarkGfm]}>
                            {getVisibleMessageContent(msg)}
                          </Markdown>
                        </div>
                      </div>
                    ) : (
                      getVisibleMessageContent(msg)
                        .split('\n')
                        .map((line, i) => (
                          <p
                            key={i}
                            className="mb-1 last:mb-0 leading-relaxed"
                          >
                            {line || '\u00A0'}
                          </p>
                        ))
                    )}
                  </div>
                </div>
              ))
            )}

            {loading && (
              <div className="flex gap-4">
                <div className="w-8 h-8 rounded-full bg-elevated text-foreground flex items-center justify-center flex-shrink-0 text-xs font-bold">
                  AI
                </div>
                <div className="min-w-[200px] max-w-[min(100%,48rem)] overflow-hidden rounded-2xl rounded-tl-sm border border-white/6 bg-card/72 px-5 py-4">
                  <div className="flex items-center gap-2.5 text-sm text-secondary-text">
                    <div className="relative w-4 h-4 flex-shrink-0">
                      <div className="absolute inset-0 rounded-full border-2 border-cyan/20" />
                      <div className="absolute inset-0 rounded-full border-2 border-cyan border-t-transparent animate-spin" />
                    </div>
                    <span className="text-secondary-text">
                      {getCurrentStage(progressSteps)}
                    </span>
                  </div>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </ScrollArea>

          {showJumpToBottom && (
            <div className="pointer-events-none absolute bottom-[5.75rem] right-4 z-20 md:bottom-24 md:right-6">
              <button
                type="button"
                className="pointer-events-auto chat-copy-btn shadow-soft-card"
                onClick={() => {
                  requestScrollToBottom('smooth');
                  scrollToBottom('smooth');
                }}
                aria-label="View latest messages"
              >
                <svg
                  className="h-3.5 w-3.5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M19 14l-7 7m0 0l-7-7m7 7V3"
                  />
                </svg>
                New messages
              </button>
            </div>
          )}

          {/* Input area */}
          <div className="relative z-20 border-t border-white/6 bg-card/88 p-3 md:p-6">
            <div className="space-y-3">
              {chatError ? <ApiErrorAlert error={chatError} /> : null}
              {isFollowUpContextLoading ? (
                <InlineAlert
                  variant="info"
                  title="Loading Follow-Up Context"
                  message="Loading historical analysis context. You can still send your follow-up now."
                  className="rounded-xl px-3 py-2 text-xs shadow-none"
                />
              ) : null}
            {skills.length > 0 && (
              <div className="rounded-xl border border-border/60 bg-elevated/35 p-2.5">
                <span className="mb-2 block text-xs font-medium uppercase tracking-wider text-muted-text">
                  Strategy
                </span>
                <div className="grid max-h-28 grid-cols-1 gap-2 overflow-y-auto pr-1 sm:max-h-none sm:grid-cols-2 lg:flex lg:flex-wrap">
                  <label className="group flex cursor-pointer items-center gap-1.5 rounded-lg border border-border/50 bg-card/45 px-2.5 py-2 text-sm">
                    <input
                      type="radio"
                      name="skill"
                      value=""
                      checked={selectedSkill === ''}
                      onChange={() => setSelectedSkill('')}
                      className="chat-skill-radio"
                    />
                    <span
                      className={`text-sm transition-colors ${selectedSkill === '' ? 'font-medium text-foreground' : 'text-secondary-text group-hover:text-foreground'}`}
                    >
                      General Analysis
                    </span>
                  </label>
                  {skills.map((s) => (
                    <label
                      key={s.id}
                      className="group relative flex cursor-pointer items-center gap-1.5 rounded-lg border border-border/50 bg-card/45 px-2.5 py-2"
                      onMouseEnter={() => setShowSkillDesc(s.id)}
                      onMouseLeave={() => setShowSkillDesc(null)}
                    >
                      <input
                        type="radio"
                        name="skill"
                        value={s.id}
                        checked={selectedSkill === s.id}
                        onChange={() => setSelectedSkill(s.id)}
                        className="chat-skill-radio"
                      />
                      <span
                        className={`truncate text-sm transition-colors ${selectedSkill === s.id ? 'font-medium text-foreground' : 'text-secondary-text group-hover:text-foreground'}`}
                      >
                        {getSkillDisplayName(s.id, s.name)}
                      </span>
                      {showSkillDesc === s.id && s.description && (
                        <div className="skill-desc-tooltip">
                          <p className="skill-title">{getSkillDisplayName(s.id, s.name)}</p>
                          <p>{getSkillDescription(s)}</p>
                        </div>
                      )}
                    </label>
                  ))}
                </div>
              </div>
            )}

              <div className="flex items-end gap-2 sm:gap-3">
                <textarea
                  ref={composerRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Ask a ticker question..."
                  disabled={loading}
                  rows={1}
                  className="input-surface input-focus-glow flex-1 min-h-[44px] max-h-[200px] rounded-xl border bg-transparent px-4 py-2.5 text-sm transition-all focus:outline-none resize-none disabled:cursor-not-allowed disabled:opacity-60"
                />
                <Button
                  variant="primary"
                  onClick={() => handleSend()}
                  disabled={!input.trim() || loading}
                  isLoading={loading}
                  className="btn-primary h-11 flex-shrink-0"
                >
                  Send
                </Button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ChatPage;
