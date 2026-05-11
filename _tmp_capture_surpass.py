import json
import sys
sys.path.insert(0, r"C:\Users\wepie\Desktop\RenderDoc-mcp\mcp")
import renderdoc_mcp_server as m
args = {
    "capture_mode": "launch",
    "game_path": r"C:\Program Files\NetEase\MuMu\nx_device\12.0\shell\MuMuNxDevice.exe",
    "emulator_profile": "mumu",
    "mumu_two_stage": True,
    "second_stage_delay_sec": 18,
    "auto_trigger": True,
    "trigger_backend": "targetcontrol",
    "trigger_delay_sec": 90,
    "open_in_qrenderdoc": True,
    "capture_output": r"C:\Users\wepie\Desktop\RenderDoc-mcp\captures\surpass_auto_{timestamp}.rdc",
    "hook_children": True,
    "wait_for_exit": False,
}
result = m._capture_game(args)
print(json.dumps(result, indent=2))
