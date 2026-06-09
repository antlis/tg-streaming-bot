import os
import json

from config import MAX_QUEUE_SIZE

QUEUE = {}
RESUME = {}  # last track + playback position per chat (for /continue)
LOOP = {}    # chat_id -> True when the current track should repeat on end


def set_loop(chat_id, on):
    if on:
        LOOP[chat_id] = True
    else:
        LOOP.pop(chat_id, None)


def is_loop(chat_id):
    return LOOP.get(chat_id, False)

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
