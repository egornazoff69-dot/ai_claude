@echo off
echo 🚀 Запускаю AI Audit Tool...

REM Проверка Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python не найден. Установи Python 3.11+
    pause
    exit /b 1
)

REM Создание виртуального окружения
if not exist "venv" (
    echo 📦 Создаю виртуальное окружение...
    python -m venv venv
)

REM Активация
call venv\Scripts\activate.bat

REM Установка зависимостей
echo 📥 Устанавливаю зависимости...
pip install -q -r requirements.txt

REM Запуск
echo ✅ Всё готово!
python server.py
