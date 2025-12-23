# send get request to http://localhost:4001/detection_info 

import requests
from pprint import pprint
import configparser


def get_detection_info():
    cfg_file_name = "user.cfg"
    user_cfg_section = "yolo_config"
    config = configparser.ConfigParser()
    config.read(cfg_file_name)
    yolo_url = config.get(user_cfg_section, "yolo_url")
    response = requests.get(yolo_url)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: {response.status_code}")
        return None

if __name__ == "__main__":
    while True:
        detection_info = get_detection_info()
        if detection_info:
            # pprint(detection_info)
            # extract boxes, classes, confidences
            boxes = detection_info.get("boxes", [])
            classes = detection_info.get("classes", [])
            confidences = detection_info.get("confidences", [])
            image_width = detection_info.get("image_width", 0)
            image_height = detection_info.get("image_height", 0)
            print(f"Image Size: {image_width}x{image_height}")
            print(f"Boxes: {boxes}")
            print(f"Classes: {classes}")
            print(f"Confidences: {confidences}")
        else:
            print("No detection info available.")
