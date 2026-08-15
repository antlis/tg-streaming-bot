import os
import json

from config import MAX_QUEUE_SIZE

QUEUE = {}
RESUME = {}    # last track + playback position per chat (for /continue)
LOOP = {}      # chat_id -> True when the current track should repeat on end
AUTOPLAY = {}  # chat_id -> True when auto-DJ should queue a related track at the end


def set_loop(chat_id, on):
    if on:
        LOOP[chat_id] = True
    else:
        LOOP.pop(chat_id, None)


def is_loop(chat_id):
    return LOOP.get(chat_id, False)


def set_autoplay(chat_id, on):
    if on:
        AUTOPLAY[chat_id] = True
    else:
        AUTOPLAY.pop(chat_id, None)


def is_autoplay(chat_id):
    return AUTOPLAY.get(chat_id, False)

# RESUME is persisted to the downloads volume so /continue still works after a
# bot restart/crash. (QUEUE isn't persisted — the voice-chat connection is gone
# after a restart, so a restored playlist would be meaningless.)
_RESUME_FILE = os.path.join("downloads", "resume.json")


def save_resume():
    try:
        os.makedirs("downloads", exist_ok=True)
        tmp = _RESUME_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(RESUME, f)
        os.replace(tmp, _RESUME_FILE)
    except Exception:
        pass


def load_resume():
    try:
        with open(_RESUME_FILE) as f:
            data = json.load(f)
        RESUME.clear()
        RESUME.update({int(k): v for k, v in data.items()})
    except Exception:
        pass

# TOPIC_LOCK restricts a group to a single forum topic (see /topic in
# program/topic.py) — every other topic there, including "General", is then
# ignored. Persisted the same way as RESUME so it survives restarts.
TOPIC_LOCK = {}  # chat_id -> locked message_thread_id
_TOPIC_LOCK_FILE = os.path.join("downloads", "topic_locks.json")


def save_topic_lock():
    try:
        os.makedirs("downloads", exist_ok=True)
        tmp = _TOPIC_LOCK_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(TOPIC_LOCK, f)
        os.replace(tmp, _TOPIC_LOCK_FILE)
    except Exception:
        pass


def load_topic_lock():
    try:
        with open(_TOPIC_LOCK_FILE) as f:
            data = json.load(f)
        TOPIC_LOCK.clear()
        TOPIC_LOCK.update({int(k): int(v) for k, v in data.items()})
    except Exception:
        pass


def set_topic_lock(chat_id, topic_id):
    TOPIC_LOCK[chat_id] = topic_id
    save_topic_lock()


def clear_topic_lock(chat_id):
    if TOPIC_LOCK.pop(chat_id, None) is not None:
        save_topic_lock()

def add_to_queue(chat_id, songname, link, ref, type, quality):
   if chat_id in QUEUE:
      chat_queue = QUEUE[chat_id]
      # QUEUE[chat_id][0] is the now-playing item; the rest are upcoming. Cap the
      # upcoming count at MAX_QUEUE_SIZE; return -1 so the caller can say so.
      if MAX_QUEUE_SIZE and len(chat_queue) > MAX_QUEUE_SIZE:
         return -1
      chat_queue.append([songname, link, ref, type, quality])
      return int(len(chat_queue)-1)
   else:
      QUEUE[chat_id] = [[songname, link, ref, type, quality]]

def get_queue(chat_id):
   if chat_id in QUEUE:
      chat_queue = QUEUE[chat_id]
      return chat_queue
   else:
      return 0

def pop_an_item(chat_id):
   if chat_id in QUEUE:
      chat_queue = QUEUE[chat_id]
      chat_queue.pop(0)
      return 1
   else:
      return 0
      
def clear_queue(chat_id):
   LOOP.pop(chat_id, None)
   if chat_id in QUEUE:
      QUEUE.pop(chat_id)
      return 1
   else:
      return 0
