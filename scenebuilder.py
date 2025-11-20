import streamlit as st
import requests
import json
import base64
import io
import time
import zipfile
import streamlit.components.v1 as components
from PIL import Image

# ==============================================================================
# 1. CONFIG & GLOBAL SETUP
# ==============================================================================

st.set_page_config(page_title="AI Storyboarder Pro", layout="wide", page_icon="🎬")

# --- API KEY SETUP ---
# You can hardcode your key here or use st.secrets
API_KEY = "" 

if not API_KEY and "api_keys" in st.secrets:
    # Tries to find the key in secrets.toml if not hardcoded
    # Ensure your secrets.toml looks like: [api_keys] keys = ["YOUR_KEY"]
    try:
        API_KEY = st.secrets["api_keys"]["keys"][0]
    except:
        pass

# --- CSS STYLING (Dark/Cinematic) ---
st.markdown("""
<style>
    .stApp { background-color: #0f172a; color: #e2e8f0; }
    .stTextArea textarea, .stTextInput input {
        background-color: #1e293b !important;
        color: #f8fafc !important;
        border: 1px solid #334155 !important;
    }
    .stButton button {
        border-radius: 0.5rem;
        font-weight: 600; 
    }
    /* Highlighting the active scene */
    div[data-testid="stImage"] {
        border: 1px solid #334155;
        border-radius: 10px;
        overflow: hidden;
    }
</style>
""", unsafe_allow_html=True)

# --- SESSION STATE INITIALIZATION ---
if 'step' not in st.session_state:
    st.session_state.update({
        'step': 1,
        'style_prompt': '',
        'style_images': [],       # Global Style Refs: [{'data': b64, 'mime': str}]
        'style_link': '',
        'script_text': '',
        'script_instructions': '',
        'storyboard': [],         # List of {script: str, prompt: str}
        'characters': [],         # List of {key: str, description: str, preview: b64}
        'scene_images': {},       # Generated images {str(index): b64_string}
        'scene_refs': {},         # Scene-specific refs {str(index): [list of {data, mime}]}
        'curr_scene': 0
    })

# ==============================================================================
# 2. CORE FUNCTIONS (API & LOGIC)
# ==============================================================================

def get_api_key():
    # Sidebar input fallback if no key is set
    if not API_KEY:
        return st.sidebar.text_input("Enter Gemini API Key", type="password")
    return API_KEY

def call_gemini_generic(payload, model="flash-preview"):
    """
    Generic handler for Gemini API calls.
    Models: 
    - 'flash-preview': gemini-2.5-flash-preview-09-2025 (Logic/Text)
    - 'image-preview': gemini-2.5-flash-image-preview (Image Gen)
    """
    key = get_api_key()
    if not key:
        st.error("API Key required.")
        return None

    if model == "image-preview":
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image-preview:generateContent"
    else:
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent"

    try:
        response = requests.post(
            f"{url}?key={key}",
            headers={'Content-Type': 'application/json'},
            json=payload
        )
        
        if response.status_code != 200:
            st.error(f"API Error {response.status_code}: {response.text}")
            return None
            
        return response.json()
    except Exception as e:
        st.error(f"Network Error: {e}")
        return None

def clean_json_text(text):
    """Extract JSON from potential markdown blocks"""
    text = text.strip()
    # FIX: Properly checking for markdown code blocks
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()

def handle_file_upload(files):
    """Convert uploaded files to base64 list"""
    processed = []
    for f in files:
        bytes_data = f.getvalue()
        b64 = base64.b64encode(bytes_data).decode('utf-8')
        processed.append({'data': b64, 'mime': f.type})
    return processed

# ==============================================================================
# 3. APP SCREENS
# ==============================================================================

