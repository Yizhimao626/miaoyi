@echo off
echo Adding Miaoyi firewall rules...
netsh advfirewall firewall add rule name="Miaoyi Backend TCP 8001" dir=in action=allow protocol=TCP localport=8001 profile=any
netsh advfirewall firewall add rule name="Miaoyi Vite TCP 5173" dir=in action=allow protocol=TCP localport=5173 profile=any
netsh advfirewall firewall add rule name="Miaoyi Backend TCP 8000-8100" dir=in action=allow protocol=TCP localport=8000-8100 profile=any
echo.
echo DONE. Press any key to close...
pause >nul
