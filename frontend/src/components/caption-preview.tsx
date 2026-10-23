"use client";

import { useState } from "react";

import { cn } from "@/lib/utils";

// Rendered by backend/scripts/render_caption_previews.py with the real caption
// renderer, so the picture matches the clip.
export function CaptionStylePreview({ templateId, fallbackColor, className }: {
  templateId: string;
  fallbackColor?: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);

  if (failed) {
    return <span className="size-3 shrink-0 rounded-full border" style={{ backgroundColor: fallbackColor || "#fff" }} />;
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- small static preview
    <img
      src={`/caption-previews/${templateId}.jpg`}
      alt=""
      loading="lazy"
      width={84}
      height={36}
      onError={() => setFailed(true)}
      className={cn("h-9 w-[84px] shrink-0 rounded-md border bg-muted object-cover", className)}
    />
  );
}
