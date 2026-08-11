"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Boxes, Check, Factory, Layers, Loader2, Package, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { CsvUploadZone } from "@/components/projects/CsvUploadZone";
import { useBackendToken } from "@/hooks/useBackendToken";
import { api, ApiError, type Project, type UploadCsvResult } from "@/lib/api";
import { cn } from "@/lib/utils";

type Mode = "classification" | "generation" | "both";

const MODES: { id: Mode; title: string; desc: string; icon: React.ElementType }[] = [
  {
    id: "classification",
    title: "Classification only",
    desc: "Tag each company as Packaging, Machinery, Finished Goods, Raw Material, or a combination.",
    icon: Layers,
  },
  {
    id: "generation",
    title: "Product generation only",
    desc: "Generate a 10-50 item product catalog per company (classification runs internally to tag products correctly).",
    icon: Package,
  },
  {
    id: "both",
    title: "Classification + Generation",
    desc: "Full pipeline: classify supply chain role and generate a grounded product catalog.",
    icon: Sparkles,
  },
];

const STEPS = ["Mode", "Upload", "Review & Launch"];

export function NewProjectWizard() {
  const router = useRouter();
  const { data: token } = useBackendToken();

  const [step, setStep] = useState(0);
  const [mode, setMode] = useState<Mode | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [project, setProject] = useState<Project | null>(null);
  const [uploadResult, setUploadResult] = useState<UploadCsvResult | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [isLaunching, setIsLaunching] = useState(false);
  const [concurrency, setConcurrency] = useState(20);

  async function handleCreateProjectAndContinue() {
    if (!token || !mode || !name.trim()) return;
    setIsCreating(true);
    try {
      const created = await api.createProject(token, { name: name.trim(), description, mode });
      setProject(created);
      setStep(1);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to create project");
    } finally {
      setIsCreating(false);
    }
  }

  async function handleFileSelected(selected: File) {
    if (!token || !project) return;
    setIsUploading(true);
    setUploadResult(null);
    try {
      const result = await api.uploadCsv(token, project.id, selected);
      setUploadResult(result);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to upload CSV");
    } finally {
      setIsUploading(false);
    }
  }

  async function handleLaunch() {
    if (!token || !project) return;
    setIsLaunching(true);
    try {
      await api.createJob(token, { project_id: project.id, mode: mode ?? undefined, concurrency });
      toast.success("Job started! Redirecting to live progress...");
      router.push(`/dashboard/projects/${project.id}`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to start job");
    } finally {
      setIsLaunching(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">New Project</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Set up a new classification / product generation run in three steps.
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
            <Label htmlFor="name">Project name</Label>
            <Input id="name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Q3 Supplier Analysis" />
          </div>
          <div className="space-y-2">
            <Label htmlFor="description">Description (optional)</Label>
            <Textarea
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What is this project for?"
              rows={2}
            />
          </div>

          <div className="space-y-2">
            <Label>Generation mode</Label>
            <div className="grid gap-3 sm:grid-cols-1">
              {MODES.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => setMode(m.id)}
                  className={cn(
                    "flex items-start gap-3 rounded-lg border p-4 text-left transition-colors",
                    mode === m.id ? "border-primary bg-primary/5" : "hover:bg-muted/40"
                  )}
                >
                  <m.icon className={cn("mt-0.5 size-5 shrink-0", mode === m.id ? "text-primary" : "text-muted-foreground")} />
                  <div>
                    <p className="text-sm font-medium">{m.title}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">{m.desc}</p>
                  </div>
                </button>
              ))}
            </div>
          </div>

          <div className="flex justify-end">
            <Button onClick={handleCreateProjectAndContinue} disabled={!mode || !name.trim() || isCreating}>
              {isCreating && <Loader2 className="mr-1.5 size-4 animate-spin" />}
              Continue
            </Button>
          </div>
        </Card>
      )}

      {step === 1 && project && (
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

      {step === 2 && project && uploadResult && (
        <Card className="p-6 space-y-6">
          <div className="space-y-4">
            <div className="flex items-center gap-3 rounded-lg border p-4">
              <Boxes className="size-8 text-primary" />
              <div>
                <p className="font-medium">{project.name}</p>
                <p className="text-xs text-muted-foreground">
                  {MODES.find((m) => m.id === mode)?.title} · {uploadResult.total_rows.toLocaleString()} companies
                </p>
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="concurrency">Concurrency (parallel companies processed at once)</Label>
              <Input
                id="concurrency"
                type="number"
                min={1}
                max={200}
                value={concurrency}
                onChange={(e) => setConcurrency(Number(e.target.value))}
              />
              <p className="text-xs text-muted-foreground">
                Default 20 balances throughput against the system&apos;s shared LLM rate limits. Raise
                this only if you&apos;ve added several of your own API keys in Settings; lower it if you
                only have a single key configured.
              </p>
            </div>

            <div className="rounded-lg border border-dashed p-4 text-xs text-muted-foreground space-y-1">
              <p className="flex items-center gap-1.5 font-medium text-foreground">
                <Factory className="size-3.5" /> What happens when you launch:
              </p>
              <p>1. Each company&apos;s website URL is read directly by an LLM (Jina Reader, with automatic fallback).</p>
              <p>2. The company is classified into its supply chain role(s).</p>
              {mode !== "classification" && <p>3. A grounded 10-50 product catalog is generated per company.</p>}
              <p>Progress streams live below once started — you can pause, resume, or stop anytime.</p>
            </div>
          </div>

          <div className="flex justify-between">
            <Button variant="outline" onClick={() => setStep(1)}>
              Back
            </Button>
            <Button onClick={handleLaunch} disabled={isLaunching}>
              {isLaunching && <Loader2 className="mr-1.5 size-4 animate-spin" />}
              Launch job
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}
