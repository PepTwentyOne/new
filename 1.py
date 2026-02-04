# Тестовый комментарий
# parttime_bot_final_working.py
# Полная автоматизация взятия и выполнения заданий с исправленными ошибками

import os
import time
import json
import random
import threading
import hashlib
import traceback
import subprocess
import sys
import uuid
import string
import mimetypes
import re
from datetime import datetime
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import telebot
from telebot import types
from pathlib import Path
from threading import Semaphore
from urllib.parse import urlparse, urljoin

# ========== Проверка и установка зависимостей ==========
def install_package(package):
    try:
        __import__(package)
    except ImportError:
        print(f"Установка {package}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])

install_package('pytelegrambotapi')

# ========== Конфигурация ==========
BOT_TOKEN = "7650316952:AAEsSnf9t-DoDoZuYtyz1EQuNUIvJdUNaAc"
ADMIN_ID = 806360930

# Конфигурация поллинга и ретраев
POLL_INTERVAL = 3          # сек (между опросами taskIssue)
MAX_APPLY_RETRIES = 3      # сколько попыток applyTask перед пропуском
APPLY_RETRY_DELAY = 1.0    # сек между попытками applyTask
SESSION_TIMEOUT = 12       # сек
PROXY_TEST_TIMEOUT = 8     # сек
MAX_TASKS_PER_RUN = 20     # лимит берём за проход
LOG_DIR = "logs/raw_responses"
DEBUG_DIR = "logs/debug"

# Конфигурация загрузки файлов
UPLOAD_TIMEOUT = 120             # seconds
UPLOAD_MAX_RETRIES = 6           # сколько попыток загрузки
UPLOAD_INITIAL_BACKOFF = 2.0    # начальная задержка
UPLOAD_BACKOFF_MULT = 2.5       # множитель задержки
UPLOAD_JITTER = 0.5             # случайное добавление к задержке
MAX_CONCURRENT_UPLOADS = 1      # лимит одновременных загрузок

# Семафор для ограничения одновременных загрузок
upload_semaphore = Semaphore(value=MAX_CONCURRENT_UPLOADS)

# Файлы и каталоги
ACCOUNTS_FILE = "accounts.json"
SETTINGS_FILE = "settings.json"
PROXIES_FILE = "proxies.txt"
SCREENSHOTS_DIR = "screenshots"
LOGS_DIR = "logs"

# Создаем необходимые папки
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(DEBUG_DIR, exist_ok=True)
os.makedirs(os.path.join(LOG_DIR, "upload_failures"), exist_ok=True)
os.makedirs(os.path.join(LOG_DIR, "submit_failures"), exist_ok=True)
os.makedirs(os.path.join(LOG_DIR, "proxy_failures"), exist_ok=True)
os.makedirs(os.path.join(LOG_DIR, "registration"), exist_ok=True)

# Инициализация TeleBot
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

# ========== Utility ==========

def log_message(level: str, message: str, category: str = "general"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {message}"
    print(line)
    
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        main_log = os.path.join(LOGS_DIR, f"bot_{datetime.now().strftime('%Y-%m-%d')}.txt")
        with open(main_log, "a", encoding="utf-8") as f:
            f.write(f"[{category}] {line}\n")
    except Exception as e:
        print(f"Ошибка записи лога: {e}")

def mask_secret(s, head=6, tail=4):
    """Маскирование чувствительной информации для отображения в Telegram"""
    if not s:
        return ""
    if len(s) <= head + tail + 3:
        return s
    return s[:head] + "..." + s[-tail:]

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
    if not os.path.exists(ACCOUNTS_FILE):
        save_json(ACCOUNTS_FILE, {})
    if not os.path.exists(SETTINGS_FILE):
        default = {
            "wallet": "",
            "invite_code": "E68E70F40",
            "max_accounts": 50,
            "delay_between_tasks": 3,
            "use_proxies": True,
            "area_code": "+1",
            "max_tasks_per_account": 10,
            "retry_attempts": 3,
            "auto_renew_token": True,
            "accounts_to_register": 10,
            "auto_take_enabled": False,
        }
        save_json(SETTINGS_FILE, default)

init_files()

def save_debug_file(filename, data, subdir=""):
    """Сохраняет отладочную информацию в файл"""
    try:
        if subdir:
            dir_path = os.path.join(DEBUG_DIR, subdir)
            os.makedirs(dir_path, exist_ok=True)
            file_path = os.path.join(dir_path, filename)
        else:
            file_path = os.path.join(DEBUG_DIR, filename)
        
        with open(file_path, "w", encoding="utf-8") as f:
            if isinstance(data, dict):
                json.dump(data, f, ensure_ascii=False, indent=2)
            else:
                f.write(str(data))
        return file_path
    except Exception as e:
        log_message("ERROR", f"save_debug_file error: {e}")
        return None

def validate_url(url: str, base_url: str = None) -> str:
    """Проверяет и нормализует URL"""
    if not url:
        return None
    
    # Нормализация относительных путей
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        if base_url:
            url = urljoin(base_url.rstrip("/") + "/", url.lstrip("/"))
        else:
            url = "https://partimetest.51c1e.live" + url
    
    # Проверка парсинга URL
    try:
        parsed = urlparse(url)
        if not (parsed.scheme in ("http", "https") and parsed.netloc):
            log_message("ERROR", f"Invalid URL format: {url}")
            return None
        
        # Проверка минимальной длины
        if len(url) < 12:
            log_message("ERROR", f"URL too short: {url}")
            return None
        
        return url
    except Exception as e:
        log_message("ERROR", f"URL parse error {url}: {e}")
        return None

# ========== Accounts helpers ==========

def get_accounts():
    return load_json(ACCOUNTS_FILE)

def save_account(phone, password, proxy="", wallet=""):
    accounts = get_accounts()
    if phone in accounts:
        return False
    
    settings = load_json(SETTINGS_FILE)
    if settings.get("use_proxies", True) and not proxy:
        proxies = get_proxies()
        if proxies:
            proxy = random.choice(proxies)
    
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
        "created": datetime.now().isoformat(),
        "auto_take": False,
        "last_poll": None,
        "task_mapping": {},  # Для хранения batchId -> taskId
        "cookies": {},  # Новое: храним cookies для сессии
        "last_cookie_update": None
    }
    save_json(ACCOUNTS_FILE, accounts)
    return True

def get_active_accounts():
    """Получить активные аккаунты с учетом настроек прокси"""
    accounts = get_accounts()
    settings = load_json(SETTINGS_FILE)
    use_proxies = settings.get("use_proxies", True)
    
    active_accounts = []
    for acc in accounts.values():
        if acc.get("status") == "active":
            if use_proxies:
                if acc.get("proxy"):
                    active_accounts.append(acc)
                else:
                    proxies = get_proxies()
                    if proxies:
                        acc["proxy"] = random.choice(proxies)
                        update_account_proxy(acc["phone"], acc["proxy"])
                        active_accounts.append(acc)
                    else:
                        log_message("WARNING", f"Пропускаем аккаунт {acc['phone']} - нет прокси", "accounts")
            else:
                active_accounts.append(acc)
    
    return active_accounts

def update_account_proxy(phone, proxy):
    """Обновить прокси аккаунта"""
    accounts = get_accounts()
    if phone in accounts:
        accounts[phone]["proxy"] = proxy
        save_json(ACCOUNTS_FILE, accounts)
        return True
    return False

def update_account_token(phone, token):
    accounts = get_accounts()
    if phone in accounts:
        if token and len(token) > 10:
            accounts[phone]["token"] = token
            accounts[phone]["last_login"] = datetime.now().isoformat()
            save_json(ACCOUNTS_FILE, accounts)
            log_message("INFO", f"Токен обновлен для {phone}: {mask_secret(token)}", "tokens")
            return True
        else:
            log_message("ERROR", f"Невалидный токен для {phone}: {token}", "tokens")
            return False
    return False

def update_account_cookies(phone, cookies_dict):
    """Обновить cookies аккаунта"""
    accounts = get_accounts()
    if phone in accounts:
        accounts[phone]["cookies"] = cookies_dict
        accounts[phone]["last_cookie_update"] = datetime.now().isoformat()
        save_json(ACCOUNTS_FILE, accounts)
        log_message("INFO", f"Cookies обновлены для {phone}: {len(cookies_dict)} cookies", "cookies")
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

# ========== Функция очистки данных аккаунтов ==========

def clear_accounts_data():
    """
    Очищает временные данные всех аккаунтов, но оставляет основные данные.
    Очищает: токены, cookies, task_mapping, авто-взятие и статистику.
    Сохраняет: номер телефона, пароль, прокси, статус.
    """
    accounts = get_accounts()
    cleared_count = 0
    
    for phone, acc in accounts.items():
        # Сохраняем основные данные
        basic_info = {
            "phone": acc.get("phone", phone),
            "password": acc.get("password", ""),
            "hashed_password": acc.get("hashed_password", ""),
            "proxy": acc.get("proxy", ""),
            "status": acc.get("status", "active"),
            "created": acc.get("created", datetime.now().isoformat()),
            "balance": acc.get("balance", 0.0),
            "area_code": acc.get("area_code", "+1"),
            "wallet": acc.get("wallet", "")
        }
        
        # Очищаем временные данные
        accounts[phone] = {
            **basic_info,
            "token": "",
            "cookies": {},
            "task_mapping": {},
            "tasks_completed": 0,
            "failed_logins": 0,
            "auto_take": False,
            "last_poll": None,
            "last_cookie_update": None,
            "last_login": None
        }
        cleared_count += 1
    
    if save_json(ACCOUNTS_FILE, accounts):
        log_message("INFO", f"Очищены данные {cleared_count} аккаунтов", "accounts")
        return True, cleared_count
    else:
        log_message("ERROR", "Ошибка сохранения после очистки данных аккаунтов", "accounts")
        return False, 0

# ========== Password Generation ==========

def generate_random_password(length=12):
    """Генерация случайного пароля"""
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(random.choice(chars) for _ in range(length))

# ========== Social media helpers ==========

def generate_random_username():
    """Генерация уникального имени пользователя"""
    adjectives = ["cool", "awesome", "best", "top", "amazing", "great", "super", "mega", "ultra", "pro", "fast", "smart", "happy", "lucky", "bright"]
    nouns = ["user", "player", "gamer", "creator", "master", "expert", "king", "queen", "star", "hero", "wolf", "tiger", "eagle", "dragon", "phoenix"]
    numbers = random.randint(1000, 99999)
    uuid_part = str(uuid.uuid4())[:8]
    return f"{random.choice(adjectives)}_{random.choice(nouns)}_{numbers}_{uuid_part}"

def get_social_url(task_title):
    """Определение соцсети по названию задания и генерация соответствующей ссылки"""
    if not task_title:
        username = generate_random_username()
        return f"https://discord.gg/{username}"
    
    task_title_lower = task_title.lower()
    
    if 'instagram' in task_title_lower or 'инстаграм' in task_title_lower or 'insta' in task_title_lower:
        username = generate_random_username()
        return f"https://www.instagram.com/{username}"
    
    elif 'tiktok' in task_title_lower or 'тикток' in task_title_lower or 'тик-ток' in task_title_lower:
        username = generate_random_username()
        return f"https://www.tiktok.com/@{username}"
    
    elif 'twitter' in task_title_lower or 'твиттер' in task_title_lower or 'x.com' in task_title_lower or 'икс' in task_title_lower:
        username = generate_random_username()
        return f"https://x.com/{username}"
    
    elif 'facebook' in task_title_lower or 'фейсбук' in task_title_lower or 'fb' in task_title_lower:
        username = generate_random_username()
        return f"https://www.facebook.com/{username}"
    
    elif 'youtube' in task_title_lower or 'ютуб' in task_title_lower or 'ютюб' in task_title_lower:
        username = generate_random_username()
        return f"https://www.youtube.com/@{username}"
    
    elif 'discord' in task_title_lower or 'дискорд' in task_title_lower:
        username = generate_random_username()
        return f"https://discord.gg/{username}"
    
    elif 'telegram' in task_title_lower or 'телеграм' in task_title_lower or 'тг' in task_title_lower:
        username = generate_random_username()
        return f"https://t.me/{username}"
    
    elif 'whatsapp' in task_title_lower or 'ватсап' in task_title_lower:
        username = generate_random_username()
        return f"https://whatsapp.com/channel/{username}"
    
    else:
        username = generate_random_username()
        return f"https://discord.gg/{username}"

# ========== Bot State for registration flows ==========
registration_states = {}
quick_registration_states = {}
mass_registration_state = {}
autotake_threads = {}

# ========== PartTime API wrapper с исправленными ошибками ==========

class PartTimeAPI:
    def __init__(self, phone=None, password=None, area_code="+1", proxy=None, token=None):
        self.base = "https://partimetest.51c1e.live"
        self.phone = phone
        self.password = password
        self.area_code = area_code
        self.token = token
        self.log_prefix = f"[{phone}] " if phone else ""
        
        # Инициализируем сессию
        self.session = requests.Session()
        
        # Настраиваем повторные попытки для сессии
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS", "TRACE"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # Browser-like headers (как в браузере)
        ua = 'Mozilla/5.0 (iPhone; CPU iPhone OS 15_8_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.6.7 Mobile/15E148 Safari/604.1'
        self.browser_headers = {
            "Accept": "*/*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Origin": self.base,
            "Referer": f"{self.base}/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": ua,
            "X-Requested-With": "XMLHttpRequest"
        }
        
        # Обновляем заголовки сессии
        self.session.headers.update(self.browser_headers)
        
        # Если токен передан, добавляем его в заголовки
        if self.token and len(self.token) > 10:
            self.session.headers.update({"Authorization": self.token})
            log_message("INFO", f"{self.log_prefix}Используется переданный токен", "api")
        
        # Устанавливаем прокси (с тестированием на целевом API)
        if proxy:
            self.set_proxy_for_account(proxy, phone, password, area_code)
        
        # Получаем начальные cookies
        if phone:
            self.fetch_initial_cookies()
    
    def fetch_initial_cookies(self):
        """Получаем начальные cookies с главной страницы"""
        try:
            log_message("INFO", f"{self.log_prefix}Получение начальных cookies...", "cookies")
            
            # Используем простой GET запрос без сложных заголовков
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
            }
            
            r = self.session.get(f"{self.base}/", headers=headers, timeout=10)
            
            # Извлекаем cookies
            cookies_dict = requests.utils.dict_from_cookiejar(self.session.cookies)
            
            if cookies_dict:
                log_message("INFO", f"{self.log_prefix}Получены cookies: {list(cookies_dict.keys())}", "cookies")
            else:
                log_message("WARNING", f"{self.log_prefix}Не удалось получить cookies", "cookies")
            
            # Сохраняем cookies в аккаунте
            if self.phone and cookies_dict:
                update_account_cookies(self.phone, cookies_dict)
            
            return True
        except Exception as e:
            log_message("ERROR", f"{self.log_prefix}Ошибка получения cookies: {e}", "cookies")
            return False
    
    def set_proxy_for_account(self, proxy_url, phone=None, password=None, area_code="+1"):
        """
        Устанавливает proxy для self.session только если:
        - короткий тест к целевому API через тестовую сессию проходит (HTTP 200),
        либо
        - тест не прошёл, но попытка login через тестовую сессию успешна (фолбэк).
        Возвращает True если proxy применён, False если отклонён.
        """
        if not proxy_url:
            return False

        # Нормализуем прокси
        if not proxy_url.startswith(("http://", "https://", "socks5://")):
            test_proxy = "http://" + proxy_url
        else:
            test_proxy = proxy_url

        test_sess = requests.Session()
        test_sess.headers.update(self.session.headers)
        test_sess.proxies.update({"http": test_proxy, "https": test_proxy})

        proxy_failure_info = {
            "timestamp": datetime.now().isoformat(),
            "proxy": test_proxy,
            "phone": phone,
            "errors": []
        }

        # 1) Короткий тест к целевому API
        try:
            url = f"{self.base}/apiAnt/taskIssue?lang=ru"
            r = test_sess.post(url, json={"pageNumber": 1, "pageSize": 1}, timeout=PROXY_TEST_TIMEOUT)
            
            if r.status_code == 200:
                self.session.proxies.update({"http": test_proxy, "https": test_proxy})
                log_message("INFO", f"{self.log_prefix}Прокси установлен: {mask_secret(test_proxy)} (тест к API OK)", "api")
                return True
            else:
                error_msg = f"Тест прокси вернул {r.status_code}"
                proxy_failure_info["errors"].append(error_msg)
                log_message("WARNING", f"{self.log_prefix}{error_msg}: {mask_secret(test_proxy)}", "api")
        except requests.exceptions.ProxyError as e:
            error_msg = f"ProxyError: {str(e)}"
            proxy_failure_info["errors"].append(error_msg)
            log_message("ERROR", f"{self.log_prefix}Ошибка прокси: {error_msg}", "api")
        except requests.exceptions.ConnectTimeout as e:
            error_msg = f"ConnectTimeout: {str(e)}"
            proxy_failure_info["errors"].append(error_msg)
            log_message("ERROR", f"{self.log_prefix}Таймаут подключения прокси: {error_msg}", "api")
        except Exception as e:
            error_msg = f"Exception: {str(e)}"
            proxy_failure_info["errors"].append(error_msg)
            log_message("ERROR", f"{self.log_prefix}Ошибка при тесте прокси {mask_secret(test_proxy)}: {e}", "api")

        # 2) Фолбэк: если логин-параметры переданы, попробовать login через test_sess
        if phone and password:
            try:
                hashed_password = hash_md5(password)
                device_id = f"device_{hash_md5(phone)}"[:20]
                
                data = {
                    "areaCode": area_code,
                    "email": "22",
                    "phone": phone,
                    "deviceType": "pc",
                    "deviceId": device_id,
                    "xieyi": [0],
                    "password": hashed_password
                }
                
                login_url = f"{self.base}/apiAnt/userLogin?lang=ru"
                r2 = test_sess.post(login_url, json=data, timeout=PROXY_TEST_TIMEOUT)
                
                if r2.status_code == 200:
                    j = r2.json()
                    if j.get("code") == 200:
                        token = j.get("data", {}).get("token")
                        if token:
                            self.session.proxies.update({"http": test_proxy, "https": test_proxy})
                            self.session.headers.update({"Authorization": token})
                            self.token = token
                            log_message("INFO", f"{self.log_prefix}Прокси установлен через фолбэк логина: {mask_secret(test_proxy)}", "api")
                            return True
                    else:
                        error_msg = f"Логин через прокси не удался, код: {j.get('code')}"
                        proxy_failure_info["errors"].append(error_msg)
                else:
                    error_msg = f"Логин HTTP статус: {r2.status_code}"
                    proxy_failure_info["errors"].append(error_msg)
                    
            except Exception as e:
                error_msg = f"Ошибка фолбэк-логина: {str(e)}"
                proxy_failure_info["errors"].append(error_msg)

        # Сохраняем информацию об ошибке прокси
        if proxy_failure_info["errors"]:
            save_debug_file(
                f"proxy_fail_{phone or 'unknown'}_{int(time.time())}.json",
                proxy_failure_info,
                "proxy_failures",
            )

        log_message("WARNING", f"{self.log_prefix}Прокси не применён: {mask_secret(test_proxy)}", "api")
        return False
    
    def post_json(self, endpoint, payload, extra_headers=None):
        """Отправляет JSON запрос с правильными заголовками и cookies"""
        url = f"{self.base}{endpoint}?lang=ru"
        
        # Подготавливаем заголовки
        headers = self.session.headers.copy()
        headers["Content-Type"] = "application/json"
        
        if extra_headers:
            headers.update(extra_headers)
        
        # Явно добавляем cookies в заголовки
        cookies_dict = requests.utils.dict_from_cookiejar(self.session.cookies)
        if cookies_dict:
            cookie_header = "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])
            headers["Cookie"] = cookie_header
        
        # Логируем запрос (без чувствительных данных)
        safe_headers = {
            k: (mask_secret(v) if 'auth' in k.lower() or 'token' in k.lower() else v)
            for k, v in headers.items()
        }
        log_message("DEBUG", f"{self.log_prefix}POST {endpoint} headers={safe_headers} payload={payload}", "api")
        
        try:
            response = self.session.post(url, json=payload, headers=headers, timeout=SESSION_TIMEOUT)
            
            # Сохраняем отладочную информацию
            if response.status_code != 200:
                debug_info = {
                    "timestamp": datetime.now().isoformat(),
                    "phone": self.phone,
                    "url": url,
                    "headers": dict(headers),
                    "cookies": cookies_dict,
                    "payload": payload,
                    "status": response.status_code,
                    "response": response.text[:500]
                }
                save_debug_file(
                    f"post_failure_{self.phone}_{endpoint.replace('/', '_')}_{int(time.time())}.json",
                    debug_info,
                    "post_failures",
                )
            
            return response
        except Exception as e:
            log_message("ERROR", f"{self.log_prefix}Ошибка POST {endpoint}: {e}", "api")
            raise
    
    def upload_file_with_retries(self, local_path, upload_endpoint=None):
        """
        Загружает один файл с retry + exponential backoff.
        Возвращает url (string) при успехе, None при провале.
        """
        if not os.path.isfile(local_path):
            log_message("WARNING", f"[{self.phone}] image not found: {local_path}", "upload")
            return None

        if upload_endpoint is None:
            upload_endpoint = "/apiAnt/upImage"

        attempt = 0
        backoff = UPLOAD_INITIAL_BACKOFF
        file_size = os.path.getsize(local_path)
        file_name = os.path.basename(local_path)

        while attempt < UPLOAD_MAX_RETRIES:
            attempt += 1
            try:
                # Определяем MIME-тип
                mime_type, _ = mimetypes.guess_type(local_path)
                if not mime_type:
                    mime_type = "image/jpeg"
                
                log_message("DEBUG", f"[{self.phone}] Загрузка {file_name} ({file_size} bytes, MIME: {mime_type})", "upload")
                
                # Сохраняем текущие заголовки
                saved_headers = self.session.headers.copy()
                
                try:
                    # Убираем Content-Type header перед загрузкой файлов
                    if "Content-Type" in self.session.headers:
                        self.session.headers.pop("Content-Type", None)
                    
                    # Явно добавляем cookies в заголовки
                    cookies_dict = requests.utils.dict_from_cookiejar(self.session.cookies)
                    if cookies_dict:
                        cookie_header = "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])
                        self.session.headers["Cookie"] = cookie_header
                    
                    with upload_semaphore:
                        url = f"{self.base}{upload_endpoint}?lang=ru"
                        with open(local_path, "rb") as f:
                            files = {"file": (file_name, f, mime_type)}
                            
                            # Сохраняем информацию о запросе
                            upload_request_info = {
                                "timestamp": datetime.now().isoformat(),
                                "phone": self.phone,
                                "file": file_name,
                                "size": file_size,
                                "mime_type": mime_type,
                                "endpoint": url,
                                "attempt": attempt,
                                "cookies": cookies_dict
                            }
                            save_debug_file(
                                f"upload_req_{self.phone}_{file_name}_attempt{attempt}.json",
                                upload_request_info,
                                "upload_requests",
                            )
                            
                            resp = self.session.post(url, files=files, timeout=UPLOAD_TIMEOUT)
                finally:
                    # ВАЖНО: восстанавливаем заголовки сессии
                    self.session.headers.clear()
                    self.session.headers.update(saved_headers)

                # Парсим JSON
                try:
                    j = resp.json()
                except Exception:
                    j = {"raw_text": resp.text}

                # Сохраняем сырой ответ
                response_info = {
                    "timestamp": datetime.now().isoformat(),
                    "phone": self.phone,
                    "file": file_name,
                    "attempt": attempt,
                    "status_code": resp.status_code,
                    "headers": dict(resp.headers),
                    "cookies": cookies_dict,
                    "response": j
                }
                
                response_file = f"upload_resp_{self.phone}_{file_name}_attempt{attempt}.json"
                save_debug_file(response_file, response_info, "upload_responses")

                # Проверяем HTTP статус и JSON содержимое на успех
                if resp.status_code in (200, 201):
                    # Извлекаем URL из ответа
                    url = None
                    if isinstance(j, dict):
                        if j.get("code") in (200, "200") and j.get("data"):
                            data = j.get("data")
                            if isinstance(data, list) and data:
                                url = data[0] if isinstance(data[0], str) else data[0].get("url")
                            elif isinstance(data, dict):
                                url = data.get("url") or data.get("fileUrl") or data.get("path")
                            elif isinstance(data, str):
                                url = data
                        
                        if not url:
                            url = j.get("url") or (j.get("data") if isinstance(j.get("data"), str) else None)
                    
                    # Валидация URL
                    if url:
                        validated_url = validate_url(url, self.base)
                        if validated_url:
                            log_message("INFO", f"[{self.phone}] upload success: {file_name} -> {mask_secret(validated_url)}", "upload")
                            return validated_url
                        else:
                            log_message("ERROR", f"[{self.phone}] upload returned invalid URL: {url}", "upload")
                            save_debug_file(
                                f"invalid_url_{self.phone}_{file_name}.json",
                                {"url": url, "response": j},
                                "upload_failures",
                            )
                    else:
                        log_message("WARNING", f"[{self.phone}] upload attempt {attempt} - no url in response", "upload")
                
                # Обработка ошибок
                msg = j.get("message") or j.get("msg") or ''
                log_message("WARNING", f"[{self.phone}] upload attempt {attempt} status {resp.status_code}: {msg}", "upload")
                
                # Если сервер говорит "Система занята" или 5xx -> retry
                if resp.status_code in (429, 503, 500) or ("Система занята" in str(msg) or "занят" in str(msg).lower()):
                    pass  # Продолжаем попытки
                else:
                    # Для других 4xx (не transient) -> прекращаем попытки
                    if 400 <= resp.status_code < 500:
                        log_message("ERROR", f"[{self.phone}] upload permanent error {resp.status_code} for {file_name}", "upload")
                        return None

            except Exception as e:
                log_message("ERROR", f"[{self.phone}] upload exception attempt {attempt} for {file_name}: {e}", "upload")
                save_debug_file(
                    f"upload_exception_{self.phone}_{file_name}_attempt{attempt}.json",
                    {"error": str(e), "traceback": traceback.format_exc()},
                    "upload_exceptions",
                )

            # Экспоненциальный бэкофф
            sleep_for = backoff + random.uniform(0, UPLOAD_JITTER)
            log_message("INFO", f"[{self.phone}] upload backoff for {sleep_for:.2f}s before retry (attempt {attempt})", "upload")
            time.sleep(sleep_for)
            backoff *= UPLOAD_BACKOFF_MULT

        log_message("ERROR", f"[{self.phone}] failed to upload {file_name} after {UPLOAD_MAX_RETRIES} attempts", "upload")
        return None
    
    def extract_task_info(self, task_data):
        """Устойчивое извлечение информации о задании"""
        task_id = (
            task_data.get("taskId")
            or task_data.get("id")
            or task_data.get("orderId")
            or task_data.get("task_id")
            or task_data.get("taskId")
        )
        
        if task_id:
            task_id = str(task_id)
        
        batch_id = task_data.get("batchId") or task_data.get("batch_id")
        if batch_id:
            batch_id = str(batch_id)
        
        if not task_id and not batch_id:
            log_message("ERROR", f"{self.log_prefix}Не найдены идентификаторы в структуре задания", "tasks")
            save_debug_file(f"task_raw_{self.phone}_{int(time.time())}.json", task_data, "task_raw")
            return None
        
        return {
            "task_id": task_id,
            "batch_id": batch_id,
            "title": task_data.get("taskTitle", "Без названия"),
            "reward": task_data.get("taskReward", 0),
            "status": task_data.get("taskStatus", 0),
            "description": task_data.get("taskDesc", ""),
            "raw_data": task_data
        }
    
    def upload_images_and_submit(self, account, task_id, local_image_paths, task_title=""):
        """
        Загружает изображения и отправляет задание с правильным форматом
        """
        # Проверяем токен перед началом
        if not self.token or len(self.token) < 10:
            log_message("ERROR", f"[{self.phone}] Нет валидного токена перед submit", "api")
            if not self.ensure_token_valid(force_login=True):
                return None
        
        uploaded_urls = []
        
        for local_path in local_image_paths:
            time.sleep(random.uniform(1.0, 2.5))
            url = self.upload_file_with_retries(local_path)
            if url:
                uploaded_urls.append(url)
            else:
                log_message("WARNING", f"[{self.phone}] skipping image {os.path.basename(local_path)}", "upload")
        
        if not uploaded_urls:
            log_message("ERROR", f"[{self.phone}] no images uploaded for task {task_id}", "submit")
            return None

        # Формируем социальную ссылку
        social_url = get_social_url(task_title)
        
        # Основной payload (проверенный формат)
        payload = {
            "taskId": str(task_id),
            "submitMsg": {
                "urlList": [social_url],
                "imgList": uploaded_urls,
                "videoUrlList": []
            }
        }
        
        # Валидация JSON перед отправкой
        try:
            payload_json = json.dumps(payload, ensure_ascii=False)
            # Двойная проверка
            json.loads(payload_json)
        except Exception as e:
            log_message("ERROR", f"[{self.phone}] Invalid JSON payload: {e}", "submit")
            save_debug_file(
                f"invalid_payload_{self.phone}_{task_id}.json",
                {"payload": payload, "error": str(e)},
                "submit_failures",
            )
            return None
        
        # Альтернативные варианты payload
        payload_variants = [
            {
                "name": "variant_full",
                "payload": payload
            },
            {
                "name": "variant_no_video",
                "payload": {
                    "taskId": str(task_id),
                    "submitMsg": {
                        "urlList": [social_url],
                        "imgList": uploaded_urls
                    }
                }
            },
            {
                "name": "variant_simple",
                "payload": {
                    "taskId": str(task_id),
                    "submitMsg": uploaded_urls[0] if uploaded_urls else social_url
                }
            }
        ]
        
        for variant in payload_variants:
            try:
                # Используем post_json для отправки
                response = self.post_json("/apiAnt/submitTask", variant["payload"])
                
                # Парсим ответ
                try:
                    j = response.json()
                except:
                    j = {"raw": response.text}
                
                # Сохраняем ответ
                response_info = {
                    "timestamp": datetime.now().isoformat(),
                    "phone": self.phone,
                    "task_id": task_id,
                    "variant": variant["name"],
                    "status": response.status_code,
                    "headers": dict(response.headers),
                    "cookies": requests.utils.dict_from_cookiejar(self.session.cookies),
                    "response": j,
                    "request_payload": variant["payload"],
                    "uploaded_urls": uploaded_urls,
                    "social_url": social_url
                }
                
                # Если ошибка 9000/9900, сохраняем отдельно для анализа
                if j.get("code") in [9000, 9900]:
                    save_debug_file(
                        f"submit_error_{self.phone}_{task_id}_{variant['name']}_code{j.get('code')}.json",
                        response_info,
                        "submit_errors",
                    )
                
                save_debug_file(
                    f"submit_resp_{self.phone}_{task_id}_{variant['name']}.json",
                    response_info,
                    "submit_responses",
                )
                
                log_message("INFO", f"[{self.phone}] submitTask response ({variant['name']}): code={j.get('code')}", "submit")
                
                if j.get("code") == 200:
                    log_message("SUCCESS", f"[{self.phone}] Вариант {variant['name']} успешен!", "submit")
                    increment_tasks_completed(self.phone)
                    return j
                elif j.get("code") == 9000:
                    log_message("ERROR", f"[{self.phone}] Вариант {variant['name']} ошибка 9000: {j.get('message')}", "submit")
                    # Пробуем следующий вариант
                    time.sleep(1)
                    continue
                elif j.get("code") == 9900:
                    log_message("ERROR", f"[{self.phone}] Вариант {variant['name']} ошибка 9900 (JSON): {j.get('message')}", "submit")
                    time.sleep(1)
                    continue
                else:
                    return j
                    
            except Exception as e:
                log_message("ERROR", f"[{self.phone}] submitTask exception for variant {variant['name']}: {e}", "submit")
                save_debug_file(
                    f"submit_exception_{self.phone}_{task_id}_{variant['name']}.json",
                    {"error": str(e), "traceback": traceback.format_exc()},
                    "submit_exceptions",
                )
                continue
        
        log_message("ERROR", f"[{self.phone}] Все варианты отправки не удались для task {task_id}", "submit")
        return None
    
    def set_log_prefix(self, prefix):
        self.log_prefix = f"[{prefix}] " if prefix else ""
    
    def get_captcha(self):
        """Получить капчу для регистрации"""
        log_message("INFO", f"{self.log_prefix}Запрос капчи", "api")
        try:
            url = f"{self.base}/apiAnt/validateCode?lang=ru&_={int(time.time()*1000)}"
            
            # Используем отдельную сессию для получения капчи
            temp_session = requests.Session()
            
            # Устанавливаем прокси если есть
            if self.session.proxies:
                temp_session.proxies.update(self.session.proxies)
            
            # Добавляем заголовки браузера
            temp_session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Referer": f"{self.base}/",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
            })
            
            r = temp_session.get(url, timeout=30)
            
            if r.status_code == 200 and r.content:
                log_message("INFO", f"{self.log_prefix}Капча получена ({len(r.content)} bytes)", "api")
                return r.content
            
            log_message("ERROR", f"{self.log_prefix}Ошибка получения капчи: статус {r.status_code}, размер {len(r.content) if r.content else 0}", "api")
            return None
        except Exception as e:
            log_message("ERROR", f"{self.log_prefix}Ошибка получения капчи: {e}", "api")
            return None
    
    def register_account(self, phone, captcha_code, password):
        """Регистрация аккаунта с улучшенной обработкой ошибок"""
        log_message("INFO", f"{self.log_prefix}Регистрация аккаунта {phone}", "api")
        
        try:
            headers = self.session.headers.copy()
            headers.update({
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Accept-Encoding": "gzip, deflate",
                "Origin": self.base,
                "Referer": f"{self.base}/",
                "Language": "ru",
            })

            # 1. Отправляем запрос sendSms
            url1 = f"{self.base}/apiAnt/sendSms?lang=ru"
            data1 = {
                "areaCode": "+1",
                "phone": phone,
                "verCode": captcha_code,
                "smsType": "REGISTER"
            }

            log_message("DEBUG", f"{self.log_prefix}Отправка sendSms: {data1}", "api")

            response1 = self.session.post(url1, json=data1, headers=headers, timeout=30)

            # Сохраняем сырой ответ для отладки
            debug_info1 = {
                "timestamp": datetime.now().isoformat(),
                "phone": phone,
                "stage": "sendSms",
                "url": url1,
                "request": data1,
                "status": response1.status_code,
                "headers": dict(response1.headers),
                "text": response1.text[:500] if response1.text else "EMPTY_RESPONSE",
                "cookies": requests.utils.dict_from_cookiejar(self.session.cookies)
            }
            save_debug_file(f"register_sendSms_{phone}_{int(time.time())}.json", debug_info1, "registration")

            if not response1.text:
                log_message("ERROR", f"{self.log_prefix}Пустой ответ от sendSms", "api")
                return False, "empty_response"

            try:
                j1 = response1.json()
            except json.JSONDecodeError as e:
                content_type = response1.headers.get("Content-Type", "")
                log_message(
                    "ERROR",
                    f"{self.log_prefix}Невозможно распарсить JSON из sendSms: {e}, content-type: {content_type}",
                    "api",
                )
                if "json" not in content_type.lower():
                    return False, f"invalid_json: non-json content-type {content_type or 'unknown'}"
                return False, f"invalid_json: {response1.text[:100]}"

            if j1.get("code") != 200:
                error_msg = j1.get("message", str(j1))
                log_message("ERROR", f"{self.log_prefix}Ошибка sendSms: {error_msg}", "api")
                return False, error_msg

            sms_code = j1.get("data", {}).get("smsCode", "")
            if not sms_code:
                log_message("ERROR", f"{self.log_prefix}Нет smsCode в ответе sendSms", "api")
                return False, "no_sms_code"

            # 2. Отправляем запрос checkSms
            url2 = f"{self.base}/apiAnt/checkSms?lang=ru"
            data2 = {
                "areaCode": "+1",
                "phone": phone,
                "smsCode": sms_code
            }

            log_message("DEBUG", f"{self.log_prefix}Отправка checkSms: {data2}", "api")

            response2 = self.session.post(url2, json=data2, headers=headers, timeout=30)

            # Сохраняем сырой ответ для отладки
            debug_info2 = {
                "timestamp": datetime.now().isoformat(),
                "phone": phone,
                "stage": "checkSms",
                "url": url2,
                "request": data2,
                "status": response2.status_code,
                "headers": dict(response2.headers),
                "text": response2.text[:500] if response2.text else "EMPTY_RESPONSE",
                "cookies": requests.utils.dict_from_cookiejar(self.session.cookies)
            }
            save_debug_file(f"register_checkSms_{phone}_{int(time.time())}.json", debug_info2, "registration")

            if not response2.text:
                log_message("ERROR", f"{self.log_prefix}Пустой ответ от checkSms", "api")
                return False, "empty_response"

            try:
                j2 = response2.json()
            except json.JSONDecodeError as e:
                content_type = response2.headers.get("Content-Type", "")
                log_message(
                    "ERROR",
                    f"{self.log_prefix}Невозможно распарсить JSON из checkSms: {e}, content-type: {content_type}",
                    "api",
                )
                if "json" not in content_type.lower():
                    return False, f"invalid_json: non-json content-type {content_type or 'unknown'}"
                return False, f"invalid_json: {response2.text[:100]}"

            if j2.get("code") != 200:
                error_msg = j2.get("message", str(j2))
                log_message("ERROR", f"{self.log_prefix}Ошибка checkSms: {error_msg}", "api")
                return False, error_msg

            sms_token = j2.get("data", {}).get("smsToken")
            if not sms_token:
                log_message("ERROR", f"{self.log_prefix}Нет sms_token в ответе", "api")
                return False, "no_sms_token"

            # 3. Отправляем запрос register
            url3 = f"{self.base}/apiAnt/register?lang=ru"
            settings = load_json(SETTINGS_FILE)
            hashed_pwd = hash_md5(password)
            device_id = str(random.randint(10**18, 10**19-1))

            data3 = {
                "password": hashed_pwd,
                "areaCode": "+1",
                "phone": phone,
                "smsToken": sms_token,
                "deviceId": device_id,
                "deviceType": "phone",
                "inviteCode": settings.get("invite_code", ""),
                "channelCode": "", 
                "refCode": ""
            }

            log_message("DEBUG", f"{self.log_prefix}Отправка register: {data3}", "api")

            response3 = self.session.post(url3, json=data3, headers=headers, timeout=30)

            # Сохраняем сырой ответ для отладки
            debug_info3 = {
                "timestamp": datetime.now().isoformat(),
                "phone": phone,
                "stage": "register",
                "url": url3,
                "request": data3,
                "status": response3.status_code,
                "headers": dict(response3.headers),
                "text": response3.text[:500] if response3.text else "EMPTY_RESPONSE",
                "cookies": requests.utils.dict_from_cookiejar(self.session.cookies)
            }
            save_debug_file(f"register_register_{phone}_{int(time.time())}.json", debug_info3, "registration")

            if not response3.text:
                log_message("ERROR", f"{self.log_prefix}Пустой ответ от register", "api")
                return False, "empty_response"

            try:
                j3 = response3.json()
            except json.JSONDecodeError as e:
                content_type = response3.headers.get("Content-Type", "")
                log_message(
                    "ERROR",
                    f"{self.log_prefix}Невозможно распарсить JSON из register: {e}, content-type: {content_type}",
                    "api",
                )
                if "json" not in content_type.lower():
                    return False, f"invalid_json: non-json content-type {content_type or 'unknown'}"
                return False, f"invalid_json: {response3.text[:100]}"

            if j3.get("code") == 200:
                token = j3.get("data", {}).get("token")
                if token:
                    self.session.headers.update({"Authorization": token})
                    self.token = token

                # Обновляем cookies после регистрации
                cookies_dict = requests.utils.dict_from_cookiejar(self.session.cookies)
                if self.phone and cookies_dict:
                    update_account_cookies(self.phone, cookies_dict)

                log_message("INFO", f"{self.log_prefix}Регистрация успешна", "api")
                return True, j3

            error_msg = j3.get("message", str(j3))
            log_message("ERROR", f"{self.log_prefix}Ошибка регистрации: {error_msg}", "api")
            return False, error_msg

        except Exception as e:
            log_message("ERROR", f"{self.log_prefix}Ошибка регистрации: {e}", "api")
            return False, str(e)

    def ensure_token_valid(self, force_login=False):
        """Проверяет валидность токена и обновляет при необходимости"""
        if force_login or not self.token:
            log_message("INFO", f"{self.log_prefix}Принудительный логин", "api")
            return self.login_auto()
        
        try:
            # Проверяем токен через taskIssue
            response = self.post_json("/apiAnt/taskIssue", {"pageNumber": 1, "pageSize": 1})
            
            if response.status_code == 200:
                result = response.json()
                if result.get("code") == 200:
                    log_message("DEBUG", f"{self.log_prefix}Токен валиден", "api")
                    return True
                elif result.get("code") in [401, 403, 500]:
                    log_message("WARNING", f"{self.log_prefix}Токен невалиден, код: {result.get('code')}", "api")
                    return self.login_auto()
            
            log_message("WARNING", f"{self.log_prefix}Ошибка HTTP при проверке токена: {response.status_code}", "api")
            return self.login_auto()
                
        except Exception as e:
            log_message("ERROR", f"{self.log_prefix}Ошибка проверки токена: {e}", "api")
            return self.login_auto()
    
    def login_auto(self):
        """Автоматический логин"""
        log_message("INFO", f"{self.log_prefix}Автоматический логин...", "api")
        return self.login(self.phone, self.password, self.area_code)
    
    def login(self, phone=None, password=None, area_code="+1"):
        """Вход через API"""
        if phone:
            self.phone = phone
        if password:
            self.password = password
        if area_code:
            self.area_code = area_code
        
        log_message("INFO", f"{self.log_prefix}Попытка входа для {self.area_code}{self.phone}", "api")
        
        # Получаем начальные cookies если их нет
        if not requests.utils.dict_from_cookiejar(self.session.cookies):
            self.fetch_initial_cookies()
        
        hashed_password = hash_md5(self.password)
        device_id = f"device_{hash_md5(self.phone)}"[:20]
        
        data = {
            "areaCode": self.area_code,
            "email": "22",
            "phone": self.phone,
            "deviceType": "pc",
            "deviceId": device_id,
            "xieyi": [0],
            "password": hashed_password
        }
        
        for attempt in range(2):
            try:
                response = self.post_json("/apiAnt/userLogin", data)
                j = response.json()
                
                if j.get("code") == 200:
                    self.token = j["data"].get("token")
                    if self.token:
                        self.session.headers.update({"Authorization": self.token})
                        
                        # Обновляем cookies после успешного логина
                        cookies_dict = requests.utils.dict_from_cookiejar(self.session.cookies)
                        if self.phone and cookies_dict:
                            update_account_cookies(self.phone, cookies_dict)
                        
                        log_message("INFO", f"{self.log_prefix}Вход успешен, токен получен", "api")
                    else:
                        log_message("ERROR", f"{self.log_prefix}Вход успешен, но токен не получен", "api")
                        if attempt < 1:
                            time.sleep(2)
                            continue
                        return False, "no_token_received"
                    return True, {"token": self.token, "data": j["data"]}
                else:
                    error_msg = j.get("message", str(j))
                    log_message("ERROR", f"{self.log_prefix}Ошибка входа (попытка {attempt+1}): {error_msg}", "api")
                    if attempt < 1:
                        time.sleep(2)
                        continue
                    return False, error_msg
                    
            except requests.exceptions.RequestException as e:
                log_message("ERROR", f"{self.log_prefix}Сетевая ошибка при входе (попытка {attempt+1}): {e}", "api")
                if attempt < 1:
                    time.sleep(2)
                    continue
                return False, str(e)
            except Exception as e:
                log_message("ERROR", f"{self.log_prefix}Неизвестная ошибка при входе (попытка {attempt+1}): {e}", "api")
                if attempt < 1:
                    time.sleep(2)
                    continue
                return False, str(e)
        
        return False, "max_attempts_exceeded"
    
    def get_all_tasks(self):
        """Получить все доступные задания"""
        log_message("INFO", f"{self.log_prefix}Запрос доступных заданий", "api")
        try:
            response = self.post_json("/apiAnt/taskIssue", {"pageNumber": 1, "pageSize": 50})
            j = response.json()
            
            # Сохраняем сырой ответ
            save_debug_file(
                f"taskIssue_{self.phone if self.phone else 'unknown'}_{int(time.time())}.json",
                j,
                "task_responses",
            )
            
            if j.get("code") == 200:
                tasks = j.get("data", {}).get("rows", [])
                log_message("INFO", f"{self.log_prefix}Найдено {len(tasks)} заданий", "api")
                return tasks, None
            elif j.get("code") in [401, 403]:
                log_message("WARNING", f"{self.log_prefix}Токен устарел при запросе заданий", "api")
                return None, "token_expired"
            else:
                error_msg = j.get("message", "no-tasks")
                log_message("WARNING", f"{self.log_prefix}Ошибка получения заданий: {error_msg}", "api")
                return [], error_msg
        except Exception as e:
            log_message("ERROR", f"{self.log_prefix}Ошибка получения заданий: {e}", "api")
            return [], str(e)
    
    def apply_task(self, batch_id):
        """Применить задание (взять его)"""
        log_message("INFO", f"{self.log_prefix}Попытка взять задание batchId={batch_id}", "api")
        try:
            response = self.post_json("/apiAnt/applyTask", {"batchId": batch_id})
            result = response.json()
            
            # Сохраняем сырой ответ
            save_debug_file(
                f"apply_{self.phone if self.phone else 'unknown'}_{batch_id}.json",
                result,
                "apply_responses",
            )
            
            if result.get("code") == 200:
                log_message("INFO", f"{self.log_prefix}Задание batchId={batch_id} успешно взято", "api")
            elif result.get("code") in [401, 403]:
                log_message("WARNING", f"{self.log_prefix}Токен устарел при взятии задания", "api")
                return {"code": "token_expired", "message": "Token expired"}
            else:
                log_message("WARNING", f"{self.log_prefix}Ошибка взятия задания batchId={batch_id}: {result.get('message')}", "api")
            
            return result
        except Exception as e:
            log_message("ERROR", f"{self.log_prefix}Ошибка взятия задания: {e}", "api")
            return {"code": 500, "message": str(e)}
    
    def get_applied_tasks(self):
        """Получить список уже взятых заданий"""
        log_message("INFO", f"{self.log_prefix}Запрос взятых заданий", "api")
        try:
            response = self.post_json("/apiAnt/taskList", {"pageNumber": 1, "pageSize": 50, "taskStatus": 1})
            j = response.json()
            
            if j.get("code") == 200:
                tasks = j.get("data", {}).get("rows", [])
                log_message("INFO", f"{self.log_prefix}Найдено {len(tasks)} взятых заданий", "api")
                return tasks, None
            elif j.get("code") in [401, 403]:
                log_message("WARNING", f"{self.log_prefix}Токен устарел при запросе взятых заданий", "api")
                return None, "token_expired"
            else:
                error_msg = j.get("message", "no-tasks")
                log_message("WARNING", f"{self.log_prefix}Ошибка получения взятых заданий: {error_msg}", "api")
                return [], error_msg
        except Exception as e:
            log_message("ERROR", f"{self.log_prefix}Ошибка получения взятых заданий: {e}", "api")
            return [], str(e)
    
    def get_task_mapping(self, batch_id):
        """Получить taskId по batchId"""
        applied_tasks, _ = self.get_applied_tasks()
        if not applied_tasks:
            return None
        
        for task in applied_tasks:
            task_info = self.extract_task_info(task)
            if task_info and task_info.get("batch_id") == str(batch_id):
                return task_info.get("task_id")
        
        return None
    
    def poll_for_applied_task(self, batch_id, timeout=15):
        """Ожидание появления взятого задания после apply"""
        log_message("INFO", f"{self.log_prefix}Ожидание подтверждения задания batchId={batch_id}", "tasks")
        
        for i in range(timeout // 2):  # Проверяем каждые 2 секунды
            applied_tasks, _ = self.get_applied_tasks()
            if applied_tasks:
                for task in applied_tasks:
                    if str(task.get("batchId")) == str(batch_id):
                        task_info = self.extract_task_info(task)
                        if task_info and task_info.get("task_id"):
                            log_message("INFO", f"{self.log_prefix}Задание подтверждено, taskId: {task_info['task_id']}", "tasks")
                            return task_info['task_id']
            
            if i < (timeout // 2) - 1:
                time.sleep(2)
        
        log_message("WARNING", f"{self.log_prefix}Задание batchId={batch_id} не появилось за {timeout} секунд", "tasks")
        return None
    
    def take_available_tasks_for_account(self, account, max_to_take=MAX_TASKS_PER_RUN):
        """
        Основной метод для взятия доступных заданий для аккаунта
        """
        phone = account.get("phone", self.phone)
        log_message("INFO", f"{self.log_prefix}Начинаю взятие заданий", "tasks")
        
        # Проверяем и обновляем токен
        if not self.ensure_token_valid():
            log_message("ERROR", f"{self.log_prefix}Не удалось получить валидный токен", "tasks")
            return 0
        
        # Получаем доступные задания
        tasks, error = self.get_all_tasks()
        if error == "token_expired":
            self.ensure_token_valid(force_login=True)
            tasks, error = self.get_all_tasks()
        
        if not tasks or error:
            log_message("INFO", f"{self.log_prefix}Нет доступных заданий или ошибка: {error}", "tasks")
            return 0
        
        # Получаем уже взятые задания
        applied_tasks, _ = self.get_applied_tasks()
        applied_batch_ids = {task.get("batchId") for task in (applied_tasks or []) if task.get("batchId")}
        
        # Фильтруем задания, которые еще не взяты
        tasks_to_take = []
        for task in tasks:
            batch_id = task.get("batchId")
            if batch_id and batch_id not in applied_batch_ids:
                tasks_to_take.append(task)
        
        if not tasks_to_take:
            log_message("INFO", f"{self.log_prefix}Все доступные задания уже взяты", "tasks")
            return 0
        
        # Ограничиваем количество заданий за один проход
        tasks_to_take = tasks_to_take[:max_to_take]
        log_message("INFO", f"{self.log_prefix}Буду брать {len(tasks_to_take)} заданий из {len(tasks)} доступных", "tasks")
        
        taken_count = 0
        for task in tasks_to_take:
            task_info = self.extract_task_info(task)
            if not task_info:
                continue
                
            batch_id = task_info.get("batch_id")
            task_title = task_info.get("title", "Без названия")
            reward = task_info.get("reward", 0)
            
            if not batch_id:
                log_message("ERROR", f"{self.log_prefix}Нет batchId для задания: {task_title}", "tasks")
                continue
            
            # Пытаемся взять задание с ретраями
            success = False
            
            for attempt in range(1, MAX_APPLY_RETRIES + 1):
                apply_result = self.apply_task(batch_id)
                
                if apply_result and apply_result.get("code") == "token_expired":
                    self.ensure_token_valid(force_login=True)
                    apply_result = self.apply_task(batch_id)
                
                if apply_result and apply_result.get("code") == 200:
                    success = True
                    taken_count += 1
                    log_message("INFO", f"{self.log_prefix}Успешно взято задание: {task_title} (${reward})", "tasks")
                    
                    # Ждем подтверждения задания и получаем task_id
                    task_id = self.poll_for_applied_task(batch_id)
                    
                    if task_id:
                        # Сохраняем маппинг в account
                        accounts = get_accounts()
                        if phone in accounts:
                            if "task_mapping" not in accounts[phone]:
                                accounts[phone]["task_mapping"] = {}
                            accounts[phone]["task_mapping"][batch_id] = task_id
                            save_json(ACCOUNTS_FILE, accounts)
                            log_message("INFO", f"{self.log_prefix}Сохранен маппинг {batch_id} -> {task_id}", "tasks")
                    
                    break
                else:
                    error_msg = apply_result.get('message', 'unknown') if apply_result else 'unknown'
                    log_message("WARNING", f"{self.log_prefix}Попытка {attempt} взятия задания {task_title} не удалась: {error_msg}", "tasks")
                    
                    if attempt < MAX_APPLY_RETRIES:
                        time.sleep(APPLY_RETRY_DELAY)
            
            if not success:
                log_message("ERROR", f"{self.log_prefix}Не удалось взять задание {task_title} после {MAX_APPLY_RETRIES} попыток", "tasks")
            
            # Небольшая пауза между взятиями заданий
            time.sleep(1.0)
        
        log_message("INFO", f"{self.log_prefix}Всего взято заданий в этом цикле: {taken_count}", "tasks")
        return taken_count
    
    def start_autotake_for_account(self, account):
        """
        Запускает фоновый поллинг для автоматического взятия заданий для аккаунта
        """
        def worker():
            phone = account.get("phone")
            log_message("INFO", f"Запущен авто-взятие для аккаунта {phone}", "autotake")
            
            while True:
                try:
                    # Проверяем, включено ли авто-взятие для аккаунта
                    accounts = get_accounts()
                    if phone not in accounts or not accounts[phone].get("auto_take", False):
                        log_message("INFO", f"Авто-взятие остановлено для аккаунта {phone}", "autotake")
                        break
                    
                    # Применяем прокси
                    proxy = account.get("proxy")
                    if proxy:
                        self.set_proxy_for_account(
                            proxy,
                            account.get("phone"),
                            account.get("password"),
                            account.get("area_code", "+1"),
                        )
                    
                    # Проверяем и обновляем токен
                    if not self.ensure_token_valid():
                        log_message("WARNING", f"Не удалось обновить токен для {phone}, пробуем логин", "autotake")
                        success, _ = self.login(
                            account.get("phone"),
                            account.get("password"),
                            account.get("area_code", "+1"),
                        )
                        if not success:
                            log_message("ERROR", f"Не удалось войти для {phone}, пропускаем цикл", "autotake")
                            time.sleep(POLL_INTERVAL * 3)
                            continue
                    
                    # Запускаем взятие заданий
                    self.take_available_tasks_for_account(account, MAX_TASKS_PER_RUN)
                    
                    # Обновляем время последнего опроса
                    accounts = get_accounts()
                    if phone in accounts:
                        accounts[phone]["last_poll"] = datetime.now().isoformat()
                        save_json(ACCOUNTS_FILE, accounts)
                    
                except Exception as e:
                    log_message("ERROR", f"Ошибка в worker авто-взятия для {phone}: {e}", "autotake")
                
                # Ждем перед следующим циклом
                time.sleep(POLL_INTERVAL)
        
        # Запускаем поток
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        return t
    
    # ========== Новые методы из второго скрипта ==========
    
    def get_all_tasks_v2(self):
        """Запрос доступных заданий (новая версия)"""
        log_message("INFO", f"{self.log_prefix}Запрос доступных заданий (v2)", "api")
        try:
            response = self.post_json("/apiAnt/taskList", {"pageNumber": 1, "pageSize": 50, "taskStatus": 1})
            j = response.json()
            
            if j.get("code") == 200:
                tasks = j.get("data", {}).get("rows", [])
                log_message("INFO", f"{self.log_prefix}Найдено {len(tasks)} заданий (v2)", "api")
                return tasks, None
            else:
                error_msg = j.get("message", "Неизвестная ошибка")
                log_message("WARNING", f"{self.log_prefix}Ошибка получения заданий (v2): {error_msg}", "api")
                return [], error_msg
        except Exception as e:
            log_message("ERROR", f"{self.log_prefix}Ошибка получения заданий (v2): {e}", "api")
            return [], str(e)
    
    def apply_task_v2(self, batch_id):
        """Попытка выполнить задание по batchId (новая версия)"""
        log_message("INFO", f"{self.log_prefix}Попытка выполнить задание с batchId={batch_id} (v2)", "api")
        try:
            response = self.post_json("/apiAnt/taskBatch", {"batchId": batch_id})
            j = response.json()

            if j.get("code") == 200:
                log_message("INFO", f"{self.log_prefix}Задание с batchId={batch_id} успешно взято (v2)", "api")
                return True
            else:
                log_message("ERROR", f"{self.log_prefix}Не удалось выполнить задание с batchId={batch_id}. Сообщение: {j.get('message')} (v2)", "api")
                return False
        except Exception as e:
            log_message("ERROR", f"{self.log_prefix}Ошибка выполнения задания с batchId={batch_id} (v2): {e}", "api")
            return False
    
    def extract_task_info_v2(self, task):
        """Извлечение полезной информации о задании (новая версия)"""
        try:
            task_info = {
                "task_id": task.get("taskId"),
                "title": task.get("taskName", {}).get("en", "Без названия"),
                "batch_id": task.get("batchId"),
                "reward": task.get("taskReward", 0),
                "status": task.get("taskStatus", 0)
            }
            return task_info
        except Exception as e:
            log_message("ERROR", f"{self.log_prefix}Ошибка извлечения данных задания (v2): {e}", "api")
            return None

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
    markup.add("➕ Регистрация", "➕ Регистрация (1 акк)", "➕ Массовая регистрация")
    markup.add("📋 Аккаунты", "🔐 Вход через API")
    markup.add("🔄 ВЗЯТЬ ВСЕ ЗАДАНИЯ", "🚀 ВЫПОЛНИТЬ ЗАДАНИЯ", "📊 Статистика")
    markup.add("🔍 Проверить задания", "🧪 Тест API", "🔄 Обновить токены", "🔃 Авто-взятие")
    markup.add("⏸ Остановить авто-взятие", "🧹 Очистить аккаунты", "🧼 Очистить данные аккаунтов", "📝 Обновить меню", "🧪 Тест v2")
    bot.send_message(ADMIN_ID, "PartTime Manager (исправленные ошибки 9000):", reply_markup=markup)

# ========== Новая функция очистки данных аккаунтов ==========

@bot.message_handler(func=lambda m: m.text == "🧼 Очистить данные аккаунтов")
@admin_only
def clear_accounts_data_handler(message):
    """Очистка данных всех аккаунтов (токены, cookies, статистика)"""
    accounts = get_accounts()
    if not accounts:
        bot.send_message(ADMIN_ID, "📭 Нет аккаунтов для очистки данных")
        return
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ ДА, очистить данные", callback_data="clear_accounts_data_yes"),
        types.InlineKeyboardButton("❌ НЕТ, отмена", callback_data="clear_accounts_data_no")
    )
    
    bot.send_message(
        ADMIN_ID,
        f"⚠️ ВНИМАНИЕ! Вы собираетесь очистить данные ВСЕХ аккаунтов ({len(accounts)} шт.)\n\n"
        f"Будут удалены:\n"
        f"• Токены авторизации\n"
        f"• Cookies сессий\n"
        f"• Маппинги заданий\n"
        f"• Статистика выполненных заданий\n"
        f"• Настройки авто-взятия\n\n"
        f"СОХРАНЯТСЯ:\n"
        f"• Номера телефонов\n"
        f"• Пароли\n"
        f"• Прокси\n"
        f"• Статусы аккаунтов\n\n"
        f"Это действие НЕОБРАТИМО!\n"
        f"Вы уверены?",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("clear_accounts_data_"))
def handle_clear_accounts_data_confirmation(call):
    if call.data == "clear_accounts_data_yes":
        success, cleared_count = clear_accounts_data()
        
        if success:
            # Останавливаем авто-взятие для всех аккаунтов
            for phone in list(autotake_threads.keys()):
                if phone in autotake_threads:
                    del autotake_threads[phone]
            
            bot.edit_message_text(
                f"✅ Успешно очищены данные {cleared_count} аккаунтов\n\n"
                f"Очищено:\n"
                f"• Токены авторизации\n"
                f"• Cookies сессий\n"
                f"• Маппинги заданий\n"
                f"• Статистика выполненных заданий\n"
                f"• Настройки авто-взятия\n\n"
                f"Сохранено:\n"
                f"• Номера телефонов\n"
                f"• Пароли\n"
                f"• Прокси\n"
                f"• Статусы аккаунтов",
                call.message.chat.id,
                call.message.message_id
            )
            
            log_message("INFO", f"Очищены данные {cleared_count} аккаунтов", "accounts")
        else:
            bot.edit_message_text(
                "❌ Ошибка при очистке данных аккаунтов",
                call.message.chat.id,
                call.message.message_id
            )
        
    elif call.data == "clear_accounts_data_no":
        bot.edit_message_text(
            "❌ Очистка данных аккаунтов отменена",
            call.message.chat.id,
            call.message.message_id
        )

@bot.message_handler(func=lambda m: m.text == "➕ Регистрация")
@admin_only
def start_quick_registration(message):
    """Быстрая регистрация (пароль = номер) из telegram_bot.py"""
    proxies = get_proxies()
    proxy = random.choice(proxies) if proxies else None
    api = PartTimeAPI(proxy=proxy)
    phone = str(random.randint(1000000000, 1999999999))
    captcha = api.get_captcha()
    if not captcha:
        bot.send_message(ADMIN_ID, "❌ Не удалось получить капчу. Попробуйте позже.")
        return
    quick_registration_states[message.from_user.id] = {
        "phone": phone,
        "api": api,
        "proxy": proxy,
        "attempts": 0,
    }
    bot.send_photo(
        ADMIN_ID,
        captcha,
        caption=f"Капча для номера +1{phone}\nВведите 4 цифры капчи:",
    )

@bot.message_handler(func=lambda m: m.text == "➕ Регистрация (1 акк)")
@admin_only
def start_registration(message):
    """Начало регистрации одного аккаунта"""
    try:
        proxies = get_proxies()
        proxy = random.choice(proxies) if proxies else None
        
        settings = load_json(SETTINGS_FILE)
        if settings.get("use_proxies", True) and not proxy:
            bot.send_message(ADMIN_ID, "❌ Нет доступных прокси для регистрации")
            return
        
        # Создаем временную сессию без номера телефона
        api = PartTimeAPI(proxy=proxy)
        
        # Генерируем номер телефона и пароль
        phone = str(random.randint(1000000000, 1999999999))
        password = generate_random_password()
        
        # Получаем капчу
        captcha = api.get_captcha()
        if not captcha:
            bot.send_message(ADMIN_ID, "❌ Не удалось получить капчу. Попробуйте позже.")
            return
        
        # Сохраняем состояние регистрации
        registration_states[message.from_user.id] = {
            "phone": phone, 
            "password": password,
            "api": api, 
            "proxy": proxy, 
            "attempts": 0
        }
        
        # Отправляем капчу пользователю
        bot.send_photo(
            ADMIN_ID,
            captcha,
            caption=(
                f"Капча для номера +1{phone}\n"
                f"Пароль: {password}\n"
                f"Прокси: {mask_secret(proxy) if proxy else 'Нет'}\n"
                "Введите 4 цифры капчи:"
            ),
        )
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Ошибка начала регистрации: {str(e)}")
        log_message("ERROR", f"Ошибка начала регистрации: {e}", "registration")

@bot.message_handler(func=lambda m: m.text == "➕ Массовая регистрация")
@admin_only
def start_mass_registration(message):
    """Начало массовой регистрации"""
    msg = bot.send_message(
        ADMIN_ID,
        "Сколько аккаунтов зарегистрировать?\n"
        "Введите число от 1 до 100:",
    )
    
    bot.register_next_step_handler(msg, process_mass_registration_count)

def process_mass_registration_count(message):
    try:
        count = int(message.text.strip())
        if count < 1 or count > 100:
            bot.send_message(ADMIN_ID, "❌ Введите число от 1 до 100")
            return
        
        settings = load_json(SETTINGS_FILE)
        settings["accounts_to_register"] = count
        save_json(SETTINGS_FILE, settings)
        
        proxies = get_proxies()
        proxy = random.choice(proxies) if proxies else None
        
        if settings.get("use_proxies", True) and not proxy:
            bot.send_message(ADMIN_ID, "❌ Нет доступных прокси для массовой регистрации")
            return
        
        # Создаем временную сессию
        api = PartTimeAPI(proxy=proxy)
        phone = str(random.randint(1000000000, 1999999999))
        password = generate_random_password()
        
        captcha = api.get_captcha()
        if not captcha:
            bot.send_message(ADMIN_ID, "❌ Не удалось получить капчу. Попробуйте позже.")
            return
        
        mass_registration_state[message.from_user.id] = {
            "total": count,
            "completed": 0,
            "failed": 0,
            "current": {
                "phone": phone,
                "password": password,
                "api": api,
                "proxy": proxy,
                "attempts": 0
            }
        }
        
        bot.send_photo(
            ADMIN_ID,
            captcha,
            caption=(
                "📦 МАССОВАЯ РЕГИСТРАЦИЯ\n"
                f"Аккаунт 1 из {count}\n"
                f"Номер: +1{phone}\n"
                f"Пароль: {password}\n"
                f"Прокси: {mask_secret(proxy) if proxy else 'Нет'}\n"
                "Введите 4 цифры капчи:"
            ),
        )
                             
    except ValueError:
        bot.send_message(ADMIN_ID, "❌ Введите корректное число")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Ошибка: {str(e)}")

@bot.message_handler(func=lambda m: m.from_user.id in quick_registration_states)
@admin_only
def handle_quick_registration_captcha(message):
    st = quick_registration_states.get(message.from_user.id)
    if not st:
        return
    code = message.text.strip()
    if not code.isdigit() or len(code) != 4:
        bot.send_message(ADMIN_ID, "Капча — 4 цифры. Попробуйте снова.")
        return
    phone = st["phone"]
    api = st["api"]
    st["attempts"] += 1
    bot.send_message(
        ADMIN_ID,
        f"Регистрация: пытаюсь зарегистрировать +1{phone} (попытка {st['attempts']})...",
    )
    ok, resp = api.register_account(phone, code, phone)
    if ok:
        settings = load_json(SETTINGS_FILE)
        saved = save_account(phone, phone, proxy=st.get("proxy", ""), wallet=settings.get("wallet", ""))
        if saved:
            token = resp.get("data", {}).get("token", "")
            if token:
                update_account_token(phone, token)
            bot.send_message(ADMIN_ID, f"✅ Зарегистрирован: +1{phone}  (пароль = номер).")
        else:
            bot.send_message(ADMIN_ID, "❌ Аккаунт уже существует.")
        quick_registration_states.pop(message.from_user.id, None)
        return
    err = str(resp)
    if st["attempts"] < 3:
        new_proxy = None
        proxies = get_proxies()
        if proxies:
            new_proxy = random.choice(proxies)
            try:
                api.set_proxy_for_account(new_proxy, phone, phone, "+1")
                st["proxy"] = new_proxy
            except Exception:
                pass
        new_captcha = api.get_captcha()
        if new_captcha:
            bot.send_photo(
                ADMIN_ID,
                new_captcha,
                caption=(
                    f"Ошибка регистрации: {err}\n"
                    f"Новая капча (прокси {new_proxy}): Введите 4 цифры"
                ),
            )
            return
    bot.send_message(ADMIN_ID, f"❌ Регистрация не удалась: {err}")
    quick_registration_states.pop(message.from_user.id, None)

@bot.message_handler(func=lambda m: m.from_user.id in registration_states)
@admin_only
def handle_captcha_reply(message):
    """Обработка капчи для регистрации одного аккаунта"""
    st = registration_states.get(message.from_user.id)
    if not st:
        return
    
    code = message.text.strip()
    if not code.isdigit() or len(code) != 4:
        bot.send_message(ADMIN_ID, "Капча — 4 цифры. Попробуйте снова.")
        return
    
    phone = st["phone"]
    password = st["password"]
    api = st["api"]
    st["attempts"] += 1
    
    bot.send_message(ADMIN_ID, f"Регистрация: пытаюсь зарегистрировать +1{phone}...")
    
    ok, resp = api.register_account(phone, code, password)
    
    if ok:
        settings = load_json(SETTINGS_FILE)
        saved = save_account(phone, password, proxy=st.get("proxy",""), wallet=settings.get("wallet",""))
        if saved:
            token = resp.get("data", {}).get("token", "")
            if token:
                update_account_token(phone, token)
                bot.send_message(
                    ADMIN_ID,
                    f"✅ Зарегистрирован: +1{phone}\n"
                    f"Пароль: {password}\n"
                    f"Прокси: {mask_secret(st.get('proxy', 'Нет'))}\n"
                    "Токен получен и сохранен!",
                )
            else:
                bot.send_message(
                    ADMIN_ID,
                    f"⚠️ Зарегистрирован: +1{phone}\n"
                    "Но токен не получен!",
                )
        else:
            bot.send_message(ADMIN_ID, "❌ Аккаунт уже существует.")
        
        registration_states.pop(message.from_user.id, None)
        return
    else:
        err = str(resp)
        if st["attempts"] < 3:
            new_proxy = None
            proxies = get_proxies()
            if proxies:
                new_proxy = random.choice(proxies)
                # Пробуем установить новый прокси
                try:
                    api.set_proxy_for_account(new_proxy, phone, password, "+1")
                    st["proxy"] = new_proxy
                except:
                    pass
            new_captcha = api.get_captcha()
            if new_captcha:
                bot.send_photo(
                    ADMIN_ID,
                    new_captcha,
                    caption=(
                        f"Ошибка регистрации: {err[:100]}\n"
                        f"Новая капча (попытка {st['attempts']}):"
                    ),
                )
                return
        
        bot.send_message(ADMIN_ID, f"❌ Регистрация не удалась: {err[:100]}")
        registration_states.pop(message.from_user.id, None)

@bot.message_handler(func=lambda m: m.from_user.id in mass_registration_state and m.text and m.text.isdigit() and len(m.text) == 4)
@admin_only
def handle_mass_registration_captcha(message):
    """Обработка капчи для массовой регистрации"""
    state = mass_registration_state.get(message.from_user.id)
    if not state:
        return
    
    code = message.text.strip()
    current = state["current"]
    phone = current["phone"]
    password = current["password"]
    api = current["api"]
    current["attempts"] += 1
    
    account_num = state["completed"] + state["failed"] + 1
    
    bot.send_message(ADMIN_ID, f"📦 Регистрация аккаунта {account_num} из {state['total']}...")
    
    ok, resp = api.register_account(phone, code, password)
    
    if ok:
        settings = load_json(SETTINGS_FILE)
        saved = save_account(phone, password, proxy=current.get("proxy",""), wallet=settings.get("wallet",""))
        
        if saved:
            token = resp.get("data", {}).get("token", "")
            if token:
                update_account_token(phone, token)
            
            state["completed"] += 1
            
            bot.send_message(
                ADMIN_ID,
                f"✅ Аккаунт {account_num} зарегистрирован:\n"
                f"+1{phone}\n"
                f"Успешно: {state['completed']} | Ошибок: {state['failed']}",
            )
        
        else:
            state["failed"] += 1
            bot.send_message(
                ADMIN_ID,
                f"⚠️ Аккаунт {account_num} уже существует\n"
                f"Успешно: {state['completed']} | Ошибок: {state['failed']}",
            )
    
    else:
        err = str(resp)
        
        if current["attempts"] < 3:
            new_proxy = None
            proxies = get_proxies()
            if proxies:
                new_proxy = random.choice(proxies)
                try:
                    api.set_proxy_for_account(new_proxy, phone, password, "+1")
                    current["proxy"] = new_proxy
                except:
                    pass
            
            new_captcha = api.get_captcha()
            if new_captcha:
                bot.send_photo(
                    ADMIN_ID,
                    new_captcha,
                    caption=(
                        f"❌ Ошибка: {err[:100]}\n"
                        f"Повторная попытка {current['attempts']}..."
                    ),
                )
                return
        
        state["failed"] += 1
        bot.send_message(
            ADMIN_ID,
            f"❌ Аккаунт {account_num} не зарегистрирован\n"
            f"Ошибка: {err[:100]}\n"
            f"Успешно: {state['completed']} | Ошибок: {state['failed']}",
        )
    
    total_processed = state["completed"] + state["failed"]
    
    if total_processed >= state["total"]:
        bot.send_message(
            ADMIN_ID,
            "📊 МАССОВАЯ РЕГИСТРАЦИЯ ЗАВЕРШЕНА\n"
            f"Всего: {state['total']}\n"
            f"✅ Успешно: {state['completed']}\n"
            f"❌ Ошибок: {state['failed']}",
        )
        
        mass_registration_state.pop(message.from_user.id, None)
        return
    
    proxies = get_proxies()
    proxy = random.choice(proxies) if proxies else None
    
    settings = load_json(SETTINGS_FILE)
    if settings.get("use_proxies", True) and not proxy:
        bot.send_message(ADMIN_ID, "❌ Нет доступных прокси. Завершаем массовую регистрацию.")
        mass_registration_state.pop(message.from_user.id, None)
        return
    
    next_api = PartTimeAPI(proxy=proxy)
    next_phone = str(random.randint(1000000000, 1999999999))
    next_password = generate_random_password()
    
    captcha = next_api.get_captcha()
    if not captcha:
        bot.send_message(ADMIN_ID, "❌ Не удалось получить капчу. Завершаем массовую регистрацию.")
        mass_registration_state.pop(message.from_user.id, None)
        return
    
    state["current"] = {
        "phone": next_phone,
        "password": next_password,
        "api": next_api,
        "proxy": proxy,
        "attempts": 0
    }
    
    next_account_num = total_processed + 1
    
    bot.send_photo(
        ADMIN_ID,
        captcha,
        caption=(
            "📦 МАССОВАЯ РЕГИСТРАЦИЯ\n"
            f"Аккаунт {next_account_num} из {state['total']}\n"
            f"Номер: +1{next_phone}\n"
            f"Пароль: {next_password}\n"
            f"Прокси: {mask_secret(proxy) if proxy else 'Нет'}\n"
            f"Успешно: {state['completed']} | Ошибок: {state['failed']}\n"
            "Введите 4 цифры капчи:"
        ),
    )

@bot.message_handler(func=lambda m: m.text == "🧪 Тест v2")
@admin_only
def test_api_v2(message):
    """Тестирование новой версии API"""
    accounts = get_active_accounts()
    if not accounts:
        bot.send_message(ADMIN_ID, "Нет активных аккаунтов")
        return
    
    # Выбираем случайный аккаунт
    acc = random.choice(accounts)
    phone = acc["phone"]
    password = acc.get("password", phone)
    area_code = acc.get("area_code", "+1")
    saved_token = acc.get("token", "")
    proxy = acc.get("proxy", "")
    cookies = acc.get("cookies", {})
    
    bot.send_message(ADMIN_ID, f"🧪 Тестируем API v2 для аккаунта {phone}...")
    
    def worker():
        try:
            api = PartTimeAPI(
                phone=phone, 
                password=password, 
                area_code=area_code, 
                token=saved_token,
                proxy=proxy
            )
            
            # Восстанавливаем cookies если есть
            if cookies:
                for cookie_name, cookie_value in cookies.items():
                    api.session.cookies.set(cookie_name, cookie_value)
            
            # Пробуем получить задания через новую версию API
            tasks, error = api.get_all_tasks_v2()
            
            if error:
                bot.send_message(ADMIN_ID, f"❌ Ошибка получения заданий v2: {error}")
                return
            
            bot.send_message(ADMIN_ID, f"✅ Получено {len(tasks)} заданий через v2 API")
            
            # Если есть задания, пробуем взять одно
            if tasks:
                task = tasks[0]
                task_info = api.extract_task_info_v2(task)
                
                if task_info:
                    bot.send_message(
                        ADMIN_ID,
                        "📝 Первое задание:\n"
                        f"Название: {task_info['title']}\n"
                        f"Награда: ${task_info['reward']}\n"
                        f"Batch ID: {task_info['batch_id']}",
                    )
                    
                    # Пробуем взять задание
                    if task_info.get("batch_id"):
                        success = api.apply_task_v2(task_info["batch_id"])
                        
                        if success:
                            bot.send_message(ADMIN_ID, f"✅ Задание успешно взято через v2 API!")
                        else:
                            bot.send_message(ADMIN_ID, f"❌ Не удалось взять задание через v2 API")
                    else:
                        bot.send_message(ADMIN_ID, f"⚠️ Нет batch_id в задании")
                else:
                    bot.send_message(ADMIN_ID, f"⚠️ Не удалось извлечь информацию о задании")
            else:
                bot.send_message(ADMIN_ID, f"📭 Нет доступных заданий через v2 API")
            
        except Exception as e:
            bot.send_message(ADMIN_ID, f"🔥 Ошибка теста v2 API: {str(e)}")
            log_message("ERROR", f"Ошибка теста v2 API: {e}", "test")
    
    threading.Thread(target=worker, daemon=True).start()

@bot.message_handler(func=lambda m: m.text == "🔍 Проверить задания")
@admin_only
def check_tasks(message):
    """Проверка заданий"""
    accounts = get_active_accounts()
    if not accounts:
        bot.send_message(ADMIN_ID, "Нет активных аккаунтов")
        return
    
    bot.send_message(ADMIN_ID, f"🔍 Проверяю задания для {len(accounts)} аккаунтов...")
    
    def worker():
        results = []
        
        for acc in accounts:
            account_result = {
                "phone": acc["phone"],
                "available_tasks": 0,
                "applied_tasks": 0,
                "has_valid_token": False,
                "details": []
            }
            
            try:
                phone = acc["phone"]
                password = acc.get("password", phone)
                area_code = acc.get("area_code", "+1")
                saved_token = acc.get("token", "")
                proxy = acc.get("proxy", "")
                cookies = acc.get("cookies", {})
                
                api = PartTimeAPI(
                    phone=phone, 
                    password=password, 
                    area_code=area_code, 
                    token=saved_token,
                    proxy=proxy
                )
                
                # Восстанавливаем cookies если есть
                if cookies:
                    for cookie_name, cookie_value in cookies.items():
                        api.session.cookies.set(cookie_name, cookie_value)
                
                account_result["details"].append(f"📱 Аккаунт: {phone}")
                account_result["details"].append(f"🔗 Прокси: {mask_secret(proxy)}")
                account_result["details"].append(f"🍪 Cookies: {len(cookies)}")
                
                if saved_token:
                    account_result["details"].append(f"🔑 Имеется сохраненный токен: {mask_secret(saved_token)}")
                    
                    if api.ensure_token_valid():
                        account_result["has_valid_token"] = True
                        account_result["details"].append(f"✅ Токен валиден")
                        update_account_token(phone, api.token)
                    else:
                        account_result["details"].append(f"❌ Токен невалиден")
                else:
                    account_result["details"].append(f"🔓 Нет сохраненного токена")
                
                if not account_result["has_valid_token"]:
                    success, login_result = api.login(phone, password, area_code)
                    if success:
                        account_result["has_valid_token"] = True
                        account_result["details"].append(f"✅ Логин успешен")
                        update_account_token(phone, api.token)
                    else:
                        account_result["details"].append(f"❌ Логин не удался: {login_result}")
                        results.append(account_result)
                        continue
                
                available_tasks, error1 = api.get_all_tasks()
                if error1 == "token_expired":
                    account_result["details"].append(f"⚠️ Токен устарел")
                    if api.ensure_token_valid(force_login=True):
                        update_account_token(phone, api.token)
                        available_tasks, error1 = api.get_all_tasks()
                
                if available_tasks is not None:
                    account_result["available_tasks"] = len(available_tasks)
                
                applied_tasks, error2 = api.get_applied_tasks()
                if error2 == "token_expired":
                    account_result["details"].append(f"⚠️ Токен устарел")
                    if api.ensure_token_valid(force_login=True):
                        update_account_token(phone, api.token)
                        applied_tasks, error2 = api.get_applied_tasks()
                
                if applied_tasks is not None:
                    account_result["applied_tasks"] = len(applied_tasks)
                
                account_result["details"].append(f"📋 Доступно заданий: {account_result['available_tasks']}")
                account_result["details"].append(f"🎯 Взято заданий: {account_result['applied_tasks']}")
                
                if applied_tasks and len(applied_tasks) > 0:
                    for i, task in enumerate(applied_tasks[:3]):
                        task_info = api.extract_task_info(task)
                        if task_info:
                            title = task_info.get("title", "Без названия")[:30]
                            reward = task_info.get("reward", 0)
                            task_id = task_info.get("task_id", "N/A")
                            batch_id = task_info.get("batch_id", "N/A")
                            status = task_info.get("status", 0)
                            account_result["details"].append(f"   [{i+1}] {title} (${reward}, taskId: {task_id}, batchId: {batch_id}, статус: {status})")
            
            except Exception as e:
                account_result["details"].append(f"🔥 Ошибка: {str(e)[:50]}")
            
            results.append(account_result)
        
        total_available = sum(r["available_tasks"] for r in results)
        total_applied = sum(r["applied_tasks"] for r in results)
        valid_tokens = sum(1 for r in results if r["has_valid_token"])
        
        report = f"📊 ПРОВЕРКА ЗАДАНИЙ\n\n"
        report += f"Аккаунтов проверено: {len(results)}\n"
        report += f"С валидным токеном: {valid_tokens}\n"
        report += f"Всего доступных заданий: {total_available}\n"
        report += f"Всего взятых заданий: {total_applied}\n\n"
        
        for res in results[:10]:
            report += f"📱 {res['phone']}:\n"
            report += f"  Доступно: {res['available_tasks']}, Взято: {res['applied_tasks']}\n"
            for detail in res['details'][-3:]:
                report += f"  {detail}\n"
            report += "\n"
        
        if len(results) > 10:
            report += f"... и еще {len(results) - 10} аккаунтов\n"
        
        bot.send_message(ADMIN_ID, report[:4000])
    
    threading.Thread(target=worker, daemon=True).start()

@bot.message_handler(func=lambda m: m.text == "🔄 ВЗЯТЬ ВСЕ ЗАДАНИЯ")
@admin_only
def take_all_tasks(message):
    """Взять все доступные задания"""
    accounts = get_active_accounts()
    if not accounts:
        bot.send_message(ADMIN_ID, "Нет активных аккаунтов")
        return
    
    bot.send_message(ADMIN_ID, f"🔄 Беру все задания для {len(accounts)} аккаунтов...")
    
    def worker():
        results = []
        settings = load_json(SETTINGS_FILE)
        max_tasks = settings.get("max_tasks_per_account", 10)
        
        for acc in accounts:
            account_result = {
                "phone": acc["phone"],
                "available_tasks": 0,
                "applied_tasks": 0,
                "failed": 0,
                "has_valid_token": False,
                "details": []
            }
            
            try:
                phone = acc["phone"]
                password = acc.get("password", phone)
                area_code = acc.get("area_code", "+1")
                saved_token = acc.get("token", "")
                proxy = acc.get("proxy", "")
                cookies = acc.get("cookies", {})
                
                log_message("INFO", f"Обработка аккаунта {phone}", "tasks")
                account_result["details"].append(f"📱 Обработка аккаунта {phone}")
                account_result["details"].append(f"🔗 Прокси: {mask_secret(proxy)}")
                account_result["details"].append(f"🍪 Cookies: {len(cookies)}")
                
                api = PartTimeAPI(
                    phone=phone, 
                    password=password, 
                    area_code=area_code, 
                    token=saved_token,
                    proxy=proxy
                )
                
                # Восстанавливаем cookies если есть
                if cookies:
                    for cookie_name, cookie_value in cookies.items():
                        api.session.cookies.set(cookie_name, cookie_value)
                
                # Единая логика проверки токена с фолбэком
                token_valid = False
                if saved_token:
                    if api.ensure_token_valid():
                        token_valid = True
                        account_result["has_valid_token"] = True
                        account_result["details"].append(f"✅ Токен валиден")
                        if api.token and len(api.token) > 10:
                            update_account_token(phone, api.token)
                    else:
                        account_result["details"].append(f"❌ Сохраненный токен невалиден, пробуем логин")
                
                if not token_valid:
                    success, login_res = api.login(phone, password, area_code)
                    if success:
                        account_result["has_valid_token"] = True
                        account_result["details"].append(f"✅ Логин успешен")
                        if api.token and len(api.token) > 10:
                            update_account_token(phone, api.token)
                    else:
                        account_result["failed"] += 1
                        account_result["details"].append(f"❌ Не удалось получить валидный токен: {login_res}")
                        results.append(account_result)
                        continue
                
                available_tasks, _ = api.get_all_tasks()
                if not available_tasks:
                    account_result["details"].append(f"📭 Нет доступных заданий")
                    results.append(account_result)
                    continue
                
                account_result["available_tasks"] = len(available_tasks)
                account_result["details"].append(f"📋 Найдено {len(available_tasks)} заданий")
                
                applied_tasks, _ = api.get_applied_tasks()
                applied_batch_ids = {task.get("batchId") for task in (applied_tasks or []) if task.get("batchId")}
                
                tasks_to_take = []
                for task in available_tasks:
                    batch_id = task.get("batchId")
                    if batch_id and batch_id not in applied_batch_ids:
                        tasks_to_take.append(task)
                
                if not tasks_to_take:
                    account_result["details"].append(f"✅ Все задания уже взяты")
                    results.append(account_result)
                    continue
                
                tasks_to_take = tasks_to_take[:max_tasks]
                account_result["details"].append(f"🎯 Буду брать {len(tasks_to_take)} заданий")
                
                success_count = 0
                for task in tasks_to_take:
                    try:
                        task_info = api.extract_task_info(task)
                        if not task_info:
                            continue
                            
                        batch_id = task_info.get("batch_id")
                        task_title = task_info.get("title", "Без названия")[:30]
                        reward = task_info.get("reward", 0)
                        
                        apply_result = api.apply_task(batch_id)
                        
                        if apply_result and apply_result.get("code") == "token_expired":
                            if api.ensure_token_valid(force_login=True):
                                update_account_token(phone, api.token)
                                apply_result = api.apply_task(batch_id)
                        
                        if apply_result and apply_result.get("code") == 200:
                            success_count += 1
                            account_result["applied_tasks"] += 1
                            account_result["details"].append(f"  ✅ Взято: {task_title} (${reward})")
                            log_message("INFO", f"Задание {batch_id} взято для {phone}", "tasks")
                            
                            # Ждем подтверждения
                            task_id = api.poll_for_applied_task(batch_id)
                            if task_id:
                                accounts_data = get_accounts()
                                if phone in accounts_data:
                                    if "task_mapping" not in accounts_data[phone]:
                                        accounts_data[phone]["task_mapping"] = {}
                                    accounts_data[phone]["task_mapping"][batch_id] = task_id
                                    save_json(ACCOUNTS_FILE, accounts_data)
                        else:
                            account_result["failed"] += 1
                            error_msg = apply_result.get('message', 'unknown') if apply_result else 'unknown'
                            account_result["details"].append(f"  ❌ Ошибка: {task_title} - {error_msg}")
                        
                        time.sleep(settings.get("delay_between_tasks", 3))
                        
                    except Exception as e:
                        account_result["failed"] += 1
                        account_result["details"].append(f"  ❌ Исключение: {str(e)[:30]}")
                        log_message("ERROR", f"Ошибка взятия задания: {e}", "tasks")
                
                if success_count > 0:
                    account_result["details"].append(f"✅ Успешно взято: {success_count} заданий")
                
            except Exception as e:
                account_result["failed"] += 1
                account_result["details"].append(f"🔥 Общая ошибка: {str(e)[:50]}")
                log_message("ERROR", f"Ошибка обработки аккаунта: {e}", "tasks")
            
            results.append(account_result)
        
        total_applied = sum(r["applied_tasks"] for r in results)
        total_available = sum(r["available_tasks"] for r in results)
        valid_tokens = sum(1 for r in results if r["has_valid_token"])
        
        report = f"📊 ОТЧЕТ ПО ВЗЯТИЮ ЗАДАНИЙ\n\n"
        report += f"Аккаунтов обработано: {len(results)}\n"
        report += f"С валидным токеном: {valid_tokens}\n"
        report += f"Всего доступных заданий: {total_available}\n"
        report += f"Успешно взято: {total_applied}\n\n"
        
        for res in results[:5]:
            report += f"📱 {res['phone']}:\n"
            report += f"  Доступно: {res['available_tasks']}, Взято: {res['applied_tasks']}, Ошибок: {res['failed']}\n"
            for detail in res['details'][-3:]:
                report += f"  {detail}\n"
            report += "\n"
        
        bot.send_message(ADMIN_ID, report[:4000])
    
    threading.Thread(target=worker, daemon=True).start()

@bot.message_handler(func=lambda m: m.text == "🚀 ВЫПОЛНИТЬ ЗАДАНИЯ")
@admin_only
def complete_all_tasks(message):
    """Выполнить все взятые задания с правильным форматом"""
    accounts = get_active_accounts()
    if not accounts:
        bot.send_message(ADMIN_ID, "Нет активных аккаунтов")
        return
    
    # Собираем все доступные изображения
    images = []
    script_dir = Path(__file__).parent.absolute()
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
    
    for search_dir in [script_dir / SCREENSHOTS_DIR, script_dir]:
        if search_dir.exists():
            for file in os.listdir(search_dir):
                file_path = search_dir / file
                if file_path.is_file() and file_path.suffix.lower() in image_extensions:
                    images.append(str(file_path))
    
    if not images:
        bot.send_message(ADMIN_ID, "⚠️ Нет изображений! Добавьте PNG/JPG файлы в папку screenshots/ или корневую папку")
        return
    
    bot.send_message(ADMIN_ID, f"🚀 Выполняю задания для {len(accounts)} аккаунтов...")
    bot.send_message(ADMIN_ID, f"📁 Найдено {len(images)} изображений")
    bot.send_message(ADMIN_ID, f"⚙️ Используется лимит параллельных загрузок: {MAX_CONCURRENT_UPLOADS}")
    
    def worker():
        results = []
        settings = load_json(SETTINGS_FILE)
        
        for acc in accounts:
            account_result = {
                "phone": acc["phone"],
                "applied_tasks": 0,
                "completed_tasks": 0,
                "failed": 0,
                "has_valid_token": False,
                "details": []
            }
            
            try:
                phone = acc["phone"]
                password = acc.get("password", phone)
                area_code = acc.get("area_code", "+1")
                saved_token = acc.get("token", "")
                proxy = acc.get("proxy", "")
                cookies = acc.get("cookies", {})
                
                log_message("INFO", f"Выполнение заданий для аккаунта {phone}", "tasks")
                account_result["details"].append(f"🚀 Выполнение заданий для {phone}")
                account_result["details"].append(f"🔗 Прокси: {mask_secret(proxy)}")
                account_result["details"].append(f"🍪 Cookies: {len(cookies)}")
                
                api = PartTimeAPI(
                    phone=phone, 
                    password=password, 
                    area_code=area_code, 
                    token=saved_token,
                    proxy=proxy
                )
                
                # Восстанавливаем cookies если есть
                if cookies:
                    for cookie_name, cookie_value in cookies.items():
                        api.session.cookies.set(cookie_name, cookie_value)
                
                # Единая логика проверки токена с фолбэком
                token_valid = False
                if saved_token:
                    if api.ensure_token_valid():
                        token_valid = True
                        account_result["has_valid_token"] = True
                        account_result["details"].append(f"✅ Токен валиден")
                        if api.token and len(api.token) > 10:
                            update_account_token(phone, api.token)
                    else:
                        account_result["details"].append(f"❌ Сохраненный токен невалиден, пробуем логин")
                
                if not token_valid:
                    success, login_res = api.login(phone, password, area_code)
                    if success:
                        account_result["has_valid_token"] = True
                        account_result["details"].append(f"✅ Логин успешен")
                        if api.token and len(api.token) > 10:
                            update_account_token(phone, api.token)
                    else:
                        account_result["failed"] += 1
                        account_result["details"].append(f"❌ Не удалось получить валидный токен: {login_res}")
                        results.append(account_result)
                        continue
                
                applied_tasks, _ = api.get_applied_tasks()
                if not applied_tasks:
                    account_result["details"].append(f"📭 Нет взятых заданий")
                    results.append(account_result)
                    continue
                
                account_result["applied_tasks"] = len(applied_tasks)
                account_result["details"].append(f"📋 Найдено {len(applied_tasks)} взятых заданий")
                
                # Обрабатываем первые несколько заданий
                tasks_to_complete = applied_tasks[:min(3, len(applied_tasks))]
                success_count = 0
                
                for task in tasks_to_complete:
                    try:
                        task_info = api.extract_task_info(task)
                        if not task_info:
                            account_result["failed"] += 1
                            account_result["details"].append(f"  ❌ Не удалось извлечь информацию о задании")
                            continue
                        
                        task_id = task_info.get("task_id")
                        task_title = task_info.get("title", "Без названия")
                        reward = task_info.get("reward", 0)
                        
                        if not task_id:
                            account_result["failed"] += 1
                            account_result["details"].append(f"  ❌ Нет taskId для задания: {task_title}")
                            continue
                        
                        account_result["details"].append(f"  📝 Задание: {task_title[:30]} (${reward}, taskId: {task_id})")
                        log_message("INFO", f"Обработка задания {task_id}: {task_title}", "tasks")
                        
                        # Выбираем случайные изображения для загрузки
                        images_to_use = min(2, len(images))
                        selected_images = random.sample(images, images_to_use)
                        
                        # Используем новую функцию для загрузки изображений и отправки задания
                        submit_result = api.upload_images_and_submit(
                            acc, task_id, selected_images, task_title
                        )
                        
                        if submit_result and isinstance(submit_result, dict):
                            if submit_result.get("code") == 200:
                                success_count += 1
                                account_result["completed_tasks"] += 1
                                account_result["details"].append(f"    🎉 Задание отправлено на проверку!")
                                log_message("INFO", f"Задание {task_id} выполнено для {phone}", "tasks")
                            else:
                                account_result["failed"] += 1
                                error_msg = submit_result.get('message', 'unknown')
                                account_result["details"].append(f"    ❌ Ошибка отправки: {error_msg}")
                                log_message("ERROR", f"Ошибка отправки задания {task_id}: {error_msg}", "tasks")
                        else:
                            account_result["failed"] += 1
                            account_result["details"].append(f"    ❌ Не удалось отправить задание")
                            log_message("ERROR", f"Не удалось отправить задание {task_id}", "tasks")
                        
                        # Задержка между заданиями
                        time.sleep(settings.get("delay_between_tasks", 3))
                        
                    except Exception as e:
                        account_result["failed"] += 1
                        account_result["details"].append(f"  ❌ Исключение: {str(e)[:50]}")
                        log_message("ERROR", f"Ошибка выполнения задания: {e}", "tasks")
                
                if success_count > 0:
                    account_result["details"].append(f"✅ Успешно выполнено: {success_count} заданий")
                
            except Exception as e:
                account_result["failed"] += 1
                account_result["details"].append(f"🔥 Общая ошибка: {str(e)[:50]}")
                log_message("ERROR", f"Ошибка обработки аккаунта: {e}", "tasks")
            
            results.append(account_result)
        
        total_completed = sum(r["completed_tasks"] for r in results)
        total_applied = sum(r["applied_tasks"] for r in results)
        valid_tokens = sum(1 for r in results if r["has_valid_token"])
        
        report = f"📊 ОТЧЕТ ПО ВЫПОЛНЕНИЮ ЗАДАНИЙ\n\n"
        report += f"Аккаунтов обработано: {len(results)}\n"
        report += f"С валидным токеном: {valid_tokens}\n"
        report += f"Всего взятых заданий: {total_applied}\n"
        report += f"Успешно выполнено: {total_completed}\n"
        report += f"Использовано изображений: {len(images)}\n"
        report += f"Лимит параллельных загрузок: {MAX_CONCURRENT_UPLOADS}\n\n"
        
        for res in results[:5]:
            report += f"📱 {res['phone']}:\n"
            report += f"  Взято: {res['applied_tasks']}, Выполнено: {res['completed_tasks']}, Ошибок: {res['failed']}\n"
            for detail in res['details'][-3:]:
                report += f"  {detail}\n"
            report += "\n"
        
        if len(results) > 5:
            report += f"... и еще {len(results) - 5} аккаунтов\n"
        
        # Информация о логах
        report += f"\n📁 Логи сохранены в папке: {DEBUG_DIR}/"
        report += f"\n📊 Файлы загрузок: upload_*"
        report += f"\n📊 Файлы отправки: submit_*"
        report += f"\n📊 Ошибки 9000/9900: submit_errors/"
        
        bot.send_message(ADMIN_ID, report[:4000])
    
    threading.Thread(target=worker, daemon=True).start()

@bot.message_handler(func=lambda m: m.text == "🔃 Авто-взятие")
@admin_only
def start_autotake(message):
    """Запуск авто-взятия заданий для всех активных аккаунтов"""
    accounts = get_active_accounts()
    if not accounts:
        bot.send_message(ADMIN_ID, "Нет активных аккаунтов")
        return
    
    bot.send_message(ADMIN_ID, f"🔃 Запускаю авто-взятие заданий для {len(accounts)} аккаунтов...")
    
    started_count = 0
    for acc in accounts:
        phone = acc["phone"]
        
        # Проверяем, не запущен ли уже поток для этого аккаунта
        if phone in autotake_threads:
            bot.send_message(ADMIN_ID, f"⚠️ Авто-взятие уже запущено для {phone}")
            continue
        
        # Включаем авто-взятие в настройках аккаунта
        accounts_data = get_accounts()
        if phone in accounts_data:
            accounts_data[phone]["auto_take"] = True
            save_json(ACCOUNTS_FILE, accounts_data)
        
        # Создаем API клиент и запускаем поток
        api = PartTimeAPI(
            phone=phone,
            password=acc.get("password", phone),
            area_code=acc.get("area_code", "+1"),
            token=acc.get("token", ""),
            proxy=acc.get("proxy", "")
        )
        
        # Восстанавливаем cookies если есть
        cookies = acc.get("cookies", {})
        if cookies:
            for cookie_name, cookie_value in cookies.items():
                api.session.cookies.set(cookie_name, cookie_value)
        
        # Запускаем поток авто-взятия
        thread = api.start_autotake_for_account(acc)
        autotake_threads[phone] = thread
        started_count += 1
        
        log_message("INFO", f"Запущено авто-взятие для {phone}", "autotake")
    
    # Обновляем настройки
    settings = load_json(SETTINGS_FILE)
    settings["auto_take_enabled"] = True
    save_json(SETTINGS_FILE, settings)
    
    bot.send_message(
        ADMIN_ID,
        f"✅ Авто-взятие запущено для {started_count} аккаунтов\n"
        f"Интервал опроса: {POLL_INTERVAL} сек\n"
        f"Лимит заданий за цикл: {MAX_TASKS_PER_RUN}",
    )

@bot.message_handler(func=lambda m: m.text == "⏸ Остановить авто-взятие")
@admin_only
def stop_autotake(message):
    """Остановка авто-взятия заданий"""
    accounts = get_accounts()
    stopped_count = 0
    
    for phone in list(autotake_threads.keys()):
        # Выключаем авто-взятие в настройках аккаунта
        if phone in accounts:
            accounts[phone]["auto_take"] = False
            stopped_count += 1
    
    # Сохраняем изменения
    save_json(ACCOUNTS_FILE, accounts)
    
    # Очищаем словарь потоков
    autotake_threads.clear()
    
    # Обновляем настройки
    settings = load_json(SETTINGS_FILE)
    settings["auto_take_enabled"] = False
    save_json(SETTINGS_FILE, settings)
    
    bot.send_message(ADMIN_ID, f"⏸ Остановлено авто-взятие для {stopped_count} аккаунтов")

@bot.message_handler(func=lambda m: m.text == "🧹 Очистить аккаунты")
@admin_only
def clear_accounts_with_confirmation(message):
    """Очистка аккаунтов с подтверждением"""
    accounts = get_accounts()
    if not accounts:
        bot.send_message(ADMIN_ID, "📭 Нет аккаунтов для очистки")
        return
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ ДА, очистить все", callback_data="clear_accounts_yes"),
        types.InlineKeyboardButton("❌ НЕТ, отмена", callback_data="clear_accounts_no")
    )
    
    bot.send_message(
        ADMIN_ID,
        f"⚠️ ВНИМАНИЕ! Вы собираетесь удалить ВСЕ аккаунты ({len(accounts)} шт.)\n\n"
        f"Это действие НЕОБРАТИМО!\n\n"
        f"Вы уверены?",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("clear_accounts_"))
