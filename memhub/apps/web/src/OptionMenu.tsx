import { useEffect, useRef, useState } from "react";
import { useGuiLanguage } from "./gui-language";

export interface OptionMenuItem {
  value: string;
  label: string;
  disabled?: boolean;
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
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const selected = props.options.find((item) => item.value === props.value);
  useEffect(() => {
    if (!open) return;
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
  }, [open]);
  return (
    <div
      className={["om-option-menu", props.className].filter(Boolean).join(" ")}
      ref={root}
    >
      <button
        type="button"
        id={props.id}
        value={props.value}
        className="om-option-menu-button"
        aria-labelledby={props.labelledBy}
        aria-label={props.ariaLabel}
        aria-describedby={props.describedBy}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={props.disabled}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "Enter") {
            event.preventDefault();
            setOpen(true);
          }
        }}
      >
        {selected?.label ?? t("wb.select")}
      </button>
      {open && (
        <div className="om-option-menu-panel" role="listbox">
          {props.options.map((item) => (
            <button
              key={item.value || item.label}
              type="button"
              role="option"
              aria-selected={item.value === props.value}
              disabled={item.disabled}
              onClick={() => {
                if (item.disabled) return;
                props.onChange(item.value);
                setOpen(false);
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
