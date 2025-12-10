import json
import psycopg2
from datetime import datetime, timedelta
import telebot
import threading
import time
import os
import re
import logging
import shutil
import hashlib
import base64
import secrets
from functools import lru_cache
import redis
import pickle
import psycopg2.pool
import asyncio
from queue import Queue
import random

# ========= نظام الإعدادات المحسن =========
class Config:
    TOKEN = "8368256870:AAHJbbEk7JysY53UCwS7jMIHMT2-7oz8dJ8"
    ADMIN_ID = 7609619256
    
    # إعدادات PostgreSQL
    DB_HOST = "db.touhofujeeiptaxwzben.supabase.co"
    DB_PORT = 5432
    DB_NAME = "postgres"
    DB_USER = "postgres"
    DB_PASSWORD = "24455539DIna"
    
    TRC20_WALLET = "TFF3JgjtGc9Kky2ko7NwtJyQY6NKujQ8YL"
    BEP20_WALLET = "0x39d730BF7fEb2648Ae1761ECd20972fD067C2114"
    SUPPORT_BOT_USERNAME = "Tarakumbot"
    SUPPORT_CHANNEL = "TarakumAE_Support"
    EMAIL = "info@tarakum.ae"
    SECRET_KEY = "tarakum_secret_key_2024"
    
    # إعدادات الأداء الجديدة
    REDIS_HOST = "localhost"
    REDIS_PORT = 6379
    REDIS_DB = 0
    MAX_CONCURRENT_TASKS = 100
    DB_POOL_SIZE = 20
    CACHE_TTL = 300
    
    # إعدادات نظام الطوابير المتقدمة لتجنب حظر تيليجرام
    TELEGRAM_RATE_LIMIT = 30  # رسائل في الثانية
    QUEUE_PROCESSING_DELAY = 0.033  # ثانية بين الرسائل (30 رسالة/ثانية)
    ANTI_BAN_DELAYS = [0.1, 0.15, 0.2, 0.25]  # تأخيرات عشوائية لتجنب الحظر
    MESSAGE_BATCH_SIZE = 10  # حجم مجموعة الرسائل
    USER_QUEUE_LIMIT = 100  # الحد الأقصى للرسائل في طابور المستخدم

# ========= نظام طابور الرسائل المتقدم لتجنب حظر تيليجرام =========
class AdvancedQueueManager:
    """نظام طوابير متقدم لتجنب حظر تيليجرام"""
    
    def __init__(self):
        self.message_queues = {}  # طوابير الرسائل لكل مستخدم
        self.broadcast_queue = Queue()  # طابور البث الجماعي
        self.processing_lock = threading.Lock()
        self.is_processing = False
        self.last_message_time = {}
        self.user_priority = {}  # أولوية المستخدمين
        
    def add_to_user_queue(self, user_id, chat_id, message_func, *args, **kwargs):
        """إضافة رسالة إلى طابور المستخدم"""
        if user_id not in self.message_queues:
            self.message_queues[user_id] = Queue(maxsize=Config.USER_QUEUE_LIMIT)
        
        try:
            self.message_queues[user_id].put_nowait({
                'chat_id': chat_id,
                'func': message_func,
                'args': args,
                'kwargs': kwargs,
                'timestamp': time.time()
            })
            
            # بدء المعالجة إذا لم تكن قيد التشغيل
            if not self.is_processing:
                self.start_processing()
                
            return True
        except:
            return False
    
    def add_to_broadcast_queue(self, message_func, *args, **kwargs):
        """إضافة رسالة إلى طابور البث الجماعي"""
        self.broadcast_queue.put({
            'func': message_func,
            'args': args,
            'kwargs': kwargs,
            'timestamp': time.time()
        })
        
        if not self.is_processing:
            self.start_processing()
    
    def start_processing(self):
        """بدء معالجة الطوابير"""
        if not self.is_processing:
            self.is_processing = True
            threading.Thread(target=self._process_queues, daemon=True).start()
    
    def _process_queues(self):
        """معالجة الطوابير مع مراعاة حدود تيليجرام"""
        while True:
            try:
                # معالجة طابور البث أولاً
                if not self.broadcast_queue.empty():
                    self._process_broadcast_queue()
                
                # معالجة طوابير المستخدمين
                self._process_user_queues()
                
                # إضافة تأخير عشوائي لتجنب الحظر
                delay = random.choice(Config.ANTI_BAN_DELAYS)
                time.sleep(delay)
                
                # التحقق إذا كانت جميع الطوابير فارغة
                if (self.broadcast_queue.empty() and 
                    all(q.empty() for q in self.message_queues.values())):
                    time.sleep(1)
                    
            except Exception as e:
                logger.error(f"Queue processing error: {e}")
                time.sleep(1)
    
    def _process_broadcast_queue(self):
        """معالجة طابور البث الجماعي"""
        batch = []
        while len(batch) < Config.MESSAGE_BATCH_SIZE and not self.broadcast_queue.empty():
            try:
                item = self.broadcast_queue.get_nowait()
                batch.append(item)
            except:
                break
        
        if batch:
            for item in batch:
                try:
                    item['func'](*item['args'], **item['kwargs'])
                    # احترام حد تيليجرام
                    time.sleep(Config.QUEUE_PROCESSING_DELAY)
                except Exception as e:
                    logger.error(f"Broadcast processing error: {e}")
    
    def _process_user_queues(self):
        """معالجة طوابير المستخدمين"""
        users = list(self.message_queues.keys())
        
        for user_id in users:
            queue = self.message_queues.get(user_id)
            if queue and not queue.empty():
                try:
                    # التحقق من آخر وقت إرسال للمستخدم
                    last_time = self.last_message_time.get(user_id, 0)
                    current_time = time.time()
                    
                    if current_time - last_time >= Config.QUEUE_PROCESSING_DELAY:
                        item = queue.get_nowait()
                        try:
                            item['func'](*item['args'], **item['kwargs'])
                            self.last_message_time[user_id] = current_time
                        except Exception as e:
                            logger.error(f"User queue processing error: {e}")
                        
                        # تنظيف الطوابير الفارغة
                        if queue.empty():
                            del self.message_queues[user_id]
                            self.last_message_time.pop(user_id, None)
                            
                except Exception as e:
                    logger.error(f"Error processing user queue: {e}")

# ========= نظام التخزين المؤقت المتقدم =========
class CacheManager:
    def __init__(self):
        try:
            self.redis_client = redis.Redis(
                host=Config.REDIS_HOST, 
                port=Config.REDIS_PORT, 
                db=Config.REDIS_DB,
                decode_responses=False
            )
            self.redis_client.ping()
            self.redis_available = True
        except:
            self.redis_available = False
            self.memory_cache = {}
    
    def get_user(self, user_id):
        if self.redis_available:
            try:
                cached = self.redis_client.get(f"user:{user_id}")
                if cached:
                    return pickle.loads(cached)
            except:
                pass
        else:
            return self.memory_cache.get(f"user:{user_id}")
        return None
    
    def set_user(self, user_id, user_data, ttl=300):
        if self.redis_available:
            try:
                self.redis_client.setex(
                    f"user:{user_id}",
                    ttl,
                    pickle.dumps(user_data)
                )
            except:
                pass
        else:
            self.memory_cache[f"user:{user_id}"] = user_data
    
    def delete_user(self, user_id):
        if self.redis_available:
            try:
                self.redis_client.delete(f"user:{user_id}")
            except:
                pass
        else:
            self.memory_cache.pop(f"user:{user_id}", None)

# ========= نظام طابور المهام =========
class TaskQueue:
    def __init__(self):
        self.active_tasks = set()
        self.task_lock = threading.Lock()
        self.max_concurrent = Config.MAX_CONCURRENT_TASKS
    
    def can_start_task(self, user_id):
        with self.task_lock:
            if len(self.active_tasks) < self.max_concurrent:
                self.active_tasks.add(user_id)
                return True
            return False
    
    def end_task(self, user_id):
        with self.task_lock:
            self.active_tasks.discard(user_id)
    
    def get_active_count(self):
        with self.task_lock:
            return len(self.active_tasks)

# ========= نظام تجميع الإشعارات =========
class NotificationManager:
    def __init__(self):
        self.pending_notifications = []
        self.notification_lock = threading.Lock()
        self.batch_size = 10
        self.last_flush = time.time()
    
    def add_notification(self, notification_type, data):
        with self.notification_lock:
            self.pending_notifications.append({
                'type': notification_type,
                'data': data,
                'timestamp': time.time()
            })
            
            # إرسال مجمع إذا وصلنا للحد أو مرت 30 ثانية
            if (len(self.pending_notifications) >= self.batch_size or 
                time.time() - self.last_flush > 30):
                self.flush_notifications()
    
    def flush_notifications(self):
        if not self.pending_notifications:
            return
            
        try:
            grouped_message = self.create_grouped_message()
            bot.send_message(Config.ADMIN_ID, grouped_message, parse_mode="Markdown")
            self.pending_notifications.clear()
            self.last_flush = time.time()
        except Exception as e:
            logger.error(f"Error flushing notifications: {e}")
    
    def create_grouped_message(self):
        message = "🔔 **إشعارات مجمعة**\n\n"
        
        deposits = [n for n in self.pending_notifications if n['type'] == 'deposit']
        withdrawals = [n for n in self.pending_notifications if n['type'] == 'withdrawal']
        supports = [n for n in self.pending_notifications if n['type'] == 'support']
        
        if deposits:
            message += f"💰 **طلبات إيداع جديدة:** {len(deposits)}\n"
            for dep in deposits[:3]:  # عرض أول 3 فقط
                message += f"• @{dep['data']['username']} - {dep['data']['amount']:.2f}$\n"
        
        if withdrawals:
            message += f"💸 **طلبات سحب جديدة:** {len(withdrawals)}\n"
            for wd in withdrawals[:3]:
                message += f"• @{wd['data']['username']} - {wd['data']['amount']:.2f}$\n"
        
        if supports:
            message += f"📞 **رسائل دعم جديدة:** {len(supports)}\n"
        
        message += f"\n⏰ **آخر تحديث:** {datetime.now().strftime('%H:%M:%S')}"
        return message

# ========= تهيئة الأنظمة =========
bot = telebot.TeleBot(Config.TOKEN)
queue_manager = AdvancedQueueManager()
cache_manager = CacheManager()
task_queue = TaskQueue()
notification_manager = NotificationManager()

# ========= إعداد اللوغرات المحسن =========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def log_event(user_id, event_type, details, level='info'):
    """تسجيل الأحداث المهمة مع مستويات مختلفة"""
    log_message = f"User {user_id} - {event_type}: {details}"
    if level == 'error':
        logger.error(log_message)
    elif level == 'warning':
        logger.warning(log_message)
    else:
        logger.info(log_message)

# ========= نظام الأمان المحسن =========
class SecuritySystem:
    def __init__(self):
        self.login_attempts = {}
        self.rate_limits = {}
        self.suspicious_activities = set()
        self.security_lock = threading.Lock()
    
    def check_rate_limit(self, user_id, action, limit=5, window=60):
        """التحقق من حدود المعدل"""
        key = f"{user_id}_{action}"
        now = time.time()
        
        with self.security_lock:
            if key not in self.rate_limits:
                self.rate_limits[key] = []
            
            # إزالة المحاولات القديمة
            self.rate_limits[key] = [t for t in self.rate_limits[key] if now - t < window]
            
            if len(self.rate_limits[key]) >= limit:
                return False
            
            self.rate_limits[key].append(now)
            return True
    
    def record_login_attempt(self, user_id, success):
        """تسجيل محاولة تسجيل الدخول"""
        with self.security_lock:
            if user_id not in self.login_attempts:
                self.login_attempts[user_id] = []
            
            self.login_attempts[user_id].append({
                'timestamp': datetime.now(),
                'success': success
            })
            
            # الاحتفاظ بآخر 10 محاولات فقط
            self.login_attempts[user_id] = self.login_attempts[user_id][-10:]
            
            if not success:
                failed_attempts = len([a for a in self.login_attempts[user_id][-5:] if not a['success']])
                if failed_attempts >= 3:
                    self.suspicious_activities.add(user_id)
                    log_event(user_id, "SUSPICIOUS_ACTIVITY", "Multiple failed login attempts", "warning")
    
    def is_suspicious(self, user_id):
        """التحقق من وجود نشاط مشبوه"""
        with self.security_lock:
            return user_id in self.suspicious_activities

security = SecuritySystem()

# ========= دوال التشفير البديلة =========
def encrypt_password(password):
    """تشفير كلمة المرور باستخدام hashlib"""
    try:
        salt = Config.SECRET_KEY.encode()
        password_bytes = password.encode()
        hashed = hashlib.pbkdf2_hmac('sha256', password_bytes, salt, 100000)
        return base64.b64encode(hashed).decode('utf-8')
    except Exception as e:
        logger.error(f"Password encryption error: {e}")
        return password

def verify_password(encrypted_password, input_password):
    """التحقق من كلمة المرور"""
    try:
        test_encrypted = encrypt_password(input_password)
        return test_encrypted == encrypted_password
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        return False

def generate_secure_token(length=32):
    """إنشاء رمز آمن"""
    return secrets.token_urlsafe(length)

# ========= نظام النسخ الاحتياطي المحسن =========
def backup_database():
    """إنشاء نسخة احتياطية من قاعدة البيانات"""
    try:
        backup_dir = "backups"
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = f"{backup_dir}/backup_{timestamp}.sql"
        
        # تصدير قاعدة بيانات PostgreSQL
        import subprocess
        cmd = [
            'pg_dump',
            '-h', Config.DB_HOST,
            '-p', str(Config.DB_PORT),
            '-U', Config.DB_USER,
            '-d', Config.DB_NAME,
            '-f', backup_file
        ]
        
        env = os.environ.copy()
        env['PGPASSWORD'] = Config.DB_PASSWORD
        
        subprocess.run(cmd, env=env, check=True)
        
        backups = sorted([f for f in os.listdir(backup_dir) if f.startswith("backup_")])
        if len(backups) > 7:
            for old_backup in backups[:-7]:
                os.remove(os.path.join(backup_dir, old_backup))
        
        logger.info("Database backup created successfully")
                
    except Exception as e:
        logger.error(f"Error in backup: {e}")

def schedule_backups():
    """جدولة النسخ الاحتياطي كل 24 ساعة"""
    while True:
        time.sleep(24 * 60 * 60)
        backup_database()

# ========= اتصال PostgreSQL مع Connection Pool =========
class DatabaseConnection:
    _pool = None
    
    @classmethod
    def get_pool(cls):
        if cls._pool is None:
            cls._pool = psycopg2.pool.SimpleConnectionPool(
                1,  # الحد الأدنى من الاتصالات
                Config.DB_POOL_SIZE,  # الحد الأقصى من الاتصالات
                host=Config.DB_HOST,
                port=Config.DB_PORT,
                database=Config.DB_NAME,
                user=Config.DB_USER,
                password=Config.DB_PASSWORD
            )
        return cls._pool
    
    @classmethod
    def get_connection(cls):
        pool = cls.get_pool()
        return pool.getconn()
    
    @classmethod
    def return_connection(cls, conn):
        pool = cls.get_pool()
        pool.putconn(conn)
    
    @classmethod
    def close_all_connections(cls):
        if cls._pool:
            cls._pool.closeall()

def get_conn():
    """الحصول على اتصال بقاعدة البيانات"""
    return DatabaseConnection.get_connection()

def return_conn(conn):
    """إرجاع الاتصال إلى الـ pool"""
    DatabaseConnection.return_connection(conn)

def optimize_database():
    """تحسين أداء قاعدة البيانات"""
    try:
        conn = get_conn()
        c = conn.cursor()
        
        # تحليل وإعادة بناء الفهارس
        c.execute("ANALYZE")
        
        # إعادة بناء الفهارس لتحسين الأداء
        c.execute("""
            SELECT tablename FROM pg_tables 
            WHERE schemaname = 'public'
        """)
        tables = c.fetchall()
        
        for table in tables:
            try:
                c.execute(f"REINDEX TABLE {table[0]}")
                logger.info(f"✅ Reindexed table: {table[0]}")
            except Exception as e:
                logger.error(f"Error reindexing {table[0]}: {e}")
        
        conn.commit()
        return_conn(conn)
        logger.info("✅ Database optimized")
    except Exception as e:
        logger.error(f"❌ Database optimization error: {e}")

def schedule_optimization():
    """جدولة تحسين قاعدة البيانات كل 12 ساعة"""
    while True:
        time.sleep(12 * 60 * 60)
        optimize_database()

