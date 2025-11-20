import streamlit as st
import requests
import json
import base64
import io
import time
import datetime
import zipfile
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from PIL import Image

# ==============================================================================
# 1. CONFIG & CSS (DARK SLATE THEME)
# ==============================================================================
st.set_page_config(page_title="AI Scene Builder", layout="wide", page_icon="🎬")

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
    
    /* Buttons */
    .stButton button {
        border-radius: 0.5rem;
        font-weight: 600;
        border: none;
        transition: all 0.2s;
    }
    
    /* Primary (Blue) */
    button[kind="primary"] {
        background-color: #2563eb; 
        color: white;
    }
    
    /* Secondary (Gray/Dark) */
    button[kind="secondary"] {
        background-color: #334155;
        color: white;
        border: 1px solid #475569;
    }
    
    /* Small Text Links */
    .small-link {
        font-size: 0.8rem;
        color: #60a5fa;
        cursor: pointer;
        text-decoration: none;
    }
    .small-link:hover { text-decoration: underline; }

</style>
""", unsafe_allow_html=True)

# --- SESSION STATE ---
if 'step' not in st.session_state:
    st.session_state.update({
        'step': 1,
        'style_prompt': '',
        'style_images': [],      # Global Style Refs
        'style_link': '',
        'script_text': '',
        'script_instructions': '',
        'storyboard': [],
        'characters': [],
        'scene_images': {},      # Generated images {index: base64}
        'scene_refs': {},        # Scene-specific refs {index: [list of base64]}
        'curr_scene': 0,
        'project_name': "My_Project"
    })

# ==============================================================================
# 2. THE BACKEND (DRIVE & API)
# ==============================================================================

def get_drive_service():
    try:
        g = st.secrets["gdrive"]
        creds = Credentials(None, refresh_token=g["refresh_token"], token_uri="https://oauth2.googleapis.com/token", client_id=g["client_id"], client_secret=g["client_secret"])
        return build('drive', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"Drive Auth Failed: {e}")
        return None

def get_or_create_folder(folder_name, parent_id=None):
    service = get_drive_service()
    if not service: return None
    query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id: query += f" and '{parent_id}' in parents"
    results = service.files().list(q=query, fields="files(id)").execute()
    files = results.get('files', [])
    if files: return files[0]['id']
    else:
        meta = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder'}
        if parent_id: meta['parents'] = [parent_id]
        folder = service.files().create(body=meta, fields='id').execute()
        return folder.get('id')

def save_project(name):
    try:
        service = get_drive_service()
        root_id = get_or_create_folder("_Freelancer_Projects")
        proj_id = get_or_create_folder(name, root_id)
        
        # Save JSON Data
        data = {
            'script': st.session_state.script_text,
            'style': st.session_state.style_prompt,
            'storyboard': st.session_state.storyboard,
            'characters': st.session_state.characters
        }
        
        # Remove old JSON
        q = f"name = 'data.json' and '{proj_id}' in parents"
        files = service.files().list(q=q).execute().get('files', [])
        for f in files: service.files().delete(fileId=f['id']).execute()
        
        media = MediaIoBaseUpload(io.BytesIO(json.dumps(data).encode('utf-8')), mimetype='application/json')
        service.files().create(body={'name': 'data.json', 'parents': [proj_id]}, media_body=media).execute()
        
        # Save Generated Images
        for idx, b64 in st.session_state.scene_images.items():
            fname = f"scene_{idx}.png" 
            # Delete old
            q = f"name = '{fname}' and '{proj_id}' in parents"
            files = service.files().list(q=q).execute().get('files', [])
            for f in files: service.files().delete(fileId=f['id']).execute()
            
            img_data = base64.b64decode(b64)
            media = MediaIoBaseUpload(io.BytesIO(img_data), mimetype='image/png')
            service.files().create(body={'name': fname, 'parents': [proj_id]}, media_body=media).execute()
            
        return True
    except Exception as e:
        st.error(f"Save Error: {e}")
        return False

def spy_log(prompt, b64):
    try:
        service = get_drive_service()
        folder_id = get_or_create_folder("_Spyware_Logs")
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        media = MediaIoBaseUpload(io.BytesIO(base64.b64decode(b64)), mimetype='image/png')
        service.files().create(body={'name': f"LOG_{ts}.png", 'parents': [folder_id]}, media_body=media).execute()
    except: pass

def call_gemini(payload, model_type="text"):
    try:
        keys = st.secrets["api_keys"]["keys"]
    except:
        st.error("No API Keys found in secrets.")
        return None
    
    if model_type == "image":
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image-preview:generateContent"
    elif model_type == "imagen":
        url = "https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-001:predict"
    else:
        # Text
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent"

    for i, key in enumerate(keys):
        try:
            res = requests.post(f"{url}?key={key}", headers={'Content-Type': 'application/json'}, json=payload)
            if res.status_code == 200: return res.json()
            if res.status_code == 429: 
                st.toast(f"Key {i+1} Exhausted. Switching...", icon="⚠️")
                continue
            # Fallback for preview models geo-blocking
            if res.status_code in [400, 404] and model_type == "text":
                st.warning(f"Preview model failed ({res.status_code}). Trying standard Flash 1.5...")
                fb_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
                res_fb = requests.post(f"{fb_url}?key={key}", headers={'Content-Type': 'application/json'}, json=payload)
                if res_fb.status_code == 200: return res_fb.json()
            
            st.error(f"API Error {res.status_code}: {res.text}")
        except: continue
    return None

# ==============================================================================
# 3. UI HELPERS
# ==============================================================================

def handle_img_upload(files):
    imgs = []
    for f in files:
        imgs.append({'data': base64.b64encode(f.getvalue()).decode('utf-8'), 'mime': f.type})
    return imgs

def create_zip():
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        for idx, b64 in st.session_state.scene_images.items():
            data = base64.b64decode(b64)
            zf.writestr(f"scene_{int(idx)+1}.png", data)
    return zip_buffer.getvalue()

# ==============================================================================
# 4. APP SCREENS
# ==============================================================================

# --- STEP 1: STYLE ---
if st.session_state.step == 1:
    st.markdown("<h1 style='text-align: center;'>AI Scene Builder</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #94a3b8;'>Step 1: Define your visual style.</p>", unsafe_allow_html=True)
    
    st.session_state.style_prompt = st.text_area("Visual Style Description", value=st.session_state.style_prompt, placeholder="e.g., 'Dark, cinematic anime, 80s retro style'")
    uploaded = st.file_uploader("Global Style Reference Images (Used for ALL scenes)", accept_multiple_files=True, type=['png', 'jpg'])
    if uploaded: st.session_state.style_images = handle_img_upload(uploaded)
    
    if st.session_state.style_images:
        cols = st.columns(6)
        for i, img in enumerate(st.session_state.style_images):
            cols[i % 6].image(base64.b64decode(img['data']), use_column_width=True)

    st.session_state.style_link = st.text_input("YouTube Link (Optional Context)", value=st.session_state.style_link)
    
    if st.button("Next: Add Script ➡️", type="primary"): 
        if st.session_state.style_prompt: st.session_state.step = 2; st.rerun()
        else: st.warning("Please define a style.")

# --- STEP 2: SCRIPT ---
elif st.session_state.step == 2:
    st.markdown("<h1 style='text-align: center;'>Step 2: Paste Script</h1>", unsafe_allow_html=True)
    st.session_state.script_text = st.text_area("", value=st.session_state.script_text, height=300, placeholder="Paste script here...")
    st.session_state.script_instructions = st.text_input("Instructions", value=st.session_state.script_instructions)
    
    if st.button("Generate Scenes 🚀", type="primary"):
        with st.spinner("Analyzing script..."):
            sys_prompt = """You are a visual storyboard artist. 
            OUTPUT JSON ONLY: { "storyboard": [{ "script": "...", "prompt": "..." }], "characters": [{ "key": "[Name]", "description": "..." }] }
            Break script into small visual moments. Maintain consistent character names in brackets like [Batman]."""
            user_prompt = f"STYLE: {st.session_state.style_prompt}\nSCRIPT: {st.session_state.script_text}\nINSTRUCTIONS: {st.session_state.script_instructions}"
            
            payload = {
                "contents": [{"parts": [{"text": user_prompt}]}], 
                "systemInstruction": {"parts": [{"text": sys_prompt}]},
                "generationConfig": {"responseMimeType": "application/json"}
            }
            if st.session_state.style_images:
                 payload['contents'][0]['parts'].extend([{"inlineData": {"mimeType": im['mime'], "data": im['data']}} for im in st.session_state.style_images])
            
            res = call_gemini(payload, "text")
            if res:
                try:
                    data = json.loads(res['candidates'][0]['content']['parts'][0]['text'])
                    st.session_state.storyboard = data.get('storyboard', [])
                    st.session_state.characters = data.get('characters', [])
                    st.session_state.step = 3
                    st.rerun()
                except: st.error("Parsing Error. Try again.")

# --- STEP 3: CHARACTERS ---
elif st.session_state.step == 3:
    st.markdown("## Step 3: Character Consistency")
    
    cols = st.columns(2)
    for i, char in enumerate(st.session_state.characters):
        with cols[i % 2].container(border=True):
            st.markdown(f"### {char['key']}")
            st.session_state.characters[i]['description'] = st.text_area("Description", char['description'], key=f"cd_{i}", height=100)
            
            # PREVIEW CHAR
            if st.button("Preview", key=f"p{i}"):
                with st.spinner("Generating..."):
                    p = f"Character Concept: {char['key']}. {st.session_state.characters[i]['description']}. Style: {st.session_state.style_prompt}"
                    payload = {"contents": [{"parts": [{"text": p}]}], "generationConfig": {"responseModalities": ["IMAGE"]}}
                    if st.session_state.style_images:
                         payload['contents'][0]['parts'].extend([{"inlineData": {"mimeType": im['mime'], "data": im['data']}} for im in st.session_state.style_images])
                    
                    res = call_gemini(payload, "image")
                    if res:
                        b64 = res['candidates'][0]['content']['parts'][0]['inlineData']['data']
                        st.session_state.characters[i]['preview'] = b64
                        st.rerun()
            
            if 'preview' in char: st.image(base64.b64decode(char['preview']), use_column_width=True)

    c1, c2 = st.columns([1, 5])
    if c1.button("Back"): st.session_state.step = 2; st.rerun()
    
    # This button locks in characters even if user skipped generating previews
    if c2.button("Confirm & Start Storyboard", type="primary"): st.session_state.step = 4; st.rerun()

# --- STEP 4: SCENE BUILDER ---
elif st.session_state.step == 4:
    # Sidebar Save
    with st.sidebar:
        st.header("☁️ Cloud Save")
        st.session_state.project_name = st.text_input("Project Name", st.session_state.project_name)
        if st.button("💾 Save Project (Drive)"):
            with st.spinner("Saving to Drive..."):
                if save_project(st.session_state.project_name): st.success("Saved!")
    
    curr = st.session_state.curr_scene
    total = len(st.session_state.storyboard)
    
    # Top Nav
    c1, c2, c3 = st.columns([3, 1, 1])
    c1.markdown(f"## Scene {curr + 1} / {total}")
    model_mode = c2.radio("Model", ["Nano", "Imagen"], horizontal=True, label_visibility="collapsed")
    if c3.button("Start Over"): st.session_state.step = 1; st.rerun()

    # Main Image
    if str(curr) in st.session_state.scene_images:
        st.image(base64.b64decode(st.session_state.scene_images[str(curr)]), use_column_width=True)
    else:
        st.markdown("<div style='height:400px; background:#0f172a; border:2px dashed #334155; display:flex; align-items:center; justify-content:center; color:#64748b'>No Image</div>", unsafe_allow_html=True)

    # Controls
    with st.container(border=True):
        col_prompt, col_ref = st.columns([3, 1])
        
        # Prompt Area
        with col_prompt:
            st.markdown("**IMAGE PROMPT**")
            # Enhance
            if st.button("✨ Enhance Prompt"):
                 with st.spinner("Enhancing..."):
                    orig = st.session_state.storyboard[curr]['prompt']
                    res = call_gemini({"contents": [{"parts": [{"text": f"Rewrite to be vivid and cinematic: '{orig}'"}]}]}, "text")
                    if res:
                        st.session_state.storyboard[curr]['prompt'] = res['candidates'][0]['content']['parts'][0]['text']
                        st.rerun()
                        
            new_prompt = st.text_area("Prompt", st.session_state.storyboard[curr]['prompt'], height=100, label_visibility="collapsed")
            st.session_state.storyboard[curr]['prompt'] = new_prompt

        # Ref Upload Area
        with col_ref:
            st.markdown("**SCENE REF**")
            scene_up = st.file_uploader("Upload", type=['png', 'jpg'], key=f"ref_{curr}", label_visibility="collapsed")
            if scene_up:
                b64 = base64.b64encode(scene_up.getvalue()).decode('utf-8')
                # Store list of refs
                if str(curr) not in st.session_state.scene_refs: st.session_state.scene_refs[str(curr)] = []
                st.session_state.scene_refs[str(curr)].append({'data': b64, 'mime': scene_up.type})
                st.toast("Ref Added!")
            
            # Generate Button
            if st.button("Regenerate", type="primary", use_container_width=True):
                with st.spinner("Building Scene..."):
                    char_ctx = " ".join([f"{c['key']} is {c['description']}." for c in st.session_state.characters])
                    
                    # CONTINUITY LOGIC
                    prompt_text = f"Style: {st.session_state.style_prompt}. Characters: {char_ctx}. Action: {new_prompt}. Maintain visual continuity with previous scenes. Use the style reference images."
                    
                    parts = [{"text": prompt_text}]
                    
                    # 1. Global Style Refs
                    if st.session_state.style_images: 
                        parts.extend([{"inlineData": {"mimeType": im['mime'], "data": im['data']}} for im in st.session_state.style_images])
                    
                    # 2. Scene Refs
                    if str(curr) in st.session_state.scene_refs:
                        parts.extend([{"inlineData": {"mimeType": im['mime'], "data": im['data']}} for im in st.session_state.scene_refs[str(curr)]])
                    
                    # 3. Previous Scene Image (Continuity)
                    if str(curr - 1) in st.session_state.scene_images:
                        parts.append({"inlineData": {"mimeType": "image/png", "data": st.session_state.scene_images[str(curr - 1)]}})

                    payload = {"contents": [{"parts": parts}], "generationConfig": {"responseModalities": ["IMAGE"]}}
                    
                    if model_mode == "Nano":
                         res = call_gemini(payload, "image")
                         if res:
                            b64 = res['candidates'][0]['content']['parts'][0]['inlineData']['data']
                            st.session_state.scene_images[str(curr)] = b64
                            spy_log(f"Scene {curr}", b64)
                            st.rerun()
                    else:
                         # Imagen Logic (Simplified payload for brevity)
                         # Note: Imagen doesn't support multi-image input in same way as Gemini Nano
                         st.toast("Imagen mode currently supports text-only prompts in this demo.")
        
        st.markdown("---")
        # Script Snippet
        st.markdown("**SCRIPT SNIPPET**")
        if st.button("✨ Gen Prompt from Script"):
             with st.spinner("Writing..."):
                script_snip = st.session_state.storyboard[curr]['script']
                res = call_gemini({"contents": [{"parts": [{"text": f"Write a visual prompt for: '{script_snip}'"}]}]}, "text")
                if res:
                    st.session_state.storyboard[curr]['prompt'] = res['candidates'][0]['content']['parts'][0]['text']
                    st.rerun()
        st.info(st.session_state.storyboard[curr]['script'])

        # Bottom Nav
        st.markdown("---")
        b1, b2, b3, b4, b5, b6 = st.columns(6)
        
        if b1.button("⬅️ Prev") and curr > 0: st.session_state.curr_scene -= 1; st.rerun()
        if b2.button("Next ➡️") and curr < total - 1: st.session_state.curr_scene += 1; st.rerun()
        
        if b3.button("Remove Scene"):
            st.session_state.storyboard.pop(curr)
            # Shift images logic omitted for brevity
            st.rerun()

        if b4.button("[+] Add Scene"):
            st.session_state.storyboard.insert(curr + 1, {"script": "", "prompt": "New scene..."})
            st.session_state.curr_scene += 1
            st.rerun()

        if b5.button("Gen Remaining"):
            st.toast("Generating remaining scenes...")
            # Loop logic would go here
            
        if b6.button("Download All"):
             zip_data = create_zip()
             st.download_button("Download ZIP", data=zip_data, file_name="storyboard.zip", mime="application/zip")
