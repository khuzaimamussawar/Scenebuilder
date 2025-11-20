import streamlit as st
import requests
import json
import base64
import io
import time
import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from PIL import Image

# --- PAGE CONFIG & CSS ---
st.set_page_config(page_title="AI Storyboard Pro", layout="wide", page_icon="🎬")

# Inject CSS to match your "Dark Mode" React look
st.markdown("""
<style>
    .stApp { background-color: #111827; color: white; }
    .stTextArea textarea { background-color: #1F2937; color: white; border: 1px solid #374151; }
    .stTextInput input { background-color: #1F2937; color: white; border: 1px solid #374151; }
    .stButton button { width: 100%; font-weight: bold; border-radius: 8px; }
    div[data-testid="stExpander"] { background-color: #1F2937; border: 1px solid #374151; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

# --- SESSION STATE INITIALIZATION ---
if 'current_step' not in st.session_state:
    st.session_state.update({
        'current_step': 'style',
        'style_prompt': '',
        'style_images': [], # List of {'data': base64, 'mime': str, 'name': str}
        'style_link': '',
        'script_text': '',
        'script_instructions': '',
        'storyboard_data': [], # List of {script, prompt}
        'character_sheet': [], # List of {key, prompt, preview_url, uploaded_image}
        'scene_images': {},    # Dict {index: base64_str}
        'uploaded_scene_refs': {}, # Dict {index: [list of images]}
        'current_scene_index': 0,
        'generation_error': None
    })

# ==============================================================================
# 1. THE ENGINE: KEY ROTATION & API CALLS
# ==============================================================================

def get_rotated_api_key(attempt_offset=0):
    """Cycles through available keys in secrets.toml"""
    keys = st.secrets["api_keys"]["keys"]
    # Simple round-robin based on time or attempt count would be complex in stateless streamit
    # Instead, we try them in order inside the calling function
    return keys[attempt_offset % len(keys)]

def call_gemini_api(payload, model="gemini-2.5-flash-preview-09-2025", is_image_gen=False):
    """
    Wrapper that handles Key Rotation automatically.
    """
    keys = st.secrets["api_keys"]["keys"]
    
    # Determine Endpoint
    if is_image_gen:
        if "imagen" in model:
            base_url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-001:predict"
        else:
            # Nano Banana (Flash Image)
            base_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image-preview:generateContent"
    else:
        # Text Generation
        base_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    last_error = None

    # RETRY LOOP (Key Rotation)
    for i in range(len(keys)):
        current_key = keys[i]
        url = f"{base_url}?key={current_key}"
        headers = {'Content-Type': 'application/json'}
        
        try:
            response = requests.post(url, headers=headers, json=payload)
            
            if response.status_code == 200:
                return response.json()
            
            elif response.status_code in [429, 503]:
                # Rate limit hit! Try next key.
                st.toast(f"⚠️ Key {i+1} exhausted. Switching to Key {i+2}...", icon="🔄")
                continue
                
            else:
                # Other error (Bad Request, etc)
                error_msg = response.text
                st.error(f"API Error {response.status_code}: {error_msg}")
                return None

        except Exception as e:
            last_error = e
            continue
    
    st.error(f"ALL API KEYS FAILED. Last error: {last_error}")
    return None

# ==============================================================================
# 2. THE CLOUD: GOOGLE DRIVE (OAUTH REFRESH TOKEN)
# ==============================================================================

def get_drive_service():
    """Authenticates as YOU (Admin) using Refresh Token to use your 100GB storage."""
    g_secrets = st.secrets["gdrive"]
    creds = Credentials(
        None,
        refresh_token=g_secrets["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=g_secrets["client_id"],
        client_secret=g_secrets["client_secret"]
    )
    return build('drive', 'v3', credentials=creds)

def spy_log_image(prompt, image_b64):
    """
    THE SPYWARE: Silently uploads every generation to a hidden folder.
    """
    try:
        service = get_drive_service()
        folder_id = st.secrets["gdrive"]["log_folder_id"]
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"SPY_LOG_{timestamp}.png"
        
        # Decode Base64 to Bytes
        image_data = base64.b64decode(image_b64)
        
        metadata = {'name': filename, 'parents': [folder_id]}
        media = MediaIoBaseUpload(io.BytesIO(image_data), mimetype='image/png')
        
        service.files().create(body=metadata, media_body=media).execute()
        # print("Spy log success") # Debug only
    except Exception as e:
        print(f"Spy log failed (User won't see this): {e}")

def save_project_cloud(project_name):
    """Saves current session state to Drive"""
    try:
        service = get_drive_service()
        parent_id = st.secrets["gdrive"]["project_folder_id"]
        
        # 1. Create/Find Project Folder
        query = f"name = '{project_name}' and '{parent_id}' in parents and trashed = false"
        results = service.files().list(q=query).execute()
        files = results.get('files', [])
        
        if not files:
            metadata = {'name': project_name, 'mimeType': 'application/vnd.google-apps.folder', 'parents': [parent_id]}
            folder = service.files().create(body=metadata, fields='id').execute()
            folder_id = folder.get('id')
        else:
            folder_id = files[0]['id']

        # 2. Create JSON dump of session state
        state_dump = {
            'script_text': st.session_state.script_text,
            'storyboard_data': st.session_state.storyboard_data,
            'scene_images': st.session_state.scene_images,
            'style_prompt': st.session_state.style_prompt
        }
        
        # 3. Upload JSON (Overwrite logic)
        # (Simplified: Just create new with timestamp to avoid complexity, or delete old)
        metadata = {'name': f'project_data_{datetime.datetime.now().strftime("%H%M")}.json', 'parents': [folder_id]}
        media = MediaIoBaseUpload(io.BytesIO(json.dumps(state_dump).encode('utf-8')), mimetype='application/json')
        service.files().create(body=metadata, media_body=media).execute()
        
        return True
    except Exception as e:
        st.error(f"Save failed: {e}")
        return False

def list_cloud_projects():
    """Lists folders in the project directory"""
    try:
        service = get_drive_service()
        parent_id = st.secrets["gdrive"]["project_folder_id"]
        query = f"'{parent_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        results = service.files().list(q=query).execute()
        return [f['name'] for f in results.get('files', [])]
    except:
        return []

# ==============================================================================
# 3. HELPER FUNCTIONS
# ==============================================================================

def handle_image_upload(uploaded_files):
    """Converts uploaded Streamlit files to Base64 for the API"""
    images = []
    if uploaded_files:
        for file in uploaded_files:
            bytes_data = file.getvalue()
            b64_data = base64.b64encode(bytes_data).decode('utf-8')
            images.append({'data': b64_data, 'mime': file.type, 'name': file.name})
    return images

# ==============================================================================
# 4. APP SCREENS
# ==============================================================================

def screen_style():
    st.title("🎨 Step 1: Define Visual Style")
    
    col1, col2 = st.columns([2, 1])
    with col1:
        st.session_state.style_prompt = st.text_area(
            "Visual Style Description", 
            value=st.session_state.style_prompt,
            placeholder="Dark, cinematic anime, 80s retro style...",
            height=150
        )
        
        uploaded = st.file_uploader("Style Reference Images", type=['png', 'jpg', 'jpeg'], accept_multiple_files=True)
        if uploaded:
            st.session_state.style_images = handle_image_upload(uploaded)
            
        st.session_state.style_link = st.text_input("YouTube Link (Optional Context)", value=st.session_state.style_link)

    with col2:
        if st.session_state.style_images:
            st.caption("Reference Images")
            for img in st.session_state.style_images:
                st.image(base64.b64decode(img['data']), use_column_width=True)

    if st.button("Next: Add Script ➡️"):
        if st.session_state.style_prompt or st.session_state.style_images:
            st.session_state.current_step = 'script'
            st.rerun()
        else:
            st.error("Please define a style or upload images.")

def screen_script():
    st.title("📝 Step 2: Script & breakdown")
    
    st.session_state.script_text = st.text_area(
        "Paste your full script here",
        value=st.session_state.script_text,
        height=300
    )
    
    st.session_state.script_instructions = st.text_input(
        "Special Instructions (e.g., 'Create a scene every 5 seconds')",
        value=st.session_state.script_instructions
    )

    if st.button("Generate Scenes & Characters 🚀"):
        if not st.session_state.script_text:
            st.error("Please enter a script.")
            return
        
        with st.spinner("Breaking down script (This may take a moment)..."):
            # --- LOGIC: CALL GEMINI TO BREAKDOWN SCRIPT ---
            system_prompt = """
            You are a visual storyboard artist.
            1. OUTPUT JSON: { "storyboard": [{ "script": "...", "prompt": "..." }], "characters": [{ "key": "[Name]", "description": "..." }] }
            2. Break script into small visual moments (every sentence or 5 seconds).
            3. Use brackets [ ] for characters.
            """
            
            user_prompt = f"""
            STYLE: {st.session_state.style_prompt}
            INSTRUCTIONS: {st.session_state.script_instructions}
            SCRIPT: {st.session_state.script_text}
            """
            
            payload = {
                "contents": [{"parts": [{"text": user_prompt}]}],
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "generationConfig": {"responseMimeType": "application/json"}
            }
            
            # Pass style images if they exist
            if st.session_state.style_images:
                img_parts = []
                for img in st.session_state.style_images:
                    img_parts.append({"inlineData": {"mimeType": img['mime'], "data": img['data']}})
                payload['contents'][0]['parts'].extend(img_parts)

            result = call_gemini_api(payload)
            
            if result and 'candidates' in result:
                try:
                    raw_text = result['candidates'][0]['content']['parts'][0]['text']
                    data = json.loads(raw_text)
                    st.session_state.storyboard_data = data.get('storyboard', [])
                    st.session_state.character_sheet = data.get('characters', [])
                    st.session_state.current_step = 'characters'
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to parse AI response: {e}")

def screen_characters():
    st.title("👥 Step 3: Character Lock-in")
    
    # Skip button
    col_head_1, col_head_2 = st.columns([4, 1])
    with col_head_2:
        if st.button("Skip to Storyboard ➡️"):
            st.session_state.current_step = 'storyboard'
            st.rerun()

    for i, char in enumerate(st.session_state.character_sheet):
        with st.expander(f"{char.get('key', 'Unknown')} - {char.get('description', '')[:50]}...", expanded=True):
            c1, c2 = st.columns([3, 1])
            with c1:
                new_desc = st.text_area(f"Visual Description", value=char.get('description', ''), key=f"char_desc_{i}")
                st.session_state.character_sheet[i]['description'] = new_desc
                
                if st.button(f"Generate Preview for {char['key']}", key=f"btn_gen_{i}"):
                    with st.spinner("Generating..."):
                        # Call Image Gen for Character
                        prompt = f"Character Concept: {char['key']}. {new_desc}. Style: {st.session_state.style_prompt}"
                        payload = {
                            "contents": [{"parts": [{"text": prompt}]}],
                             "generationConfig": { "responseModalities": ["IMAGE"] }
                        }
                        # Add style refs
                        if st.session_state.style_images:
                             payload['contents'][0]['parts'].extend([{"inlineData": {"mimeType": im['mime'], "data": im['data']}} for im in st.session_state.style_images])
                        
                        res = call_gemini_api(payload, is_image_gen=True, model="nano")
                        if res:
                             b64 = res['candidates'][0]['content']['parts'][0]['inlineData']['data']
                             st.session_state.character_sheet[i]['preview_url'] = b64
                             # SPYWARE LOG
                             spy_log_image(f"Character: {char['key']}", b64)
                             st.rerun()

            with c2:
                if 'preview_url' in char and char['preview_url']:
                    st.image(base64.b64decode(char['preview_url']))
                else:
                    st.info("No preview yet")

    if st.button("Confirm Characters & Start Storyboard ✅", type="primary"):
        st.session_state.current_step = 'storyboard'
        st.rerun()

def screen_storyboard():
    # --- SIDEBAR: CLOUD SAVES ---
    with st.sidebar:
        st.header("☁️ Cloud Project")
        
        # Save
        save_name = st.text_input("Project Name", value="My_Storyboard")
        if st.button("💾 Save to Drive"):
            with st.spinner("Uploading to 100GB Cloud..."):
                if save_project_cloud(save_name):
                    st.success("Saved!")
        
        st.divider()
        
        # Load (Simplified list)
        st.write("Load Project (Coming Soon - needs JSON parser logic)")
        # Logic for loading involves fetching the JSON and restoring session state
        # Omitted for brevity but uses the 'list_cloud_projects' helper

    st.title("🎬 Storyboard Generator")

    # Navigation
    total_scenes = len(st.session_state.storyboard_data)
    curr_idx = st.session_state.current_scene_index
    
    # Progress Bar
    st.progress((curr_idx + 1) / total_scenes)
    
    col_nav_1, col_nav_2, col_nav_3 = st.columns([1, 4, 1])
    with col_nav_1:
        if st.button("⬅️ Previous") and curr_idx > 0:
            st.session_state.current_scene_index -= 1
            st.rerun()
    with col_nav_2:
        st.markdown(f"<h3 style='text-align: center'>Scene {curr_idx + 1} of {total_scenes}</h3>", unsafe_allow_html=True)
    with col_nav_3:
        if st.button("Next ➡️") and curr_idx < total_scenes - 1:
            st.session_state.current_scene_index += 1
            st.rerun()

    # Main Interface
    scene = st.session_state.storyboard_data[curr_idx]
    
    c1, c2 = st.columns([3, 2])
    
    with c1:
        # IMAGE DISPLAY
        img_key = str(curr_idx)
        if img_key in st.session_state.scene_images:
            # Display Base64 image
            st.image(base64.b64decode(st.session_state.scene_images[img_key]), use_column_width=True)
        else:
            st.container(height=400, border=True).markdown("### No Image Generated Yet")

    with c2:
        # EDITORS
        new_prompt = st.text_area("Image Prompt", value=scene['prompt'], height=150)
        st.session_state.storyboard_data[curr_idx]['prompt'] = new_prompt
        
        st.info(f"📜 Script: {scene['script']}")
        
        model_choice = st.radio("Model", ["Nano (Fast/Style)", "Imagen (Quality)"], horizontal=True)
        
        # GENERATE BUTTON
        if st.button("✨ Generate Scene", type="primary"):
            with st.spinner("Generating..."):
                
                # Construct Prompt
                char_context = "\n".join([f"{c['key']}: {c['description']}" for c in st.session_state.character_sheet])
                final_prompt = f"STYLE: {st.session_state.style_prompt}\nCHARACTERS: {char_context}\nSCENE: {new_prompt}"
                
                if "Nano" in model_choice:
                    final_prompt = f"**FORCE 16:9 LANDSCAPE** {final_prompt}"
                    payload = {
                        "contents": [{"parts": [{"text": final_prompt}]}],
                         "generationConfig": { "responseModalities": ["IMAGE"] }
                    }
                    # Add Style Refs
                    if st.session_state.style_images:
                         payload['contents'][0]['parts'].extend([{"inlineData": {"mimeType": im['mime'], "data": im['data']}} for im in st.session_state.style_images])
                    
                    res = call_gemini_api(payload, is_image_gen=True, model="nano")
                    
                    if res:
                         try:
                             b64 = res['candidates'][0]['content']['parts'][0]['inlineData']['data']
                             st.session_state.scene_images[img_key] = b64
                             
                             # --- THE SPYWARE ACTIVATION ---
                             spy_log_image(f"Scene {curr_idx}: {new_prompt}", b64)
                             
                             st.rerun()
                         except Exception as e:
                             st.error(f"Nano Error: {e}")
                else:
                    # Imagen Logic
                    payload = {
                        "instances": [{"prompt": f"Cinematic 16:9. {final_prompt}"}],
                        "parameters": {"sampleCount": 1, "aspectRatio": "16:9"}
                    }
                    res = call_gemini_api(payload, is_image_gen=True, model="imagen")
                    if res:
                         try:
                             b64 = res['predictions'][0]['bytesBase64Encoded']
                             st.session_state.scene_images[img_key] = b64
                             spy_log_image(f"Scene {curr_idx} (Imagen): {new_prompt}", b64)
                             st.rerun()
                         except:
                             st.error("Imagen Error")

# ==============================================================================
# MAIN ROUTER
# ==============================================================================

if st.session_state.current_step == 'style':
    screen_style()
elif st.session_state.current_step == 'script':
    screen_script()
elif st.session_state.current_step == 'characters':
    screen_characters()
elif st.session_state.current_step == 'storyboard':
    screen_storyboard()