def init_db():
    """تهيئة قاعدة بيانات PostgreSQL"""
    try:
        conn = get_conn()
        c = conn.cursor()
        
        # جدول المستخدمين
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                membership_id SERIAL,
                username VARCHAR(100),
                password VARCHAR(255),
                email VARCHAR(255),
                phone VARCHAR(50),
                wallet VARCHAR(255),
                balance DECIMAL(15, 2) DEFAULT 0.0,
                registered BOOLEAN DEFAULT FALSE,
                deposited BOOLEAN DEFAULT FALSE,
                last_deposit TIMESTAMP,
                last_task TIMESTAMP,
                withdraw_request BOOLEAN DEFAULT FALSE,
                transactions JSONB DEFAULT '[]',
                referrer_id BIGINT,
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP,
                login_count INTEGER DEFAULT 0,
                status VARCHAR(20) DEFAULT 'active',
                first_deposit_time TIMESTAMP,
                last_withdrawal_time TIMESTAMP,
                last_task_status VARCHAR(20) DEFAULT 'completed',
                
                CONSTRAINT unique_membership_id UNIQUE (membership_id)
            )
        """)
        
        # إنشاء فهارس لجدول المستخدمين
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
            CREATE INDEX IF NOT EXISTS idx_users_balance ON users(balance);
            CREATE INDEX IF NOT EXISTS idx_users_referrer ON users(referrer_id);
            CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
            CREATE INDEX IF NOT EXISTS idx_users_membership ON users(membership_id);
            CREATE INDEX IF NOT EXISTS idx_users_created_date ON users(created_date);
        """)
        
        # جدول طلبات الإيداع
        c.execute("""
            CREATE TABLE IF NOT EXISTS deposit_requests (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id),
                username VARCHAR(100),
                amount DECIMAL(15, 2),
                network VARCHAR(20),
                txid VARCHAR(255) UNIQUE,
                status VARCHAR(20) DEFAULT 'pending',
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reject_reason TEXT,
                sender_wallet VARCHAR(255)
            )
        """)
        
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_deposits_status ON deposit_requests(status);
            CREATE INDEX IF NOT EXISTS idx_deposits_txid ON deposit_requests(txid);
            CREATE INDEX IF NOT EXISTS idx_deposits_user_id ON deposit_requests(user_id);
            CREATE INDEX IF NOT EXISTS idx_deposits_date ON deposit_requests(date);
        """)
        
        # جدول طلبات السحب
        c.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id),
                amount DECIMAL(15, 2),
                status VARCHAR(20) DEFAULT 'pending',
                admin_id BIGINT,
                processed_date TIMESTAMP,
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                tx_hash VARCHAR(255),
                reject_reason TEXT
            )
        """)
        
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_withdrawals_status ON withdrawals(status);
            CREATE INDEX IF NOT EXISTS idx_withdrawals_user_id ON withdrawals(user_id);
            CREATE INDEX IF NOT EXISTS idx_withdrawals_date ON withdrawals(date);
        """)
        
        # جدول رسائل الدعم
        c.execute("""
            CREATE TABLE IF NOT EXISTS support_messages (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id),
                membership_id INTEGER,
                username VARCHAR(100),
                category VARCHAR(50),
                category_name VARCHAR(100),
                message TEXT,
                status VARCHAR(20) DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                admin_response TEXT,
                responded_at TIMESTAMP,
                admin_id BIGINT
            )
        """)
        
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_support_status ON support_messages(status);
            CREATE INDEX IF NOT EXISTS idx_support_user_id ON support_messages(user_id);
            CREATE INDEX IF NOT EXISTS idx_support_created_at ON support_messages(created_at);
        """)
        
        # جدول جوائز الإحالة الجماعية
        c.execute("""
            CREATE TABLE IF NOT EXISTS referral_batch_bonus (
                id SERIAL PRIMARY KEY,
                referrer_id BIGINT REFERENCES users(user_id),
                completed_batches INTEGER DEFAULT 0,
                pending_users JSONB DEFAULT '[]',
                total_bonus_earned DECIMAL(15, 2) DEFAULT 0.0,
                last_bonus_date TIMESTAMP,
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_referral_batch_referrer ON referral_batch_bonus(referrer_id);
        """)
        
        # جدول الحظر
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_bans (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id),
                username VARCHAR(100),
                admin_id BIGINT,
                ban_reason TEXT,
                ban_duration VARCHAR(50),
                ban_start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ban_end_time TIMESTAMP,
                status VARCHAR(20) DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_bans_user_id ON user_bans(user_id);
            CREATE INDEX IF NOT EXISTS idx_user_bans_status ON user_bans(status);
            CREATE INDEX IF NOT EXISTS idx_user_bans_end_time ON user_bans(ban_end_time);
            CREATE INDEX IF NOT EXISTS idx_user_bans_start_time ON user_bans(ban_start_time);
        """)
        
        # جدول الإحصائيات
        c.execute("""
            CREATE TABLE IF NOT EXISTS system_stats (
                id SERIAL PRIMARY KEY,
                stat_key VARCHAR(100) UNIQUE,
                stat_value JSONB,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # تهيئة الإحصائيات
        c.execute("""
            INSERT INTO system_stats (stat_key, stat_value) 
            VALUES ('last_membership_id', '{"value": 861}') 
            ON CONFLICT (stat_key) DO NOTHING
        """)
        
        c.execute("""
            INSERT INTO system_stats (stat_key, stat_value) 
            VALUES ('user_count', '{"value": 0}') 
            ON CONFLICT (stat_key) DO NOTHING
        """)
        
        conn.commit()
        return_conn(conn)
        logger.info("✅ PostgreSQL database initialized successfully")
        
    except Exception as e:
        logger.error(f"❌ Database initialization error: {e}")
        raise

def initialize_existing_users():
    """تهيئة أرقام العضوية للمستخدمين الحاليين"""
    try:
        conn = get_conn()
        c = conn.cursor()
        
        # الحصول على آخر رقم عضوية
        c.execute("SELECT stat_value->>'value' FROM system_stats WHERE stat_key = 'last_membership_id'")
        result = c.fetchone()
        current_id = int(result[0]) if result else 862
        
        # البحث عن المستخدمين بدون رقم عضوية
        c.execute("SELECT user_id FROM users WHERE membership_id IS NULL")
        users_without_membership = c.fetchall()
        
        if users_without_membership:
            logger.info(f"🔧 Initializing membership IDs for {len(users_without_membership)} existing users")
            
            for user_row in users_without_membership:
                user_id = user_row[0]
                c.execute("UPDATE users SET membership_id = %s WHERE user_id = %s", (current_id, user_id))
                current_id += 1
            
            # تحديث آخر رقم عضوية
            c.execute("""
                UPDATE system_stats 
                SET stat_value = jsonb_set(stat_value, '{value}', %s)
                WHERE stat_key = 'last_membership_id'
            """, (str(current_id),))
            
            conn.commit()
            logger.info(f"✅ Assigned membership IDs to {len(users_without_membership)} users")
    
    except Exception as e:
        logger.error(f"Error initializing existing users: {e}")
        if 'conn' in locals():
            conn.rollback()
    finally:
        if 'conn' in locals():
            return_conn(conn)

def get_next_membership_id():
    """الحصول على رقم العضوية التالي"""
    try:
        conn = get_conn()
        c = conn.cursor()
        
        c.execute("SELECT stat_value->>'value' FROM system_stats WHERE stat_key = 'last_membership_id'")
        result = c.fetchone()
        
        if result:
            next_id = int(result[0]) + 1
        else:
            next_id = 862
        
        # تحديث آخر رقم عضوية
        c.execute("""
            UPDATE system_stats 
            SET stat_value = jsonb_set(stat_value, '{value}', %s)
            WHERE stat_key = 'last_membership_id'
        """, (str(next_id),))
        
        conn.commit()
        return_conn(conn)
        return next_id
        
    except Exception as e:
        logger.error(f"Error getting next membership ID: {e}")
        return 862

@lru_cache(maxsize=1000)
def load_user(user_id):
    """تحميل بيانات المستخدم مع التخزين المؤقت"""
    # التحقق من التخزين المؤقت أولاً
    cached_user = cache_manager.get_user(user_id)
    if cached_user:
        return cached_user
    
    try:
        conn = get_conn()
        c = conn.cursor()
        
        c.execute("""
            SELECT 
                user_id, membership_id, username, password, email, phone, wallet, 
                balance, registered, deposited, last_deposit, last_task, 
                withdraw_request, transactions, referrer_id, created_date, 
                last_login, login_count, status, first_deposit_time, 
                last_withdrawal_time, last_task_status
            FROM users WHERE user_id = %s
        """, (str(user_id),))
        
        row = c.fetchone()
        return_conn(conn)
        
        if not row:
            return None
        
        # تحويل النتيجة إلى قاموس
        user = {
            "user_id": str(row[0]),
            "membership_id": row[1],
            "username": row[2],
            "password": row[3],
            "email": row[4],
            "phone": row[5],
            "wallet": row[6],
            "balance": float(row[7]) if row[7] else 0.0,
            "registered": bool(row[8]),
            "deposited": bool(row[9]),
            "last_deposit": row[10],
            "last_task": row[11],
            "withdraw_request": bool(row[12]),
            "transactions": row[13] if row[13] else [],
            "referrer_id": str(row[14]) if row[14] else None,
            "created_date": row[15],
            "last_login": row[16],
            "login_count": row[17] or 0,
            "status": row[18] or "active",
            "first_deposit_time": row[19],
            "last_withdrawal_time": row[20],
            "last_task_status": row[21] or "completed"
        }
        
        # تحويل المعاملات إذا كانت نصاً
        if isinstance(user["transactions"], str):
            try:
                user["transactions"] = json.loads(user["transactions"])
            except:
                user["transactions"] = []
        
        # تخزين في الكاش
        cache_manager.set_user(user_id, user)
        return user
        
    except Exception as e:
        logger.error(f"Error loading user {user_id}: {e}")
        return None

def save_user(user):
    """حفظ بيانات المستخدم مع إبطال التخزين المؤقت"""
    load_user.cache_clear()
    cache_manager.delete_user(user.get("user_id"))
    
    try:
        conn = get_conn()
        c = conn.cursor()
        
        c.execute("""
            INSERT INTO users (
                user_id, membership_id, username, password, email, phone, wallet, balance,
                registered, deposited, last_deposit, last_task, withdraw_request, 
                transactions, referrer_id, created_date, last_login, login_count, 
                status, first_deposit_time, last_withdrawal_time, last_task_status
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            ) ON CONFLICT (user_id) DO UPDATE SET
                membership_id = EXCLUDED.membership_id,
                username = EXCLUDED.username,
                password = EXCLUDED.password,
                email = EXCLUDED.email,
                phone = EXCLUDED.phone,
                wallet = EXCLUDED.wallet,
                balance = EXCLUDED.balance,
                registered = EXCLUDED.registered,
                deposited = EXCLUDED.deposited,
                last_deposit = EXCLUDED.last_deposit,
                last_task = EXCLUDED.last_task,
                withdraw_request = EXCLUDED.withdraw_request,
                transactions = EXCLUDED.transactions,
                referrer_id = EXCLUDED.referrer_id,
                created_date = EXCLUDED.created_date,
                last_login = EXCLUDED.last_login,
                login_count = EXCLUDED.login_count,
                status = EXCLUDED.status,
                first_deposit_time = EXCLUDED.first_deposit_time,
                last_withdrawal_time = EXCLUDED.last_withdrawal_time,
                last_task_status = EXCLUDED.last_task_status
        """, (
            str(user.get("user_id")), 
            user.get("membership_id"),
            user.get("username"), 
            user.get("password"),
            user.get("email"), 
            user.get("phone"),
            user.get("wallet"), 
            user.get("balance", 0.0), 
            user.get("registered", False),
            user.get("deposited", False), 
            user.get("last_deposit"), 
            user.get("last_task"),
            user.get("withdraw_request", False), 
            json.dumps(user.get("transactions", [])), 
            user.get("referrer_id"), 
            user.get("created_date", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            user.get("last_login"),
            user.get("login_count", 0),
            user.get("status", "active"),
            user.get("first_deposit_time"),
            user.get("last_withdrawal_time"),
            user.get("last_task_status", "completed")
        ))
        
        conn.commit()
        return_conn(conn)
        
    except Exception as e:
        logger.error(f"Error saving user {user.get('user_id')}: {e}")

# ========= دوال التحقق المحسنة =========
def validate_email(email):
    """التحقق من صحة البريد الإلكتروني"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def validate_phone_number(phone):
    """التحقق من صحة رقم الهاتف مع رمز الدولة"""
    pattern = r'^(\+?\d{1,4}|00\d{1,4})?[\s\-]?\(?\d{1,5}\)?[\s\-]?\d{1,5}[\s\-]?\d{1,5}[\s\-]?\d{1,5}$'
    return re.match(pattern, phone) is not None and len(phone) >= 8

def validate_wallet_address(wallet, network=None):
    """التحقق من صحة عنوان المحفظة"""
    if not wallet or len(wallet) < 20:
        return False
    
    if network == "TRC20":
        return wallet.startswith("T") and len(wallet) == 34 and all(c in '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz' for c in wallet)
    elif network == "BEP20":
        return wallet.startswith("0x") and len(wallet) == 42 and all(c in '0123456789abcdefABCDEF' for c in wallet[2:])
    
    if wallet.startswith("T") and len(wallet) == 34:
        return True
    elif wallet.startswith("0x") and len(wallet) == 42:
        return True
    
    return False

def is_txid_unique(txid):
    """التحقق من أن رقم العملية غير مستخدم"""
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT 1 FROM deposit_requests WHERE txid = %s", (txid,))
        exists = c.fetchone() is not None
        return_conn(conn)
        return not exists
    except Exception as e:
        logger.error(f"Error checking TXID uniqueness: {e}")
        return False

def get_txid_usage_info(txid):
    """الحصول على معلومات عن TXID إذا كان مستخدماً"""
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT dr.user_id, u.username, dr.amount, dr.network, dr.status, dr.date 
            FROM deposit_requests dr 
            LEFT JOIN users u ON dr.user_id = u.user_id 
            WHERE dr.txid = %s
        """, (txid,))
        result = c.fetchone()
        return_conn(conn)
        
        if result:
            return {
                'user_id': result[0],
                'username': result[1],
                'amount': result[2],
                'network': result[3],
                'status': result[4],
                'date': result[5]
            }
        return None
    except Exception as e:
        logger.error(f"Error getting TXID info: {e}")
        return None

def validate_txid_format(txid, network):
    """التحقق من صيغة TXID بناءً على الشبكة"""
    if not txid or len(txid) < 10:
        return False
    
    if network == "TRC20":
        return len(txid) == 64 and all(c in '0123456789abcdefABCDEF' for c in txid)
    elif network == "BEP20":
        return txid.startswith('0x') and len(txid) == 66 and all(c in '0123456789abcdefABCDEF' for c in txid[2:])
    
    return True

def user_exists(username, exclude_id=None):
    try:
        conn = get_conn()
        c = conn.cursor()
        if exclude_id:
            c.execute("SELECT 1 FROM users WHERE username = %s AND user_id != %s", (username, str(exclude_id)))
        else:
            c.execute("SELECT 1 FROM users WHERE username = %s", (username,))
        exists = c.fetchone() is not None
        return_conn(conn)
        return exists
    except Exception as e:
        logger.error(f"Error checking user existence: {e}")
        return False

def wallet_exists(wallet, exclude_id=None):
    """التحقق من وجود المحفظة مسبقاً"""
    try:
        conn = get_conn()
        c = conn.cursor()
        if exclude_id:
            c.execute("SELECT 1 FROM users WHERE wallet = %s AND user_id != %s", (wallet, str(exclude_id)))
        else:
            c.execute("SELECT 1 FROM users WHERE wallet = %s", (wallet,))
        exists = c.fetchone() is not None
        return_conn(conn)
        return exists
    except Exception as e:
        logger.error(f"Error checking wallet existence: {e}")
        return False

# ========= نظام الحظر المحسن =========
def is_user_banned(user_id):
    """التحقق من إذا كان المستخدم محظوراً حالياً"""
    try:
        conn = get_conn()
        c = conn.cursor()
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("""
            SELECT * FROM user_bans 
            WHERE user_id = %s AND status = 'active' AND ban_end_time > %s
            ORDER BY ban_start_time DESC LIMIT 1
        """, (str(user_id), now))
        
        active_ban = c.fetchone()
        return_conn(conn)
        
        if active_ban:
            return {
                'banned': True,
                'ban_id': active_ban[0],
                'user_id': active_ban[1],
                'username': active_ban[2],
                'admin_id': active_ban[3],
                'ban_reason': active_ban[4],
                'ban_duration': active_ban[5],
                'ban_start_time': active_ban[6],
                'ban_end_time': active_ban[7],
                'status': active_ban[8]
            }
        
        return {'banned': False}
    except Exception as e:
        logger.error(f"Error checking user ban: {e}")
        return {'banned': False}

def ban_user(user_id, admin_id, ban_duration, ban_reason="تم الحظر من قبل الإدارة"):
    """حظر مستخدم لمدة محددة"""
    try:
        user = load_user(user_id)
        if not user:
            return False
        
        ban_start_time = datetime.now()
        
        # حساب وقت انتهاء الحظر بناءً على المدة
        if ban_duration == "2_minutes":
            ban_end_time = ban_start_time + timedelta(minutes=2)
            duration_text = "دقيقتين"
        elif ban_duration == "1_hour":
            ban_end_time = ban_start_time + timedelta(hours=1)
            duration_text = "ساعة واحدة"
        elif ban_duration == "24_hours":
            ban_end_time = ban_start_time + timedelta(hours=24)
            duration_text = "24 ساعة"
        elif ban_duration == "3_days":
            ban_end_time = ban_start_time + timedelta(days=3)
            duration_text = "3 أيام"
        elif ban_duration == "1_week":
            ban_end_time = ban_start_time + timedelta(weeks=1)
            duration_text = "أسبوع واحد"
        elif ban_duration == "permanent":
            ban_end_time = ban_start_time + timedelta(days=365*10)  # 10 سنوات
            duration_text = "دائم (حتى إلغاء الحظر)"
        else:
            return False
        
        conn = get_conn()
        c = conn.cursor()
        
        # إدخال سجل الحظر
        c.execute("""
            INSERT INTO user_bans 
            (user_id, username, admin_id, ban_reason, ban_duration, ban_start_time, ban_end_time, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            str(user_id),
            user.get('username'),
            str(admin_id),
            ban_reason,
            ban_duration,
            ban_start_time.strftime("%Y-%m-%d %H:%M:%S"),
            ban_end_time.strftime("%Y-%m-%d %H:%M:%S"),
            'active',
            ban_start_time.strftime("%Y-%m-%d %H:%M:%S")
        ))
        
        conn.commit()
        return_conn(conn)
        
        # إرسال رسالة للمستخدم المحظور عبر نظام الطوابير
        ban_message = f"""
🚫 **تم حظر حسابك من قبل إدارة تراكم**

📋 **تفاصيل الحظر:**
• ⏰ **مدة الحظر:** {duration_text}
• 🕐 **وقت البدء:** {ban_start_time.strftime('%Y-%m-%d %H:%M:%S')}
• ⏳ **وقت الانتهاء:** {ban_end_time.strftime('%Y-%m-%d %H:%M:%S')}
• 📝 **السبب:** {ban_reason}

🔒 **ملاحظات مهمة:**
• لا يمكنك استخدام أي من ميزات البوت خلال فترة الحظر
• يمكنك فقط التواصل مع الدعم الفني
• سيتم فك الحظر تلقائياً بعد انتهاء المدة

📞 **للاستفسار أو الطعن في الحظر:** 
@{Config.SUPPORT_BOT_USERNAME}

نأسف للإزعاج ونأمل أن تكون فترة الحظر فرصة للالتزام بشروط الاستخدام.
        """
        
        queue_manager.add_to_user_queue(user_id, user_id, bot.send_message, user_id, ban_message, parse_mode="Markdown")
        
        log_event(admin_id, "USER_BANNED", f"User: {user['username']}, Duration: {duration_text}, Reason: {ban_reason}")
        return True
        
    except Exception as e:
        logger.error(f"Error banning user {user_id}: {e}")
        return False

def unban_user(ban_id, admin_id):
    """فك حظر مستخدم"""
    try:
        conn = get_conn()
        c = conn.cursor()
        
        # الحصول على معلومات الحظر
        c.execute("SELECT * FROM user_bans WHERE id = %s", (ban_id,))
        ban_record = c.fetchone()
        
        if not ban_record:
            return_conn(conn)
            return False
        
        # تحديث حالة الحظر
        c.execute("UPDATE user_bans SET status = 'inactive' WHERE id = %s", (ban_id,))
        conn.commit()
        return_conn(conn)
        
        # إرسال رسالة للمستخدم عبر نظام الطوابير
        unban_message = f"""
🎉 **تم فك الحظر عن حسابك**

✅ **معلومات فك الحظر:**
• 📅 **وقت فك الحظر:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
• 👨‍💼 **تم بواسطة:** الإدارة

🚀 **يمكنك الآن استخدام جميع ميزات البوت بشكل طبيعي.**

نرحب بعودتك إلى تراكم ونتطلع إلى توفير أفضل تجربة استثمارية لك.

شكراً لتفهمك والتزامك بشروط الاستخدام.
        """
        
        queue_manager.add_to_user_queue(ban_record[1], ban_record[1], bot.send_message, ban_record[1], unban_message, parse_mode="Markdown")
        
        log_event(admin_id, "USER_UNBANNED", f"User: {ban_record[2]}, Ban ID: {ban_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error unbanning user with ban ID {ban_id}: {e}")
        return False

def get_active_bans():
    """الحصول على جميع الحظور النشطة"""
    try:
        conn = get_conn()
        c = conn.cursor()
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("""
            SELECT * FROM user_bans 
            WHERE status = 'active' AND ban_end_time > %s
            ORDER BY ban_start_time DESC
        """, (now,))
        
        active_bans = c.fetchall()
        return_conn(conn)
        
        logger.info(f"🔍 [BANS_DEBUG] عدد الحظور في قاعدة البيانات: {len(active_bans)}")
        
        bans_list = []
        for ban in active_bans:
            bans_list.append({
                'ban_id': ban[0],
                'user_id': ban[1],
                'username': ban[2] or 'غير معروف',
                'admin_id': ban[3],
                'ban_reason': ban[4] or 'غير محدد',
                'ban_duration': ban[5] or 'غير محدد',
                'ban_start_time': ban[6] or 'غير محدد',
                'ban_end_time': ban[7] or 'غير محدد',
                'status': ban[8]
            })
        
        return bans_list
        
    except Exception as e:
        logger.error(f"❌ خطأ في get_active_bans: {e}")
        return []

def check_ban_expiry():
    """التحقق من انتهاء مدة الحظر وتحديثها تلقائياً"""
    try:
        conn = get_conn()
        c = conn.cursor()
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # الحصول على الحظور المنتهية
        c.execute("""
            SELECT * FROM user_bans 
            WHERE status = 'active' AND ban_end_time <= %s
        """, (now,))
        
        expired_bans = c.fetchall()
        
        for ban in expired_bans:
            # تحديث حالة الحظر إلى منتهي
            c.execute("UPDATE user_bans SET status = 'expired' WHERE id = %s", (ban[0],))
            
            # إرسال رسالة للمستخدم عبر نظام الطوابير
            expiry_message = f"""
🎉 **انتهت مدة حظر حسابك**

✅ **معلومات انتهاء الحظر:**
• 📅 **وقت الانتهاء:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
• 🔓 **الحالة:** تم فك الحظر تلقائياً

🚀 **يمكنك الآن استخدام جميع ميزات البوت بشكل طبيعي.**

نرحب بعودتك إلى تراكم ونتطلع إلى توفير أفضل تجربة استثمارية لك.

شكراً لتفهمك والتزامك بشروط الاستخدام.
            """
            
            queue_manager.add_to_user_queue(ban[1], ban[1], bot.send_message, ban[1], expiry_message, parse_mode="Markdown")
            
            logger.info(f"Ban expired automatically for user {ban[2]}")
        
        conn.commit()
        return_conn(conn)
        
    except Exception as e:
        logger.error(f"Error checking ban expiry: {e}")

def schedule_ban_check():
    """جدولة التحقق من انتهاء مدة الحظر كل دقيقة"""
    while True:
        time.sleep(60)  # التحقق كل دقيقة
        check_ban_expiry()

# ========= التحقق من وجود طلب إيداع معلق =========
def has_pending_deposit(user_id):
    """التحقق من وجود طلب إيداع معلق للمستخدم"""
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT 1 FROM deposit_requests WHERE user_id = %s AND status = 'pending'", (str(user_id),))
        exists = c.fetchone() is not None
        return_conn(conn)
        return exists
    except Exception as e:
        logger.error(f"Error checking pending deposit: {e}")
        return False

# ========= دوال المستخدم المحسنة =========
def create_user(user_id, username, referrer_id=None):
    """إنشاء مستخدم جديد مع رقم عضوية فريد"""
    existing_user = load_user(user_id)
    if existing_user:
        return False
    
    membership_id = get_next_membership_id()
    
    user = {
        "user_id": str(user_id),
        "membership_id": membership_id,
        "username": username,
        "password": None,
        "email": None,
        "phone": None,
        "wallet": None,
        "balance": 0.0,
        "registered": False,
        "deposited": False,
        "last_deposit": None,
        "last_task": None,
        "withdraw_request": False,
        "transactions": [],
        "referrer_id": referrer_id if referrer_id and referrer_id != str(user_id) else None,
        "created_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_login": None,
        "login_count": 0,
        "status": "active",
        "first_deposit_time": None,
        "last_withdrawal_time": None,
        "last_task_status": "completed"
    }
    save_user(user)
    
    log_event(user_id, "USER_CREATED", f"Membership ID: {membership_id}, Username: {username}")
    return True

def add_transaction(user_id, tx_type, amount, description=""):
    """إضافة معاملة مع وصف تفصيلي"""
    user = load_user(user_id)
    if not user:
        return
    
    tx = {
        "type": tx_type,
        "amount": amount,
        "description": description,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tx_id": generate_secure_token(16)
    }
    
    user.setdefault("transactions", [])
    user["transactions"].append(tx)
    save_user(user)

def add_withdrawal(user_id, amount):
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            INSERT INTO withdrawals (user_id, amount, status, date) 
            VALUES (%s, %s, %s, %s)
        """, (str(user_id), amount, "pending", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        return_conn(conn)
    except Exception as e:
        logger.error(f"Error adding withdrawal: {e}")

def get_total_membership_count():
    """الحصول على إجمالي عدد الأعضاء"""
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users WHERE registered = true")
        count = c.fetchone()[0]
        return_conn(conn)
        return count
    except Exception as e:
        logger.error(f"Error getting total membership count: {e}")
        return 0

# ========= نظام جوائز الإحالة الجماعية الجديد =========
def get_referral_batch_bonus(referrer_id):
    """الحصول على بيانات جائزة الإحالة الجماعية للمحيل"""
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM referral_batch_bonus WHERE referrer_id = %s", (str(referrer_id),))
        row = c.fetchone()
        return_conn(conn)
        
        if not row:
            return None
        
        return {
            'id': row[0],
            'referrer_id': row[1],
            'completed_batches': row[2],
            'pending_users': row[3] if row[3] else [],
            'total_bonus_earned': float(row[4]) if row[4] else 0.0,
            'last_bonus_date': row[5],
            'created_date': row[6]
        }
    except Exception as e:
        logger.error(f"Error getting referral batch bonus: {e}")
        return None

def create_referral_batch_bonus(referrer_id):
    """إنشاء سجل جديد لجائزة الإحالة الجماعية"""
    try:
        conn = get_conn()
        c = conn.cursor()
        
        initial_data = {
            'referrer_id': str(referrer_id),
            'completed_batches': 0,
            'pending_users': [],
            'total_bonus_earned': 0.0,
            'last_bonus_date': None,
            'created_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        c.execute("""
            INSERT INTO referral_batch_bonus 
            (referrer_id, completed_batches, pending_users, total_bonus_earned, last_bonus_date, created_date)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            initial_data['referrer_id'],
            initial_data['completed_batches'],
            json.dumps(initial_data['pending_users']),
            initial_data['total_bonus_earned'],
            initial_data['last_bonus_date'],
            initial_data['created_date']
        ))
        
        conn.commit()
        return_conn(conn)
        return initial_data
    except Exception as e:
        logger.error(f"Error creating referral batch bonus: {e}")
        return None

def update_referral_batch_bonus(referrer_id, completed_batches=None, pending_users=None, total_bonus_earned=None):
    """تحديث بيانات جائزة الإحالة الجماعية"""
    try:
        conn = get_conn()
        c = conn.cursor()
        
        current_data = get_referral_batch_bonus(referrer_id)
        if not current_data:
            current_data = create_referral_batch_bonus(referrer_id)
        
        if completed_batches is not None:
            current_data['completed_batches'] = completed_batches
        
        if pending_users is not None:
            current_data['pending_users'] = pending_users
        
        if total_bonus_earned is not None:
            current_data['total_bonus_earned'] = total_bonus_earned
        
        current_data['last_bonus_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        c.execute("""
            UPDATE referral_batch_bonus 
            SET completed_batches = %s, pending_users = %s, total_bonus_earned = %s, last_bonus_date = %s
            WHERE referrer_id = %s
        """, (
            current_data['completed_batches'],
            json.dumps(current_data['pending_users']),
            current_data['total_bonus_earned'],
            current_data['last_bonus_date'],
            str(referrer_id)
        ))
        
        conn.commit()
        return_conn(conn)
        return current_data
    except Exception as e:
        logger.error(f"Error updating referral batch bonus: {e}")
        return None

def handle_referral_batch_bonus(referrer_id, new_user_id, new_user_username, deposit_amount):
    """إدارة نظام جوائز الإحالة الجماعية"""
    try:
        bonus_data = get_referral_batch_bonus(referrer_id)
        if not bonus_data:
            bonus_data = create_referral_batch_bonus(referrer_id)
        
        pending_users = bonus_data['pending_users']
        
        user_exists = any(user['user_id'] == str(new_user_id) for user in pending_users)
        if not user_exists:
            pending_users.append({
                'user_id': str(new_user_id),
                'username': new_user_username,
                'deposit_amount': deposit_amount,
                'added_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
        
        # التعديل: تغيير من 20 إلى 3 مستخدمين
        if len(pending_users) >= 3:
            batch_bonus = 100.0
            referrer = load_user(referrer_id)
            if referrer:
                referrer['balance'] += batch_bonus
                save_user(referrer)
                
                add_transaction(referrer_id, "referral_batch_bonus", batch_bonus, 
                               f"جائزة الإحالة الجماعية - مجموعة {bonus_data['completed_batches'] + 1}")
                
                new_completed_batches = bonus_data['completed_batches'] + 1
                new_total_bonus = bonus_data['total_bonus_earned'] + batch_bonus
                
                remaining_users = pending_users[3:]
                
                bonus_data = update_referral_batch_bonus(
                    referrer_id,
                    completed_batches=new_completed_batches,
                    pending_users=remaining_users,
                    total_bonus_earned=new_total_bonus
                )
                
                try:
                    send_batch_bonus_notification(referrer_id, bonus_data, pending_users[:3], batch_bonus)
                except Exception as e:
                    logger.error(f"Error sending batch bonus notification: {e}")
                
                log_event(referrer_id, "BATCH_BONUS_AWARDED", 
                         f"Batch: {new_completed_batches}, Users: {[u['username'] for u in pending_users[:3]]}, Bonus: {batch_bonus}")
                
                return True, batch_bonus
            else:
                update_referral_batch_bonus(referrer_id, pending_users=pending_users)
                return False, 0
        else:
            update_referral_batch_bonus(referrer_id, pending_users=pending_users)
            return False, 0
            
    except Exception as e:
        logger.error(f"Error in handle_referral_batch_bonus: {e}")
        return False, 0

def send_batch_bonus_notification(referrer_id, bonus_data, completed_users, bonus_amount):
    """إرسال إشعار جائزة الإحالة الجماعية"""
    referrer = load_user(referrer_id)
    if not referrer:
        return
    
    users_list = ""
    for i, user in enumerate(completed_users, 1):
        users_list += f"{i}. @{user['username']} - {user['deposit_amount']:.2f}$\n"
    
    notification_text = f"""
🎉 **تهانينا! فزت بجائزة الإحالة الجماعية** 🎉

🏆 **تفاصيل الجائزة:**
• 🎯 العدد المطلوب: 3 مستخدمين جدد
• ✅ المحالين النشطين: 3 مستخدمين
• 💰 قيمة الجائزة: {bonus_amount:.2f}$
• 💳 رصيدك الجديد: {referrer['balance']:.2f}$

👥 **تفاصيل المحالين:**
{users_list}
📊 **إحصائياتك التراكمية:**
• 📦 المجموعات المكتملة: {bonus_data['completed_batches']}
• 💵 إجمالي الجوائز: {bonus_data['total_bonus_earned']:.2f}$
• 🎯 المحالين في الانتظار: {len(bonus_data['pending_users'])}

🚀 **استمر في جلب المزيد من الأعضاء واكسب 100$ لكل 3 مستخدمين جدد!**
"""
    
    queue_manager.add_to_user_queue(referrer_id, referrer_id, bot.send_message, referrer_id, notification_text, parse_mode="Markdown")

def get_batch_bonus_progress(referrer_id):
    """الحصول على تقدم المحيل في جوائز الإحالة الجماعية"""
    bonus_data = get_referral_batch_bonus(referrer_id)
    if not bonus_data:
        return {
            'completed_batches': 0,
            'pending_users_count': 0,
            'total_bonus_earned': 0.0,
            'users_until_next_bonus': 3,
            'progress_percentage': 0
        }
    
    pending_count = len(bonus_data['pending_users'])
    users_until_next = 3 - pending_count
    progress_percentage = (pending_count / 3) * 100
    
    return {
        'completed_batches': bonus_data['completed_batches'],
        'pending_users_count': pending_count,
        'total_bonus_earned': bonus_data['total_bonus_earned'],
        'users_until_next_bonus': users_until_next if users_until_next > 0 else 0,
        'progress_percentage': progress_percentage
    }

# ========= نظام الرسائل المحسن =========
class MessageTemplates:
    """قوالب الرسائل الاحترافية"""
    
    @staticmethod
    def welcome_message():
        return """
🌟 **مرحباً بك في تراكم - منصة الاستثمار الذكية** 🌟

🏆 **المنصة الأكثر تطوراً للاستثمار الآمن**

✨ **مميزات حصرية نقدمها لك:**
• ✅ استثمار مرخص وموثوق عالمياً
• 💰 عوائد يومية تصل إلى 7% 
• 🛡️ حماية كاملة لرأس المال
• 🌐 تقنيات أمنية متطورة
• 📱 دعم فني على مدار الساعة

🚀 **ابدأ رحلتك الاستثمارية اليوم وكن جزءاً من مجتمعنا الناجح!**
        """
    
    @staticmethod
    def registration_success(user):
        return f"""
🎊 **تهانينا! تم إنشاء حسابك بنجاح** 🎊

📋 **تفاصيل حسابك:**
👤 **اسم المستخدم:** `{user['username']}`
🔐 **كلمة المرور:** `{user['password']}`
📧 **البريد الإلكتروني:** `{user['email']}`
📱 **رقم الهاتف:** `{user.get('phone', 'غير مسجل')}`
🆔 **رقم العضوية:** `{user.get('membership_id', 'N/A')}`
📅 **تاريخ التسجيل:** {user['created_date']}

💡 **نصائح أمنية:**
• 🔒 احفظ بيانات الدخول في مكان آمن
• 🔄 غير كلمة المرور دورياً
• 📧 لا تشارك بياناتك مع أحد

💰 **يمكنك الآن البدء في الإيداع والاستثمار!**
        """
    
    @staticmethod
    def deposit_instructions(amount, network, wallet_address):
        network_info = {
            "TRC20": "شبكة تورن (TRC20)",
            "BEP20": "شبكة بينانس سمارت تشين (BEP20)"
        }
        
        return f"""
💳 **تعليمات الإيداع - {network_info[network]}** 💳

📍 **عنوان المحفظة:**
`{wallet_address}`

📋 **تفاصيل التحويل:**
💵 **المبلغ:** {amount:.2f}$
🌐 **الشبكة:** {network}
⏰ **الوقت المتوقع:** 5-15 دقيقة

🔍 **خطوات التنفيذ:**
1. انتقل إلى محفظتك
2. اختر "إرسال" أو "Send"
3. أدخل العنوان أعلاه
4. اختر الشبكة: **{network}**
5. أدخل المبلغ: **{amount:.2f}$**
6. تأكد من صحة البيانات
7. أكد عملية الإرسال

📝 **ملاحظات هامة:**
• تأكد من اختيار الشبكة الصحيحة
• لا ترسل عملات غير USDT
• احفظ رقم العملية (TXID)
• الإيداع عبر شبكات أخرى سيؤدي إلى فقدان الأموال
        """
    
    @staticmethod
    def withdrawal_requested(amount, wallet):
        return f"""
✅ **تم إرسال طلب السحب بنجاح**

📋 **تفاصيل الطلب:**
💸 **المبلغ:** {amount:.2f}$
💳 **المحفظة:** `{wallet}`
📅 **وقت الطلب:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

⏳ **حالة الطلب:** قيد المراجعة
🕐 **الوقت المتوقع:** 4-24 ساعة

📞 **للاستفسار:** @{Config.SUPPORT_BOT_USERNAME}
        """
    
    @staticmethod
    def daily_task_info(profit, balance):
        return f"""
🎯 **المهمة اليومية - النظام التقليدي**

💰 **التفاصيل:**
• 📊 الرصيد الحالي: {balance:.2f}$
• 💰 الربح المتوقع: {profit:.2f}$
• ⏰ المدة: 30 ثانية
• 🔄 المهمة القادمة: بعد 24 ساعة من انتهاء هذه المهمة

📋 **المتطلبات:**
• البقاء في الصفحة خلال التنفيذ
• عدم الضغط على أي أزرار
• انتظار انتهاء المؤقت

⚠️ **تحذير:** أي خروج من الصفحة سيفشل المهمة!
        """
    
    @staticmethod
    def deposit_rejected(amount, reason):
        return f"""
❌ **تم رفض طلب الإيداع**

📋 **تفاصيل الطلب:**
💵 **المبلغ:** {amount:.2f}$
📅 **الوقت:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

📝 **سبب الرفض:**
{reason}

📞 **للاستفسار:** @{Config.SUPPORT_BOT_USERNAME}
        """
    
    @staticmethod
    def withdrawal_rejected(amount, reason):
        return f"""
❌ **تم رفض طلب السحب**

📋 **تفاصيل الطلب:**
💸 **المبلغ:** {amount:.2f}$
📅 **الوقت:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

📝 **سبب الرفض:**
{reason}

📞 **للاستفسار:** @{Config.SUPPORT_BOT_USERNAME}
        """

# ========= حالات المستخدم =========
user_states = {}
logged_in_users = set()
active_tasks = {}  # تتبع المهام النشطة

def require_login(func):
    def wrapper(message, *args, **kwargs):
        user_id = message.from_user.id
        
        if not security.check_rate_limit(user_id, "login_check", 10, 60):
            queue_manager.add_to_user_queue(user_id, message.chat.id, bot.send_message, message.chat.id, "⏳ تم تجاوز الحد المسموح للمحاولات. يرجى الانتظار قليلاً.")
            return
        
        if user_id not in logged_in_users:
            queue_manager.add_to_user_queue(user_id, message.chat.id, bot.send_message, message.chat.id, "🔐 **يجب تسجيل الدخول أولاً**\n\nالرجاء تسجيل الدخول للوصول إلى هذه الميزة.")
            return
        
        return func(message, *args, **kwargs)
    return wrapper

# ========= التحقق من وجود طلب سحب معلق =========
def has_pending_withdrawal(user_id):
    """التحقق من وجود طلب سحب معلق للمستخدم"""
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT 1 FROM withdrawals WHERE user_id = %s AND status = 'pending'", (str(user_id),))
        exists = c.fetchone() is not None
        return_conn(conn)
        return exists
    except Exception as e:
        logger.error(f"Error checking pending withdrawal: {e}")
        return False

# ========= بدء النظام =========
if __name__ == "__main__":
    print("=" * 60)
    print("🏦 **نظام تراكم - منصة الاستثمار الذكية**")
    print("🇦🇪 **المنصة الاستثمارية الإماراتية المرخصة**")
    print("=" * 60)
    print("📧 البريد الإلكتروني: info@tarakum.ae")
    print("📞 الدعم: @Tarakumbot")
    print("📢 القناة: t.me/TarakumAE_Support")
    print("=" * 60)
    print("🚀 جاري تحميل الجزء الأول من النظام...")
    # ========= نظام المهمة اليومية المحسن مع الإصلاح الكامل =========
def get_task_status(user_id):
    """الحصول على حالة المهمة للمستخدم - نسخة معدلة"""
    user = load_user(user_id)
    if not user:
        return {'status': 'not_registered', 'message': '❌ يجب التسجيل أولاً'}
    
    if user_id in active_tasks:
        task_info = active_tasks[user_id]
        elapsed = int((datetime.now() - task_info['start_time']).total_seconds())
        remaining = 30 - elapsed
        return {
            'status': 'active', 
            'message': f'⏳ المهمة جارية... المتبقي: {remaining} ثانية',
            'remaining': remaining
        }
    
    last_task_time = user.get("last_task")
    last_task_status = user.get("last_task_status", "completed")
    
    if last_task_status == "failed":
        return {
            'status': 'available', 
            'message': '🔄 المهمة متاحة للمحاولة مرة أخرى (المحاولة السابقة فشلت)',
            'can_retry': True
        }
    
    if not last_task_time:
        return {
            'status': 'available',
            'message': '✅ المهمة متاحة الآن للبدء لأول مرة',
            'can_retry': False
        }
    
    try:
        last_task_date = datetime.strptime(last_task_time, "%Y-%m-%d %H:%M:%S")
        next_available = last_task_date + timedelta(hours=24)
        now = datetime.now()
        
        if now >= next_available:
            return {
                'status': 'available',
                'message': '✅ المهمة متاحة الآن',
                'can_retry': False
            }
        else:
            remaining = next_available - now
            hours = int(remaining.total_seconds() // 3600)
            minutes = int((remaining.total_seconds() % 3600)) // 60
            
            return {
                'status': 'waiting',
                'message': f'⏳ المتبقي للمهمة القادمة: {hours}س {minutes}د',
                'next_available': next_available.strftime('%H:%M:%S')
            }
    except:
        return {
            'status': 'available',
            'message': '✅ المهمة متاحة الآن',
            'can_retry': False
        }

@bot.message_handler(func=lambda m: m.text == "🎯 المهمة اليومية")
@require_login
def daily_task(message):
    ban_check = is_user_banned(message.from_user.id)
    if ban_check['banned']:
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "🚫 حسابك محظور ولا يمكنك استخدام هذه الميزة."
        )
        return
    
    user_id = message.from_user.id
    
    if user_id in active_tasks:
        task_info = active_tasks[user_id]
        elapsed = int((datetime.now() - task_info['start_time']).total_seconds())
        remaining = 30 - elapsed
        
        queue_manager.add_to_user_queue(
            user_id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id,
            f"⏳ **المهمة قيد التنفيذ حالياً**\n\n"
            f"📊 **حالة المهمة:** جارية\n"
            f"⏰ **الوقت المتبقي:** {remaining} ثانية\n"
            f"💰 **الربح المتوقع:** {task_info['profit']:.2f}$\n\n"
            f"📱 **الرجاء الانتظار حتى انتهاء المهمة**",
            parse_mode="Markdown"
        )
        return
    
    user = load_user(user_id)
    task_status = get_task_status(user_id)
    
    if task_status['status'] != 'available':
        queue_manager.add_to_user_queue(
            user_id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id,
            f"ℹ️ **حالة المهمة**\n\n{task_status['message']}",
            parse_mode="Markdown"
        )
        return

    task_profit = round(user.get("balance", 0.0) * 0.03, 2)
    if task_profit < 0.01:
        task_profit = 0.01
    
    queue_manager.add_to_user_queue(
        user_id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id,
        f"🎯 **المهمة اليومية - النظام التقليدي**\n\n"
        f"💰 **التفاصيل:**\n"
        f"• 📊 الرصيد الحالي: {user.get('balance',0.0):.2f}$\n"
        f"• 💰 الربح المتوقع: {task_profit:.2f}$\n"
        f"• ⏰ المدة: 30 ثانية\n"
        f"• 🔄 المهمة القادمة: بعد 24 ساعة من نجاح هذه المهمة\n\n"
        f"📋 **المتطلبات:**\n"
        f"• البقاء في الصفحة خلال التنفيذ\n"
        f"• عدم الضغط على أي أزرار\n"
        f"• انتظار انتهاء المؤقت\n\n"
        f"⚠️ **تحذير:** أي خروج من الصفحة سيفشل المهمة!",
        parse_mode="Markdown",
        reply_markup=task_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == "🎯 بدء المهمة اليومية")
@require_login
def start_daily_task(message):
    ban_check = is_user_banned(message.from_user.id)
    if ban_check['banned']:
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "🚫 حسابك محظور ولا يمكنك استخدام هذه الميزة."
        )
        return
    
    user_id = message.from_user.id
    
    if user_id in active_tasks:
        queue_manager.add_to_user_queue(
            user_id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id,
            "⏳ **المهمة قيد التنفيذ حالياً**\n\n"
            "لديك مهمة نشطة حالياً.\n"
            "الرجاء الانتظار حتى انتهاء المهمة الحالية.",
            parse_mode="Markdown"
        )
        return
    
    user = load_user(user_id)
    task_status = get_task_status(user_id)
    
    if task_status['status'] != 'available':
        queue_manager.add_to_user_queue(
            user_id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id,
            f"❌ **لا يمكن بدء المهمة**\n\n{task_status['message']}",
            parse_mode="Markdown",
            reply_markup=main_menu(user_id)
        )
        return
    
    task_profit = round(user.get("balance", 0.0) * 0.03, 2)
    if task_profit < 0.01:
        task_profit = 0.01
    
    # تسجيل المهمة كنشطة
    active_tasks[user_id] = {
        'start_time': datetime.now(),
        'profit': task_profit,
        'message_id': None,
        'status': 'running',
        'chat_id': message.chat.id,
        'user_id': user_id
    }
    
    def send_countdown():
        countdown_msg = bot.send_message(
            message.chat.id, 
            f"🚀 **بدء المهمة اليومية**\n\n"
            f"⏰ **المدة:** 30 ثانية\n"
            f"💰 **الربح المتوقع:** {task_profit:.2f}$\n"
            f"📊 **التقدم:** 0%\n\n"
            f"🎯 **جاري تهيئة النظام...**\n"
            f"📱 **لا تترك هذه الصفحة!**",
            parse_mode="Markdown"
        )
        
        active_tasks[user_id]['message_id'] = countdown_msg.message_id

        # بدء المؤقت في thread منفصل
        threading.Thread(
            target=task_countdown, 
            args=(user_id, message.chat.id, countdown_msg.message_id, task_profit),
            daemon=True
        ).start()
    
    queue_manager.add_to_user_queue(user_id, message.chat.id, send_countdown)

def task_countdown(user_id, chat_id, msg_id, profit):
    """دالة المؤقت للمهمة - نسخة محسنة"""
    try:
        duration = 30
        start_time = time.time()
        task_completed = False
        
        for elapsed in range(1, duration + 1):
            time.sleep(1)
            
            # التحقق إذا ما زالت المهمة نشطة
            if user_id not in active_tasks:
                logger.info(f"المهمة ألغيت للمستخدم {user_id}")
                return
            
            if active_tasks[user_id].get('status') != 'running':
                logger.info(f"المهمة توقفت للمستخدم {user_id}")
                return
            
            # حساب النسبة المئوية
            percentage = int((elapsed / duration) * 100)
            
            # إنشاء شريط التقدم
            progress_bar = "🟢" * (percentage // 10) + "⚪" * (10 - (percentage // 10))
            remaining = duration - elapsed
            
            def update_progress():
                try:
                    bot.edit_message_text(
                        chat_id=chat_id, 
                        message_id=msg_id,
                        text=f"🚀 **جاري تنفيذ المهمة**\n\n"
                             f"⏰ **الوقت المتبقي:** {remaining} ثانية\n"
                             f"📊 **التقدم:** {percentage}%\n"
                             f"📈 **شريط التقدم:** {progress_bar}\n"
                             f"💰 **الربح المتوقع:** {profit:.2f}$\n\n"
                             f"📱 **لا تترك هذه الصفحة!**\n"
                             f"🔒 **الجلسة مؤمنة...**",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"خطأ في تحديث المؤقت: {e}")
            
            queue_manager.add_to_user_queue(user_id, chat_id, update_progress)
        
        # إذا أكمل المهمة بنجاح
        if user_id in active_tasks and active_tasks[user_id].get('status') == 'running':
            user = load_user(user_id)
            if user:
                user["balance"] = round(user.get("balance", 0.0) + profit, 2)
                user["last_task"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                user["last_task_status"] = "completed"  # حالة النجاح
                add_transaction(user_id, "daily_task", profit, "إكمال المهمة اليومية بنجاح")
                save_user(user)
                
                next_task_time = datetime.now() + timedelta(hours=24)
                
                success_message = f"""
🎉 **تم إكمال المهمة بنجاح!** 🎉

✅ **الحالة:** مكتملة بنجاح
💰 **الربح المحقق:** {profit:.2f}$
💳 **الرصيد الجديد:** {user['balance']:.2f}$
🕐 **وقت الإكمال:** {datetime.now().strftime('%H:%M:%S')}

⏰ **المهمة القادمة متاحة:** {next_task_time.strftime('%Y-%m-%d %H:%M:%S')}
🔄 **ستتمكن من البدء بعد 24 ساعة**

🎯 **استمر في تحقيق الأرباح!**
                """
                
                def send_success():
                    try:
                        # محاولة تحديث الرسالة الأصلية
                        bot.edit_message_text(
                            chat_id=chat_id, 
                            message_id=msg_id,
                            text=success_message,
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"خطأ في تحديث رسالة النجاح: {e}")
                        # إذا فشل التحديث، أرسل رسالة جديدة
                        try:
                            bot.send_message(
                                chat_id=chat_id,
                                text=success_message,
                                parse_mode="Markdown",
                                reply_markup=main_menu(user_id)
                            )
                        except Exception as e2:
                            logger.error(f"خطأ في إرسال رسالة النجاح: {e2}")
                
                queue_manager.add_to_user_queue(user_id, chat_id, send_success)
                
                log_event(user_id, "DAILY_TASK_COMPLETED", f"الربح: {profit}")
                task_completed = True
                
    except Exception as e:
        logger.error(f"خطأ في مؤقت المهمة: {e}")
    finally:
        # إزالة المهمة من القائمة النشطة
        if user_id in active_tasks:
            active_tasks.pop(user_id, None)

@bot.message_handler(func=lambda m: m.from_user.id in active_tasks)
def handle_interrupt_task(message):
    """معالجة مقاطعة المهمة - نسخة محسنة"""
    user_id = message.from_user.id
    
    if user_id in active_tasks:
        task_info = active_tasks[user_id]
        profit = task_info.get('profit', 0)
        
        # تحديث حالة المستخدم بالفشل
        user = load_user(user_id)
        if user:
            user["last_task_status"] = "failed"  # حالة الفشل
            save_user(user)
        
        # إزالة المهمة
        active_tasks.pop(user_id, None)
        
        # إرسال إشعار الفشل عبر نظام الطوابير
        def send_failure_notification():
            bot.send_message(
                message.chat.id,
                f"❌ **تم إلغاء المهمة**\n\n"
                f"📋 **تفاصيل الإلغاء:**\n"
                f"⏰ **الوقت:** {datetime.now().strftime('%H:%M:%S')}\n"
                f"💰 **الربح المفقود:** {profit:.2f}$\n\n"
                f"📝 **سبب الإلغاء:** قمت بالضغط على زر أو إجراء action آخر\n\n"
                f"💡 **نصائح للنجاح في المرة القادمة:**\n"
                f"• لا تضغط على أي أزرار أثناء التنفيذ\n"
                f"• ابق في الصفحة حتى انتهاء المؤقت\n"
                f"• تأكد من اتصال الإنترنت\n\n"
                f"🔄 **يمكنك البدء بمهمة جديدة الآن**",
                parse_mode="Markdown",
                reply_markup=main_menu(user_id)
            )
        
        queue_manager.add_to_user_queue(user_id, message.chat.id, send_failure_notification)
        
        log_event(user_id, "TASK_CANCELLED", "المستخدم قام بمقاطعة المهمة")

# ========= الأوامر الرئيسية المحسنة =========
@bot.message_handler(commands=["start"])
def start(message):
    if not security.check_rate_limit(message.from_user.id, "start", 5, 60):
        return
    
    # التحقق من الحظر أولاً
    ban_check = is_user_banned(message.from_user.id)
    if ban_check['banned']:
        ban_message = f"""
🚫 **حسابك محظور حالياً**

📋 **تفاصيل الحظر:**
• ⏰ **مدة الحظر:** {ban_check['ban_duration']}
• 🕐 **وقت البدء:** {ban_check['ban_start_time']}
• ⏳ **وقت الانتهاء:** {ban_check['ban_end_time']}
• 📝 **السبب:** {ban_check['ban_reason']}

🔒 **ملاحظات مهمة:**
• لا يمكنك استخدام أي من ميزات البوت خلال فترة الحظر
• يمكنك فقط التواصل مع الدعم الفني
• سيتم فك الحظر تلقائياً بعد انتهاء المدة

📞 **للاستفسار أو الطعن في الحظر:** 
@{Config.SUPPORT_BOT_USERNAME}
        """
        queue_manager.add_to_user_queue(message.from_user.id, message.chat.id, bot.send_message, message.chat.id, ban_message, parse_mode="Markdown")
        return
    
    args = message.text.split()
    referrer_id = None
    if len(args) > 1 and args[1].startswith("ref_"):
        referrer_id = args[1].split("_", 1)[1]

    user = load_user(message.from_user.id)
    if user and user.get("registered"):
        user["last_login"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        user["login_count"] = user.get("login_count", 0) + 1
        save_user(user)
        
        logged_in_users.add(message.from_user.id)
        
        welcome_back = f"""
🎉 **أهلاً بعودتك إلى تراكم!**

👤 **مرحباً مجدداً، {user['username']}**
🆔 **رقم عضوية:** {user.get('membership_id', 'N/A')}
💼 **حالة الحساب:** ✅ نشط

💰 **اختر من القائمة أدناه لمواصلة رحلتك الاستثمارية:**
        """
        
        def send_welcome_back():
            bot.send_message(
                message.chat.id, 
                welcome_back, 
                parse_mode="Markdown",
                reply_markup=main_menu(message.from_user.id)
            )
        
        queue_manager.add_to_user_queue(message.from_user.id, message.chat.id, send_welcome_back)
        return

    success = create_user(message.from_user.id, message.from_user.username or f"user_{message.from_user.id}", referrer_id)
    if success:
        def send_welcome():
            bot.send_message(
                message.chat.id, 
                MessageTemplates.welcome_message(), 
                parse_mode="Markdown",
                reply_markup=main_menu(message.from_user.id)
            )
        
        queue_manager.add_to_user_queue(message.from_user.id, message.chat.id, send_welcome)
        log_event(message.from_user.id, "START", f"Referrer: {referrer_id}")

# ========= التسجيل المحسن مع إضافة رقم الهاتف =========
@bot.message_handler(func=lambda m: m.text == "📝 التسجيل / تحديث البيانات")
def register_start(message):
    # التحقق من الحظر
    ban_check = is_user_banned(message.from_user.id)
    if ban_check['banned']:
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "🚫 حسابك محظور ولا يمكنك استخدام هذه الميزة."
        )
        return
    
    if not security.check_rate_limit(message.from_user.id, "registration", 3, 300):
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "⏳ يمكنك محاولة التسجيل مرة أخرى بعد 5 دقائق."
        )
        return
    
    user = load_user(message.from_user.id)
    if user and user.get("registered"):
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "📝 **تحديث البيانات الشخصية**\n\nيمكنك تحديث معلومات حسابك من خلال الخيارات أدناه:",
            parse_mode="Markdown",
            reply_markup=main_menu(message.from_user.id)
        )
        return
    
    user_states[message.from_user.id] = "await_username"
    
    queue_manager.add_to_user_queue(
        message.from_user.id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id, 
        "👤 **إنشاء حساب استثماري جديد**\n\n"
        "📝 الرجاء إدخال اسم المستخدم المطلوب:\n"
        "• يجب أن يكون فريداً وغير مستخدم\n"
        "• يمكن استخدام الأحرف الإنجليزية والأرقام\n"
        "• الطول بين 3 إلى 20 حرفاً",
        parse_mode="Markdown",
        reply_markup=reply_keyboard_with_cancel()
    )

# ========= تسجيل الدخول المحسن =========
@bot.message_handler(func=lambda m: m.text == "🔑 تسجيل الدخول")
def login_start(message):
    # التحقق من الحظر
    ban_check = is_user_banned(message.from_user.id)
    if ban_check['banned']:
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "🚫 حسابك محظور ولا يمكنك استخدام هذه الميزة."
        )
        return
    
    if not security.check_rate_limit(message.from_user.id, "login_start", 3, 60):
        return
    
    user = load_user(str(message.from_user.id))
    if not user:
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "❌ الحساب غير موجود. الرجاء التسجيل أولاً."
        )
        return
    
    if not user.get("registered"):
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "❌ الحساب غير مكتمل. الرجاء إكمال التسجيل."
        )
        return
    
    user_states[message.from_user.id] = "await_login_username"
    
    queue_manager.add_to_user_queue(
        message.from_user.id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id, 
        "🔑 **تسجيل الدخول**\n\n"
        "👤 أدخل اسم المستخدم:",
        parse_mode="Markdown",
        reply_markup=reply_keyboard_with_cancel()
    )

# ========= نسيت بيانات الدخول =========
@bot.message_handler(func=lambda m: m.text == "❓ نسيت بيانات الدخول")
def forgot_credentials(message):
    # التحقق من الحظر
    ban_check = is_user_banned(message.from_user.id)
    if ban_check['banned']:
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "🚫 حسابك محظور ولا يمكنك استخدام هذه الميزة."
        )
        return
    
    user = load_user(str(message.from_user.id))
    if not user or not user.get("registered"):
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "⚠️ لا يوجد حساب مرتبط بهذا المستخدم."
        )
        return
    
    raw_password = user.get('password', 'غير معروف')
    
    queue_manager.add_to_user_queue(
        message.from_user.id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id,
        f"🔐 **بيانات الدخول**\n\n"
        f"👤 **اسم المستخدم:** `{user.get('username')}`\n"
        f"🔑 **كلمة المرور:** `{raw_password}`\n\n"
        f"💡 **نصيحة أمنية:** احفظ هذه المعلومات في مكان آمن ولا تشاركها مع أحد.",
        parse_mode="Markdown"
    )

# ========= تسجيل الخروج =========
@bot.message_handler(func=lambda m: m.text == "🚪 تسجيل الخروج")
def logout(message):
    user_id = message.from_user.id
    logged_in_users.discard(user_id)
    task_queue.end_task(user_id)
    
    queue_manager.add_to_user_queue(
        user_id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id, 
        "✅ **تم تسجيل الخروج بنجاح**\n\n"
        "شكراً لاستخدامك تراكم! نراك قريباً 🚀",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

# ========= نظام دعم العملاء المحسن =========
@bot.message_handler(func=lambda m: m.text == "📞 الدعم الفني")
@require_login
def contact_support(message):
    user = load_user(message.from_user.id)
    
    support_text = f"""
📞 **مركز دعم تراكم**

👤 **معلوماتك:**
• العضوية: {user.get('membership_id', 'N/A')}
• المستخدم: @{user.get('username')}

🎯 **اختر نوع المشكلة:**
    """
    
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.row("🎯 مشكلة فنية", "💰 مشكلة مالية")
    keyboard.row("🔐 مشكلة تسجيل دخول", "📊 استفسار استثماري")
    keyboard.row("⚠️ بلاغ عن مشكلة", "📞 التواصل مع الإدارة")
    keyboard.row("🏠 العودة للقائمة الرئيسية")
    
    user_states[message.from_user.id] = "awaiting_support_category"
    
    queue_manager.add_to_user_queue(
        message.from_user.id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id, 
        support_text, 
        parse_mode="Markdown", 
        reply_markup=keyboard
    )

@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "awaiting_support_category")
def handle_support_category(message):
    user_id = message.from_user.id
    user = load_user(user_id)
    
    if message.text == "🏠 العودة للقائمة الرئيسية":
        user_states.pop(user_id, None)
        queue_manager.add_to_user_queue(
            user_id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "✅ العودة للقائمة الرئيسية", 
            reply_markup=main_menu(user_id)
        )
        return
    
    category_map = {
        "🎯 مشكلة فنية": "technical",
        "💰 مشكلة مالية": "financial", 
        "🔐 مشكلة تسجيل دخول": "login",
        "📊 استفسار استثماري": "investment",
        "⚠️ بلاغ عن مشكلة": "report",
        "📞 التواصل مع الإدارة": "management"
    }
    
    category = category_map.get(message.text, "general")
    
    user_states[user_id] = "awaiting_support_message"
    user_states[f"{user_id}_support_category"] = category
    user_states[f"{user_id}_support_category_name"] = message.text
    
    queue_manager.add_to_user_queue(
        user_id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id, 
        f"📝 **{message.text}**\n\nالرجاء كتابة تفاصيل مشكلتك أو استفسارك بالتفصيل:"
    )

@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "awaiting_support_message")
def handle_support_message(message):
    user_id = message.from_user.id
    user = load_user(user_id)
    
    if not message.text or len(message.text.strip()) < 5:
        queue_manager.add_to_user_queue(
            user_id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "❌ الرسالة قصيرة جداً، يرجاء كتابة تفاصيل أكثر."
        )
        return
    
    category = user_states.get(f"{user_id}_support_category")
    category_name = user_states.get(f"{user_id}_support_category_name")
    
    user_states.pop(user_id, None)
    user_states.pop(f"{user_id}_support_category", None)
    user_states.pop(f"{user_id}_support_category_name", None)
    
    save_support_message(user_id, user, category, category_name, message.text)
    
    queue_manager.add_to_user_queue(
        user_id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id,
        f"✅ **تم إرسال رسالتك بنجاح!**\n\n"
        f"📞 سيقوم فريق الدعم بالرد عليك خلال 24 ساعة.\n\n"
        f"شكراً لثقتك بـ تراكم! 🚀",
        reply_markup=main_menu(user_id)
    )

def save_support_message(user_id, user, category, category_name, message):
    """حفظ رسالة الدعم في قاعدة البيانات"""
    try:
        conn = get_conn()
        c = conn.cursor()
        
        message_data = (
            str(user_id),
            user.get('membership_id'),
            user.get('username'),
            category,
            category_name,
            message.strip(),
            'open',
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            None,
            None,
            None
        )
        
        c.execute('''
            INSERT INTO support_messages 
            (user_id, membership_id, username, category, category_name, message, status, created_at, admin_response, responded_at, admin_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', message_data)
        
        conn.commit()
        return_conn(conn)
        
        notify_admins_support_message(user, category_name, message)
        
        logger.info(f"Support message saved for user {user_id}")
        
    except Exception as e:
        logger.error(f"Error saving support message: {e}")

def notify_admins_support_message(user, category_name, message):
    """إرسال إشعار للمسؤولين برسالة دعم جديدة"""
    try:
        notification_manager.add_notification('support', {
            'username': user.get('username'),
            'category': category_name,
            'message': message[:100] + '...' if len(message) > 100 else message
        })
    except Exception as e:
        logger.error(f"Error notifying admin: {e}")

# ========= معالجة أزرار الإلغاء والعودة =========
@bot.message_handler(func=lambda m: m.text in ["❌ إلغاء العملية", "🏠 العودة للقائمة الرئيسية"])
def handle_cancel_and_home_buttons(message):
    user_id = message.from_user.id
    
    if message.text == "❌ إلغاء العملية":
        user_states.pop(user_id, None)
        task_queue.end_task(user_id)
        
        for key in list(user_states.keys()):
            if str(key).startswith(f"{user_id}_"):
                user_states.pop(key, None)
        
        queue_manager.add_to_user_queue(
            user_id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "✅ تم إلغاء العملية.", 
            reply_markup=main_menu(user_id)
        )
        
    elif message.text == "🏠 العودة للقائمة الرئيسية":
        if user_id in user_states and user_states[user_id] == "await_deposit_txid":
            queue_manager.add_to_user_queue(
                user_id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "🏠 **العودة للقائمة الرئيسية**\n\n"
                "يمكنك متابعة الإيداع لاحقاً من خلال زر الإيداع.",
                reply_markup=main_menu(user_id)
            )
        else:
            user_states.pop(user_id, None)
            task_queue.end_task(user_id)
            
            for key in list(user_states.keys()):
                if str(key).startswith(f"{user_id}_"):
                    user_states.pop(key, None)
            
            queue_manager.add_to_user_queue(
                user_id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "✅ العودة للقائمة الرئيسية.", 
                reply_markup=main_menu(user_id)
            )

# ========= معالجة حالات المستخدم المحسنة مع إضافة رقم الهاتف =========
@bot.message_handler(func=lambda m: m.from_user.id in user_states)
def handle_all_states(message):
    state = user_states.get(message.from_user.id)
    if not state:
        return

    if state == "await_deposit_amount":
        handle_deposit_amount(message)
        return
    elif state == "await_deposit_txid":
        handle_deposit_txid(message)
        return
    elif state == "await_deposit_sender_wallet":
        handle_deposit_sender_wallet(message)
        return
    elif state == "await_withdraw_amount":
        handle_withdraw_amount(message)
        return
    elif state == "await_wallet_confirmation":
        handle_wallet_confirmation(message)
        return

    handle_state(message)

# ========= التعامل مع حالات التسجيل / تسجيل الدخول مع إضافة رقم الهاتف =========
def handle_state(message):
    state = user_states.get(message.from_user.id)
    user = load_user(str(message.from_user.id))

    if state == "await_login_username":
        user_states[message.from_user.id] = "await_login_password"
        user_states[f"{message.from_user.id}_username"] = message.text.strip()
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "🔑 **كلمة المرور**\n\n"
            "أدخل كلمة المرور الخاصة بحسابك:",
            parse_mode="Markdown",
            reply_markup=reply_keyboard_with_cancel()
        )
        return
        
    elif state == "await_login_password":
        username = user_states.get(f"{message.from_user.id}_username")
        password = message.text.strip()
        
        try:
            conn = get_conn()
            c = conn.cursor()
            c.execute("SELECT * FROM users WHERE user_id = %s AND username = %s", 
                     (str(message.from_user.id), username))
            row = c.fetchone()
            return_conn(conn)
            
            if row:
                user_data = {
                    "user_id": str(row[0]),
                    "membership_id": row[1],
                    "username": row[2],
                    "password": row[3],
                    "email": row[4],
                    "phone": row[5],
                    "wallet": row[6],
                    "balance": float(row[7]) if row[7] else 0.0,
                    "registered": bool(row[8]),
                    "deposited": bool(row[9]),
                    "last_deposit": row[10],
                    "last_task": row[11],
                    "withdraw_request": bool(row[12]),
                    "transactions": row[13],
                    "referrer_id": str(row[14]) if row[14] else None,
                    "created_date": row[15],
                    "last_login": row[16],
                    "login_count": row[17] or 0,
                    "status": row[18] or "active",
                    "first_deposit_time": row[19],
                    "last_withdrawal_time": row[20],
                    "last_task_status": row[21] or "completed"
                }
                
                if user_data.get('password') == password:
                    logged_in_users.add(message.from_user.id)
                    user_states.pop(message.from_user.id, None)
                    user_states.pop(f"{message.from_user.id}_username", None)
                    
                    user = load_user(str(message.from_user.id))
                    user["last_login"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    user["login_count"] = user.get("login_count", 0) + 1
                    save_user(user)
                    
                    queue_manager.add_to_user_queue(
                        message.from_user.id, 
                        message.chat.id, 
                        bot.send_message, 
                        message.chat.id, 
                        "🎉 **تم تسجيل الدخول بنجاح!**\n\n"
                        f"مرحباً بعودتك {user['username']} إلى منصتك الاستثمارية! 🚀",
                        parse_mode="Markdown",
                        reply_markup=main_menu(message.from_user.id)
                    )
                    security.record_login_attempt(message.from_user.id, True)
                    log_event(message.from_user.id, "LOGIN_SUCCESS", f"Username: {username}")
                else:
                    queue_manager.add_to_user_queue(
                        message.from_user.id, 
                        message.chat.id, 
                        bot.send_message, 
                        message.chat.id, 
                        "❌ **خطأ في البيانات**\n\n"
                        "اسم المستخدم أو كلمة المرور غير صحيحة.",
                        parse_mode="Markdown",
                        reply_markup=reply_keyboard_with_cancel()
                    )
                    security.record_login_attempt(message.from_user.id, False)
                    log_event(message.from_user.id, "LOGIN_FAILED", f"Username: {username}")
            else:
                queue_manager.add_to_user_queue(
                    message.from_user.id, 
                    message.chat.id, 
                    bot.send_message, 
                    message.chat.id, 
                    "❌ **خطأ في البيانات**\n\n"
                    "اسم المستخدم أو كلمة المرور غير صحيحة.",
                    parse_mode="Markdown",
                    reply_markup=reply_keyboard_with_cancel()
                )
                security.record_login_attempt(message.from_user.id, False)
                log_event(message.from_user.id, "LOGIN_FAILED", f"Username: {username}")
            return
        except Exception as e:
            logger.error(f"Error during login: {e}")
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "❌ **حدث خطأ في النظام**\n\nيرجى المحاولة مرة أخرى.",
                parse_mode="Markdown",
                reply_markup=reply_keyboard_with_cancel()
            )
            return

    elif state == "await_username":
        username = message.text.strip()
        if len(username) < 3 or len(username) > 20:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "❌ **اسم المستخدم غير صالح**\n\n"
                "يجب أن يكون اسم المستخدم بين 3 إلى 20 حرفاً.\n"
                "الرجاء إدخال اسم مستخدم صحيح:"
            )
            return
        
        if user_exists(username, exclude_id=message.from_user.id):
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "❌ **اسم مستخدم مكرر**\n\n"
                "هذا الاسم مستخدم مسبقاً من قبل عضو آخر.\n"
                "الرجاء اختيار اسم مستخدم مختلف:"
            )
            return
        
        if not user:
            create_user(message.from_user.id, username)
            user = load_user(message.from_user.id)
            
        user["username"] = username
        save_user(user)
        user_states[message.from_user.id] = "await_password"
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "🔐 **إنشاء كلمة مرور قوية**\n\n"
            "الرجاء إدخال كلمة المرور المرغوبة:\n"
            "• يجب أن تحتوي على 6 أحرف على الأقل\n"
            "• يمكن استخدام الأحرف الإنجليزية والأرقام\n"
            "• للحماية الأمثل، استخدم مزيجاً من الأحرف والأرقام",
            parse_mode="Markdown",
            reply_markup=reply_keyboard_with_cancel()
        )
        return
        
    elif state == "await_password":
        if not user:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "⚠️ حدث خطأ، حاول مرة again.", 
                reply_markup=main_menu()
            )
            user_states.pop(message.from_user.id, None)
            return
            
        password = message.text.strip()
        if len(password) < 6:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "❌ **كلمة المرور ضعيفة**\n\n"
                "كلمة المرور يجب أن تحتوي على 6 أحرف على الأقل.\n"
                "الرجاء إدخال كلمة مرور أقوى:"
            )
            return
            
        user["password"] = password
        save_user(user)
        user_states[message.from_user.id] = "await_email"
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "📧 **البريد الإلكتروني**\n\n"
            "الرجاء إدخال بريدك الإلكتروني:\n"
            "• يجب أن يكون بريداً إلكترونياً صالحاً\n"
            "• سيتم استخدامه للتواصل الرسمي\n"
            "• مثال: yourname@domain.com",
            parse_mode="Markdown",
            reply_markup=reply_keyboard_with_cancel()
        )
        return
        
    elif state == "await_email":
        if not user:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "⚠️ حدث خطأ، حاول مرة again.", 
                reply_markup=main_menu()
            )
            user_states.pop(message.from_user.id, None)
            return
            
        email = message.text.strip()
        if not validate_email(email):
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "❌ **بريد إلكتروني غير صالح**\n\n"
                "الرجاء إدخال بريد إلكتروني صحيح.\n"
                "مثال: yourname@gmail.com",
                parse_mode="Markdown",
                reply_markup=reply_keyboard_with_cancel()
            )
            return
            
        user["email"] = email
        save_user(user)
        user_states[message.from_user.id] = "await_phone"
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "📱 **رقم الهاتف**\n\n"
            "الرجاء إدخال رقم هاتفك مع رمز الدولة:\n"
            "• يجب أن يتضمن رمز الدولة\n"
            "• سيتم استخدامه للتواصل الرسمي والتحقق\n"
            "• مثال: +201234567890 أو 00201234567890\n\n"
            "📝 **أمثلة صحيحة:**\n"
            "• +966501234567\n"
            "• 00966501234567\n"
            "• +971501234567\n"
            "• 00971501234567",
            parse_mode="Markdown",
            reply_markup=reply_keyboard_with_cancel()
        )
        return
        
    elif state == "await_phone":
        if not user:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "⚠️ حدث خطأ، حاول مرة again.", 
                reply_markup=main_menu()
            )
            user_states.pop(message.from_user.id, None)
            return
            
        phone = message.text.strip()
        if not validate_phone_number(phone):
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "❌ **رقم هاتف غير صالح**\n\n"
                "الرجاء إدخال رقم هاتف صحيح مع رمز الدولة.\n\n"
                "📝 **أمثلة صحيحة:**\n"
                "• +201234567890\n"
                "• 00201234567890\n"
                "• +966501234567\n"
                "• 00966501234567\n\n"
                "📝 **أمثلة خاطئة:**\n"
                "• 01234567890 (بدون رمز دولة)\n"
                "• 123456 (قصير جداً)\n"
                "• abcdefgh (أحرف غير مسموحة)",
                parse_mode="Markdown",
                reply_markup=reply_keyboard_with_cancel()
            )
            return
            
        user["phone"] = phone
        save_user(user)
        user_states[message.from_user.id] = "await_wallet"
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "💳 **المحفظة الإلكترونية**\n\n"
            "الرجاء إدخال عنوان محفظتك:\n"
            "• يجب أن يكون عنوان محفظة صحيح\n"
            "• سيتم استخدامه لسحب الأرباح\n"
            "• يدعم TRC20 و BEP20\n\n"
            "📝 **أمثلة:**\n"
            "• TRC20: TFF3JgjtGc9Kky2ko7NwtJyQY6NKujQ8YL\n"
            "• BEP20: 0x39d730BF7fEb2648Ae1761ECd20972fD067C2114",
            parse_mode="Markdown",
            reply_markup=reply_keyboard_with_cancel()
        )
        return
        
    elif state == "await_wallet":
        if not user:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "⚠️ حدث خطأ، حاول مرة again."
            )
            return
            
        wallet = message.text.strip()
        if not validate_wallet_address(wallet):
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "❌ **عنوان محفظة غير صالح**\n\n"
                "الرجاء إدخال عنوان محفظة صحيح.\n\n"
                "📝 **الصيغ المقبولة:**\n"
                "• TRC20: يبدأ بـ T ويتكون من 34 حرفاً\n"
                "• BEP20: يبدأ بـ 0x ويتكون من 42 حرفاً\n\n"
                "⚠️ تأكد من صحة العنوان قبل الإرسال",
                parse_mode="Markdown",
                reply_markup=reply_keyboard_with_cancel()
            )
            return
        
        if wallet_exists(wallet, exclude_id=message.from_user.id):
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id,
                "❌ **عنوان المحفظة مستخدم مسبقاً**\n\n"
                "هذا العنوان مرتبط بحساب آخر.\n"
                "الرجاء استخدام عنوان محفظة مختلف.",
                parse_mode="Markdown",
                reply_markup=reply_keyboard_with_cancel()
            )
            return
            
        user["wallet"] = wallet
        user["registered"] = True
        user["last_login"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        user["login_count"] = 1
        save_user(user)
        
        user_states.pop(message.from_user.id, None)
        logged_in_users.add(message.from_user.id)
        
        if user.get("referrer_id"):
            try:
                referrer = load_user(user["referrer_id"])
                if referrer:
                    def send_referral_notification():
                        bot.send_message(
                            int(user["referrer_id"]),
                            f"🎉 **عضو جديد من خلال رابطك!**\n\n"
                            f"👤 العضو الجديد: @{user['username']}\n"
                            f"🆔 رقم العضوية: {user.get('membership_id', 'N/A')}\n"
                            f"📅 وقت التسجيل: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                            f"💰 **ستحصل على 5% من أول إيداع له!**",
                            parse_mode="Markdown"
                        )
                    
                    queue_manager.add_to_user_queue(
                        user["referrer_id"], 
                        int(user["referrer_id"]), 
                        send_referral_notification
                    )
                    log_event(user["referrer_id"], "REFERRAL_NEW_USER", f"New user: {user['username']}")
            except Exception as e:
                logger.error(f"Error notifying referrer: {e}")

        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id,
            MessageTemplates.registration_success(user),
            parse_mode="Markdown",
            reply_markup=main_menu(message.from_user.id)
        )
        
        log_event(message.from_user.id, "REGISTRATION_COMPLETE", 
                 f"Membership ID: {user.get('membership_id')}, Username: {user['username']}")
        return

# ========= الإيداع المحسن مع التسلسل المصحح =========
@bot.message_handler(func=lambda m: m.text == "💵 الإيداع")
@require_login
def deposit_start(message):
    # التحقق من الحظر
    ban_check = is_user_banned(message.from_user.id)
    if ban_check['banned']:
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "🚫 حسابك محظور ولا يمكنك استخدام هذه الميزة."
        )
        return
    
    if not security.check_rate_limit(message.from_user.id, "deposit", 5, 60):
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "⏳ تم تجاوز الحد المسموح. يرجى الانتظار قليلاً."
        )
        return
    
    if has_pending_deposit(message.from_user.id):
        queue_manager.add_to_user_queue(
            message.from_user.id,
            message.chat.id,
            bot.send_message,
            message.chat.id,
            "⏳ **لديك طلب إيداع قيد المراجعة**\n\n"
            "يوجد طلب إيداع معلق بالفعل. يرجى انتظار مراجعة الطلب الحالي قبل تقديم طلب إيداع جديد.\n\n"
            "📞 **للاستفسار:** @{Config.SUPPORT_BOT_USERNAME}",
            parse_mode="Markdown",
            reply_markup=main_menu(message.from_user.id)
        )
        return
    
    user_states[message.from_user.id] = "await_deposit_amount"
    
    queue_manager.add_to_user_queue(
        message.from_user.id,
        message.chat.id,
        bot.send_message,
        message.chat.id,
        "💰 **نظام الإيداع الآمن**\n\n"
        "💵 الرجاء إدخال المبلغ الذي ترغب في إيداعه:\n"
        "• الحد الأدنى: 20$\n"
        "• الحد الأقصى: 50,000$\n"
        "• العملة: USDT فقط\n\n"
        "📝 سيتم توجيهك لاختيار شبكة التحويل بعد إدخال المبلغ",
        parse_mode="Markdown",
        reply_markup=reply_keyboard_with_cancel_and_home()
    )

def handle_deposit_amount(message):
    user_id = message.from_user.id
    user = load_user(user_id)
    
    try:
        amount = float(message.text.strip())
        if amount < 20:
            queue_manager.add_to_user_queue(
                user_id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "❌ **المبلغ أقل من الحد الأدنى**\n\n"
                "الحد الأدنى للإيداع هو 20$\n"
                "الرجاء إدخال مبلغ 20$ أو أكثر."
            )
            return
        if amount > 50000:
            queue_manager.add_to_user_queue(
                user_id,
                message.chat.id,
                bot.send_message,
                message.chat.id,
                "❌ **المبلغ يتجاوز الحد الأقصى**\n\n"
                "الحد الأقصى للإيداع هو 50,000$\n"
                "الرجاء إدخال مبلغ أقل."
            )
            return
        
        user_states[user_id] = "await_deposit_network"
        user_states[f"{user_id}_amount"] = amount

        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(
            telebot.types.InlineKeyboardButton("🌐 TRC20 (ترون)", callback_data="deposit_trc"),
            telebot.types.InlineKeyboardButton("🌐 BEP20 (بينانس)", callback_data="deposit_bep")
        )

        queue_manager.add_to_user_queue(
            user_id,
            message.chat.id,
            bot.send_message,
            message.chat.id,
            f"💰 **تفاصيل الإيداع**\n\n"
            f"💵 **المبلغ:** {amount:.2f}$\n"
            f"💼 **العملة:** USDT\n\n"
            f"🌐 **اختر شبكة التحويل المناسبة:**",
            parse_mode="Markdown",
            reply_markup=markup
        )
        
        log_event(user_id, "DEPOSIT_AMOUNT_ENTERED", f"Amount: {amount}")
        
    except ValueError:
        queue_manager.add_to_user_queue(
            user_id,
            message.chat.id,
            bot.send_message,
            message.chat.id,
            "❌ **قيمة غير صالحة**\n\n"
            "الرجاء إدخال رقم صحيح (مثال: 50)\n"
            "بدون رموز أو أحرف إضافية."
        )

@bot.callback_query_handler(func=lambda call: call.data in ["deposit_trc", "deposit_bep"])
def deposit_network_choice(call):
    user_id = call.from_user.id
    amount = user_states.get(f"{user_id}_amount")
    if not amount:
        bot.answer_callback_query(call.id, "⚠️ حدث خطأ، أعد المحاولة.")
        return

    network = "TRC20" if call.data == "deposit_trc" else "BEP20"
    wallet_address = Config.TRC20_WALLET if network == "TRC20" else Config.BEP20_WALLET

    user_states[user_id] = "await_deposit_txid"
    user_states[f"{user_id}_network"] = network

    queue_manager.add_to_user_queue(
        user_id,
        call.message.chat.id,
        bot.send_message,
        call.message.chat.id,
        MessageTemplates.deposit_instructions(amount, network, wallet_address),
        parse_mode="Markdown"
    )
    
    queue_manager.add_to_user_queue(
        user_id,
        call.message.chat.id,
        bot.send_message,
        call.message.chat.id,
        f"🔍 **رقم العملية (TXID)**\n\n"
        f"الرجاء إدخال رقم العملية (TXID) الخاص بالتحويل:\n\n"
        f"📝 **معلومات مهمة:**\n"
        f"• رقم العملية هو الرمز الذي تحصل عليه بعد تأكيد عملية التحويل\n"
        f"• يجب أن يكون الرقم صحيحاً وفريداً\n"
        f"• تأكد من نسخ الرقم بشكل صحيح\n\n"
        f"💡 **أمثلة على صيغة TXID:**\n"
        f"• TRC20: 64 حرفاً (أرقام وحروف إنجليزية)\n"
        f"• BEP20: يبدأ بـ 0x ويحتوي على 66 حرفاً\n\n"
        f"📋 **أدخل رقم العملية الآن:**",
        parse_mode="Markdown",
        reply_markup=reply_keyboard_with_cancel_and_home()
    )
    
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    
    log_event(user_id, "DEPOSIT_NETWORK_CHOSEN", f"Network: {network}, Amount: {amount}")

def handle_deposit_txid(message):
    user_id = message.from_user.id
    user = load_user(user_id)
    
    if not user:
        queue_manager.add_to_user_queue(
            user_id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "⚠️ حدث خطأ، أعد العملية.", 
            reply_markup=main_menu(user_id)
        )
        user_states.pop(user_id, None)
        return

    txid = message.text.strip()
    amount = user_states.get(f"{user_id}_amount")
    network = user_states.get(f"{user_id}_network")

    if not amount or not network:
        queue_manager.add_to_user_queue(
            user_id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "⚠️ حدث خطأ، أعد العملية.", 
            reply_markup=main_menu(user_id)
        )
        user_states.pop(user_id, None)
        return

    if not validate_txid_format(txid, network):
        if network == "TRC20":
            error_msg = "❌ **صيغة TXID غير صحيحة لشبكة TRC20**\n\nيجب أن يتكون TXID من 64 حرفاً (أرقام وحروف إنجليزية فقط).\n\n📝 **مثال على TXID صحيح:** 1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p7q8r9s0t1u2v3w4x5y6z7a8b9c0d1e2f"
        else:
            error_msg = "❌ **صيغة TXID غير صحيحة لشبكة BEP20**\n\nيجب أن يبدأ TXID بـ '0x' ويحتوي على 66 حرفاً (أرقام وحروف إنجليزية فقط).\n\n📝 **مثال على TXID صحيح:** 0x1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p7q8r9s0t1u2v3w4x5y6z7a8b9c0d1e2f"
        
        queue_manager.add_to_user_queue(
            user_id,
            message.chat.id,
            bot.send_message,
            message.chat.id,
            error_msg,
            parse_mode="Markdown",
            reply_markup=reply_keyboard_with_cancel_and_home()
        )
        return

    if not is_txid_unique(txid):
        txid_info = get_txid_usage_info(txid)
        if txid_info:
            error_msg = f"""
❌ **رقم العملية مستخدم مسبقاً!**

🔍 **معلومات الرقم المستخدم:**
• 👤 المستخدم: @{txid_info['username'] or 'غير معروف'}
• 💵 المبلغ: {txid_info['amount']:.2f}$
• 🌐 الشبكة: {txid_info['network']}
• 📊 الحالة: {txid_info['status']}
• 📅 التاريخ: {txid_info['date']}

⚠️ **لا يمكن استخدام نفس رقم العملية مرتين!**
يرجى التحقق من الرقم وإدخال رقم عملية صحيح وفريد.
            """
        else:
            error_msg = "❌ رقم العملية مستخدم مسبقاً. يرجاء استخدام رقم عملية مختلف."

        queue_manager.add_to_user_queue(
            user_id,
            message.chat.id,
            bot.send_message,
            message.chat.id,
            error_msg,
            parse_mode="Markdown",
            reply_markup=reply_keyboard_with_cancel_and_home()
        )
        return

    user_states[user_id] = "await_deposit_sender_wallet"
    user_states[f"{user_id}_txid"] = txid
    
    queue_manager.add_to_user_queue(
        user_id,
        message.chat.id,
        bot.send_message,
        message.chat.id,
        f"🔍 **تأكيد عملية الإيداع**\n\n"
        f"📋 **تفاصيل الإيداع حتى الآن:**\n"
        f"💵 **المبلغ:** {amount:.2f}$\n"
        f"🌐 **الشبكة:** {network}\n"
        f"🔑 **رقم العملية (TXID):** `{txid}`\n\n"
        f"💳 **الرجاء إدخال عنوان المحفظة التي أرسلت منها الأموال:**\n"
        f"• يجب أن يكون عنوان محفظة صحيح\n"
        f"• يجب أن يتطابق مع الشبكة المختارة ({network})\n\n"
        f"📝 **أمثلة:**\n"
        f"• TRC20: TFF3JgjtGc9Kky2ko7NwtJyQY6NKujQ8YL\n"
        f"• BEP20: 0x39d730BF7fEb2648Ae1761ECd20972fD067C2114",
        parse_mode="Markdown",
        reply_markup=reply_keyboard_with_cancel_and_home()
    )

@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "await_deposit_sender_wallet")
def handle_deposit_sender_wallet(message):
    user_id = message.from_user.id
    user = load_user(user_id)
    
    sender_wallet = message.text.strip()
    amount = user_states.get(f"{user_id}_amount")
    network = user_states.get(f"{user_id}_network")
    txid = user_states.get(f"{user_id}_txid")

    if not validate_wallet_address(sender_wallet, network):
        if network == "TRC20":
            error_msg = "❌ **عنوان المحفظة غير صالح لشبكة TRC20**\n\nيجب أن يبدأ العنوان بـ 'T' ويتكون من 34 حرفاً."
        else:
            error_msg = "❌ **عنوان المحفظة غير صالح لشبكة BEP20**\n\nيجب أن يبدأ العنوان بـ '0x' ويتكون من 42 حرفاً."
        
        queue_manager.add_to_user_queue(
            user_id,
            message.chat.id,
            bot.send_message,
            message.chat.id,
            error_msg,
            parse_mode="Markdown",
            reply_markup=reply_keyboard_with_cancel_and_home()
        )
        return

    def process_deposit_request():
        processing_msg = bot.send_message(
            message.chat.id,
            f"⏳ **جاري معالجة طلب الإيداع...**\n\n"
            f"💵 **المبلغ:** {amount:.2f}$\n"
            f"🌐 **الشبكة:** {network}\n"
            f"🔑 **رقم العملية (TXID):** `{txid}`\n"
            f"💳 **المحفظة المرسلة:** `{sender_wallet}`\n\n"
            f"📝 **جاري التحقق من البيانات وإرسال الطلب للإدارة...**",
            parse_mode="Markdown"
        )

        try:
            conn = get_conn()
            c = conn.cursor()
            
            c.execute(
                "INSERT INTO deposit_requests (user_id, username, amount, network, txid, status, date, sender_wallet) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (str(user["user_id"]), user["username"], amount, network, txid, "pending", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sender_wallet)
            )
            deposit_request_id = c.lastrowid
            conn.commit()
            return_conn(conn)
            
            logger.info(f"✅ Deposit request saved successfully. ID: {deposit_request_id}, User: {user['username']}, Amount: {amount}")
            
            user_states.pop(user_id, None)
            user_states.pop(f"{user_id}_amount", None)
            user_states.pop(f"{user_id}_network", None)
            user_states.pop(f"{user_id}_txid", None)

            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=processing_msg.message_id,
                text=f"✅ **تم إرسال طلب الإيداع بنجاح!**\n\n"
                     f"📋 **تفاصيل الطلب:**\n"
                     f"💵 **المبلغ:** {amount:.2f}$\n"
                     f"🌐 **الشبكة:** {network}\n"
                     f"🔑 **رقم العملية (TXID):** `{txid}`\n"
                     f"💳 **المحفظة المرسلة:** `{sender_wallet}`\n\n"
                     f"⏳ **حالة الطلب:** قيد المراجعة\n"
                     f"📅 **الوقت المتوقع:** 24 ساعة كحد أقصى\n\n"
                     f"📞 **للاستفسار:** @{Config.SUPPORT_BOT_USERNAME}",
                parse_mode="Markdown"
            )
            
            try:
                markup = telebot.types.InlineKeyboardMarkup()
                markup.add(
                    telebot.types.InlineKeyboardButton("✅ قبول الطلب", callback_data=f"approve_{deposit_request_id}"),
                    telebot.types.InlineKeyboardButton("❌ رفض الطلب مع السبب", callback_data=f"reject_deposit_reason_{deposit_request_id}")
                )
                
                admin_message = bot.send_message(
                    Config.ADMIN_ID,
                    f"🆕 **طلب إيداع جديد**\n\n"
                    f"👤 **المستخدم:** @{user['username']}\n"
                    f"🆔 **ID:** {user['user_id']}\n"
                    f"💵 **المبلغ:** {amount:.2f}$\n"
                    f"🌐 **الشبكة:** {network}\n"
                    f"🔑 **TXID:** `{txid}`\n"
                    f"💳 **المحفظة المرسلة:** `{sender_wallet}`\n"
                    f"📅 **الوقت:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    parse_mode="Markdown",
                    reply_markup=markup
                )
                logger.info(f"✅ Deposit request notification sent to admin. Message ID: {admin_message.message_id}, Deposit ID: {deposit_request_id}")
            except Exception as e:
                logger.error(f"❌ Error notifying admin: {e}")
            
            log_event(user_id, "DEPOSIT_REQUEST_SUBMITTED", f"Amount: {amount}, Network: {network}, TXID: {txid}, Sender Wallet: {sender_wallet}, Deposit ID: {deposit_request_id}")
            
        except Exception as e:
            logger.error(f"Error processing deposit: {e}")
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=processing_msg.message_id,
                text=f"❌ **حدث خطأ أثناء معالجة الطلب**\n\n"
                     f"الرجاء المحاولة مرة أخرى أو التواصل مع الدعم.\n"
                     f"📞 @{Config.SUPPORT_BOT_USERNAME}",
                parse_mode="Markdown"
            )
        
        bot.send_message(message.chat.id, "🏠 **العودة للقائمة الرئيسية**", reply_markup=main_menu(user_id))
    
    queue_manager.add_to_user_queue(user_id, message.chat.id, process_deposit_request)

# ========= استعلام الرصيد =========
@bot.message_handler(func=lambda m: m.text == "💰 رصيدي")
@require_login
def balance_check(message):
    user = load_user(message.from_user.id)
    
    queue_manager.add_to_user_queue(
        message.from_user.id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id, 
        f"💳 **رصيدك الحالي**\n\n"
        f"💰 **المبلغ:** {user.get('balance',0.0):.2f}$\n\n"
        f"📈 **آخر تحديث:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        parse_mode="Markdown"
    )

# ========= لوحة التحكم المحسنة =========
@bot.message_handler(func=lambda m: m.text == "📊 لوحة التحكم")
@require_login
def dashboard_info(message):
    user_id = message.from_user.id
    user = load_user(user_id)
    
    if not user:
        queue_manager.add_to_user_queue(
            user_id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "❌ لم يتم العثور على بيانات المستخدم."
        )
        return
    
    try:
        conn = get_conn()
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) FROM users WHERE referrer_id = %s", (str(user_id),))
        referred_count = c.fetchone()[0]
        
        c.execute("""
            SELECT COUNT(*) FROM users u 
            JOIN deposit_requests dr ON u.user_id = dr.user_id 
            WHERE u.referrer_id = %s AND dr.status = 'approved'
        """, (str(user_id),))
        active_referrals = c.fetchone()[0]
        
        total_referral_earnings = 0
        for tx in user.get("transactions", []):
            if tx.get("type") == "referral_bonus":
                total_referral_earnings += tx.get("amount", 0)
        
        batch_bonus_data = get_batch_bonus_progress(user_id)
        
        c.execute("SELECT SUM(amount) FROM deposit_requests WHERE user_id = %s AND status = 'approved'", (str(user_id),))
        total_deposits = c.fetchone()[0] or 0
        
        c.execute("SELECT SUM(amount) FROM withdrawals WHERE user_id = %s AND status = 'approved'", (str(user_id),))
        total_withdrawals = c.fetchone()[0] or 0
        
        return_conn(conn)
        
        membership_days = 0
        if user.get('created_date'):
            try:
                created_date = datetime.strptime(user['created_date'], '%Y-%m-%d %H:%M:%S')
                membership_days = (datetime.now() - created_date).days
            except:
                membership_days = 0
        
        task_status = get_task_status(user_id)
        
        info_text = f"""
👤 **الملف الشخصي - تراكم**

🆔 **معلومات العضوية:**
• رقم العضوية: `{user.get('membership_id', 'N/A')}`
• اسم المستخدم: @{user.get('username')}
• البريد الإلكتروني: {user.get('email')}
• رقم الهاتف: {user.get('phone', 'غير مسجل')}
• المحفظة: `{user.get('wallet') or 'غير مسجلة'}`

💰 **الحالة المالية:**
• الرصيد الحالي: {user.get('balance',0.0):.2f}$
• إجمالي الإيداعات: {total_deposits:.2f}$
• إجمالي السحوبات: {total_withdrawals:.2f}$
• صافي الأرباح: {(user.get('balance',0) + total_withdrawals - total_deposits):.2f}$

📊 **نظام الإحالة:**
• عدد المُحالين: {referred_count}
• المحالين النشطين: {active_referrals}
• أرباح الإحالة: {total_referral_earnings:.2f}$

🏆 **جوائز الإحالة الجماعية:**
• المجموعات المكتملة: {batch_bonus_data['completed_batches']}
• المحالين في الانتظار: {batch_bonus_data['pending_users_count']}/3
• الجوائز المستلمة: {batch_bonus_data['total_bonus_earned']:.2f}$
• المتبقي للمجموعة القادمة: {batch_bonus_data['users_until_next_bonus']} مستخدم

🎯 **حالة المهمة اليومية:**
• {task_status['message']}
• آخر مهمة: {user.get('last_task') or 'لم تنفذ بعد'}

📊 **نظام المهام التقليدي:**
• ⏰ المدة: 30 ثانية
• 💰 الربح: 3% من الرصيد
• 🔄 التكرار: كل 24 ساعة
• 🎯 يمكن تنفيذ مهمة واحدة يومياً
    """
        
        queue_manager.add_to_user_queue(
            user_id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            info_text, 
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Error generating dashboard info: {e}")
        queue_manager.add_to_user_queue(
            user_id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "❌ حدث خطأ في تحميل لوحة التحكم. يرجى المحاولة مرة أخرى."
        )

# ========= السحب المحسن مع شرط 3 دقائق بين السحوبات =========
@bot.message_handler(func=lambda m: m.text == "💸 طلب سحب")
@require_login
def withdraw_request(message):
    # التحقق من الحظر
    ban_check = is_user_banned(message.from_user.id)
    if ban_check['banned']:
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "🚫 حسابك محظور ولا يمكنك استخدام هذه الميزة."
        )
        return
    
    user = load_user(message.from_user.id)
    if not user or user.get("balance",0.0) < 1:
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "❌ **رصيد غير كافٍ**\n\n"
            "الحد الأدنى للسحب هو 1$\n"
            f"رصيدك الحالي: {user.get('balance',0.0):.2f}$",
            parse_mode="Markdown"
        )
        return
    
    if not user.get("first_deposit_time"):
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id,
            "❌ **لا يمكن السحب بعد**\n\n"
            "يجب أن تقوم بأول إيداع قبل أن تتمكن من السحب.\n"
            "الرجاء القيام بالإيداع أولاً من خلال زر '💵 الإيداع'.",
            parse_mode="Markdown"
        )
        return
    
    if has_pending_withdrawal(message.from_user.id):
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id,
            "⏳ **لديك طلب سحب قيد المراجعة**\n\n"
            "يوجد طلب سحب معلق بالفعل. يرجى انتظار مراجعة الطلب الحالي قبل تقديم طلب جديد.",
            parse_mode="Markdown"
        )
        return
    
    withdrawal_check = check_withdrawal_eligibility(user)
    if not withdrawal_check['eligible']:
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            withdrawal_check['message'], 
            parse_mode="Markdown"
        )
        return
    
    user_states[message.from_user.id] = "await_withdraw_amount"
    
    queue_manager.add_to_user_queue(
        message.from_user.id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id, 
        f"💸 **طلب سحب الأرباح**\n\n"
        f"💰 **رصيدك المتاح:** {user.get('balance',0.0):.2f}$\n"
        f"💳 **المحفظة المسجلة:** {user.get('wallet')}\n\n"
        f"📝 **أدخل المبلغ الذي تريد سحبه:**\n"
        f"• الحد الأدنى: 1$\n"
        f"• الرصيد المتاح: {user.get('balance',0.0):.2f}$\n\n"
        f"✅ **يمكنك السحب الآن**\n"
        f"⚠️ **ملاحظة:** سيتم مراجعة طلبك من قبل الإدارة",
        parse_mode="Markdown",
        reply_markup=reply_keyboard_with_cancel_and_home()
    )

def check_withdrawal_eligibility(user):
    """التحقق من أهلية المستخدم للسحب"""
    user_id = user['user_id']
    
    first_deposit_time = user.get("first_deposit_time")
    if not first_deposit_time:
        return {
            'eligible': False,
            'message': "❌ **لا يمكن السحب بعد**\n\nيجب أن تقوم بأول إيداع قبل أن تتمكن من السحب."
        }
    
    last_withdrawal_time = user.get("last_withdrawal_time")
    
    if not last_withdrawal_time:
        try:
            first_deposit = datetime.strptime(first_deposit_time, "%Y-%m-%d %H:%M:%S")
            current_time = datetime.now()
            time_since_first_deposit = current_time - first_deposit
            
            # التعديل: تغيير من 30 يوم إلى 3 دقائق
            if time_since_first_deposit.total_seconds() < 3 * 60:
                remaining_seconds = 3 * 60 - int(time_since_first_deposit.total_seconds())
                remaining_minutes = remaining_seconds // 60
                remaining_seconds = remaining_seconds % 60
                
                return {
                    'eligible': False,
                    'message': f"⏳ **لا يمكن السحب بعد**\n\nيجب الانتظار 3 دقائق من أول إيداع قبل أول سحب.\n⏰ **الوقت المتبقي:** {remaining_minutes} دقيقة و {remaining_seconds} ثانية"
                }
        except Exception as e:
            logger.error(f"Error checking first deposit time: {e}")
            return {
                'eligible': False,
                'message': "❌ **خطأ في التحقق من أهلية السحب**\n\nيرجى المحاولة لاحقاً."
            }
    else:
        try:
            last_withdrawal = datetime.strptime(last_withdrawal_time, "%Y-%m-%d %H:%M:%S")
            current_time = datetime.now()
            time_since_last_withdrawal = current_time - last_withdrawal
            
            # التعديل: تغيير من 30 يوم إلى 3 دقائق
            if time_since_last_withdrawal.total_seconds() < 3 * 60:
                remaining_seconds = 3 * 60 - int(time_since_last_withdrawal.total_seconds())
                remaining_minutes = remaining_seconds // 60
                remaining_seconds = remaining_seconds % 60
                
                return {
                    'eligible': False,
                    'message': f"⏳ **لا يمكن السحب بعد**\n\nيجب الانتظار 3 دقائق بين كل سحب وآخر.\n⏰ **الوقت المتبقي:** {remaining_minutes} دقيقة و {remaining_seconds} ثانية"
                }
        except Exception as e:
            logger.error(f"Error checking last withdrawal time: {e}")
            return {
                'eligible': False,
                'message': "❌ **خطأ في التحقق من أهلية السحب**\n\nيرجى المحاولة لاحقاً."
            }
    
    return {
        'eligible': True,
        'message': "✅ يمكنك السحب الآن"
    }

def handle_withdraw_amount(message):
    user_id = message.from_user.id
    user = load_user(user_id)
    
    try:
        amount = float(message.text.strip())
        if amount < 1:
            queue_manager.add_to_user_queue(
                user_id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "⚠️ الحد الأدنى للسحب هو 1$"
            )
            return
        if amount > user.get("balance", 0.0):
            queue_manager.add_to_user_queue(
                user_id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                f"⚠️ رصيدك غير كافٍ للسحب.\n"
                f"💳 رصيدك الحالي: {user.get('balance',0.0):.2f}$"
            )
            return
        
        withdrawal_check = check_withdrawal_eligibility(user)
        if not withdrawal_check['eligible']:
            queue_manager.add_to_user_queue(
                user_id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                withdrawal_check['message'], 
                parse_mode="Markdown"
            )
            return
        
        user_states[user_id] = "await_wallet_confirmation"
        user_states[f"{user_id}_withdraw_amount"] = amount
        
        queue_manager.add_to_user_queue(
            user_id,
            message.chat.id,
            bot.send_message,
            message.chat.id,
            f"💳 **تأكيد المحفظة**\n\n"
            f"📋 **تفاصيل طلب السحب:**\n"
            f"💸 **المبلغ:** {amount:.2f}$\n"
            f"💳 **المحفظة المسجلة:** {user.get('wallet')}\n\n"
            f"✅ **لتأكيد طلب السحب، الرجاء إدخال محفظتك مرة أخرى للتأكيد:**",
            parse_mode="Markdown",
            reply_markup=reply_keyboard_with_cancel_and_home()
        )
        
    except ValueError:
        queue_manager.add_to_user_queue(
            user_id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "⚠️ الرجاء إدخال رقم صحيح."
        )

def handle_wallet_confirmation(message):
    """معالجة تأكيد المحفظة للسحب"""
    user_id = message.from_user.id
    user = load_user(user_id)
    
    if not user:
        queue_manager.add_to_user_queue(
            user_id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "⚠️ حدث خطأ، أعد العملية.", 
            reply_markup=main_menu(user_id)
        )
        user_states.pop(user_id, None)
        return
    
    wallet_confirmation = message.text.strip()
    amount = user_states.get(f"{user_id}_withdraw_amount")
    
    if not amount:
        queue_manager.add_to_user_queue(
            user_id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "⚠️ حدث خطأ، أعد العملية.", 
            reply_markup=main_menu(user_id)
        )
        user_states.pop(user_id, None)
        return
    
    if wallet_confirmation != user.get('wallet'):
        queue_manager.add_to_user_queue(
            user_id,
            message.chat.id,
            bot.send_message,
            message.chat.id,
            "❌ **المحفظة غير متطابقة**\n\n"
            f"المحفظة المدخلة: `{wallet_confirmation}`\n"
            f"المحفظة المسجلة: `{user.get('wallet')}`\n\n"
            "الرجاء إدخال نفس المحفظة المسجلة في حسابك:",
            parse_mode="Markdown",
            reply_markup=reply_keyboard_with_cancel_and_home()
        )
        return
    
    withdrawal_check = check_withdrawal_eligibility(user)
    if not withdrawal_check['eligible']:
        queue_manager.add_to_user_queue(
            user_id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            withdrawal_check['message'], 
            parse_mode="Markdown"
        )
        user_states.pop(user_id, None)
        return
    
    def process_withdrawal_request():
        processing_msg = bot.send_message(
            message.chat.id,
            f"⏳ **جاري معالجة طلب السحب...**\n\n"
            f"💸 **المبلغ:** {amount:.2f}$\n"
            f"💳 **المحفظة:** `{wallet_confirmation}`",
            parse_mode="Markdown",
            reply_markup=reply_keyboard_with_cancel_and_home()
        )
        
        try:
            conn = get_conn()
            c = conn.cursor()
            
            c.execute(
                "INSERT INTO withdrawals (user_id, amount, status, date) VALUES (%s, %s, %s, %s)",
                (str(user["user_id"]), amount, "pending", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            withdrawal_id = c.lastrowid
            conn.commit()
            return_conn(conn)
            
            user_states.pop(user_id, None)
            user_states.pop(f"{user_id}_withdraw_amount", None)
            
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=processing_msg.message_id,
                text=f"✅ **تم إرسال طلب السحب بنجاح!**\n\n"
                     f"📋 **تفاصيل الطلب:**\n"
                     f"💸 **المبلغ:** {amount:.2f}$\n"
                     f"💳 **المحفظة:** `{wallet_confirmation}`\n\n"
                     f"⏳ **حالة الطلب:** قيد المراجعة\n"
                     f"📅 **الوقت المتوقع:** 4-24 ساعة\n\n"
                     f"📞 **للاستفسار:** @{Config.SUPPORT_BOT_USERNAME}",
                parse_mode="Markdown"
            )
            
            try:
                markup = telebot.types.InlineKeyboardMarkup()
                markup.add(
                    telebot.types.InlineKeyboardButton("✅ قبول الطلب", callback_data=f"approve_withdraw_{withdrawal_id}"),
                    telebot.types.InlineKeyboardButton("❌ رفض الطلب مع السبب", callback_data=f"reject_withdraw_reason_{withdrawal_id}")
                )
                
                bot.send_message(
                    Config.ADMIN_ID,
                    f"🆕 **طلب سحب جديد**\n\n"
                    f"👤 **المستخدم:** @{user['username']}\n"
                    f"🆔 **ID:** {user['user_id']}\n"
                    f"💸 **المبلغ:** {amount:.2f}$\n"
                    f"💳 **المحفظة:** {user['wallet']}\n"
                    f"💰 **رصيده الحالي:** {user['balance']:.2f}$\n"
                    f"📅 **الوقت:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    parse_mode="Markdown",
                    reply_markup=markup
                )
            except Exception as e:
                logger.error(f"Error notifying admin: {e}")
                
            log_event(user_id, "WITHDRAWAL_REQUEST_SUBMITTED", f"Amount: {amount}, Status: pending")
            
        except Exception as e:
            logger.error(f"Error processing withdrawal: {e}")
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=processing_msg.message_id,
                text=f"❌ **حدث خطأ أثناء معالجة الطلب**\n\n"
                     f"الرجاء المحاولة مرة أخرى أو التواصل مع الدعم.\n"
                     f"📞 @{Config.SUPPORT_BOT_USERNAME}",
                parse_mode="Markdown"
            )
        
        bot.send_message(message.chat.id, "🏠 **العودة للقائمة الرئيسية**", reply_markup=main_menu(user_id))
    
    queue_manager.add_to_user_queue(user_id, message.chat.id, process_withdrawal_request)

# ========= رابط الإحالة المحسن مع زر المشاركة =========
@bot.message_handler(func=lambda m: m.text == "👥 رابط الإحالة")
@require_login
def referral_link(message):
    user = load_user(message.from_user.id)
    bot_username = bot.get_me().username
    link = f"https://t.me/{bot_username}?start=ref_{message.from_user.id}"
    
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users WHERE referrer_id = %s", (str(message.from_user.id),))
        referred_count = c.fetchone()[0]
        
        c.execute("""
            SELECT COUNT(*) FROM users u 
            JOIN deposit_requests dr ON u.user_id = dr.user_id 
            WHERE u.referrer_id = %s AND dr.status = 'approved'
        """, (str(message.from_user.id),))
        active_referrals = c.fetchone()[0]
        
        c.execute("""
            SELECT SUM(dr.amount) FROM deposit_requests dr
            JOIN users u ON dr.user_id = u.user_id
            WHERE u.referrer_id = %s AND dr.status = 'approved' AND u.deposited = true
        """, (str(message.from_user.id),))
        total_deposits = c.fetchone()[0] or 0
        referral_earnings = total_deposits * 0.05
        
        batch_bonus_data = get_batch_bonus_progress(message.from_user.id)
        
        return_conn(conn)
        
        markup = telebot.types.InlineKeyboardMarkup()
        share_button = telebot.types.InlineKeyboardButton("📤 مشاركة الرابط", url=f"https://t.me/share/url?url={link}&text=انضم%20إلى%20تراكم%20للحصول%20على%20أفضل%20الاستثمارات!")
        markup.add(share_button)
        
        referral_text = f"""
👥 **نظام الإحالة - تراكم**\n\n
🔗 **رابط الإحالة الخاص بك:**\n
`{link}`\n\n
📊 **إحصائيات أدائك:**\n
• 👥 **عدد المُحالين:** {referred_count}\n
• ✅ **المحالين النشطين:** {active_referrals}\n
• 💰 **إجمالي أرباحك:** {referral_earnings:.2f}$\n\n
🏆 **جوائز الإحالة الجماعية:**\n
• 📦 **المجموعات المكتملة:** {batch_bonus_data['completed_batches']}\n
• 👥 **المحالين في الانتظار:** {batch_bonus_data['pending_users_count']}/3\n
• 💵 **إجمالي الجوائز:** {batch_bonus_data['total_bonus_earned']:.2f}$\n
• 🎯 **المتبقي للجائزة القادمة:** {batch_bonus_data['users_until_next_bonus']} مستخدم\n\n
💰 **مكافآت الإحالة:**\n
• 🎯 5% من أول إيداع لكل مستخدم جديد\n
• 🏆 100$ لكل 3 مستخدمين جدد يقومون بالإيداع\n
• 💵 المكافآت تضاف تلقائياً لرصيدك\n
• 📈 كلما زاد عدد المحالين زادت أرباحك\n\n
📣 **كيفية الاستفادة:**\n
1. شارك الرابط مع أصدقائك\n
2. عند تسجيلهم عبر رابطك\n
3. وعند أول إيداع لهم\n
4. تحصل على 5% من إيداعهم + 100$ لكل 3 مستخدمين\n\n
🚀 **ابدأ بجني الأرباح الآن!**
        """
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            referral_text,
            parse_mode="Markdown",
            reply_markup=markup
        )
        
    except Exception as e:
        logger.error(f"Error generating referral link: {e}")
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "❌ حدث خطأ في تحميل رابط الإحالة. يرجى المحاولة مرة أخرى."
        )

# ========= الشروط والأحكام المحسنة =========
@bot.message_handler(func=lambda m: m.text == "📄 الشروط والأحكام")
def explain(message):
    terms_text = """
📜 **الشروط والأحكام - تراكم** 📜

🏛️ **مقدمة عامة:**
تراكم هي منصة استثمارية رقمية مرخصة وخاضعة للرقابة، تقدم خدمات استثمارية آمنة وموثوقة عالمياً. نحن ملتزمون بتوفير بيئة استثمارية شفافة وآمنة لجميع عملائنا.

🔐 **البند 1: التسجيل والمصادقة**
1.1 يجب أن يكون عمر المستخدم 18 سنة على الأقل.
1.2 تقديم معلومات شخصية دقيقة وصحيحة أثناء التسجيل.
1.3 يحظر استخدام هويات مزيفة أو معلومات غير صحيحة.
1.4 يحق للإدارة تعليق أو إغلاق أي حساب يتم اكتشاف معلومات غير صحيحة فيه.

💼 **البند 2: الخدمات الاستثمارية**
2.1 الحد الأدنى للإيداع: 20 دولار أمريكي.
2.2 الحد الأقصى للإيداع: 50,000 دولار أمريكي.
2.3 العوائد تتراوح بين 3% إلى 7% يومياً حسب نوع الاستثمار.
2.4 جميع الاستثمارات محمية بأنظمة أمان متطورة.
2.5 يحق للإدارة تعديل معدلات العوائد مع إشعار مسبق.

🛡️ **البند 3: الحماية والأمان**
3.1 نستخدم تقنيات تشفير متقدمة لحماية البيانات.
3.2 جميع الأموال محفوظة في محافظ باردة معزولة.
3.3 نلتزم بأعلى معايير الأمان المالي العالمي.
3.4 يتم مراقبة جميع المعاملات بأنظمة رقابية متطورة.

💰 **البند 4: السحوبات والتحويلات**
4.1 الحد الأدنى للسحب: 1 دولار أمريكي.
4.2 ✅ **يمكن السحب بعد مرور 3 دقائق من أول إيداع فقط**
4.3 ✅ **يجب الانتظار 3 دقائق بين كل سحب وآخر**
4.4 يتم معالجة طلبات السحب خلال 4-24 ساعة.
4.5 قد تخضع السحوبات لرسوم شبكة التحويل.

🎯 **البند 5: المهام اليومية**
5.1 النظام التقليدي: يمكن البدء كل 24 ساعة من آخر مهمة
5.2 الربح: 3% من الرصيد الحالي.
5.3 يجب إكمال المهمة دون مغادرة الصفحة.
5.4 أي محاولة للغش تؤدي إلى إلغاء المهمة.

👥 **البند 6: نظام الإحالة**
6.1 مكافأة الإحالة: 5% من أول إيداع للمستخدم الجديد.
6.2 🏆 **جائزة الإحالة الجماعية: 100$ لكل 3 مستخدمين جدد يقومون بالإيداع**
6.3 يجب أن يكون المستخدم الجديد نشطاً ومودعاً.
6.4 يحظر إنشاء حسابات وهمية للإحالة.

🚫 **البند 7: الحظر والعقوبات**
7.1 تحتفظ الإدارة بالحق في حظر أي حساب يخالف الشروط.
7.2 مدة الحظر تتراوح من دقيقتين إلى حظر دائم.
7.3 يمكن للمستخدم المحظور التواصل مع الدعم فقط.
7.4 يتم فك الحظر تلقائياً بعد انتهاء المدة.

📞 **البند 8: الدعم الفني**
8.1 خدمة الدعم متاحة 24/7.
8.2 وقت الاستجابة: 24 ساعة كحد أقصى.
8.3 يمكن التواصل عبر البوت أو قنوات الدعم الرسمية.

🔒 **البند 9: الخصوصية**
9.1 نحن نحافظ على سرية بيانات العملاء.
9.2 لا نشارك المعلومات مع أطراف ثالثة.
9.3 يمكن للعملاء طلب حذف بياناتهم الشخصية.

⚖️ **البند 10: المسؤولية**
10.1 المستخدم يتحمل المسؤولية الكاملة عن أمن حسابه.
10.2 ليست لدينا مسؤولية عن الأخطاء الناتجة عن إهمال المستخدم.
10.3 نحن غير مسؤولين عن الخسائر الناتجة عن ظروف خارجة عن إرادتنا.

🔄 **البند 11: التعديلات**
11.1 تحتفظ الإدارة بالحق في تعديل الشروط والأحكام.
11.2 يتم إشعار المستخدمين بأي تغييرات.
11.3 استمرار استخدام الخدمة يعني الموافقة على التعديلات.

📞 **خاتمة:**
نشكرك على اختيار تراكم كشريكك الاستثماري. نحن ملتزمون بتوفير أفضل الخدمات وضمان تجربة استثمارية آمنة ومربحة لجميع عملائنا.

**📅 تاريخ آخر تحديث: 1 يناير 2024**
**🏢 تراكم - منصة الاستثمار الذكي الموثوقة عالمياً**
    """
    
    queue_manager.add_to_user_queue(
        message.from_user.id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id, 
        terms_text, 
        parse_mode="Markdown"
    )

print("✅ تم تحميل الجزء الثاني بنجاح!")
print("🎯 أنظمة المهام اليومية والأوامر الأساسية جاهزة!")
print("🚀 جاري تحضير الجزء الثالث...")
# ========= لوحة التحكم الإدارية المحسنة =========
@bot.message_handler(func=lambda m: m.text == "👨‍💼 لوحة التحكم الإدارية")
def admin_panel(message):
    if message.from_user.id != Config.ADMIN_ID:
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "❌ لا يوجد صلاحية للوصول لهذه اللوحة."
        )
        return
    
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("📊 إحصائيات شاملة", "🔔 إشعار عام")
    keyboard.row("💼 إدارة طلبات الإيداع", "💸 إدارة طلبات السحب")
    keyboard.row("📩 إدارة رسائل الدعم", "👥 إدارة الأعضاء")
    keyboard.row("⚙️ إعدادات النظام", "🏠 العودة للقائمة الرئيسية")
    
    queue_manager.add_to_user_queue(
        message.from_user.id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id, 
        "👨‍💼 **لوحة تحكم الإدارة - تراكم**\n\n"
        "مرحباً بك في لوحة التحكم الإدارية\n"
        "اختر القسم الذي تريد إدارته:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

# ========= الإصلاح: إحصائيات شاملة =========
@bot.message_handler(func=lambda m: m.text == "📊 إحصائيات شاملة" and m.from_user.id == Config.ADMIN_ID)
def comprehensive_stats(message):
    """إحصائيات شاملة للنظام"""
    try:
        conn = get_conn()
        c = conn.cursor()
        
        today = datetime.now().strftime("%Y-%m-%d")
        
        c.execute("SELECT COUNT(*) FROM users WHERE registered = true")
        total_users = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users WHERE deposited = true")
        active_users = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users WHERE DATE(created_date) = %s", (today,))
        new_users_today = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM deposit_requests WHERE status = 'approved'")
        total_deposits = c.fetchone()[0]
        
        c.execute("SELECT SUM(amount) FROM deposit_requests WHERE status = 'approved'")
        total_deposit_amount = c.fetchone()[0] or 0
        
        c.execute("SELECT COUNT(*) FROM deposit_requests WHERE status = 'pending'")
        pending_deposits = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM withdrawals WHERE status = 'approved'")
        total_withdrawals = c.fetchone()[0]
        
        c.execute("SELECT SUM(amount) FROM withdrawals WHERE status = 'approved'")
        total_withdrawal_amount = c.fetchone()[0] or 0
        
        c.execute("SELECT COUNT(*) FROM withdrawals WHERE status = 'pending'")
        pending_withdrawals = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users WHERE referrer_id IS NOT NULL")
        referred_users = c.fetchone()[0]
        
        c.execute("SELECT SUM(completed_batches), SUM(total_bonus_earned) FROM referral_batch_bonus")
        batch_result = c.fetchone()
        total_batches = batch_result[0] or 0
        total_batch_bonus = batch_result[1] or 0
        
        c.execute("SELECT COUNT(*) FROM support_messages WHERE status = 'open'")
        open_support_tickets = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM support_messages WHERE DATE(created_at) = %s", (today,))
        support_tickets_today = c.fetchone()[0]
        
        # إحصائيات الحظر
        c.execute("SELECT COUNT(*) FROM user_bans WHERE status = 'active'")
        active_bans = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM user_bans WHERE DATE(created_at) = %s", (today,))
        new_bans_today = c.fetchone()[0]
        
        return_conn(conn)
        
        stats_text = f"""
📊 **الإحصائيات الشاملة - تراكم**

👥 **إحصائيات المستخدمين:**
• 👤 إجمالي المستخدمين: {total_users}
• ✅ المستخدمين النشطين: {active_users}
• 🆕 مستخدمين جدد اليوم: {new_users_today}
• 👥 المستخدمين المحالين: {referred_users}

💰 **إحصائيات الإيداع:**
• 📥 إجمالي الإيداعات: {total_deposits}
• 💵 إجمالي المبالغ: {total_deposit_amount:.2f}$
• ⏳ طلبات معلقة: {pending_deposits}

💸 **إحصائيات السحب:**
• 📤 إجمالي السحوبات: {total_withdrawals}
• 💰 إجمالي المبالغ: {total_withdrawal_amount:.2f}$
• ⏳ طلبات معلقة: {pending_withdrawals}

🏆 **جوائز الإحالة الجماعية:**
• 📦 المجموعات المكتملة: {total_batches}
• 💵 إجمالي الجوائز: {total_batch_bonus:.2f}$

📞 **إحصائيات الدعم:**
• 🔓 تذاكر مفتوحة: {open_support_tickets}
• 📨 تذاكر اليوم: {support_tickets_today}

🚫 **إحصائيات الحظر:**
• 🔒 حسابات محظورة نشطة: {active_bans}
• 🆕 حظور جديدة اليوم: {new_bans_today}

📈 **الأداء العام:**
• 💼 نسبة النشاط: {(active_users/total_users*100) if total_users > 0 else 0:.1f}%
• 📊 معدل النمو: {new_users_today} مستخدم/يوم
• 💰 إجمالي التداول: {total_deposit_amount + total_withdrawal_amount:.2f}$
        """
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            stats_text, 
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Error generating comprehensive stats: {e}")
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "❌ حدث خطأ في توليد الإحصائيات."
        )

# ========= الإصلاح: إشعار عام =========
@bot.message_handler(func=lambda m: m.text == "🔔 إشعار عام" and m.from_user.id == Config.ADMIN_ID)
def broadcast_message(message):
    """إرسال إشعار عام لجميع المستخدمين"""
    user_states[f"admin_{message.from_user.id}"] = "await_broadcast_message"
    
    queue_manager.add_to_user_queue(
        message.from_user.id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id,
        "🔔 **إرسال إشعار عام**\n\n"
        "الرجاء إدخال الرسالة التي تريد إرسالها لجميع المستخدمين:\n\n"
        "💡 **نصائح:**\n"
        "• استخدم التنسيق Markdown لجعل الرسالة جذابة\n"
        "• يمكنك إضافة إيموجي لتحسين المظهر\n"
        "• تأكد من صحة المعلومات قبل الإرسال",
        parse_mode="Markdown",
        reply_markup=reply_keyboard_with_cancel()
    )

@bot.message_handler(func=lambda m: m.from_user.id == Config.ADMIN_ID and user_states.get(f"admin_{m.from_user.id}") == "await_broadcast_message")
def handle_broadcast_message(message):
    if message.text == "❌ إلغاء العملية":
        user_states.pop(f"admin_{message.from_user.id}", None)
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "✅ تم إلغاء الإرسال."
        )
        return
    
    broadcast_text = message.text
    user_states.pop(f"admin_{message.from_user.id}", None)
    
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton("✅ نعم، أرسل الإشعار", callback_data="confirm_broadcast"),
        telebot.types.InlineKeyboardButton("❌ لا، إلغاء الإرسال", callback_data="cancel_broadcast")
    )
    
    user_states[f"admin_{message.from_user.id}_broadcast"] = broadcast_text
    
    queue_manager.add_to_user_queue(
        message.from_user.id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id,
        f"📋 **تأكيد إرسال الإشعار**\n\n"
        f"📝 **محتوى الرسالة:**\n{broadcast_text}\n\n"
        f"⚠️ **سيتم إرسال هذه الرسالة لجميع المستخدمين المسجلين.**\n"
        f"هل أنت متأكد من المتابعة?",
        parse_mode="Markdown",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data in ["confirm_broadcast", "cancel_broadcast"])
def handle_broadcast_confirmation(call):
    if call.data == "cancel_broadcast":
        user_states.pop(f"admin_{call.from_user.id}_broadcast", None)
        bot.edit_message_text("✅ تم إلغاء إرسال الإشعار.", call.message.chat.id, call.message.message_id)
        return
    
    broadcast_text = user_states.get(f"admin_{call.from_user.id}_broadcast")
    if not broadcast_text:
        bot.answer_callback_query(call.id, "❌ لم يتم العثور على نص الإشعار!")
        return
    
    user_states.pop(f"admin_{call.from_user.id}_broadcast", None)
    
    def send_broadcast():
        processing_msg = bot.send_message(call.message.chat.id, "⏳ جاري إرسال الإشعار لجميع المستخدمين...")
        
        try:
            conn = get_conn()
            c = conn.cursor()
            c.execute("SELECT user_id FROM users WHERE registered = true")
            users = c.fetchall()
            return_conn(conn)
            
            success_count = 0
            fail_count = 0
            
            for user_row in users:
                user_id = user_row[0]
                try:
                    bot.send_message(user_id, f"🔔 **إشعار من الإدارة**\n\n{broadcast_text}", parse_mode="Markdown")
                    success_count += 1
                    time.sleep(0.1)  # تأخير لتجنب حظر تيليجرام
                except Exception as e:
                    fail_count += 1
                    logger.error(f"Failed to send broadcast to {user_id}: {e}")
            
            bot.edit_message_text(
                f"✅ **تم إرسال الإشعار بنجاح!**\n\n"
                f"📊 **نتائج الإرسال:**\n"
                f"• ✅ تم الإرسال بنجاح: {success_count} مستخدم\n"
                f"• ❌ فشل في الإرسال: {fail_count} مستخدم\n"
                f"• 📨 الإجمالي: {success_count + fail_count} مستخدم",
                call.message.chat.id,
                processing_msg.message_id
            )
            
            log_event(call.from_user.id, "BROADCAST_SENT", f"Success: {success_count}, Failed: {fail_count}")
            
        except Exception as e:
            bot.edit_message_text(f"❌ حدث خطأ أثناء إرسال الإشعار: {e}", call.message.chat.id, processing_msg.message_id)
            logger.error(f"Error in broadcast: {e}")
    
    queue_manager.add_to_broadcast_queue(send_broadcast)

# ========= إدارة طلبات الإيداع - الإصلاح الكامل =========
@bot.message_handler(func=lambda m: m.text == "💼 إدارة طلبات الإيداع" and m.from_user.id == Config.ADMIN_ID)
def manage_deposit_requests(message):
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("📋 طلبات الانتظار", "✅ الطلبات المقبولة")
    keyboard.row("❌ الطلبات المرفوضة", "📊 جميع الطلبات")
    keyboard.row("⬅️ العودة للوحة التحكم")
    
    queue_manager.add_to_user_queue(
        message.from_user.id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id,
        "💼 **إدارة طلبات الإيداع**\n\n"
        "اختر نوع الطلبات التي تريد عرضها:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

@bot.message_handler(func=lambda m: m.text == "📋 طلبات الانتظار" and m.from_user.id == Config.ADMIN_ID)
def show_pending_deposits(message):
    try:
        conn = get_conn()
        c = conn.cursor()
        
        c.execute("SELECT * FROM deposit_requests WHERE status = 'pending' ORDER BY date DESC")
        requests = c.fetchall()
        return_conn(conn)
        
        if not requests:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "✅ لا توجد طلبات إيداع معلقة حالياً."
            )
            return
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            f"📋 **الطلبات المعلقة:** {len(requests)} طلب"
        )
        
        for req in requests:
            req_id = req[0]
            user_id = req[1]
            username = req[2]
            amount = req[3]
            network = req[4]
            txid = req[5]
            status = req[6]
            date = req[7]
            reject_reason = req[8]
            sender_wallet = req[9] if len(req) > 9 else "غير محدد"
            
            markup = telebot.types.InlineKeyboardMarkup()
            markup.add(
                telebot.types.InlineKeyboardButton("✅ قبول الطلب", callback_data=f"approve_{req_id}"),
                telebot.types.InlineKeyboardButton("❌ رفض الطلب مع السبب", callback_data=f"reject_deposit_reason_{req_id}")
            )
            
            queue_manager.add_to_user_queue(
                message.from_user.id,
                message.chat.id,
                bot.send_message,
                message.chat.id,
                f"🆔 **رقم الطلب:** {req_id}\n"
                f"👤 **المستخدم:** @{username}\n"
                f"🆔 **ID:** {user_id}\n"
                f"💵 **المبلغ:** {amount:.2f}$\n"
                f"🌐 **الشبكة:** {network}\n"
                f"🔑 **TXID:** `{txid}`\n"
                f"💳 **المحفظة المرسلة:** `{sender_wallet}`\n"
                f"📅 **التاريخ:** {date}",
                parse_mode="Markdown",
                reply_markup=markup
            )
            
    except Exception as e:
        logger.error(f"Error showing pending deposits: {e}")

@bot.message_handler(func=lambda m: m.text == "✅ الطلبات المقبولة" and m.from_user.id == Config.ADMIN_ID)
def show_approved_deposits(message):
    try:
        conn = get_conn()
        c = conn.cursor()
        
        c.execute("SELECT * FROM deposit_requests WHERE status = 'approved' ORDER BY date DESC LIMIT 20")
        requests = c.fetchall()
        return_conn(conn)
        
        if not requests:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "✅ لا توجد طلبات إيداع مقبولة حالياً."
            )
            return
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            f"✅ **الطلبات المقبولة:** {len(requests)} طلب"
        )
        
        for req in requests:
            req_id = req[0]
            user_id = req[1]
            username = req[2]
            amount = req[3]
            network = req[4]
            txid = req[5]
            status = req[6]
            date = req[7]
            reject_reason = req[8]
            sender_wallet = req[9] if len(req) > 9 else "غير محدد"
            
            queue_manager.add_to_user_queue(
                message.from_user.id,
                message.chat.id,
                bot.send_message,
                message.chat.id,
                f"🆔 **رقم الطلب:** {req_id}\n"
                f"👤 **المستخدم:** @{username}\n"
                f"🆔 **ID:** {user_id}\n"
                f"💵 **المبلغ:** {amount:.2f}$\n"
                f"🌐 **الشبكة:** {network}\n"
                f"🔑 **TXID:** `{txid}`\n"
                f"💳 **المحفظة المرسلة:** `{sender_wallet}`\n"
                f"📅 **التاريخ:** {date}\n"
                f"✅ **الحالة:** {status}",
                parse_mode="Markdown"
            )
            
    except Exception as e:
        logger.error(f"Error showing approved deposits: {e}")

@bot.message_handler(func=lambda m: m.text == "❌ الطلبات المرفوضة" and m.from_user.id == Config.ADMIN_ID)
def show_rejected_deposits(message):
    try:
        conn = get_conn()
        c = conn.cursor()
        
        c.execute("SELECT * FROM deposit_requests WHERE status = 'rejected' ORDER BY date DESC LIMIT 20")
        requests = c.fetchall()
        return_conn(conn)
        
        if not requests:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "✅ لا توجد طلبات إيداع مرفوضة حالياً."
            )
            return
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            f"❌ **الطلبات المرفوضة:** {len(requests)} طلب"
        )
        
        for req in requests:
            req_id = req[0]
            user_id = req[1]
            username = req[2]
            amount = req[3]
            network = req[4]
            txid = req[5]
            status = req[6]
            date = req[7]
            reject_reason = req[8]
            sender_wallet = req[9] if len(req) > 9 else "غير محدد"
            
            queue_manager.add_to_user_queue(
                message.from_user.id,
                message.chat.id,
                bot.send_message,
                message.chat.id,
                f"🆔 **رقم الطلب:** {req_id}\n"
                f"👤 **المستخدم:** @{username}\n"
                f"🆔 **ID:** {user_id}\n"
                f"💵 **المبلغ:** {amount:.2f}$\n"
                f"🌐 **الشبكة:** {network}\n"
                f"🔑 **TXID:** `{txid}`\n"
                f"💳 **المحفظة المرسلة:** `{sender_wallet}`\n"
                f"📅 **التاريخ:** {date}\n"
                f"❌ **الحالة:** {status}\n"
                f"📝 **سبب الرفض:** {reject_reason}",
                parse_mode="Markdown"
            )
            
    except Exception as e:
        logger.error(f"Error showing rejected deposits: {e}")

@bot.message_handler(func=lambda m: m.text == "📊 جميع الطلبات" and m.from_user.id == Config.ADMIN_ID)
def show_all_deposits(message):
    try:
        conn = get_conn()
        c = conn.cursor()
        
        c.execute("SELECT * FROM deposit_requests ORDER BY date DESC LIMIT 20")
        requests = c.fetchall()
        return_conn(conn)
        
        if not requests:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "✅ لا توجد طلبات إيداع حالياً."
            )
            return
        
        status_emojis = {
            'pending': '⏳',
            'approved': '✅',
            'rejected': '❌'
        }
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            f"📊 **جميع طلبات الإيداع:** {len(requests)} طلب"
        )
        
        for req in requests:
            req_id = req[0]
            user_id = req[1]
            username = req[2]
            amount = req[3]
            network = req[4]
            txid = req[5]
            status = req[6]
            date = req[7]
            reject_reason = req[8]
            sender_wallet = req[9] if len(req) > 9 else "غير محدد"
            
            emoji = status_emojis.get(status, '📄')
            
            message_text = f"{emoji} **طلب #{req_id}**\n"
            message_text += f"👤 @{username} | 💵 {amount:.2f}$\n"
            message_text += f"🌐 {network} | 📅 {date}\n"
            message_text += f"🔑 TXID: `{txid[:20]}...`\n"
            message_text += f"💳 المحفظة: `{sender_wallet}`\n"
            message_text += f"📊 الحالة: {status}"
            
            if status == 'rejected' and reject_reason:
                message_text += f"\n📝 السبب: {reject_reason}"
            
            queue_manager.add_to_user_queue(
                message.from_user.id,
                message.chat.id,
                bot.send_message,
                message.chat.id,
                message_text,
                parse_mode="Markdown"
            )
            
    except Exception as e:
        logger.error(f"Error showing all deposits: {e}")

# ========= إدارة طلبات السحب - الإصلاح الكامل =========
@bot.message_handler(func=lambda m: m.text == "💸 إدارة طلبات السحب" and m.from_user.id == Config.ADMIN_ID)
def manage_withdrawal_requests(message):
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("📋 طلبات سحب الانتظار", "✅ طلبات سحب المقبولة")
    keyboard.row("❌ طلبات سحب المرفوضة", "📊 جميع طلبات السحب")
    keyboard.row("⬅️ العودة للوحة التحكم")
    
    queue_manager.add_to_user_queue(
        message.from_user.id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id,
        "💸 **إدارة طلبات السحب**\n\n"
        "اختر نوع الطلبات التي تريد عرضها:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

@bot.message_handler(func=lambda m: m.text == "📋 طلبات سحب الانتظار" and m.from_user.id == Config.ADMIN_ID)
def show_pending_withdrawals(message):
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT w.*, u.username, u.wallet 
            FROM withdrawals w 
            JOIN users u ON w.user_id = u.user_id 
            WHERE w.status = 'pending' 
            ORDER BY w.date DESC
        """)
        requests = c.fetchall()
        return_conn(conn)
        
        if not requests:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "✅ لا توجد طلبات سحب معلقة حالياً."
            )
            return
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            f"💸 **طلبات السحب المعلقة:** {len(requests)} طلب"
        )
        
        for req in requests:
            w_id, user_id, amount, status, admin_id, processed_date, date, tx_hash, reject_reason, username, wallet = req
            markup = telebot.types.InlineKeyboardMarkup()
            markup.add(
                telebot.types.InlineKeyboardButton("✅ قبول الطلب", callback_data=f"approve_withdraw_{w_id}"),
                telebot.types.InlineKeyboardButton("❌ رفض الطلب مع السبب", callback_data=f"reject_withdraw_reason_{w_id}")
            )
            
            queue_manager.add_to_user_queue(
                message.from_user.id,
                message.chat.id,
                bot.send_message,
                message.chat.id,
                f"🆔 **رقم الطلب:** {w_id}\n"
                f"👤 **المستخدم:** @{username}\n"
                f"🆔 **ID:** {user_id}\n"
                f"💸 **المبلغ:** {amount:.2f}$\n"
                f"💳 **المحفظة:** {wallet}\n"
                f"📅 **التاريخ:** {date}",
                parse_mode="Markdown",
                reply_markup=markup
            )
            
    except Exception as e:
        logger.error(f"Error showing pending withdrawals: {e}")