def handle_clear_accounts_confirmation(call):
    if call.data == "clear_accounts_yes":
        accounts = get_accounts()
        count = len(accounts)
        
        save_json(ACCOUNTS_FILE, {})
        
        bot.edit_message_text(
            f"✅ Успешно очищено {count} аккаунтов",
            call.message.chat.id,
            call.message.message_id
        )
        
        log_message("INFO", f"Очищено {count} аккаунтов", "accounts")
        
    elif call.data == "clear_accounts_no":
        bot.edit_message_text(
            "❌ Очистка аккаунтов отменена",
            call.message.chat.id,
            call.message.message_id
        )

@bot.message_handler(func=lambda m: m.text == "🔄 Обновить токены")
@admin_only
def renew_tokens(message):
    """Обновить токены"""
    accounts = get_active_accounts()
    if not accounts:
        bot.send_message(ADMIN_ID, "Нет активных аккаунтов")
        return
    
    bot.send_message(ADMIN_ID, f"🔄 Обновляю токены для {len(accounts)} аккаунтов...")
    
    def worker():
        results = []
        
        for acc in accounts:
            phone = acc["phone"]
            password = acc.get("password", phone)
            area_code = acc.get("area_code", "+1")
            proxy = acc.get("proxy", "")
            cookies = acc.get("cookies", {})
            
            try:
                api = PartTimeAPI(
                    phone=phone, 
                    password=password, 
                    area_code=area_code, 
                    token="",
                    proxy=proxy
                )
                
                # Восстанавливаем cookies если есть
                if cookies:
                    for cookie_name, cookie_value in cookies.items():
                        api.session.cookies.set(cookie_name, cookie_value)
                
                success, result = api.login(phone, password, area_code)
                
                if success:
                    update_account_token(phone, api.token)
                    
                    # Обновляем cookies
                    cookies_dict = requests.utils.dict_from_cookiejar(api.session.cookies)
                    if cookies_dict:
                        update_account_cookies(phone, cookies_dict)
                    
                    results.append(f"✅ {phone}: Токен и cookies обновлены")
                    log_message("INFO", f"Токен и cookies обновлены для {phone}", "tokens")
                else:
                    results.append(f"❌ {phone}: Ошибка - {result}")
                    log_message("ERROR", f"Ошибка обновления токена для {phone}: {result}", "tokens")
                
                time.sleep(2)
                
            except Exception as e:
                results.append(f"🔥 {phone}: Исключение - {str(e)[:50]}")
                log_message("ERROR", f"Исключение при обновлении токена для {phone}: {e}", "tokens")
        
        success_count = sum(1 for r in results if r.startswith("✅"))
        fail_count = sum(1 for r in results if r.startswith("❌") or r.startswith("🔥"))
        
        report = f"📊 ОТЧЕТ ПО ОБНОВЛЕНИЮ ТОКЕНОВ\n\n"
        report += f"Аккаунтов обработано: {len(results)}\n"
        report += f"Успешно: {success_count}\n"
        report += f"Ошибок: {fail_count}\n\n"
        
        report += "\n".join(results[:20])
        
        if len(results) > 20:
            report += f"\n... и еще {len(results) - 20} аккаунтов"
        
        bot.send_message(ADMIN_ID, report[:4000])
    
    threading.Thread(target=worker, daemon=True).start()