# --- STEP 1: STYLE DEFINITION ---
if st.session_state.step == 1:
    st.title("🎬 AI Storyboard Pro")
    st.markdown("### Step 1: Define Visual Style")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.session_state.style_prompt = st.text_area(
            "Visual Style Description", 
            value=st.session_state.style_prompt,
            height=150,
            placeholder="e.g., Dark cinematic anime, 1980s retro futurism, high contrast, purple and blue neon lighting..."
        )
        st.session_state.style_link = st.text_input("YouTube Context Link (Optional)", value=st.session_state.style_link)

    with col2:
        st.markdown("**Global Style Reference Images**")
        uploaded_style = st.file_uploader("Upload images that define the overall look", accept_multiple_files=True, type=['png', 'jpg', 'jpeg', 'webp'])
        if uploaded_style:
            st.session_state.style_images = handle_file_upload(uploaded_style)
            
        if st.session_state.style_images:
            st.markdown(f"*{len(st.session_state.style_images)} images loaded*")
            # Simple preview grid
            cols = st.columns(4)
            for i, img in enumerate(st.session_state.style_images[:4]):
                cols[i].image(base64.b64decode(img['data']), use_column_width=True)

    if st.button("Next: Script Input ➡️", type="primary", use_container_width=True):
        if not st.session_state.style_prompt and not st.session_state.style_images:
            st.warning("Please provide a text description or images.")
        else:
            st.session_state.step = 2
            st.rerun()


# --- STEP 2: SCRIPT PROCESSING ---
elif st.session_state.step == 2:
    st.title("📝 Step 2: Script & Breakdown")
    
    st.session_state.script_text = st.text_area(
        "Paste your full script here", 
        value=st.session_state.script_text, 
        height=300
    )
    
    st.session_state.script_instructions = st.text_input(
        "Special Breakdown Instructions (Optional)",
        value=st.session_state.script_instructions,
        placeholder="e.g., Focus on close-ups, Ignore minor transitions..."
    )

    if st.button("Generate Scenes & Characters 🚀", type="primary"):
        if not st.session_state.script_text:
            st.error("Please enter a script.")
        else:
            with st.spinner("Analyzing script, breaking down scenes, and extracting characters..."):
                # --- LOGIC FROM REACT FILE ---
                system_instruction = """
                You are a visual storyboard artist. Read the script and style.
                1. OUTPUT JSON: { "storyboard": [{ "script": "...", "prompt": "..." }], "characters": [{ "key": "[Name]", "description": "..." }] }
                2. CRITICAL: Break down the script into VERY small visual moments. Create a separate scene/prompt for:
                    - Every single sentence.
                    - Every 15-20 words of narration.
                    - Or roughly every 5 seconds of reading time.
                3. DO NOT group multiple concepts into one scene. Split them up!
                4. Prompts must match the requested style.
                5. ALWAYS use brackets [ ] for any character reference. e.g., [Adult Griselda], [Police Officer].
                6. Identify new characters and add them to the characters list.
                """
                
                user_instructions = ""
                if st.session_state.script_instructions:
                    user_instructions = f"\nUSER OVERRIDE INSTRUCTIONS: {st.session_state.script_instructions}"

                user_prompt = f"""
                STYLE PROMPT: {st.session_state.style_prompt}
                SCRIPT:
                \"\"\"{st.session_state.script_text}\"\"\"
                {user_instructions}
                """

                # Prepare payload with style images if available
                parts = [{"text": user_prompt}]
                for img in st.session_state.style_images:
                    parts.append({"inlineData": {"mimeType": img['mime'], "data": img['data']}})

                payload = {
                    "contents": [{"parts": parts}],
                    "systemInstruction": {"parts": [{"text": system_instruction}]},
                    "generationConfig": {"responseMimeType": "application/json"}
                }

                res = call_gemini_generic(payload, model="flash-preview")
                
                if res and 'candidates' in res:
                    try:
                        raw_text = res['candidates'][0]['content']['parts'][0]['text']
                        data = json.loads(clean_json_text(raw_text))
                        
                        # Normalize character keys
                        chars = data.get('characters', [])
                        for c in chars:
                            if not c['key'].startswith('['): c['key'] = f"[{c['key']}]"
                        
                        st.session_state.storyboard = data.get('storyboard', [])
                        st.session_state.characters = chars
                        st.session_state.step = 3
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to parse AI response: {e}")
                        st.text(raw_text)


