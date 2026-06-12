import React, { useState } from 'react';
import { motion } from 'motion/react';
import { BarChart3, BookOpenCheck, BriefcaseBusiness, Crosshair, DatabaseZap, LogOut, MessageSquareQuote, Newspaper, Search, Settings2, Sunrise, Target, TrendingUp, Users } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { useAgentChatStore } from '../../stores/agentChatStore';
import { cn } from '../../utils/cn';
import { ConfirmDialog } from '../common/ConfirmDialog';
import { StatusDot } from '../common/StatusDot';
import { ThemeToggle } from '../theme/ThemeToggle';

type SidebarNavProps = {
  collapsed?: boolean;
  onNavigate?: () => void;
};

type NavItem = {
  key: string;
  label: string;
  to: string;
  icon: React.ComponentType<{ className?: string }>;
  exact?: boolean;
  badge?: 'completion';
};

const NAV_ITEMS: NavItem[] = [
  { key: 'today', label: 'Today', to: '/', icon: Sunrise, exact: true },
  { key: 'analyze', label: 'Analyze', to: '/analyze', icon: Search },
  { key: 'guide', label: 'Guide', to: '/guide', icon: BookOpenCheck },
  { key: 'chat', label: 'Chat', to: '/chat', icon: MessageSquareQuote, badge: 'completion' },
  { key: 'signals', label: 'Signals', to: '/signals', icon: TrendingUp },
  { key: 'data', label: 'Data', to: '/data', icon: DatabaseZap },
  { key: 'rotation', label: 'Rotation', to: '/rotation', icon: Newspaper },
  { key: 'positioning', label: 'Positioning', to: '/positioning', icon: Crosshair },
  { key: 'sources', label: 'Sources', to: '/sources', icon: Users },
  { key: 'portfolio', label: 'Portfolio', to: '/portfolio', icon: BriefcaseBusiness },
  { key: 'backtest', label: 'Backtest', to: '/backtest', icon: BarChart3 },
  { key: 'scorecard', label: 'Scorecard', to: '/scorecard', icon: Target },
  { key: 'settings', label: 'Settings', to: '/settings', icon: Settings2 },
];

export const SidebarNav: React.FC<SidebarNavProps> = ({ collapsed = false, onNavigate }) => {
  const { authEnabled, logout } = useAuth();
  const completionBadge = useAgentChatStore((state) => state.completionBadge);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);

  return (
    <div className="flex h-full flex-col">
      <div className={cn('mb-5 flex items-center gap-3 px-1', collapsed ? 'justify-center' : '')}>
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-gradient text-[hsl(var(--primary-foreground))] shadow-[0_10px_22px_var(--nav-brand-shadow)]">
          <BarChart3 className="h-5 w-5" />
        </div>
        {!collapsed ? (
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-foreground">DSA</p>
            <p className="truncate text-[11px] text-secondary-text">Signal workspace</p>
          </div>
        ) : null}
      </div>

      <nav className="flex flex-1 flex-col gap-1.5" aria-label="Primary navigation">
        {NAV_ITEMS.map(({ key, label, to, icon: Icon, exact, badge }) => (
          <NavLink
            key={key}
            to={to}
            end={exact}
            onClick={onNavigate}
            aria-label={label}
            className={({ isActive }) =>
              cn(
                'group relative flex items-center gap-2.5 rounded-xl border text-sm transition-all',
                'h-[var(--nav-item-height)]',
                collapsed ? 'justify-center px-0' : 'px-3',
                isActive
                  ? 'border-[var(--nav-active-border)] bg-[var(--nav-active-bg)] text-[hsl(var(--primary))] font-semibold shadow-[0_8px_20px_var(--nav-active-shadow)]'
                  : 'border-transparent text-secondary-text hover:border-border/80 hover:bg-[var(--nav-hover-bg)] hover:text-foreground'
              )
            }
          >
            {({ isActive }) => (
              <>
                {isActive && (
                  <motion.div 
                    layoutId="activeIndicator"
                    className="absolute bottom-2 left-1 top-2 w-[var(--nav-indicator-width)] rounded-full bg-[var(--nav-indicator-bg)]"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ duration: 0.2 }}
                  />
                )}
                <Icon className={cn('h-5 w-5 shrink-0', isActive ? 'text-[var(--nav-icon-active)]' : 'text-current')} />
                {!collapsed ? <span className="truncate">{label}</span> : null}
                {badge === 'completion' && completionBadge ? (
                  <StatusDot
                    tone="info"
                    data-testid="chat-completion-badge"
                    className={cn(
                      'absolute right-3 border-2 border-background shadow-[0_0_10px_var(--nav-indicator-shadow)]',
                      collapsed ? 'right-2 top-2' : ''
                    )}
                    aria-label="Chat has a new message"
                  />
                ) : null}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="mt-4 mb-2">
        <ThemeToggle variant="nav" collapsed={collapsed} />
      </div>

      {authEnabled ? (
        <button
          type="button"
          onClick={() => setShowLogoutConfirm(true)}
          className={cn(
            'mt-5 flex h-11 w-full cursor-pointer select-none items-center gap-3 rounded-xl border border-transparent px-3 text-sm text-secondary-text transition-all hover:border-border/80 hover:bg-hover hover:text-foreground',
            collapsed ? 'justify-center px-2' : ''
          )}
        >
          <LogOut className="h-5 w-5 shrink-0" />
          {!collapsed ? <span>Log out</span> : null}
        </button>
      ) : null}

      <ConfirmDialog
        isOpen={showLogoutConfirm}
        title="Log out"
        message="Log out of the current session? You will need to enter the password again."
        confirmText="Log out"
        cancelText="Cancel"
        isDanger
        onConfirm={() => {
          setShowLogoutConfirm(false);
          onNavigate?.();
          void logout();
        }}
        onCancel={() => setShowLogoutConfirm(false)}
      />
    </div>
  );
};
