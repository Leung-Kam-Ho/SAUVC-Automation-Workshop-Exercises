"""Unified camera service for TCP streaming, pole-label OCR, and YOLO.

The camera is opened once and frames are shared by every enabled feature:

* Raw frames are always sent to the ROS2 UI over TCP using a 4-byte
  big-endian length prefix followed by a JPEG payload. Recording to segmented
  MP4 files is optional.
* OCR is enabled by the ``ocr`` section of the YAML file passed with
  ``--config``. It posts recognized pole labels to the web server and saves
  timestamped snapshots.
* ``/video_feed`` always serves the camera. It shows YOLO-annotated frames
  when the ``yolo`` section is present and raw frames otherwise.
* YOLO is enabled by the ``yolo`` section, which also adds
  ``/detection_info``.
* A missing ``ocr`` or ``yolo`` section disables that feature. With neither
  section present the script acts as the normal camera service: video feed,
  TCP stream, and optional recording.

Every option lives in the YAML config. ``--config`` is the only CLI flag; with
no config file the script uses camera, TCP stream, and recording defaults.

Examples:
    uv run python utils/vision_service.py
    uv run --extra yolo python utils/vision_service.py --config utils/config.yaml
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2


DEFAULT_POLE_URL = "http://localhost:3601/grid_pole_number"
DEFAULT_IMAGES_DIR = Path("images")
DEFAULT_OCR_INTERVAL = 1.0
DEFAULT_SAVE_INTERVAL = 2.0
DEFAULT_CONFIDENCE = 0.5
DEFAULT_RESIZE = 1.0
DEFAULT_TCP_HOST = "127.0.0.1"
DEFAULT_TCP_PORT = 5003
DEFAULT_TCP_WIDTH = 854
DEFAULT_TCP_HEIGHT = 480
DEFAULT_JPEG_QUALITY = 80
DEFAULT_SEGMENT_SECONDS = 300.0
DEFAULT_CAMERA_WIDTH = 1920
DEFAULT_CAMERA_HEIGHT = 1080
DEFAULT_CAMERA_FPS = 30
DEFAULT_CAMERA_FOURCC = "MJPG"
DEFAULT_SERVER_HOST = "0.0.0.0"
DEFAULT_SERVER_PORT = 4001
TCP_RECONNECT_DELAY = 2.0
LABEL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z])\s*-\s*([0-9]+)(?![A-Za-z0-9])"
)
OCR_CONFIG = (
    "--psm 11 "
    "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz-0123456789"
)
POST_RETRY_INTERVAL = 5.0
CLASS_PALETTE = (
    (0, 255, 255),
    (255, 140, 0),
    (255, 0, 0),
    (255, 255, 255),
    (0, 140, 255),
    (0, 0, 255),
    (0, 0, 139),
    (255, 0, 255),
)


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClassOptions:
    color: tuple[int, int, int] | None = None
    max_count: int | None = None


@dataclass(frozen=True)
class OcrConfig:
    pole_url: str = DEFAULT_POLE_URL
    interval: float = DEFAULT_OCR_INTERVAL
    save_interval: float = DEFAULT_SAVE_INTERVAL
    images_dir: Path = DEFAULT_IMAGES_DIR


@dataclass(frozen=True)
class CameraConfig:
    index: int | None = None
    width: int = DEFAULT_CAMERA_WIDTH
    height: int = DEFAULT_CAMERA_HEIGHT
    fps: int = DEFAULT_CAMERA_FPS
    fourcc: str = DEFAULT_CAMERA_FOURCC


@dataclass(frozen=True)
class ServerConfig:
    host: str = DEFAULT_SERVER_HOST
    port: int = DEFAULT_SERVER_PORT


@dataclass(frozen=True)
class YoloConfig:
    model_path: Path
    device: str | None = None
    confidence: float = DEFAULT_CONFIDENCE
    resize: float = DEFAULT_RESIZE
    classes: dict[str, ClassOptions] | None = None


@dataclass(frozen=True)
class StreamConfig:
    enabled: bool = False
    host: str = DEFAULT_TCP_HOST
    port: int = DEFAULT_TCP_PORT
    width: int = DEFAULT_TCP_WIDTH
    height: int = DEFAULT_TCP_HEIGHT
    quality: int = DEFAULT_JPEG_QUALITY
    record_dir: Path | None = None
    segment_seconds: float = DEFAULT_SEGMENT_SECONDS


@dataclass(frozen=True)
class ServiceConfig:
    camera: CameraConfig
    server: ServerConfig
    ocr: OcrConfig | None
    yolo: YoloConfig | None
    stream: StreamConfig


def find_label(text: str) -> str | None:
    """Return the first pole label in OCR text, normalized to uppercase."""
    match = LABEL_PATTERN.search(text)
    return f"{match.group(1)}-{match.group(2)}".upper() if match else None


def read_label(frame: Any) -> str | None:
    """Try contrast-enhanced and thresholded versions of a camera frame."""
    import pytesseract

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    contrast = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    enlarged = cv2.resize(
        contrast, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC
    )
    _, thresholded = cv2.threshold(
        enlarged, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )

    for image, config in (
        (enlarged, OCR_CONFIG),
        (thresholded, OCR_CONFIG),
        (enlarged, OCR_CONFIG.replace("--psm 11", "--psm 6")),
    ):
        text = pytesseract.image_to_string(image, config=config)
        label = find_label(text)
        if label:
            return label
    return None


class OcrWorker:
    def __init__(self, config: OcrConfig):
        self.config = config
        self.last_sent_label: str | None = None
        self.last_attempt_label: str | None = None
        self.last_attempt_time = 0.0
        self.last_observed_label: str | None = None
        self.next_ocr_time = 0.0

    def due(self, now: float) -> bool:
        return now >= self.next_ocr_time

    def process(self, frame: Any) -> None:
        now = time.monotonic()
        self.next_ocr_time = now + self.config.interval

        label = read_label(frame)
        if label is None:
            self.last_observed_label = None
            return

        if label != self.last_observed_label:
            print(f"Detected pole label: {label}")
            self.last_observed_label = label

        now = time.monotonic()
        retry_due = now - self.last_attempt_time >= POST_RETRY_INTERVAL
        if label == self.last_sent_label:
            return
        if label == self.last_attempt_label and not retry_due:
            return

        self.last_attempt_label = label
        self.last_attempt_time = now
        if self.post_label(label):
            self.last_sent_label = label
            self.last_attempt_label = None
            self.last_attempt_time = 0.0

    def post_label(self, label: str) -> bool:
        payload = json.dumps({"value": label}).encode("utf-8")
        request = urllib.request.Request(
            self.config.pole_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=2.0) as response:
                if 200 <= response.status < 300:
                    print(f"Sent pole label {label}")
                    print(
                        f"POST {self.config.pole_url} returned HTTP "
                        f"{response.status}"
                    )
                    return True
                print(
                    f"POST {self.config.pole_url} returned HTTP "
                    f"{response.status}"
                )
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"Could not send pole label to {self.config.pole_url}: {exc}")

        return False


class YoloInferencer:
    def __init__(self, config: YoloConfig):
        try:
            from ultralytics import YOLO
        except ModuleNotFoundError as exc:
            raise ConfigError(
                "YOLO is configured but ultralytics is not installed. "
                "Install it with `uv sync --extra yolo`."
            ) from exc

        self.config = config
        self.model = YOLO(str(config.model_path))
        raw_names = self.model.names
        if isinstance(raw_names, dict):
            self.names = {
                int(class_id): str(name)
                for class_id, name in raw_names.items()
            }
        else:
            self.names = {
                index: str(name) for index, name in enumerate(raw_names)
            }

        if config.classes is None:
            self.tracked_classes = {
                name: ClassOptions() for name in self.names.values()
            }
        else:
            model_class_names = set(self.names.values())
            missing = sorted(set(config.classes) - model_class_names)
            if missing:
                raise ConfigError(
                    "Unknown YOLO class name(s) in config: "
                    + ", ".join(missing)
                )
            self.tracked_classes = dict(config.classes)

        self.colors = self._build_colors()

    def _build_colors(self) -> dict[str, tuple[int, int, int]]:
        colors: dict[str, tuple[int, int, int]] = {}
        for index, (name, options) in enumerate(self.tracked_classes.items()):
            colors[name] = options.color or CLASS_PALETTE[
                index % len(CLASS_PALETTE)
            ]
        return colors

    def infer_frame(self, frame: Any) -> tuple[Any, dict[str, Any]]:
        image = frame
        if self.config.resize != 1.0:
            interpolation = (
                cv2.INTER_AREA
                if self.config.resize < 1.0
                else cv2.INTER_LINEAR
            )
            image = cv2.resize(
                frame,
                None,
                fx=self.config.resize,
                fy=self.config.resize,
                interpolation=interpolation,
            )

        results = self.model(
            image, verbose=False, device=self.config.device
        )[0]
        boxes = results.boxes.xyxy.cpu().numpy()
        classes = results.boxes.cls.cpu().numpy().astype(int)
        confidences = results.boxes.conf.cpu().numpy()

        kept_boxes = []
        kept_classes = []
        kept_confidences = []
        for box, class_id, confidence in zip(boxes, classes, confidences):
            class_name = self.names.get(int(class_id))
            if class_name not in self.tracked_classes:
                continue
            kept_boxes.append(box.tolist())
            kept_classes.append(class_name)
            kept_confidences.append(float(confidence))

        detect_info = {
            "boxes": kept_boxes,
            "classes": kept_classes,
            "confidences": kept_confidences,
            "image_width": image.shape[1],
            "image_height": image.shape[0],
        }

        annotated = image.copy()
        detected_counts: dict[str, int] = {}
        for box, class_name, confidence in zip(
            kept_boxes, kept_classes, kept_confidences
        ):
            if confidence < self.config.confidence:
                continue

            options = self.tracked_classes[class_name]
            count = detected_counts.get(class_name, 0)
            if options.max_count is not None and count >= options.max_count:
                continue

            x1, y1, x2, y2 = map(int, box)
            color = self.colors[class_name]
            label = f"{class_name} {confidence:.2f}"
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                annotated,
                label,
                (x1, y1 - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2,
            )
            detected_counts[class_name] = count + 1

        return annotated, detect_info


class FrameBuffer:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._sequence = 0
        self._jpeg: bytes | None = None
        self._detection_info: dict[str, Any] | None = None

    def publish(self, jpeg: bytes, detection_info: dict[str, Any] | None) -> None:
        with self._condition:
            self._sequence += 1
            self._jpeg = jpeg
            self._detection_info = detection_info
            self._condition.notify_all()

    def wait_for_frame(
        self, last_sequence: int, timeout: float = 5.0
    ) -> tuple[int, bytes | None]:
        with self._condition:
            self._condition.wait_for(
                lambda: self._sequence > last_sequence, timeout=timeout
            )
            return self._sequence, self._jpeg

    def detection_info(self) -> dict[str, Any] | None:
        with self._condition:
            return self._detection_info


def create_app(buffer: FrameBuffer, yolo_enabled: bool):
    try:
        from flask import Flask, Response
    except ModuleNotFoundError as exc:
        raise ConfigError(
            "The camera server requires Flask. Install it with `uv sync`."
        ) from exc

    app = Flask(__name__)

    def gen_frames():
        sequence = 0
        while True:
            new_sequence, jpeg = buffer.wait_for_frame(sequence)
            if new_sequence == sequence or jpeg is None:
                continue
            sequence = new_sequence
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            )

    @app.route("/video_feed")
    def video_feed():
        return Response(
            gen_frames(),
            mimetype="multipart/x-mixed-replace; boundary=frame",
        )

    if yolo_enabled:

        @app.route("/detection_info", methods=["GET"])
        def detection_info():
            return buffer.detection_info() or {"detections": []}

    return app


def start_server(app: Any, host: str, port: int) -> None:
    bind_host = "::" if host in {"0.0.0.0", "::"} else host

    def serve() -> None:
        options = {
            "host": bind_host,
            "port": port,
            "debug": False,
            "use_reloader": False,
            "threaded": True,
        }
        try:
            app.run(**options)
        except OSError:
            if bind_host != "::":
                raise
            print("IPv6 bind failed; falling back to IPv4.")
            options["host"] = "0.0.0.0"
            app.run(**options)

    thread = threading.Thread(
        target=serve,
        name="video-server",
        daemon=True,
    )
    thread.start()


def display_server_host(host: str) -> str:
    if host in {"0.0.0.0", "::"}:
        return "localhost"
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


class TcpStreamer:
    def __init__(self, config: StreamConfig):
        self.config = config
        self.socket: socket.socket | None = None
        self.next_connect_time = 0.0

    def send(self, frame: Any, now: float) -> None:
        if not self.config.enabled:
            return

        if self.socket is None:
            if now < self.next_connect_time:
                return
            self._connect(now)
            if self.socket is None:
                return

        resized = cv2.resize(
            frame,
            (self.config.width, self.config.height),
            interpolation=cv2.INTER_AREA,
        )
        encoded, buffer = cv2.imencode(
            ".jpg",
            resized,
            [cv2.IMWRITE_JPEG_QUALITY, self.config.quality],
        )
        if not encoded:
            return

        payload = buffer.tobytes()
        try:
            self.socket.sendall(len(payload).to_bytes(4, byteorder="big"))
            self.socket.sendall(payload)
        except OSError as exc:
            print(f"TCP stream send failed: {exc}")
            self.close()
            self.next_connect_time = now + TCP_RECONNECT_DELAY

    def _connect(self, now: float) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        try:
            sock.connect((self.config.host, self.config.port))
        except OSError as exc:
            sock.close()
            print(
                "Could not connect to TCP stream "
                f"{self.config.host}:{self.config.port}: {exc}"
            )
            self.next_connect_time = now + TCP_RECONNECT_DELAY
            return

        self.socket = sock
        print(
            "Connected TCP stream to "
            f"{self.config.host}:{self.config.port}."
        )

    def close(self) -> None:
        if self.socket is not None:
            self.socket.close()
            self.socket = None


class SegmentRecorder:
    def __init__(
        self, config: StreamConfig, camera_idx: int, fps: float
    ):
        self.config = config
        self.camera_idx = camera_idx
        self.fps = fps if fps and fps > 0 else 30.0
        self.writer: cv2.VideoWriter | None = None
        self.segment_start = 0.0
        self.segment_index = 0
        self.failed = False

        if config.record_dir is not None:
            try:
                config.record_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ConfigError(
                    "Could not create recording directory "
                    f"{config.record_dir}: {exc}"
                ) from exc

    def write(self, frame: Any, now: float) -> None:
        if self.config.record_dir is None or self.failed:
            return

        if self.writer is None:
            self._start_segment(frame, now)
            if self.writer is None:
                return

        if now - self.segment_start >= self.config.segment_seconds:
            self.writer.release()
            self.writer = None
            self._start_segment(frame, now)
            if self.writer is None:
                return

        self.writer.write(frame)

    def _start_segment(self, frame: Any, now: float) -> None:
        height, width = frame.shape[:2]
        self.segment_index += 1
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        path = self.config.record_dir / (
            f"recording_{timestamp}_idx_{self.camera_idx}_"
            f"{self.segment_index:04d}.mp4"
        )
        writer = cv2.VideoWriter(
            str(path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            self.fps,
            (width, height),
        )
        if not writer.isOpened():
            writer.release()
            print(f"Could not start recording to {path}")
            self.failed = True
            return

        self.writer = writer
        self.segment_start = now
        print(f"Recording to {path}")

    def close(self) -> None:
        if self.writer is not None:
            self.writer.release()
            self.writer = None
            print("Recording saved.")


def configure_camera(
    camera: cv2.VideoCapture, config: CameraConfig
) -> None:
    camera.set(
        cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*config.fourcc)
    )
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, config.width)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, config.height)
    camera.set(cv2.CAP_PROP_FPS, config.fps)


def open_camera(config: CameraConfig) -> tuple[cv2.VideoCapture, int]:
    if config.index is not None:
        camera = cv2.VideoCapture(config.index)
        configure_camera(camera, config)
        if not camera.isOpened():
            camera.release()
            raise ConfigError(f"Could not open camera index {config.index}")
        return camera, config.index

    for candidate_idx in range(5):
        camera = cv2.VideoCapture(candidate_idx)
        if camera.isOpened():
            configure_camera(camera, config)
            success, frame = camera.read()
            if (
                success
                and frame.shape[1] == config.width
                and frame.shape[0] == config.height
            ):
                print(
                    f"Using camera {candidate_idx} at "
                    f"{config.width}x{config.height}."
                )
                return camera, candidate_idx
        camera.release()

    raise ConfigError(
        f"Could not find a {config.width}x{config.height} camera; set "
        "camera.index in the config to choose one."
    )


def resolve_path(value: str | Path) -> Path:
    return Path(value).expanduser()


def parse_float(
    value: Any, name: str, default: float, minimum: float = 0.0
) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if parsed < minimum:
        raise ConfigError(f"{name} must be at least {minimum}")
    return parsed


def parse_int(
    value: Any,
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ConfigError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


def parse_camera_config(value: Any) -> CameraConfig:
    if value is None:
        return CameraConfig()
    if not isinstance(value, dict):
        raise ConfigError("camera must be a mapping")

    index = value.get("index")
    if index is not None:
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ConfigError("camera.index must be a non-negative integer")

    fourcc = value.get("fourcc", DEFAULT_CAMERA_FOURCC)
    if not isinstance(fourcc, str) or len(fourcc) != 4:
        raise ConfigError("camera.fourcc must be a four-character string")

    return CameraConfig(
        index=index,
        width=parse_int(
            value.get("width"),
            "camera.width",
            DEFAULT_CAMERA_WIDTH,
            1,
            16384,
        ),
        height=parse_int(
            value.get("height"),
            "camera.height",
            DEFAULT_CAMERA_HEIGHT,
            1,
            16384,
        ),
        fps=parse_int(
            value.get("fps"),
            "camera.fps",
            DEFAULT_CAMERA_FPS,
            1,
            1000,
        ),
        fourcc=fourcc,
    )


def parse_server_config(value: Any) -> ServerConfig:
    if value is None:
        return ServerConfig()
    if not isinstance(value, dict):
        raise ConfigError("server must be a mapping")

    host = value.get("host", DEFAULT_SERVER_HOST)
    if not isinstance(host, str) or not host:
        raise ConfigError("server.host must be a non-empty string")

    return ServerConfig(
        host=host,
        port=parse_int(
            value.get("port"),
            "server.port",
            DEFAULT_SERVER_PORT,
            1,
            65535,
        ),
    )


def parse_color(value: Any, name: str) -> tuple[int, int, int] | None:
    if value is None:
        return None
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 3
        or any(not isinstance(channel, int) for channel in value)
        or any(channel < 0 or channel > 255 for channel in value)
    ):
        raise ConfigError(
            f"{name} color must be a BGR list of three integers from 0 to 255"
        )
    return int(value[0]), int(value[1]), int(value[2])


def parse_classes(value: Any) -> dict[str, ClassOptions] | None:
    if value is None:
        return None

    if isinstance(value, list):
        entries = {name: None for name in value}
    elif isinstance(value, dict):
        entries = value
    else:
        raise ConfigError("yolo.classes must be a list or mapping")

    classes: dict[str, ClassOptions] = {}
    for class_name, raw_options in entries.items():
        if not isinstance(class_name, str):
            raise ConfigError("yolo.classes keys must be class-name strings")
        if raw_options is None:
            raw_options = {}
        if not isinstance(raw_options, dict):
            raise ConfigError(
                f"yolo.classes.{class_name} must be a mapping"
            )

        max_count = raw_options.get("max_count")
        if max_count is not None:
            if not isinstance(max_count, int) or max_count < 1:
                raise ConfigError(
                    f"yolo.classes.{class_name}.max_count must be a positive "
                    "integer"
                )

        classes[class_name] = ClassOptions(
            color=parse_color(
                raw_options.get("color"), f"yolo.classes.{class_name}"
            ),
            max_count=max_count,
        )
    return classes


def parse_ocr_config(value: Any) -> OcrConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConfigError("ocr must be a mapping")

    interval = parse_float(value.get("interval"), "ocr.interval", 1.0)
    save_interval = parse_float(
        value.get("save_interval"), "ocr.save_interval", 2.0
    )
    pole_url = value.get("pole_url", DEFAULT_POLE_URL)
    if not isinstance(pole_url, str) or not pole_url:
        raise ConfigError("ocr.pole_url must be a non-empty string")

    images_dir = resolve_path(value.get("images_dir", DEFAULT_IMAGES_DIR))
    return OcrConfig(
        pole_url=pole_url,
        interval=interval,
        save_interval=save_interval,
        images_dir=images_dir,
    )


def parse_yolo_config(value: Any) -> YoloConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConfigError("yolo must be a mapping")

    model = value.get("model", value.get("model_path"))
    if not isinstance(model, str) or not model:
        raise ConfigError("yolo.model is required when yolo is configured")

    confidence = parse_float(
        value.get("confidence"), "yolo.confidence", DEFAULT_CONFIDENCE
    )
    if confidence > 1.0:
        raise ConfigError("yolo.confidence must be between 0 and 1")

    resize = parse_float(value.get("resize"), "yolo.resize", DEFAULT_RESIZE)
    if resize <= 0.0:
        raise ConfigError("yolo.resize must be greater than 0")

    device = value.get("device")
    if device is not None and not isinstance(device, str):
        raise ConfigError("yolo.device must be a string")

    return YoloConfig(
        model_path=resolve_path(model),
        device=device or None,
        confidence=confidence,
        resize=resize,
        classes=parse_classes(value.get("classes")),
    )


def parse_stream_config(value: Any) -> StreamConfig:
    if value is None:
        return StreamConfig()
    if not isinstance(value, dict):
        raise ConfigError("stream must be a mapping")

    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("stream.enabled must be true or false")

    host = value.get("host", value.get("ip", DEFAULT_TCP_HOST))
    if not isinstance(host, str) or not host:
        raise ConfigError("stream.host must be a non-empty string")

    record_dir_value = value.get("record_dir", value.get("save_dir"))
    record_dir = None
    if record_dir_value is not None:
        if not isinstance(record_dir_value, str) or not record_dir_value:
            raise ConfigError("stream.record_dir must be a non-empty string")
        record_dir = resolve_path(record_dir_value)

    return StreamConfig(
        enabled=enabled,
        host=host,
        port=parse_int(
            value.get("port"),
            "stream.port",
            DEFAULT_TCP_PORT,
            1,
            65535,
        ),
        width=parse_int(
            value.get("width"),
            "stream.width",
            DEFAULT_TCP_WIDTH,
            1,
            16384,
        ),
        height=parse_int(
            value.get("height"),
            "stream.height",
            DEFAULT_TCP_HEIGHT,
            1,
            16384,
        ),
        quality=parse_int(
            value.get("quality"),
            "stream.quality",
            DEFAULT_JPEG_QUALITY,
            1,
            100,
        ),
        record_dir=record_dir,
        segment_seconds=parse_float(
            value.get("segment_seconds"),
            "stream.segment_seconds",
            DEFAULT_SEGMENT_SECONDS,
            0.1,
        ),
    )


def load_config(path: Path) -> ServiceConfig:
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise ConfigError(
            "Reading --config requires PyYAML. Install it with `uv sync`."
        ) from exc

    try:
        with path.open("r", encoding="utf-8") as config_file:
            raw = yaml.safe_load(config_file)
    except OSError as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse config file {path}: {exc}") from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("Config file must contain a YAML mapping")

    return ServiceConfig(
        camera=parse_camera_config(raw.get("camera")),
        server=parse_server_config(raw.get("server")),
        ocr=parse_ocr_config(raw.get("ocr")),
        yolo=parse_yolo_config(raw.get("yolo")),
        stream=parse_stream_config(raw.get("stream")),
    )


def save_timestamped_frame(frame: Any, images_dir: Path) -> None:
    captured_at = datetime.now()
    timestamp = captured_at.strftime("%Y%m%d_%H%M%S_%f")
    timestamp_text = captured_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    image_path = images_dir / f"frame_{timestamp}.jpg"

    saved_frame = frame.copy()
    text_size, _ = cv2.getTextSize(
        timestamp_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1
    )
    frame_height = saved_frame.shape[0]
    text_origin = (12, max(text_size[1] + 12, frame_height - 12))
    cv2.putText(
        saved_frame,
        timestamp_text,
        text_origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (0, 0, 0),
        3,
        cv2.LINE_AA,
    )
    cv2.putText(
        saved_frame,
        timestamp_text,
        text_origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )

    if cv2.imwrite(str(image_path), saved_frame):
        print(f"Saved frame: {image_path}")
    else:
        print(f"Could not save frame to {image_path}")


def run_service(args: argparse.Namespace) -> None:
    config = ServiceConfig(
        camera=CameraConfig(),
        server=ServerConfig(),
        ocr=None,
        yolo=None,
        stream=StreamConfig(),
    )
    if args.config is not None:
        config = load_config(args.config)

    if config.ocr is None:
        print("OCR config not found; OCR is disabled.")
    if config.yolo is None:
        print("YOLO config not found; YOLO inference is disabled.")
    if config.stream.enabled:
        print(
            "TCP stream target: "
            f"{config.stream.host}:{config.stream.port}."
        )
    else:
        print("TCP stream is disabled.")
    if config.stream.record_dir is not None:
        print(f"Recording directory: {config.stream.record_dir}.")

    ocr_worker: OcrWorker | None = None
    if config.ocr is not None:
        try:
            pytesseract.get_tesseract_version()
        except pytesseract.TesseractNotFoundError as exc:
            raise ConfigError(
                "Tesseract is not installed or not on PATH. Install the "
                "system Tesseract OCR executable and try again."
            ) from exc
        try:
            config.ocr.images_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(
                "Could not create image directory "
                f"{config.ocr.images_dir}: {exc}"
            ) from exc
        ocr_worker = OcrWorker(config.ocr)
        print(
            f"OCR enabled; posting labels to {config.ocr.pole_url} and "
            f"saving frames to {config.ocr.images_dir}."
        )

    inferencer: YoloInferencer | None = None
    if config.yolo is not None:
        inferencer = YoloInferencer(config.yolo)
        print(f"YOLO enabled with model {config.yolo.model_path}.")

    buffer = FrameBuffer()
    app = create_app(buffer, yolo_enabled=inferencer is not None)
    camera, camera_idx = open_camera(config.camera)
    recorder: SegmentRecorder | None = None
    streamer: TcpStreamer | None = None

    try:
        recorder = SegmentRecorder(
            config.stream,
            camera_idx,
            camera.get(cv2.CAP_PROP_FPS),
        )
        streamer = TcpStreamer(config.stream)

        start_server(app, config.server.host, config.server.port)
        feed_kind = "YOLO" if inferencer is not None else "raw"
        display_host = display_server_host(config.server.host)
        print(
            f"Serving {feed_kind} video feed at "
            f"http://{display_host}:{config.server.port}/video_feed"
        )

        print(f"Using camera index {camera_idx}.")
        print("Press Ctrl+C to stop.")

        next_frame_save = time.monotonic() + (
            config.ocr.save_interval if config.ocr is not None else 0.0
        )
        previous_frame_time = 0.0

        while True:
            success, frame = camera.read()
            if not success:
                print(f"Could not read a frame from camera {camera_idx}")
                time.sleep(0.2)
                continue

            now = time.monotonic()
            recorder.write(frame, now)

            if ocr_worker is not None and config.ocr is not None:
                if now >= next_frame_save:
                    save_timestamped_frame(frame, config.ocr.images_dir)
                    next_frame_save = now + config.ocr.save_interval
                if ocr_worker.due(now):
                    ocr_worker.process(frame)

            detection_info = None
            output_frame = frame
            if inferencer is not None:
                output_frame, detection_info = inferencer.infer_frame(frame)
                fps = (
                    1.0 / (now - previous_frame_time)
                    if previous_frame_time > 0
                    else 0.0
                )
                previous_frame_time = now
                cv2.putText(
                    output_frame,
                    f"FPS: {fps:.2f}",
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1,
                    (0, 255, 0),
                    2,
                )

            encoded, jpeg = cv2.imencode(".jpg", output_frame)
            if encoded:
                buffer.publish(jpeg.tobytes(), detection_info)

            streamer.send(output_frame, now)
    finally:
        camera.release()
        if recorder is not None:
            recorder.close()
        if streamer is not None:
            streamer.close()
        print("Camera released.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="YAML file containing camera, stream, ocr, and yolo settings",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        run_service(args)
    except KeyboardInterrupt:
        print("Vision service stopped.")
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
