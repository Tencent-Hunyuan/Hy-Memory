import { Check, ChevronDown, Clock3 } from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useOnboardingTranslation } from "./onboarding-language";
import { R6Button } from "./R6Controls";

const HOURS = Array.from({ length: 24 }, (_, value) =>
  value.toString().padStart(2, "0"),
);
const MINUTES = Array.from({ length: 60 }, (_, value) =>
  value.toString().padStart(2, "0"),
);

function splitTime(value: string): [string, string] {
  const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(value);
  return match ? [match[1], match[2]] : ["00", "00"];
}

export function TimePicker({
  id,
  label,
  value,
  disabled = false,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  disabled?: boolean;
  onChange(value: string): Promise<boolean>;
}) {
  const { locale, text } = useOnboardingTranslation();
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [placement, setPlacement] = useState<"above" | "below">("above");
  const [hour, setHour] = useState(() => splitTime(value)[0]);
  const [minute, setMinute] = useState(() => splitTime(value)[1]);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLElement>(null);
  const hourSelect = useRef<HTMLSelectElement>(null);
  const minuteSelect = useRef<HTMLSelectElement>(null);
  const restoreTriggerFocus = useRef(false);
  const restoreSaveFocus = useRef(false);
  const savingRef = useRef(false);
  savingRef.current = saving;
  const panelId = `${id}-picker`;

  const close = useCallback((restoreFocus = true) => {
    setOpen(false);
    restoreTriggerFocus.current = restoreFocus;
  }, []);

  function show() {
    const [nextHour, nextMinute] = splitTime(value);
    setHour(nextHour);
    setMinute(nextMinute);
    setOpen(true);
  }

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: MouseEvent) => {
      if (savingRef.current) return;
      const target = event.target as Element | null;
      const label = target?.closest("label");
      if (label instanceof HTMLLabelElement && label.htmlFor === id) return;
      if (!root.current?.contains(target)) close(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || savingRef.current) return;
      event.preventDefault();
      close();
    };
    document.addEventListener("mousedown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    hourSelect.current?.focus();
    hourSelect.current?.selectedOptions[0]?.scrollIntoView?.({
      block: "nearest",
    });
    minuteSelect.current?.selectedOptions[0]?.scrollIntoView?.({
      block: "nearest",
    });
    return () => {
      document.removeEventListener("mousedown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [close, id, open]);

  useEffect(() => {
    if (disabled && open && !saving) close(false);
    if (!disabled && !open && restoreTriggerFocus.current) {
      restoreTriggerFocus.current = false;
      trigger.current?.focus();
    }
    if (!disabled && open && restoreSaveFocus.current) {
      restoreSaveFocus.current = false;
      panel.current
        ?.querySelector<HTMLButtonElement>("[data-time-picker-save]")
        ?.focus();
    }
    if (saving && open) panel.current?.focus();
  }, [close, disabled, open, saving]);

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const anchor = trigger.current?.getBoundingClientRect();
      const picker = panel.current?.getBoundingClientRect();
      if (!anchor || !picker) return;
      const scroller = trigger.current?.closest(
        ".app-content, .onboarding-frame",
      );
      const bounds = scroller?.getBoundingClientRect();
      const topEdge = (bounds ? Math.max(0, bounds.top) : 0) + 12;
      const bottomEdge =
        (bounds
          ? Math.min(window.innerHeight, bounds.bottom)
          : window.innerHeight) - 12;
      const above = anchor.top - topEdge;
      const below = bottomEdge - anchor.bottom;
      setPlacement(
        above >= picker.height || above >= below ? "above" : "below",
      );
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open]);

  async function save() {
    const next = `${hour}:${minute}`;
    if (next === value) {
      close();
      return;
    }
    setSaving(true);
    const saved = await onChange(next);
    setSaving(false);
    if (saved) close();
    else restoreSaveFocus.current = true;
  }

  return (
    <div className="r6-time-picker" ref={root}>
      <button
        ref={trigger}
        id={id}
        type="button"
        className="r6-time-picker-trigger"
        aria-label={
          locale.startsWith("zh") ? `${label}，${value}` : `${label}: ${value}`
        }
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={panelId}
        disabled={disabled}
        onClick={() => (open ? close() : show())}
      >
        <span>{value}</span>
        <span className="r6-time-picker-icons" aria-hidden="true">
          <Clock3 size={15} />
          <ChevronDown size={14} />
        </span>
      </button>
      {open ? (
        <section
          ref={panel}
          id={panelId}
          className="r6-time-picker-panel"
          role="dialog"
          aria-modal="true"
          aria-label={label}
          tabIndex={-1}
          data-placement={placement}
          onKeyDown={(event) => {
            if (event.key !== "Tab") return;
            const controls = Array.from(
              event.currentTarget.querySelectorAll<HTMLElement>(
                "select:not(:disabled), button:not(:disabled)",
              ),
            );
            const first = controls[0];
            const last = controls.at(-1);
            if (!first || !last) {
              event.preventDefault();
              event.currentTarget.focus();
              return;
            }
            if (event.shiftKey && document.activeElement === first) {
              event.preventDefault();
              last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
              event.preventDefault();
              first.focus();
            }
          }}
        >
          <div className="r6-time-picker-columns">
            <label className="r6-time-picker-column">
              <span aria-hidden="true">HH</span>
              <select
                ref={hourSelect}
                size={6}
                aria-label={`${label} HH`}
                value={hour}
                disabled={saving}
                onChange={(event) => setHour(event.target.value)}
              >
                {HOURS.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
            <span className="r6-time-picker-separator" aria-hidden="true">
              :
            </span>
            <label className="r6-time-picker-column">
              <span aria-hidden="true">MM</span>
              <select
                ref={minuteSelect}
                size={6}
                aria-label={`${label} MM`}
                value={minute}
                disabled={saving}
                onChange={(event) => setMinute(event.target.value)}
              >
                {MINUTES.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <footer>
            <R6Button disabled={saving} onClick={() => close()}>
              {text("取消")}
            </R6Button>
            <R6Button
              variant="primary"
              pending={saving}
              data-time-picker-save
              onClick={() => void save()}
            >
              <Check size={15} aria-hidden="true" />
              {text("保存修改")}
            </R6Button>
          </footer>
        </section>
      ) : null}
    </div>
  );
}
