from flask import Flask, request, redirect, jsonify
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
                <b><a href="{escape(short)}" target="_blank">{escape(short)}</a></b><br>
                → {escape(long_url)}<br>
                <span class="visits-counter" data-code="{escape(code)}" data-max="{max_visits}">Переходы: {escape(visits_html)}</span><br>
                <span class="countdown" data-expire-ts="{expire_ts}" style="font-weight:bold;">
                    Загрузка...
                </span><br><br>

                <form method="post" action="/delete/{escape(code)}" style="display:inline;">
                    <button type="submit" onclick="return confirm('Удалить ссылку?')">Удалить</button>
                </form>
                <button onclick="navigator.clipboard.writeText('{escape(short)}'); alert('Скопировано!')">Копировать</button>
            </li><br>
        """

    error_html = ""
    if error_message:
        error_html = f'<p style="color: red; font-weight: bold; padding: 10px; background: #ffe6e6; border-radius: 6px;">{escape(error_message)}</p>'

    return f"""
            <!DOCTYPE html>
            <html lang="ru">
            <head>
                <meta charset="UTF-8">
                <title>Сокращатель ссылок</title>
                <style>
                    /* Глобальные настройки шрифта и фона */
                    body {{
                        font-family: Arial, sans-serif;
                        background-color: #f4f7f6; /* Светло-серый фон сайта */
                        color: #333333;           /* Темный цвет текста */
                        margin: 0 auto;
                        max-width: 900px;         /* Ширина контента */
                        padding: 30px 20px;
                    }}

                    /* Заголовки */
                    h1, h2 {{
                        color: #2c3e50;           /* Темно-синий цвет заголовков */
                    }}

                    /* Карточки со ссылками */
                    ol#links-list {{
                        padding-left: 0;
                    }}
                    li {{
                        background: #ffffff;      /* Белый фон для каждой ссылки */
                        padding: 20px;
                        margin-bottom: 15px;
                        border-radius: 10px;      /* Скругленные углы */
                        box-shadow: 0 4px 6px rgba(0,0,0,0.05); /* Легкая тень */
                        list-style-type: none;    /* Убираем стандартные цифры списка */
                        border-left: 5px solid #0066ff; /* Синяя полоска слева */
                    }}

                    /* Ссылки */
                    a {{
                        color: #0066ff;
                        text-decoration: none;
                        font-size: 18px;
                    }}
                    a:hover {{
                        text-decoration: underline;
                    }}

                    /* Общие стили для маленьких кнопок в списке */
                    li button {{
                        padding: 8px 16px;
                        border: none;
                        border-radius: 6px;
                        cursor: pointer;
                        font-family: Arial, sans-serif;
                        font-weight: bold;
                        margin-top: 10px;
                        margin-right: 10px;
                        transition: 0.2s;
                    }}

                    /* Кнопка "Удалить" */
                    li form button {{
                        background-color: #ffe0e0;
                        color: #d32f2f;
                    }}
                    li form button:hover {{
                        background-color: #ffcccc;
                    }}

                    /* Кнопка "Копировать" */
                    li > button {{
                        background-color: #e0f2f1;
                        color: #00796b;
                    }}
                    li > button:hover {{
                        background-color: #b2dfdb;
                    }}

                    /* Стили для формы создания ссылки */
                    input, select, .main-btn {{
                        font-family: Arial, sans-serif;
                    }}

                    /* Красное заблокированное поле ввода */
                    input[name="long_url"].locked {{
                        border-color: #ff4d4d !important;
                        background-color: #fff2f2 !important;
                        color: #d32f2f;
                        cursor: not-allowed;
                    }}
                </style>
            </head>
            <body>
                <h1>Механизм сокращения ссылок</h1>

                {error_html}

                <form method="post" style="max-width: 800px; background: white; padding: 25px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 30px;">
                    <input type="text" name="long_url" id="long_url" placeholder="Вставь длинную ссылку" 
                        style="width:100%; box-sizing:border-box; padding:14px; font-size:17px; margin-bottom:15px; border: 2px solid #e0e0e0; border-radius: 6px; outline: none;" required>

                    <div style="display: flex; gap: 20px; margin-bottom: 20px;">
                        <div style="flex: 1;">
                            <label style="display:block; margin-bottom:6px; font-weight: bold; color: #555;">Время жизни:</label>
                            <select name="ttl" style="padding:12px; font-size:16px; width:100%; border: 2px solid #e0e0e0; border-radius: 6px; outline: none;">
                                <option value="60">1 минута</option>
                                <option value="3600">1 час</option>
                                <option value="86400" selected>1 день</option>
                                <option value="604800">7 дней</option>
                                <option value="2592000">30 дней</option>
                            </select>
                        </div>

                        <div style="flex: 1;">
                            <label style="display:block; margin-bottom:6px; font-weight: bold; color: #555;">Удалить после переходов:</label>
                            <select name="max_visits" style="padding:12px; font-size:16px; width:100%; border: 2px solid #e0e0e0; border-radius: 6px; outline: none;">
                                <option value="0" selected>Без ограничения</option>
                                <option value="10">10 переходов</option>
                                <option value="50">50 переходов</option>
                                <option value="100">100 переходов</option>
                                <option value="500">500 переходов</option>
                            </select>
                        </div>
                    </div>

                    <button type="submit" id="submit-btn" class="main-btn"
                            style="padding:14px 32px; font-size:18px; font-weight:bold; background:#0066ff; color:white; border:none; border-radius:6px; cursor:pointer; width:100%; transition: background 0.2s;">
                        Сократить ссылку
                    </button>
                </form>

                <hr style="border: none; border-top: 1px solid #ddd; margin: 30px 0;">
                <h2>Все короткие ссылки (<span id="count">{len(all_links)}</span> шт.)</h2>
                <ol id="links-list">{links_html}</ol>

                <script>
                    const escapeHtml = (unsafe) => {{
                        return unsafe
                            .replace(/&/g, "&amp;")
                            .replace(/</g, "&lt;")
                            .replace(/>/g, "&gt;")
                            .replace(/"/g, "&quot;")
                            .replace(/'/g, "&#039;");
                    }};

                    function refreshList() {{
                        fetch('/api/links')
                            .then(r => r.json())
                            .then(links => {{
                                let html = '';
                                links.forEach(link => {{
                                    const visits_html = link.max_visits > 0 
                                        ? `${{link.visits}} / ${{link.max_visits}}` 
                                        : `${{link.visits}} / ∞`;
                                    
                                    html += `
                                        <li>
                                            <b><a href="${{link.short}}" target="_blank">${{link.short}}</a></b><br><br>
                                            <span style="color: #666;">→ ${{escapeHtml(link.long_url)}}</span><br><br>
                                            <span class="visits-counter" data-code="${{link.code}}" data-max="${{link.max_visits}}" style="display:inline-block; margin-right: 15px; color: #555;">Переходы: <b>${{visits_html}}</b></span>
                                            <span class="countdown" data-expire-ts="${{link.expire_ts}}" style="font-weight:bold; color: #d32f2f;">
                                                ⏳ Загрузка...
                                            </span><br>

                                            <form method="post" action="/delete/${{link.code}}" style="display:inline;">
                                                <button type="submit" onclick="return confirm('Удалить ссылку?')">Удалить</button>
                                            </form>
                                            <button onclick="navigator.clipboard.writeText('${{link.short}}'); alert('Скопировано!')">Копировать</button>
                                        </li>
                                    `;
                                }});
                                document.getElementById('links-list').innerHTML = html;
                                document.getElementById('count').textContent = links.length;
                                updateCountdowns();
                            }})
                            .catch(() => {{}});
                    }}

                    function updateCountdowns() {{
                        document.querySelectorAll('.countdown').forEach(el => {{
                            const expireTs = parseInt(el.dataset.expireTs || 0);
                            if (!expireTs) return;

                            let rem = expireTs - Math.floor(Date.now() / 1000);
                            if (rem <= 0) {{
                                const li = el.closest('li');
                                if (li) li.remove();
                                return;
                            }}

                            const d = Math.floor(rem / 86400); rem %= 86400;
                            const h = Math.floor(rem / 3600); rem %= 3600;
                            const m = Math.floor(rem / 60); const s = rem % 60;

                            let txt = "⏳ Осталось: ";
                            if (d) txt += d + "д ";
                            if (h) txt += h + "ч ";
                            if (m) txt += m + "м ";
                            txt += s + "с";
                            el.innerHTML = txt;
                        }});
                    }}

                    // === МЕХАНИКА БЛОКИРОВКИ ПОЛЯ ===
                    const inputField = document.getElementById('long_url');
                    const submitBtn = document.getElementById('submit-btn');
                    let retryAfter = {retry_after}; // Передается из Flask

                    function activateLockdown(seconds) {{
                        if (seconds <= 0) return;

                        inputField.disabled = true;
                        submitBtn.disabled = true;
                        submitBtn.style.background = "#ccc";
                        inputField.classList.add('locked');

                        let remaining = seconds;
                        const originalPlaceholder = inputField.placeholder;

                        const interval = setInterval(() => {{
                            if (remaining <= 0) {{
                                clearInterval(interval);
                                inputField.disabled = false;
                                submitBtn.disabled = false;
                                submitBtn.style.background = "#0066ff";
                                inputField.classList.remove('locked');
                                inputField.placeholder = originalPlaceholder;
                                inputField.value = "";
                            }} else {{
                                inputField.value = ""; // Очищаем поле от текста
                                inputField.placeholder = "Слишком много запросов! Подождите " + remaining + " сек.";
                                remaining--;
                            }}
                        }}, 1000);
                    }}

                    if (retryAfter > 0) {{
                        activateLockdown(retryAfter);
                    }}

                    setInterval(refreshList, 3000);
                    setInterval(updateCountdowns, 1000);

                    refreshList();
                    updateCountdowns();
                </script>
            </body>
            </html>
        """

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