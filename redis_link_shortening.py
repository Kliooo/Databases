from flask import Flask, request, redirect, jsonify, render_template
from urllib.parse import urlparse
import redis
import random
import string
import time
from html import escape

app = Flask(__name__)

r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

NGROK_DOMAIN = "robert-unpenetrated-kittenishly.ngrok-free.dev"   # ←←← ПОМЕНЯЙ НА СВОЙ
BASE_URL = f"https://{NGROK_DOMAIN}"

print("Механизм сокращения ссылок — Day 1 Redis (TTL + Max Visits)")
print(f"Публичный адрес: {BASE_URL}\n")

# ====================== RATE LIMITING ======================
def check_rate_limit(ip: str):
    key = f"rate:{ip}"
    current = r.get(key)
    
    if current and int(current) >= 5:
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
    print(f"Открывай: {BASE_URL}")
    app.run(host='0.0.0.0', port=5000, debug=True)
