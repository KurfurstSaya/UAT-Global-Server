import os
import sys
import time
import cv2
import numpy as np
from multiprocessing import Queue


def _decode_gray(jpg_bytes: bytes):
    arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    return img


def _load_template_gray(path: str):
    return cv2.imread(path, cv2.IMREAD_GRAYSCALE)


def run(task_q: Queue, resp_q: Queue):
    try:
        cv2.setNumThreads(max(1, int(os.getenv("UAT_CV_THREADS", "1"))))
    except Exception:
        pass
    while True:
        try:
            task = task_q.get()
        except Exception:
            break
        if task is None:
            break
        tid = task.get('id')
        op = task.get('op')
        if op == 'ping':
            resp_q.put({'id': tid, 'ok': True})
            continue
        if op != 'match':
            resp_q.put({'id': tid, 'ok': False, 'err': f'unknown op {op}'})
            continue
        try:
            roi = _decode_gray(task['img_jpg'])
            tpl = _load_template_gray(task['template_path'])
            if roi is None or tpl is None or roi.size == 0 or tpl.size == 0:
                resp_q.put({'id': tid, 'ok': True, 'find_match': False, 'center': None, 'area': None, 'err': None})
                continue
            th, tw = tpl.shape[:2]
            if roi.shape[0] < th or roi.shape[1] < tw:
                resp_q.put({'id': tid, 'ok': True, 'find_match': False, 'center': None, 'area': None, 'err': None})
                continue
            res = cv2.matchTemplate(roi, tpl, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
            threshold = float(task.get('threshold', 0.86))
            if max_val > threshold:
                cx, cy = int(max_loc[0] + tw / 2), int(max_loc[1] + th / 2)
                area = ((max_loc[0], max_loc[1]), (max_loc[0] + tw, max_loc[1] + th))
                resp_q.put({'id': tid, 'ok': True, 'find_match': True, 'center': (cx, cy), 'area': area, 'err': None})
            else:
                resp_q.put({'id': tid, 'ok': True, 'find_match': False, 'center': None, 'area': None, 'err': None})
        except Exception as e:
            try:
                resp_q.put({'id': tid, 'ok': False, 'err': str(e)})
            except Exception:
                pass
