import requests
import json

APP_ID = ""
BOT_TOKEN = ""
HEADERS = {"Authorization": f"Bot {BOT_TOKEN}"}

def get_app_emojis():
    url = f"https://discord.com/api/v10/applications/{APP_ID}/emojis"
    response = requests.get(url, headers=HEADERS)
    
    if response.status_code == 200:
        data = response.json()
        emojis = data.get('items', [])
        
        # Create a dictionary for easy bot use: { "name": "full_emoji_tag" }
        emoji_map = {}
        for e in emojis:
            prefix = "a" if e['animated'] else ""
            # Format: <:name:id> or <a:name:id>
            emoji_map[e['name']] = f"<{prefix}:{e['name']}:{e['id']}>"
        
        # Save inside the gambling cog so cards.py can load it at runtime
        with open("gambling/emoji_map.json", "w") as f:
            json.dump(emoji_map, f, indent=4)

        print(f"Successfully mapped {len(emojis)} emojis to gambling/emoji_map.json")
    else:
        print(f"Error {response.status_code}: {response.text}")

get_app_emojis()