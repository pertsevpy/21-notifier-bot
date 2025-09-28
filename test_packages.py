def check_system():
    """Проверка наличия необходимых компонентов"""
    import subprocess
    import shutil

    print("🔍 Проверка системы...")

    # Проверка браузеров
    browsers = {
        "Google Chrome": ["google-chrome", "google-chrome-stable"],
        "Chromium": ["chromium", "chromium-browser"]
    }

    for browser_name, commands in browsers.items():
        for cmd in commands:
            path = shutil.which(cmd)
            if path:
                print(f"✅ {browser_name} найден: {path}")
                break
        else:
            print(f"❌ {browser_name} не найден")

    # Проверка Python пакетов
    try:
        from selenium import webdriver
        print("✅ Selenium установлен")
    except ImportError:
        print("❌ Selenium не установлен")

    try:
        from webdriver_manager.chrome import ChromeDriverManager
        print("✅ webdriver-manager установлен")
    except ImportError:
        print("❌ webdriver-manager не установлен")


if __name__ == "__main__":
    check_system()