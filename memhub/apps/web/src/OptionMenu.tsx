import { useEffect, useId, useRef, useState } from "react";
import { useGuiLanguage } from "./gui-language";

export interface OptionMenuItem {
  value: string;
  label: string;
  disabled?: boolean;
  lang?: string;
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
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
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

  useEffect(() => {
    if (!open) return;
    setActiveIndex(preferredIndex);
    const close = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", onKey);
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
          if (!root.current?.contains(event.relatedTarget as Node | null))
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
      {open && (
        <div id={panelId} className="om-option-menu-panel" role="listbox">
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
        </div>
      )}
    </div>
  );
}
