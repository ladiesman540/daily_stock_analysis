import type React from 'react';
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Info } from 'lucide-react';
import { GLOSSARY } from '../../utils/glossary';
import { cn } from '../../utils/cn';

/** Module-level single-open enforcement: opening one InfoTip closes the previous one. */
let closeActiveInfoTip: (() => void) | null = null;

interface InfoTipProps {
  /** Glossary key (utils/glossary.ts). Takes precedence unless title/body are given. */
  term?: string;
  /** Explicit content, used when no glossary term fits. */
  title?: string;
  body?: string;
  computedFrom?: string;
  /** Render a small info glyph instead of underlining children. */
  icon?: boolean;
  children?: React.ReactNode;
  className?: string;
}

type PopoverPosition = { top: number; left: number };

/**
 * Tappable term explainer: dotted-underline text (or a small info glyph) that
 * opens a popover with a plain-English definition and an optional
 * "Computed from" line. Hover-opens on pointer devices, click-toggles on touch.
 */
export const InfoTip: React.FC<InfoTipProps> = ({
  term,
  title,
  body,
  computedFrom,
  icon = false,
  children,
  className = '',
}) => {
  const entry = term ? GLOSSARY[term] : undefined;
  const resolvedTitle = title ?? entry?.title;
  const resolvedBody = body ?? entry?.body;
  const resolvedComputedFrom = computedFrom ?? entry?.computedFrom;

  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const popoverId = useId();
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<PopoverPosition>({ top: 0, left: 0 });
  // Below lg the popover must stay under the fixed mobile nav buttons (z-40),
  // but inside dialogs/drawers (z up to 100, which cover the nav anyway) it
  // still has to stack above the dialog surface.
  const [popoverAboveNav, setPopoverAboveNav] = useState(true);
  // Hover-capable devices open on hover; touch devices rely on tap.
  const hoverableRef = useRef(
    typeof window !== 'undefined' && window.matchMedia('(hover: hover) and (pointer: fine)').matches,
  );

  const close = useCallback(() => {
    setOpen(false);
  }, []);

  const openPopover = useCallback(() => {
    if (closeActiveInfoTip && closeActiveInfoTip !== close) {
      closeActiveInfoTip();
    }
    closeActiveInfoTip = close;
    setPopoverAboveNav(
      window.matchMedia('(min-width: 1024px)').matches ||
        Boolean(triggerRef.current?.closest('[role="dialog"]')),
    );
    setOpen(true);
  }, [close]);

  // Position above the trigger, flipping below when there is no room; clamp to viewport.
  const updatePosition = useCallback(() => {
    const trigger = triggerRef.current;
    const popover = popoverRef.current;
    if (!trigger || !popover) return;

    const triggerRect = trigger.getBoundingClientRect();
    const popoverRect = popover.getBoundingClientRect();
    const gap = 8;
    const margin = 8;

    let top = triggerRect.top - popoverRect.height - gap;
    if (top < margin) {
      top = triggerRect.bottom + gap;
    }

    let left = triggerRect.left + triggerRect.width / 2 - popoverRect.width / 2;
    left = Math.max(margin, Math.min(left, window.innerWidth - popoverRect.width - margin));
    top = Math.max(margin, Math.min(top, window.innerHeight - popoverRect.height - margin));

    setPosition({ top, left });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    const frameId = window.requestAnimationFrame(updatePosition);
    return () => window.cancelAnimationFrame(frameId);
  }, [open, updatePosition]);

  useEffect(() => {
    if (!open) return;

    const handleViewportChange = () => updatePosition();
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (triggerRef.current?.contains(target) || popoverRef.current?.contains(target)) {
        return;
      }
      close();
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };

    window.addEventListener('resize', handleViewportChange);
    window.addEventListener('scroll', handleViewportChange, true);
    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('resize', handleViewportChange);
      window.removeEventListener('scroll', handleViewportChange, true);
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open, close, updatePosition]);

  // Clear the module-level handle when this tip closes or unmounts.
  useEffect(() => {
    if (!open) return;
    return () => {
      if (closeActiveInfoTip === close) {
        closeActiveInfoTip = null;
      }
    };
  }, [open, close]);

  if (!resolvedTitle || !resolvedBody) {
    return <>{children}</>;
  }

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={open}
        aria-describedby={open ? popoverId : undefined}
        aria-label={icon && !children ? `About ${resolvedTitle}` : undefined}
        onClick={() => (open ? close() : openPopover())}
        onMouseEnter={() => {
          if (hoverableRef.current) openPopover();
        }}
        onMouseLeave={() => {
          if (hoverableRef.current) close();
        }}
        className={cn(
          // p-2/-m-2 widens the touch target to ~36px without affecting layout.
          'inline-flex cursor-help items-center gap-0.5 border-0 bg-transparent -m-2 p-2 text-left align-baseline text-inherit',
          !icon && 'underline decoration-muted-text/70 decoration-dotted underline-offset-2',
          className,
        )}
      >
        {children}
        {icon ? <Info aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-text" /> : null}
      </button>

      {typeof document !== 'undefined' && open
        ? createPortal(
            <div
              ref={popoverRef}
              id={popoverId}
              role="tooltip"
              style={{ position: 'fixed', top: position.top, left: position.left }}
              className={cn(
                popoverAboveNav ? 'z-[120]' : 'z-30',
                'max-w-72 rounded-xl border border-border/70 bg-elevated/95 px-3 py-2 text-left shadow-[0_16px_40px_rgba(3,8,20,0.18)] backdrop-blur-xl',
              )}
            >
              <p className="text-xs font-semibold leading-5 text-foreground">{resolvedTitle}</p>
              <p className="mt-0.5 text-xs leading-5 text-secondary-text">{resolvedBody}</p>
              {resolvedComputedFrom ? (
                <p className="mt-1 text-[11px] leading-4 text-muted-text">
                  Computed from: {resolvedComputedFrom}
                </p>
              ) : null}
            </div>,
            document.body,
          )
        : null}
    </>
  );
};