@bot.message_handler(func=lambda m: m.text == "🔐 Вход через API")
@admin_only
def login_via_api(message):
    """Вход через API"""
    accounts = get_accounts()
    if not accounts:
        bot.send_message(ADMIN_ID, "📭 Нет аккаунтов")
        return
    
    msg = bot.send_message(ADMIN_ID, "Введите номер телефона для входа через API:")
    
    def process_phone_input(msg_input):
        phone = msg_input.text.strip()
        
        if phone not in accounts:
            bot.send_message(ADMIN_ID, f"❌ Аккаунт {phone} не найден")
            return
        
        acc = accounts[phone]
        area_code = acc.get("area_code", "+1")
        proxy = acc.get("proxy", "")
        cookies = acc.get("cookies", {})
        
        bot.send_message(ADMIN_ID, f"🔄 Вход через API для {area_code}{phone}...")
        
        def worker():
            try:
                saved_token = acc.get("token", "")
                api = PartTimeAPI(
                    phone=phone, 
                    password=acc.get("password", phone), 
                    area_code=area_code, 
                    token=saved_token,
                    proxy=proxy
                )
                
                # Восстанавливаем cookies если есть
                if cookies:
                    for cookie_name, cookie_value in cookies.items():
                        api.session.cookies.set(cookie_name, cookie_value)
                
                success, result = api.login(phone, acc.get("password", phone), area_code)
                
                if success:
                    token = result.get("token")
                    update_account_token(phone, token)
                    
                    # Обновляем cookies
                    cookies_dict = requests.utils.dict_from_cookiejar(api.session.cookies)
                    if cookies_dict:
                        update_account_cookies(phone, cookies_dict)
                    
                    applied_tasks, _ = api.get_applied_tasks()
                    
                    bot.send_message(ADMIN_ID, 
                        f"✅ Успешный вход через API для {area_code}{phone}\n"
                        f"Прокси: {mask_secret(proxy)}\n"
                        f"Токен: {mask_secret(token)}\n"
                        f"Cookies: {len(cookies_dict)} шт.\n"
                        f"Взятых заданий: {len(applied_tasks) if applied_tasks else 0}\n"
                        f"Ссылка: https://partimetest.51c1e.live/#/pages/task/index"
                    )
                    
                else:
                    bot.send_message(ADMIN_ID, 
                        f"❌ Ошибка входа через API для {area_code}{phone}\n"
                        f"Ошибка: {result}"
                    )
                
            except Exception as e:
                bot.send_message(ADMIN_ID, f"🔥 Исключение при входе через API: {str(e)}")
        
        threading.Thread(target=worker, daemon=True).start()
    
    bot.register_next_step_handler(msg, process_phone_input)

