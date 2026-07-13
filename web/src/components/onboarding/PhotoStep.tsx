// Step 2 — profile photo upload with a local preview (object URL) before commit.
// "Upload & Continue" uploads then advances; "Skip" advances without uploading.
// Upload errors (wrong type / too large — enforced server-side) surface inline.

import { Camera, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { uploadAvatar } from "../../lib/api";
import { ErrorBox } from "../Spinner";

const ACCEPTED = "image/jpeg,image/png,image/webp";

export function PhotoStep({ onNext }: { onNext: () => void }) {
  const qc = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Create/revoke an object URL for the local preview whenever the selected file changes.
  useEffect(() => {
    if (!file) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  function handlePick(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    setError(null);
    if (f) setFile(f);
  }

  async function handleUpload() {
    if (!file) {
      onNext();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await uploadAvatar(file);
      qc.invalidateQueries({ queryKey: ["profile"] });
      onNext();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not upload your photo.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-center text-center">
      <h1 className="text-xl font-semibold text-text-primary">Add a profile photo?</h1>
      <p className="mt-2 max-w-sm text-sm text-text-muted">
        Totally optional — but it makes the dashboard feel a little more like yours.
      </p>

      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        aria-label="Choose a photo"
        className="fd-card-hover mt-6 flex h-32 w-32 items-center justify-center overflow-hidden rounded-full border border-border bg-bg-surface text-text-muted transition-colors hover:border-accent/50"
      >
        {previewUrl ? (
          <img src={previewUrl} alt="Preview" className="h-full w-full object-cover" />
        ) : (
          <Camera size={32} strokeWidth={1.5} />
        )}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED}
        onChange={handlePick}
        aria-label="Profile photo file"
        className="hidden"
      />
      <button
        type="button"
        className="mt-3 text-xs text-accent hover:underline"
        onClick={() => inputRef.current?.click()}
      >
        {file ? "Choose a different photo" : "Choose a photo"}
      </button>

      {error && <div className="mt-3 w-full"><ErrorBox message={error} /></div>}

      <div className="mt-6 flex w-full gap-3">
        <button type="button" className="fd-btn-secondary flex-1" onClick={onNext} disabled={busy}>
          Skip
        </button>
        <button
          type="button"
          className="fd-btn-primary inline-flex flex-1 items-center justify-center gap-1.5"
          onClick={handleUpload}
          disabled={busy}
        >
          {file && <Upload size={15} strokeWidth={2} />}
          {busy ? "Uploading…" : file ? "Upload & Continue" : "Continue"}
        </button>
      </div>
    </div>
  );
}
