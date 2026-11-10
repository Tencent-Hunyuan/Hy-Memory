import { useOnboardingTranslation } from "./onboarding-language";
import { R6Button } from "./R6Controls";

export function R6StateRecovery({
  message,
  onRetry,
  pending = false,
  role = "alert",
}: {
  message: string;
  onRetry(): void;
  pending?: boolean;
  role?: "alert" | "status";
}) {
  const { t } = useOnboardingTranslation();
  return (
    <div className="r6-state-recovery">
      <p className="r6-settings-error" role={role}>
        {message}
      </p>
      <R6Button pending={pending} onClick={onRetry}>
        {t("startupRetry")}
      </R6Button>
    </div>
  );
}
