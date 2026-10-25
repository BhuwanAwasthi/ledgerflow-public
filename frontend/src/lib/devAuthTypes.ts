/** The contract of the dev stub login (AUTH_MODE=dev). Implemented in devLogin.tsx; devLogin.off.ts is
 * swapped in by vite.config.ts for production builds, so none of that code ships. */
import type { ComponentType } from "react";

export interface DevSession {
  access_token: string;
  expires_at: number; // epoch ms
  user: { id: string; email: string; name: string | null };
}

export interface DevAuth {
  load(): DevSession | null;
  clear(): void;
  Form: ComponentType<{ onSignedIn: (s: DevSession) => void }>;
}