@bot.message_handler(func=lambda m: m.text == "🧪 Тест API")
@admin_only
def test_api(message):
    """Тест API случайного аккаунта"""
    accounts = get_active_accounts()
    if not accounts:
        bot.send_message(ADMIN_ID, "Нет активных аккаунтов")
        return
    
    acc = random.choice(accounts)
    phone = acc["phone"]
    password = acc.get("password", phone)
    area_code = acc.get("area_code", "+1")
    saved_token = acc.get("token", "")
    proxy = acc.get("proxy", "")
    cookies = acc.get("cookies", {})
    
    images = []
    script_dir = Path(__file__).parent.absolute()
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
    
    for search_dir in [script_dir / SCREENSHOTS_DIR, script_dir]:
        if search_dir.exists():
            for file in os.listdir(search_dir):
                file_path = search_dir / file
                if file_path.is_file() and file_path.suffix.lower() in image_extensions:
                    images.append(str(file_path))
    
    if not images:
        bot.send_message(ADMIN_ID, "Сначала добавьте изображения в папку проекта или screenshots/")
        return
    
    bot.send_message(ADMIN_ID, f"🧪 Тестируем API для аккаунта {phone}...")
    
    def worker():
        try:
            api = PartTimeAPI(
                phone=phone, 
                password=password, 
                area_code=area_code, 
                token=saved_token,
                proxy=proxy
            )
            
            # Восстанавливаем cookies если есть
            if cookies:
                for cookie_name, cookie_value in cookies.items():
                    api.session.cookies.set(cookie_name, cookie_value)
            
            # Единая логика проверки токена с фолбэком
            token_valid = False
            if saved_token:
                if api.ensure_token_valid():
                    token_valid = True
                    bot.send_message(ADMIN_ID, f"✅ Токен валиден: {mask_secret(api.token)}")
                    update_account_token(phone, api.token)
                else:
                    bot.send_message(ADMIN_ID, f"❌ Сохраненный токен невалиден, пробуем логин")
            
            if not token_valid:
                success, login_res = api.login(phone, password, area_code)
                if not success:
                    bot.send_message(ADMIN_ID, f"❌ Не удалось получить валидный токен для {phone}: {login_res}")
                    return
                else:
                    bot.send_message(ADMIN_ID, f"✅ Логин успешен, токен получен: {mask_secret(api.token)}")
                    update_account_token(phone, api.token)
            
            applied_tasks, _ = api.get_applied_tasks()
            if not applied_tasks:
                bot.send_message(ADMIN_ID, f"📭 Нет взятых заданий для теста")
                return
            
            # Выбираем первое задание
            task = applied_tasks[0]
            task_info = api.extract_task_info(task)
            if not task_info or not task_info.get("task_id"):
                bot.send_message(ADMIN_ID, f"❌ Не удалось извлечь taskId из задания")
                return
            
            task_id = task_info.get("task_id")
            task_title = task_info.get("title", "Без названия")[:30]
            
            bot.send_message(ADMIN_ID, f"🔍 Тестируем задание: {task_title} (ID: {task_id})")
            
            # Выбираем одно случайное изображение
            img_path = random.choice(images)
            img_file = os.path.basename(img_path)
            
            bot.send_message(ADMIN_ID, f"⬆️ Загружаем изображение {img_file} с повторными попытками...")
            
            # Используем новую функцию загрузки с ретраями
            img_url = api.upload_file_with_retries(img_path)
            
            if not img_url:
                bot.send_message(ADMIN_ID, f"❌ Ошибка загрузки изображения после {UPLOAD_MAX_RETRIES} попыток")
                # Проверяем логи
                log_files = [f for f in os.listdir(DEBUG_DIR) if f.startswith(f"upload_{phone}")]
                if log_files:
                    bot.send_message(ADMIN_ID, f"📁 Проверьте логи в папке {DEBUG_DIR}/upload_failures/")
                return
            
            bot.send_message(ADMIN_ID, f"✅ Изображение загружено: {mask_secret(img_url)}")
            
            social_url = get_social_url(task_title)
            bot.send_message(ADMIN_ID, f"🔗 Генерируем ссылку: {social_url}")
            
            bot.send_message(ADMIN_ID, f"📤 Отправляем задание {task_id}...")
            
            # Используем функцию для загрузки изображений и отправки задания
            submit_result = api.upload_images_and_submit(
                acc, task_id, [img_path], task_title
            )
            
            response_text = f"🧪 РЕЗУЛЬТАТ ТЕСТА API:\n\n"
            response_text += f"Аккаунт: {phone}\n"
            response_text += f"Прокси: {mask_secret(proxy)}\n"
            response_text += f"Cookies: {len(requests.utils.dict_from_cookiejar(api.session.cookies))} шт.\n"
            response_text += f"Задание: {task_title}\n"
            response_text += f"ID задания: {task_id}\n"
            response_text += f"Изображение: {img_file}\n"
            response_text += f"Соцсеть: {social_url}\n"
            
            if submit_result and isinstance(submit_result, dict):
                response_text += f"Код ответа: {submit_result.get('code')}\n"
                response_text += f"Сообщение: {submit_result.get('message')}\n\n"
                
                if submit_result.get("code") == 200:
                    response_text += f"✅ ТЕСТ ПРОЙДЕН УСПЕШНО!\n"
                else:
                    response_text += f"❌ ТЕСТ НЕ ПРОЙДЕН\n"
            else:
                response_text += f"❌ ОТВЕТ НЕ ПОЛУЧЕН\n\n"
            
            response_text += f"\n📁 Логи сохранены в папке: {DEBUG_DIR}/"
            
            bot.send_message(ADMIN_ID, response_text)
            
        except Exception as e:
            bot.send_message(ADMIN_ID, f"🔥 Исключение при тесте API: {str(e)}")
    
    threading.Thread(target=worker, daemon=True).start()

