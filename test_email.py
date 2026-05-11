import smtplib
import os

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USERNAME = "твой@gmail.com"
SMTP_PASSWORD = "app_password_без_пробелов"

try:
    server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
    server.starttls()
    server.login(SMTP_USERNAME, SMTP_PASSWORD)
    print("✅ EMAIL OK")
    server.quit()
except Exception as e:
    print("❌ ERROR:", e)
