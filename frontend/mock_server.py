#!/usr/bin/env python3
"""
Mock-backend для Auto Layout AI (только для локальной проверки визуала).

Реализует все эндпоинты, которые дёргает static/app.js, но вместо реального
DART/CVAT генерирует правдоподобные фейковые данные. Реальные загруженные
изображения из zip действительно распаковываются и раздаются — то есть
статистика и превью в интерфейсе будут настоящими, а вот "найденные объекты"
и авторазметка — сгенерированы случайно.

Запуск:
    python3 mock_server.py [порт по умолчанию 8000]

Никаких pip-зависимостей не требуется — только стандартная библиотека.
"""

import io
import json
import mimetypes
import os
import random
import re
import shutil
import struct
import threading
import time
import traceback
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "_mock_data"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}

DATASETS = {}  # id -> Dataset
DATASETS_LOCK = threading.Lock()


# --------------------------------------------------------------- image sizes

def jpeg_size(path):
    with open(path, "rb") as f:
        f.read(2)
        while True:
            b = f.read(1)
            if not b:
                return None, None
            if b != b"\xff":
                continue
            marker = f.read(1)
            while marker == b"\xff":
                marker = f.read(1)
            if marker in (
                b"\xc0", b"\xc1", b"\xc2", b"\xc3", b"\xc5", b"\xc6", b"\xc7",
                b"\xc9", b"\xca", b"\xcb", b"\xcd", b"\xce", b"\xcf",
            ):
                f.read(3)
                h, w = struct.unpack(">HH", f.read(4))
                return w, h
            seg_len_raw = f.read(2)
            if len(seg_len_raw) < 2:
                return None, None
            seg_len = struct.unpack(">H", seg_len_raw)[0]
            f.read(seg_len - 2)


def get_image_size(path):
    try:
        with open(path, "rb") as f:
            head = f.read(32)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", head[16:24])
            return w, h
        if head[:2] == b"\xff\xd8":
            return jpeg_size(path)
        if head[:6] in (b"GIF87a", b"GIF89a"):
            w, h = struct.unpack("<HH", head[6:10])
            return w, h
        if head[:2] == b"BM":
            w, h = struct.unpack("<ii", head[18:26])
            return w, abs(h)
    except Exception:
        pass
    return None, None


# --------------------------------------------------------------- multipart

def parse_multipart(body, content_type):
    m = re.search(r"boundary=(.+)", content_type)
    if not m:
        raise ValueError("multipart без boundary")
    boundary = m.group(1).strip()
    if boundary.startswith('"') and boundary.endswith('"'):
        boundary = boundary[1:-1]
    boundary_bytes = ("--" + boundary).encode()

    fields = {}
    for part in body.split(boundary_bytes):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        header_bytes, data = part.split(b"\r\n\r\n", 1)
        data = data.rstrip(b"\r\n")
        headers = header_bytes.decode(errors="replace")
        name_match = re.search(r'name="([^"]+)"', headers)
        filename_match = re.search(r'filename="([^"]*)"', headers)
        if not name_match:
            continue
        fields[name_match.group(1)] = (
            filename_match.group(1) if filename_match else None,
            data,
        )
    return fields


# --------------------------------------------------------------- dataset

class Dataset:
    def __init__(self, dataset_id, root):
        self.id = dataset_id
        self.root = root  # DATA_DIR / id
        self.images = []  # [{id, filename, rel, ext, width, height}]
        self.rep = {"n": 0, "pool": [], "index": 0, "approved": set()}
        self.dart_settings = {
            "prompt": "",
            "confidence": 0.35,
            "mode": "bbox",
            "show_overlay": True,
        }
        self.autolabel = {
            "status": "idle",
            "progress": {"done": 0, "total": 0},
            "errors": [],
            "stop_flag": False,
        }
        self.exports = {}  # format -> rel path
        self.lock = threading.Lock()

    def image_by_id(self, image_id):
        for img in self.images:
            if img["id"] == image_id:
                return img
        return None


