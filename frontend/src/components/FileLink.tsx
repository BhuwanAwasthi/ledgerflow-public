/**
 * A link to a file route (template, ZIP, audit pack, CSV, fixture PDF). A plain href can't authenticate,
 * so a click asks for a 60-second file token first and follows that URL instead.
 * Works inside <Button asChild> (forwards the ref and props to the anchor).
 */
import { forwardRef, type AnchorHTMLAttributes } from "react";
import { toast } from "sonner";
import { apiUrl, downloadFile } from "@/api/client";

type Props = AnchorHTMLAttributes<HTMLAnchorElement> & { path: string };

export const FileLink = forwardRef<HTMLAnchorElement, Props>(({ path, onClick, children, ...props }, ref) => (
  <a
    ref={ref}
    href={apiUrl(path)}
    {...props}
    onClick={(e) => {
      onClick?.(e);
      if (e.defaultPrevented || e.button !== 0) return;
      e.preventDefault();
      downloadFile(path).catch((err: unknown) => toast.error(err instanceof Error ? err.message : "Download failed"));
    }}
  >
    {children}
  </a>
));
FileLink.displayName = "FileLink";
