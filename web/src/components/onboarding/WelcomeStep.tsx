// Step 1 — friendly greeting + name capture. "Continue" writes the name (if any)
// then advances; an empty name is treated the same as Skip (nothing to write).

import { Dumbbell } from "lucide-react";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { putProfile, setAthleteProfile } from "../../lib/api";
import { ErrorBox } from "../Spinner";

export function WelcomeStep({ onNext }: { onNext: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [age, setAge] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleContinue() {
    const trimmed = name.trim();
    const ageNum = parseInt(age, 10) || 0;
    if (!trimmed && !ageNum) {
      onNext();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (trimmed) await putProfile({ name: trimmed });
      if (ageNum > 0) await setAthleteProfile(ageNum);
      qc.invalidateQueries({ queryKey: ["profile"] });
      onNext();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save your details.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-center text-center">
      <span className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-accent/15 text-accent">
        <Dumbbell size={28} strokeWidth={2} />
      </span>
      <h1 className="text-xl font-semibold text-text-primary">Hey there, welcome to FitDash!</h1>
      <p className="mt-2 max-w-sm text-sm text-text-muted">
        I&apos;m your training buddy — I&apos;ll help you keep tabs on your training, cheer you on,
        and nudge you when it matters. Let&apos;s get you set up, it only takes a minute (and you
        can skip anything).
      </p>

      <div className="mt-6 w-full text-left">
        <label className="fd-label" htmlFor="onboarding-name">
          What should I call you?
        </label>
        <input
          id="onboarding-name"
          autoFocus
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleContinue();
          }}
          placeholder="Your first name"
          className="fd-input mt-1 w-full"
          disabled={busy}
        />

        <label className="fd-label mt-4 block" htmlFor="onboarding-age">
          Your age <span className="font-normal normal-case tracking-normal text-text-faint">— sets your default heart-rate zones</span>
        </label>
        <input
          id="onboarding-age"
          type="number" min={10} max={100}
          value={age}
          onChange={(e) => { setAge(e.target.value); setError(null); }}
          onKeyDown={(e) => { if (e.key === "Enter") handleContinue(); }}
          placeholder="e.g. 30"
          className="fd-input mt-1 w-full"
          disabled={busy}
        />
        {error && <div className="mt-2"><ErrorBox message={error} /></div>}
      </div>

      <div className="mt-6 flex w-full gap-3">
        <button type="button" className="fd-btn-secondary flex-1" onClick={onNext} disabled={busy}>
          Skip
        </button>
        <button type="button" className="fd-btn-primary flex-1" onClick={handleContinue} disabled={busy}>
          {busy ? "Saving…" : "Continue"}
        </button>
      </div>
    </div>
  );
}
