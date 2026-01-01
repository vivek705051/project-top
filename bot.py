import requests
import time
import json
import os
import math
import re

TOKEN = "8228183877:AAGdJHc53yDreIldh-EFX-nOMnCgHdc9yvA"
BASE_URL = f"https://api.telegram.org/bot{TOKEN}"
BOT_ID = None
BOT_USERNAME = None

DEFAULT_RETRY = 3


def _request_with_backoff(method, url, max_retries=DEFAULT_RETRY, backoff_factor=1.5, **kwargs):
    """Send HTTP request with special handling for Telegram 429 responses.

    On 429, reads `retry_after` from JSON `parameters` when available and sleeps
    that many seconds before retrying. Uses exponential backoff otherwise.
    """
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.request(method, url, **kwargs)
        except Exception as e:
            sleep_time = backoff_factor ** attempt
            print(f"Request exception: {e}. Sleeping {sleep_time}s before retry {attempt}/{max_retries}")
            time.sleep(sleep_time)
            continue

        # Try to parse JSON safely
        try:
            j = resp.json()
        except Exception:
            j = None

        # Check for explicit Telegram 429 in status code or JSON error_code
        is_429 = resp.status_code == 429 or (isinstance(j, dict) and j.get('error_code') == 429)
        if is_429:
            retry_after = None
            if isinstance(j, dict):
                retry_after = j.get('parameters', {}).get('retry_after')
            if retry_after is None:
                # exponential backoff fallback
                retry_after = int(math.ceil(backoff_factor ** attempt))
            print(f"Telegram 429 received. Sleeping {retry_after}s before retry {attempt}/{max_retries}")
            time.sleep(int(retry_after))
            continue

        # Non-429 response — return it
        return resp

    # Exhausted retries — return last response object if available, else raise
    return resp
episode_counter = 1
episode_counters = {}
last_update_id = 0
user_quality = {}
user_waiting_quality = set()
user_waiting_episode = set()
user_videos = {}
bot_messages = {}
all_messages = {}
video_messages = {}
user_photos = {}  # Track user's photos to keep them
started_users = set()

STATE_FILE = "bot_state.json"


def save_state():
    try:
        state = {
            'episode_counters': {str(k): v for k, v in episode_counters.items()},
            'user_quality': {str(k): v for k, v in user_quality.items()},
            'started_users': list(started_users)
        }
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f)
        print("State saved")
    except Exception as e:
        print(f"Failed to save state: {e}")


def load_state():
    global episode_counter, user_quality, started_users
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
        ecs = state.get('episode_counters', {})
        for k, v in ecs.items():
            episode_counters[int(k)] = int(v)
        uq = state.get('user_quality', {})
        user_quality = {int(k): v for k, v in uq.items()}
        started_users = set(state.get('started_users', []))
        print("State loaded")
    except Exception as e:
        print(f"Failed to load state: {e}")


def get_start_text(chat_id=None):
    ep = episode_counters.get(chat_id, episode_counter)
    return f"""🎬 Welcome to Auto Caption Bot!

📺 I automatically add captions to your anime videos with:
• Episode numbers (Current: {ep})
• Hindi Dub labels
• Quality indicators
• Channel promotion

🚀 How to use:
1. Use /autocaption to set quality (480p/720p/1080p/2160p)
2. Send me video(s) - I'll caption them automatically!
3. Higher quality = more videos needed

⚙️ Commands:
/autocaption - Choose quality settings
/refresh - Reset episode counter
/del - Delete bot text messages
/all_del - Delete ALL messages (including videos)
/help - Show detailed help
/stop - Deactivate bot in this chat/channel

Send your first video to begin! 🎯"""


def get_help_text(chat_id=None):
    ep = episode_counters.get(chat_id, episode_counter)
    return f"""🤖 Anime Caption Bot Help

📹 Send me a video and I'll add episode captions automatically!

✨ Features:
• Auto episode numbering
• Hindi dub labels
• Quality tags
• Channel promotion

📺 Current episode: {ep}

Commands:
/autocaption - Set quality options
/refresh - Reset episode counter
/del - Delete bot text messages
/all_del - Delete ALL messages
/stop - Deactivate bot in this chat/channel

Just send a video to get started!"""


