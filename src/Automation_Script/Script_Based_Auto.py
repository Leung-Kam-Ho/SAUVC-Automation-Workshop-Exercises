from Minecraft_Tool.minecraft_override_config import MinecraftOverrideConfig
from time import sleep
from geometry_msgs.msg import Twist  # if you are not using ROS, you can comment this line out and create a mock Twist class

# Workshop part 1 - Script-based automation
if __name__ == "__main__":
    for i in range(5, 0, -1):
        print(f"Starting in {i} seconds...")
        sleep(1)
    moc = MinecraftOverrideConfig(override=True)
    
    def send_command(linear_x=0.0, linear_y=0.0, angular_z=0.0, name=""):
        test_twist = Twist()
        test_twist.linear.x = linear_x
        test_twist.linear.y = linear_y
        test_twist.angular.z = angular_z
        if name:
            print(f"Sending {name} command")
        moc.send_twist_message(test_twist)
    
    def execute_sequence(actions):
        for action, duration in actions:
            action()
            sleep(duration)

    # Demo Sequence
    execute_sequence([
        (moc.init_world, 2),
        (lambda: moc.switch_camera(front=False), 1),
        (lambda: moc.switch_camera(front=True), 1),
        (lambda: send_command(linear_x=1.0, name="forward"), 10),
        (lambda: send_command(name="stop"), 1),
    ])