def build_dataset_from_zip(zip_bytes):
    dataset_id = uuid.uuid4().hex[:8]
    root = DATA_DIR / dataset_id
    images_dir = root / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir() or "__MACOSX" in name or name.startswith("."):
                continue
            ext = Path(name).suffix.lower()
            if ext not in IMAGE_EXTS:
                continue
            safe_name = f"{len(list(images_dir.iterdir())):05d}_{Path(name).name}"
            target = images_dir / safe_name
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)

    ds = Dataset(dataset_id, root)
    for i, path in enumerate(sorted(images_dir.iterdir())):
        w, h = get_image_size(path)
        ds.images.append(
            {
                "id": f"img_{i:04d}",
                "filename": path.name,
                "rel": f"images/{path.name}",
                "ext": path.suffix.lower(),
                "width": w,
                "height": h,
            }
        )

    with DATASETS_LOCK:
        DATASETS[dataset_id] = ds
    return ds


def compute_stats(ds):
    images = ds.images
    exts, res_counter = {}, {}
    widths, heights, unreadable = [], [], 0

    for img in images:
        exts[img["ext"]] = exts.get(img["ext"], 0) + 1
        if img["width"] and img["height"]:
            widths.append(img["width"])
            heights.append(img["height"])
            key = f"{img['width']}x{img['height']}"
            res_counter[key] = res_counter.get(key, 0) + 1
        else:
            unreadable += 1

    warnings = []
    if not images:
        warnings.append("В архиве не найдено изображений с поддерживаемым расширением.")
    if unreadable:
        warnings.append(f"Не удалось прочитать размеры {unreadable} файлов — возможно, повреждены.")
    if len(exts) > 1:
        warnings.append("Датасет содержит смешанные форматы: " + ", ".join(sorted(exts)) + ".")

    min_res = max_res = None
    common_resolutions = []
    if widths:
        pairs = list(zip(widths, heights))
        mw, mh = min(pairs, key=lambda p: p[0] * p[1])
        Mw, Mh = max(pairs, key=lambda p: p[0] * p[1])
        min_res = {"width": mw, "height": mh}
        max_res = {"width": Mw, "height": Mh}
        common_key = max(res_counter, key=res_counter.get)
        cw, ch = common_key.split("x")
        common_resolutions = [
            {"resolution": common_key, "count": res_counter[common_key]}
        ]

    return {
        "image_count": len(images),
        "readable_image_count": len(images) - unreadable,
        "unreadable_image_count": unreadable,
        "extensions": exts,
        "min_size": min_res,
        "max_size": max_res,
        "common_resolutions": common_resolutions,
        "warnings_count": len(warnings),
        "warnings": warnings,
    }


def frame_response(ds):
    if not ds.rep["pool"]:
        return {
            "dataset_id": ds.id,
            "target_count": ds.rep["n"],
            "approved_count": 0,
            "approved_image_ids": [],
            "viewed_count": 0,
            "total_count": len(ds.images),
            "current_image": None,
            "can_go_prev": False,
            "can_go_next": False,
            "completed": False,
        }
    image_id = ds.rep["pool"][ds.rep["index"]]
    img = ds.image_by_id(image_id)
    return {
        "dataset_id": ds.id,
        "target_count": ds.rep["n"],
        "approved_count": len(ds.rep["approved"]),
        "approved_image_ids": sorted(ds.rep["approved"]),
        "viewed_count": ds.rep["index"] + 1,
        "total_count": len(ds.images),
        "current_image": {
            "id": image_id,
            "filename": img["filename"],
            "url": f"/media/{ds.id}/{img['rel']}",
            "width": img["width"],
            "height": img["height"],
            "approved": image_id in ds.rep["approved"],
        },
        "can_go_prev": ds.rep["index"] > 0,
        "can_go_next": (
            len(ds.rep["approved"]) < ds.rep["n"]
            and ds.rep["index"] < len(ds.rep["pool"]) - 1
        ),
        "completed": len(ds.rep["approved"]) >= ds.rep["n"],
    }


