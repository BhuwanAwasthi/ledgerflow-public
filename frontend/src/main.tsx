import "@fontsource-variable/inter";
import "./index.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, Outlet as RouterOutlet, RouterProvider } from "react-router-dom";
import { Toaster } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { Skeleton } from "@/components/ui/primitives";
import { TooltipProvider } from "@/components/ui/overlays";
import { PrefsProvider, usePrefs } from "@/lib/prefs";
import { homeFor } from "@/lib/nav";

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

function Home() {
  const { role } = usePrefs();
  return role === "submitter" ? <Navigate to={homeFor(role)} replace /> : <DashboardPage />;
}

const soon = (title: string, phase: string, description: string) => <ComingSoonPage title={title} phase={phase} description={description} />;

const router = createBrowserRouter([
  {
    // public vendor upload page: no app shell, no role switcher
    path: "/portal/:token",
    element: (
      <Suspense fallback={null}>
        <PortalPage />
      </Suspense>
    ),
  },
  {
    element: (
      <AppShell>
        <Suspense fallback={<PageFallback />}>
          <RouterOutlet />
        </Suspense>
      </AppShell>
    ),
    children: [
      { path: "/", element: <Home /> },
      { path: "/submit", element: <SubmitPage /> },
      { path: "/submissions", element: <MySubmissionsPage /> },
      { path: "/queue", element: <CasesPage queue /> },
      { path: "/cases", element: <CasesPage /> },
      { path: "/cases/:id/live", element: <LiveRunPage /> },
      { path: "/cases/:id", element: <Navigate to="live" replace /> },
      { path: "/cases/:id/review", element: <ReviewPage /> },
      { path: "/scenarios", element: <ScenariosPage /> },
      { path: "/library", element: <LibraryPage /> },
      { path: "/vendors", element: <VendorsPage /> },
      { path: "/vendors/:id", element: <VendorDetailPage /> },
      { path: "/pos", element: <POsPage /> },
      { path: "/pos/:id", element: <PODetailPage /> },
      { path: "/studio", element: <StudioPage /> },
      { path: "*", element: soon("Page not found", "—", "That address doesn't match a screen.") },
    ],
  },
]);

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <PrefsProvider>
        <TooltipProvider>
          <RouterProvider router={router} />
          <ThemedToaster />
        </TooltipProvider>
      </PrefsProvider>
    </QueryClientProvider>
  </StrictMode>,
);
