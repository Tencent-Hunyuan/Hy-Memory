import {
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useMemoryLanguage } from "./memory-language";
import { useMediaPreference } from "./use-media-preference";

type Phase = "closed" | "opening" | "open" | "closing";

export function MemoryEvidencePanel({
  open,
  onClose,
  children,
  suspended = false,
}: {
  open: boolean;
  onClose(): void;
  children: ReactNode;
  suspended?: boolean;
}) {
  const { text } = useMemoryLanguage();
  const compact = useMediaPreference("(max-width: 1100px)");
  const reduceMotion = useMediaPreference("(prefers-reduced-motion: reduce)");
  const reduceTransparency = useMediaPreference(
    "(prefers-reduced-transparency: reduce)",
  );
  const panel = useRef<HTMLElement>(null);
  const [phase, setPhase] = useState<Phase>(open ? "open" : "closed");
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  const snapshot = useRef<Keyframe | null>(null);
  const priorPreferences = useRef({
    compact,
    reduceMotion,
    reduceTransparency,
  });
  const present = !compact || open || phase !== "closed";
  const modal = compact && present && !suspended;

  useLayoutEffect(() => {
    const element = panel.current;
    if (!element) return;
    const previous = priorPreferences.current;
    priorPreferences.current = { compact, reduceMotion, reduceTransparency };
    const instant =
      !compact ||
      reduceMotion ||
      previous.compact !== compact ||
      previous.reduceMotion !== reduceMotion ||
      previous.reduceTransparency !== reduceTransparency ||
      document.hidden;
    const terminal = open ? "open" : "closed";
    if (
      instant ||
      !element.animate ||
      (!open && phaseRef.current === "closed")
    ) {
      setPhase(terminal);
      snapshot.current = null;
      return;
    }
    setPhase(open ? "opening" : "closing");
    if (!open) element.focus();
    const from = snapshot.current ?? {
      transform: open ? "translateX(16px)" : "none",
      opacity: open && !reduceTransparency ? 0 : 1,
    };
    const to = {
      transform: open ? "none" : "translateX(12px)",
      opacity: !open && !reduceTransparency ? 0 : 1,
    };
    const current = element.animate([from, to], {
      duration: open ? 220 : 160,
      easing: "cubic-bezier(.32,.72,0,1)",
      fill: "both",
    });
    let alive = true;
    const finish = () => {
      if (!alive) return;
      alive = false;
      snapshot.current = null;
      setPhase(terminal);
      current.cancel();
    };
    void current.finished.then(finish, (error: unknown) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      finish();
    });
    const hidden = () => {
      if (document.hidden) finish();
    };
    document.addEventListener("visibilitychange", hidden);
    return () => {
      if (alive && element.isConnected) {
        const computed = getComputedStyle(element);
        snapshot.current = {
          transform: computed.transform,
          opacity: computed.opacity,
        };
      }
      alive = false;
      current.cancel();
      document.removeEventListener("visibilitychange", hidden);
    };
    // Phase is the result of this transition, not a new animation intent.
  }, [open, compact, reduceMotion, reduceTransparency]);

  useEffect(() => {
    if (!modal || !panel.current) return;
    const element = panel.current;
    const previous = document.activeElement;
    const siblings = new Map<HTMLElement, boolean>();
    let ancestor: HTMLElement = element;
    while (ancestor.parentElement) {
      for (const sibling of ancestor.parentElement.children) {
        if (!(sibling instanceof HTMLElement) || sibling === ancestor) continue;
        siblings.set(sibling, sibling.inert);
        sibling.inert = true;
      }
      ancestor = ancestor.parentElement;
      if (ancestor === document.body) break;
    }
    element.querySelector<HTMLElement>("button:not(:disabled)")?.focus();
    return () => {
      for (const [sibling, inert] of siblings) sibling.inert = inert;
      if (previous instanceof HTMLElement && previous.isConnected)
        previous.focus();
    };
  }, [modal]);

  return (
    <aside
      ref={panel}
      className="map-inspector"
      {...(modal ? { role: "dialog", "aria-modal": !suspended } : {})}
      inert={suspended}
      aria-label={text("记忆与原文")}
      data-tour-id="memory-original-pane"
      tabIndex={-1}
      hidden={!present || (compact && suspended)}
      data-open={String(present)}
      data-phase={phase}
      onClickCapture={(event) => {
        if (compact && phase === "closing") {
          event.preventDefault();
          event.stopPropagation();
        }
      }}
      onKeyDown={(event) => {
        if (!modal || suspended || event.defaultPrevented) return;
        if (
          (event.target as HTMLElement).closest('[role="dialog"]') !==
          panel.current
        )
          return;
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          onClose();
        } else if (event.key === "Tab") {
          const controls = Array.from(
            panel.current?.querySelectorAll<HTMLElement>(
              "button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary, [tabindex='0']",
            ) ?? [],
          ).filter(
            (item) => item.getClientRects().length && !item.closest("[inert]"),
          );
          const first = controls[0],
            last = controls.at(-1);
          if (phase === "closing" || !first) {
            event.preventDefault();
            panel.current?.focus();
          } else if (
            event.shiftKey &&
            (document.activeElement === first ||
              document.activeElement === panel.current)
          ) {
            event.preventDefault();
            last?.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
          }
        }
      }}
    >
      {children}
    </aside>
  );
}
