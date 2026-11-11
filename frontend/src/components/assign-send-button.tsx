"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AlertCircle, CalendarClock, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

interface AssignClip {
  id: string;
  clip_order: number;
  hook_title: string | null;
  text: string | null;
}

interface AssignChannel {
  id: string;
  name: string;
  identifier: string;
  picture: string | null;
}

async function readError(response: Response, fallback: string): Promise<string> {
  try {
    const data = await response.json();
    return data?.detail || data?.error || fallback;
  } catch {
    return fallback;
  }
}

/**
 * "Send to Assign": uploads the clip to Assign and creates a draft per chosen
 * channel. The user then sets the time and publishes inside Assign. Renders
 * nothing unless the backend has ASSIGN_BASE_URL and ASSIGN_API_KEY set.
 */
export function AssignSendButton({ clip }: { clip: AssignClip }) {
  const [configured, setConfigured] = useState(false);
  const [open, setOpen] = useState(false);
  const [channels, setChannels] = useState<AssignChannel[] | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [caption, setCaption] = useState("");
  const [date, setDate] = useState("");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/assign/status", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!cancelled) setConfigured(data?.configured === true);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  if (!configured) return null;

  const openSheet = async () => {
    setError(null);
    setCaption(clip.hook_title ?? "");
    setDate("");
    setSelected([]);
    setOpen(true);
    setLoading(true);
    try {
      const response = await fetch("/api/assign/channels", { cache: "no-store" });
      if (!response.ok) throw new Error(await readError(response, "Could not load Assign channels."));
      const data = await response.json();
      setChannels(data.channels ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load Assign channels.");
    } finally {
      setLoading(false);
    }
  };

  const toggle = (id: string) =>
    setSelected((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));

  const submit = async () => {
    setSending(true);
    setError(null);
    try {
      const response = await fetch(`/api/assign/clips/${clip.id}/send`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          integration_ids: selected,
          caption,
          date: date ? new Date(date).toISOString() : null,
        }),
      });
      if (!response.ok) throw new Error(await readError(response, "Could not send the clip to Assign."));
      toast.success(
        selected.length === 1
          ? "Clip sent to Assign as a draft."
          : `Clip sent to Assign as ${selected.length} drafts.`,
        { description: "Open Assign to set the time and publish." },
      );
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send the clip to Assign.");
    } finally {
      setSending(false);
    }
  };

  return (
    <>
      <Button size="sm" variant="outline" onClick={openSheet}>
        <CalendarClock className="w-4 h-4" />
        Send to Assign
      </Button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="w-full sm:max-w-lg overflow-y-auto">
          <SheetHeader>
            <SheetTitle>Send clip {clip.clip_order} to Assign</SheetTitle>
            <SheetDescription>
              Uploads the video and creates a draft on each channel you pick. Set the final time and publish from Assign.
            </SheetDescription>
          </SheetHeader>

          <div className="px-4 pb-4 space-y-5">
            {error && (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            <div className="space-y-2">
              <Label>Channels</Label>
              {loading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" /> Loading channels
                </div>
              ) : channels && channels.length === 0 ? (
                <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
                  No channels are connected in Assign yet. Connect one there first.
                </div>
              ) : (
                <div className="space-y-2">
                  {(channels ?? []).map((channel) => (
                    <label
                      key={channel.id}
                      className="flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 text-sm"
                    >
                      <input
                        type="checkbox"
                        checked={selected.includes(channel.id)}
                        onChange={() => toggle(channel.id)}
                      />
                      <span className="font-medium">{channel.name}</span>
                      <span className="text-muted-foreground">{channel.identifier}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor={`assign-caption-${clip.id}`}>Caption</Label>
              <Textarea
                id={`assign-caption-${clip.id}`}
                value={caption}
                onChange={(event) => setCaption(event.target.value)}
                rows={5}
                maxLength={5000}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor={`assign-date-${clip.id}`}>Planned date (optional)</Label>
              <Input
                id={`assign-date-${clip.id}`}
                type="datetime-local"
                value={date}
                onChange={(event) => setDate(event.target.value)}
              />
            </div>
          </div>

          <SheetFooter>
            <Button onClick={submit} disabled={sending || selected.length === 0}>
              {sending && <Loader2 className="size-4 animate-spin" />}
              {sending ? "Sending" : "Send as draft"}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </>
  );
}