def handle_text(chat_id, text):
    global episode_counter
    # Auto-enable for groups/channels
    if chat_id < 0:
        started_users.add(chat_id)

    print(f"Text message: {text} (chat_id: {chat_id})")

    # If we're waiting for episode number and the user sends a non-command, accept it
    if chat_id in user_waiting_episode and not text.startswith('/'):
        txt = text.strip()
        if txt.isdigit():
            episode_counters[chat_id] = int(txt)
            user_waiting_episode.discard(chat_id)
            send_message(chat_id, f"Episode set to {txt}. Send your video(s) to begin.")
            save_state()
        else:
            send_message(chat_id, "Please send a valid episode number (e.g., 5).")
        return

    if text == '/start' or text.startswith('/start@'):
        started_users.add(chat_id)
        # Reset per-chat episode counter and clear quality (require selection)
        episode_counters[chat_id] = 1
        user_quality.pop(chat_id, None)
        user_videos.pop(chat_id, None)
        user_waiting_quality.discard(chat_id)
        save_state()

        # Show quality selection directly
        keyboard = {
            "inline_keyboard": [
                [{"text": "480p (1x)", "callback_data": "480"}],
                [{"text": "720p (2x)", "callback_data": "720"}],
                [{"text": "1080p (3x)", "callback_data": "1080"}],
                [{"text": "2160p (4x)", "callback_data": "2160"}]
            ]
        }
        send_message(chat_id, "Select Quality:", json.dumps(keyboard))
        user_waiting_quality.add(chat_id)
    elif text == '/stop' or text.startswith('/stop@'):
        if chat_id in started_users:
            started_users.discard(chat_id)
            user_videos.pop(chat_id, None)
            user_quality.pop(chat_id, None)
            user_waiting_quality.discard(chat_id)
            send_message(chat_id, "Bot deactivated in this chat. Send /start to activate again.")
            print(f"Bot stopped for chat {chat_id}")
            save_state()
        else:
            send_message(chat_id, "Bot is already inactive in this chat.")
    elif chat_id not in started_users and chat_id > 0:
        send_message(chat_id, "Please send /start first to activate the bot! 🚀")
    elif text == '/help' or text.startswith('/help@'):
        send_message(chat_id, get_help_text(chat_id))
    elif text == '/autocaption' or text.startswith('/autocaption@'):
        keyboard = {
            "inline_keyboard": [
                [{"text": "480p (1x)", "callback_data": "480"}],
                [{"text": "720p (2x)", "callback_data": "720"}],
                [{"text": "1080p (3x)", "callback_data": "1080"}],
                [{"text": "2160p (4x)", "callback_data": "2160"}]
            ]
        }
        send_message(chat_id, "Select Quality:", json.dumps(keyboard))
        user_waiting_quality.add(chat_id)
        save_state()
    elif text == '/refresh' or text.startswith('/refresh@'):
        # Reset episode counter only for the requesting chat
        episode_counters[chat_id] = 1
        # clear any queued videos for this chat
        user_videos.pop(chat_id, None)
        send_message(chat_id, "✅ Your episode counter has been reset to 1.")
        print(f"Server refreshed by user {chat_id}")
        save_state()
    elif text == '/del' or text.startswith('/del@'):
        if chat_id < 0:
            if chat_id in all_messages and len(all_messages[chat_id]) > 0:
                if is_bot_admin(chat_id):
                    for message_id in list(all_messages.get(chat_id, [])):
                        delete_message(chat_id, message_id)
                    all_messages[chat_id] = []
                    bot_messages[chat_id] = []
                    video_messages[chat_id] = []
                    user_photos[chat_id] = []
                    print(f"Admin delete: Deleted ALL messages for channel {chat_id}")
                else:
                    to_keep = set(video_messages.get(chat_id, [])) | set(user_photos.get(chat_id, []))
                    remaining = []
                    for message_id in all_messages.get(chat_id, []):
                        if message_id in to_keep:
                            remaining.append(message_id)
                        else:
                            delete_message(chat_id, message_id)
                    all_messages[chat_id] = remaining
                    bot_messages[chat_id] = []
                    print(f"Deleted messages for channel {chat_id}; kept {len(remaining)} items")
        else:
            if chat_id in bot_messages:
                deleted_ids = set(bot_messages[chat_id])
                for message_id in bot_messages[chat_id]:
                    delete_message(chat_id, message_id)
                bot_messages[chat_id] = []
                if chat_id in all_messages:
                    all_messages[chat_id] = [m for m in all_messages[chat_id] if m not in deleted_ids]
                print(f"Deleted bot messages for chat {chat_id}")
    elif text == '/all_del' or text.startswith('/all_del@'):
        if chat_id in all_messages:
            for message_id in all_messages[chat_id]:
                delete_message(chat_id, message_id)
            all_messages[chat_id] = []
            bot_messages[chat_id] = []
            video_messages[chat_id] = []
            print(f"Deleted ALL messages for chat {chat_id}")
    elif chat_id in user_waiting_quality and text in ['480', '720', '1080', '2160']:
        user_quality[chat_id] = text
        user_waiting_quality.remove(chat_id)
        send_message(chat_id, f"Quality set to {text}p!")
        save_state()


