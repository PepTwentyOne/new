# parttime_bot.py
# Рабочий Telegram-бот для автоматизации PartTime API (регистрация, логин, загрузка скринов, выполнение заданий).
# Установка:
# 1) pip install pytelegrambotapi requests
# 2) Создать папку проекта и положить сюда этот файл.
# 3) Создать (или оставить пустыми) файлы: accounts.json, settings.json, proxies.txt
# 4) Создать папку screenshots/ и положить туда тестовые изображения.
# 5) Экспортировать токен: export BOT_TOKEN="ваш_токен" (или вставить токен в переменную BOT_TOKEN ниже).
# 6) Запустить: python parttime_bot.py
#
# Примечание: сетевые операции (API) реально выполняются по URL из скриптов-референсов.
# Код основан на ваших исходниках (1.py, telegram_bot.py). См. ссылки/цитаты в ответе.

import os
import time
import json
import io
import random
import threading
import hashlib
import traceback  # Добавляем для детальных логов ошибок
from datetime import datetime
import requests
import telebot
from telebot import types

# ========== Настройки (замените/установите через env при желании) ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7650316952:AAEsSnf9t-DoDoZuYtyz1EQuNUIvJdUNaAc")  # либо вставьте токен прямо сюда (не рекомендуется)
if not BOT_TOKEN:
    BOT_TOKEN = "REPLACE_WITH_YOUR_BOT_TOKEN"
ADMIN_ID = int(os.environ.get("ADMIN_ID",  "806360930"))  # по умолчанию ваш ID из присланных скриптов

# Файлы и каталоги
ACCOUNTS_FILE = "accounts.json"
SETTINGS_FILE = "settings.json"
PROXIES_FILE = "proxies.txt"
SCREENSHOTS_DIR = "screenshots"
LOGS_DIR = "logs"

# Инициализация TeleBot
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

# ========== Utility ==========

