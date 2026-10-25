/** /privacy and /terms: public, required by Google's OAuth consent screen. */
import { PublicShell } from "@/components/auth/PublicShell";

const PRIVACY = [
  ["What we receive", "Your name, email address and profile picture from Google when you sign in. Nothing else from your Google account."],
  ["What we store", "Your workspace: the demo data you change, invoices you upload, runs, reviews and exports. If you add your own model API key, only its encrypted form is stored, plus its last four characters so you can recognise it."],
  ["Who can see it", "Only you. Every record is tied to your account and every request is checked against it. There are no shared workspaces."],
  ["Retention", "Workspaces inactive for 30 days are deleted automatically. You can delete your key or your whole account at any time in Settings."],
  ["Processors", "Supabase (sign-in, database, file storage), Render (API hosting) and Vercel (this website). With your own key, the model provider you chose processes the invoices you submit."],
] as const;

const TERMS = [
  ["What this is", "A portfolio demonstration of an accounts-payable engine, provided as is, without warranty and without uptime guarantees."],
  ["Your use", "Don't upload documents you aren't allowed to share, real personal data, or anything unlawful. Usage limits apply to keep the free hosting available to everyone."],
  ["Your key", "Calls made with your own model key are billed to your account with that provider. Remove the key whenever you like."],
  ["Changes", "The demo can be reset, changed or taken offline at any time."],
] as const;

export default function LegalPage({ kind }: { kind: "privacy" | "terms" }) {
  const sections = kind === "privacy" ? PRIVACY : TERMS;
  return (
    <PublicShell>
      <article className="space-y-6">
        <h1 className="text-xl font-semibold tracking-tight">{kind === "privacy" ? "Privacy" : "Terms of use"}</h1>
        {sections.map(([title, text]) => (
          <section key={title}>
            <h2 className="text-sm font-semibold">{title}</h2>
            <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{text}</p>
          </section>
        ))}
      </article>
    </PublicShell>
  );
}
