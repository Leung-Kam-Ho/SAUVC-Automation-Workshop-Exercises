from time import time
try:
    from geometry_msgs.msg import Twist
except ImportError:
    from Minecraft_Tool.minecraft_override_config import Twist
    
import py_trees
from Minecraft_Tool.minecraft_override_config import MinecraftOverrideConfig
from YOLO_Tool.get_detect_info import get_detection_info



class AvoidBasedOnDetection_Sway(py_trees.behaviour.Behaviour):
    def __init__(self, node: MinecraftOverrideConfig, target_class: str, center_percent: float = 0.05, move_duration: float = 0.5, conf_threshold: float = 0.5):
        """
        :param target_class: Name of the object class to track
        :param center_percent: threshold as percentage of image width from center to trigger movement
        :param move_duration: how long each movement lasts (seconds)
        :param conf_threshold: minimum confidence to consider the detection valid
        """
        super().__init__("AvoidBasedOnDetection_Sway")
        self.target_class = target_class
        self.center_percent = center_percent
        self.node = node
        self.move_duration = move_duration
        self.conf_threshold = conf_threshold
        self._action_node = None

    def update(self):
        detection_info = get_detection_info()
        if detection_info:
            boxes = detection_info.get("boxes", [])
            classes = detection_info.get("classes", [])
            scores = detection_info.get("confidences", [])
            image_width = detection_info.get("image_width", 1)  # avoid division by zero

            for box, cls, score in zip(boxes, classes, scores):
                if cls == self.target_class and score >= self.conf_threshold:
                    x_min, y_min, x_max, y_max = box
                    box_center_x = (x_min + x_max) / 2
                    image_center_x = image_width / 2
                    offset = (box_center_x - image_center_x) / image_width  # normalized offset

                    if offset > self.center_percent:
                        self.feedback_message = f"{cls} ({score:.2f}) is {offset * 100:.1f}% right → move left"
                        self._action_node = MoveLeft(self.node, self.move_duration)
                    elif offset < -self.center_percent:
                        self.feedback_message = f"{cls} ({score:.2f}) is {abs(offset) * 100:.1f}% left → move right"
                        self._action_node = MoveRight(self.node, self.move_duration)
                    else:
                        self.feedback_message = f"{cls} is near center → no lateral movement"
                        return py_trees.common.Status.FAILURE

                    self._action_node.initialise()
                    return self._action_node.update()

        self.feedback_message = "No detection info available, target not found"
        return py_trees.common.Status.FAILURE


class MoveBasedOnDetection_Sway(py_trees.behaviour.Behaviour):
    def __init__(self, node: MinecraftOverrideConfig, target_class: str, center_percent: float = 0.05, move_duration: float = 1.0, center_hold_time: float = 2.0, conf_threshold: float = 0.5):
        """
        :param target_class: Name of the object class to track
        :param center_percent: threshold as percentage of image width from center to trigger movement
        :param move_duration: how long each movement lasts (seconds)
        :param center_hold_time: how long object must stay near center to succeed (seconds)
        :param conf_threshold: minimum confidence to consider the detection valid
        """
        super().__init__("MoveBasedOnDetection_Sway")
        self.target_class = target_class
        self.center_percent = center_percent
        self.node = node
        self.move_duration = move_duration
        self.center_hold_time = center_hold_time
        self.conf_threshold = conf_threshold
        self._action_node = None
        self._center_start_time = None

    def update(self):
        detection_info = get_detection_info()
        if detection_info:
            boxes = detection_info.get("boxes", [])
            classes = detection_info.get("classes", [])
            scores = detection_info.get("confidences", [])
            image_width = detection_info.get("image_width", 1)  # avoid division by zero

            for box, cls, score in zip(boxes, classes, scores):
                if cls == self.target_class and score >= self.conf_threshold:
                    x_min, y_min, x_max, y_max = box
                    box_center_x = (x_min + x_max) / 2
                    image_center_x = image_width / 2
                    offset = (box_center_x - image_center_x) / image_width  # normalized offset

                    if offset > self.center_percent:
                        self.feedback_message = f"{cls} ({score:.2f}) is {offset * 100:.1f}% right → move right"
                        self._action_node = MoveRight(self.node, self.move_duration)
                        self._center_start_time = None
                    elif offset < -self.center_percent:
                        self.feedback_message = f"{cls} ({score:.2f}) is {abs(offset) * 100:.1f}% left → move left"
                        self._action_node = MoveLeft(self.node, self.move_duration)
                        self._center_start_time = None
                    else:
                        self.feedback_message = f"{cls} is near center → stop"
                        self.feedback_message += f" (held for {self.center_hold_time - (time() - self._center_start_time):.1f}s)" if self._center_start_time else ""
                        self._action_node = Stop(self.node, self.move_duration)
                        if self._center_start_time is None:
                            self._center_start_time = time()
                        elif time() - self._center_start_time >= self.center_hold_time:
                            self._action_node.initialise()
                            self._action_node.update()
                            self.feedback_message += f" → held center for {self.center_hold_time}s, SUCCESS"
                            return py_trees.common.Status.SUCCESS

                    self._action_node.initialise()
                    return self._action_node.update()

        self.feedback_message = "No detection info available, target not found, or confidence too low."
        self._center_start_time = None
        return py_trees.common.Status.FAILURE