def log_message(level: str, message: str, category: str = "general"):
    """
    Логирование сообщений в файлы по категориям
    category: general, tasks, api, errors, registration
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {message}"
    
    # Вывод в консоль
    print(line)
    
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        
        # Основной лог (все сообщения)
        main_log = os.path.join(LOGS_DIR, f"bot_{datetime.now().strftime('%Y-%m-%d')}.txt")
        with open(main_log, "a", encoding="utf-8") as f:
            f.write(f"[{category}] {line}\n")
        
        # Лог по категориям
        category_log = os.path.join(LOGS_DIR, f"{category}_{datetime.now().strftime('%Y-%m-%d')}.txt")
        with open(category_log, "a", encoding="utf-8") as f:
            f.write(f"{line}\n")
        
        # Лог ошибок (отдельный файл для быстрого поиска)
        if level in ["ERROR", "FATAL"]:
            error_log = os.path.join(LOGS_DIR, f"errors_{datetime.now().strftime('%Y-%m-%d')}.txt")
            with open(error_log, "a", encoding="utf-8") as f:
                f.write(f"[{category}] {line}\n")
                
    except Exception as e:
        print(f"Ошибка записи лога: {e}")

def hash_md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()

def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log_message("ERROR", f"save_json {path}: {e}")
        return False

def init_files():
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    if not os.path.exists(ACCOUNTS_FILE):
        save_json(ACCOUNTS_FILE, {})
    if not os.path.exists(SETTINGS_FILE):
        default = {
            "wallet": "",
            "invite_code": "E68E70F40",
            "max_accounts": 50,
            "delay_between_tasks": 5,
            "use_proxies": True
        }
        save_json(SETTINGS_FILE, default)
    if not os.path.exists(PROXIES_FILE):
        with open(PROXIES_FILE, "w", encoding="utf-8") as f:
            f.write("# proxy per line, e.g. http://user:pass@ip:port\n")

init_files()

# ========== Accounts / Proxies helpers ==========

def get_accounts():
    return load_json(ACCOUNTS_FILE)

def save_account(phone, password, proxy="", wallet=""):
    accounts = get_accounts()
    if phone in accounts:
        return False
    accounts[phone] = {
        "phone": phone,
        "password": password,
        "hashed_password": hash_md5(password),
        "proxy": proxy,
        "token": "",
        "balance": 0.0,
        "status": "active",
        "tasks_completed": 0,
        "failed_logins": 0,
        "created": datetime.now().isoformat()
    }
    save_json(ACCOUNTS_FILE, accounts)
    return True

def update_account_token(phone, token):
    accounts = get_accounts()
    if phone in accounts:
        accounts[phone]["token"] = token
        accounts[phone]["last_login"] = datetime.now().isoformat()
        accounts[phone]["failed_logins"] = 0
        save_json(ACCOUNTS_FILE, accounts)
        return True
    return False

def increment_failed_logins(phone):
    accounts = get_accounts()
    if phone in accounts:
        accounts[phone]["failed_logins"] = accounts[phone].get("failed_logins", 0) + 1
        save_json(ACCOUNTS_FILE, accounts)
        return True
    return False

def increment_tasks_completed(phone):
    accounts = get_accounts()
    if phone in accounts:
        accounts[phone]["tasks_completed"] = accounts[phone].get("tasks_completed", 0) + 1
        save_json(ACCOUNTS_FILE, accounts)
        return True
    return False

def get_proxies():
    if not os.path.exists(PROXIES_FILE):
        return []
    with open(PROXIES_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]

def add_proxy_line(proxy):
    with open(PROXIES_FILE, "a", encoding="utf-8") as f:
        f.write(proxy + "\n")
    return True

def clear_proxies():
    with open(PROXIES_FILE, "w", encoding="utf-8") as f:
        f.write("# cleared\n")
    return True

def get_active_accounts():
    return [a for a in get_accounts().values() if a.get("status") == "active"]

# ========== PartTime API wrapper (адаптирован из ваших скриптов) ==========
# ========== PartTime API wrapper (исправленный по перехваченным запросам) ==========
# ========== PartTime API wrapper (расширенный для всех заданий) ==========
# ========== PartTime API wrapper (исправленный по перехваченным запросам) ==========
# ========== PartTime API wrapper (исправленный) ==========
class PartTimeAPI:
    def __init__(self, proxy=None):
        self.base = "https://partimetest.51c1e.live"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": self.base,
            "Referer": f"{self.base}/",
            "Language": "ru"
        })
        self.token = None
        self.log_prefix = ""  # Для идентификации в логах
        if proxy:
            self.set_proxy(proxy)

    def set_proxy(self, proxy_url):
        """Установить прокси для сессии"""
        if not proxy_url:
            return
        if not proxy_url.startswith(("http://", "https://", "socks5://")):
            proxy_url = "http://" + proxy_url
        self.session.proxies.update({"http": proxy_url, "https": proxy_url})
        log_message("INFO", f"{self.log_prefix}Установлен прокси: {proxy_url}", "api")

    def set_log_prefix(self, prefix):
        """Установить префикс для логов (например, номер телефона)"""
        self.log_prefix = f"[{prefix}] " if prefix else ""

    def login(self, phone, password=None, timeout=30):
        log_message("INFO", f"{self.log_prefix}Попытка входа для {phone}", "api")
        if password is None:
            password = phone
        data = {"areaCode": "+1", "phone": phone, "password": hash_md5(password),
                "deviceType": "pc", "deviceId": str(random.randint(10**18, 10**19-1)),
                "email": "", "xieyi": [0]}
        try:
            r = self.session.post(f"{self.base}/apiAnt/userLogin?lang=ru", json=data, timeout=timeout)
            r.raise_for_status()
            j = r.json()
            
            log_message("DEBUG", f"{self.log_prefix}Ответ логина: {j}", "api")
            
            if j.get("code") == 200:
                self.token = j["data"].get("token")
                if self.token:
                    self.session.headers.update({"Authorization": self.token})
                log_message("INFO", f"{self.log_prefix}Вход успешен", "api")
                return True, j
            else:
                error_msg = j.get("message", str(j))
                log_message("ERROR", f"{self.log_prefix}Ошибка входа: {error_msg}", "api")
                return False, error_msg
        except requests.exceptions.RequestException as e:
            log_message("ERROR", f"{self.log_prefix}Сетевая ошибка при входе: {e}", "api")
            return False, str(e)
        except Exception as e:
            log_message("ERROR", f"{self.log_prefix}Неизвестная ошибка при входе: {e}", "api")
            return False, str(e)

    def get_all_tasks(self):
        """Получить все доступные задания"""
        log_message("INFO", f"{self.log_prefix}Запрос доступных заданий", "api")
        try:
            data = {"pageNumber": 1, "pageSize": 50}
            r = self.session.post(f"{self.base}/apiAnt/taskIssue?lang=ru", json=data, timeout=30)
            r.raise_for_status()
            j = r.json()
            
            log_message("DEBUG", f"{self.log_prefix}Ответ taskIssue: код {j.get('code')}", "api")
            
            if j.get("code") == 200:
                tasks = j.get("data", {}).get("rows", [])
                log_message("INFO", f"{self.log_prefix}Найдено {len(tasks)} заданий", "api")
                return tasks, None
            else:
                error_msg = j.get("message", "no-tasks")
                log_message("WARNING", f"{self.log_prefix}Ошибка получения заданий: {error_msg}", "api")
                return [], error_msg
        except requests.exceptions.RequestException as e:
            log_message("ERROR", f"{self.log_prefix}Сетевая ошибка при получении заданий: {e}", "api")
            return [], str(e)
        except Exception as e:
            log_message("ERROR", f"{self.log_prefix}Неизвестная ошибка при получении заданий: {e}", "api")
            return [], str(e)

    def apply_task(self, batch_id):
        """Применить задание (взять его)"""
        log_message("INFO", f"{self.log_prefix}Попытка взять задание batchId={batch_id}", "api")
        try:
            data = {"batchId": batch_id}
            r = self.session.post(f"{self.base}/apiAnt/applyTask?lang=ru", json=data, timeout=30)
            r.raise_for_status()
            result = r.json()
            
            log_message("DEBUG", f"{self.log_prefix}Ответ applyTask: {result}", "api")
            
            if result.get("code") == 200:
                log_message("INFO", f"{self.log_prefix}Задание batchId={batch_id} успешно взято", "api")
            else:
                log_message("WARNING", f"{self.log_prefix}Ошибка взятия задания batchId={batch_id}: {result.get('message')}", "api")
            
            return result
        except requests.exceptions.RequestException as e:
            log_message("ERROR", f"{self.log_prefix}Сетевая ошибка при взятии задания: {e}", "api")
            return {"code": 500, "message": str(e)}
        except Exception as e:
            log_message("ERROR", f"{self.log_prefix}Неизвестная ошибка при взятии задания: {e}", "api")
            return {"code": 500, "message": str(e)}

    def get_applied_tasks(self):
        """Получить список уже взятых заданий"""
        log_message("INFO", f"{self.log_prefix}Запрос взятых заданий", "api")
        try:
            data = {"pageNumber": 1, "pageSize": 50, "taskStatus": 1}
            r = self.session.post(f"{self.base}/apiAnt/taskList?lang=ru", json=data, timeout=30)
            r.raise_for_status()
            j = r.json()
            
            if j.get("code") == 200:
                tasks = j.get("data", {}).get("rows", [])
                log_message("INFO", f"{self.log_prefix}Найдено {len(tasks)} взятых заданий", "api")
                return tasks, None
            else:
                error_msg = j.get("message", "no-tasks")
                log_message("WARNING", f"{self.log_prefix}Ошибка получения взятых заданий: {error_msg}", "api")
                return [], error_msg
        except requests.exceptions.RequestException as e:
            log_message("ERROR", f"{self.log_prefix}Сетевая ошибка при получении взятых заданий: {e}", "api")
            return [], str(e)
        except Exception as e:
            log_message("ERROR", f"{self.log_prefix}Неизвестная ошибка при получении взятых заданий: {e}", "api")
            return [], str(e)

    def get_completed_tasks(self):
        """Получить список выполненных заданий"""
        try:
            data = {"pageNumber": 1, "pageSize": 50, "taskStatus": 3}  # taskStatus=3 - выполненные
            r = self.session.post(f"{self.base}/apiAnt/taskList?lang=ru", json=data, timeout=30)
            r.raise_for_status()
            j = r.json()
            if j.get("code") == 200:
                tasks = j.get("data", {}).get("rows", [])
                return tasks, None
            return [], j.get("message", "no-tasks")
        except Exception as e:
            return [], str(e)

    def get_task_detail(self, task_id):
        """Получить детали задания"""
        try:
            data = {"taskId": str(task_id)}
            r = self.session.post(f"{self.base}/apiAnt/taskDetail?lang=ru", json=data, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            return {"code": 500, "message": str(e)}

    def get_captcha(self):
        """Получить капчу для регистрации"""
        log_message("INFO", f"{self.log_prefix}Запрос капчи для регистрации", "api")
        try:
            url = f"{self.base}/apiAnt/validateCode?lang=ru&_={int(time.time()*1000)}"
            r = self.session.get(url, timeout=20)
            if r.status_code == 200:
                log_message("INFO", f"{self.log_prefix}Капча успешно получена ({len(r.content)} bytes)", "api")
                return r.content
            log_message("ERROR", f"{self.log_prefix}Ошибка получения капчи: статус {r.status_code}", "api")
            return None
        except Exception as e:
            log_message("ERROR", f"{self.log_prefix}Ошибка получения капчи: {e}", "api")
            return None

    def register_account(self, phone, captcha_code):
        """Регистрация аккаунта"""
        log_message("INFO", f"{self.log_prefix}Регистрация аккаунта {phone}", "api")
        # упрощённый flow: sendSms -> checkSms -> register
        try:
            r1 = self.session.post(f"{self.base}/apiAnt/sendSms?lang=ru", 
                                  json={"areaCode":"+1","phone":phone,"verCode":captcha_code,"smsType":"REGISTER"}, 
                                  timeout=30)
            r1.raise_for_status()
            j1 = r1.json()
            log_message("DEBUG", f"{self.log_prefix}Ответ sendSms: {j1}", "api")
            
            if j1.get("code") != 200:
                error_msg = j1.get("message", str(j1))
                log_message("ERROR", f"{self.log_prefix}Ошибка sendSms: {error_msg}", "api")
                return False, error_msg
            
            sms_code = j1.get("data", {}).get("smsCode", "")
            r2 = self.session.post(f"{self.base}/apiAnt/checkSms?lang=ru", 
                                  json={"areaCode":"+1","phone":phone,"smsCode":sms_code}, 
                                  timeout=30)
            r2.raise_for_status()
            j2 = r2.json()
            log_message("DEBUG", f"{self.log_prefix}Ответ checkSms: {j2}", "api")
            
            if j2.get("code") != 200:
                error_msg = j2.get("message", str(j2))
                log_message("ERROR", f"{self.log_prefix}Ошибка checkSms: {error_msg}", "api")
                return False, error_msg
            
            sms_token = j2.get("data", {}).get("smsToken")
            if not sms_token:
                log_message("ERROR", f"{self.log_prefix}Нет sms_token в ответе", "api")
                return False, "no_sms_token"
            
            settings = load_json(SETTINGS_FILE)
            hashed_pwd = hash_md5(phone)
            r3 = self.session.post(f"{self.base}/apiAnt/register?lang=ru", json={
                "password": hashed_pwd,
                "areaCode": "+1",
                "phone": phone,
                "smsToken": sms_token,
                "deviceId": str(random.randint(10**18, 10**19-1)),
                "deviceType": "phone",
                "inviteCode": settings.get("invite_code",""),
                "channelCode": "", "refCode": ""
            }, timeout=30)
            r3.raise_for_status()
            j3 = r3.json()
            log_message("DEBUG", f"{self.log_prefix}Ответ register: {j3}", "api")
            
            if j3.get("code") == 200:
                token = j3.get("data", {}).get("token")
                if token:
                    self.session.headers.update({"Authorization": token})
                log_message("INFO", f"{self.log_prefix}Регистрация успешна для {phone}", "api")
                return True, j3
            error_msg = j3.get("message", str(j3))
            log_message("ERROR", f"{self.log_prefix}Ошибка регистрации: {error_msg}", "api")
            return False, error_msg
        except requests.exceptions.RequestException as e:
            log_message("ERROR", f"{self.log_prefix}Сетевая ошибка при регистрации: {e}", "api")
            return False, str(e)
        except Exception as e:
            log_message("ERROR", f"{self.log_prefix}Неизвестная ошибка при регистрации: {e}", "api")
            return False, str(e)

    def upload_screenshot(self, image_bytes, filename="screen.png"):
        """Загрузка скриншота"""
        log_message("INFO", f"{self.log_prefix}Загрузка скриншота {filename} ({len(image_bytes)} bytes)", "api")
        try:
            url = f"{self.base}/apiAnt/upImage"
            headers = {k:v for k,v in self.session.headers.items() if k.lower() != "content-type"}
            files = {'file': (filename, io.BytesIO(image_bytes), 'image/jpeg')}
            r = self.session.post(url, files=files, headers=headers, timeout=40)
            r.raise_for_status()
            j = r.json()
            
            log_message("DEBUG", f"{self.log_prefix}Ответ upImage: {j}", "api")
            
            if j.get("code") == 200:
                image_paths = j.get("data", [])
                if image_paths:
                    log_message("INFO", f"{self.log_prefix}Скриншот загружен: {image_paths[0]}", "api")
                    return image_paths[0], None
                else:
                    log_message("ERROR", f"{self.log_prefix}Пустой ответ при загрузке скриншота", "api")
                    return None, "no image path returned"
            else:
                error_msg = j.get("message", "upload_failed")
                log_message("ERROR", f"{self.log_prefix}Ошибка загрузки скриншота: {error_msg}", "api")
                return None, error_msg
        except requests.exceptions.RequestException as e:
            log_message("ERROR", f"{self.log_prefix}Сетевая ошибка при загрузке скриншота: {e}", "api")
            return None, str(e)
        except Exception as e:
            log_message("ERROR", f"{self.log_prefix}Неизвестная ошибка при загрузке скриншота: {e}", "api")
            return None, str(e)

    def complete_task(self, task_id, image_url, social_url="https://discord.gg/test"):
        """Отправить выполненное задание"""
        log_message("INFO", f"{self.log_prefix}Отправка задания taskId={task_id}", "api")
        try:
            url = f"{self.base}/apiAnt/submitTask?lang=ru"
            data = {
                "taskId": task_id,
                "submitMsg": {
                    "urlList": [social_url],
                    "imgList": [image_url],
                    "videoUrlList": []
                }
            }
            log_message("DEBUG", f"{self.log_prefix}Данные для submitTask: {data}", "api")
            
            r = self.session.post(url, json=data, timeout=30)
            r.raise_for_status()
            result = r.json()
            
            log_message("DEBUG", f"{self.log_prefix}Ответ submitTask: {result}", "api")
            
            if result.get("code") == 200:
                log_message("INFO", f"{self.log_prefix}Задание taskId={task_id} успешно отправлено", "api")
            else:
                log_message("ERROR", f"{self.log_prefix}Ошибка отправки задания taskId={task_id}: {result.get('message')}", "api")
            
            return result
        except requests.exceptions.RequestException as e:
            log_message("ERROR", f"{self.log_prefix}Сетевая ошибка при отправке задания: {e}", "api")
            return {"code": 500, "message": str(e)}
        except Exception as e:
            log_message("ERROR", f"{self.log_prefix}Неизвестная ошибка при отправке задания: {e}", "api")
            return {"code": 500, "message": str(e)}

# ========== Bot State for registration flows ==========
registration_states = {}  # user_id -> {phone, api_obj, proxy, attempts}

# ========== Bot Handlers ==========

def admin_only(func):
    def wrapper(message, *args, **kwargs):
        if message.from_user.id != ADMIN_ID:
            return
        return func(message, *args, **kwargs)
    return wrapper


@bot.message_handler(commands=["start", "menu"])
@admin_only
def cmd_start(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    markup.add("➕ Регистрация", "📋 Аккаунты", "🔐 Проверить вход")
    markup.add("🔄 Задания", "🔍 Проверить задания", "🧪 Тест загрузки")
    markup.add("📊 Статистика", "🌐 Прокси", "📁 Экспорт")
    markup.add("📜 Логи", "🔄 Последние ошибки", "🧹 Очистка")
    markup.add("📝 Обновить меню")
    bot.send_message(ADMIN_ID, "PartTime Manager — меню:", reply_markup=markup)


@bot.message_handler(func=lambda m: m.text == "📋 Аккаунты")
@admin_only
def show_accounts(message):
    accounts = get_accounts()
    if not accounts:
        bot.send_message(ADMIN_ID, "📭 Нет аккаунтов")
        return
    out = f"📋 Аккаунты ({len(accounts)}):\n\n"
    for phone, acc in list(accounts.items())[:50]:
        status = "✅" if acc.get("status")=="active" else "⛔"
        out += f"{status} {phone}  | tasks:{acc.get('tasks_completed',0)} failed_login:{acc.get('failed_logins',0)}\n"
    bot.send_message(ADMIN_ID, out)

@bot.message_handler(func=lambda m: m.text == "➕ Регистрация")
@admin_only
def start_registration(message):
    # берем случайный рабочий прокси (если есть)
    proxies = get_proxies()
    proxy = random.choice(proxies) if proxies else None
    api = PartTimeAPI(proxy=proxy)
    phone = str(random.randint(1000000000, 1999999999))
    captcha = api.get_captcha()
    if not captcha:
        bot.send_message(ADMIN_ID, "❌ Не удалось получить капчу. Попробуйте позже.")
        return
    registration_states[message.from_user.id] = {"phone": phone, "api": api, "proxy": proxy, "attempts":0}
    bot.send_photo(ADMIN_ID, captcha, caption=f"Капча для номера +1{phone}\nВведите 4 цифры капчи:")

@bot.message_handler(func=lambda m: m.from_user.id in registration_states)
@admin_only
def handle_captcha_reply(message):
    st = registration_states.get(message.from_user.id)
    if not st:
        return
    code = message.text.strip()
    if not code.isdigit() or len(code) != 4:
        bot.send_message(ADMIN_ID, "Капча — 4 цифры. Попробуйте снова.")
        return
    phone = st["phone"]
    api = st["api"]
    st["attempts"] += 1
    bot.send_message(ADMIN_ID, f"Регистрация: пытаюсь зарегистрировать +1{phone} (попытка {st['attempts']})...")
    ok, resp = api.register_account(phone, code)
    if ok:
        # Сохраняем аккаунт
        settings = load_json(SETTINGS_FILE)
        saved = save_account(phone, phone, proxy=st.get("proxy",""), wallet=settings.get("wallet",""))
        if saved:
            token = resp.get("data", {}).get("token", "")
            if token:
                update_account_token(phone, token)
            bot.send_message(ADMIN_ID, f"✅ Зарегистрирован: +1{phone}  (пароль = номер).")
        else:
            bot.send_message(ADMIN_ID, "❌ Аккаунт уже существует.")
        registration_states.pop(message.from_user.id, None)
        return
    else:
        err = str(resp)
        if st["attempts"] < 3:
            # пробуем заново: новая капча
            new_proxy = None
            proxies = get_proxies()
            if proxies:
                new_proxy = random.choice(proxies)
                api.set_proxy(new_proxy)
                st["proxy"] = new_proxy
            new_captcha = api.get_captcha()
            if new_captcha:
                bot.send_photo(ADMIN_ID, new_captcha, caption=f"Ошибка регистрации: {err}\nНовая капча (прокси {new_proxy}): Введите 4 цифры")
                return
        bot.send_message(ADMIN_ID, f"❌ Регистрация не удалась: {err}")
        registration_states.pop(message.from_user.id, None)

@bot.message_handler(func=lambda m: m.text == "🔐 Проверить вход")
@admin_only
def check_login_all(message):
    accounts = get_accounts()
    if not accounts:
        bot.send_message(ADMIN_ID, "📭 Нет аккаунтов")
        return
    msg = bot.send_message(ADMIN_ID, f"Проверка входов для {len(accounts)} аккаунтов...")
    def worker():
        ok_count = 0
        fail_count = 0
        lines = []
        for phone, acc in accounts.items():
            try:
                api = PartTimeAPI(proxy=acc.get("proxy")) if acc.get("proxy") else PartTimeAPI()
                ok, resp = api.login(phone, acc.get("password", phone))
                if ok:
                    ok_count += 1
                    new_token = resp.get("data", {}).get("token","")
                    if new_token:
                        update_account_token(phone, new_token)
                    lines.append(f"✅ {phone}")
                else:
                    fail_count += 1
                    increment_failed_logins(phone)
                    lines.append(f"❌ {phone}: {resp}")
            except Exception as e:
                fail_count += 1
                increment_failed_logins(phone)
                lines.append(f"🔥 {phone}: {str(e)[:80]}")
            time.sleep(1)
        bot.edit_message_text(f"Результат: ✅{ok_count} ❌{fail_count}\n\n" + "\n".join(lines[:30]), ADMIN_ID, msg.message_id)
    threading.Thread(target=worker, daemon=True).start()

@bot.message_handler(func=lambda m: m.text == "🧪 Тест загрузки")
@admin_only
def test_upload_handler(message):
    accounts = get_active_accounts()
    if not accounts:
        bot.send_message(ADMIN_ID, "Нет активных аккаунтов")
        return
    images = [f for f in os.listdir(SCREENSHOTS_DIR) if f.lower().endswith((".png",".jpg",".jpeg"))]
    if not images:
        bot.send_message(ADMIN_ID, "Нет изображений в screenshots/")
        return
    acc = accounts[0]
    api = PartTimeAPI(proxy=acc.get("proxy"))
    ok, resp = api.login(acc["phone"], acc.get("password", acc["phone"]))
    if not ok:
        bot.send_message(ADMIN_ID, f"Ошибка логина {acc['phone']}: {resp}")
        return
    path = os.path.join(SCREENSHOTS_DIR, random.choice(images))
    with open(path, "rb") as f:
        b = f.read()
    bot.send_message(ADMIN_ID, f"Загружаю {os.path.basename(path)}...")
    url, err = api.upload_screenshot(b, os.path.basename(path))
    if url:
        bot.send_message(ADMIN_ID, f"Успешно: {url}")
    else:
        bot.send_message(ADMIN_ID, f"Ошибка загрузки: {err}")

@bot.message_handler(func=lambda m: m.text == "🔄 Задания")
@admin_only
def run_tasks(message):
    log_message("INFO", f"Запуск выполнения заданий по команде от пользователя {message.from_user.id}", "tasks")
    
    accounts = get_active_accounts()
    images = [f for f in os.listdir(SCREENSHOTS_DIR) if f.lower().endswith((".png",".jpg",".jpeg"))]
    
    if not accounts:
        bot.send_message(ADMIN_ID, "Нет активных аккаунтов")
        log_message("WARNING", "Нет активных аккаунтов для выполнения заданий", "tasks")
        return
    if not images:
        bot.send_message(ADMIN_ID, "Нет картинок в screenshots/")
        log_message("WARNING", "Нет изображений в директории screenshots", "tasks")
        return
    
    log_message("INFO", f"Найдено {len(accounts)} аккаунтов и {len(images)} изображений", "tasks")
    bot.send_message(ADMIN_ID, f"Запуск заданий для {len(accounts)} аккаунтов...")
    
    def worker():
        results = []
        settings = load_json(SETTINGS_FILE)
        delay = settings.get("delay_between_tasks", 5)
        
        for acc in accounts:
            account_log_prefix = f"Аккаунт {acc['phone']}"
            log_message("INFO", f"Обработка {account_log_prefix}", "tasks")
            
            account_result = {
                "phone": acc["phone"],
                "available_tasks": 0,
                "applied_tasks": 0,
                "completed_tasks": 0,
                "failed": 0,
                "details": []
            }
            
            try:
                # Инициализация API
                api = PartTimeAPI(proxy=acc.get("proxy")) if acc.get("proxy") else PartTimeAPI()
                api.set_log_prefix(acc["phone"])
                
                # 1. Логин
                log_message("INFO", f"{account_log_prefix}: Попытка входа", "tasks")
                ok, res = api.login(acc["phone"], acc.get("password", acc["phone"]))
                if not ok:
                    account_result["failed"] += 1
                    error_msg = f"Логин не удался: {res}"
                    account_result["details"].append(f"❌ {error_msg}")
                    log_message("ERROR", f"{account_log_prefix}: {error_msg}", "tasks")
                    results.append(account_result)
                    continue
                
                log_message("INFO", f"{account_log_prefix}: Вход успешен", "tasks")
                
                # 2. Получить уже взятые задания
                log_message("INFO", f"{account_log_prefix}: Получение взятых заданий", "tasks")
                applied_tasks, _ = api.get_applied_tasks()
                
                # Преобразуем в множества для быстрого поиска
                applied_batch_ids = {task.get("batchId") for task in applied_tasks}
                account_result["applied_tasks"] = len(applied_tasks)
                
                log_message("INFO", f"{account_log_prefix}: Уже взято {len(applied_tasks)} заданий", "tasks")
                
                # 3. Получить все доступные задания
                log_message("INFO", f"{account_log_prefix}: Получение доступных заданий", "tasks")
                available_tasks, terr = api.get_all_tasks()
                if not available_tasks:
                    account_result["details"].append(f"📭 Нет доступных заданий")
                    log_message("INFO", f"{account_log_prefix}: Нет доступных заданий", "tasks")
                    results.append(account_result)
                    continue
                
                account_result["available_tasks"] = len(available_tasks)
                log_message("INFO", f"{account_log_prefix}: Найдено {len(available_tasks)} доступных заданий", "tasks")
                
                # 4. Фильтруем задания: исключаем уже взятые
                tasks_to_do = []
                for task in available_tasks:
                    batch_id = task.get("batchId")
                    if batch_id not in applied_batch_ids:
                        tasks_to_do.append(task)
                
                if not tasks_to_do:
                    account_result["details"].append(f"✓ Все доступные задания уже взяты")
                    log_message("INFO", f"{account_log_prefix}: Все доступные задания уже взяты", "tasks")
                    results.append(account_result)
                    continue
                
                log_message("INFO", f"{account_log_prefix}: {len(tasks_to_do)} новых заданий для выполнения", "tasks")
                account_result["details"].append(f"📋 Найдено {len(tasks_to_do)} новых заданий")
                
                # 5. Ограничиваем количество заданий для выполнения за раз
                max_tasks_per_account = 5
                tasks_to_do = tasks_to_do[:max_tasks_per_account]
                
                # 6. Выполняем задания
                for task in tasks_to_do:
                    try:
                        batch_id = task.get("batchId")
                        task_title = task.get("taskTitle", "Без названия")
                        reward = task.get("taskReward", 0)
                        
                        log_message("INFO", f"{account_log_prefix}: Обработка задания '{task_title}' (batchId: {batch_id})", "tasks")
                        account_result["details"].append(f"├── Задание: {task_title} (${reward})")
                        
                        # Применяем задание
                        log_message("INFO", f"{account_log_prefix}: Применение задания batchId={batch_id}", "tasks")
                        apply_result = api.apply_task(batch_id)
                        
                        if apply_result.get("code") != 200:
                            error_msg = f"Не удалось взять задание: {apply_result.get('message')}"
                            account_result["failed"] += 1
                            account_result["details"].append(f"│   ❌ {error_msg}")
                            log_message("ERROR", f"{account_log_prefix}: {error_msg}", "tasks")
                            time.sleep(2)
                            continue
                        
                        account_result["details"].append(f"│   ✓ Задание взято")
                        log_message("INFO", f"{account_log_prefix}: Задание batchId={batch_id} успешно взято", "tasks")
                        
                        # Ждем, пока задание появится в списке взятых
                        log_message("INFO", f"{account_log_prefix}: Поиск взятого задания в списке", "tasks")
                        max_attempts = 10
                        found_task = None
                        
                        for attempt in range(max_attempts):
                            time.sleep(3)
                            applied_tasks, _ = api.get_applied_tasks()
                            for applied_task in applied_tasks:
                                if applied_task.get("batchId") == batch_id:
                                    found_task = applied_task
                                    break
                            if found_task:
                                log_message("INFO", f"{account_log_prefix}: Задание найдено в списке взятых (попытка {attempt+1})", "tasks")
                                break
                            log_message("DEBUG", f"{account_log_prefix}: Поиск задания... (попытка {attempt+1}/{max_attempts})", "tasks")
                        
                        if not found_task:
                            error_msg = "Задание не найдено после взятия"
                            account_result["failed"] += 1
                            account_result["details"].append(f"│   ❌ {error_msg}")
                            log_message("ERROR", f"{account_log_prefix}: {error_msg}", "tasks")
                            continue
                        
                        task_id = found_task.get("taskId")
                        if not task_id:
                            error_msg = "Нет taskId в найденном задании"
                            account_result["failed"] += 1
                            account_result["details"].append(f"│   ❌ {error_msg}")
                            log_message("ERROR", f"{account_log_prefix}: {error_msg}", "tasks")
                            continue
                        
                        log_message("INFO", f"{account_log_prefix}: Найден taskId={task_id} для batchId={batch_id}", "tasks")
                        
                        # Загружаем несколько изображений
                        log_message("INFO", f"{account_log_prefix}: Загрузка изображений для задания", "tasks")
                        image_urls = []
                        images_to_upload = min(3, len(images))
                        
                        for i in range(images_to_upload):
                            img_file = random.choice(images)
                            img_path = os.path.join(SCREENSHOTS_DIR, img_file)
                            
                            with open(img_path, "rb") as f:
                                img_data = f.read()
                            
                            log_message("INFO", f"{account_log_prefix}: Загрузка изображения {i+1}/{images_to_upload}: {img_file}", "tasks")
                            img_url, upload_error = api.upload_screenshot(img_data, f"image_{i}.jpg")
                            
                            if img_url:
                                image_urls.append(img_url)
                                account_result["details"].append(f"│   ✓ Изображение {i+1} загружено")
                                log_message("INFO", f"{account_log_prefix}: Изображение {i+1} успешно загружено", "tasks")
                            else:
                                account_result["details"].append(f"│   ❌ Ошибка загрузки изображения {i+1}: {upload_error}")
                                log_message("ERROR", f"{account_log_prefix}: Ошибка загрузки изображения {i+1}: {upload_error}", "tasks")
                        
                        if not image_urls:
                            error_msg = "Не удалось загрузить ни одного изображения"
                            account_result["failed"] += 1
                            account_result["details"].append(f"│   ❌ {error_msg}")
                            log_message("ERROR", f"{account_log_prefix}: {error_msg}", "tasks")
                            continue
                        
                        log_message("INFO", f"{account_log_prefix}: Загружено {len(image_urls)} изображений", "tasks")
                        
                        # Отправляем выполнение задания
                        discord_link = f"https://discord.gg/{''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=10))}"
                        log_message("INFO", f"{account_log_prefix}: Отправка выполнения задания taskId={task_id}", "tasks")
                        
                        submit_result = api.complete_task(task_id, image_urls, discord_link)
                        
                        if submit_result.get("code") == 200:
                            account_result["completed_tasks"] += 1
                            increment_tasks_completed(acc["phone"])
                            account_result["details"].append(f"│   ✅ Задание отправлено на проверку")
                            log_message("INFO", f"{account_log_prefix}: Задание taskId={task_id} успешно отправлено", "tasks")
                        else:
                            error_msg = f"Ошибка отправки: {submit_result.get('message')}"
                            account_result["failed"] += 1
                            account_result["details"].append(f"│   ❌ {error_msg}")
                            log_message("ERROR", f"{account_log_prefix}: {error_msg}", "tasks")
                        
                        time.sleep(delay)
                        
                    except Exception as e:
                        error_msg = f"Исключение при обработке задания: {str(e)}"
                        account_result["failed"] += 1
                        account_result["details"].append(f"│   🔥 {error_msg[:50]}")
                        log_message("ERROR", f"{account_log_prefix}: {error_msg}", "tasks")
                        log_message("DEBUG", f"{account_log_prefix}: Traceback: {traceback.format_exc()}", "tasks")
                        time.sleep(2)
                
            except Exception as e:
                error_msg = f"Общая ошибка при обработке аккаунта: {str(e)}"
                account_result["failed"] += 1
                account_result["details"].append(f"🔥 {error_msg[:80]}")
                log_message("ERROR", f"{account_log_prefix}: {error_msg}", "tasks")
                log_message("DEBUG", f"{account_log_prefix}: Traceback: {traceback.format_exc()}", "tasks")
            
            results.append(account_result)
        
        # Формируем отчет
        total_completed = sum(r["completed_tasks"] for r in results)
        total_failed = sum(r["failed"] for r in results)
        total_available = sum(r["available_tasks"] for r in results)
        
        report = f"📊 ОТЧЕТ ПО ВЫПОЛНЕНИЮ ЗАДАНИЙ\n\n"
        report += f"Аккаунтов обработано: {len(results)}\n"
        report += f"Всего доступных заданий: {total_available}\n"
        report += f"Успешно выполнено: {total_completed}\n"
        report += f"Ошибок: {total_failed}\n\n"
        
        # Детали по каждому аккаунту
        for i, res in enumerate(results[:10]):
            report += f"📱 {res['phone']}:\n"
            report += f"   Доступно: {res['available_tasks']}, Выполнено: {res['completed_tasks']}, Ошибок: {res['failed']}\n"
            for detail in res['details'][-3:]:
                report += f"   {detail}\n"
            report += "\n"
        
        if len(results) > 10:
            report += f"... и еще {len(results) - 10} аккаунтов\n"
        
        # Сохраняем отчет в лог
        log_message("INFO", f"Итоговый отчет: {total_completed} выполнено, {total_failed} ошибок", "tasks")
        
        bot.send_message(ADMIN_ID, report[:4000])
    
    threading.Thread(target=worker, daemon=True).start()

@bot.message_handler(func=lambda m: m.text == "🌐 Прокси")
@admin_only
def proxies_menu(message):
    proxies = get_proxies()
    text = f"Прокси ({len(proxies)}):\n" + ("\n".join(proxies[:50]) if proxies else "Нет")
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("➕ Добавить прокси", "🧹 Очистить прокси")
    bot.send_message(ADMIN_ID, text, reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "➕ Добавить прокси")
@admin_only
def add_proxy_step(message):
    msg = bot.send_message(ADMIN_ID, "Отправьте строку прокси (пример: http://user:pass@ip:port):")
    bot.register_next_step_handler(msg, add_proxy_handler)

def add_proxy_handler(message):
    proxy = message.text.strip()
    if proxy:
        add_proxy_line(proxy)
        bot.send_message(ADMIN_ID, f"Добавлено: {proxy}")
    else:
        bot.send_message(ADMIN_ID, "Пустой ввод")

@bot.message_handler(func=lambda m: m.text == "🧹 Очистить прокси")
@admin_only
def clear_proxies_handler(message):
    clear_proxies()
    bot.send_message(ADMIN_ID, "Прокси очищены")

@bot.message_handler(func=lambda m: m.text == "📁 Экспорт")
@admin_only
def export_accounts_handler(message):
    accounts = get_accounts()
    if not accounts:
        bot.send_message(ADMIN_ID, "Нет аккаунтов")
        return
    path = "accounts_export.txt"
    with open(path, "w", encoding="utf-8") as f:
        for phone, acc in accounts.items():
            f.write(f"{phone}:{acc.get('password')}:{acc.get('balance',0)}\n")
    with open(path, "rb") as f:
        bot.send_document(ADMIN_ID, f, caption="Экспорт аккаунтов")

@bot.message_handler(func=lambda m: m.text == "🧹 Очистка")
@admin_only
def clear_accounts_handler(message):
    save_json(ACCOUNTS_FILE, {})
    bot.send_message(ADMIN_ID, "Все аккаунты удалены")

@bot.message_handler(func=lambda m: m.text == "🔍 Проверить задания")
@admin_only
def check_account_tasks(message):
    """Проверить статус заданий на конкретном аккаунте"""
    msg = bot.send_message(ADMIN_ID, "Введите номер телефона аккаунта (без +1):")
    bot.register_next_step_handler(msg, process_account_check)

def process_account_check(message):
    phone = message.text.strip()
    accounts = get_accounts()
    
    if phone not in accounts:
        bot.send_message(ADMIN_ID, f"❌ Аккаунт {phone} не найден")
        return
    
    acc = accounts[phone]
    api = PartTimeAPI(proxy=acc.get("proxy")) if acc.get("proxy") else PartTimeAPI()
    
    ok, res = api.login(phone, acc.get("password", phone))
    if not ok:
        bot.send_message(ADMIN_ID, f"❌ Ошибка логина: {res}")
        return
    
    # Получаем все виды заданий
    available_tasks, _ = api.get_all_tasks()
    applied_tasks, _ = api.get_applied_tasks()
    completed_tasks, _ = api.get_completed_tasks()
    
    report = f"📊 Статус заданий для +1{phone}:\n\n"
    report += f"✅ Доступных заданий: {len(available_tasks)}\n"
    report += f"📝 Взятых заданий: {len(applied_tasks)}\n"
    report += f"🏁 Выполненных заданий: {len(completed_tasks)}\n\n"
    
    if available_tasks:
        report += "📋 Доступные задания:\n"
        for task in available_tasks[:5]:  # Показываем первые 5
            title = task.get("taskTitle", "Без названия")[:30]
            reward = task.get("taskReward", 0)
            batch_id = task.get("batchId")
            report += f"  • {title} (${reward}) [ID: {batch_id}]\n"
    
    bot.send_message(ADMIN_ID, report[:4000])

@bot.message_handler(func=lambda m: m.text == "📜 Логи")
@admin_only
def show_logs(message):
    """Показать последние логи"""
    try:
        log_files = []
        for f in os.listdir(LOGS_DIR):
            if f.endswith(".txt") and "log_" in f:
                log_files.append(f)
        
        if not log_files:
            bot.send_message(ADMIN_ID, "Логи не найдены")
            return
        
        # Сортируем по дате изменения (новые сначала)
        log_files.sort(key=lambda x: os.path.getmtime(os.path.join(LOGS_DIR, x)), reverse=True)
        
        markup = types.InlineKeyboardMarkup()
        for log_file in log_files[:5]:
            markup.add(types.InlineKeyboardButton(
                log_file, 
                callback_data=f"log_{log_file}"
            ))
        
        bot.send_message(ADMIN_ID, "Выберите лог для просмотра:", reply_markup=markup)
        
    except Exception as e:
        bot.send_message(ADMIN_ID, f"Ошибка получения логов: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("log_"))
def send_log_file(call):
    """Отправить файл лога"""
    log_file = call.data[4:]  # Убираем "log_"
    log_path = os.path.join(LOGS_DIR, log_file)
    
    try:
        if os.path.exists(log_path):
            with open(log_path, "rb") as f:
                bot.send_document(call.message.chat.id, f, caption=f"Файл лога: {log_file}")
        else:
            bot.answer_callback_query(call.id, "Файл лога не найден")
    except Exception as e:
        bot.answer_callback_query(call.id, f"Ошибка: {str(e)[:100]}")

@bot.message_handler(func=lambda m: m.text == "🔄 Последние ошибки")
@admin_only
def show_recent_errors(message):
    """Показать последние ошибки из лога"""
    try:
        error_files = []
        for f in os.listdir(LOGS_DIR):
            if f.startswith("errors_") and f.endswith(".txt"):
                error_files.append(f)
        
        if not error_files:
            bot.send_message(ADMIN_ID, "Логи ошибок не найдены")
            return
        
        # Берем последний файл ошибок
        error_files.sort(key=lambda x: os.path.getmtime(os.path.join(LOGS_DIR, x)), reverse=True)
        latest_error_file = error_files[0]
        error_path = os.path.join(LOGS_DIR, latest_error_file)
        
        with open(error_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        if not lines:
            bot.send_message(ADMIN_ID, "Лог ошибок пуст")
            return
        
        # Показываем последние 20 ошибок
        recent_errors = lines[-20:]
        error_text = f"Последние ошибки из {latest_error_file}:\n\n"
        error_text += "".join(recent_errors[-10:])  # Последние 10 строк
        
        bot.send_message(ADMIN_ID, error_text[:4000])
        
    except Exception as e:
        bot.send_message(ADMIN_ID, f"Ошибка чтения логов: {e}")

@bot.message_handler(func=lambda m: m.text == "📝 Обновить меню")
@admin_only
def update_menu(message):
    cmd_start(message)

# ========== Start polling ==========
if __name__ == "__main__":
    log_message("INFO", "Bot starting...")
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        log_message("FATAL", f"Polling error: {e}")
