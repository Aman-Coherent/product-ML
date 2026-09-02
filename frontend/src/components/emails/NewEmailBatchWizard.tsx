"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Check, Loader2, Mail } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { CsvUploadZone } from "@/components/projects/CsvUploadZone";
import { useBackendToken } from "@/hooks/useBackendToken";
import { api, ApiError, type EmailBatch, type UploadCsvResult } from "@/lib/api";
import { cn } from "@/lib/utils";

const STEPS = ["Name", "Upload", "Review & Launch"];

export function NewEmailBatchWizard() {
  const router = useRouter();
  const { data: token } = useBackendToken();

  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [batch, setBatch] = useState<EmailBatch | null>(null);
  const [uploadResult, setUploadResult] = useState<UploadCsvResult | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [isLaunching, setIsLaunching] = useState(false);
  const [concurrency, setConcurrency] = useState(10);

  async function handleCreateAndContinue() {
    if (!token || !name.trim()) return;
    setIsCreating(true);
    try {
      const created = await api.createEmailBatch(token, { name: name.trim() });
      setBatch(created);
      setStep(1);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to create batch");
    } finally {
      setIsCreating(false);
    }
  }

  async function handleFileSelected(selected: File) {
    if (!token || !batch) return;
    setIsUploading(true);
    setUploadResult(null);
    try {
      const result = await api.uploadEmailCsv(token, batch.id, selected);
      setUploadResult(result);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to upload CSV");
    } finally {
      setIsUploading(false);
    }
  }

  async function handleLaunch() {
    if (!token || !batch) return;
    setIsLaunching(true);
    try {
      await api.startEmailBatch(token, batch.id, concurrency);
      toast.success("Batch started! Redirecting to live progress...");
      router.push(`/dashboard/emails/${batch.id}`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to start batch");
    } finally {
      setIsLaunching(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">New Email Finder Batch</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Upload a company list and find each company&apos;s real official contact email in three steps.
        </p>
      </div>

      <div className="flex items-center gap-2">
        {STEPS.map((label, i) => (
          <div key={label} className="flex flex-1 items-center gap-2">
            <div
              className={cn(
                "flex size-7 shrink-0 items-center justify-center rounded-full border text-xs font-medium",
                i < step
                  ? "border-primary bg-primary text-primary-foreground"
                  : i === step
                  ? "border-primary text-primary"
                  : "border-muted-foreground/30 text-muted-foreground"
              )}
            >
              {i < step ? <Check className="size-3.5" /> : i + 1}
            </div>
            <span className={cn("text-sm", i === step ? "font-medium" : "text-muted-foreground")}>{label}</span>
            {i < STEPS.length - 1 && <div className="h-px flex-1 bg-border" />}
          </div>
        ))}
      </div>

      {step === 0 && (
        <Card className="p-6 space-y-6">
          <div className="space-y-2">
            <Label htmlFor="name">Batch name</Label>
            <Input id="name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Q3 Supplier Contacts" />
          </div>

          <div className="rounded-lg border border-dashed p-4 text-xs text-muted-foreground space-y-1">
            <p className="flex items-center gap-1.5 font-medium text-foreground">
              <Mail className="size-3.5" /> How this works:
            </p>
            <p>1. A website column is optional — if a company has no known URL, we search for its official site first.</p>
            <p>2. We only report emails that actually appear on a real page, or a clearly-labeled best guess if none exist.</p>
            <p>3. Every result shows exactly where it came from and how confident it is — nothing is invented.</p>
          </div>

          <div className="flex justify-end">
            <Button onClick={handleCreateAndContinue} disabled={!name.trim() || isCreating}>
              {isCreating && <Loader2 className="mr-1.5 size-4 animate-spin" />}
              Continue
            </Button>
          </div>
        </Card>
      )}

      {step === 1 && batch && (
        <Card className="p-6 space-y-6">
          <CsvUploadZone onFileSelected={handleFileSelected} uploadResult={uploadResult} isUploading={isUploading} />
          <div className="flex justify-between">
            <Button variant="outline" onClick={() => setStep(0)}>
              Back
            </Button>
            <Button onClick={() => setStep(2)} disabled={!uploadResult || uploadResult.total_rows === 0}>
              Continue
            </Button>
          </div>
        </Card>
      )}

      {step === 2 && batch && uploadResult && (
        <Card className="p-6 space-y-6">
          <div className="space-y-4">
            <div className="flex items-center gap-3 rounded-lg border p-4">
              <Mail className="size-8 text-primary" />
              <div>
                <p className="font-medium">{batch.name}</p>
                <p className="text-xs text-muted-foreground">
                  {uploadResult.total_rows.toLocaleString()} companies
                </p>
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="concurrency">Concurrency (parallel companies processed at once)</Label>
              <Input
                id="concurrency"
                type="number"
                min={1}
                max={50}
                value={concurrency}
                onChange={(e) => setConcurrency(Number(e.target.value))}
              />
              <p className="text-xs text-muted-foreground">
                Default 10 — lower than product generation, since this workload also probes third-party mail
                servers directly, not just our own LLM provider keys.
              </p>
            </div>
          </div>

          <div className="flex justify-between">
            <Button variant="outline" onClick={() => setStep(1)}>
              Back
            </Button>
            <Button onClick={handleLaunch} disabled={isLaunching}>
              {isLaunching && <Loader2 className="mr-1.5 size-4 animate-spin" />}
              Launch batch
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}
