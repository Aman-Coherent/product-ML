import { create } from "zustand";

export interface JobProgress {
  done: number;
  failed: number;
  total: number;
  percent: number;
}

export interface CircuitStatus {
  state: "CLOSED" | "OPEN" | "HALF_OPEN";
}

interface JobState {
  status: Record<string, string>; // jobId -> status
  progress: Record<string, JobProgress>;
  logs: Record<string, string[]>; // jobId -> recent log lines
  setStatus: (jobId: string, status: string) => void;
  setProgress: (jobId: string, progress: JobProgress) => void;
  pushLog: (jobId: string, line: string) => void;
  reset: (jobId: string) => void;
}

export const useJobStore = create<JobState>((set) => ({
  status: {},
  progress: {},
  logs: {},
  setStatus: (jobId, status) =>
    set((state) => ({ status: { ...state.status, [jobId]: status } })),
  setProgress: (jobId, progress) =>
    set((state) => ({ progress: { ...state.progress, [jobId]: progress } })),
  pushLog: (jobId, line) =>
    set((state) => ({
      logs: { ...state.logs, [jobId]: [...(state.logs[jobId] || []).slice(-199), line] },
    })),
  reset: (jobId) =>
    set((state) => {
      const status = { ...state.status };
      const progress = { ...state.progress };
      const logs = { ...state.logs };
      delete status[jobId];
      delete progress[jobId];
      delete logs[jobId];
      return { status, progress, logs };
    }),
}));
