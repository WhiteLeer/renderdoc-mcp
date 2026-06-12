@echo off
setlocal
cd /d C:\Users\wepie\Desktop\RenderDoc-mcp
set PYTHONPATH=C:\Users\wepie\Desktop\RenderDoc-mcp\src;%PYTHONPATH%
"C:\Users\wepie\Desktop\RenderDoc-mcp\_ext_renderdoc_trial\python313\python.exe" "C:\Users\wepie\Desktop\RenderDoc-mcp\run_renderdoc_workbench.py" --legacy-mcp-server
