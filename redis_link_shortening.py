from flask import Flask, request, redirect, jsonify, render_template
from urllib.parse import urlparse
from urllib.request import urlopen
from urllib.error import URLError
import redis
import random
import string
import time
from html import escape
import os
import subprocess
import json
import sys

app = Flask(__name__)

r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

NGROK_DOMAIN = "robert-unpenetrated-kittenishly.ngrok-free.dev"   # ←←← ПОМЕНЯЙ НА СВОЙ
NGROK_EXECUTABLE = r".\ngrok.exe"
LOCAL_BASE_URL = "http://127.0.0.1:5000"
NGROK_BASE_URL = f"https://{NGROK_DOMAIN}"

# По умолчанию используем локальный адрес, чтобы импорт файла не блокировался.
BASE_URL = LOCAL_BASE_URL
_NGROK_PROCESS = None


def _get_ngrok_public_url(timeout_sec: int = 20) -> str | None:
    """Пытается прочитать публичный URL из локального API ngrok."""
    deadline = time.time() + timeout_sec
    api_url = "http://127.0.0.1:4040/api/tunnels"

    while time.time() < deadline:
        try:
            with urlopen(api_url, timeout=2) as response:
                data = json.loads(response.read().decode("utf-8"))

            for tunnel in data.get("tunnels", []):
                public_url = tunnel.get("public_url", "")
                if public_url.startswith("https://"):
                    return public_url.rstrip("/")
        except Exception:
            pass

        time.sleep(1)

    return None