# --- STEP 3: CHARACTER CONSISTENCY ---
elif st.session_state.step == 3:
    st.title("👥 Step 3: Character Lock-in")
    st.info("Define your characters visuals here. These descriptions and images will be passed to EVERY scene generation to ensure consistency.")

    # Helper to generate char preview
    def gen_char_preview(idx):
        char = st.session_state.characters[idx]
        prompt = f"**Force 16:9 landscape. Copy style from reference.** Cinematic shot of {char['description']}, Style: {st.session_state.style_prompt}"
        
        parts = [{"text": prompt}]
        # Add global style images
        for img in st.session_state.style_images:
            parts.append({"inlineData": {"mimeType": img['mime'], "data": img['data']}})
            
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {"responseModalities": ["IMAGE"]}
        }
        
        res = call_gemini_generic(payload, model="image-preview")
        if res and 'candidates' in res:
            b64 = res['candidates'][0]['content']['parts'][0]['inlineData']['data']
            st.session_state.characters[idx]['preview'] = b64

    # UI for Characters
    for i, char in enumerate(st.session_state.characters):
        with st.container():
            col_img, col_txt = st.columns([1, 3])
            
            with col_img:
                if 'preview' in char and char['preview']:
                    st.image(base64.b64decode(char['preview']), use_column_width=True)
                else:
                    st.markdown("<div style='height:100px; background:#1e293b; display:flex; align-items:center; justify-content:center;'>No Preview</div>", unsafe_allow_html=True)
                
                if st.button(f"Generate Preview", key=f"gen_char_{i}"):
                    gen_char_preview(i)
                    st.rerun()

            with col_txt:
                c_name = st.text_input("Name (Key)", value=char['key'], key=f"name_{i}")
                c_desc = st.text_area("Visual Description", value=char['description'], key=f"desc_{i}", height=70)
                
                # Update state on change
                st.session_state.characters[i]['key'] = c_name
                st.session_state.characters[i]['description'] = c_desc

    st.markdown("---")
    col_back, col_next = st.columns([1, 5])
    if col_back.button("Back"):
        st.session_state.step = 2
        st.rerun()
    if col_next.button("Confirm Characters & Start Storyboard ➡️", type="primary"):
        st.session_state.step = 4
        st.rerun()


