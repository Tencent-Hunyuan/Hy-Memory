import type { ReactNode } from "react";
import { LanguageSelector } from "./LanguageSelector";
import { R6LocalStorage, R6Stepper } from "./R6Controls";
import "./onboarding-r6.css";

export function R6ColdStartFrame({
  step,
  title,
  eyebrow,
  children,
  footer,
}: {
  step: 1 | 2 | 3;
  title: string;
  eyebrow: string;
  children: ReactNode;
  footer: ReactNode;
}) {
  return (
    <main className="onboarding-r6" aria-label={title}>
      {/* The source (143:3007 / 143:3008) puts a mark + "MemHub" on the
          left of this bar and a "第一次见面" caption on the right. The user
          removed both deliberately on 2026-09-20 — this is a product decision,
          not a missing implementation, so do not "restore" them when comparing
          against the canvas. The bar itself stays: it is the window drag
          region (at least 58px) and reserves room for native traffic lights.
          The language control opts out of dragging. */}
      <header className="onboarding-r6-titlebar">
        <LanguageSelector compact />
      </header>
      <div className="onboarding-r6-stage">
        <section className="onboarding-r6-shell" data-step={step}>
          <header className="onboarding-r6-head">
            <div>
              <p>{eyebrow}</p>
              <h1>{title}</h1>
            </div>
            <R6Stepper active={step} />
          </header>
          <div className="onboarding-r6-body">{children}</div>
          <footer className="onboarding-r6-foot">{footer}</footer>
        </section>
      </div>
      <footer className="onboarding-r6-local-footer">
        <R6LocalStorage />
      </footer>
    </main>
  );
}
