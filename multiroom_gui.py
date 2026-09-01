#!/usr/bin/env python3


import os
import sys
import json
import time
import threading
import queue
import hashlib
import uuid
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Set
from collections import deque

# Socket.IO client
try:
    import socketio
    SOCKETIO_AVAILABLE = True
except ImportError:
    print("❌ python-socketio not installed. Run: pip install 'python-socketio[client]'")
    SOCKETIO_AVAILABLE = False
    sys.exit(1)

# Selenium for login
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    print("⚠️ Selenium not installed. Login will require TANDRO_TOKEN env variable.")


class MultiRoomChatLogger:
    def __init__(self, log_dir: str = "chat_logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.buffer_lock = threading.RLock()
        self.message_buffers: Dict[int, List[Dict[str, Any]]] = {}
        self.flush_interval = 10.0
        self.flush_batch_size = 50
        self.last_flush = time.time()
        
    def log_message(self, room_id: int, message: Dict[str, Any]):
        with self.buffer_lock:
            if room_id not in self.message_buffers:
                self.message_buffers[room_id] = []
            self.message_buffers[room_id].append(message)
            if len(self.message_buffers[room_id]) >= self.flush_batch_size:
                self.flush_room(room_id)
    
    def flush_room(self, room_id: int, force: bool = False):
        with self.buffer_lock:
            if room_id not in self.message_buffers:
                return
            if not force and (time.time() - self.last_flush) < self.flush_interval:
                return
            batch = self.message_buffers[room_id][:]
            self.message_buffers[room_id].clear()
            self.last_flush = time.time()
        
        if not batch:
            return
        
        try:
            date_str = datetime.now().strftime("%Y-%m-%d")
            day_dir = self.log_dir / date_str
            day_dir.mkdir(parents=True, exist_ok=True)
            
            time_str = datetime.now().strftime("%H-%M-%S")
            filename = f"room_{room_id}_{time_str}.json"
            filepath = day_dir / filename
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(batch, f, ensure_ascii=False, indent=2)
            
            daily_log = day_dir / f"room_{room_id}_full.log"
            with open(daily_log, 'a', encoding='utf-8') as f:
                for msg in batch:
                    timestamp = msg.get('timestamp', '')
                    user = msg.get('user', 'unknown')
                    text = msg.get('text', '')
                    direction = msg.get('direction', 'in')
                    if direction == 'in':
                        f.write(f"[{timestamp}] {user}: {text}\n")
                    else:
                        f.write(f"[{timestamp}] → {user}: {text}\n")
        except Exception as e:
            print(f"❌ Failed to write chat log: {e}")
    
    def flush_all(self, force: bool = True):
        with self.buffer_lock:
            room_ids = list(self.message_buffers.keys())
        for room_id in room_ids:
            self.flush_room(room_id, force)


class MultiRoomSwitcher:
    def __init__(self, config_file: str = "multi_room_config.json", gui_queue: queue.Queue = None):
        self.config_file = config_file
        self.gui_queue = gui_queue or queue.Queue()
        
        self.state_lock = threading.RLock()
        
        self.room_map: Dict[int, str] = {}
        self.monitored_rooms: Set[int] = set()
        self.room_positions: Dict[int, Tuple[float, float]] = {}
        self.user_cache: Dict[int, str] = {}
        self.user_rooms: Dict[int, int] = {}  # Track which room users are in
        self.seen_ids = set()
        self.seen_order = deque()
        self.seen_limit = 5000
        self.afk_cooldowns = {}
        
        self.config = self.load_config()
        
        self.sio = None
        self.ws_connected = False
        self.ws_token: Optional[str] = None
        self.my_id: Optional[int] = None
        self.my_username: Optional[str] = None
        
        self.current_room_id = 1
        self.current_room_name = None
        
        self.driver = None
        self.running = True
        
        self.autojoin_rooms: Set[int] = set(self.config.get("autojoin_rooms", [1]))
        self.autojoin_enabled = self.config.get("autojoin_enabled", True)
        self.room_switch_interval = self.config.get("room_switch_interval", 5)
        self.auto_switch_enabled = self.config.get("auto_switch_enabled", False)
        
        self.afk_mode = self.config.get("afkMode", False)
        self.afk_message = self.config.get("afkMessage", "I'm currently AFK!")
        self.afk_cooldown = self.config.get("afkCooldown", 60)
        
        log_dir = self.config.get("chat_log_dir", "chat_logs")
        self.chat_logger = MultiRoomChatLogger(log_dir)
        
        self.ws_token = os.getenv("TANDRO_TOKEN") or self.config.get("tandro_token")
        
        default_positions = self.config.get("room_positions", {})
        for room_str, pos in default_positions.items():
            self.room_positions[int(room_str)] = (pos.get("x", 150.0), pos.get("y", 150.0))

    def gui_log(self, text: str, sys: bool = True, direction: str = "sys", room_id: int = None, user: str = "SYSTEM"):
        """Helper to send messages to the GUI queue"""
        self.gui_queue.put({
            "time": datetime.now().strftime("%H:%M:%S"),
            "user": user,
            "text": text,
            "dir": direction,
            "room_id": room_id
        })

    def load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
        else:
            config = {
                "username": "", "password": "", "tandro_token": "",
                "autojoin_rooms": [1], "autojoin_enabled": True,
                "room_switch_interval": 5, "auto_switch_enabled": False,
                "room_positions": {"1": {"x": 150.0, "y": 150.0}},
                "chat_log_dir": "chat_logs", "log_messages": True,
                "afkMode": False, "afkMessage": "Ich bin gerade AFK und antworte später!", "afkCooldown": 60,
                "socket": {"url": "https://tandro.de", "path": "socket.io", "transports": ["websocket"]},
                "discovered_rooms": {},
                "monitored_rooms": []
            }
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        
        discovered = config.get("discovered_rooms", {})
        for rid_str, rname in discovered.items():
            try:
                self.room_map[int(rid_str)] = rname
            except:
                pass
        
        monitored_list = config.get("monitored_rooms", [])
        for rid in monitored_list:
            try:
                self.monitored_rooms.add(int(rid))
            except:
                pass
        
        return config
    
    def save_config(self):
        config = self.config.copy()
        with self.state_lock:
            config["discovered_rooms"] = {str(rid): rname for rid, rname in self.room_map.items()}
            config["monitored_rooms"] = list(self.monitored_rooms)
            config["room_positions"] = {str(rid): {"x": pos[0], "y": pos[1]} for rid, pos in self.room_positions.items()}
            config["auto_switch_enabled"] = self.auto_switch_enabled
            config["room_switch_interval"] = self.room_switch_interval
            config["log_messages"] = self.config.get("log_messages", True)
            config["afkMode"] = self.afk_mode
            config["afkMessage"] = self.afk_message
        
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            self.gui_log(f"Failed to save config: {e}")

    def set_room_position(self, room_id: int, x: float, y: float):
        with self.state_lock:
            self.room_positions[room_id] = (x, y)
        self.save_config()

    def remove_room(self, room_id: int):
        with self.state_lock:
            self.room_map.pop(room_id, None)
            self.monitored_rooms.discard(room_id)
            self.room_positions.pop(room_id, None)
        self.save_config()

    def mark_seen(self, msg_id: str) -> bool:
        if not msg_id:
            return False
        with self.state_lock:
            if msg_id in self.seen_ids:
                return False
            self.seen_ids.add(msg_id)
            self.seen_order.append(msg_id)
            while len(self.seen_order) > self.seen_limit:
                old = self.seen_order.popleft()
                self.seen_ids.discard(old)
            return True
    
    def get_username(self, user_id: int) -> str:
        with self.state_lock:
            # Check for both int and string versions of user_id just in case
            return self.user_cache.get(user_id, self.user_cache.get(str(user_id), "Unknown"))
    
    def cache_users_from_list(self, users: List[Dict]):
        with self.state_lock:
            for user in users:
                if isinstance(user, dict):
                    uid = user.get("id")
                    username = user.get("username")
                    room_id = user.get("currentRoomId") or user.get("room")
                    
                    if uid and username:
                        self.user_cache[uid] = username
                        self.user_cache[str(uid)] = username
                    if uid and room_id is not None:
                        self.user_rooms[uid] = room_id
                        self.user_rooms[str(uid)] = room_id
        self.gui_queue.put({"type": "users_update"})

    def add_room_to_map(self, room_id: int, room_name: str):
        if room_id and room_name and room_id not in self.room_map:
            with self.state_lock:
                self.room_map[room_id] = room_name
            self.save_config()

    def setup_driver(self) -> bool:
        if not SELENIUM_AVAILABLE:
            return False
        self.gui_log("🚀 Setting up Chrome driver...")
        import shutil
        driver_path = os.getenv("CHROMEDRIVER_PATH") or shutil.which("chromedriver")
        if not driver_path:
            self.gui_log("❌ chromedriver not found")
            return False
        
        options = Options()
        options.add_argument("--window-size=1280,720")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        if self.config.get("headless", True):
            options.add_argument("--headless=new")
        
        try:
            service = Service(executable_path=driver_path)
            self.driver = webdriver.Chrome(service=service, options=options)
            self.gui_log("✅ Chrome driver ready!")
            return True
        except Exception as e:
            self.gui_log(f"❌ Driver setup failed: {e}")
            return False
    
    def automated_login(self) -> bool:
        if not self.driver:
            return False
        username = self.config.get("username", "")
        password = self.config.get("password", "")
        if not username or not password:
            self.gui_log("❌ Username or password missing in config.")
            return False
        
        self.gui_log("🔐 Logging in via Selenium...")
        try:
            self.driver.get("https://tandro.de/login")
            time.sleep(2)
            user_field = self.driver.find_element(By.CSS_SELECTOR, 'input[type="text"], input[type="email"]')
            user_field.clear()
            user_field.send_keys(username)
            pass_field = self.driver.find_element(By.CSS_SELECTOR, 'input[type="password"]')
            pass_field.clear()
            pass_field.send_keys(password)
            login_btn = self.driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
            login_btn.click()
            time.sleep(3)
            if "login" not in self.driver.current_url.lower():
                self.gui_log("✅ Login successful!")
                return True
            return False
        except Exception as e:
            self.gui_log(f"❌ Login error: {e}")
            return False
    
    def extract_token_from_browser(self) -> Optional[str]:
        if not self.driver:
            return None
        try:
            js = """
                try {
                    const raw = localStorage.getItem('auth');
                    if (!raw) return null;
                    const obj = JSON.parse(raw);
                    return obj.token || null;
                } catch(e) { return null; }
            """
            token = self.driver.execute_script(js)
            if token:
                self.gui_log("✅ Token extracted from local storage.")
                return token
        except Exception:
            pass
        return None

    def ws_connect(self, token: str) -> bool:
        if not token:
            self.gui_log("❌ No token provided for WebSocket.")
            return False
        
        socket_config = self.config.get("socket", {})
        url = socket_config.get("url", "https://tandro.de")
        path = socket_config.get("path", "socket.io")
        transports = socket_config.get("transports", ["websocket"])
        
        self.gui_log(f"🔌 Connecting to {url}...")
        
        self.sio = socketio.Client(reconnection=True, reconnection_attempts=3, reconnection_delay=1.0)
        self._setup_event_handlers()
        
        try:
            self.sio.connect(url, socketio_path=path, transports=transports, 
                           auth={"token": token}, wait_timeout=10)
            self.ws_connected = True
            self.gui_queue.put({"type": "status_update", "status": True})
            return True
        except Exception as e:
            self.gui_log(f"❌ WebSocket connection failed: {e}")
            return False
    
    def _setup_event_handlers(self):
        @self.sio.event
        def connect():
            self.ws_connected = True
            self.gui_log("📡 Socket.IO connected.")
            self.gui_queue.put({"type": "status_update", "status": True})
        
        @self.sio.event
        def disconnect():
            self.ws_connected = False
            self.gui_log("📡 Socket.IO disconnected.")
            self.gui_queue.put({"type": "status_update", "status": False})
        
        @self.sio.on("existingUsers")
        def on_existing_users(data):
            try:
                myself = data.get("myself", {})
                if myself:
                    self.my_id = myself.get("id")
                    self.my_username = myself.get("username")
                    room_id = myself.get("currentRoomId")
                    room_name = myself.get("currentRoom")
                    
                    if room_id:
                        self.current_room_id = room_id
                        self.current_room_name = room_name
                        self.add_room_to_map(room_id, room_name)
                        self.monitored_rooms.add(room_id)
                        
                        with self.state_lock:
                            self.user_rooms[self.my_id] = room_id
                            self.user_rooms[str(self.my_id)] = room_id
                        
                        self.gui_queue.put({"type": "users_update"})
                        self.gui_log(f"Logged in as: {self.my_username} | Current Room: {room_id}")
                
                users = data.get("users", [])
                if users:
                    self.cache_users_from_list(users)
            except Exception as e:
                pass
        
        @self.sio.on("userListUpdate")
        def on_user_list_update(data):
            if isinstance(data, list):
                self.cache_users_from_list(data)
        
        @self.sio.on("userJoinedUserList")
        def on_user_joined_user_list(data):
            try:
                if isinstance(data, dict):
                    uid = data.get("id")
                    username = data.get("username")
                    room_id = data.get("currentRoomId")
                    with self.state_lock:
                        if uid and username:
                            self.user_cache[uid] = username
                            self.user_cache[str(uid)] = username
                        if uid and room_id is not None:
                            self.user_rooms[uid] = room_id
                            self.user_rooms[str(uid)] = room_id
                    self.gui_queue.put({"type": "users_update"})
            except: pass
            
        @self.sio.on("userLeftUserList")
        def on_user_left_list(data):
            try:
                uid = data.get("id") if isinstance(data, dict) else data
                if uid:
                    with self.state_lock:
                        self.user_rooms.pop(uid, None)
                        self.user_rooms.pop(str(uid), None)
                    self.gui_queue.put({"type": "users_update"})
            except: pass
        
        @self.sio.on("userChangedRoom")
        def on_user_changed_room(data):
            try:
                user_id = data.get("userId")
                room_id = data.get("roomId")
                room_name = data.get("roomName")
                
                if room_id and room_name:
                    self.add_room_to_map(room_id, room_name)
                    
                if user_id and room_id:
                    with self.state_lock:
                        self.user_rooms[user_id] = room_id
                        self.user_rooms[str(user_id)] = room_id
                    self.gui_queue.put({"type": "users_update"})
                
                if user_id == self.my_id and room_id:
                    self.current_room_id = room_id
                    self.current_room_name = room_name
                    self.monitored_rooms.add(room_id)
                    self.gui_log(f"🔄 Switched to room {room_id}: {room_name}")
            except: pass

        @self.sio.on("updateChatLines")
        def on_update_chat_lines(data):
            self._handle_chat_event(data)

        @self.sio.on("userMessage")
        def on_user_message(data):
            self._handle_chat_event(data, is_speech_bubble=True)

    def _handle_chat_event(self, data, is_speech_bubble=False):
        try:
            if not isinstance(data, dict):
                return
            
            msg_id = str(data.get("id") or "")
            room_id = data.get("room") or data.get("roomId")
            user_id = data.get("userId")
            
            if is_speech_bubble:
                user = self.get_username(user_id) if user_id else ""
                text = (data.get("message") or data.get("speechBubbleText") or "").strip()
            else:
                user = (data.get("user") or data.get("username") or "").strip()
                if not user and user_id:
                    user = self.get_username(user_id)
                text = (data.get("chatLine") or data.get("message") or data.get("text") or "").strip()
            
            timestamp = data.get("timestamp") or datetime.now().isoformat()
            is_server = data.get("isServerMessage", False)
            is_join = data.get("isJoin", False)
            is_leave = data.get("isLeave", False)
            
            if room_id:
                self.monitored_rooms.add(room_id)
                room_name = data.get("roomName")
                if room_name:
                    self.add_room_to_map(room_id, room_name)
            
            if not msg_id:
                raw = f"{room_id}|{timestamp}|{user}|{text}"
                msg_id = "msg-" + hashlib.sha1(raw.encode()).hexdigest()[:16]
            
            if not self.mark_seen(msg_id):
                return
            
            # Disk logging
            if self.config.get("log_messages", True):
                self.chat_logger.log_message(room_id, {
                    "timestamp": timestamp, "direction": "in",
                    "room_id": room_id, "room_name": self.room_map.get(room_id, f"Room {room_id}"),
                    "user": user or f"User{user_id}", "text": text, "message_id": msg_id
                })

            # Formatting for GUI
            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                time_str = dt.strftime("%H:%M:%S")
            except:
                time_str = datetime.now().strftime("%H:%M:%S")

            direction = "sys" if (is_server or is_join or is_leave) else "in"
            if user_id == self.my_id or user == self.my_username:
                direction = "out"
            
            display_user = user or f"User{user_id}"
            
            if is_server:
                msg_text = text
            elif is_join:
                msg_text = f"→ {display_user} joined"
            elif is_leave:
                msg_text = f"← {display_user} left"
            else:
                msg_text = text
            
            self.gui_queue.put({
                "time": time_str,
                "user": display_user if not (is_server or is_join or is_leave) else "SERVER",
                "text": msg_text,
                "dir": direction,
                "room_id": room_id
            })

            # AFK Logic
            if self.afk_mode and self.afk_message and direction == "in":
                self._check_afk_trigger(user_id, text, data)

        except Exception as e:
            pass

    def _check_afk_trigger(self, sender_id, text, msg_data):
        if not self.my_id or not sender_id:
            return
        
        is_mentioned = False
        mentions = msg_data.get("mentions", [])
        if self.my_id in [str(m) for m in mentions] or self.my_id in mentions:
            is_mentioned = True
        
        reply_to = msg_data.get("replyTo")
        if reply_to and str(reply_to) == str(self.my_id):
            is_mentioned = True
            
        my_name = (self.my_username or "").lower()
        if my_name and my_name in text.lower():
            is_mentioned = True
            
        if is_mentioned:
            now = time.time()
            last_replied = self.afk_cooldowns.get(sender_id, 0)
            if (now - last_replied) > self.afk_cooldown:
                self.afk_cooldowns[sender_id] = now
                afk_text = f"[AFK] {self.afk_message}"
                # Send back to same room
                room_id = msg_data.get("room") or self.current_room_id
                self.send_message_to_room(room_id, afk_text)

    def ws_disconnect(self):
        if self.sio:
            try:
                self.sio.disconnect()
            except: pass
            self.sio = None
        self.ws_connected = False
        self.gui_queue.put({"type": "status_update", "status": False})
    
    def join_room(self, room_id: int, x: float = None, y: float = None) -> bool:
        if not self.ws_connected or not self.sio:
            return False
        
        if x is None or y is None:
            x, y = self.room_positions.get(room_id, (150.0, 150.0))
        
        room_name = self.room_map.get(room_id, "")
        
        try:
            self.sio.emit("joinRoom", {
                "room": room_id,
                "roomName": room_name,
                "user": {"position": {"x": float(x), "y": float(y)}}
            })
            self.current_room_id = room_id
            self.current_room_name = room_name
            self.monitored_rooms.add(room_id)
            self.room_positions[room_id] = (float(x), float(y))
            self.sio.emit("getUserList", room_id)
            self.save_config()
            return True
        except Exception as e:
            return False
    
    def move_user(self, room_id: int, x: float, y: float) -> bool:
        """Move avatar within a room by re-emitting joinRoom with new coords."""
        return self.join_room(room_id, x, y)

    def switch_room(self, new_room_id: int) -> bool:
        return self.join_room(new_room_id)
    
    def add_monitored_room(self, room_id: int):
        if room_id not in self.monitored_rooms:
            previous = self.current_room_id
            self.switch_room(room_id)
            time.sleep(1)
            self.monitored_rooms.add(room_id)
            self.save_config()
            if previous != room_id:
                time.sleep(0.5)
                self.switch_room(previous)
    
    def auto_switch_rooms_loop(self):
        index = 0
        while self.running:
            if self.ws_connected and self.auto_switch_enabled:
                try:
                    with self.state_lock:
                        room_list = list(self.monitored_rooms)
                    if not room_list:
                        time.sleep(self.room_switch_interval)
                        continue
                    
                    next_room = room_list[index % len(room_list)]
                    if next_room != self.current_room_id:
                        self.switch_room(next_room)
                    index += 1
                except:
                    pass
            time.sleep(self.room_switch_interval)

    def send_message(self, text: str) -> bool:
        if not self.ws_connected or not self.sio or not self.my_id:
            return False
        
        try:
            self.sio.emit("newMessage", {
                "id": str(uuid.uuid4()),
                "room": self.current_room_id,
                "userId": self.my_id,
                "message": text,
                "speechBubbleText": text,
                "mentions": [],
                "replyTo": None,
                "isEmoji": False,
                "isGif": False,
                "emojiUrl": ""
            })
            return True
        except Exception:
            return False
    
    def send_message_to_room(self, room_id: int, text: str) -> bool:
        if room_id not in self.monitored_rooms:
            self.add_monitored_room(room_id)
        
        previous = self.current_room_id
        if previous != room_id:
            self.switch_room(room_id)
            time.sleep(0.5)
        
        success = self.send_message(text)
        
        if previous != room_id and success:
            time.sleep(0.5)
            self.switch_room(previous)
        
        return success

    def start_background(self):
        def auto_flush():
            while self.running:
                time.sleep(5)
                self.chat_logger.flush_all(force=False)
        
        threading.Thread(target=auto_flush, daemon=True).start()
        threading.Thread(target=self.auto_switch_rooms_loop, daemon=True).start()

        def connection_routine():
            token = self.ws_token
            if not token:
                if not SELENIUM_AVAILABLE:
                    self.gui_log("❌ No token and Selenium missing.", direction="sys")
                    return
                if not self.setup_driver():
                    return
                if not self.automated_login():
                    return
                token = self.extract_token_from_browser()
                if not token:
                    return
                self.ws_token = token
                self.config["tandro_token"] = token
                self.save_config()
                if self.driver:
                    try:
                        self.driver.quit()
                    except: pass
            
            if not self.ws_connect(token):
                return
            
            time.sleep(2)
            if self.autojoin_enabled and self.autojoin_rooms:
                for room_id in self.autojoin_rooms:
                    if room_id not in self.monitored_rooms:
                        self.add_monitored_room(room_id)
                        time.sleep(1)
            
            self.gui_log("✨ Setup complete! Joined initial rooms.", direction="sys")

        threading.Thread(target=connection_routine, daemon=True).start()

    def shutdown(self):
        self.running = False
        self.chat_logger.flush_all(force=True)
        self.ws_disconnect()
        if self.driver:
            try:
                self.driver.quit()
            except: pass

class MultiRoomGUI:
    def __init__(self, root, switcher: MultiRoomSwitcher):
        self.root = root
        self.switcher = switcher
        self.root.title("Tandro Multi-Room Monitor")
        self.root.geometry("1400x760")
        self.root.configure(bg="#202225")
        
        self.setup_styles()
        self.create_layout()
        self.load_gui_state()
        
        self.root.after(100, self.update_gui_loop)

    def setup_styles(self):
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
            
        style.configure("TFrame", background="#202225")
        style.configure("TLabel", background="#202225", foreground="#dcddde", font=("Segoe UI", 10))
        style.configure("Header.TLabel", font=("Segoe UI", 13, "bold"), foreground="#ffffff")
        style.configure("TCheckbutton", background="#202225", foreground="#dcddde", font=("Segoe UI", 10), focuscolor="#202225")
        style.map("TCheckbutton", background=[('active', '#2f3136')])
        style.configure("TButton", font=("Segoe UI", 9, "bold"), background="#5865f2", foreground="white", borderwidth=0, padding=4)
        style.map("TButton", background=[('active', '#4752c4')])
        style.configure("Danger.TButton", background="#ed4245")
        style.map("Danger.TButton", background=[('active', '#c9383b')])
        style.configure("Secondary.TButton", background="#4f545c")
        style.map("Secondary.TButton", background=[('active', '#5d6269')])

        # Treeview styling for room & user lists
        style.configure("Treeview", background="#2f3136", foreground="#dcddde", fieldbackground="#2f3136", rowheight=24, borderwidth=0)
        style.map("Treeview", background=[('selected', '#5865f2')], foreground=[('selected', '#ffffff')])
        style.configure("Treeview.Heading", background="#202225", foreground="#ffffff", font=("Segoe UI", 9, "bold"))
        style.map("Treeview.Heading", background=[('active', '#36393f')])

    def create_layout(self):
        # --- 3-Column Layout: Left (Settings/Rooms), Center (Chat), Right (Users) ---
        self.left_frame = ttk.Frame(self.root, width=340)
        self.left_frame.pack(side="left", fill="y", padx=15, pady=15)
        
        self.right_sidebar = ttk.Frame(self.root, width=250)
        self.right_sidebar.pack(side="right", fill="y", padx=(0, 15), pady=15)
        
        self.center_frame = ttk.Frame(self.root)
        self.center_frame.pack(side="left", fill="both", expand=True, padx=(0, 15), pady=15)

        # --- Left Panel (Settings & Rooms) ---
        ttk.Label(self.left_frame, text="⚙️ Status", style="Header.TLabel").pack(anchor="w", pady=(0, 5))
        
        self.status_var = tk.StringVar(value="🔴 Offline (Connecting...)")
        self.status_label = ttk.Label(self.left_frame, textvariable=self.status_var, foreground="#ed4245", font=("Segoe UI", 10, "bold"))
        self.status_label.pack(anchor="w", pady=(0, 10))

        ttk.Label(self.left_frame, text="🛠️ System Options", style="Header.TLabel").pack(anchor="w", pady=(5, 5))
        
        self.logger_var = tk.BooleanVar()
        self.create_toggle("📡 Log Chat to Disk", self.logger_var, self.on_logger_toggle)
        
        self.auto_switch_var = tk.BooleanVar()
        self.create_toggle("🔄 Auto-Switch Rooms", self.auto_switch_var, self.on_autoswitch_toggle)
        
        switch_frame = tk.Frame(self.left_frame, bg="#202225")
        switch_frame.pack(anchor="w", pady=2, padx=25)
        tk.Label(switch_frame, text="Interval (Sec):", bg="#202225", fg="#b0b0b0", font=("Segoe UI", 9)).pack(side="left")
        self.interval_var = tk.StringVar()
        self.interval_var.trace_add("write", self.on_interval_change)
        tk.Entry(switch_frame, textvariable=self.interval_var, width=5, bg="#36393f", fg="white", insertbackground="white", borderwidth=1, relief="flat").pack(side="left", padx=5)

        self.afk_var = tk.BooleanVar()
        self.create_toggle("🤖 Enable AFK Mode", self.afk_var, self.on_afk_toggle)
        
        # Room & Avatar Controls
        ttk.Label(self.left_frame, text="📍 Room & Position", style="Header.TLabel").pack(anchor="w", pady=(15, 5))
        
        pos_frame = tk.Frame(self.left_frame, bg="#202225")
        pos_frame.pack(anchor="w", fill="x", pady=2)
        
        tk.Label(pos_frame, text="Room ID:", bg="#202225", fg="#dcddde", font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", pady=2)
        self.room_entry = tk.Entry(pos_frame, width=6, bg="#36393f", fg="white", insertbackground="white", relief="flat")
        self.room_entry.grid(row=0, column=1, padx=4, pady=2)
        
        tk.Label(pos_frame, text="X:", bg="#202225", fg="#dcddde", font=("Segoe UI", 9)).grid(row=0, column=2, sticky="e", pady=2)
        self.pos_x_entry = tk.Entry(pos_frame, width=5, bg="#36393f", fg="white", insertbackground="white", relief="flat")
        self.pos_x_entry.insert(0, "150")
        self.pos_x_entry.grid(row=0, column=3, padx=2, pady=2)

        tk.Label(pos_frame, text="Y:", bg="#202225", fg="#dcddde", font=("Segoe UI", 9)).grid(row=0, column=4, sticky="e", pady=2)
        self.pos_y_entry = tk.Entry(pos_frame, width=5, bg="#36393f", fg="white", insertbackground="white", relief="flat")
        self.pos_y_entry.insert(0, "150")
        self.pos_y_entry.grid(row=0, column=5, padx=2, pady=2)

        btn_frame = tk.Frame(self.left_frame, bg="#202225")
        btn_frame.pack(anchor="w", fill="x", pady=4)
        
        # Split Join and Move functionality per request
        ttk.Button(btn_frame, text="Join", command=self.join_room_ui).pack(side="left", fill="x", expand=True, padx=(0, 2))
        ttk.Button(btn_frame, text="Move", command=self.move_room_ui).pack(side="left", fill="x", expand=True, padx=(0, 2))
        ttk.Button(btn_frame, text="Monitor", command=self.monitor_room_ui).pack(side="left", fill="x", expand=True, padx=(0, 0))

        # Room Status Treeview
        ttk.Label(self.left_frame, text="🗺️ Discovered Rooms (Double-click to Join)", style="Header.TLabel").pack(anchor="w", pady=(15, 5))
        
        tree_container = tk.Frame(self.left_frame, bg="#2f3136")
        tree_container.pack(fill="both", expand=True, pady=(0, 5))
        
        columns = ("id", "name", "status", "pos")
        self.room_tree = ttk.Treeview(tree_container, columns=columns, show="headings", height=7, selectmode="browse")
        self.room_tree.heading("id", text="ID")
        self.room_tree.heading("name", text="Name")
        self.room_tree.heading("status", text="Status")
        self.room_tree.heading("pos", text="Pos (X,Y)")

        self.room_tree.column("id", width=35, anchor="center")
        self.room_tree.column("name", width=105, anchor="w")
        self.room_tree.column("status", width=95, anchor="center")
        self.room_tree.column("pos", width=75, anchor="center")
        
        self.room_tree.pack(side="left", fill="both", expand=True)
        tree_scroll = ttk.Scrollbar(tree_container, orient="vertical", command=self.room_tree.yview)
        tree_scroll.pack(side="right", fill="y")
        self.room_tree.configure(yscrollcommand=tree_scroll.set)
        
        # Bindings for selection and double-click
        self.room_tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.room_tree.bind("<Double-1>", self.on_tree_double_click)

        tree_btn_frame = tk.Frame(self.left_frame, bg="#202225")
        tree_btn_frame.pack(fill="x", pady=(0, 10))
        ttk.Button(tree_btn_frame, text="Switch", command=self.switch_to_selected_tree).pack(side="left", fill="x", expand=True, padx=(0, 2))
        ttk.Button(tree_btn_frame, text="Toggle Monitor", command=self.toggle_monitor_selected).pack(side="left", fill="x", expand=True, padx=2)
        ttk.Button(tree_btn_frame, text="Save Pos", command=self.save_pos_selected).pack(side="right", fill="x", expand=True, padx=(2, 0))

        ttk.Label(self.left_frame, text="💾 Config", style="Header.TLabel").pack(anchor="w", pady=(5, 5))
        backup_frame = ttk.Frame(self.left_frame)
        backup_frame.pack(fill="x")
        ttk.Button(backup_frame, text="📤 Export", command=self.export_config).pack(side="left", fill="x", expand=True, padx=(0, 2))
        ttk.Button(backup_frame, text="📥 Import", command=self.import_config).pack(side="right", fill="x", expand=True, padx=(2, 0))

        # --- Right Sidebar (Online Users) ---
        ttk.Label(self.right_sidebar, text="👥 Online Users", style="Header.TLabel").pack(anchor="w", pady=(0, 5))
        
        users_container = tk.Frame(self.right_sidebar, bg="#2f3136")
        users_container.pack(fill="both", expand=True, pady=(0, 10))
        
        user_cols = ("name", "room")
        self.users_tree = ttk.Treeview(users_container, columns=user_cols, show="headings", selectmode="none")
        self.users_tree.heading("name", text="User")
        self.users_tree.heading("room", text="Room")
        
        self.users_tree.column("name", width=130, anchor="w")
        self.users_tree.column("room", width=100, anchor="w")
        
        self.users_tree.pack(side="left", fill="both", expand=True)
        users_scroll = ttk.Scrollbar(users_container, orient="vertical", command=self.users_tree.yview)
        users_scroll.pack(side="right", fill="y")
        self.users_tree.configure(yscrollcommand=users_scroll.set)

        # --- Center Panel (Chat) ---
        ttk.Label(self.center_frame, text="💬 Live Chat Monitor", style="Header.TLabel").pack(anchor="w", pady=(0, 5))
        
        chat_container = tk.Frame(self.center_frame, bg="#36393f", highlightthickness=1, highlightbackground="#202225")
        chat_container.pack(fill="both", expand=True, pady=(0, 10))
        
        self.chat_text = tk.Text(chat_container, bg="#36393f", fg="#dcddde", font=("Segoe UI", 10), wrap="word", borderwidth=0)
        self.chat_text.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        
        scrollbar = ttk.Scrollbar(chat_container, command=self.chat_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.chat_text.configure(yscrollcommand=scrollbar.set)
        
        self.chat_text.tag_config("time", foreground="#72767d")
        self.chat_text.tag_config("room", foreground="#eb459e", font=("Segoe UI", 10, "bold"))
        self.chat_text.tag_config("user_in", foreground="#5865f2", font=("Segoe UI", 10, "bold"))
        self.chat_text.tag_config("user_out", foreground="#3ba55c", font=("Segoe UI", 10, "bold"))
        self.chat_text.tag_config("sys", foreground="#faa61a", font=("Segoe UI", 10, "italic"))
        self.chat_text.tag_config("text", foreground="#dcddde")
        self.chat_text.config(state="disabled")
        
        # Bottom Input area
        input_frame = tk.Frame(self.center_frame, bg="#202225")
        input_frame.pack(fill="x", side="bottom")
        
        self.msg_entry = tk.Entry(input_frame, bg="#40444b", fg="white", font=("Segoe UI", 11), insertbackground="white", relief="flat")
        self.msg_entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 10))
        self.msg_entry.bind("<Return>", lambda e: self.send_message_ui())
        
        ttk.Button(input_frame, text="Send to Current", command=self.send_message_ui).pack(side="right", ipady=2)

    def create_toggle(self, text, variable, command):
        cb = ttk.Checkbutton(self.left_frame, text=text, variable=variable, command=command)
        cb.pack(anchor="w", pady=3)

    def load_gui_state(self):
        self.logger_var.set(self.switcher.config.get("log_messages", True))
        self.auto_switch_var.set(self.switcher.auto_switch_enabled)
        self.interval_var.set(str(self.switcher.room_switch_interval))
        self.afk_var.set(self.switcher.afk_mode)
        self.refresh_room_table()
        self.refresh_users_table()

    def on_logger_toggle(self):
        self.switcher.config["log_messages"] = self.logger_var.get()
        self.switcher.save_config()

    def on_autoswitch_toggle(self):
        self.switcher.auto_switch_enabled = self.auto_switch_var.get()
        self.switcher.save_config()
        
    def on_afk_toggle(self):
        self.switcher.afk_mode = self.afk_var.get()
        self.switcher.save_config()

    def on_interval_change(self, *args):
        val = self.interval_var.get()
        if val.isdigit() and int(val) > 0:
            self.switcher.room_switch_interval = int(val)
            self.switcher.save_config()

    def join_room_ui(self):
        try:
            rid = int(self.room_entry.get())
            x = float(self.pos_x_entry.get() or 150.0)
            y = float(self.pos_y_entry.get() or 150.0)
            self.switcher.join_room(rid, x, y)
            self.refresh_room_table()
        except ValueError:
            messagebox.showerror("Error", "Please enter valid numeric Room ID and Coordinates.")

    def move_room_ui(self):
        """Moves the user in the currently specified room without altering monitoring heavily."""
        try:
            rid = int(self.room_entry.get())
            x = float(self.pos_x_entry.get() or 150.0)
            y = float(self.pos_y_entry.get() or 150.0)
            self.switcher.move_user(rid, x, y)
            self.refresh_room_table()
        except ValueError:
            messagebox.showerror("Error", "Please enter valid numeric Room ID and Coordinates.")

    def monitor_room_ui(self):
        try:
            rid = int(self.room_entry.get())
            self.switcher.add_monitored_room(rid)
            self.refresh_room_table()
            messagebox.showinfo("Success", f"Added Room {rid} to monitored list.")
        except ValueError:
            messagebox.showerror("Error", "Please enter a valid numeric Room ID.")

    def on_tree_select(self, event):
        selected = self.room_tree.selection()
        if selected:
            item = self.room_tree.item(selected[0])
            rid = item["values"][0]
            self.room_entry.delete(0, tk.END)
            self.room_entry.insert(0, str(rid))
            pos = self.switcher.room_positions.get(int(rid), (150.0, 150.0))
            self.pos_x_entry.delete(0, tk.END)
            self.pos_x_entry.insert(0, str(int(pos[0])))
            self.pos_y_entry.delete(0, tk.END)
            self.pos_y_entry.insert(0, str(int(pos[1])))

    def on_tree_double_click(self, event):
        """Double clicking a room automatically joins it."""
        selected = self.room_tree.selection()
        if selected:
            item = self.room_tree.item(selected[0])
            rid = int(item["values"][0])
            pos = self.switcher.room_positions.get(rid, (150.0, 150.0))
            self.switcher.join_room(rid, pos[0], pos[1])
            self.refresh_room_table()

    def switch_to_selected_tree(self):
        selected = self.room_tree.selection()
        if not selected:
            return
        rid = int(self.room_tree.item(selected[0])["values"][0])
        x = float(self.pos_x_entry.get() or 150.0)
        y = float(self.pos_y_entry.get() or 150.0)
        self.switcher.join_room(rid, x, y)
        self.refresh_room_table()

    def toggle_monitor_selected(self):
        selected = self.room_tree.selection()
        if not selected:
            return
        rid = int(self.room_tree.item(selected[0])["values"][0])
        if rid in self.switcher.monitored_rooms:
            self.switcher.monitored_rooms.discard(rid)
        else:
            self.switcher.monitored_rooms.add(rid)
        self.switcher.save_config()
        self.refresh_room_table()

    def save_pos_selected(self):
        try:
            rid = int(self.room_entry.get())
            x = float(self.pos_x_entry.get())
            y = float(self.pos_y_entry.get())
            self.switcher.set_room_position(rid, x, y)
            self.refresh_room_table()
            messagebox.showinfo("Saved", f"Position ({x}, {y}) saved for Room {rid}.")
        except ValueError:
            messagebox.showerror("Error", "Please enter valid Room ID, X and Y values.")

    def refresh_room_table(self):
        # Keep selected ID if possible
        selected = self.room_tree.selection()
        prev_id = None
        if selected:
            prev_id = self.room_tree.item(selected[0])["values"][0]

        for item in self.room_tree.get_children():
            self.room_tree.delete(item)
            
        all_rooms = set(self.switcher.room_map.keys()) | set(self.switcher.monitored_rooms) | {self.switcher.current_room_id}
        for rid in sorted(all_rooms):
            name = self.switcher.room_map.get(rid, f"Room {rid}")
            if rid == self.switcher.current_room_id:
                status = "📍 Active"
            elif rid in self.switcher.monitored_rooms:
                status = "👁️ Monitored"
            else:
                status = "Known"
            
            pos = self.switcher.room_positions.get(rid, (150.0, 150.0))
            pos_str = f"{int(pos[0])},{int(pos[1])}"
            
            item_id = self.room_tree.insert("", "end", values=(rid, name, status, pos_str))
            if prev_id is not None and str(rid) == str(prev_id):
                self.room_tree.selection_set(item_id)
                
    def refresh_users_table(self):
        for item in self.users_tree.get_children():
            self.users_tree.delete(item)
            
        with self.switcher.state_lock:
            handled = set()
            user_list = []
            
            for uid_raw, room_id in self.switcher.user_rooms.items():
                try:
                    uid = int(uid_raw)
                except:
                    continue
                    
                if uid in handled: continue
                handled.add(uid)
                
                username = self.switcher.user_cache.get(uid, f"User {uid}")
                room_name = self.switcher.room_map.get(room_id, f"Room {room_id}")
                user_list.append((username, room_name))
            
            # Sort users alphabetically
            for username, room_name in sorted(user_list, key=lambda x: x[0].lower()):
                self.users_tree.insert("", "end", values=(username, room_name))

    def send_message_ui(self):
        text = self.msg_entry.get().strip()
        if text:
            if self.switcher.send_message(text):
                self.msg_entry.delete(0, tk.END)

    def export_config(self):
        file_path = filedialog.asksaveasfilename(defaultextension=".json", initialfile="multiroom_config_backup.json", filetypes=[("JSON", "*.json")])
        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(self.switcher.config, f, indent=4)
                messagebox.showinfo("Success", "Config exported successfully!")
            except Exception as e:
                messagebox.showerror("Error", str(e))

    def import_config(self):
        file_path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    imported = json.load(f)
                self.switcher.config.update(imported)
                self.switcher.save_config()
                
                self.switcher.auto_switch_enabled = self.switcher.config.get("auto_switch_enabled", False)
                self.switcher.room_switch_interval = self.switcher.config.get("room_switch_interval", 5)
                self.switcher.afk_mode = self.switcher.config.get("afkMode", False)
                
                self.load_gui_state()
                messagebox.showinfo("Success", "Config imported! Applied changes.")
            except Exception as e:
                messagebox.showerror("Error", str(e))

    def update_gui_loop(self):
        while not self.switcher.gui_queue.empty():
            msg = self.switcher.gui_queue.get_nowait()
            
            if msg.get("type") == "status_update":
                if msg.get("status"):
                    self.status_var.set(f"🟢 Connected (ID: {self.switcher.my_id})")
                    self.status_label.configure(foreground="#3ba55c")
                else:
                    self.status_var.set("🔴 Offline")
                    self.status_label.configure(foreground="#ed4245")
                self.refresh_room_table()
                continue
                
            if msg.get("type") == "users_update":
                self.refresh_users_table()
                continue
                
            if "text" in msg:
                self.chat_text.config(state="normal")
                
                time_str = f"[{msg['time']}] "
                self.chat_text.insert("end", time_str, "time")
                
                if msg.get("room_id"):
                    room_str = f"[Rm {msg['room_id']}] "
                    self.chat_text.insert("end", room_str, "room")
                
                if msg['dir'] == 'sys':
                    self.chat_text.insert("end", f"{msg['user']}: {msg['text']}\n", "sys")
                else:
                    user_tag = "user_out" if msg['dir'] == 'out' else "user_in"
                    self.chat_text.insert("end", f"{msg['user']}: ", user_tag)
                    self.chat_text.insert("end", f"{msg['text']}\n", "text")
                
                self.chat_text.see("end")
                self.chat_text.config(state="disabled")
                self.refresh_room_table()

        self.root.after(100, self.update_gui_loop)

def main():
    root = tk.Tk()
    
    switcher = MultiRoomSwitcher()
    gui = MultiRoomGUI(root, switcher)
    
    def on_closing():
        switcher.shutdown()
        root.destroy()
        os._exit(0)
        
    root.protocol("WM_DELETE_WINDOW", on_closing)
    
    switcher.start_background()
    
    root.mainloop()

if __name__ == "__main__":
    main()