def start_ngrok_tunnel(port: int = 5000) -> str:
    """Запускает ngrok.exe http <port> и возвращает публичный URL."""
    global _NGROK_PROCESS

    if _NGROK_PROCESS and _NGROK_PROCESS.poll() is None:
        public_url = _get_ngrok_public_url(timeout_sec=5)
        return public_url or NGROK_BASE_URL

    if not os.path.exists(NGROK_EXECUTABLE):
        print(f"[WARN] Не найден {NGROK_EXECUTABLE}. Использую запасной URL: {NGROK_BASE_URL}")
        return NGROK_BASE_URL

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)

    try:
        _NGROK_PROCESS = subprocess.Popen(
            [NGROK_EXECUTABLE, "http", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except Exception as exc:
        print(f"[WARN] Не удалось запустить ngrok: {exc}")
        return NGROK_BASE_URL

    public_url = _get_ngrok_public_url(timeout_sec=20)
    if public_url:
        return public_url

    print(f"[WARN] ngrok запущен, но публичный URL не найден. Использую запасной: {NGROK_BASE_URL}")
    return NGROK_BASE_URL


def choose_base_url() -> str:
    print("Механизм сокращения ссылок — Day 1 Redis (TTL + Max Visits)")
    print("Выберите режим запуска:")
    print("  1 — локально (http://127.0.0.1:5000)")
    print("  2 — через ngrok (автозапуск .\\ngrok.exe http 5000)")

    while True:
        try:
            choice = input("Ваш выбор [1/2]: ").strip().lower()
        except EOFError:
            choice = "1"

        if choice in ("", "1", "local", "локально"):
            return LOCAL_BASE_URL
        if choice in ("2", "ngrok"):
            return start_ngrok_tunnel(5000)

        print("Введите 1 для локального запуска или 2 для ngrok.")

# ====================== RATE LIMITING ======================
def check_rate_limit(ip: str):
    key = f"rate:{ip}"
    current = r.get(key)
    
    if current and int(current) >= 3:
        # Лимит превышен. Возвращаем False и оставшийся TTL ключа в секундах
        return False, r.ttl(key)
        
    current = r.incr(key)
    if current == 1:
        r.expire(key, 60)
    return True, 0

# ====================== ПРОВЕРКА КОРРЕКТНОСТИ URL ======================
def is_valid_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return bool(parsed.scheme) and bool(parsed.netloc)
    except:
        return False

# ====================== АВТООЧИСТКА МЁРТВЫХ ССЫЛОК ======================
def cleanup_dead_links():
    """Удаляет из zset и полностью чистит всё, что уже истекло по TTL или по max_visits"""
    for code in list(r.zrange('visits', 0, -1)):
        if not r.exists(code):
            r.zrem('visits', code)
            continue

        meta = r.hgetall(f"link:{code}")
        if not meta:
            r.zrem('visits', code)
            continue

        max_v = int(meta.get("max_visits", 0))
        current_v = int(r.zscore('visits', code) or 0)

        if max_v > 0 and current_v >= max_v:
            with r.pipeline() as pipe:
                pipe.zrem('visits', code)
                pipe.delete(code, f"link:{code}", f"tags:{code}")
                pipe.execute()

# ====================== ГЛАВНАЯ СТРАНИЦА ======================
@app.route('/', methods=['GET', 'POST'])
def home():
    error_message = ""
    retry_after = 0  # Время блокировки для передачи в JS

    if request.method == 'POST':
        is_allowed, time_left = check_rate_limit(request.remote_addr)
        
        if not is_allowed:
            retry_after = time_left
        else:
            long_url_input = request.form['long_url'].strip()

            if not long_url_input:
                error_message = "Введите ссылку"
            else:
                if not long_url_input.startswith(('http://', 'https://')):
                    long_url = 'https://' + long_url_input
                else:
                    long_url = long_url_input

                if not is_valid_url(long_url):
                    error_message = "Некорректная ссылка. Введите правильный URL (например, example.com или https://example.com)"
                else:
                    ttl_sec = int(request.form.get('ttl', 86400))
                    if ttl_sec <= 0:
                        error_message = "Бессрочные ссылки по времени запрещены. Выберите время жизни."
                    else:
                        max_visits = int(request.form.get('max_visits', 0))

                        code = ''.join(random.choices(string.ascii_letters + string.digits, k=6))

                        with r.pipeline() as pipe:
                            pipe.set(code, long_url)
                            pipe.hset(f"link:{code}", mapping={
                                "url": long_url,
                                "created_at": str(int(time.time())),
                                "created_by": "anonymous",
                                "expire_ts": str(int(time.time()) + ttl_sec),
                                "max_visits": str(max_visits)
                            })
                            pipe.zadd('visits', {code: 0})
                            pipe.sadd(f"tags:{code}", "uncategorized")

                            pipe.expire(code, ttl_sec)
                            pipe.expire(f"link:{code}", ttl_sec)
                            pipe.expire(f"tags:{code}", ttl_sec)
                            pipe.execute()

                        return redirect('/')

    # Автоочистка перед показом списка
    cleanup_dead_links()

    all_links = r.zrevrange('visits', 0, -1, withscores=True)

    links_html = ""
    for code, visits in all_links:
        meta = r.hgetall(f"link:{code}")
        if not meta:
            continue
        long_url = meta.get("url", "—")
        max_visits = int(meta.get("max_visits", 0))
        expire_ts = int(meta.get("expire_ts", 0))

        short = f"{BASE_URL}/s/{code}"

        visits_html = f"{int(visits)} / {max_visits}" if max_visits > 0 else f"{int(visits)} / ∞"

        links_html += f"""
            <li>
                <b><a href=\"{escape(short)}\" target=\"_blank\">{escape(short)}</a></b><br>
                → {escape(long_url)}<br>
                <span class=\"visits-counter\" data-code=\"{escape(code)}\" data-max=\"{max_visits}\">Переходы: {escape(visits_html)}</span><br>
                <span class=\"countdown\" data-expire-ts=\"{expire_ts}\" style=\"font-weight:bold;\">
                    Загрузка...
                </span><br><br>

                <form method=\"post\" action=\"/delete/{escape(code)}\" style=\"display:inline;\">
                    <button type=\"submit\" onclick=\"return confirm('Удалить ссылку?')\">Удалить</button>
                </form>
                <button onclick=\"navigator.clipboard.writeText('{escape(short)}'); alert('Скопировано!')\">Копировать</button>
            </li><br>
        """

    error_html = ""
    if error_message:
        error_html = f'<p style="color: red; font-weight: bold; padding: 10px; background: #ffe6e6; border-radius: 6px;">{escape(error_message)}</p>'

    return render_template(
        'index.html',
        error_html=error_html,
        retry_after=retry_after,
        links_html=links_html,
        links_count=len(all_links),
    )

# ====================== API — полный актуальный список ======================
@app.route('/api/links')
def api_links():
    cleanup_dead_links()
    all_links = r.zrevrange('visits', 0, -1, withscores=True)
    result = []
    for code, visits in all_links:
        meta = r.hgetall(f"link:{code}")
        if not meta:
            continue
        result.append({
            "code": code,
            "short": f"{BASE_URL}/s/{code}",
            "long_url": meta.get("url", "—"),
            "visits": int(visits),
            "max_visits": int(meta.get("max_visits", 0)),
            "expire_ts": int(meta.get("expire_ts", 0))
        })
    return jsonify(result)

# ====================== РЕДИРЕКТ ======================
@app.route('/s/<code>')
def redirect_to_url(code):
    long_url = r.get(code)
    if not long_url:
        r.zrem('visits', code)
        return "Ссылка не найдена", 404

    r.zincrby('visits', 1, code)
    current = int(r.zscore('visits', code) or 0)
    max_v = int(r.hget(f"link:{code}", "max_visits") or 0)

    if max_v > 0 and current >= max_v:
        with r.pipeline() as pipe:
            pipe.zrem('visits', code)
            pipe.delete(code, f"link:{code}", f"tags:{code}")
            pipe.execute()

    return redirect(long_url)

# ====================== УДАЛЕНИЕ ======================
@app.route('/delete/<code>', methods=['POST'])
def delete_link(code):
    with r.pipeline() as pipe:
        pipe.delete(code, f"link:{code}", f"tags:{code}")
        pipe.zrem('visits', code)
        pipe.execute()
    return redirect('/')

if __name__ == '__main__':
    BASE_URL = choose_base_url()
    print(f"Открывай: {BASE_URL}")
    # use_reloader=False, чтобы Flask debug не запускал скрипт второй раз и не ломал ввод.
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