def run_autolabel(ds):
    total = len(ds.images)
    with ds.lock:
        ds.autolabel["progress"] = {"done": 0, "total": total}
        ds.autolabel["errors"] = []
        ds.autolabel["status"] = "running"
        ds.autolabel["stop_flag"] = False

    if total == 0:
        with ds.lock:
            ds.autolabel["status"] = "completed"
        return

    for i, img in enumerate(ds.images):
        time.sleep(0.2)
        with ds.lock:
            if ds.autolabel["stop_flag"]:
                ds.autolabel["status"] = "stopped"
                return
            ds.autolabel["progress"]["done"] = i + 1
            if random.random() < 0.12:
                ds.autolabel["errors"].append(
                    {"image_id": img["filename"], "message": "DART timeout (mock)"}
                )

    with ds.lock:
        ds.autolabel["status"] = "completed"


def build_export_zip(ds, fmt):
    export_dir = ds.root / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    zip_path = export_dir / f"{fmt}.zip"
    readme = (
        f"Mock CVAT export ({fmt.upper()})\n"
        f"Dataset: {ds.id}\n"
        f"Images: {len(ds.images)}\n"
        "Это заглушка — реальных аннотаций внутри нет.\n"
    )
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("README.txt", readme)
    ds.exports[fmt] = f"exports/{fmt}.zip"
    return f"/media/{ds.id}/exports/{fmt}.zip"