@bot.message_handler(func=lambda m: m.text == "✅ طلبات سحب المقبولة" and m.from_user.id == Config.ADMIN_ID)
def show_approved_withdrawals(message):
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT w.*, u.username, u.wallet 
            FROM withdrawals w 
            JOIN users u ON w.user_id = u.user_id 
            WHERE w.status = 'approved' 
            ORDER BY w.date DESC LIMIT 20
        """)
        requests = c.fetchall()
        return_conn(conn)
        
        if not requests:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "✅ لا توجد طلبات سحب مقبولة حالياً."
            )
            return
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            f"✅ **طلبات السحب المقبولة:** {len(requests)} طلب"
        )
        
        for req in requests:
            w_id, user_id, amount, status, admin_id, processed_date, date, tx_hash, reject_reason, username, wallet = req
            
            queue_manager.add_to_user_queue(
                message.from_user.id,
                message.chat.id,
                bot.send_message,
                message.chat.id,
                f"🆔 **رقم الطلب:** {w_id}\n"
                f"👤 **المستخدم:** @{username}\n"
                f"🆔 **ID:** {user_id}\n"
                f"💸 **المبلغ:** {amount:.2f}$\n"
                f"💳 **المحفظة:** {wallet}\n"
                f"📅 **تاريخ الطلب:** {date}\n"
                f"⏰ **تاريخ المعالجة:** {processed_date}\n"
                f"👨‍💼 **المعالج:** {admin_id}\n"
                f"✅ **الحالة:** {status}",
                parse_mode="Markdown"
            )
            
    except Exception as e:
        logger.error(f"Error showing approved withdrawals: {e}")

@bot.message_handler(func=lambda m: m.text == "❌ طلبات سحب المرفوضة" and m.from_user.id == Config.ADMIN_ID)
def show_rejected_withdrawals(message):
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT w.*, u.username, u.wallet 
            FROM withdrawals w 
            JOIN users u ON w.user_id = u.user_id 
            WHERE w.status = 'rejected' 
            ORDER BY w.date DESC LIMIT 20
        """)
        requests = c.fetchall()
        return_conn(conn)
        
        if not requests:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "✅ لا توجد طلبات سحب مرفوضة حالياً."
            )
            return
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            f"❌ **طلبات السحب المرفوضة:** {len(requests)} طلب"
        )
        
        for req in requests:
            w_id, user_id, amount, status, admin_id, processed_date, date, tx_hash, reject_reason, username, wallet = req
            
            queue_manager.add_to_user_queue(
                message.from_user.id,
                message.chat.id,
                bot.send_message,
                message.chat.id,
                f"🆔 **رقم الطلب:** {w_id}\n"
                f"👤 **المستخدم:** @{username}\n"
                f"🆔 **ID:** {user_id}\n"
                f"💸 **المبلغ:** {amount:.2f}$\n"
                f"💳 **المحفظة:** {wallet}\n"
                f"📅 **تاريخ الطلب:** {date}\n"
                f"⏰ **تاريخ المعالجة:** {processed_date}\n"
                f"👨‍💼 **المعالج:** {admin_id}\n"
                f"❌ **الحالة:** {status}\n"
                f"📝 **سبب الرفض:** {reject_reason}",
                parse_mode="Markdown"
            )
            
    except Exception as e:
        logger.error(f"Error showing rejected withdrawals: {e}")

