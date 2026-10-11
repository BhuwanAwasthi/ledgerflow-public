import { Construction, Radio } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { EmptyState, PageHeader } from "@/components/common";
import { Button } from "@/components/ui/button";

/** Screens built in later phases (F2 review workspace, F3 library/master data/Agent Studio). */
export default function ComingSoonPage({ title, phase, description }: { title: string; phase: string; description: string }) {
  const { id } = useParams();
  return (
    <div>
      <PageHeader title={title} />
      <EmptyState
        icon={Construction}
        title={`Arrives in phase ${phase}`}
        action={
          id ? (
            <Button size="sm" variant="outline" asChild>
              <Link to={`/cases/${id}/live`}>
                <Radio /> Open the live run instead
              </Link>
            </Button>
          ) : undefined
        }
      >
        {description}
      </EmptyState>
    </div>
  );
}
