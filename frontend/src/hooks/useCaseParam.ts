/**
 * The case behind `/cases/:ref`: a ref like INV-2026-0003 (the canonical link) is resolved through
 * GET /api/cases/by-ref/{ref}; a bare number is a case id (notification links, old bookmarks).
 */
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, unwrap } from "@/api/client";

export function useCaseParam(): { id: number | null; isLoading: boolean; error: unknown } {
  const ref = useParams().ref ?? "";
  const numeric = /^\d+$/.test(ref) ? Number(ref) : null;
  const q = useQuery({
    queryKey: ["case-ref", ref],
    queryFn: async () => unwrap(await api.GET("/api/cases/by-ref/{ref}", { params: { path: { ref } } })).id,
    enabled: numeric === null && !!ref,
    staleTime: Infinity,
  });
  if (numeric !== null) return { id: numeric, isLoading: false, error: null };
  return { id: q.data ?? null, isLoading: q.isLoading, error: q.error };
}