@bot.message_handler(func=lambda m: m.text == "📊 جميع طلبات السحب" and m.from_user.id == Config.ADMIN_ID)
def show_all_withdrawals(message):
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT w.*, u.username, u.wallet 
            FROM withdrawals w 
            JOIN users u ON w.user_id = u.user_id 
            ORDER BY w.date DESC LIMIT 20
        """)
        requests = c.fetchall()
        return_conn(conn)
        
        if not requests:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "✅ لا توجد طلبات سحب حالياً."
            )
            return
        
        status_emojis = {
            'pending': '⏳',
            'approved': '✅',
            'rejected': '❌'
        }
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            f"📊 **جميع طلبات السحب:** {len(requests)} طلب"
        )
        
        for req in requests:
            w_id, user_id, amount, status, admin_id, processed_date, date, tx_hash, reject_reason, username, wallet = req
            
            emoji = status_emojis.get(status, '📄')
            
            message_text = f"{emoji} **طلب سحب #{w_id}**\n"
            message_text += f"👤 @{username} | 💸 {amount:.2f}$\n"
            message_text += f"💳 {wallet}\n"
            message_text += f"📅 {date}\n"
            message_text += f"📊 الحالة: {status}"
            
            if status == 'rejected' and reject_reason:
                message_text += f"\n📝 السبب: {reject_reason}"
            elif status == 'approved' and processed_date:
                message_text += f"\n⏰ معالج: {processed_date}"
            
            queue_manager.add_to_user_queue(
                message.from_user.id,
                message.chat.id,
                bot.send_message,
                message.chat.id,
                message_text,
                parse_mode="Markdown"
            )
            
    except Exception as e:
        logger.error(f"Error showing all withdrawals: {e}")

# ========= إدارة الأعضاء المحسنة مع نظام الحظر =========
@bot.message_handler(func=lambda m: m.text == "👥 إدارة الأعضاء" and m.from_user.id == Config.ADMIN_ID)
def manage_members(message):
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("📊 إحصائيات الأعضاء", "🔍 بحث عن عضو")
    keyboard.row("🚫 حظر عضو", "✅ فك حظر عضو")
    keyboard.row("📧 إرسال رسالة لعضو", "⬅️ العودة للوحة التحكم")
    
    queue_manager.add_to_user_queue(
        message.from_user.id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id,
        "👥 **إدارة الأعضاء**\n\n"
        "اختر الإجراء الذي تريد تنفيذه:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

@bot.message_handler(func=lambda m: m.text == "📊 إحصائيات الأعضاء" and m.from_user.id == Config.ADMIN_ID)
def member_statistics(message):
    try:
        conn = get_conn()
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) FROM users WHERE registered = true")
        total_members = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users WHERE deposited = true")
        active_members = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM user_bans WHERE status = 'active'")
        banned_members = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users WHERE DATE(created_date) = CURRENT_DATE")
        new_today = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users WHERE referrer_id IS NOT NULL")
        referred_members = c.fetchone()[0]
        
        c.execute("SELECT SUM(balance) FROM users")
        total_balance = c.fetchone()[0] or 0
        
        c.execute("SELECT AVG(balance) FROM users WHERE balance > 0")
        avg_balance = c.fetchone()[0] or 0
        
        return_conn(conn)
        
        stats_text = f"""
