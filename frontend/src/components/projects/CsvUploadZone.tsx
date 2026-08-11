"use client";

import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";
import { CheckCircle2, FileSpreadsheet, Upload, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { FormatGuideModal } from "@/components/projects/FormatGuideModal";
import type { UploadCsvResult } from "@/lib/api";

interface Props {
  onFileSelected: (file: File) => void;
  uploadResult?: UploadCsvResult | null;
  isUploading?: boolean;
}

export function CsvUploadZone({ onFileSelected, uploadResult, isUploading }: Props) {
  const [fileName, setFileName] = useState<string | null>(null);

  const onDrop = useCallback(
    (accepted: File[]) => {
      const file = accepted[0];
      if (!file) return;
      setFileName(file.name);
      onFileSelected(file);
    },
    [onFileSelected]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "text/csv": [".csv"] },
    maxFiles: 1,
  });

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium">Upload company list (CSV)</p>
        <FormatGuideModal />
      </div>

      <div
        {...getRootProps()}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-10 text-center transition-colors",
          isDragActive ? "border-primary bg-primary/5" : "border-muted-foreground/25 hover:bg-muted/40"
        )}
      >
        <input {...getInputProps()} />
        {fileName ? (
          <FileSpreadsheet className="size-8 text-primary" />
        ) : (
          <Upload className="size-8 text-muted-foreground" />
        )}
        <p className="text-sm font-medium">
          {fileName ? fileName : isDragActive ? "Drop the CSV here" : "Drag & drop your CSV, or click to browse"}
        </p>
        <p className="text-xs text-muted-foreground">
          We auto-detect company name / website / location columns from common header names.
        </p>
      </div>

      {isUploading && <p className="text-sm text-muted-foreground">Validating and uploading...</p>}

      {uploadResult && (
        <div className="space-y-3 rounded-lg border p-4">
          <div className="flex items-center gap-2 text-sm">
            <CheckCircle2 className="size-4 text-emerald-500" />
            <span className="font-medium">{uploadResult.total_rows.toLocaleString()} companies ready</span>
          </div>

          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-muted-foreground">Detected columns:</span>
            <Badge variant="secondary" className="font-normal">
              name ← {uploadResult.detected_columns.company_name}
            </Badge>
            <Badge variant={uploadResult.detected_columns.url ? "secondary" : "outline"} className="font-normal">
              url ← {uploadResult.detected_columns.url ?? "not found"}
            </Badge>
            <Badge
              variant={uploadResult.detected_columns.location ? "secondary" : "outline"}
              className="font-normal"
            >
              location ← {uploadResult.detected_columns.location ?? "not found"}
            </Badge>
          </div>

          {uploadResult.errors.length > 0 && (
            <div className="flex items-start gap-2 text-xs text-amber-500">
              <XCircle className="mt-0.5 size-3.5 shrink-0" />
              <span>
                {uploadResult.errors.length} row(s) skipped (missing company name): rows{" "}
                {uploadResult.errors.map((e) => e.row).join(", ")}
              </span>
            </div>
          )}

          {uploadResult.preview.length > 0 && (
            <div className="overflow-x-auto rounded-md border">
              <table className="w-full text-xs">
                <thead className="bg-muted">
                  <tr>
                    {Object.keys(uploadResult.preview[0]).map((key) => (
                      <th key={key} className="px-3 py-2 text-left font-medium">
                        {key}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {uploadResult.preview.map((row, i) => (
                    <tr key={i} className="border-t">
                      {Object.values(row).map((val, j) => (
                        <td key={j} className="px-3 py-2 text-muted-foreground">
                          {val || "—"}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
