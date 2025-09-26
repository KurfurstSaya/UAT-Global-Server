import os
import time
import cv2
import psutil
import numpy as np
from multiprocessing import get_context
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue
from threading import RLock
from typing import Optional, Tuple

from bot.recog import match_worker


class MatchWorkerClient:
    def __init__(self):
        self._ctx = get_context('spawn')
        self._task_q: Optional[Queue] = None
        self._resp_q: Optional[Queue] = None
        self._proc: Optional[BaseProcess] = None
        self._pid: Optional[int] = None
        self._lock = RLock()
        self._next_id = 1
        self._timeout = float(os.getenv('UAT_MATCH_TIMEOUT_SEC', '5') or '5')
        self._rss_limit_mb = int(os.getenv('UAT_MATCH_WORKER_RSS_LIMIT_MB', '1024') or '1024')
        self._restart_every = int(os.getenv('UAT_MATCH_RESTART_EVERY', '100000') or '100000')
        self._jpeg_quality = int(os.getenv('UAT_MATCH_JPEG_QUALITY', '80') or '80')
        self._tasks_done = 0
        self._last_start = 0.0

    def start(self):
        with self._lock:
            if self._proc and self._proc.is_alive():
                return
            self._task_q = self._ctx.Queue(maxsize=64)
            self._resp_q = self._ctx.Queue(maxsize=64)
            self._proc = self._ctx.Process(target=match_worker.run, args=(self._task_q, self._resp_q), daemon=True)
            self._proc.start()
            self._pid = self._proc.pid
            self._last_start = time.time()

    def stop(self):
        with self._lock:
            try:
                if self._task_q:
                    self._task_q.put_nowait(None)
            except Exception:
                pass
            if self._proc is not None:
                try:
                    self._proc.join(timeout=1.0)
                except Exception:
                    pass
            self._proc = None
            self._pid = None
            self._task_q = None
            self._resp_q = None
            self._tasks_done = 0

    def _should_restart(self) -> bool:
        if self._restart_every > 0 and self._tasks_done >= self._restart_every:
            return True
        try:
            if self._pid:
                p = psutil.Process(self._pid)
                rss_mb = p.memory_info().rss / (1024**2)
                return rss_mb >= self._rss_limit_mb
        except Exception:
            return False
        return False

    def _ensure(self):
        if self._proc is None or not self._proc.is_alive():
            self.start()
        elif self._should_restart():
            self.stop()
            self.start()

    def _next_task_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def match_gray(self, roi_gray: np.ndarray, template_path: str, threshold: float = 0.86) -> Tuple[bool, Optional[Tuple[int, int]], Optional[Tuple[Tuple[int, int], Tuple[int, int]]]]:
        self._ensure()
        if self._task_q is None or self._resp_q is None:
            return False, None, None
        try:
            ok, buf = cv2.imencode('.jpg', roi_gray, [int(cv2.IMWRITE_JPEG_QUALITY), int(self._jpeg_quality)])
            if not ok:
                return False, None, None
            task_id = self._next_task_id()
            self._task_q.put({
                'id': task_id,
                'op': 'match',
                'img_jpg': bytes(buf),
                'template_path': template_path,
                'threshold': float(threshold),
            })
            t0 = time.time()
            while time.time() - t0 < self._timeout:
                try:
                    resp = self._resp_q.get(timeout=max(0.0, self._timeout - (time.time() - t0)))
                except Exception:
                    break
                if isinstance(resp, dict) and resp.get('id') == task_id:
                    self._tasks_done += 1
                    if resp.get('ok') and resp.get('find_match'):
                        return True, tuple(resp.get('center')), (tuple(resp.get('area')[0]), tuple(resp.get('area')[1]))
                    return False, None, None
            self.stop()
        except Exception:
            try:
                self.stop()
            except Exception:
                pass
        return False, None, None


_client_singleton: Optional[MatchWorkerClient] = None


def get_client() -> MatchWorkerClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = MatchWorkerClient()
        _client_singleton.start()
    return _client_singleton