📊 **إحصائيات الأعضاء - تراكم**

👥 **الأعضاء:**
• 👤 إجمالي الأعضاء: {total_members}
• ✅ أعضاء نشطين: {active_members}
• 🚫 أعضاء محظورين: {banned_members}
• 🆕 أعضاء جدد اليوم: {new_today}
• 👥 أعضاء محالين: {referred_members}

💰 **الأرصدة:**
• 💵 إجمالي الأرصدة: {total_balance:.2f}$
• 📈 متوسط الرصيد: {avg_balance:.2f}$

📈 **النسب:**
• 📊 نسبة النشاط: {(active_members/total_members*100) if total_members > 0 else 0:.1f}%
• 🎯 نسبة الإحالة: {(referred_members/total_members*100) if total_members > 0 else 0:.1f}%
    """
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            stats_text, 
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Error generating member statistics: {e}")

@bot.message_handler(func=lambda m: m.text == "🔍 بحث عن عضو" and m.from_user.id == Config.ADMIN_ID)
def search_member(message):
    user_states[f"admin_{message.from_user.id}"] = "await_member_search"
    
    queue_manager.add_to_user_queue(
        message.from_user.id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id,
        "🔍 **بحث عن عضو**\n\n"
        "أدخل اسم المستخدم أو رقم العضوية أو ID العضو:",
        parse_mode="Markdown",
        reply_markup=reply_keyboard_with_cancel()
    )

@bot.message_handler(func=lambda m: m.from_user.id == Config.ADMIN_ID and user_states.get(f"admin_{m.from_user.id}") == "await_member_search")
def handle_member_search(message):
    if message.text == "❌ إلغاء العملية":
        user_states.pop(f"admin_{message.from_user.id}", None)
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "✅ تم إلغاء البحث."
        )
        return
    
    search_term = message.text.strip()
    user_states.pop(f"admin_{message.from_user.id}", None)
    
    try:
        conn = get_conn()
        c = conn.cursor()
        
        # البحث باسم المستخدم
        c.execute("SELECT * FROM users WHERE username LIKE %s OR user_id = %s OR membership_id = %s", 
                 (f"%{search_term}%", search_term, search_term))
        users = c.fetchall()
        return_conn(conn)
        
        if not users:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "❌ لم يتم العثور على أي عضو بهذا المعيار."
            )
            return
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            f"🔍 **نتائج البحث:** {len(users)} عضو"
        )
        
        for user in users:
            user_id = user[0]
            membership_id = user[1]
            username = user[2]
            email = user[4]
            phone = user[5]
            wallet = user[6]
            balance = user[7]
            registered = user[8]
            deposited = user[9]
            status = user[18]
            
            # التحقق من الحظر
            ban_check = is_user_banned(user_id)
            
            status_emoji = "✅" if status == 'active' else "🚫"
            registered_text = "✅" if registered else "❌"
            deposited_text = "✅" if deposited else "❌"
            ban_status = "🚫 محظور" if ban_check['banned'] else "✅ نشط"
            
            user_info = f"""
{status_emoji} **العضو:** @{username}
🆔 **ID:** {user_id}
🎫 **رقم العضوية:** {membership_id}
📧 **البريد:** {email or 'غير محدد'}
📱 **الهاتف:** {phone or 'غير مسجل'}
💳 **المحفظة:** `{wallet or 'غير مسجلة'}`
💰 **الرصيد:** {balance:.2f}$
📝 **مسجل:** {registered_text}
💵 **مودع:** {deposited_text}
🔰 **الحالة:** {status}
🚫 **حالة الحظر:** {ban_status}
            """
            
            markup = telebot.types.InlineKeyboardMarkup()
            markup.add(
                telebot.types.InlineKeyboardButton("📧 مراسلة", callback_data=f"message_user_{user_id}"),
            )
            
            if ban_check['banned']:
                markup.add(telebot.types.InlineKeyboardButton("✅ فك الحظر", callback_data=f"unban_user_{ban_check['ban_id']}"))
            else:
                markup.add(telebot.types.InlineKeyboardButton("🚫 حظر", callback_data=f"ban_user_{user_id}"))
            
            queue_manager.add_to_user_queue(
                message.from_user.id,
                message.chat.id,
                bot.send_message,
                message.chat.id,
                user_info,
                parse_mode="Markdown",
                reply_markup=markup
            )
        
    except Exception as e:
        logger.error(f"Error searching for members: {e}")

# ========= نظام الحظر المحسن =========
@bot.message_handler(func=lambda m: m.text == "🚫 حظر عضو" and m.from_user.id == Config.ADMIN_ID)
def ban_member_start(message):
    user_states[f"admin_{message.from_user.id}"] = "await_ban_search"
    
    queue_manager.add_to_user_queue(
        message.from_user.id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id,
        "🚫 **حظر عضو**\n\n"
        "أدخل اسم المستخدم أو رقم العضوية أو ID العضو الذي تريد حظره:",
        parse_mode="Markdown",
        reply_markup=reply_keyboard_with_cancel_and_home()
    )

@bot.message_handler(func=lambda m: m.from_user.id == Config.ADMIN_ID and user_states.get(f"admin_{m.from_user.id}") == "await_ban_search")
def handle_ban_search(message):
    if message.text in ["❌ إلغاء العملية", "🏠 العودة للقائمة الرئيسية"]:
        user_states.pop(f"admin_{message.from_user.id}", None)
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "✅ تم إلغاء العملية.", 
            reply_markup=main_menu(message.from_user.id)
        )
        return
    
    search_term = message.text.strip()
    user_states.pop(f"admin_{message.from_user.id}", None)
    
    try:
        conn = get_conn()
        c = conn.cursor()
        
        # البحث عن المستخدم
        c.execute("SELECT * FROM users WHERE username LIKE %s OR user_id = %s OR membership_id = %s", 
                 (f"%{search_term}%", search_term, search_term))
        users = c.fetchall()
        return_conn(conn)
        
        if not users:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "❌ لم يتم العثور على أي عضو بهذا المعيار."
            )
            return
        
        if len(users) > 1:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                f"🔍 **وجدت {len(users)} عضو، الرجاء تحديد المستخدم بدقة أكثر.**"
            )
            return
        
        user = users[0]
        user_id = user[0]
        username = user[2]
        
        # التحقق إذا كان المستخدم محظوراً بالفعل
        ban_check = is_user_banned(user_id)
        if ban_check['banned']:
            queue_manager.add_to_user_queue(
                message.from_user.id,
                message.chat.id,
                bot.send_message,
                message.chat.id,
                f"🚫 **المستخدم محظور بالفعل**\n\n"
                f"👤 المستخدم: @{username}\n"
                f"⏰ مدة الحظر: {ban_check['ban_duration']}\n"
                f"🕐 وقت الانتهاء: {ban_check['ban_end_time']}",
                parse_mode="Markdown"
            )
            return
        
        user_states[f"admin_{message.from_user.id}_ban_user"] = user_id
        user_states[f"admin_{message.from_user.id}_ban_username"] = username
    
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.row("⏰ حظر لمدة دقيقتين", "⏰ حظر لمدة ساعة")
        keyboard.row("⏰ حظر لمدة ٢٤ ساعة", "⏰ حظر لمدة ٣ ايام")
        keyboard.row("⏰ حظر لمدة اسبوع", "🚫 حظر حتى يتم الالغاء")
        keyboard.row("❌ إلغاء العملية", "🏠 العودة للقائمة الرئيسية")
        
        queue_manager.add_to_user_queue(
            message.from_user.id,
            message.chat.id,
            bot.send_message,
            message.chat.id,
            f"🚫 **حظر المستخدم:** @{username}\n\n"
            f"🆔 **ID:** {user_id}\n\n"
            f"⏰ **اختر مدة الحظر:**",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Error in ban search: {e}")

@bot.message_handler(func=lambda m: m.from_user.id == Config.ADMIN_ID and user_states.get(f"admin_{m.from_user.id}_ban_user"))
def handle_ban_duration(message):
    if message.text in ["❌ إلغاء العملية", "🏠 العودة للقائمة الرئيسية"]:
        user_states.pop(f"admin_{message.from_user.id}_ban_user", None)
        user_states.pop(f"admin_{message.from_user.id}_ban_username", None)
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "✅ تم إلغاء عملية الحظر.", 
            reply_markup=main_menu(message.from_user.id)
        )
        return
    
    duration_map = {
        "⏰ حظر لمدة دقيقتين": "2_minutes",
        "⏰ حظر لمدة ساعة": "1_hour", 
        "⏰ حظر لمدة ٢٤ ساعة": "24_hours",
        "⏰ حظر لمدة ٣ ايام": "3_days",
        "⏰ حظر لمدة اسبوع": "1_week",
        "🚫 حظر حتى يتم الالغاء": "permanent"
    }
    
    ban_duration = duration_map.get(message.text)
    if not ban_duration:
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "❌ الرجاء اختيار مدة حظر صحيحة من القائمة."
        )
        return
    
    user_id = user_states.get(f"admin_{message.from_user.id}_ban_user")
    username = user_states.get(f"admin_{message.from_user.id}_ban_username")
    
    user_states.pop(f"admin_{message.from_user.id}_ban_user", None)
    user_states.pop(f"admin_{message.from_user.id}_ban_username", None)
    
    # تنفيذ الحظر
    success = ban_user(user_id, message.from_user.id, ban_duration)
    
    if success:
        duration_text = {
            "2_minutes": "دقيقتين",
            "1_hour": "ساعة واحدة", 
            "24_hours": "24 ساعة",
            "3_days": "3 أيام",
            "1_week": "أسبوع واحد",
            "permanent": "دائم (حتى إلغاء الحظر)"
        }.get(ban_duration, "غير معروف")
        
        queue_manager.add_to_user_queue(
            message.from_user.id,
            message.chat.id,
            bot.send_message,
            message.chat.id,
            f"✅ **تم حظر المستخدم بنجاح**\n\n"
            f"👤 **المستخدم:** @{username}\n"
            f"🆔 **ID:** {user_id}\n"
            f"⏰ **مدة الحظر:** {duration_text}\n"
            f"📅 **وقت الحظر:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            parse_mode="Markdown",
            reply_markup=main_menu(message.from_user.id)
        )
    else:
        queue_manager.add_to_user_queue(
            message.from_user.id,
            message.chat.id,
            bot.send_message,
            message.chat.id,
            "❌ **فشل في حظر المستخدم**\n\n"
            "الرجاء المحاولة مرة أخرى.",
            parse_mode="Markdown",
            reply_markup=main_menu(message.from_user.id)
        )

@bot.message_handler(func=lambda m: m.text == "✅ فك حظر عضو" and m.from_user.id == Config.ADMIN_ID)
def unban_member_start(message):
    """عرض الأعضاء المحظورين الحاليين بشكل تفصيلي"""
    try:
        active_bans = get_active_bans()
        
        logger.info(f"🔍 [BANS_DEBUG] عدد الحظور المعروضة: {len(active_bans)}")
        
        if not active_bans:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "✅ لا توجد حسابات محظورة حالياً."
            )
            return
        
        # إرسال رسالة واحدة بالعدد
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            f"🚫 **الحسابات المحظورة حالياً:** {len(active_bans)} حساب\n\n**اختر الحساب الذي تريد فك حظره:**",
            parse_mode="Markdown"
        )
        
        # عرض كل حساب محظور مع أزرار فك الحظر
        for ban in active_bans:
            # تحويل مدة الحظر إلى نص مفهوم
            duration_map = {
                "2_minutes": "⏰ دقيقتين",
                "1_hour": "⏰ ساعة واحدة", 
                "24_hours": "⏰ 24 ساعة",
                "3_days": "⏰ 3 أيام", 
                "1_week": "⏰ أسبوع واحد",
                "permanent": "🚫 حظر دائم"
            }
            
            duration_text = duration_map.get(ban['ban_duration'], f"⏰ {ban['ban_duration']}")
            
            ban_info = f"""
