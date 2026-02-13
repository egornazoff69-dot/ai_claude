#!/bin/bash

echo "🚀 Запускаю AI Audit Tool..."

# Проверка установки Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python не найден. Установи Python 3.11+"
    exit 1
fi

# Создание виртуального окружения (если нет)
if [ ! -d "venv" ]; then
    echo "📦 Создаю виртуальное окружение..."
    python3 -m venv venv
fi

# Активация виртуального окружения
source venv/bin/activate

# Установка зависимостей
echo "📥 Устанавливаю зависимости..."
pip install -q -r requirements.txt

# Запуск сервера
echo "✅ Всё готово!"
python3 server.py