def extract_episode_from_text(text):
    """Extract episode number from text using regex patterns."""
    if not text:
        return None
    
    original = text
    text = text.strip()
    print(f"Attempting to extract episode from: '{text}'")
    
    # Patterns ordered by priority (most specific first)
    patterns = [
        r'[:\s]+(\d{2})(?:,|\s)',  # : 02, or space 02,
        r's\d+e(\d{2,3})',  # S01E02
        r'episode[\s:_-]+(\d{1,3})',  # episode 12
        r'\bep[\s:_-]+(\d{1,3})',  # ep 12
        r'\be(\d{1,3})\b',  # e12
        r'\[(\d{1,3})\]',  # [12]
        r'-\s*(\d{1,3})\s*-',  # - 12 -
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            ep_num = int(match.group(1))
            # Validate: exclude quality numbers, year-like numbers, and invalid ranges
            if ep_num not in [480, 720, 1080, 2160] and not (1900 <= ep_num <= 2100) and 1 <= ep_num <= 999:
                print(f"✓ Detected episode: {ep_num}")
                return ep_num
    
    print("✗ No episode detected")
    return None

def handle_video(chat_id, video_file_id, caption=None, filename=None):
    global episode_counter
    chat_type = "group/channel" if chat_id < 0 else "private"
    print(f"Received video from {chat_type} chat_id: {chat_id}")
    print(f"Caption: {caption}")
    print(f"Filename: {filename}")

    # Require the user to select quality first
    if chat_id not in user_quality:
        send_message(chat_id, "please select the quality first")
        return

    if chat_id not in started_users and chat_id > 0:
        send_message(chat_id, "Please send /start first to activate the bot! 🚀")
        return
    
    # Auto-detect episode from caption or filename on every first video of batch
    if chat_id not in user_videos or len(user_videos.get(chat_id, [])) == 0:
        detected_ep = None
        if caption:
            detected_ep = extract_episode_from_text(caption)
        if not detected_ep and filename:
            detected_ep = extract_episode_from_text(filename)
        
        if detected_ep:
            episode_counters[chat_id] = detected_ep
            save_state()
            print(f"✓ Episode counter set to {detected_ep}")

    max_quality = user_quality.get(chat_id, '480')

    if chat_id not in user_videos:
        user_videos[chat_id] = []

    user_videos[chat_id].append(video_file_id)

    quality_levels = ['480', '720', '1080', '2160']
    quality_labels = {'480': 'SD', '720': 'HD', '1080': 'FHD', '2160': '4K'}
    max_index = quality_levels.index(max_quality)
    required_videos = max_index + 1

    if len(user_videos[chat_id]) >= required_videos:
        ep = episode_counters.get(chat_id, episode_counter)
        for i in range(required_videos):
            current_quality = quality_levels[i]
            current_label = quality_labels[current_quality]
            current_video = user_videos[chat_id][i]

            caption = f"""Episode :- {ep}
🗣 Language :- Hindi Dub
🟡 Quality :- {current_quality}p [{current_label}]
@NEW_HINDI_ANIME_OFFICIAL_DUB"""

            send_video(chat_id, current_video, caption)
            time.sleep(1)

        user_videos[chat_id] = []
        # Only auto-increment if no episode was detected
        episode_counters[chat_id] = ep + 1
        save_state()
        print(f"Processed episode {ep} with {required_videos} different quality videos")

def send_message(chat_id, text, keyboard=None):
    url = f"{BASE_URL}/sendMessage"
    data = {
        'chat_id': chat_id,
        'text': text
    }
    if keyboard:
        data['reply_markup'] = keyboard
    response = _request_with_backoff('post', url, data=data)
    try:
        result = response.json()
        if result.get('ok'):
            message_id = result['result']['message_id']
            if chat_id not in bot_messages:
                bot_messages[chat_id] = []
            bot_messages[chat_id].append(message_id)
            if chat_id not in all_messages:
                all_messages[chat_id] = []
            all_messages[chat_id].append(message_id)
        else:
            print(f"Failed to send message to {chat_id}: {result}")
    except Exception as e:
        print(f"Error sending message to {chat_id}: {e}")
    return response

def send_video(chat_id, video_file_id, caption):
    url = f"{BASE_URL}/sendVideo"
    data = {
        'chat_id': chat_id,
        'video': video_file_id,
        'caption': caption
    }
    response = _request_with_backoff('post', url, data=data)
    try:
        result = response.json()
        if result.get('ok'):
            message_id = result['result']['message_id']
            if chat_id not in all_messages:
                all_messages[chat_id] = []
            all_messages[chat_id].append(message_id)
            if chat_id not in video_messages:
                video_messages[chat_id] = []
            video_messages[chat_id].append(message_id)
            # Also track as a bot message if we sent it (so /del in private can remove it)
            if chat_id not in bot_messages:
                bot_messages[chat_id] = []
            bot_messages[chat_id].append(message_id)
    except:
        pass
    return response


def is_bot_admin(chat_id):
    """Return True if bot is admin/creator in chat_id."""
    global BOT_ID
    if BOT_ID is None:
        return False
    try:
        url = f"{BASE_URL}/getChatMember"
        params = {'chat_id': chat_id, 'user_id': BOT_ID}
        r = requests.get(url, params=params)
        j = r.json()
        if not j.get('ok'):
            return False
        status = j['result'].get('status')
        return status in ('administrator', 'creator')
    except Exception:
        return False

def delete_message(chat_id, message_id):
    url = f"{BASE_URL}/deleteMessage"
    data = {
        'chat_id': chat_id,
        'message_id': message_id
    }
    _request_with_backoff('post', url, data=data)

def get_updates():
    global last_update_id
    url = f"{BASE_URL}/getUpdates"
    params = {'offset': last_update_id + 1}
    response = requests.get(url, params=params)
    return response.json()

def main():
    global episode_counter, last_update_id
    load_state()
    # Get bot info
    bot_info_url = f"{BASE_URL}/getMe"
    bot_response = requests.get(bot_info_url)
    if bot_response.status_code == 200:
        bot_data = bot_response.json()
        if bot_data['ok']:
            BOT_ID = bot_data['result']['id']
            BOT_USERNAME = bot_data['result']['username']
            print(f"Bot started! Username: @{BOT_USERNAME}")
        else:
            print("Bot started!")
    else:
        print("Bot started!")
    
    while True:
        try:
            updates = get_updates()
            
            if updates['ok']:
                for update in updates['result']:
                    last_update_id = update['update_id']
                    
                    if 'callback_query' in update:
                        chat_id = update['callback_query']['message']['chat']['id']
                        # Auto-enable for groups/channels
                        if chat_id < 0:
                            started_users.add(chat_id)
                        elif chat_id not in started_users:
                            callback_url = f"{BASE_URL}/answerCallbackQuery"
                            callback_data = {'callback_query_id': update['callback_query']['id']}
                            requests.post(callback_url, data=callback_data)
                            send_message(chat_id, "Please send /start first to activate the bot! 🚀")
                            continue
                        
                        quality = update['callback_query']['data']
                        user_quality[chat_id] = quality
                        # clear waiting flag and persist selection
                        user_waiting_quality.discard(chat_id)
                        save_state()
                        
                        callback_url = f"{BASE_URL}/answerCallbackQuery"
                        callback_data = {'callback_query_id': update['callback_query']['id']}
                        requests.post(callback_url, data=callback_data)
                        
                        # Delete the quality selection message
                        quality_msg_id = update['callback_query']['message'].get('message_id')
                        if quality_msg_id:
                            delete_message(chat_id, quality_msg_id)
                        
                        # Send messages and schedule deletion
                        msg1 = send_message(chat_id, f"Quality set to {quality}p!")
                        msg2 = send_message(chat_id, "Please send the episode number to start from (e.g., 5).")
                        user_waiting_episode.add(chat_id)
                        
                        # Delete messages after 2 seconds
                        time.sleep(2)
                        if msg1:
                            try:
                                result1 = msg1.json()
                                if result1.get('ok'):
                                    delete_message(chat_id, result1['result']['message_id'])
                            except: pass
                        if msg2:
                            try:
                                result2 = msg2.json()
                                if result2.get('ok'):
                                    delete_message(chat_id, result2['result']['message_id'])
                            except: pass
                    
                    elif 'message' in update:
                        chat_id = update['message']['chat']['id']
                        chat_type = "group/channel" if chat_id < 0 else "private"
                        print(f"Received message from {chat_type} chat_id: {chat_id}")
                        
                        # Auto-enable for groups/channels
                        if chat_id < 0:
                            started_users.add(chat_id)
                        
                        if 'text' in update['message']:
                            # Track text messages
                            msg_id = update['message'].get('message_id')
                            if msg_id is not None:
                                if chat_id not in all_messages:
                                    all_messages[chat_id] = []
                                all_messages[chat_id].append(msg_id)
                            text = update['message']['text']
                            handle_text(chat_id, text)
                        
                        elif 'photo' in update['message']:
                            # Track user's photo messages to preserve them
                            photo_msg_id = update['message'].get('message_id')
                            if photo_msg_id is not None:
                                if chat_id not in user_photos:
                                    user_photos[chat_id] = []
                                user_photos[chat_id].append(photo_msg_id)
                        
                        elif 'video' in update['message']:
                            # Track user's incoming video in all_messages (will be deleted)
                            vid_msg_id = update['message'].get('message_id')
                            if vid_msg_id is not None:
                                if chat_id not in all_messages:
                                    all_messages[chat_id] = []
                                all_messages[chat_id].append(vid_msg_id)
                            video_file_id = update['message']['video']['file_id']
                            caption = update['message'].get('caption', '')
                            filename = update['message']['video'].get('file_name', '')
                            handle_video(chat_id, video_file_id, caption, filename)

                    # Handle posts in channels (they appear as 'channel_post' in updates)
                    elif 'channel_post' in update:
                        post = update['channel_post']
                        chat_id = post['chat']['id']
                        chat_type = "group/channel" if chat_id < 0 else "private"
                        print(f"Received channel_post from {chat_type} chat_id: {chat_id}")

                        # Auto-enable for channels
                        if chat_id < 0:
                            started_users.add(chat_id)

                        if 'text' in post:
                            # Track text posts
                            post_id = post.get('message_id')
                            if post_id is not None:
                                if chat_id not in all_messages:
                                    all_messages[chat_id] = []
                                all_messages[chat_id].append(post_id)
                            text = post['text']
                            handle_text(chat_id, text)
                        
                        elif 'photo' in post:
                            # Track channel photos to preserve them
                            photo_post_id = post.get('message_id')
                            if photo_post_id is not None:
                                if chat_id not in user_photos:
                                    user_photos[chat_id] = []
                                user_photos[chat_id].append(photo_post_id)

                        elif 'video' in post:
                            # Track incoming video in all_messages (will be deleted)
                            vid_post_id = post.get('message_id')
                            if vid_post_id is not None:
                                if chat_id not in all_messages:
                                    all_messages[chat_id] = []
                                all_messages[chat_id].append(vid_post_id)
                            video_file_id = post['video']['file_id']
                            caption = post.get('caption', '')
                            filename = post['video'].get('file_name', '')
                            handle_video(chat_id, video_file_id, caption, filename)
            
            time.sleep(1)
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()