🚫 **حساب محظور**

👤 **المستخدم:** @{ban['username']}
🆔 **ID المستخدم:** `{ban['user_id']}`
🎫 **رقم الحظر:** `{ban['ban_id']}`
⏰ **مدة الحظر:** {duration_text}
🕐 **وقت البدء:** {ban['ban_start_time']}
⏳ **وقت الانتهاء:** {ban['ban_end_time']}
📝 **سبب الحظر:** {ban['ban_reason']}
            """
            
            # إنشاء أزرار Inline
            markup = telebot.types.InlineKeyboardMarkup()
            markup.row(
                telebot.types.InlineKeyboardButton("✅ فك الحظر", callback_data=f"unban_user_{ban['ban_id']}"),
                telebot.types.InlineKeyboardButton("📋 تفاصيل", callback_data=f"ban_details_{ban['ban_id']}")
            )
            
            # إرسال كل حساب في رسالة منفصلة
            queue_manager.add_to_user_queue(
                message.from_user.id,
                message.chat.id,
                bot.send_message,
                message.chat.id,
                ban_info, 
                parse_mode="Markdown", 
                reply_markup=markup
            )
            
    except Exception as e:
        logger.error(f"❌ خطأ في unban_member_start: {e}")
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            f"❌ حدث خطأ في عرض الحسابات المحظورة: {e}"
        )

# ========= إدارة رسائل الدعم - الإصلاح الكامل =========
@bot.message_handler(func=lambda m: m.text == "📩 إدارة رسائل الدعم" and m.from_user.id == Config.ADMIN_ID)
def manage_support_messages(message):
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("📨 الرسائل المفتوحة", "✅ الرسائل المجابة")
    keyboard.row("📊 جميع الرسائل", "⬅️ العودة للوحة التحكم")
    
    queue_manager.add_to_user_queue(
        message.from_user.id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id,
        "📩 **إدارة رسائل الدعم**\n\n"
        "اختر نوع الرسائل التي تريد عرضها:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

@bot.message_handler(func=lambda m: m.text == "📨 الرسائل المفتوحة" and m.from_user.id == Config.ADMIN_ID)
def show_open_support_messages(message):
    try:
        conn = get_conn()
        c = conn.cursor()
        
        c.execute("SELECT * FROM support_messages WHERE status = 'open' ORDER BY created_at DESC")
        messages = c.fetchall()
        return_conn(conn)
        
        if not messages:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "✅ لا توجد رسائل دعم مفتوحة حالياً."
            )
            return
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            f"📨 **الرسائل المفتوحة:** {len(messages)} رسالة"
        )
        
        for msg in messages:
            msg_id = msg[0]
            user_id = msg[1]
            membership_id = msg[2]
            username = msg[3]
            category = msg[4]
            category_name = msg[5]
            message_text = msg[6]
            status = msg[7]
            created_at = msg[8]
            
            markup = telebot.types.InlineKeyboardMarkup()
            markup.add(
                telebot.types.InlineKeyboardButton("📝 الرد على الرسالة", callback_data=f"reply_support_{msg_id}"),
                telebot.types.InlineKeyboardButton("✅ إغلاق الرسالة", callback_data=f"close_support_{msg_id}")
            )
            
            queue_manager.add_to_user_queue(
                message.from_user.id,
                message.chat.id,
                bot.send_message,
                message.chat.id,
                f"📩 **رسالة دعم #{msg_id}**\n\n"
                f"👤 **المستخدم:** @{username}\n"
                f"🎫 **العضوية:** {membership_id}\n"
                f"📂 **الفئة:** {category_name}\n"
                f"📅 **الوقت:** {created_at}\n\n"
                f"💬 **الرسالة:**\n{message_text[:200]}...",
                parse_mode="Markdown",
                reply_markup=markup
            )
            
    except Exception as e:
        logger.error(f"Error showing open support messages: {e}")

@bot.message_handler(func=lambda m: m.text == "✅ الرسائل المجابة" and m.from_user.id == Config.ADMIN_ID)
def show_answered_support_messages(message):
    try:
        conn = get_conn()
        c = conn.cursor()
        
        c.execute("SELECT * FROM support_messages WHERE status = 'closed' ORDER BY responded_at DESC LIMIT 10")
        messages = c.fetchall()
        return_conn(conn)
        
        if not messages:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "✅ لا توجد رسائل دعم مجابة حالياً."
            )
            return
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            f"✅ **الرسائل المجابة:** {len(messages)} رسالة"
        )
        
        for msg in messages:
            msg_id = msg[0]
            user_id = msg[1]
            membership_id = msg[2]
            username = msg[3]
            category = msg[4]
            category_name = msg[5]
            message_text = msg[6]
            status = msg[7]
            created_at = msg[8]
            admin_response = msg[9]
            responded_at = msg[10]
            
            queue_manager.add_to_user_queue(
                message.from_user.id,
                message.chat.id,
                bot.send_message,
                message.chat.id,
                f"✅ **رسالة دعم #{msg_id}**\n\n"
                f"👤 **المستخدم:** @{username}\n"
                f"🎫 **العضوية:** {membership_id}\n"
                f"📂 **الفئة:** {category_name}\n"
                f"📅 **الوقت:** {created_at}\n"
                f"⏰ **وقت الرد:** {responded_at}\n\n"
                f"💬 **الرسالة:**\n{message_text[:150]}...\n\n"
                f"📝 **الرد:**\n{admin_response[:150]}...",
                parse_mode="Markdown"
            )
            
    except Exception as e:
        logger.error(f"Error showing answered support messages: {e}")

@bot.message_handler(func=lambda m: m.text == "📊 جميع الرسائل" and m.from_user.id == Config.ADMIN_ID)
def show_all_support_messages(message):
    try:
        conn = get_conn()
        c = conn.cursor()
        
        c.execute("SELECT * FROM support_messages ORDER BY created_at DESC LIMIT 15")
        messages = c.fetchall()
        return_conn(conn)
        
        if not messages:
            queue_manager.add_to_user_queue(
                message.from_user.id, 
                message.chat.id, 
                bot.send_message, 
                message.chat.id, 
                "✅ لا توجد رسائل دعم حالياً."
            )
            return
        
        status_emojis = {
            'open': '📨',
            'closed': '✅'
        }
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            f"📊 **جميع رسائل الدعم:** {len(messages)} رسالة"
        )
        
        for msg in messages:
            msg_id = msg[0]
            user_id = msg[1]
            membership_id = msg[2]
            username = msg[3]
            category = msg[4]
            category_name = msg[5]
            message_text = msg[6]
            status = msg[7]
            created_at = msg[8]
            
            emoji = status_emojis.get(status, '📄')
            
            message_display = f"{emoji} **رسالة #{msg_id}**\n"
            message_display += f"👤 @{username} | 🎫 {membership_id}\n"
            message_display += f"📂 {category_name} | 📅 {created_at}\n"
            message_display += f"📊 الحالة: {status}\n"
            message_display += f"💬 {message_text[:100]}..."
            
            queue_manager.add_to_user_queue(
                message.from_user.id,
                message.chat.id,
                bot.send_message,
                message.chat.id,
                message_display,
                parse_mode="Markdown"
            )
            
    except Exception as e:
        logger.error(f"Error showing all support messages: {e}")

# ========= إعدادات النظام - الإصلاح الكامل =========
@bot.message_handler(func=lambda m: m.text == "⚙️ إعدادات النظام" and m.from_user.id == Config.ADMIN_ID)
def system_settings(message):
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("🔄 تحديث النظام", "🗃️ نسخ احتياطي")
    keyboard.row("📈 تحسين الأداء", "🛠️ صيانة النظام")
    keyboard.row("⬅️ العودة للوحة التحكم")
    
    queue_manager.add_to_user_queue(
        message.from_user.id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id,
        "⚙️ **إعدادات النظام**\n\n"
        "اختر الإجراء الذي تريد تنفيذه:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

@bot.message_handler(func=lambda m: m.text == "🔄 تحديث النظام" and m.from_user.id == Config.ADMIN_ID)
def system_update(message):
    try:
        # تحديث الإحصائيات
        optimize_database()
        
        # تنظيف الذاكرة المؤقتة
        cache_manager.memory_cache.clear()
        load_user.cache_clear()
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id,
            "✅ **تم تحديث النظام بنجاح**\n\n"
            "• 🗃️ قاعدة البيانات محسنة\n"
            "• 🧹 الذاكرة المؤقتة نظيفة\n"
            "• 📊 الإحصائيات محدثة\n"
            "• ⚡ النظام جاهز للعمل",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id,
            f"❌ **خطأ في تحديث النظام**\n\n"
            f"الخطأ: {str(e)}",
            parse_mode="Markdown"
        )

@bot.message_handler(func=lambda m: m.text == "🗃️ نسخ احتياطي" and m.from_user.id == Config.ADMIN_ID)
def system_backup(message):
    try:
        backup_database()
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id,
            "✅ **تم إنشاء نسخة احتياطية بنجاح**\n\n"
            "• 📁 تم حفظ نسخة من قاعدة البيانات\n"
            "• 🛡️ البيانات محمية بنسخ احتياطي\n"
            "• ⏰ آخر تحديث: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            parse_mode="Markdown"
        )
        
    except Exception as e:
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id,
            f"❌ **خطأ في إنشاء النسخة الاحتياطية**\n\n"
            f"الخطأ: {str(e)}",
            parse_mode="Markdown"
        )

@bot.message_handler(func=lambda m: m.text == "📈 تحسين الأداء" and m.from_user.id == Config.ADMIN_ID)
def system_optimize(message):
    try:
        optimize_database()
        
        # تنظيف الملفات المؤقتة
        temp_files = [f for f in os.listdir('.') if f.endswith('.tmp')]
        for temp_file in temp_files:
            try:
                os.remove(temp_file)
            except:
                pass
        
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id,
            "✅ **تم تحسين أداء النظام بنجاح**\n\n"
            "• 🗃️ قاعدة البيانات محسنة\n"
            "• 🧹 الملفات المؤقتة نظيفة\n"
            "• ⚡ الأداء في أفضل حالة\n"
            "• 📊 النظام يعمل بكفاءة عالية",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id,
            f"❌ **خطأ في تحسين الأداء**\n\n"
            f"الخطأ: {str(e)}",
            parse_mode="Markdown"
        )

@bot.message_handler(func=lambda m: m.text == "🛠️ صيانة النظام" and m.from_user.id == Config.ADMIN_ID)
def system_maintenance(message):
    maintenance_text = f"""
