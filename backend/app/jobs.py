"""Background job queues. GPU jobs run one at a time because they share the graphics card; CPU jobs
(speech-to-text) have their own queue so they never wait behind a long video render."""
from __future__ import annotations

import queue
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

# Held by whatever is using the GPU (a job, or an interactive chat request).
# A plain Lock (not RLock): streaming responses may release it from another thread.
GPU_LOCK = threading.Lock()


class Cancelled(Exception):
    pass


@dataclass
class Job:
    kind: str
    title: str
    fn: Callable[["Job"], Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "queued"         # queued | running | done | error | cancelled
    progress: float = 0.0
    stage: str = "Waiting in queue"
    error: str | None = None
    result: Any = None
    ref: str | None = None         # what the job works on (e.g. "<project>:<scene>"), so a page can find it again
    info: dict = field(default_factory=dict)   # live details for the UI while it runs (e.g. scenes painted so far)
    created: float = field(default_factory=time.time)
    finished: float | None = None
    _cancel: bool = False

    def update(self, progress: float | None = None, stage: str | None = None) -> None:
        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))
        if stage is not None:
            self.stage = stage
        self.check_cancel()

    def check_cancel(self) -> None:
        if self._cancel:
            raise Cancelled()

    @property
    def cancelled(self) -> bool:
        return self._cancel

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in
                ("id", "kind", "title", "status", "progress", "stage", "error", "result",
                 "ref", "info", "created", "finished")}


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._q: queue.Queue[Job] = queue.Queue()
        self._cpu_q: queue.Queue[Job] = queue.Queue()
        threading.Thread(target=self._worker, args=(self._q, GPU_LOCK), daemon=True, name="job-worker").start()
        threading.Thread(target=self._worker, args=(self._cpu_q, None), daemon=True, name="cpu-job-worker").start()

    def submit(self, kind: str, title: str, fn: Callable[[Job], Any], gpu: bool = True,
               ref: str | None = None) -> Job:
        job = Job(kind=kind, title=title, fn=fn, ref=ref)
        self.jobs[job.id] = job
        (self._q if gpu else self._cpu_q).put(job)
        return job

    def cancel(self, job_id: str) -> None:
        job = self.jobs[job_id]
        job._cancel = True
        if job.status == "queued":
            job.status, job.stage, job.finished = "cancelled", "Cancelled", time.time()

    def _worker(self, q: "queue.Queue[Job]", lock: threading.Lock | None) -> None:
        while True:
            job = q.get()
            if job.status == "cancelled":
                continue
            job.status, job.stage = "running", "Starting"
            try:
                if lock is None:
                    job.result = job.fn(job)
                else:
                    with lock:
                        job.result = job.fn(job)
                job.status, job.progress, job.stage = "done", 1.0, "Done"
            except Cancelled:
                job.status, job.stage = "cancelled", "Cancelled"
            except Exception as exc:  # surface any failure to the UI
                traceback.print_exc()
                job.status, job.error, job.stage = "error", str(exc), "Failed"
            job.finished = time.time()


jobs = JobManager()