class MoveBasedOnDetection_Sway_Surge(py_trees.behaviour.Behaviour):
    def __init__(self, node: MinecraftOverrideConfig, target_class: str, center_percent: float = 0.05, move_duration: float = 0.5, center_hold_time: float = 2.0, conf_threshold: float = 0.5):
        """
        :param target_class: Name of the object class to track
        :param center_percent: threshold as percentage of image width from center to trigger movement
        :param move_duration: how long each movement lasts (seconds)
        :param center_hold_time: how long object must stay near center to succeed (seconds)
        :param conf_threshold: minimum confidence to consider the detection valid
        """
        super().__init__("MoveBasedOnDetection_Sway_Surge")
        self.target_class = target_class
        self.center_percent = center_percent
        self.node = node
        self.move_duration = move_duration
        self.center_hold_time = center_hold_time
        self.conf_threshold = conf_threshold
        self._action_node = None
        self._center_start_time = None

    def update(self):
        detection_info = get_detection_info()
        if detection_info:
            boxes = detection_info.get("boxes", [])
            classes = detection_info.get("classes", [])
            scores = detection_info.get("confidences", [])
            image_width = detection_info.get("image_width", 1)  # avoid division by zero
            image_height = detection_info.get("image_height", 1)
            for box, cls, score in zip(boxes, classes, scores):
                if cls == self.target_class and score >= self.conf_threshold:
                    x_min, y_min, x_max, y_max = box
                    box_center_x = (x_min + x_max) / 2
                    image_center_x = image_width / 2
                    offset_x = (box_center_x - image_center_x) / image_width  # normalized offset
                    # Sway (left/right)
                    # Surge (forward/backward)
                    box_height = y_max - y_min
                    box_center_y = (y_min + y_max) / 2
                    image_center_y = image_height / 2
                    offset_y = (box_center_y - image_center_y) / image_height  # normalized offset

                    if offset_x > self.center_percent:
                        self.feedback_message = f"{cls} ({score:.2f}) is {offset_x * 100:.1f}% right → move right"
                        self._action_node = MoveRight(self.node, self.move_duration)
                        self._center_start_time = None
                    elif offset_x < -self.center_percent:
                        self.feedback_message = f"{cls} ({score:.2f}) is {abs(offset_x) * 100:.1f}% left → move left"
                        self._action_node = MoveLeft(self.node, self.move_duration)
                        self._center_start_time = None
                    elif abs(offset_y) > self.center_percent:
                        if offset_y > 0:
                            self.feedback_message = f"{cls} ({score:.2f}) is {offset_y * 100:.1f}% down → move backward"
                            self._action_node = MoveBackward(self.node, self.move_duration)
                        else:
                            self.feedback_message = f"{cls} ({score:.2f}) is {abs(offset_y) * 100:.1f}% up → move forward"
                            self._action_node = MoveForward(self.node, self.move_duration)
                        self._center_start_time = None
                    else:
                        self.feedback_message = f"{cls} is near center → stop"
                        self.feedback_message += f" (held for {self.center_hold_time - (time() - self._center_start_time):.1f}s)" if self._center_start_time else ""
                        self._action_node = Stop(self.node, self.move_duration)
                        if self._center_start_time is None:
                            self._center_start_time = time()
                        elif time() - self._center_start_time >= self.center_hold_time:
                            self._action_node.initialise()
                            self._action_node.update()
                            self.feedback_message += f" → held center for {self.center_hold_time}s, SUCCESS"
                            return py_trees.common.Status.SUCCESS

                    self._action_node.initialise()
                    return self._action_node.update()
        # stop if no detection
        self.feedback_message = "No detection info available, target not found, or confidence too low."
        self._center_start_time = None
        return py_trees.common.Status.FAILURE