@bot.message_handler(func=lambda m: m.text == "📋 Аккаунты")
@admin_only
def show_accounts(message):
    accounts = get_accounts()
    if not accounts:
        bot.send_message(ADMIN_ID, "📭 Нет аккаунтов")
        return
    
    out = f"📋 Аккаунты ({len(accounts)}):\n\n"
    for phone, acc in list(accounts.items())[:15]:
        status = "✅" if acc.get("status") == "active" else "⛔"
        has_token = "🔑" if acc.get("token") else "🔓"
        has_proxy = "🔗" if acc.get("proxy") else "🌐"
        has_cookies = "🍪" if acc.get("cookies") else "❌"
        password = acc.get("password", "не указан")
        tasks = acc.get("tasks_completed", 0)
        proxy = mask_secret(acc.get("proxy", "нет"))
        auto_take = "🔄" if acc.get("auto_take") else "⏸"
        task_mapping = len(acc.get("task_mapping", {}))
        cookies_count = len(acc.get("cookies", {}))
        
        out += f"{status}{has_token}{has_proxy}{has_cookies}{auto_take} {phone}\n"
        out += f"  Пароль: {password} | Выполнено: {tasks} | Маппингов: {task_mapping} | Cookies: {cookies_count}\n"
        out += f"  Прокси: {proxy}\n\n"
    
    active = len([a for a in accounts.values() if a.get("status") == "active"])
    has_token_count = len([a for a in accounts.values() if a.get("token")])
    has_proxy_count = len([a for a in accounts.values() if a.get("proxy")])
    has_cookies_count = len([a for a in accounts.values() if a.get("cookies")])
    total_tasks = sum(a.get("tasks_completed", 0) for a in accounts.values())
    auto_take_count = len([a for a in accounts.values() if a.get("auto_take")])
    
    out += f"\n📊 Статистика:\n"
    out += f"Активных: {active} | С токеном: {has_token_count} | С прокси: {has_proxy_count}\n"
    out += f"С cookies: {has_cookies_count} | Авто-взятие: {auto_take_count} | Всего заданий: {total_tasks}"
    
    out += f"\n\n⚠️ ВНИМАНИЕ: пароли показаны полностью! Не делитесь этим сообщением!"
    
    bot.send_message(ADMIN_ID, out)

