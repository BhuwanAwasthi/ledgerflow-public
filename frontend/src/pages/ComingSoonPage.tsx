import { Construction, PlayCircle } from "lucide-react";
import { Link } from "react-router-dom";
import { EmptyState, PageHeader } from "@/components/common";
import { Button } from "@/components/ui/button";
import { PRIVATE_ROUTES } from "@/lib/routes";

/** Screens built in later phases, and the signed-in "page not found". */
export default function ComingSoonPage({ title, phase, description }: { title: string; phase?: string; description: string }) {
  return (
    <div>
      <PageHeader title={title} />
      <EmptyState
        icon={Construction}
        title={phase ? `Arrives in phase ${phase}` : title}
        action={
          <Button size="sm" variant="outline" asChild>
            <Link to={PRIVATE_ROUTES.launcher}>
              <PlayCircle /> Go to the scenario launcher
            </Link>
          </Button>
        }
      >
        {description}
      </EmptyState>
    </div>
  );
}