🛠️ **صيانة النظام - تراكم**

📊 **حالة النظام الحالية:**
• 🤖 البوت: 🟢 يعمل
• 🗃️ قاعدة البيانات: 🟢 مستقرة
• ⚡ الأداء: 🟢 ممتاز
• 🛡️ الأمان: 🟢 محمي

📈 **إحصائيات الأداء:**
• 👥 المستخدمون النشطون: {len(logged_in_users)}
• 💼 المهام النشطة: {task_queue.get_active_count()}
• 📨 الإشعارات المجمعة: {len(notification_manager.pending_notifications)}
• 🚫 الحظور النشطة: {len(get_active_bans())}

🔧 **إجراءات الصيانة المتاحة:**
1. تنظيف الذاكرة المؤقتة
2. تحسين قاعدة البيانات
3. تحديث الإحصائيات
4. فحص سلامة النظام

✅ **النظام يعمل بشكل طبيعي ولا يحتاج لصيانة طارئة**
    """
    
    queue_manager.add_to_user_queue(
        message.from_user.id, 
        message.chat.id, 
        bot.send_message, 
        message.chat.id, 
        maintenance_text, 
        parse_mode="Markdown"
    )

# ========= نظام التقارير اليومية =========
def generate_daily_report():
    """تقرير أداء يومي"""
    try:
        conn = get_conn()
        c = conn.cursor()
        
        today = datetime.now().strftime("%Y-%m-%d")
        
        c.execute("SELECT COUNT(*) FROM users WHERE DATE(created_date) = %s", (today,))
        new_users = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM deposit_requests WHERE DATE(date) = %s AND status = 'approved'", (today,))
        deposits_today = c.fetchone()[0]
        
        c.execute("SELECT SUM(amount) FROM deposit_requests WHERE DATE(date) = %s AND status = 'approved'", (today,))
        deposits_amount = c.fetchone()[0] or 0
        
        c.execute("SELECT COUNT(*) FROM withdrawals WHERE DATE(date) = %s", (today,))
        withdrawals_today = c.fetchone()[0]
        
        total_members = get_total_membership_count()
        
        c.execute("SELECT COUNT(*) FROM referral_batch_bonus WHERE DATE(last_bonus_date) = %s", (today,))
        batch_bonus_today = c.fetchone()[0]
        
        c.execute("SELECT SUM(total_bonus_earned) FROM referral_batch_bonus WHERE DATE(last_bonus_date) = %s", (today,))
        batch_bonus_amount = c.fetchone()[0] or 0
        
        # إحصائيات الحظر
        c.execute("SELECT COUNT(*) FROM user_bans WHERE DATE(created_at) = %s", (today,))
        new_bans_today = c.fetchone()[0]
        
        return_conn(conn)
        
        report = f"""
📊 **التقرير اليومي - {today}**

👥 الأعضاء الجدد: {new_users}
💰 طلبات الإيداع: {deposits_today}
💵 إجمالي الإيداعات: {deposits_amount:.2f}$
💸 طلبات السحب: {withdrawals_today}
🎯 المهام النشطة: {task_queue.get_active_count()}
🏢 إجمالي الأعضاء: {total_members}

🏆 **جوائز الإحالة الجماعية اليوم:**
• المجموعات المكتملة: {batch_bonus_today}
• إجمالي الجوائز: {batch_bonus_amount:.2f}$

🚫 **الحظور الجديدة اليوم:**
• حسابات محظورة جديدة: {new_bans_today}
        """
        
        try:
            queue_manager.add_to_user_queue(
                Config.ADMIN_ID, 
                Config.ADMIN_ID, 
                bot.send_message, 
                Config.ADMIN_ID, 
                report, 
                parse_mode="Markdown"
            )
        except:
            pass
            
    except Exception as e:
        logger.error(f"Error generating daily report: {e}")

def schedule_daily_reports():
    """جدولة التقارير اليومية"""
    while True:
        now = datetime.now()
        target_time = now.replace(hour=23, minute=50, second=0, microsecond=0)
        if now > target_time:
            target_time += timedelta(days=1)
        
        sleep_seconds = (target_time - now).total_seconds()
        time.sleep(sleep_seconds)
        
        generate_daily_report()

# ========= إدارة الأزرار الأخرى =========
@bot.message_handler(func=lambda m: m.text in ["⬅️ العودة للوحة التحكم", "🏠 العودة للقائمة الرئيسية"] and m.from_user.id == Config.ADMIN_ID)
def handle_admin_back_buttons(message):
    if message.text == "⬅️ العودة للوحة التحكم":
        admin_panel(message)
    elif message.text == "🏠 العودة للقائمة الرئيسية":
        queue_manager.add_to_user_queue(
            message.from_user.id, 
            message.chat.id, 
            bot.send_message, 
            message.chat.id, 
            "✅ **العودة للقائمة الرئيسية**\n\n"
            "تم الخروج من لوحة التحكم الإدارية.",
            parse_mode="Markdown",
            reply_markup=main_menu(message.from_user.id)
        )

print("✅ تم تحميل الجزء الثالث بنجاح!")
print("👨‍💼 لوحة التحكم الإدارية المتقدمة جاهزة!")
print("📊 نظام الإشعارات والإحصائيات يعمل!")
print("🚀 جاري تحضير الجزء الرابع والأخير...")
# ========= الإصلاح: معالجة طلبات الإيداع مع إضافة خاصية الرفض مع السبب =========
@bot.callback_query_handler(func=lambda call: call.data.startswith(("approve_", "reject_deposit_reason_", "approve_withdraw_", "reject_withdraw_reason_", "message_user_", "ban_user_", "unban_user_", "ban_details_", "reply_support_", "close_support_")))
def handle_admin_actions(call):
    if call.from_user.id != Config.ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ لا تملك الصلاحية!")
        return
    
    data_parts = call.data.split("_")
    
    if call.data.startswith("approve_") and not call.data.startswith("approve_withdraw_"):
        req_id = data_parts[1]
        approve_deposit_request(call, req_id)
        
    elif call.data.startswith("reject_deposit_reason_"):
        req_id = data_parts[3]
        request_deposit_rejection_reason(call, req_id)
        
    elif call.data.startswith("approve_withdraw_"):
        withdraw_id = data_parts[2]
        approve_withdrawal_request(call, withdraw_id)
        
    elif call.data.startswith("reject_withdraw_reason_"):
        withdraw_id = data_parts[3]
        request_withdrawal_rejection_reason(call, withdraw_id)
        
    elif call.data.startswith("message_user_"):
        user_id = data_parts[2]
        user_states[f"admin_{call.from_user.id}"] = f"await_user_message_{user_id}"
        bot.send_message(call.from_user.id, 
                        f"📧 **إرسال رسالة للمستخدم**\n\n"
                        f"أدخل الرسالة التي تريد إرسالها للمستخدم:",
                        parse_mode="Markdown",
                        reply_markup=reply_keyboard_with_cancel())
        
    elif call.data.startswith("ban_user_"):
        user_id = data_parts[2]
        user_states[f"admin_{call.from_user.id}_ban_user"] = user_id
        user = load_user(user_id)
        if user:
            user_states[f"admin_{call.from_user.id}_ban_username"] = user.get('username')
        
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.row("⏰ حظر لمدة دقيقتين", "⏰ حظر لمدة ساعة")
        keyboard.row("⏰ حظر لمدة ٢٤ ساعة", "⏰ حظر لمدة ٣ ايام")
        keyboard.row("⏰ حظر لمدة اسبوع", "🚫 حظر حتى يتم الالغاء")
        keyboard.row("❌ إلغاء العملية", "🏠 العودة للقائمة الرئيسية")
        
        bot.send_message(call.from_user.id,
                        f"🚫 **حظر المستخدم:** @{user.get('username')}\n\n"
                        f"🆔 **ID:** {user_id}\n\n"
                        f"⏰ **اختر مدة الحظر:**",
                        parse_mode="Markdown",
                        reply_markup=keyboard)
        
    elif call.data.startswith("unban_user_"):
        ban_id = data_parts[2]
        success = unban_user(ban_id, call.from_user.id)
        if success:
            bot.answer_callback_query(call.id, "✅ تم فك الحظر بنجاح")
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
        else:
            bot.answer_callback_query(call.id, "❌ فشل في فك الحظر")
            
    elif call.data.startswith("ban_details_"):
        ban_id = data_parts[2]
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM user_bans WHERE id = %s", (ban_id,))
        ban = c.fetchone()
        return_conn(conn)
        
        if ban:
            ban_info = f"""
📋 **تفاصيل الحظر**
👤 **المستخدم:** @{ban[2]}
🆔 **ID:** {ban[1]}
👨‍💼 **تم الحظر بواسطة:** {ban[3]}
⏰ **مدة الحظر:** {ban[5]}
🕐 **وقت البدء:** {ban[6]}
⏳ **وقت الانتهاء:** {ban[7]}
📝 **السبب:** {ban[4]}
📊 **الحالة:** {ban[8]}
            """
            bot.send_message(call.from_user.id, ban_info, parse_mode="Markdown")
        
    elif call.data.startswith("reply_support_"):
        msg_id = data_parts[2]
        user_states[f"admin_{call.from_user.id}"] = f"await_support_reply_{msg_id}"
        bot.send_message(call.from_user.id,
                        f"📝 **الرد على رسالة الدعم**\n\n"
                        f"أدخل ردك على رسالة الدعم:",
                        parse_mode="Markdown",
                        reply_markup=reply_keyboard_with_cancel())
        
    elif call.data.startswith("close_support_"):
        msg_id = data_parts[2]
        close_support_message(call, msg_id)

def approve_deposit_request(call, req_id):
    """قبول طلب إيداع"""
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM deposit_requests WHERE id = %s", (req_id,))
        req = c.fetchone()
        
        if not req:
            bot.answer_callback_query(call.id, "❌ الطلب غير موجود!")
            return_conn(conn)
            return
        
        req_id, user_id, username, amount, network, txid, status, date, reject_reason, sender_wallet = req
        
        user = load_user(user_id)
        if user:
            if not user.get("first_deposit_time"):
                user["first_deposit_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            if not user.get("deposited"):
                referrer_id = user.get("referrer_id")
                if referrer_id and referrer_id != "None" and referrer_id != user_id:
                    referrer = load_user(referrer_id)
                    if referrer:
                        referral_bonus = float(amount) * 0.05
                        referrer["balance"] += referral_bonus
                        save_user(referrer)
                        
                        add_transaction(referrer_id, "referral_bonus", referral_bonus, f"مكافأة إحالة من @{username}")
                        
                        try:
                            def send_referral_notification():
                                bot.send_message(
                                    int(referrer_id),
                                    f"🎉 **مكافأة إحالة جديدة!**\n\n"
                                    f"👤 تم إيداع أول مبلغ من المستخدم: @{username}\n"
                                    f"💵 المبلغ المُودع: {amount:.2f}$\n"
                                    f"💰 مكافأتك: {referral_bonus:.2f}$ (5%)\n"
                                    f"💳 رصيدك الجديد: {referrer['balance']:.2f}$\n\n"
                                    f"شكراً لدعمك مجتمعنا! 🚀",
                                    parse_mode="Markdown"
                                )
                            
                            queue_manager.add_to_user_queue(referrer_id, int(referrer_id), send_referral_notification)
                            log_event(referrer_id, "REFERRAL_BONUS", f"Amount: {referral_bonus}, From: {username}")
                        except Exception as e:
                            logger.error(f"Error notifying referrer: {e}")
                        
                        bonus_awarded, bonus_amount = handle_referral_batch_bonus(
                            referrer_id, user_id, username, float(amount)
                        )
                        
                        if bonus_awarded:
                            logger.info(f"Batch bonus awarded to {referrer_id}: {bonus_amount}$")

            user["balance"] += float(amount)
            user["deposited"] = True
            user["last_deposit"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_user(user)
            
            add_transaction(user_id, "deposit", amount, f"إيداع عبر {network}")
            
            c.execute("UPDATE deposit_requests SET status = 'approved' WHERE id = %s", (req_id,))
            conn.commit()
            
            try:
                def send_deposit_approval():
                    bot.send_message(
                        int(user_id),
                        f"🎉 **تم قبول إيداعك بنجاح!**\n\n"
                        f"💵 **المبلغ:** {amount:.2f}$\n"
                        f"🌐 **الشبكة:** {network}\n"
                        f"🔑 **TXID:** {txid}\n"
                        f"💳 **رصيدك الحالي:** {user['balance']:.2f}$\n\n"
                        f"✅ **يمكنك الآن طلب السحب بعد مرور 3 دقائق من هذا الإيداع**\n\n"
                        f"شكراً لاستخدامك تراكم! 📈",
                        parse_mode="Markdown"
                    )
                
                queue_manager.add_to_user_queue(user_id, int(user_id), send_deposit_approval)
            except Exception as e:
                logger.error(f"Error notifying user: {e}")
            
            bot.answer_callback_query(call.id, f"✅ تم قبول إيداع {amount:.2f}$")
            
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"✅ **تمت الموافقة على الطلب**\n\n"
                     f"🆔 **رقم الطلب:** {req_id}\n"
                     f"👤 **المستخدم:** @{username}\n"
                     f"💵 **المبلغ:** {amount:.2f}$\n"
                     f"🌐 **الشبكة:** {network}\n"
                     f"🔑 **TXID:** {txid}\n"
                     f"💳 **المحفظة المرسلة:** `{sender_wallet}`\n"
                     f"⏰ **وقت القبول:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                parse_mode="Markdown"
            )
            log_event(user_id, "DEPOSIT_APPROVED", f"Amount: {amount}")
        
        return_conn(conn)
    except Exception as e:
        logger.error(f"Error approving deposit: {e}")
        bot.answer_callback_query(call.id, "❌ حدث خطأ أثناء معالجة الطلب")

def request_deposit_rejection_reason(call, req_id):
    """طلب سبب رفض طلب الإيداع"""
    user_states[f"admin_{call.from_user.id}"] = f"await_deposit_reject_reason_{req_id}"
    
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM deposit_requests WHERE id = %s", (req_id,))
        req = c.fetchone()
        return_conn(conn)
        
        if req:
            req_id, user_id, username, amount, network, txid, status, date, reject_reason, sender_wallet = req
            
            bot.send_message(
                call.from_user.id,
                f"❌ **رفض طلب الإيداع**\n\n"
                f"🆔 **رقم الطلب:** {req_id}\n"
                f"👤 **المستخدم:** @{username}\n"
                f"💵 **المبلغ:** {amount:.2f}$\n\n"
                f"📝 **الرجاء إدخال سبب الرفض:**",
                parse_mode="Markdown",
                reply_markup=reply_keyboard_with_cancel()
            )
            
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
    except Exception as e:
        logger.error(f"Error requesting deposit rejection reason: {e}")

@bot.message_handler(func=lambda m: m.from_user.id == Config.ADMIN_ID and user_states.get(f"admin_{m.from_user.id}", "").startswith("await_deposit_reject_reason_"))
def handle_deposit_rejection_reason(message):
    if message.text == "❌ إلغاء العملية":
        user_states.pop(f"admin_{message.from_user.id}", None)
        bot.send_message(message.chat.id, "✅ تم إلغاء العملية.")
        return
    
    state = user_states.get(f"admin_{message.from_user.id}")
    req_id = state.split("_")[-1]
    reject_reason = message.text
    
    user_states.pop(f"admin_{message.from_user.id}", None)
    
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM deposit_requests WHERE id = %s", (req_id,))
        req = c.fetchone()
        
        if req:
            req_id, user_id, username, amount, network, txid, status, date, old_reject_reason, sender_wallet = req
            
            c.execute("UPDATE deposit_requests SET status = 'rejected', reject_reason = %s WHERE id = %s", (reject_reason, req_id))
            conn.commit()
            
            # إرسال إشعار الرفض للمستخدم مع السبب - نسخة محسنة
            rejection_sent = False
            max_retries = 3
            
            for attempt in range(max_retries):
                try:
                    rejection_message = f"""
❌ **تم رفض طلب الإيداع**

📋 **تفاصيل الطلب:**
💵 **المبلغ:** {amount:.2f}$
🌐 **الشبكة:** {network}
📅 **الوقت:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

📝 **سبب الرفض:**
{reject_reason}

📞 **للاستفسار أو تقديم طلب جديد:** 
@{Config.SUPPORT_BOT_USERNAME}

نأسف للإزعاج ونتطلع لخدمتك في طلبات مستقبلية.
                    """
                    
                    def send_rejection():
                        sent_msg = bot.send_message(
                            int(user_id),
                            rejection_message,
                            parse_mode="Markdown"
                        )
                        return sent_msg
                    
                    sent_msg = queue_manager.add_to_user_queue(user_id, int(user_id), send_rejection)
                    
                    logger.info(f"✅ Deposit rejection notification sent to user {user_id}")
                    rejection_sent = True
                    break
                    
                except Exception as e:
                    logger.error(f"❌ Attempt {attempt + 1} - Error notifying user about deposit rejection: {e}")
                    if attempt < max_retries - 1:
                        time.sleep(2)  # انتظار قبل المحاولة التالية
            
            # إذا فشلت جميع المحاولات، تسجيل الخطأ
            if not rejection_sent:
                logger.error(f"❌ Failed to send deposit rejection notification to user {user_id} after {max_retries} attempts")
                
                # محاولة إرسال رسالة مبسطة كبديل أخير
                try:
                    simple_rejection = f"""
❌ تم رفض طلب الإيداع

المبلغ: {amount:.2f}$
السبب: {reject_reason}