@bot.message_handler(func=lambda m: m.text == "📊 Статистика")
@admin_only
def show_stats(message):
    accounts = get_accounts()
    active = len([a for a in accounts.values() if a.get("status") == "active"])
    total_tasks = sum(a.get("tasks_completed", 0) for a in accounts.values())
    has_token = len([a for a in accounts.values() if a.get("token")])
    has_proxy = len([a for a in accounts.values() if a.get("proxy")])
    has_cookies = len([a for a in accounts.values() if a.get("cookies")])
    auto_take = len([a for a in accounts.values() if a.get("auto_take")])
    
    valid_tokens = 0
    for phone, acc in accounts.items():
        if acc.get("token") and len(acc.get("token", "")) > 10:
            valid_tokens += 1
    
    images_count = 0
    script_dir = Path(__file__).parent.absolute()
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
    
    for search_dir in [script_dir / SCREENSHOTS_DIR, script_dir]:
        if search_dir.exists():
            for file in os.listdir(search_dir):
                file_path = search_dir / file
                if file_path.is_file() and file_path.suffix.lower() in image_extensions:
                    images_count += 1
    
    settings = load_json(SETTINGS_FILE)
    use_proxies = settings.get("use_proxies", True)
    
    # Считаем файлы логов
    upload_logs = 0
    submit_logs = 0
    error_logs = 0
    if os.path.exists(DEBUG_DIR):
        for root, dirs, files in os.walk(DEBUG_DIR):
            for file in files:
                if "upload" in root.lower():
                    upload_logs += 1
                elif "submit" in root.lower():
                    submit_logs += 1
                if "error" in root.lower() or "fail" in root.lower():
                    error_logs += 1
    
    bot.send_message(ADMIN_ID, 
        f"📊 Статистика:\n"
        f"Всего аккаунтов: {len(accounts)}\n"
        f"Активных: {active}\n"
        f"С токеном: {has_token}\n"
        f"Валидных токенов: {valid_tokens}\n"
        f"С прокси: {has_proxy}\n"
        f"С cookies: {has_cookies}\n"
        f"Авто-взятие: {auto_take}\n"
        f"Использовать прокси: {'Да' if use_proxies else 'Нет'}\n"
        f"Выполнено заданий: {total_tasks}\n"
        f"Доступных изображений: {images_count}\n"
        f"Потоков авто-взятия: {len(autotake_threads)}\n"
        f"Логов загрузок: {upload_logs}\n"
        f"Логов отправок: {submit_logs}\n"
        f"Логов ошибок: {error_logs}\n"
        f"Лимит параллельных загрузок: {MAX_CONCURRENT_UPLOADS}"
    )