class DetecedObject(py_trees.behaviour.Behaviour):
    def __init__(self, target_class: str, conf_threshold: float = 0.5):
        super().__init__("DetectedObject")
        self.target_class = target_class
        self.conf_threshold = conf_threshold

    def update(self):
        detection_info = get_detection_info()
        if detection_info:
            classes = detection_info.get("classes", [])
            scores = detection_info.get("confidences", [])

            for cls, score in zip(classes, scores):
                if cls == self.target_class and score >= self.conf_threshold:
                    self.feedback_message = f"Detected {cls} with confidence {score:.2f}"
                    return py_trees.common.Status.SUCCESS

        self.feedback_message = f"{self.target_class} not detected or confidence too low."
        return py_trees.common.Status.FAILURE


class InitWorld(py_trees.behaviour.Behaviour):
    def __init__(self, node : MinecraftOverrideConfig):
        super().__init__("InitWorld")
        self.node = node
        self._done = False

    def initialise(self):
        self.node.init_world()
        self._done = False
        self._start_time = time()

    def update(self):
        if not self._done:
            if time() - self._start_time >= 2:
                self._done = True
                return py_trees.common.Status.SUCCESS
            else:
                return py_trees.common.Status.RUNNING
        return py_trees.common.Status.SUCCESS


class SwitchCameraFront(py_trees.behaviour.Behaviour):
    def __init__(self, node : MinecraftOverrideConfig):
        super().__init__("SwitchCameraFront")
        self.node = node
        self._done = False

    def initialise(self):
        self.node.switch_camera(front=True)
        self._done = False
        self._start_time = time()

    def update(self):
        if not self._done:
            if time() - self._start_time >= 1:
                self._done = True
                return py_trees.common.Status.SUCCESS
            else:
                return py_trees.common.Status.RUNNING
        return py_trees.common.Status.SUCCESS


class SwitchCameraBottom(py_trees.behaviour.Behaviour):
    def __init__(self, node : MinecraftOverrideConfig):
        super().__init__("SwitchCameraBottom")
        self.node = node
        self._done = False

    def initialise(self):
        self.node.switch_camera(front=False)
        self._done = False
        self._start_time = time()

    def update(self):
        if not self._done:
            if time() - self._start_time >= 1:
                self._done = True
                return py_trees.common.Status.SUCCESS
            else:
                return py_trees.common.Status.RUNNING
        return py_trees.common.Status.SUCCESS


class Move(py_trees.behaviour.Behaviour):
    def __init__(self, node : MinecraftOverrideConfig, twist : Twist):
        super().__init__("Move")
        self.node = node
        self.twist = twist

    def initialise(self):
        self.node.send_twist_message(self.twist)

    def update(self):
        return py_trees.common.Status.SUCCESS



