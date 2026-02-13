from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# API ключи (защищены на backend)
JINA_API_KEY = "jina_4a1bffaf16024782bb72fea51a9262e3J_HbkOkeBEFTSyeNSCX7vxCj05o_"
POLZA_API_KEY = "pza_jMBRqPvRDKvN9kOdlNNJy39AP3eaAn7x"

@app.route('/')
def index():
    """Отдаём HTML"""
    return app.send_static_file('index.html')

@app.route('/api/parse', methods=['POST'])
def parse_page():
    """Парсинг через Jina AI"""
    data = request.json
    url = data.get('url')

    if not url:
        return jsonify({'error': 'URL не указан'}), 400

    try:
        response = requests.get(
            f"https://r.jina.ai/{url}",
            headers={
                'Authorization': f'Bearer {JINA_API_KEY}',
                'X-Return-Format': 'text'
            },
            timeout=30
        )
        response.raise_for_status()
        return jsonify({'content': response.text})
    except requests.exceptions.Timeout:
        return jsonify({'error': 'Timeout: сайт слишком долго отвечает'}), 504
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Ошибка парсинга: {str(e)}'}), 500

@app.route('/api/analyze', methods=['POST'])
def analyze_page():
    """Анализ через Polza AI"""
    data = request.json

    if not data.get('payload'):
        return jsonify({'error': 'Payload не указан'}), 400

    try:
        response = requests.post(
            'https://api.polza.ai/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {POLZA_API_KEY}',
                'Content-Type': 'application/json'
            },
            json=data['payload'],
            timeout=120  # 2 минуты на анализ
        )
        response.raise_for_status()
        return jsonify(response.json())
    except requests.exceptions.Timeout:
        return jsonify({'error': 'Timeout: LLM анализ слишком долгий'}), 504
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Ошибка LLM: {str(e)}'}), 500

if __name__ == '__main__':
    print('🚀 Сервер запущен: http://localhost:5000')
    print('📊 Открой браузер и перейди по ссылке выше')
    app.run(debug=True, port=5000, host='0.0.0.0')
