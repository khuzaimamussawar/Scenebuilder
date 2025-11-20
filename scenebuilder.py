return text.strip()

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
            # ADDED: Explicit Override Logic
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
            
            # UPDATED PROMPT for style adherence
            prompt_text = (
                f"Use the provided STYLE_REFERENCE_IMAGES as the primary visual guide. "
                f"Style: {st.session_state.style_prompt}. "
                f"Aspect Ratio: 16:9. Characters: {char_ctx}. "
                f"Action: {scene_data['prompt']}. "
                f"Match visual continuity with previous scenes."
            )
            
            parts = [{"text": prompt_text}]
            
            # 1. Global Style
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
            
            # UPLOAD REF (FIXED DUPLICATION)
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

        if b5.button("Gen Remaining", key="gen_all"):
            prog = st.progress(0)
            status = st.empty()
            for i in range(total):
                if str(i) not in st.session_state.scene_images:
                    status.text(f"Generating scene {i+1}...")
                    generate_scene_logic(i, model_mode)
                    time.sleep(0.5)
                prog.progress((i+1)/total)
            st.success("Done!")
            st.rerun()
            
        if b6.button("Download All", key="dl_all"):
             st.download_button("Download ZIP", data=create_zip(), file_name="storyboard.zip", mime="application/zip")
