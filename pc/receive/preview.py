"""运行时浏览器预览：把每帧合成图以 MJPEG 推给任何浏览器。

为什么不用 cv2.imshow：
  - 通过 SSH 启动的进程在 macOS 上拿不到 WindowServer，窗口不显示；
  - 服务器上往往没有 X11；
  - 浏览器方案不需要装任何东西，手机/另一台机器也能看。

用法（在 pc.receive.live 里）：
    python -m pc.receive.live ... --preview-port 8080
    浏览器打开 http://<PC 的 IP>:8080/   （同一网段即可）
"""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

import cv2
import numpy as np


def _local_ip() -> str:
    """猜一个对局域网可见的地址（不真的发包）。失败就返回 127.0.0.1。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def depth_colormap(depth: np.ndarray, valid: Optional[np.ndarray] = None) -> np.ndarray:
    """把深度图转成 Turbo 伪彩色；无效像素压成暗灰。"""
    d = np.asarray(depth, dtype=np.float32)
    vis = np.zeros((*d.shape, 3), np.uint8)
    mask = np.isfinite(d) & (d > 0)
    if valid is not None:
        mask &= np.asarray(valid, dtype=bool)
    if not mask.any():
        return vis
    lo, hi = np.percentile(d[mask], (2, 98))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi - lo < 1e-3:
        hi = lo + 1e-3
    norm = np.clip((d - lo) / (hi - lo), 0.0, 1.0)
    norm = np.where(mask, norm, 0.0)  # NaN 会被 astype 变成 0 并报 RuntimeWarning
    color = cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    vis[mask] = color[mask]
    return vis


def composite(result: Any, calib: Any, scale: float = 1.0) -> np.ndarray:
    """左：校正图 + 物体框；右：深度伪彩色。两侧同高，底部状态条。"""
    left = np.asarray(result.rectified_left)
    if left.ndim == 2:
        left = cv2.cvtColor(left, cv2.COLOR_GRAY2BGR)
    if result.objects:
        from pc.validate.visualize import draw_objects

        left = draw_objects(left, result.objects, calib)
    depth = depth_colormap(result.depth, getattr(result.fused, "valid", None))
    if depth.shape[:2] != left.shape[:2]:
        depth = cv2.resize(depth, (left.shape[1], left.shape[0]), interpolation=cv2.INTER_NEAREST)
    panel = np.hstack([left, depth])
    bar = np.zeros((28, panel.shape[1], 3), np.uint8)
    cov = float(getattr(result.fused, "coverage", 0.0)) * 100.0
    total = float(result.timings_ms.get("total", 0.0))
    text = (
        "pair {} | objects {} | coverage {:.1f}% | {:.0f} ms | Z 2-98%: "
        "{:.2f}-{:.2f} m".format(
            result.frame_id, len(result.objects), cov, total,
            _percentile(result.depth, 2), _percentile(result.depth, 98),
        )
    )
    cv2.putText(bar, text, (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (230, 230, 230), 1, cv2.LINE_AA)
    panel = np.vstack([panel, bar])
    if scale and abs(scale - 1.0) > 1e-3:
        panel = cv2.resize(panel, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return panel


def _percentile(depth: np.ndarray, q: float) -> float:
    d = np.asarray(depth, dtype=np.float32)
    mask = np.isfinite(d) & (d > 0)
    if not mask.any():
        return 0.0
    return float(np.percentile(d[mask], q))


def _page(title: str) -> bytes:
    html = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
 body {{ margin: 0; background: #111; color: #ddd; font-family: system-ui, sans-serif; }}
 header {{ padding: 10px 16px; font-size: 14px; display: flex; gap: 16px; align-items: baseline; }}
 header b {{ color: #8fd; font-weight: 600; }}
 #wrap {{ padding: 0 12px 12px; }}
 img {{ width: 100%; height: auto; display: block; image-rendering: auto; }}
 a {{ color: #8bd; }}
</style></head>
<body>
<header><b>{title}</b><span id="s">等待第一帧…</span>
<a href="/snapshot.jpg">单帧</a><a href="/stats.json">stats</a></header>
<div id="wrap"><img src="/stream" alt="stream"></div>
<script>
setInterval(async () => {{
  try {{
    const r = await fetch('/stats.json'); const j = await r.json();
    document.getElementById('s').textContent =
      'pair ' + j.pair_id + ' · ' + j.fps.toFixed(1) + ' fps · ' +
      j.coverage_pct.toFixed(1) + '% 覆盖 · ' + j.total_ms.toFixed(0) + ' ms';
  }} catch (e) {{}}
}}, 1000);
</script>
</body></html>""".format(title=title)
    return html.encode("utf-8")


class PreviewServer:
    """单帧槽 + MJPEG 广播。线程安全；没有客户端时开销只有一次 JPEG 编码。"""

    def __init__(self, host: str = "0.0.0.0", port: int = 8080,
                 quality: int = 80, title: str = "双目 3D 实时预览") -> None:
        self.host = host
        self.quality = int(quality)
        self.title = title
        self._cond = threading.Condition()
        self._jpeg: Optional[bytes] = None
        self._seq = 0
        self._stats: Dict[str, Any] = {"pair_id": -1, "fps": 0.0,
                                       "coverage_pct": 0.0, "total_ms": 0.0}
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._port = int(port)

    # ------------------------------------------------------------------ #
    @property
    def port(self) -> int:
        httpd = self._httpd
        return int(httpd.server_address[1]) if httpd is not None else self._port

    @property
    def url(self) -> str:
        return "http://{}:{}/".format(_local_ip(), self.port)

    def start(self) -> "PreviewServer":
        srv = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, fmt, *args):  # 静音，别刷终端
                return

            def do_GET(self):  # noqa: N802 (http.server 接口)
                path = self.path.split("?", 1)[0]
                if path in ("/", "/index.html"):
                    body = _page(srv.title)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif path == "/snapshot.jpg":
                    with srv._cond:
                        buf = srv._jpeg
                    if buf is None:
                        self.send_error(503, "no frame yet")
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(buf)))
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(buf)
                elif path == "/stats.json":
                    with srv._cond:
                        body = json.dumps(srv._stats, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif path == "/stream":
                    self._stream()
                else:
                    self.send_error(404)

            def _stream(self):
                self.send_response(200)
                self.send_header("Content-Type",
                                 "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                last = -1
                try:
                    while True:
                        with srv._cond:
                            if srv._seq == last:
                                srv._cond.wait(2.0)
                            seq, buf = srv._seq, srv._jpeg
                        if buf is None:
                            time.sleep(0.05)
                            continue
                        last = seq
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(
                            "Content-Length: {}\r\n\r\n".format(len(buf)).encode("ascii"))
                        self.wfile.write(buf)
                        self.wfile.write(b"\r\n")
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

        self._httpd = ThreadingHTTPServer((self.host, self._port), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="preview-http", daemon=True)
        self._thread.start()
        return self

    # ------------------------------------------------------------------ #
    def update(self, frame_bgr: np.ndarray, stats: Optional[Dict[str, Any]] = None) -> None:
        ok, buf = cv2.imencode(".jpg", frame_bgr,
                               [int(cv2.IMWRITE_JPEG_QUALITY), self.quality])
        if not ok:
            return
        data = buf.tobytes()
        with self._cond:
            self._jpeg = data
            self._seq += 1
            if stats:
                self._stats.update(stats)
            self._cond.notify_all()

    def close(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
