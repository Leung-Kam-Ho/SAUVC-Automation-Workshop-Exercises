from Minecraft_Tool.minecraft_override_config import MinecraftOverrideConfig


def main():
    print("Hello from automation-workshop!")
    node = MinecraftOverrideConfig(override=True)
    node.init_world()


if __name__ == "__main__":
    main()