@bot.message_handler(func=lambda m: m.text == "📝 Обновить меню")
@admin_only
def update_menu(message):
    cmd_start(message)



# ========== Запуск бота ==========

if __name__ == "__main__":
    log_message("INFO", "Bot starting with fixed 9000 errors...", "system")
    log_message("INFO", f"Лимит параллельных загрузок: {MAX_CONCURRENT_UPLOADS}", "system")
    log_message("INFO", f"Максимум попыток загрузки: {UPLOAD_MAX_RETRIES}", "system")
    log_message("INFO", f"Таймаут загрузки: {UPLOAD_TIMEOUT} сек", "system")
    log_message("INFO", f"Отладочная информация сохраняется в: {DEBUG_DIR}", "system")
    
    # Проверяем и запускаем авто-взятие для аккаунтов с включенной опцией
    settings = load_json(SETTINGS_FILE)
    if settings.get("auto_take_enabled", False):
        log_message("INFO", "Авто-взятие было включено, запускаем потоки...", "system")
        accounts = get_active_accounts()
        
        for acc in accounts:
            if acc.get("auto_take"):
                phone = acc["phone"]
                api = PartTimeAPI(
                    phone=phone,
                    password=acc.get("password", phone),
                    area_code=acc.get("area_code", "+1"),
                    token=acc.get("token", ""),
                    proxy=acc.get("proxy", "")
                )
                
                # Восстанавливаем cookies если есть
                cookies = acc.get("cookies", {})
                if cookies:
                    for cookie_name, cookie_value in cookies.items():
                        api.session.cookies.set(cookie_name, cookie_value)
                
                thread = api.start_autotake_for_account(acc)
                autotake_threads[phone] = thread
                log_message("INFO", f"Авто-взятие запущено для {phone}", "autotake")
    
    images_count = 0
    script_dir = Path(__file__).parent.absolute()
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
    
    log_message("INFO", "Поиск изображений...", "system")
    for search_dir in [script_dir / SCREENSHOTS_DIR, script_dir]:
        if search_dir.exists():
            for file in os.listdir(search_dir):
                file_path = search_dir / file
                if file_path.is_file() and file_path.suffix.lower() in image_extensions:
                    images_count += 1
                    log_message("INFO", f"Найдено изображение: {file}", "system")
    
    if images_count > 0:
        log_message("INFO", f"Найдено {images_count} изображений для заданий", "system")
    else:
        log_message("WARNING", "Нет изображений! Добавьте PNG/JPG файлы в папку screenshots/ или корневую папку", "system")
    
    accounts = get_active_accounts()
    log_message("INFO", f"Найдено {len(accounts)} активных аккаунтов", "system")
    
    accounts_with_proxies = []
    accounts_with_tokens = []
    accounts_with_cookies = []
    for acc in accounts:
        if acc.get("proxy"):
            accounts_with_proxies.append(acc["phone"])
        if acc.get("token"):
            accounts_with_tokens.append(acc["phone"])
        if acc.get("cookies"):
            accounts_with_cookies.append(acc["phone"])
    
    if accounts_with_proxies:
        log_message("INFO", f"Аккаунты с прокси: {len(accounts_with_proxies)}", "system")
    else:
        log_message("WARNING", "Нет аккаунтов с прокси!", "system")
    
    if accounts_with_tokens:
        log_message("INFO", f"Аккаунты с токенами: {len(accounts_with_tokens)}", "system")
    else:
        log_message("WARNING", "Нет аккаунтов с токенами", "system")
    
    if accounts_with_cookies:
        log_message("INFO", f"Аккаунты с cookies: {len(accounts_with_cookies)}", "system")
    else:
        log_message("WARNING", "Нет аккаунтов с cookies", "system")
    
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        log_message("FATAL", f"Polling error: {e}")
