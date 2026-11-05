import argparse
import time
import cv2
import atexit
import threading
from flask import Flask, Response
import time
import random
from ultralytics import YOLO
from pathlib import Path



class FrameInferencer:
    def __init__(self, model_id="auv-amalgam/2"):
        self.model = YOLO(Path() / "models" / "best.pt")
        self.detections_xyxy = []
        self.detections_label = []
        self.detections_confidence = []
        self.annotated_image = None
        # Gate : white, Orange Flare : orange, Blue Pail : blue, Red Pail : red, other : random color
        #names= ['Ball', 'Blue flare', 'Blue pail', 'Gate', 'Orange flare', 'Red flare', 'Red pail', 'Yellow flare']
        self.class_colors = {
            0: (0, 255, 255),    # Ball - Yellow
            1: (255, 140, 0),    # Blue flare - Dark blue
            2: (255, 0, 0),      # Blue pail - Blue
            3: (255, 255, 255),  # Gate - White
            4: (0, 140, 255),    # Orange flare - Dark Orange
            5: (0, 0, 255),      # Red flare - Red
            6: (0, 0, 139),      # Red pail - Dark Red
            7: (0, 255, 255)     # Yellow flare - Yellow
        }
        self.detect_info : dict | None = None  # json like dict to hold detection info

    def infer_frame(self, image):
        device = "mps"
        results = self.model(image, verbose=False,device=device)[0]
        boxes = results.boxes.xyxy.cpu().numpy()
        classes = results.boxes.cls.cpu().numpy().astype(int)
        confidences = results.boxes.conf.cpu().numpy()

        # Keep only the most confident Blue Pail (class name "Blue Pail")
        blue_pail_indices = [i for i, cls in enumerate(classes) if self.model.names[cls] == "Blue Pail"]
        if len(blue_pail_indices) > 1:
            best_idx = max(blue_pail_indices, key=lambda i: confidences[i])
            keep_indices = [i for i in range(len(classes)) if i not in blue_pail_indices or i == best_idx]
            boxes = boxes[keep_indices]
            classes = classes[keep_indices]
            confidences = confidences[keep_indices]

        detected_counts = {"Gate": 0, "Orange Flare": 0, "Blue Pail": 0}
        
        # convert result to json like dict
        detect_info = {
            "boxes": boxes.tolist(),
            "classes": [self.model.names[cls] for cls in classes],
            "confidences": confidences.tolist(),
            "image_width": image.shape[1],
            "image_height": image.shape[0]
        }
        self.detect_info = detect_info

        for (box, cls, conf) in zip(boxes, classes, confidences):
            class_name = self.model.names[cls]
            if conf < 0.5:
                continue
            # Skip if max count for this class is reached
            if class_name in detected_counts and detected_counts[class_name] >= 1:
                continue

            x1, y1, x2, y2 = map(int, box)
            label = f"{class_name} {conf:.2f}"
            color = self.class_colors[cls]  # pick color for this class
            cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
            cv2.putText(image, label, (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

            if class_name in detected_counts:
                detected_counts[class_name] += 1

        self.annotated_image = image
        return image

    def display_results(self):
        # Display the image
        if self.annotated_image is not None:
            cv2.imshow("YOLO", self.annotated_image)


app = Flask(__name__)
# 0 and 1 is the usb camera connected, 2 is OBS virtual camera (use this one)
cap = None
inferencer = FrameInferencer()
lock = threading.Lock()
current_frame = None

# Read frames in a background thread
def capture_frames():
    global current_frame
    prev_time = 0
    fps = 0
    preview_path = Path("media") / "camera_preview.jpg"
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    last_preview_time = 0.0
    while cap.isOpened():
        success, frame = cap.read()
        if not success:
            continue
        with lock:
            # resize frame for faster inference if needed
            width, height = frame.shape[1], frame.shape[0]
            frame = cv2.resize(frame, (width // 2, height // 2))
            yolo_output = inferencer.infer_frame(frame)
            # --- FPS calculation ---
            curr_time = time.time()
            fps = 1.0 / (curr_time - prev_time) if prev_time > 0 else 0
            prev_time = curr_time
            # --- Display FPS on frame ---
            cv2.putText(yolo_output, f"FPS: {fps:.2f}", (10, 30),cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

            if curr_time - last_preview_time >= 1.0:
                if not cv2.imwrite(str(preview_path), yolo_output):
                    print(f"Could not update camera preview at {preview_path}")
                last_preview_time = curr_time

            ret, buffer = cv2.imencode('.jpg', yolo_output)
            current_frame = buffer.tobytes()


# Clean up camera on exit
@atexit.register
def cleanup():
    if cap is not None and cap.isOpened():
        cap.release()
        print("Camera released")


# Yield current frame for each client
def gen_frames():
    while True:
        with lock:
            if current_frame is None:
                continue
            frame = current_frame
        yield (b'--frame\r\n'b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')


@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/detection_info', methods=['GET'])
def detection_info():
    with lock:
        info = inferencer.detect_info
    if info is None:
        return {"detections": []}
    return info

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Run the YOLO camera server.")
    parser.add_argument(
        "--camera-idx",
        type=int,
        default=0,
        help="Camera device index to use (default: 0).",
    )
    args = parser.parse_args()
    print()
    print(f"==" * 15)
    print(f"Using camera index: {args.camera_idx}")
    print("Go to http://localhost:4001/video_feed to view the camera feed!!!.")
    print(f"==" * 15)
    print()

    cap = cv2.VideoCapture(args.camera_idx)
    threading.Thread(target=capture_frames, daemon=True).start()
    app.run(debug=True, host="0.0.0.0", port=4001)
