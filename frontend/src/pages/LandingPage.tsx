/**
 * Public landing (`/`): what LedgerFlow is, and the way in. Placeholder until the product-experience
 * phase adds the live-run capture. Pings /healthz in the background so the free API host is awake by the
 * time the visitor signs in.
 */
import { ArrowRight, Bot, Scale, ShieldCheck } from "lucide-react";
import { useEffect } from "react";
import { Link } from "react-router-dom";
import { apiUrl } from "@/api/client";
import { PublicShell } from "@/components/auth/PublicShell";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { PRIVATE_ROUTES, PUBLIC_ROUTES } from "@/lib/routes";

const POINTS = [
  { icon: Bot, title: "Agents read the paperwork", text: "GPT-5.2 extracts invoices, matches vendors and purchase orders, and explains what it saw." },
  { icon: Scale, title: "Code makes the money decision", text: "Every approve, hold or reject comes from a rules engine you can read, tune and test." },
  { icon: ShieldCheck, title: "Your own private workspace", text: "Sign in with Google and get demo vendors, POs and scenarios no one else can see." },
];

export default function LandingPage() {
  const { status } = useAuth();
  useEffect(() => {
    fetch(apiUrl("/healthz"), { cache: "no-store" }).catch(() => undefined); // wake the API while they read
  }, []);
  return (
    <PublicShell wide>
      <section className="py-8 text-center sm:py-14">
        <h1 className="mx-auto max-w-2xl text-3xl font-semibold tracking-tight sm:text-4xl">
          From invoice PDF to a decision you can audit, in under a minute.
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-muted-foreground">
          LedgerFlow is an accounts-payable engine: AI agents perceive, deterministic rules decide, and a reviewer stays in control.
        </p>
        <div className="mt-8 flex justify-center">
          <Button asChild size="lg">
            {status === "signed_in" ? (
              <Link to={PRIVATE_ROUTES.launcher}>
                Open your workspace <ArrowRight />
              </Link>
            ) : (
              <Link to={PUBLIC_ROUTES.login}>
                Sign in with Google <ArrowRight />
              </Link>
            )}
          </Button>
        </div>
      </section>
      <section className="grid gap-4 sm:grid-cols-3">
        {POINTS.map((p) => (
          <div key={p.title} className="rounded-lg border bg-card p-5">
            <p.icon className="mb-3 size-5 text-primary" />
            <h2 className="text-sm font-semibold">{p.title}</h2>
            <p className="mt-1 text-sm text-muted-foreground">{p.text}</p>
          </div>
        ))}
      </section>
    </PublicShell>
  );
}
