Option Explicit
Dim shell, command, result
Set shell = CreateObject("WScript.Shell")
command = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File ""D:\VS Code\vn-air-quality-weather-pipeline\scripts\publish_timer_tick.ps1"""
result = shell.Run(command, 0, True)
WScript.Quit result
