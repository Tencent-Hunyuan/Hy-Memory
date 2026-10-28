import {
  type CSSProperties,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { useGuiLanguage } from "./gui-language";

export interface OptionMenuItem {
  value: string;
  label: string;
  disabled?: boolean;
  lang?: string;
}

const VIEWPORT_MARGIN = 12;
const PANEL_GAP = 4;
const MAX_PANEL_HEIGHT = 280;

export function optionMenuPosition(
  anchor: Pick<DOMRect, "top" | "right" | "bottom" | "left" | "width">,
  panelHeight: number,
  viewportWidth: number,
  viewportHeight: number,
) {
  const width = Math.min(
    anchor.width,
    Math.max(0, viewportWidth - VIEWPORT_MARGIN * 2),
  );
  const left = Math.min(
    Math.max(VIEWPORT_MARGIN, anchor.left),
    Math.max(VIEWPORT_MARGIN, viewportWidth - VIEWPORT_MARGIN - width),
  );
  const below = Math.max(
    0,
    viewportHeight - VIEWPORT_MARGIN - anchor.bottom - PANEL_GAP,
  );
  const above = Math.max(0, anchor.top - VIEWPORT_MARGIN - PANEL_GAP);
  const placement: "above" | "below" =
    below >= Math.min(panelHeight, 120) || below >= above ? "below" : "above";
  const maxHeight = Math.min(
    MAX_PANEL_HEIGHT,
    placement === "below" ? below : above,
  );
  const height = Math.min(panelHeight, maxHeight);
  const top =
    placement === "below"
      ? anchor.bottom + PANEL_GAP
      : anchor.top - PANEL_GAP - height;
  return { top, left, width, maxHeight, placement };
}

/** Replaces a native select so the open list follows the product field, not the OS menu. */
export function OptionMenu(props: {
  id?: string;
  labelledBy?: string;
  ariaLabel?: string;
  describedBy?: string;
  value: string;
  disabled?: boolean;
  className?: string;
  panelClassName?: string;
  options: readonly OptionMenuItem[];
  onChange: (value: string) => void;
}) {
  const { t } = useGuiLanguage();
  const generatedId = useId();
  const [open, setOpen] = useState(false);
  const selectedIndex = props.options.findIndex(
    (item) => item.value === props.value,
  );
  const firstEnabledIndex = props.options.findIndex((item) => !item.disabled);
  const preferredIndex =
    selectedIndex >= 0 && !props.options[selectedIndex]?.disabled
      ? selectedIndex
      : firstEnabledIndex;
  const [activeIndex, setActiveIndex] = useState(preferredIndex);
  const [panelStyle, setPanelStyle] = useState<CSSProperties>({
    visibility: "hidden",
  });
  const [placement, setPlacement] = useState<"above" | "below">("below");
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const panelMouseDown = useRef(false);
  const selected = props.options.find((item) => item.value === props.value);
  const visibleLabel = selected?.label ?? t("wb.select");
  const controlId = props.id ?? `${generatedId}-control`;
  const panelId = `${controlId}-options`;

  function enabledIndex(from: number, direction: 1 | -1) {
    if (firstEnabledIndex < 0) return -1;
    let next = from;
    for (let checked = 0; checked < props.options.length; checked += 1) {
      next =
        next < 0
          ? direction === 1
            ? 0
            : props.options.length - 1
          : (next + direction + props.options.length) % props.options.length;
      if (!props.options[next]?.disabled) return next;
    }
    return -1;
  }

  function choose(index: number) {
    const item = props.options[index];
    if (!item || item.disabled) return;
    props.onChange(item.value);
    setOpen(false);
    button.current?.focus();
  }

  const placePanel = useCallback(() => {
    const anchor = button.current?.getBoundingClientRect();
    const menu = panel.current;
    if (!anchor || !menu) return;
    if (
      anchor.width > 0 &&
      (anchor.bottom <= 0 ||
        anchor.top >= window.innerHeight ||
        anchor.right <= 0 ||
        anchor.left >= window.innerWidth)
    ) {
      setOpen(false);
      return;
    }
    const measuredWidth = Math.min(
      anchor.width,
      Math.max(0, window.innerWidth - VIEWPORT_MARGIN * 2),
    );
    menu.style.width = `${measuredWidth}px`;
    menu.style.maxHeight = `${MAX_PANEL_HEIGHT}px`;
    const next = optionMenuPosition(
      anchor,
      menu.scrollHeight,
      window.innerWidth,
      window.innerHeight,
    );
    setPlacement(next.placement);
    setPanelStyle({
      top: next.top,
      left: next.left,
      width: next.width,
      maxHeight: next.maxHeight,
      visibility: "visible",
    });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    placePanel();
    window.addEventListener("resize", placePanel);
    window.addEventListener("scroll", placePanel, true);
    return () => {
      window.removeEventListener("resize", placePanel);
      window.removeEventListener("scroll", placePanel, true);
    };
  }, [open, placePanel]);

  useEffect(() => {
    if (!open) return;
    setActiveIndex(preferredIndex);
    const close = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!root.current?.contains(target) && !panel.current?.contains(target))
        setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    const releasePanelPointer = () => {
      panelMouseDown.current = false;
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("mouseup", releasePanelPointer);
    document.addEventListener("keydown", onKey);
    window.addEventListener("blur", releasePanelPointer);
    return () => {
      panelMouseDown.current = false;
      document.removeEventListener("mousedown", close);
      document.removeEventListener("mouseup", releasePanelPointer);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("blur", releasePanelPointer);
    };
  }, [open, preferredIndex]);
  return (
    <div
      className={["om-option-menu", props.className].filter(Boolean).join(" ")}
      ref={root}
    >
      <button
        ref={button}
        type="button"
        id={controlId}
        value={props.value}
        className="om-option-menu-button"
        role="combobox"
        aria-labelledby={props.labelledBy}
        aria-label={
          props.ariaLabel ?? (props.labelledBy ? undefined : t("wb.select"))
        }
        aria-describedby={props.describedBy}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={panelId}
        aria-activedescendant={
          open && activeIndex >= 0
            ? `${panelId}-option-${activeIndex}`
            : undefined
        }
        disabled={props.disabled}
        onBlur={(event) => {
          if (panelMouseDown.current) return;
          const target = event.relatedTarget as Node | null;
          if (
            !root.current?.contains(target) &&
            !panel.current?.contains(target)
          )
            setOpen(false);
        }}
        onClick={() => {
          if (preferredIndex < 0) return;
          setActiveIndex(preferredIndex);
          setOpen((current) => !current);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            if (!open) {
              if (preferredIndex >= 0) {
                setActiveIndex(preferredIndex);
                setOpen(true);
              }
            } else {
              setActiveIndex((current) => enabledIndex(current, 1));
            }
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            if (!open) {
              if (preferredIndex >= 0) {
                setActiveIndex(preferredIndex);
                setOpen(true);
              }
            } else {
              setActiveIndex((current) => enabledIndex(current, -1));
            }
          } else if (event.key === "Home" && open) {
            event.preventDefault();
            setActiveIndex(enabledIndex(-1, 1));
          } else if (event.key === "End" && open) {
            event.preventDefault();
            setActiveIndex(enabledIndex(0, -1));
          } else if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            if (open) choose(activeIndex);
            else if (preferredIndex >= 0) {
              setActiveIndex(preferredIndex);
              setOpen(true);
            }
          }
        }}
      >
        {visibleLabel}
      </button>
      {open &&
        createPortal(
          <div
            ref={panel}
            id={panelId}
            className={["om-option-menu-panel", props.panelClassName]
              .filter(Boolean)
              .join(" ")}
            role="listbox"
            data-placement={placement}
            style={panelStyle}
            onMouseDownCapture={() => {
              panelMouseDown.current = true;
            }}
          >
            {props.options.map((item, index) => (
              <button
                key={item.value || item.label}
                id={`${panelId}-option-${index}`}
                type="button"
                role="option"
                lang={item.lang}
                data-active={activeIndex === index || undefined}
                aria-selected={item.value === props.value}
                disabled={item.disabled}
                tabIndex={-1}
                onMouseEnter={() => {
                  if (!item.disabled) setActiveIndex(index);
                }}
                onClick={() => choose(index)}
              >
                {item.label}
              </button>
            ))}
          </div>,
          document.body,
        )}
    </div>
  );
}
