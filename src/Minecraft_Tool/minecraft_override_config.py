import requests
from time import time, sleep
# from geometry_msgs.msg import Twist  # if you are not using ROS, you can comment this line out and create a mock Twist class
import configparser

class Twist:
    def __init__(self):
        self.linear = type('linear', (), {'x': 0.0, 'y': 0.0, 'z': 0.0})()
        self.angular = type('angular', (), {'x': 0.0, 'y': 0.0, 'z': 0.0})()

class MinecraftOverrideConfig:
    def __init__(self, override: bool = False):
        self.minecraft_override = override
        self.cfg_file_name = "user.cfg"
        self.user_cfg_section = "server_config"
        config = configparser.ConfigParser()
        config.read(self.cfg_file_name)
        self.minecraft_server_url = config.get(self.user_cfg_section, "minecraft_server_url")
        self.last_output = Twist()

    def init_world(self):
        # send get request to minecraft server /init_world endpoint
        try:
            response = requests.get(f"{self.minecraft_server_url}/init_world")
            if response.status_code == 200:
                print("Successfully initialized Minecraft world.")
            else:
                print(f"Failed to initialize Minecraft world. Status code: {response.status_code}")
                raise e
        except Exception as e:
            print(f"Error initializing Minecraft world: {e}")
            raise e

    def setpoint(self, angle: float):
        # send request to minecraft server /setpoint endpoint
        try:
            data = {"setpoint": angle}
            response = requests.post(f"{self.minecraft_server_url}/setpoint", json=data)
            if response.status_code == 200:
                print("Successfully sent Setpoint message to Minecraft server.")
            else:
                print(f"Failed to send Setpoint message. Status code: {response.status_code}")
                raise e
        except Exception as e:
            print(f"Error sending Setpoint message to Minecraft server: {e}")
            raise e

    def switch_camera(self, front=True):
        # send get request to minecraft server /switch_camera endpoint
        try:
            response = requests.get(f"{self.minecraft_server_url}/{"front" if front else "bottom"}_camera")
            if response.status_code == 200:
                print("Successfully switched camera on Minecraft server.")
            else:
                print(f"Failed to switch camera. Status code: {response.status_code}")
                raise e
        except Exception as e:
            print(f"Error switching camera on Minecraft server: {e}")
            raise e

    def send_twist_message(self, twist_msg : Twist):
        # send request to minecraft server /twist endpoint
        if self.last_output == twist_msg:
            return  # No change in command, skip sending
        try:
            self.last_output = twist_msg
            data = {
                "linear": {
                    "x": twist_msg.linear.x,
                    "y": twist_msg.linear.y,
                    "z": twist_msg.linear.z
                },
                "angular": {
                    "x": twist_msg.angular.x,
                    "y": twist_msg.angular.y,
                    "z": twist_msg.angular.z
                }
            }
            response = requests.post(f"{self.minecraft_server_url}/twist", json=data)
            
            if response.status_code == 200:
                print("Successfully sent Twist message to Minecraft server.")
            else:
                print(f"Failed to send Twist message. Status code: {response.status_code}")
                raise e
            # if angular z > 0, instead of sending twist, send to /setpoint endpoint with -90 or 90
            if abs(twist_msg.angular.z) > 0.1:
                angle = 90 if twist_msg.angular.z > 0 else -90
                self.setpoint(angle)
                
        except Exception as e:
            print(f"Error sending Twist message to Minecraft server: {e}")
            raise e


# Workshop part 1 - Script-based automation
if __name__ == "__main__":
    moc = MinecraftOverrideConfig(override=True)
