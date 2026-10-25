import "@fontsource-variable/inter";
import "./index.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, StrictMode, Suspense, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, Outlet as RouterOutlet, RouterProvider, useParams, type RouteObject } from "react-router-dom";
import { Toaster } from "sonner";
import { FullPageSpinner, RequireAuth } from "@/components/auth/RequireAuth";
import { AppShell } from "@/components/layout/AppShell";
import { Skeleton } from "@/components/ui/primitives";
import { TooltipProvider } from "@/components/ui/overlays";
import { AuthProvider } from "@/lib/auth";
import { PrefsProvider, usePrefs } from "@/lib/prefs";
import { fillPath, PRIVATE_ROUTES, PUBLIC_ROUTES, REDIRECTS, type PrivateRoute } from "@/lib/routes";

const LandingPage = lazy(() => import("@/pages/LandingPage"));
const LoginPage = lazy(() => import("@/pages/LoginPage"));
const AuthCallbackPage = lazy(() => import("@/pages/AuthCallbackPage"));
const LegalPage = lazy(() => import("@/pages/LegalPage"));
const DashboardPage = lazy(() => import("@/pages/DashboardPage"));
const LiveRunPage = lazy(() => import("@/pages/LiveRunPage"));
const SubmitPage = lazy(() => import("@/pages/SubmitPage"));
const MySubmissionsPage = lazy(() => import("@/pages/MySubmissionsPage"));
const ScenariosPage = lazy(() => import("@/pages/ScenariosPage"));
const CasesPage = lazy(() => import("@/pages/CasesPage"));
const ReviewPage = lazy(() => import("@/pages/ReviewPage"));
const ComingSoonPage = lazy(() => import("@/pages/ComingSoonPage"));
const LibraryPage = lazy(() => import("@/pages/LibraryPage"));
const VendorsPage = lazy(() => import("@/pages/VendorsPage").then((m) => ({ default: m.VendorsPage })));
const VendorDetailPage = lazy(() => import("@/pages/VendorsPage").then((m) => ({ default: m.VendorDetailPage })));
const POsPage = lazy(() => import("@/pages/POsPage").then((m) => ({ default: m.POsPage })));
const PODetailPage = lazy(() => import("@/pages/POsPage").then((m) => ({ default: m.PODetailPage })));
const StudioPage = lazy(() => import("@/pages/StudioPage"));
const PortalPage = lazy(() => import("@/pages/PortalPage"));

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

function PageFallback() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-7 w-48" />
      <Skeleton className="h-4 w-80" />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-24 rounded-lg" />
        ))}
      </div>
      <Skeleton className="h-72 rounded-lg" />
    </div>
  );
}

function ThemedToaster() {
  const { dark } = usePrefs();
  return (
    <Toaster
      theme={dark ? "dark" : "light"}
      richColors
      closeButton
      position="bottom-right"
      offset={96} // clear of the review workspace's sticky action bar
      toastOptions={{ classNames: { toast: "border shadow-lg", closeButton: "bg-card border text-muted-foreground" } }}
    />
  );
}

/** Old path -> new path, keeping params, query and hash. */
function Redirect({ to }: { to: string }) {
  const params = useParams();
  const target = fillPath(to, Object.fromEntries(Object.entries(params).map(([k, v]) => [k, v ?? ""])));
  return <Navigate to={{ pathname: target, search: window.location.search, hash: window.location.hash }} replace />;
}

/** /studio/rules -> /studio?tab=rules (Agent Studio keeps its tab in the query string). */
function StudioTab() {
  const { tab } = useParams();
  return <Navigate to={`${PRIVATE_ROUTES.studio}?tab=${encodeURIComponent(tab ?? "")}`} replace />;
}

const soon = (title: string, phase: string, description: string) => <ComingSoonPage title={title} phase={phase} description={description} />;

// Every signed-in screen inside the app shell. A Record over the route table, so the typechecker refuses a
// route without a screen (and e2e/auth-gate.spec.ts visits each of them signed out).
const shellPages: Record<Exclude<PrivateRoute, "portal">, ReactNode> = {
  launcher: <ScenariosPage />,
  dashboard: <DashboardPage />,
  submit: <SubmitPage />,
  submissions: <MySubmissionsPage />,
  cases: <CasesPage />,
  liveRun: <LiveRunPage />,
  review: <ReviewPage />,
  queue: <CasesPage queue />,
  approved: <LibraryPage />,
  vendors: <VendorsPage />,
  vendor: <VendorDetailPage />,
  pos: <POsPage />,
  po: <PODetailPage />,
  studio: <StudioPage />,
  studioTab: <StudioTab />,
  whatif: soon("What-if simulator", "P6", "Replay your cases against changed rules and tolerances before you save them."),
  guide: soon("Replicate this", "P7", "A step-by-step guide to running LedgerFlow in your own company."),
  settings: soon("Settings", "P4", "Your API key, mode, demo data reset and account deletion."),
  owner: soon("Owner console", "P7", "Aggregate usage and kill switches, for the site owner only."),
  notFound: <ComingSoonPage title="Page not found" description="That address doesn't match a screen." />,
};

const publicPages: Record<keyof typeof PUBLIC_ROUTES, ReactNode> = {
  landing: <LandingPage />,
  login: <LoginPage />,
  callback: <AuthCallbackPage />,
  privacy: <LegalPage kind="privacy" />,
  terms: <LegalPage kind="terms" />,
};

const routes: RouteObject[] = [
  {
    element: (
      <Suspense fallback={<FullPageSpinner label="Loading…" />}>
        <RouterOutlet />
      </Suspense>
    ),
    children: Object.entries(PUBLIC_ROUTES).map(([k, path]) => ({ path, element: publicPages[k as keyof typeof PUBLIC_ROUTES] })),
  },
  {
    // vendor upload page: signed in + token + ownership (DECISIONS: vendor portal), no app shell
    path: PRIVATE_ROUTES.portal,
    element: (
      <RequireAuth>
        <Suspense fallback={null}>
          <PortalPage />
        </Suspense>
      </RequireAuth>
    ),
  },
  {
    element: (
      <RequireAuth>
        <AppShell>
          <Suspense fallback={<PageFallback />}>
            <RouterOutlet />
          </Suspense>
        </AppShell>
      </RequireAuth>
    ),
    children: [
      ...(Object.keys(shellPages) as (keyof typeof shellPages)[]).map((k) => ({ path: PRIVATE_ROUTES[k], element: shellPages[k] })),
      ...Object.entries(REDIRECTS).map(([from, to]) => ({ path: from, element: <Redirect to={to} /> })),
    ],
  },
];

const router = createBrowserRouter(routes);

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <PrefsProvider>
        <AuthProvider>
          <TooltipProvider>
            <RouterProvider router={router} />
            <ThemedToaster />
          </TooltipProvider>
        </AuthProvider>
      </PrefsProvider>
    </QueryClientProvider>
  </StrictMode>,
);