# --- STEP 4: STORYBOARD GENERATOR ---
elif st.session_state.step == 4:
    
    # --- GENERATION LOGIC (The Core) ---
    def generate_scene_image(index):
        """
        Generates image for a specific scene index using:
        1. Global Style Images
        2. Character Context (from Step 3)
        3. Scene Specific Uploaded References
        4. Previous Scene Image (for continuity)
        """
        scene = st.session_state.storyboard[index]
        
        # 1. Build Context String
        char_context = "\n".join([f"{c['key']}: {c['description']}" for c in st.session_state.characters])
        
        # 2. Build Prompt (React logic)
        final_prompt = f"""
        **FORCE 16:9 LANDSCAPE.** Copy style from reference images.
        STYLE: {st.session_state.style_prompt}
        CONTEXT: {char_context}
        SCENE: {scene['prompt']}
        """

        scene_idx_str = str(index)
        specific_refs = st.session_state.scene_refs.get(scene_idx_str, [])

        if specific_refs:
            final_prompt += f"\nCRITICAL: Use the provided scene-specific images as key visual references."

        # 3. Construct Payload Parts
        parts = [{"text": final_prompt}]

        # A. Global Style Refs
        for img in st.session_state.style_images:
            parts.append({"inlineData": {"mimeType": img['mime'], "data": img['data']}})

        # B. Scene Specific Refs
        for img in specific_refs:
            parts.append({"inlineData": {"mimeType": img['mime'], "data": img['data']}})

        # C. Previous Image (Continuity) - Logic from React file
        # "Use the last image in the list as the previous scene for visual continuity"
        prev_idx_str = str(index - 1)
        if index > 0 and prev_idx_str in st.session_state.scene_images:
            prev_b64 = st.session_state.scene_images[prev_idx_str]
            parts.append({"inlineData": {"mimeType": "image/png", "data": prev_b64}})
            parts[0]['text'] += " (USE THE LAST IMAGE IN THE LIST AS THE PREVIOUS SCENE FOR VISUAL CONTINUITY)"

        # 4. API Call
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {"responseModalities": ["IMAGE"]}
        }
        
        res = call_gemini_generic(payload, model="image-preview")
        
        if res and 'candidates' in res:
            b64 = res['candidates'][0]['content']['parts'][0]['inlineData']['data']
            st.session_state.scene_images[scene_idx_str] = b64
            return True
        return False

    # --- UI LAYOUT ---
    
    # Header
    st.markdown(f"## Storyboard: Scene {st.session_state.curr_scene + 1} / {len(st.session_state.storyboard)}")
    
    current_idx = st.session_state.curr_scene
    current_scene_data = st.session_state.storyboard[current_idx]
    current_idx_str = str(current_idx)

    # Main Viewer
    viewer_col, controls_col = st.columns([2, 1])

    with viewer_col:
        if current_idx_str in st.session_state.scene_images:
            st.image(base64.b64decode(st.session_state.scene_images[current_idx_str]), use_column_width=True)
            
            # Download button for single scene
            b64_data = st.session_state.scene_images[current_idx_str]
            st.download_button("Download Scene", data=base64.b64decode(b64_data), file_name=f"scene_{current_idx+1}.png", mime="image/png")
        else:
            st.markdown("""
            <div style="height: 400px; border: 2px dashed #334155; border-radius: 10px; display: flex; align-items: center; justify-content: center; background: #0f172a;">
                <p style="color: #64748b;">No image generated yet</p>
            </div>
            """, unsafe_allow_html=True)

    with controls_col:
        st.markdown("### Controls")
        
        # Prompt Editor
        new_prompt = st.text_area("Prompt", value=current_scene_data['prompt'], height=100)
        if new_prompt != current_scene_data['prompt']:
            st.session_state.storyboard[current_idx]['prompt'] = new_prompt
            
        # Script Viewer
        st.text_area("Script Snippet", value=current_scene_data['script'], height=80, disabled=True)

        # Scene Specific Ref Upload
        st.markdown("##### Scene Specific Refs")
        uploaded_scene_refs = st.file_uploader("Add objects/refs just for this scene", key=f"up_{current_idx}", accept_multiple_files=True)
        if uploaded_scene_refs:
            processed_refs = handle_file_upload(uploaded_scene_refs)
            # Append or Create
            if current_idx_str not in st.session_state.scene_refs:
                st.session_state.scene_refs[current_idx_str] = []
            st.session_state.scene_refs[current_idx_str].extend(processed_refs)
            st.success("Refs added! Regenerate to use them.")

        # Show existing scene refs count
        ref_count = len(st.session_state.scene_refs.get(current_idx_str, []))
        if ref_count > 0:
            st.caption(f"📎 {ref_count} scene-specific references attached.")
            if st.button("Clear Scene Refs"):
                st.session_state.scene_refs[current_idx_str] = []
                st.rerun()

        # Actions
        col_gen, col_enhance = st.columns(2)
        if col_gen.button("⚡ Generate", type="primary", use_container_width=True):
            with st.spinner("Dreaming..."):
                generate_scene_image(current_idx)
            st.rerun()
            
        if col_enhance.button("✨ Enhance Prompt", use_container_width=True):
            with st.spinner("Improving prompt..."):
                # Quick LLM call to improve prompt
                p = f"Improve this image prompt to be more cinematic and detailed, keeping the style '{st.session_state.style_prompt}': {current_scene_data['prompt']}"
                res = call_gemini_generic({"contents": [{"parts": [{"text": p}]}]})
                if res:
                    txt = res['candidates'][0]['content']['parts'][0]['text']
                    st.session_state.storyboard[current_idx]['prompt'] = txt
                    st.rerun()

    st.markdown("---")
    
    # Navigation & Bulk Actions
    nav_col, bulk_col = st.columns([1, 1])
    
    with nav_col:
        c1, c2, c3 = st.columns(3)
        if c1.button("⬅️ Previous") and current_idx > 0:
            st.session_state.curr_scene -= 1
            st.rerun()
        
        if c2.button("Next ➡️") and current_idx < len(st.session_state.storyboard) - 1:
            st.session_state.curr_scene += 1
            st.rerun()
            
        if c3.button("Add Scene"):
            st.session_state.storyboard.insert(current_idx + 1, {"script": "", "prompt": "New scene description..."})
            st.session_state.curr_scene += 1
            st.rerun()

    with bulk_col:
        c4, c5 = st.columns(2)
        if c4.button("Generate ALL Remaining"):
            progress_bar = st.progress(0)
            total = len(st.session_state.storyboard)
            start = current_idx
            for i in range(start, total):
                if str(i) not in st.session_state.scene_images:
                    with st.spinner(f"Generating Scene {i+1}..."):
                        generate_scene_image(i)
                progress_bar.progress((i + 1 - start) / (total - start))
            st.success("Batch generation complete!")
            st.rerun()
            
        if c5.button("Download All (ZIP)"):
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w") as zf:
                for idx, b64_data in st.session_state.scene_images.items():
                    data = base64.b64decode(b64_data)
                    zf.writestr(f"scene_{int(idx)+1}.png", data)
            
            st.download_button("Download ZIP", data=zip_buffer.getvalue(), file_name="storyboard.zip", mime="application/zip")
