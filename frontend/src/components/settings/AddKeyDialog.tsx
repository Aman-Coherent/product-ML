"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";

function countKeys(raw: string): number {
  return raw
    .split(/[,\n]+/)
    .map((k) => k.trim())
    .filter(Boolean).length;
}

const PROVIDERS = [
  { id: "groq", label: "Groq" },
  { id: "mistral", label: "Mistral" },
  { id: "jina", label: "Jina Reader" },
  { id: "claude", label: "Anthropic Claude" },
  { id: "openai", label: "OpenAI" },
  { id: "custom", label: "Custom (OpenAI-compatible)" },
];

export function AddKeyDialog({ token, onAdded }: { token: string | null; onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const [provider, setProvider] = useState("groq");
  const [label, setLabel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [modelName, setModelName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [loading, setLoading] = useState(false);

  const needsModel = provider === "claude" || provider === "openai" || provider === "custom";
  const needsBaseUrl = provider === "custom";
  const keyCount = countKeys(apiKey);

  async function handleSubmit() {
    if (!token) {
      toast.error("Your session isn't ready yet. Please wait a moment and try again, or sign out and back in.");
      return;
    }
    if (!label.trim() || keyCount === 0) return;
    setLoading(true);
    try {
      const created = await api.addApiKeysBulk(token, {
        provider,
        label: label.trim(),
        api_keys: apiKey,
        model_name: modelName.trim() || undefined,
        base_url: baseUrl.trim() || undefined,
      });
      toast.success(
        created.length > 1 ? `${created.length} API keys added (${created.map((k) => k.label).join(", ")})` : "API key added"
      );
      setOpen(false);
      setLabel("");
      setApiKey("");
      setModelName("");
      setBaseUrl("");
      onAdded();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to add key");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button size="sm">
            <Plus className="mr-1.5 size-4" /> Add API Key
          </Button>
        }
      />
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add an API key</DialogTitle>
          <DialogDescription>
            Your key is encrypted at rest and only used for your own jobs. Adding extra Groq/Mistral
            keys increases your throughput; adding Claude/OpenAI enables the higher-quality fallback
            tier. Paste multiple keys separated by commas (or one per line) to add them all at once —
            they&apos;ll be auto-labeled &quot;Label 1&quot;, &quot;Label 2&quot;, etc.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label>Provider</Label>
            <Select value={provider} onValueChange={(value) => value && setProvider(value)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PROVIDERS.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="label">Label</Label>
            <Input
              id="label"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Personal Groq key"
            />
            {keyCount > 1 && (
              <p className="text-xs text-muted-foreground">
                Will be saved as {Array.from({ length: keyCount }, (_, i) => `"${label.trim() || "Key"} ${i + 1}"`).join(", ")}
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="apiKey">
              API Key{keyCount > 1 ? "s" : ""}
              {keyCount > 0 && <span className="ml-1.5 font-normal text-muted-foreground">({keyCount})</span>}
            </Label>
            <Textarea
              id="apiKey"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={"sk-...\n\nPaste multiple keys separated by commas or new lines to add them all at once."}
              rows={3}
              // The base Textarea uses `field-sizing-content`, which sizes the
              // box to fit a single long unbroken line (e.g. several comma
              // -joined keys with no spaces) instead of wrapping it — that
              // blows the box (and the whole dialog) far past its max-width.
              // Force fixed sizing + wrapping so long pastes wrap inside the box.
              className="field-sizing-fixed resize-y break-all font-mono text-sm"
            />
          </div>

          {needsModel && (
            <div className="space-y-2">
              <Label htmlFor="modelName">Model name</Label>
              <Input
                id="modelName"
                value={modelName}
                onChange={(e) => setModelName(e.target.value)}
                placeholder={provider === "claude" ? "claude-3-5-sonnet-latest" : "gpt-4o-mini"}
              />
            </div>
          )}

          {needsBaseUrl && (
            <div className="space-y-2">
              <Label htmlFor="baseUrl">Base URL</Label>
              <Input
                id="baseUrl"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://api.your-provider.com/v1"
              />
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={loading || !label.trim() || keyCount === 0}>
            {loading ? "Adding..." : keyCount > 1 ? `Add ${keyCount} keys` : "Add key"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
