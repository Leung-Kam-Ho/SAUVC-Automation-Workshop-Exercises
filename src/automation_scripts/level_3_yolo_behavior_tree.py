from time import sleep, perf_counter
import py_trees
from py_trees.decorators import Retry, SuccessIsFailure, FailureIsSuccess, Inverter, Timeout
from py_trees.composites import Selector, Sequence,Parallel
from minecraft.minecraft_override_config import MinecraftOverrideConfig
from pathlib import Path
from math import inf
from actions import (
    InitWorld,
    SwitchCameraFront,
    SwitchCameraBottom,
    MoveForward,
    MoveBackward,
    MoveLeft,
    MoveRight,
    Yaw,
    Stop,
    MoveBasedOnDetection_Sway,
    MoveBasedOnDetection_Sway_Surge,
    AvoidBasedOnDetection_Sway,
    DetecedObject
)

if __name__ == "__main__":

    search_angle = 40
    print("Starting in 2 seconds...")
    sleep(2)
    print("Starting...")
    node = MinecraftOverrideConfig(override=True)

    root = py_trees.composites.Sequence("RootSequence", memory=True)
    init_setup = Sequence("Initial Setup", memory=True, children=[
        InitWorld(node),
        SwitchCameraBottom(node),
        SwitchCameraFront(node),
    ])
    advance_forward = Sequence("Advance Forward", memory=True, children=[
        # Selector("Find Gate", memory=True, children=[
        #     Sequence("Check Gate Right", memory=True, children=[
        #         Yaw(node, -search_angle),
        #         DetecedObject("Gate", conf_threshold=0.8),
        #         Yaw(node, search_angle),
        #     ]),
        #     Sequence("Check Gate Left", memory=True, children=[
        #         Yaw(node, search_angle),
        #         DetecedObject("Gate", conf_threshold=0.8),
        #         Yaw(node, -search_angle),
        #     ]),
        #     Sequence("Probably Centered", memory=True, children=[
        #         DetecedObject("Gate", conf_threshold=0.8),
        #         MoveLeft(node, 5),
        #     ]),
        # ]),
        MoveForward(node, 10),
    ])
    
    navigate_to_gate = Sequence("Navigate through Gate", memory=True, children=[
        Selector("Find Gate", memory=True, children=[
            Sequence("Check Gate Right", memory=True, children=[
                Yaw(node, -search_angle),
                DetecedObject("Gate", conf_threshold=0.8),
                Yaw(node, search_angle),
                Parallel("Move to Gate", policy=py_trees.common.ParallelPolicy.SuccessOnOne(), children=[
                    Retry(name="Retry MoveBasedOnDetection", child=MoveBasedOnDetection_Sway(node, "Gate"), num_failures=inf),
                    MoveRight(node, inf),
                ]),
            ]),
            SuccessIsFailure("Reset to center", child=Yaw(node, search_angle)),
            Sequence("Check Gate Left", memory=True, children=[
                Yaw(node, search_angle),
                DetecedObject("Gate", conf_threshold=0.8),
                Yaw(node, -search_angle),
                Parallel("Move to Gate", policy=py_trees.common.ParallelPolicy.SuccessOnOne(), children=[
                    Retry(name="Retry MoveBasedOnDetection", child=MoveBasedOnDetection_Sway(node, "Gate"), num_failures=inf),
                    MoveLeft(node, inf),
                ]),         
            ]),
        ]),
        MoveForward(node, 10),
    ])

    navigate_to_blue_pail = Sequence("Navigate to Blue Pail", memory=True, children=[
        Selector("Find Blue Pail", memory=True, children=[
            Sequence("Check Blue Pail Right", memory=True, children=[
                Yaw(node, -search_angle),
                DetecedObject("Blue pail"),
                Yaw(node, search_angle),
                Parallel("Center to Blue Pail", policy=py_trees.common.ParallelPolicy.SuccessOnOne(), children=[
                    Retry(name="Retry MoveBasedOnDetection", child=MoveBasedOnDetection_Sway(node, "Blue pail", center_hold_time=2), num_failures=inf),
                    # MoveForward(node, 10),
                    MoveRight(node, 20), 
                ]),
            ]),
            SuccessIsFailure("Reset to center", child=Yaw(node, search_angle)),
            Sequence("Check Blue Pail Left", memory=True, children=[
                Yaw(node, search_angle),
                DetecedObject("Blue pail"),
                Yaw(node, -search_angle),
                Parallel("Center to Blue Pail", policy=py_trees.common.ParallelPolicy.SuccessOnOne(), children=[
                    Retry(name="Retry MoveBasedOnDetection", child=MoveBasedOnDetection_Sway(node, "Blue pail", center_hold_time=2), num_failures=inf),
                    # MoveForward(node, 10),
                    MoveLeft(node, 20), 
                ]),
            ]),
        ]),

        SwitchCameraBottom(node),
        Parallel("Move to Blue Pail", policy=py_trees.common.ParallelPolicy.SuccessOnOne(), children=[
            # Retry(name="Retry DetectedObject", child=DetecedObject("Blue pail"), num_failures=10),
            MoveForward(node, inf),
            Retry(name="Retry DetectedObject", child=DetecedObject("Blue pail"), num_failures=inf)

        ]),
        FailureIsSuccess(name="MoveBasedOnDetection", child=Timeout(name="Timeout MoveBasedOnDetection", duration=20,child=Retry(name="Retry MoveBasedOnDetection", child=MoveBasedOnDetection_Sway_Surge(node, "Blue pail", center_percent=0.1, move_duration=0.35, center_hold_time=2), num_failures=inf),))
    ])

    leave_pail = Sequence("Leave Pail Area", memory=True, children=[
        SwitchCameraFront(node),
        MoveBackward(node, 5),
    ])
    navigate_to_pole = Sequence(name="Navigate to pole", memory=True, children=[
        Yaw(node, -90),
        MoveForward(node, 8),
    ])


    root.add_children([
        init_setup,
        advance_forward,
        Retry(name="Retry Navigate to Gate", child=navigate_to_gate, num_failures=inf),
        Retry(name="Retry Navigate to Blue Pail", child=navigate_to_blue_pail, num_failures=inf),
        leave_pail,
        navigate_to_pole,
    ])
    root = Timeout(name="Overall Timeout", duration=120, child=root)
    tree = py_trees.trees.BehaviourTree(root)
    tree.setup(timeout=1.0)

    # Directory to store rendered tree images
    path = Path() / "media"
    path.mkdir(parents=True, exist_ok=True)
    py_trees.display.render_dot_tree(tree.root, name="behavior_tree_based", target_directory=path)

    count = 0
    RATE_HZ = 20.0          # Display update rate
    TICK_HZ = 20.0         # Actual tick rate (simulatet/apc pitch 0 real-time updates)
    period_display = 1.0 / RATE_HZ
    period_tick = 1.0 / TICK_HZ

    last_display = perf_counter()
    next_tick = perf_counter()

    while True:
        now = perf_counter()

        # Tick the tree frequently (real-time)
        if now >= next_tick:
            tree.tick()
            next_tick += period_tick

        # Display status at a slower rate
        if now - last_display >= period_display:
            count += 1
            print(f"\n--- Tick {count} ---")
            child_states = ", ".join(f"{c.name}:{c.status.name}" for c in tree.root.children)
            print(py_trees.display.ascii_tree(tree.root, show_status=True))
            print("\n")
            last_display = now

        # Exit condition
        if tree.root.status != py_trees.common.Status.RUNNING:
            print("Tree finished execution.")
            break

        sleep(0.001)  # small sleep to avoid busy waiting    