def build_results_files(ds):
    results_dir = ds.root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    annotations = [
        {
            "image": img["filename"],
            "objects": [
                {
                    "label": ds.dart_settings.get("prompt") or "object",
                    "confidence": round(random.uniform(0.5, 0.95), 2),
                    "bbox": [10, 10, 80, 80],
                }
            ],
        }
        for img in ds.images
        if img["id"] not in {e.get("image_id") for e in ds.autolabel["errors"]}
    ]
    (results_dir / "annotations.json").write_text(
        json.dumps(annotations, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (results_dir / "errors.json").write_text(
        json.dumps(ds.autolabel["errors"], ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --------------------------------------------------------------- HTTP handler

ROUTES_GET = []
ROUTES_POST = []


def route_get(pattern):
    regex = re.compile(pattern)

    def deco(fn):
        ROUTES_GET.append((regex, fn))
        return fn

    return deco


def route_post(pattern):
    regex = re.compile(pattern)

    def deco(fn):
        ROUTES_POST.append((regex, fn))
        return fn

    return deco


def require_dataset(dataset_id):
    with DATASETS_LOCK:
        ds = DATASETS.get(dataset_id)
    if not ds:
        raise KeyError(f"dataset {dataset_id} не найден (перезагрузите zip после рестарта сервера)")
    return ds


@route_get(r"^/health$")
def h_health(handler, m, body):
    return 200, {"service": "auto-layout-ai-mock", "version": "0.1.0-mock"}


@route_post(r"^/api/datasets/upload$")
def h_upload(handler, m, body):
    content_type = handler.headers.get("Content-Type", "")
    fields = parse_multipart(body, content_type)
    if "file" not in fields or fields["file"][0] is None:
        return 400, {"detail": "Поле 'file' с zip-архивом не найдено в запросе"}
    filename, data = fields["file"]
    if not filename.lower().endswith(".zip"):
        return 400, {"detail": "Ожидается .zip файл"}
    try:
        ds = build_dataset_from_zip(data)
    except zipfile.BadZipFile:
        return 400, {"detail": "Файл повреждён или не является zip-архивом"}
    return 201, {
        "status": "OK",
        "dataset": {
            "id": ds.id,
            "image_count": len(ds.images),
            "status": "READY",
        },
    }


@route_get(r"^/api/datasets/(?P<id>[\w-]+)/stats$")
def h_stats(handler, m, body):
    ds = require_dataset(m.group("id"))
    stats = compute_stats(ds)
    warnings = stats.pop("warnings")
    return 200, {
        "dataset_id": ds.id,
        "stats": stats,
        "warnings": warnings,
        "images": ds.images,
    }


@route_post(r"^/api/datasets/(?P<id>[\w-]+)/representative/init$")
def h_rep_init(handler, m, body):
    ds = require_dataset(m.group("id"))
    payload = json.loads(body or b"{}")
    n = max(1, int(payload.get("target_count", 8)))
    n = min(n, len(ds.images)) if ds.images else 0
    pool = [img["id"] for img in ds.images] if n else []
    approved = set(pool) if len(pool) <= n else set()
    with ds.lock:
        ds.rep = {"n": n, "pool": pool, "index": 0, "approved": approved}
    return 200, frame_response(ds)


@route_get(r"^/api/datasets/(?P<id>[\w-]+)/representative/current$")
def h_rep_current(handler, m, body):
    ds = require_dataset(m.group("id"))
    return 200, frame_response(ds)


@route_post(r"^/api/datasets/(?P<id>[\w-]+)/representative/next$")
def h_rep_next(handler, m, body):
    ds = require_dataset(m.group("id"))
    with ds.lock:
        if ds.rep["pool"]:
            ds.rep["index"] = min(ds.rep["index"] + 1, len(ds.rep["pool"]) - 1)
    return 200, frame_response(ds)


@route_post(r"^/api/datasets/(?P<id>[\w-]+)/representative/prev$")
def h_rep_prev(handler, m, body):
    ds = require_dataset(m.group("id"))
    with ds.lock:
        if ds.rep["pool"]:
            ds.rep["index"] = max(ds.rep["index"] - 1, 0)
    return 200, frame_response(ds)


@route_post(r"^/api/datasets/(?P<id>[\w-]+)/representative/approve$")
def h_rep_approve(handler, m, body):
    ds = require_dataset(m.group("id"))
    payload = json.loads(body or b"{}")
    image_id = payload.get("image_id") or (
        ds.rep["pool"][ds.rep["index"]] if ds.rep["pool"] else None
    )
    if image_id:
        with ds.lock:
            ds.rep["approved"].add(image_id)
    return 200, frame_response(ds)


@route_post(r"^/api/datasets/(?P<id>[\w-]+)/representative/unapprove$")
def h_rep_unapprove(handler, m, body):
    ds = require_dataset(m.group("id"))
    payload = json.loads(body or b"{}")
    image_id = payload.get("image_id") or (
        ds.rep["pool"][ds.rep["index"]] if ds.rep["pool"] else None
    )
    if image_id:
        with ds.lock:
            ds.rep["approved"].discard(image_id)
    return 200, frame_response(ds)


@route_get(r"^/api/datasets/(?P<id>[\w-]+)/dart/settings$")
def h_dart_settings_get(handler, m, body):
    ds = require_dataset(m.group("id"))
    return 200, ds.dart_settings


@route_post(r"^/api/datasets/(?P<id>[\w-]+)/dart/settings$")
def h_dart_settings_post(handler, m, body):
    ds = require_dataset(m.group("id"))
    payload = json.loads(body or b"{}")
    ds.dart_settings.update(
        {k: v for k, v in payload.items() if k in ds.dart_settings}
    )
    return 200, ds.dart_settings


@route_post(r"^/api/datasets/(?P<id>[\w-]+)/dart/preview$")
def h_dart_preview(handler, m, body):
    ds = require_dataset(m.group("id"))
    payload = json.loads(body or b"{}")
    image_id = payload.get("image_id")
    prompt = payload.get("prompt") or "object"
    confidence = float(payload.get("confidence", 0.35))

    img = ds.image_by_id(image_id)
    if not img and ds.images:
        img = ds.images[0]
    if not img:
        return 400, {"detail": "В датасете нет изображений для preview"}

    k = random.randint(2, 4)
    objects = [
        {
            "label": prompt,
            "confidence": round(min(0.99, max(0.4, confidence + random.uniform(-0.05, 0.25))), 2),
            "bbox": [
                random.randint(0, 40),
                random.randint(0, 40),
                random.randint(60, 160),
                random.randint(60, 160),
            ],
        }
        for _ in range(k)
    ]
    return 200, {
        "status": "ok",
        "objects_count": k,
        "preview_url": f"/media/{ds.id}/{img['rel']}",
        "result": {"objects": objects},
    }


@route_post(r"^/api/datasets/(?P<id>[\w-]+)/autolabel/start$")
def h_autolabel_start(handler, m, body):
    ds = require_dataset(m.group("id"))
    if ds.autolabel["status"] == "running":
        return 200, {"status": "already_running"}
    thread = threading.Thread(target=run_autolabel, args=(ds,), daemon=True)
    thread.start()
    return 200, {"status": "started"}


@route_get(r"^/api/datasets/(?P<id>[\w-]+)/autolabel/status$")
def h_autolabel_status(handler, m, body):
    ds = require_dataset(m.group("id"))
    with ds.lock:
        return 200, {
            "status": ds.autolabel["status"],
            "total_images": ds.autolabel["progress"]["total"],
            "processed_images": ds.autolabel["progress"]["done"],
            "failed_images": len(ds.autolabel["errors"]),
            "errors": list(ds.autolabel["errors"]),
        }


@route_post(r"^/api/datasets/(?P<id>[\w-]+)/autolabel/stop$")
def h_autolabel_stop(handler, m, body):
    ds = require_dataset(m.group("id"))
    with ds.lock:
        ds.autolabel["stop_flag"] = True
    return 200, {"status": "stopping"}


@route_post(r"^/api/datasets/(?P<id>[\w-]+)/cvat/export$")
def h_cvat_export(handler, m, body):
    ds = require_dataset(m.group("id"))
    payload = json.loads(body or b"{}")
    fmt = payload.get("format", "yolo")
    path = build_export_zip(ds, fmt)
    return 200, {
        "status": "OK",
        "format": fmt,
        "archive_url": f"/media/{ds.id}/{path}",
    }


@route_get(r"^/api/datasets/(?P<id>[\w-]+)/results$")
def h_results(handler, m, body):
    ds = require_dataset(m.group("id"))
    build_results_files(ds)
    previews = [
        f"/media/{ds.id}/{img['rel']}"
        for img in ds.images
        if img["id"] in ds.rep["approved"]
    ] or [f"/media/{ds.id}/{img['rel']}" for img in ds.images[:6]]

    return 200, {
        "annotations_url": f"/media/{ds.id}/results/annotations.json",
        "errors_url": f"/media/{ds.id}/results/errors.json",
        "cvat_export": (
            {
                "status": "ready",
                "format": "yolo",
                "archive_url": f"/media/{ds.id}/{ds.exports['yolo']}",
            }
            if "yolo" in ds.exports
            else {"status": "not_created"}
        ),
        "previews": previews,
        "errors": ds.autolabel["errors"],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "AutoLayoutMock/0.1"

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} - {fmt % args}")

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type=None):
        if not path.exists() or not path.is_file():
            self._send_json(404, {"detail": f"file not found: {path.name}"})
            return
        ctype = content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self, routes, body):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            return self._send_file(ROOT / "index.html", "text/html; charset=utf-8")
        if path.startswith("/static/"):
            return self._send_file(ROOT / path.lstrip("/"))
        if path.startswith("/media/"):
            rest = path[len("/media/"):]
            dataset_id, _, rel = rest.partition("/")
            try:
                ds = require_dataset(dataset_id)
            except KeyError as e:
                return self._send_json(404, {"detail": str(e)})
            return self._send_file(ds.root / rel)

        for regex, fn in routes:
            m = regex.match(path)
            if m:
                try:
                    status, payload = fn(self, m, body)
                except KeyError as e:
                    status, payload = 404, {"detail": str(e)}
                except Exception as e:
                    traceback.print_exc()
                    status, payload = 500, {"detail": f"{type(e).__name__}: {e}"}
                return self._send_json(status, payload)

        self._send_json(404, {"detail": f"no route for {self.command} {path}"})

    def do_GET(self):
        self._dispatch(ROUTES_GET, None)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        self._dispatch(ROUTES_POST, body)


def main():
    import sys

    DATA_DIR.mkdir(exist_ok=True)
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Mock backend запущен: http://localhost:{port}")
    print(f"Данные распакованных датасетов лежат в: {DATA_DIR}")
    print("Остановить: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")


if __name__ == "__main__":
    main()
