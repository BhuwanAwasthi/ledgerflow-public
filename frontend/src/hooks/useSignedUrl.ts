/**
 * A fresh 60-second file-token URL for `path`, fetched once per mount (react-pdf and <img> load it
 * straight away; a remount later asks for a new one, so an expired token is never reused).
 */
import { useEffect, useState } from "react";
import { signedFileUrl } from "@/api/client";

export function useSignedUrl(path: string | null): { url: string | null; error: unknown } {
  const [state, setState] = useState<{ path: string | null; url: string | null; error: unknown }>({ path: null, url: null, error: null });
  useEffect(() => {
    if (!path) return;
    let alive = true;
    signedFileUrl(path).then(
      (url) => alive && setState({ path, url, error: null }),
      (error: unknown) => alive && setState({ path, url: null, error }),
    );
    return () => {
      alive = false;
    };
  }, [path]);
  return state.path === path ? { url: state.url, error: state.error } : { url: null, error: null };
}
