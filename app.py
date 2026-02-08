import streamlit as st
import pandas as pd
import requests
import re
import time

# --- CONFIGURATION ---
st.set_page_config(page_title="Apollo Contact Hunter", layout="wide")

# --- 1. SYNONYM DICTIONARY (THE BACKEND) ---
ROLE_DEFINITIONS = {
    "Owner": ["owner", "co-owner", "proprietor", "sole proprietor"],
    "Founder": ["founder", "co-founder", "founding partner"],
    "CEO": ["ceo", "c.e.o.", "chief executive officer", "chief executive"],
    "COO": ["coo", "c.o.o.", "chief operating officer", "vp of operations", "head of operations"],
    "CFO": ["cfo", "c.f.o.", "chief financial officer", "vp of finance", "head of finance", "finance director"],
    "CMO": ["cmo", "c.m.o.", "chief marketing officer", "vp of marketing", "head of marketing"],
    "CIO/CTO": ["cio", "c.i.o.", "cto", "c.t.o.", "chief investment officer", "chief technology officer", "chief information officer"],
    "President": ["president", "executive director"],
    "Managing Director": ["managing director", "md", "m.d."],
    "Managing Partner": ["managing partner"],
    "Partner": ["partner", "general partner"],
    "Vice President": ["vice president", "vp", "v.p."],
    "Principal": ["principal"],
    "Head": ["head of"],
    "Director": ["director"]
}

# --- UTILS: DOMAIN CLEANING ---
def clean_domain(input_str):
    if not input_str: return ""
    text = input_str.strip().lower()
    text = re.sub(r'^https?://', '', text)
    text = re.sub(r'^www\.', '', text)
    match = re.match(r'^([^/?#]+)', text)
    if match: return match.group(1).strip()
    return ""

# --- LOGIC: DYNAMIC RANKING ---
def get_contact_score(title, selected_roles_ordered):
    if not title: return 999
    t = title.lower()
    for index, role_label in enumerate(selected_roles_ordered):
        keywords = ROLE_DEFINITIONS[role_label]
        for pattern in keywords:
            p = pattern.lower()
            if len(p) <= 4:
                 if re.search(r'\b' + re.escape(p) + r'\b', t): return index
            else:
                if p in t: return index
    return 999

# --- NAME PARSING (FIXED LOGIC) ---
def parse_contact_name(person_dict):
    """
    Forces Full Name reconstruction to avoid incomplete API names.
    """
    # 1. Extract raw values
    first = person_dict.get('first_name')
    last = person_dict.get('last_name')
    raw_full_source = person_dict.get('name') or person_dict.get('label')
    
    # 2. Normalize to strings
    if first: first = first.strip()
    else: first = ""
    
    if last: last = last.strip()
    else: last = ""
    
    if raw_full_source: raw_full_source = raw_full_source.strip()
    else: raw_full_source = ""

    # 3. Fallback: If Last Name is missing, try to harvest it from the source string
    if (not last) and raw_full_source:
        parts = raw_full_source.split()
        if len(parts) > 1:
            # Assume the very last word is the last name
            last = parts[-1]
            # If first name was also missing, reconstruct it from the remainder
            if not first: 
                first = " ".join(parts[:-1])
        elif len(parts) == 1:
            # Single word name found
            if not first: 
                first = parts[0]
    
    # 4. CRUCIAL: Always rebuild Full Name from components
    # This overrides the API's potentially incomplete 'name' field
    full = f"{first} {last}".strip()
        
    return first, last, full

# --- API HANDLER ---
def fetch_contacts(domain, api_key, target_titles_flat, selected_roles_ordered, max_contacts, skip_n=0, should_reveal=False):
    search_url = "https://api.apollo.io/v1/mixed_people/api_search"
    headers = {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-cache',
        'X-Api-Key': api_key.strip()
    }
    
    # Always fetch Page 1 to get the actual employees of small firms
    search_payload = {
        "q_organization_domains": domain,
        "contact_email_status": ["verified"],
        "person_titles": target_titles_flat,
        "page": 1, 
        "per_page": 100 
    }
    
    try:
        res = requests.post(search_url, headers=headers, json=search_payload)
        if res.status_code != 200:
            return [], f"Search Error ({res.status_code})"

        data = res.json()
        people = data.get('people', [])
        
        if not people:
            return [], "No contacts found"

        candidates = []
        for p in people:
            title = p.get('title', '')
            score = get_contact_score(title, selected_roles_ordered)
            
            first_name, last_name, full_name = parse_contact_name(p)

            if score < 999:
                candidates.append({
                    'id': p.get('id'), 
                    'Name': full_name,
                    'First': first_name,
                    'Last': last_name,
                    'Title': title,
                    'Email': p.get('email') or "N/A", 
                    'Score': score
                })
        
        if not candidates:
            return [], "No matching titles found"

        # 1. Sort by Hierarchy (Best matches first)
        candidates.sort(key=lambda x: x['Score'])
        
        # 2. ROTATION LOGIC: Skip the top N matches
        if skip_n > 0:
            if skip_n >= len(candidates):
                return [], "Skipped all available contacts"
            candidates = candidates[skip_n:]
        
        # 3. Slice the requested amount
        top_candidates = candidates[:max_contacts]
        
        # REVEAL LOOP
        final_results = []
        for cand in top_candidates:
            if should_reveal and cand['Email'] == "N/A":
                reveal_url = "https://api.apollo.io/v1/people/bulk_match"
                reveal_payload = {
                    "details": [{"id": cand['id']}],
                    "reveal_personal_emails": True
                }
                rev_res = requests.post(reveal_url, headers=headers, json=reveal_payload)
                if rev_res.status_code == 200:
                    rev_data = rev_res.json()
                    matches = rev_data.get('matches', [])
                    if matches:
                        m = matches[0]
                        revealed_email = (m.get('email') or m.get('email_address') or m.get('sanitized_email'))
                        if revealed_email:
                            cand['Email'] = revealed_email
                        
                        # Double check name from reveal data
                        f, l, fu = parse_contact_name(m)
                        if not cand['Last'] and l:
                             cand['First'] = f
                             cand['Last'] = l
                             cand['Name'] = fu

            final_results.append(cand)

        return final_results, None

    except Exception as e:
        return [], f"Error: {str(e)}"

