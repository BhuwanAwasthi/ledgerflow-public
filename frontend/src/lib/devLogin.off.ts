/** Production builds: no stub login (vite.config.ts aliases "@/lib/devLogin" here). */
import type { DevAuth } from "./devAuthTypes";

export const devAuth: DevAuth | null = null;