class MoveForward(py_trees.behaviour.Behaviour):
    def __init__(self, node : MinecraftOverrideConfig, duration):
        super().__init__("MoveForward")
        self.node = node
        self.duration = duration

    def initialise(self):
        twist = Twist()
        twist.linear.x = 1.0
        self._move = Move(self.node, twist)
        self._move.initialise()
        self._start_time = time()

    def update(self):
        elapsed = time() - self._start_time
        remaining = max(0, self.duration - elapsed)
        self.feedback_message = f"Moving forward... Time left: {remaining:.1f}s"
        return py_trees.common.Status.SUCCESS if elapsed >= self.duration else py_trees.common.Status.RUNNING

    def terminate(self, new_status):
        twist = Twist()
        stop = Move(self.node, twist)
        stop.initialise()


class MoveBackward(py_trees.behaviour.Behaviour):
    def __init__(self, node : MinecraftOverrideConfig, duration):
        super().__init__("MoveBackward")
        self.node = node
        self.duration = duration

    def initialise(self):
        twist = Twist()
        twist.linear.x = -1.0
        self._move = Move(self.node, twist)
        self._move.initialise()
        self._start_time = time()

    def update(self):
        elapsed = time() - self._start_time
        remaining = max(0, self.duration - elapsed)
        self.feedback_message = f"Moving backward... Time left: {remaining:.1f}s"
        return py_trees.common.Status.SUCCESS if elapsed >= self.duration else py_trees.common.Status.RUNNING

    def terminate(self, new_status):
        twist = Twist()
        stop = Move(self.node, twist)
        stop.initialise()


class MoveLeft(py_trees.behaviour.Behaviour):
    def __init__(self, node : MinecraftOverrideConfig, duration):
        super().__init__("MoveLeft")
        self.node = node
        self.duration = duration

    def initialise(self):
        twist = Twist()
        twist.linear.y = 1.0
        self._move = Move(self.node, twist)
        self._move.initialise()
        self._start_time = time()

    def update(self):
        elapsed = time() - self._start_time
        remaining = max(0, self.duration - elapsed)
        self.feedback_message = f"Moving left... Time left: {remaining:.1f}s"
        return py_trees.common.Status.SUCCESS if elapsed >= self.duration else py_trees.common.Status.RUNNING

    def terminate(self, new_status):
        twist = Twist()
        stop = Move(self.node, twist)
        stop.initialise()


class MoveRight(py_trees.behaviour.Behaviour):
    def __init__(self, node : MinecraftOverrideConfig, duration):
        super().__init__("MoveRight")
        self.node = node
        self.duration = duration

    def initialise(self):
        twist = Twist()
        twist.linear.y = -1.0
        self._move = Move(self.node, twist)
        self._move.initialise()
        self._start_time = time()

    def update(self):
        elapsed = time() - self._start_time
        remaining = max(0, self.duration - elapsed)
        self.feedback_message = f"Moving right... Time left: {remaining:.1f}s"
        return py_trees.common.Status.SUCCESS if elapsed >= self.duration else py_trees.common.Status.RUNNING

    def terminate(self, new_status):
        twist = Twist()
        stop = Move(self.node, twist)
        stop.initialise()


class Stop(py_trees.behaviour.Behaviour):
    def __init__(self, node : MinecraftOverrideConfig, duration):
        super().__init__("Stop")
        self.node = node
        self.duration = duration

    def initialise(self):
        twist = Twist()
        self._move = Move(self.node, twist)
        self._move.initialise()
        self._start_time = time()

    def update(self):
        elapsed = time() - self._start_time
        remaining = max(0, self.duration - elapsed)
        self.feedback_message = f"Stopping... Time left: {remaining:.1f}s"
        return py_trees.common.Status.SUCCESS if elapsed >= self.duration else py_trees.common.Status.RUNNING


class Yaw(py_trees.behaviour.Behaviour):
    def __init__(self, node : MinecraftOverrideConfig, yaw : float):
        super().__init__("Yaw")
        self.node = node
        self.yaw = yaw
        self._done = False

    def initialise(self):
        self.node.setpoint(self.yaw)
        self._done = False
        self._start_time = time()

    def update(self):
        if not self._done:
            if time() - self._start_time >= 1:
                self._done = True
                return py_trees.common.Status.SUCCESS
            else:
                return py_trees.common.Status.RUNNING
        return py_trees.common.Status.SUCCESS