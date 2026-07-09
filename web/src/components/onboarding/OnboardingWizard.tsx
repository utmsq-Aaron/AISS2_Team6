// First-login onboarding wizard — shown ONCE, right after login, until the last
// step is completed or skipped (which marks onboarding_complete server-side via
// PUT /profile). Every step is skippable. This is a required-or-skip flow with
// its own internal navigation, NOT a dismissable modal: no Escape/backdrop-close.
//
// Step flow: 1 Welcome+name → 2 Photo → 3 Goals → 4 Services. Steps 1-3 both
// advance on Continue/Skip; step 4's "Skip" performs the SAME finish action as
// its primary button, since it's the last step and skipping it must still
// complete onboarding (there's nothing left to loop back to).

import { useState } from "react";

import { GoalsStep } from "./GoalsStep";
import { PhotoStep } from "./PhotoStep";
import { ServicesStep } from "./ServicesStep";
import { WelcomeStep } from "./WelcomeStep";

const TOTAL_STEPS = 4;
const STEP_LABELS = ["Welcome", "Photo", "Goals", "Services"];

export function OnboardingWizard({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState(1);

  const next = () => setStep((s) => Math.min(s + 1, TOTAL_STEPS));

  return (
    <div className="fixed inset-0 z-50 flex flex-col overflow-y-auto bg-bg-app text-text-primary">
      <div className="mx-auto flex w-full max-w-lg flex-1 flex-col justify-center px-6 py-10">
        {/* Step indicator */}
        <div className="mb-6 flex flex-col items-center gap-2">
          <div className="flex items-center gap-2">
            {Array.from({ length: TOTAL_STEPS }, (_, i) => i + 1).map((n) => (
              <span
                key={n}
                className={
                  "h-1.5 rounded-full transition-all " +
                  (n === step
                    ? "w-6 bg-accent"
                    : n < step
                      ? "w-1.5 bg-accent/50"
                      : "w-1.5 bg-border")
                }
              />
            ))}
          </div>
          <span className="fd-label">
            Step {step} of {TOTAL_STEPS} — {STEP_LABELS[step - 1]}
          </span>
        </div>

        <div className="fd-card p-6 sm:p-8">
          {step === 1 && <WelcomeStep onNext={next} />}
          {step === 2 && <PhotoStep onNext={next} />}
          {step === 3 && <GoalsStep onNext={next} />}
          {step === 4 && <ServicesStep onFinish={onDone} />}
        </div>
      </div>
    </div>
  );
}
