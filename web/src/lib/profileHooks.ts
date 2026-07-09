import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchAvatarBlob, getProfile } from "./api";

/** Profile (name + has_avatar) shared with the onboarding gate/wizard via the
 *  same `["profile"]` query key — one source of truth that invalidates together. */
export function useProfile() {
  return useQuery({ queryKey: ["profile"], queryFn: getProfile });
}

/** Object URL for the user's avatar image, fetched only when `has_avatar` is
 *  true. Revokes the previous URL on cleanup/unmount or when `has_avatar` flips. */
export function useAvatarUrl(hasAvatar: boolean): string | null {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!hasAvatar) {
      setUrl(null);
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    fetchAvatarBlob().then((blob) => {
      if (cancelled || !blob) return;
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
    });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [hasAvatar]);

  return url;
}
