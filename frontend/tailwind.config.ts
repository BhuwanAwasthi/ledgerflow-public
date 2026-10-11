import animate from "tailwindcss-animate";
import type { Config } from "tailwindcss";

const v = (name: string) => `hsl(var(--${name}) / <alpha-value>)`;

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    container: { center: true, padding: "1.5rem" },
    extend: {
      fontFamily: {
        sans: ['"Inter Variable"', "Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"Cascadia Mono"', "Consolas", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      colors: {
        border: v("border"),
        input: v("input"),
        ring: v("ring"),
        background: v("background"),
        foreground: v("foreground"),
        surface: v("surface"),
        primary: { DEFAULT: v("primary"), foreground: v("primary-foreground") },
        muted: { DEFAULT: v("muted"), foreground: v("muted-foreground") },
        accent: { DEFAULT: v("accent"), foreground: v("accent-foreground") },
        card: { DEFAULT: v("card"), foreground: v("card-foreground") },
        popover: { DEFAULT: v("popover"), foreground: v("popover-foreground") },
        destructive: { DEFAULT: v("destructive"), foreground: v("destructive-foreground") },
      },
      borderRadius: { lg: "var(--radius)", md: "calc(var(--radius) - 2px)", sm: "calc(var(--radius) - 4px)" },
      keyframes: {
        "pulse-ring": {
          "0%": { boxShadow: "0 0 0 0 hsl(var(--ring) / 0.45)" },
          "70%": { boxShadow: "0 0 0 8px hsl(var(--ring) / 0)" },
          "100%": { boxShadow: "0 0 0 0 hsl(var(--ring) / 0)" },
        },
        shimmer: { "100%": { transform: "translateX(100%)" } },
        "slide-in": { from: { opacity: "0", transform: "translateY(6px)" }, to: { opacity: "1", transform: "none" } },
      },
      animation: {
        "pulse-ring": "pulse-ring 1.6s ease-out infinite",
        "slide-in": "slide-in 240ms ease-out both",
      },
    },
  },
  plugins: [animate],
} satisfies Config;