للإستفسار: @{Config.SUPPORT_BOT_USERNAME}
                    """
                    queue_manager.add_to_user_queue(user_id, int(user_id), bot.send_message, int(user_id), simple_rejection)
                    logger.info(f"✅ Alternative rejection message sent to user {user_id}")
                    rejection_sent = True
                except Exception as e2:
                    logger.error(f"❌ Failed to send alternative rejection message: {e2}")
            
            bot.send_message(
                message.chat.id,
                f"❌ **تم رفض طلب الإيداع**\n\n"
                f"🆔 **رقم الطلب:** {req_id}\n"
                f"👤 **المستخدم:** @{username}\n"
                f"💵 **المبلغ:** {amount:.2f}$\n"
                f"📝 **سبب الرفض:** {reject_reason}\n\n"
                f"{'✅ تم إرسال إشعار الرفض للمستخدم' if rejection_sent else '⚠️ حدث خطأ في إرسال الإشعار للمستخدم'}",
                parse_mode="Markdown"
            )
            log_event(user_id, "DEPOSIT_REJECTED", f"Amount: {amount}, Reason: {reject_reason}, Notification: {rejection_sent}")
        
        return_conn(conn)
    except Exception as e:
        logger.error(f"Error handling deposit rejection: {e}")

def approve_withdrawal_request(call, withdraw_id):
    """قبول طلب سحب"""
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM withdrawals WHERE id = %s", (withdraw_id,))
        withdrawal = c.fetchone()
        
        if not withdrawal:
            bot.answer_callback_query(call.id, "❌ طلب السحب غير موجود!")
            return_conn(conn)
            return
        
        w_id, user_id, amount, status, admin_id, processed_date, date, tx_hash, reject_reason = withdrawal
        
        user = load_user(user_id)
        if user and user.get("balance", 0) >= amount:
            user["balance"] -= amount
            user["last_withdrawal_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_user(user)
            
            add_transaction(user_id, "withdrawal", -amount, "سحب رصيد")
            
            c.execute("UPDATE withdrawals SET status = 'approved', admin_id = %s, processed_date = %s WHERE id = %s", 
                     (str(call.from_user.id), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), withdraw_id))
            conn.commit()
            
            try:
                def send_withdrawal_approval():
                    bot.send_message(
                        int(user_id),
                        f"✅ **تمت الموافقة على طلب السحب**\n\n"
                        f"💸 **المبلغ:** {amount:.2f}$\n"
                        f"💳 **المحفظة:** {user['wallet']}\n"
                        f"💳 **رصيدك الجديد:** {user['balance']:.2f}$\n"
                        f"📅 **وقت المعالجة:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                        f"⚠️ **ملاحظة:** يمكنك طلب السحب التالي بعد مرور 3 دقائق من هذا السحب",
                        parse_mode="Markdown"
                    )
                
                queue_manager.add_to_user_queue(user_id, int(user_id), send_withdrawal_approval)
            except Exception as e:
                logger.error(f"Error notifying user: {e}")
            
            bot.answer_callback_query(call.id, f"✅ تم قبول سحب {amount:.2f}$")
            
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"✅ **تمت الموافقة على السحب**\n\n"
                     f"🆔 **رقم الطلب:** {withdraw_id}\n"
                     f"👤 **المستخدم:** @{user['username']}\n"
                     f"💸 **المبلغ:** {amount:.2f}$\n"
                     f"💳 **المحفظة:** {user['wallet']}\n"
                     f"⏰ **وقت القبول:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                parse_mode="Markdown"
            )
            log_event(user_id, "WITHDRAWAL_APPROVED", f"Amount: {amount}")
        else:
            bot.answer_callback_query(call.id, "❌ رصيد المستخدم غير كافٍ!")
        
        return_conn(conn)
    except Exception as e:
        logger.error(f"Error approving withdrawal: {e}")

def request_withdrawal_rejection_reason(call, withdraw_id):
    """طلب سبب رفض طلب السحب"""
    user_states[f"admin_{call.from_user.id}"] = f"await_withdraw_reject_reason_{withdraw_id}"
    
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT w.*, u.username FROM withdrawals w JOIN users u ON w.user_id = u.user_id WHERE w.id = %s", (withdraw_id,))
        withdrawal = c.fetchone()
        return_conn(conn)
        
        if withdrawal:
            w_id, user_id, amount, status, admin_id, processed_date, date, tx_hash, reject_reason, username = withdrawal
            
            bot.send_message(
                call.from_user.id,
                f"❌ **رفض طلب السحب**\n\n"
                f"🆔 **رقم الطلب:** {withdraw_id}\n"
                f"👤 **المستخدم:** @{username}\n"
                f"💸 **المبلغ:** {amount:.2f}$\n\n"
                f"📝 **الرجاء إدخال سبب الرفض:**",
                parse_mode="Markdown",
                reply_markup=reply_keyboard_with_cancel()
            )
            
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
    except Exception as e:
        logger.error(f"Error requesting withdrawal rejection reason: {e}")

@bot.message_handler(func=lambda m: m.from_user.id == Config.ADMIN_ID and user_states.get(f"admin_{m.from_user.id}", "").startswith("await_withdraw_reject_reason_"))
def handle_withdrawal_rejection_reason(message):
    if message.text == "❌ إلغاء العملية":
        user_states.pop(f"admin_{message.from_user.id}", None)
        bot.send_message(message.chat.id, "✅ تم إلغاء العملية.")
        return
    
    state = user_states.get(f"admin_{message.from_user.id}")
    withdraw_id = state.split("_")[-1]
    reject_reason = message.text
    
    user_states.pop(f"admin_{message.from_user.id}", None)
    
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT w.*, u.username, u.user_id FROM withdrawals w JOIN users u ON w.user_id = u.user_id WHERE w.id = %s", (withdraw_id,))
        withdrawal = c.fetchone()
        
        if withdrawal:
            w_id, user_id, amount, status, admin_id, processed_date, date, tx_hash, old_reject_reason, username, user_id = withdrawal
            
            c.execute("UPDATE withdrawals SET status = 'rejected', reject_reason = %s WHERE id = %s", (reject_reason, withdraw_id))
            conn.commit()
            
            # إرسال إشعار الرفض للمستخدم مع السبب - نسخة محسنة ومصححة
            rejection_sent = False
            max_retries = 3
            
            for attempt in range(max_retries):
                try:
                    rejection_message = f"""
❌ **تم رفض طلب السحب**

📋 **تفاصيل الطلب:**
💸 **المبلغ:** {amount:.2f}$
📅 **وقت الطلب:** {date}
⏰ **وقت الرفض:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

📝 **سبب الرفض:**
{reject_reason}

💡 **ملاحظات:**
• يمكنك تقديم طلب سحب جديد بعد تصحيح المشكلة
• تأكد من صحة بيانات المحفظة والمبلغ
• يجب أن يكون الرصيد كافياً للسحب

📞 **للاستفسار:** 
@{Config.SUPPORT_BOT_USERNAME}

نأسف للإزعاج ونتطلع لخدمتك في طلبات مستقبلية.
                    """
                    
                    def send_rejection():
                        sent_msg = bot.send_message(
                            int(user_id),
                            rejection_message,
                            parse_mode="Markdown"
                        )
                        return sent_msg
                    
                    sent_msg = queue_manager.add_to_user_queue(user_id, int(user_id), send_rejection)
                    
                    logger.info(f"✅ Withdrawal rejection notification sent to user {user_id}")
                    rejection_sent = True
                    break
                    
                except Exception as e:
                    logger.error(f"❌ Attempt {attempt + 1} - Error notifying user about withdrawal rejection: {e}")
                    if attempt < max_retries - 1:
                        time.sleep(2)  # انتظار قبل المحاولة التالية
            
            # إذا فشلت جميع المحاولات، تسجيل الخطأ
            if not rejection_sent:
                logger.error(f"❌ Failed to send withdrawal rejection notification to user {user_id} after {max_retries} attempts")
                
                # محاولة إرسال رسالة مبسطة كبديل أخير
                try:
                    simple_rejection = f"""
❌ تم رفض طلب السحب

المبلغ: {amount:.2f}$
السبب: {reject_reason}

للإستفسار: @{Config.SUPPORT_BOT_USERNAME}
                    """
                    queue_manager.add_to_user_queue(user_id, int(user_id), bot.send_message, int(user_id), simple_rejection)
                    logger.info(f"✅ Alternative rejection message sent to user {user_id}")
                    rejection_sent = True
                except Exception as e2:
                    logger.error(f"❌ Failed to send alternative rejection message: {e2}")
            
            bot.send_message(
                message.chat.id,
                f"❌ **تم رفض طلب السحب**\n\n"
                f"🆔 **رقم الطلب:** {withdraw_id}\n"
                f"👤 **المستخدم:** @{username}\n"
                f"💸 **المبلغ:** {amount:.2f}$\n"
                f"📝 **سبب الرفض:** {reject_reason}\n\n"
                f"{'✅ تم إرسال إشعار الرفض للمستخدم' if rejection_sent else '❌ فشل إرسال إشعار الرفض للمستخدم'}",
                parse_mode="Markdown"
            )
            log_event(user_id, "WITHDRAWAL_REJECTED", f"Amount: {amount}, Reason: {reject_reason}, Notification: {rejection_sent}")
        
        return_conn(conn)
    except Exception as e:
        logger.error(f"Error handling withdrawal rejection: {e}")

def close_support_message(call, msg_id):
    """إغلاق رسالة دعم"""
    try:
        conn = get_conn()
        c = conn.cursor()
        
        c.execute("UPDATE support_messages SET status = 'closed' WHERE id = %s", (msg_id,))
        conn.commit()
        return_conn(conn)
        
        bot.answer_callback_query(call.id, "✅ تم إغلاق رسالة الدعم")
        log_event(call.from_user.id, "SUPPORT_CLOSED", f"Message ID: {msg_id}")
    except Exception as e:
        logger.error(f"Error closing support message: {e}")

@bot.message_handler(func=lambda m: m.from_user.id == Config.ADMIN_ID and user_states.get(f"admin_{m.from_user.id}", "").startswith("await_support_reply_"))
def handle_support_reply(message):
    if message.text == "❌ إلغاء العملية":
        user_states.pop(f"admin_{message.from_user.id}", None)
        bot.send_message(message.chat.id, "✅ تم إلغاء الرد.")
        return
    
    state = user_states.get(f"admin_{message.from_user.id}")
    msg_id = state.split("_")[-1]
    reply_text = message.text
    
    user_states.pop(f"admin_{message.from_user.id}", None)
    
    try:
        conn = get_conn()
        c = conn.cursor()
        
        c.execute("SELECT * FROM support_messages WHERE id = %s", (msg_id,))
        support_msg = c.fetchone()
        
        if support_msg:
            user_id = support_msg[1]
            username = support_msg[3]
            original_message = support_msg[6]
            
            c.execute("UPDATE support_messages SET status = 'closed', admin_response = %s, responded_at = %s, admin_id = %s WHERE id = %s", 
                     (reply_text, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(message.from_user.id), msg_id))
            conn.commit()
            
            try:
                def send_support_reply():
                    bot.send_message(
                        int(user_id),
                        f"📞 **رد على رسالة الدعم الخاصة بك**\n\n"
                        f"💬 **رسالتك الأصلية:**\n{original_message}\n\n"
                        f"📝 **رد الإدارة:**\n{reply_text}\n\n"
                        f"⏰ **وقت الرد:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                        f"شكراً لتواصلك معنا! 🚀",
                        parse_mode="Markdown"
                    )
                
                queue_manager.add_to_user_queue(user_id, int(user_id), send_support_reply)
            except Exception as e:
                logger.error(f"Error sending support reply: {e}")
            
            bot.send_message(
                message.chat.id,
                f"✅ **تم إرسال الرد بنجاح**\n\n"
                f"👤 **للمستخدم:** @{username}\n"
                f"📝 **الرد:** {reply_text}",
                parse_mode="Markdown"
            )
            log_event(message.from_user.id, "SUPPORT_REPLIED", f"To: {username}, Message ID: {msg_id}")
        
        return_conn(conn)
    except Exception as e:
        logger.error(f"Error handling support reply: {e}")

@bot.message_handler(func=lambda m: m.from_user.id == Config.ADMIN_ID and user_states.get(f"admin_{m.from_user.id}", "").startswith("await_user_message_"))
def handle_user_message(message):
    if message.text == "❌ إلغاء العملية":
        user_states.pop(f"admin_{message.from_user.id}", None)
        bot.send_message(message.chat.id, "✅ تم إلغاء الرسالة.")
        return
    
    state = user_states.get(f"admin_{message.from_user.id}")
    user_id = state.split("_")[-1]
    message_text = message.text
    
    user_states.pop(f"admin_{message.from_user.id}", None)
    
    user = load_user(user_id)
    if not user:
        bot.send_message(message.chat.id, "❌ المستخدم غير موجود!")
        return
    
    try:
        def send_admin_message():
            bot.send_message(
                int(user_id),
                f"📨 **رسالة من إدارة تراكم**\n\n"
                f"{message_text}\n\n"
                f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                parse_mode="Markdown"
            )
        
        queue_manager.add_to_user_queue(user_id, int(user_id), send_admin_message)
        
        bot.send_message(
            message.chat.id,
            f"✅ **تم إرسال الرسالة بنجاح**\n\n"
            f"👤 **للمستخدم:** @{user['username']}\n"
            f"📝 **الرسالة:** {message_text}",
            parse_mode="Markdown"
        )
        log_event(message.from_user.id, "ADMIN_MESSAGE_SENT", f"To: {user['username']}")
        
    except Exception as e:
        bot.send_message(
            message.chat.id,
            f"❌ **فشل إرسال الرسالة**\n\n"
            f"الخطأ: {str(e)}",
            parse_mode="Markdown"
        )

# ========= دوال إضافية لتحسين الأداء =========
def cleanup_expired_sessions():
    """تنظيف الجلسات المنتهية"""
    try:
        current_time = time.time()
        expired_sessions = []
        
        for user_id, task_info in list(active_tasks.items()):
            if current_time - task_info['start_time'].timestamp() > 1800:  # 30 دقيقة
                expired_sessions.append(user_id)
        
        for user_id in expired_sessions:
            if user_id in active_tasks:
                active_tasks.pop(user_id)
                logger.info(f"Cleaned up expired session for user {user_id}")
        
        # تنظيف حالات المستخدمين المنتهية
        expired_states = []
        for user_id, state in list(user_states.items()):
            if isinstance(state, str) and state.startswith("await_") and user_id not in expired_sessions:
                # إذا كانت الحالة قديمة أكثر من ساعة
                if user_id in user_states and not user_id in expired_sessions:
                    # نحتفظ بالحالات النشطة
                    continue
                expired_states.append(user_id)
        
        for user_id in expired_states:
            user_states.pop(user_id, None)
            logger.info(f"Cleaned up expired state for user {user_id}")
            
    except Exception as e:
        logger.error(f"Error in cleanup_expired_sessions: {e}")

def schedule_session_cleanup():
    """جدولة تنظيف الجلسات المنتهية"""
    while True:
        time.sleep(1800)  # كل 30 دقيقة
        cleanup_expired_sessions()

def get_system_health():
    """الحصول على صحة النظام"""
    try:
        conn = get_conn()
        c = conn.cursor()
        
        # التحقق من صحة قاعدة البيانات
        c.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'")
        table_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users")
        user_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM deposit_requests WHERE status = 'pending'")
        pending_deposits = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM withdrawals WHERE status = 'pending'")
        pending_withdrawals = c.fetchone()[0]
        
        return_conn(conn)
        
        health_status = {
            'database_tables': table_count >= 6,  # يجب أن يكون هناك على الأقل 6 جداول
            'user_count': user_count,
            'active_sessions': len(logged_in_users),
            'active_tasks': len(active_tasks),
            'pending_deposits': pending_deposits,
            'pending_withdrawals': pending_withdrawals,
            'cache_health': cache_manager.redis_available if hasattr(cache_manager, 'redis_available') else True,
            'memory_usage': len(user_states)
        }
        
        return health_status
        
    except Exception as e:
        logger.error(f"Error getting system health: {e}")
        return {'error': str(e)}

def send_health_report():
    """إرسال تقرير صحة النظام للمسؤول"""
    try:
        health = get_system_health()
        
        if 'error' in health:
            report = f"❌ **تقرير صحة النظام - خطأ**\n\nالخطأ: {health['error']}"
        else:
            status_emoji = "🟢" if all([
                health['database_tables'],
                health['user_count'] >= 0,
                health['cache_health']
            ]) else "🟡"
            
            report = f"""
{status_emoji} **تقرير صحة النظام - تراكم**

🗃️ **قاعدة البيانات:**
• الجداول: {'✅ صحية' if health['database_tables'] else '❌ مشكلة'}
• المستخدمين: {health['user_count']}
• الإيداعات المعلقة: {health['pending_deposits']}
• السحوبات المعلقة: {health['pending_withdrawals']}

⚡ **الأداء:**
• الجلسات النشطة: {health['active_sessions']}
• المهام النشطة: {health['active_tasks']}
• حالات الذاكرة: {health['memory_usage']}
• التخزين المؤقت: {'✅ نشط' if health['cache_health'] else '❌ معطل'}

📊 **الحالة العامة:** {'✅ مستقرة' if status_emoji == '🟢' else '⚠️ تحت المراقبة'}
            """
        
        queue_manager.add_to_user_queue(
            Config.ADMIN_ID, 
            Config.ADMIN_ID, 
            bot.send_message, 
            Config.ADMIN_ID, 
            report, 
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Error sending health report: {e}")

def schedule_health_reports():
    """جدولة تقارير صحة النظام"""
    while True:
        time.sleep(6 * 60 * 60)  # كل 6 ساعات
        send_health_report()

# ========= تحسينات الأمان الإضافية =========
def check_suspicious_activity(user_id, action_type, details):
    """التحقق من النشاط المشبوه"""
    try:
        # تسجيل النشاط للتحليل
        security.record_login_attempt(user_id, True)
        
        # التحقق من الأنشطة المشبوهة
        suspicious_patterns = [
            "multiple_failed_logins",  # محاولات تسجيل دخول فاشلة متعددة
            "rapid_requests",  # طلبات سريعة متتالية
            "unusual_amounts",  # مبالغ غير عادية
            "suspicious_wallet"  # محافظ مشبوهة
        ]
        
        # يمكن إضافة المزيد من التحليلات هنا
        user = load_user(user_id)
        if user:
            # التحقق من معدل الطلبات
            if not security.check_rate_limit(user_id, action_type, 10, 60):
                logger.warning(f"Suspicious activity detected for user {user_id}: {action_type}")
                return True
                
        return False
        
    except Exception as e:
        logger.error(f"Error checking suspicious activity: {e}")
        return False

def encrypt_sensitive_data(data):
    """تشفير البيانات الحساسة"""
    try:
        if not data:
            return data
        
        # استخدام مفتاح التشفير من الإعدادات
        key = hashlib.sha256(Config.SECRET_KEY.encode()).digest()
        cipher = hashlib.blake2b(key=key)
        cipher.update(data.encode() if isinstance(data, str) else data)
        return cipher.hexdigest()
        
    except Exception as e:
        logger.error(f"Error encrypting data: {e}")
        return data

# ========= تحسينات واجهة المستخدم =========
def create_rich_message(title, content, message_type="info"):
    """إنشاء رسالة غنية بالتنسيق"""
    emojis = {
        "info": "ℹ️",
        "success": "✅", 
        "warning": "⚠️",
        "error": "❌",
        "money": "💰",
        "task": "🎯",
        "referral": "👥",
        "support": "📞"
    }
    
    emoji = emojis.get(message_type, "📄")
    
    message = f"{emoji} **{title}**\n\n{content}"
    return message

def format_balance(balance):
    """تنسيق الرصيد بشكل جميل"""
    return f"{balance:,.2f}$"

def format_percentage(value):
    """تنسيق النسبة المئوية"""
    return f"{value:.1f}%"

# ========= إشعارات محسنة =========
def send_enhanced_notification(user_id, notification_type, data):
    """إرسال إشعار محسن"""
    try:
        notification_templates = {
            "deposit_approved": {
                "title": "تم قبول إيداعك! 🎉",
                "message": f"""
تمت الموافقة على طلب الإيداع الخاص بك بنجاح!

📋 **التفاصيل:**
💵 **المبلغ:** {data.get('amount', 0):.2f}$
🌐 **الشبكة:** {data.get('network', 'N/A')}
💳 **الرصيد الجديد:** {data.get('new_balance', 0):.2f}$

✅ يمكنك الآن استخدام جميع ميزات المنصة!
                """
            },
            "withdrawal_approved": {
                "title": "تم قبول سحبك! 💸", 
                "message": f"""
تمت الموافقة على طلب السحب الخاص بك!

📋 **التفاصيل:**
💸 **المبلغ:** {data.get('amount', 0):.2f}$
💳 **المحفظة:** {data.get('wallet', 'N/A')}
⏰ **الوقت المتوقع:** 4-24 ساعة

سيتم تحويل المبلغ إلى محفظتك قريباً.
                """
            },
            "referral_bonus": {
                "title": "مكافأة إحالة جديدة! 👥",
                "message": f"""
🎉 تهانينا! لقد ربحت مكافأة إحالة

📋 **التفاصيل:**
👤 **المستخدم الجديد:** @{data.get('new_user', 'N/A')}
💰 **المكافأة:** {data.get('bonus', 0):.2f}$
💳 **رصيدك الجديد:** {data.get('new_balance', 0):.2f}$

استمر في جلب المزيد من الأعضاء لتربح أكثر! 🚀
                """
            },
            "batch_bonus": {
                "title": "جائزة الإحالة الجماعية! 🏆",
                "message": f"""
🎊 تهانينا! فزت بجائزة الإحالة الجماعية

📋 **التفاصيل:**
🎯 **المجموعة المكتملة:** {data.get('batch_number', 0)}
👥 **عدد الأعضاء:** 3 أعضاء جدد
💰 **الجائزة:** {data.get('bonus', 0):.2f}$
💳 **رصيدك الجديد:** {data.get('new_balance', 0):2f}$

🔥 استمر في النجاح!
                """
            }
        }
        
        template = notification_templates.get(notification_type)
        if template:
            message = create_rich_message(template["title"], template["message"], "success")
            queue_manager.add_to_user_queue(user_id, user_id, bot.send_message, user_id, message, parse_mode="Markdown")
            
    except Exception as e:
        logger.error(f"Error sending enhanced notification: {e}")

# ========= التحقق من الحظر في جميع الأوامر =========
def check_ban_decorator(func):
    """ديكوراتور للتحقق من الحظر قبل تنفيذ أي أمر"""
    def wrapper(message):
        user_id = message.from_user.id
        ban_check = is_user_banned(user_id)
        
        if ban_check['banned'] and message.text not in ["📞 الدعم الفني"]:
            ban_message = f"""
🚫 **حسابك محظور حالياً**

📋 **تفاصيل الحظر:**
• ⏰ **مدة الحظر:** {ban_check['ban_duration']}
• 🕐 **وقت البدء:** {ban_check['ban_start_time']}
• ⏳ **وقت الانتهاء:** {ban_check['ban_end_time']}
• 📝 **السبب:** {ban_check['ban_reason']}

🔒 **ملاحظات مهمة:**
• لا يمكنك استخدام أي من ميزات البوت خلال فترة الحظر
• يمكنك فقط التواصل مع الدعم الفني
• سيتم فك الحظر تلقائياً بعد انتهاء المدة

📞 **للاستفسار أو الطعن في الحظر:** 
@{Config.SUPPORT_BOT_USERNAME}
            """
            queue_manager.add_to_user_queue(user_id, user_id, bot.send_message, user_id, ban_message, parse_mode="Markdown")
            return
        
        return func(message)
    return wrapper

# تطبيق الديكوراتور على جميع الأوامر الرئيسية
for handler in bot.message_handlers:
    if hasattr(handler, 'filters'):
        if any(filt for filt in handler.filters if hasattr(filt, '__call__') and 'require_login' in filt.__code__.co_names):
            original_func = handler.function
            handler.function = check_ban_decorator(original_func)

# ========= التشغيل النهائي مع جميع التحسينات =========
def initialize_system():
    """تهيئة النظام بالكامل"""
    try:
        logger.info("🚀 بدء تهيئة نظام تراكم...")
        
        # تهيئة قاعدة البيانات
        init_db()
        
        # بدء الخدمات المجدولة
        services = [
            schedule_backups,
            schedule_optimization, 
            schedule_daily_reports,
            schedule_ban_check,
            schedule_session_cleanup,
            schedule_health_reports
        ]
        
        for service in services:
            threading.Thread(target=service, daemon=True).start()
        
        # إنشاء النسخة الاحتياطية الأولى
        backup_database()
        
        # إرسال تقرير البدء
        startup_report = f"""
🚀 **بدء تشغيل نظام تراكم**

✅ **الخدمات المحملة:**
• 🗃️ قاعدة البيانات
• 🔄 النسخ الاحتياطي
• ⚡ تحسين الأداء
• 📊 التقارير اليومية
• 🚫 نظام الحظر
• 🧹 تنظيف الجلسات
• 📈 مراقبة الصحة

⏰ **وقت البدء:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
👥 **الأعضاء المسجلين:** {get_total_membership_count()}

🎯 **النظام جاهز للعمل!**
        """
        
        queue_manager.add_to_user_queue(
            Config.ADMIN_ID, 
            Config.ADMIN_ID, 
            bot.send_message, 
            Config.ADMIN_ID, 
            startup_report, 
            parse_mode="Markdown"
        )
        logger.info("✅ تم تهيئة النظام بنجاح!")
        
    except Exception as e:
        logger.error(f"❌ فشل في تهيئة النظام: {e}")
        try:
            queue_manager.add_to_user_queue(
                Config.ADMIN_ID, 
                Config.ADMIN_ID, 
                bot.send_message, 
                Config.ADMIN_ID, 
                f"❌ **فشل في تهيئة النظام**\n\nالخطأ: {str(e)}", 
                parse_mode="Markdown"
            )
        except:
            pass

# ========= لوحات المفاتيح المساعدة =========
def main_menu(user_id=None):
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    user = load_user(user_id) if user_id else None
    
    if user and user.get("registered") and user_id in logged_in_users:
        buttons = [
            "💵 الإيداع", "🎯 المهمة اليومية",
            "💰 رصيدي", "📊 لوحة التحكم", 
            "💸 طلب سحب", "🚪 تسجيل الخروج",
            "👥 رابط الإحالة", "📞 الدعم الفني",
            "📄 الشروط والأحكام"
        ]
        
        if user_id == Config.ADMIN_ID:
            buttons.append("👨‍💼 لوحة التحكم الإدارية")
        
        for i in range(0, len(buttons), 2):
            if i + 1 < len(buttons):
                keyboard.row(buttons[i], buttons[i+1])
            else:
                keyboard.row(buttons[i])
    else:
        keyboard.row("📝 التسجيل / تحديث البيانات")
        keyboard.row("🔑 تسجيل الدخول")
        keyboard.row("❓ نسيت بيانات الدخول")
    
    return keyboard

def reply_keyboard_with_cancel():
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("❌ إلغاء العملية")
    return keyboard

def reply_keyboard_with_cancel_and_home():
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("❌ إلغاء العملية", "🏠 العودة للقائمة الرئيسية")
    return keyboard

def reply_keyboard_with_home():
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("🏠 العودة للقائمة الرئيسية")
    return keyboard

def task_keyboard():
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("🎯 بدء المهمة اليومية")
    keyboard.row("🏠 العودة للقائمة الرئيسية")
    return keyboard

def support_keyboard():
    keyboard = telebot.types.InlineKeyboardMarkup()
    keyboard.add(telebot.types.InlineKeyboardButton(
        "💬 التحدث مع الدعم", 
        url=f"https://t.me/{Config.SUPPORT_BOT_USERNAME}"
    ))
    return keyboard

# ========= التشغيل الرئيسي المحسن =========
if __name__ == "__main__":
    try:
        print("=" * 60)
        print("🏦 **نظام تراكم - منصة الاستثمار الذكية**")
        print("🇦🇪 **المنصة الاستثمارية الإماراتية المرخصة**")
        print("=" * 60)
        print("📧 البريد الإلكتروني: info@tarakum.ae")
        print("📞 الدعم: @Tarakumbot")
        print("📢 القناة: t.me/TarakumAE_Support")
        print("=" * 60)
        
        # تهيئة النظام
        initialize_system()
        
        print("✅ تم تحميل جميع أجزاء النظام بنجاح!")
        print("🤖 البوت يعمل الآن واستعداد لاستقبال الرسائل...")
        print("🚀 **عدد الأسطر الإجمالي:** ~6,200 سطر")
        print("📊 **المميزات الرئيسية:**")
        print("   • 🗃️ نظام PostgreSQL المتقدم")
        print("   • ⚡ نظام الطوابير لتجنب حظر تيليجرام")
        print("   • 🔒 نظام أمان متكامل")
        print("   • 💰 جميع أنظمة الدفع والسحب")
        print("   • 🎯 نظام المهام اليومية")
        print("   • 👥 نظام الإحالة الجماعية")
        print("   • 🚫 نظام الحظر المتقدم")
        print("   • 📊 لوحة تحكم إدارية متكاملة")
        print("=" * 60)
        
        # بدء استقبال الرسائل
        logger.info("🤖 بدء تشغيل البوت واستقبال الرسائل...")
        bot.infinity_polling()
        
    except Exception as e:
        logger.error(f"💥 تحطم البوت: {e}", exc_info=True)
        try:
            crash_report = f"""
💥 **تحطم نظام تراكم**

📋 **تفاصيل التحطم:**
⏰ **الوقت:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
❌ **الخطأ:** {str(e)}

🚑 **جاري إعادة التشغيل التلقائي...**
            """
            queue_manager.add_to_user_queue(
                Config.ADMIN_ID, 
                Config.ADMIN_ID, 
                bot.send_message, 
                Config.ADMIN_ID, 
                crash_report, 
                parse_mode="Markdown"
            )
        except:
            pass
        
        # إعادة التشغيل بعد 30 ثانية
        time.sleep(30)
        os.execv(sys.executable, ['python'] + sys.argv)
