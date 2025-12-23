from flask import Flask, request, jsonify
from pynput.keyboard import Key, Controller
from time import sleep
import threading


app = Flask(__name__)

keyboard = Controller()


@app.route('/front_camera')
def front_camera():
    keyboard.type('t')
    sleep(0.1)
    keyboard.type("/apc pitch 0")
    keyboard.press(Key.enter)
    keyboard.release(Key.enter)
    return jsonify(success=True)


@app.route('/bottom_camera')
def bottom_camera():
    keyboard.type('t')
    sleep(0.1)
    keyboard.type("/apc pitch 90")
    keyboard.press(Key.enter)
    keyboard.release(Key.enter)
    return jsonify(success=True)


@app.route('/init_world')
def init_world():
    """Reset the Minecraft world by setting yaw, pitch and teleporting the player."""
    
    # Randomize Red & Blue Bucket
    keyboard.type("t")
    sleep(0.1)
    keyboard.type("/setblock 1444 57 -3353 minecraft:redstone_block")
    keyboard.press(Key.enter)
    keyboard.release(Key.enter)
    
    sleep(0.1)

    # Randomize Red & Blue & Yellow Flare
    keyboard.type("t")
    sleep(0.1)
    keyboard.type("/setblock 1448 57 -3314 minecraft:redstone_block")
    keyboard.press(Key.enter)
    keyboard.release(Key.enter)
    
    # Reset Angle
    # keyboard type "/apc yaw 90" and press enter
    sleep(0.1)
    keyboard.type("t")
    sleep(0.1)
    keyboard.type("/apc yaw -90")
    keyboard.press(Key.enter)
    keyboard.release(Key.enter)
    sleep(0.1)
    front_camera()
    
    # Teleport Player
    sleep(0.1)
    keyboard.type("t")
    sleep(0.1)
    keyboard.type("/tp @s 1377.68 57 -3334.5")
    keyboard.press(Key.enter)
    keyboard.release(Key.enter)
    return jsonify(success=True)


@app.route('/setpoint', methods=['POST'])
def set_yaw():
    """
    Set the player's yaw in Minecraft.
    Example JSON request body: {"setpoint": 90}
    90 means turn left 90 degrees
    -90 means turn right 90 degrees
    0 means no turn
    """
    yaw = request.json.get('setpoint', 0)
    if yaw > 0:
        for _i in range(0, int(yaw), 1):
            keyboard.press(Key.left)
            keyboard.release(Key.left)
            sleep(1 / (90 * 2))
        
    elif yaw < 0:
        for _i in range(0, abs(int(yaw)), 1):
            keyboard.press(Key.right)
            keyboard.release(Key.right)
            sleep(1 / (90 * 2))
    return jsonify(success=True)


@app.route('/twist', methods=['POST'])
def receive_twist():
    """
    if linear.x > 0 key down w key
    if linear.x < 0 key down s key
    if linear.x == 0 key up w s keys

    if linear.y > 0 key down a key
    if linear.y < 0 key down d key
    if linear.y == 0 key up a d keys
    
    Note : Angular should be input from 0 - 360 degrees through /setpoint endpoint

    """
    # print the received twist message
    value = request.json
    # print(f"Received twist message: {value}")
    linear = value.get("linear", {})
    # angular = value.get("angular", {})
    # print(f"Linear: {linear}, Angular: {angular}")
    # handle linear x
    linear_x = linear.get("x", 0)
    linear_y = linear.get("y", 0)
    print(f"Linear X: {linear_x}, Linear Y: {linear_y}")
    if linear_x != 0:
        keyboard.release('w')
        keyboard.release('s')
        if linear_x > 0:
            keyboard.press('w')
        elif linear_x < 0:
            keyboard.press('s')
    elif linear_y == 0:
        keyboard.release('w')
        keyboard.release('s')

    # handle linear y
    if linear_y != 0:
        keyboard.release('a')
        keyboard.release('d')
        if linear.get("y", 0) > 0:
            keyboard.press('a')
        elif linear.get("y", 0) < 0:
            keyboard.press('d')
    elif linear_x == 0:
        keyboard.release('a')
        keyboard.release('d')

    return jsonify(success=True)


if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=25565)