while True:
    try:
        print("CHECK:", datetime.now())

        # 👇 ВРЕМЕННО ТЕСТ
        print("SENDING TEST REPORT...")
        send_monthly_reports()

        time.sleep(300)  # 5 минут пауза

    except Exception as e:
        print("SCHEDULER ERROR:", e)

    time.sleep(60)