# --- MAIN UI ---
def main():
    st.title("Apollo Priority Contact Hunter")
    st.markdown("**Goal:** Find top verified contacts per domain (Wide Format).")

    col1, col2 = st.columns([1, 2])
    
    with col1:
        api_key_input = st.text_input("Apollo API Key", type="password")
        
        st.write("---")
        st.write("### Target Roles (In Priority Order)")
        
        selected_roles = st.multiselect(
            "Select Hierarchy:",
            options=list(ROLE_DEFINITIONS.keys()),
            default=["Owner", "Founder", "CEO", "Managing Director"]
        )
        
        st.write("---")
        st.write("### Settings")
        
        num_contacts = st.slider("Contacts per Domain:", min_value=1, max_value=5, value=2)
        
        # New Feature: Rotation Slider
        skip_n = st.slider(
            "Skip Top N Matches (Rotate):", 
            min_value=0, 
            max_value=20, 
            value=0, 
            help="Increase this to ignore the top matches and find the NEXT best people (e.g. set to 2 to skip the first 2 people)."
        )
        
        use_reveal = st.checkbox("Reveal Emails", value=False)
        if use_reveal:
            st.warning(f"Warning: Could use {num_contacts} credits per domain.")
            
        name_format = st.radio("Name Output Format:", options=["Separate Columns", "Full Name"], index=0)

    with col2:
        domain_input = st.text_area("Domains (One per line)", height=250, placeholder="apple.com\nopenai.com")

    if st.button("Run Search", type="primary"):
        if not api_key_input or not domain_input.strip():
            st.error("Missing Data.")
            return
            
        if not selected_roles:
            st.error("Select Target Roles.")
            return
        
        target_search_terms = []
        for role in selected_roles:
            target_search_terms.extend(ROLE_DEFINITIONS[role])
        
        raw_lines = [line.strip() for line in domain_input.split('\n') if line.strip()]
        processed_rows = []
        
        progress = st.progress(0)
        status_text = st.empty()
        
        for i, raw_line in enumerate(raw_lines):
            clean_dom = clean_domain(raw_line)
            status_text.text(f"Scanning: {clean_dom}...")
            
            contacts, error = fetch_contacts(
                clean_dom, 
                api_key_input, 
                target_search_terms, 
                selected_roles, 
                num_contacts,
                skip_n=skip_n, # Pass skip value
                should_reveal=use_reveal
            )
            
            # --- BUILD WIDE ROW ---
            row = {'Clean Domain': clean_dom}
            
            if error:
                row['Status'] = error
            elif not contacts:
                row['Status'] = 'No Matches'
            else:
                row['Status'] = 'Success'
                # Flatten contacts into columns
                for idx, c in enumerate(contacts):
                    suffix = f"_{idx+1}" # e.g., _1, _2
                    
                    row[f"Title{suffix}"] = c['Title']
                    row[f"Email{suffix}"] = c['Email']
                    
                    if name_format == "Separate Columns":
                        row[f"First Name{suffix}"] = c['First']
                        row[f"Last Name{suffix}"] = c['Last']
                    else:
                        row[f"Name{suffix}"] = c['Name']
            
            processed_rows.append(row)
            progress.progress((i + 1) / len(raw_lines))
            time.sleep(0.2)
            
        progress.empty()
        status_text.success("Done!")
        
        if processed_rows:
            df = pd.DataFrame(processed_rows)
            
            # Reorder columns
            base_cols = ['Status', 'Clean Domain']
            dynamic_cols = [c for c in df.columns if c not in base_cols]
            
            # Sort dynamic cols logically
            def sort_key(col_name):
                match = re.search(r'_(\d+)$', col_name)
                if match:
                    num = int(match.group(1))
                    rank = 0
                    if "First" in col_name or "Name" in col_name and "Last" not in col_name: rank = 1
                    elif "Last" in col_name: rank = 2
                    elif "Title" in col_name: rank = 3
                    elif "Email" in col_name: rank = 4
                    return num * 10 + rank
                return 999

            dynamic_cols.sort(key=sort_key)
            final_cols = base_cols + dynamic_cols
            
            df_display = df[final_cols]
            
            st.divider()
            st.subheader("Search Results")
            
            # Success Counter
            success_count = len(df[df['Status'] == 'Success'])
            st.metric("Matches Found", f"{success_count} / {len(raw_lines)}")
            
            st.dataframe(df_display, use_container_width=True)
            
            st.markdown("### Copy for Google Sheets")
            tsv_data = df_display.to_csv(index=False, sep='\t')
            st.code(tsv_data, language="text")

if __name__ == "__main__":
    main()