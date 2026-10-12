@echo off
REM Double-click to expose the backend (port 3001) at your static ngrok domain.
REM Keep this window AND start-server.bat open while testing WhatsApp / Telegram / Gmail.
echo.
echo  Public URL: https://driveway-class-pureness.ngrok-free.dev
echo  WhatsApp webhook: https://driveway-class-pureness.ngrok-free.dev/webhooks/whatsapp
echo.
ngrok http --url=driveway-class-pureness.ngrok-free.dev 3001
pause
