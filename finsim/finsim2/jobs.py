"""Background jobs (data refresh, ML training, universe scans) with progress the UI can poll."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from typing import Callable, Dict, List, Optional


class Job:
    def __init__(self, kind: str, key: str):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.key = key
        self.status = "queued"
        self.done = 0
        self.total = 0
        self.message = ""
        self.result = None
        self.error: Optional[str] = None
        self.started = time.time()
        self.finished: Optional[float] = None

    def progress(self, done, total, message=""):
        self.done, self.total, self.message = done, total, message

    def view(self) -> dict:
        return {"id": self.id, "kind": self.kind, "key": self.key, "status": self.status, "done": self.done, "total": self.total,
                "message": self.message, "error": self.error, "started": self.started, "finished": self.finished,
                "elapsed": round((self.finished or time.time()) - self.started, 1), "result": self.result if self.status == "done" else None}


class JobManager:
    """One worker thread per job; a job of the same kind and key already running is returned instead of a duplicate."""

    def __init__(self, max_history: int = 50):
        self.jobs: Dict[str, Job] = {}
        self.order: List[str] = []
        self.lock = threading.Lock()
        self.max_history = max_history

    def running(self, kind: Optional[str] = None) -> List[Job]:
        return [j for j in self.jobs.values() if j.status in ("queued", "running") and (kind is None or j.kind == kind)]

    def start(self, kind: str, key: str, fn: Callable[[Job], object]) -> Job:
        with self.lock:
            for j in self.jobs.values():
                if j.kind == kind and j.key == key and j.status in ("queued", "running"):
                    return j
            job = Job(kind, key)
            self.jobs[job.id] = job
            self.order.append(job.id)
            while len(self.order) > self.max_history:
                old = self.order.pop(0)
                if self.jobs.get(old) and self.jobs[old].status not in ("queued", "running"):
                    self.jobs.pop(old, None)

        def run():
            job.status = "running"
            try:
                job.result = fn(job)
                job.status = "done"
            except Exception as e:           # a failed job reports why; the server keeps going
                job.status = "failed"
                job.error = f"{type(e).__name__}: {e}"
                job.message = traceback.format_exc(limit=3)[-600:]
            finally:
                job.finished = time.time()
        threading.Thread(target=run, daemon=True, name=f"job-{kind}-{key}").start()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def list(self) -> List[dict]:
        return [self.jobs[i].view() for i in reversed(self.order) if i in self.jobs]
