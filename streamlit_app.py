import streamlit as st
import os
import re
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup
import zipfile
import io

# -----------------------------
# Constants & initial setup
# -----------------------------
BASE_URL = "https://va.mite.gov.it"
DOWNLOAD_FOLDER = "downloads"
DELAY_BETWEEN_REQUESTS = 1.0  # polite delay in seconds

if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

# -----------------------------
# Helper Functions
# -----------------------------
def build_search_url(keyword: str, search_type="o", page: int = 1):
    search_endpoint = "/it-IT/Ricerca/ViaLibera"
    params = {
        "Testo": keyword,
        "t": search_type,
        "pagina": page
    }
    return f"{BASE_URL}{search_endpoint}?{urllib.parse.urlencode(params)}"


def find_total_pages(soup) -> int:
    """
    Extract the total number of pages from the pagination section,
    typically found in a <li class="etichettaRicerca">Pagina 1 di 8</li>.
    Adjust this logic if the site changed its structure.
    """
    pag_ul = soup.find("ul", class_="pagination")
    if not pag_ul:
        return 1

    label_li = pag_ul.find("li", class_="etichettaRicerca")
    if not label_li:
        return 1

    match = re.search(r'Pagina\s+(\d+)\s+di\s+(\d+)', label_li.text)
    if match:
        return int(match.group(2))
    return 1

# ---------------------------------------
# Step 1: Collect search results (paged)
# ---------------------------------------
@st.cache_data
def collect_search_results(keyword: str, search_type="o"):
    """ Gather all detail page URLs across paginated search results. """
    all_links = []
    page = 1

    while True:
        url = build_search_url(keyword, search_type=search_type, page=page)
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            st.warning(f"Failed to fetch {url}: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")

        if search_type == 'o':
            link_pattern = "/it-IT/Oggetti/Info/"
        else:
            link_pattern = "/it-IT/Oggetti/Documentazione/"

        # Parse links from current page
        for a in soup.select(f"a[href*='{link_pattern}']"):
            href = a.get("href", "")
            full_url = urllib.parse.urljoin(BASE_URL, href)
            if full_url not in all_links:
                all_links.append(full_url)

        # Check for more pages
        total_pages = find_total_pages(soup)
        if page >= total_pages:
            break

        page += 1
        time.sleep(DELAY_BETWEEN_REQUESTS)

    return all_links


def get_project_id(detail_url: str) -> str:
    """
    Extract a numeric ID from detail URLs: /Info/1234 or /Documentazione/5678
    """
    match = re.search(r'(?:Info|Documentazione)/(\d+)', detail_url)
    if match:
        return match.group(1)
    return "UnknownProject"


def get_procedura_links(detail_url: str):
    """
    Step 2: From the detail page, gather links to the actual "procedure" pages.
    """
    try:
        resp = requests.get(detail_url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        st.warning(f"Could not retrieve {detail_url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    procedura_links = []

    link_pattern = "/it-IT/Oggetti/Documentazione/"
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if link_pattern in href:
            full_url = urllib.parse.urljoin(BASE_URL, href)
            if full_url not in procedura_links:
                procedura_links.append(full_url)

    return procedura_links

# ------------------------------------------------
# Step 3: Multi-page Document Retrieval
# ------------------------------------------------
def get_document_links(procedura_url: str):
    """
    Inside a procedure page, find the final "Scarica documento" links
    in the 'Documentazione' table. Now handles pagination of that table.

    Example usage:
    doc_links = get_document_links("https://va.mite.gov.it/it-IT/Oggetti/Documentazione/1234")
    """
    print(f"[INFO] Parsing procedure page => {procedura_url}")
    doc_links = []
    page = 1

    while True:
        # If it's page 1, we use the base URL. Otherwise, append ?pagina=2, ?pagina=3, etc.
        # If the URL already has a query (?foo=bar), then use &pagina=2.
        if page == 1:
            url = procedura_url
        else:
            join_char = "&" if "?" in procedura_url else "?"
            url = f"{procedura_url}{join_char}pagina={page}"

        print(f"[INFO] Fetching document page => {url}")
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[WARN] Could not retrieve {url}: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", class_="Documentazione")

        if not table:
            print(f"[WARN] No 'Documentazione' table found in {url}. Possibly no documents on this page.")
            break

        # Process current page's documents
        rows = table.find_all("tr")[1:]  # skip header row
        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 9:
                continue

            nome_file = cols[1].get_text(strip=True)
            download_td = cols[8]
            download_a = download_td.find("a", href=True, title="Scarica il documento")
            if download_a:
                href = download_a["href"]
                download_url = urllib.parse.urljoin(BASE_URL, href)
                doc_links.append((download_url, nome_file))

        # Check if there are more document pages
        total_pages = find_total_pages(soup)
        print(f"[INFO] Processing documents page {page}/{total_pages}")

        if page >= total_pages:
            break

        page += 1
        time.sleep(DELAY_BETWEEN_REQUESTS)

    print(f"[INFO] Found {len(doc_links)} total document links in {procedura_url}.")
    return doc_links


def download_file(url: str, nome_file: str, save_path: str):
    """
    Step 4: Download the file from the given URL, saving it under 'nome_file' in 'save_path'.
    """
    safe_filename = re.sub(r'[\\/*?:"<>|]', "_", nome_file)
    local_path = os.path.join(save_path, safe_filename)

    if os.path.exists(local_path):
        st.info(f"File '{safe_filename}' already exists. Skipping.")
        return

    try:
        st.write(f"Downloading: {url}")
        with requests.get(url, stream=True, timeout=20) as r:
            r.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

        st.success(f"Saved => {local_path}")
    except Exception as e:
        st.error(f"Failed to download {url}: {e}")


def zip_folder_contents(folder_path: str) -> bytes:
    """
    Recursively zip all files in folder_path into an in-memory zip.
    Returns the zip as raw bytes, which can be served via st.download_button.
    """
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, start=folder_path)
                zf.write(file_path, arcname)
    zip_buffer.seek(0)
    return zip_buffer.getvalue()

# -------------------------
# Streamlit App / Main UI
# -------------------------
def main():
    st.title("Scraping App for va.mite.gov.it — Multi-Page Documents")
    st.write("Enter a keyword, select Progetti or Documenti, then press **Search**. "
             "Now the code supports multi-page document tables on procedure pages.")

    keyword = st.text_input("Keyword to search", "")
    search_type_map = {"Progetti (o)": "o", "Documenti (d)": "d"}
    search_type_choice = st.selectbox(
        "Choose search type:",
        list(search_type_map.keys()),
        index=0
    )
    search_type = search_type_map[search_type_choice]

    # Keep track of results in session state so user can trigger downloads
    if "detail_urls" not in st.session_state:
        st.session_state.detail_urls = []
    if "results_info" not in st.session_state:
        st.session_state.results_info = []  # Will store (detail_url, procedure_url, doc_links, project_folder)
    if "base_save_dir" not in st.session_state:
        st.session_state.base_save_dir = None

    # Search button
    if st.button("Search"):
        if not keyword.strip():
            st.error("Please provide a keyword before searching.")
            return

        # Clear old data
        st.session_state.detail_urls.clear()
        st.session_state.results_info.clear()
        st.session_state.base_save_dir = None

        safe_keyword = re.sub(r'[\\/*?:"<>|]', "_", keyword)
        search_type_full = "Progetti" if search_type == "o" else "Documenti"
        base_save_dir = os.path.join(DOWNLOAD_FOLDER, safe_keyword, search_type_full)
        os.makedirs(base_save_dir, exist_ok=True)
        st.session_state.base_save_dir = base_save_dir

        # Step 1: collect detail URLs
        with st.spinner("Collecting search results..."):
            detail_urls = collect_search_results(keyword, search_type=search_type)
        st.success(f"Found {len(detail_urls)} detail URLs.")

        st.session_state.detail_urls = detail_urls

        # Step 2: For each detail, get procedure pages, then docs (with pagination!)
        for detail_url in detail_urls:
            project_id = get_project_id(detail_url)
            project_folder = os.path.join(base_save_dir, project_id)
            os.makedirs(project_folder, exist_ok=True)

            procedure_urls = get_procedura_links(detail_url)
            for proc_url in procedure_urls:
                doc_links = get_document_links(proc_url)
                st.session_state.results_info.append(
                    (detail_url, proc_url, doc_links, project_folder)
                )

        st.success("All procedures parsed. Expand below or proceed to download documents.")

    # Show results after search
    if st.session_state.detail_urls:
        st.write("---")
        st.write("## Found Procedure & Document Links")

        for (detail_url, proc_url, doc_links, project_folder) in st.session_state.results_info:
            st.write(f"**Detail URL**: {detail_url}")
            st.write(f"&emsp;**Procedure URL**: {proc_url}")
            st.write(f"&emsp;Documents found: **{len(doc_links)}**")
            if doc_links:
                with st.expander(f"Show documents for {proc_url}"):
                    for (durl, nome_file) in doc_links:
                        st.write(f"- **File**: {nome_file}, **Link**: {durl}")

        # Download options
        st.write("---")
        st.write("## Download Options")

        if st.button("Download All Locally"):
            st.write("**Starting bulk download...**")
            for (detail_url, proc_url, doc_links, project_folder) in st.session_state.results_info:
                for (durl, nome_file) in doc_links:
                    download_file(durl, nome_file, project_folder)
                    time.sleep(DELAY_BETWEEN_REQUESTS)
            st.success("All files have been downloaded to local disk.")

        if st.button("Create a ZIP of All Downloaded Files"):
            if st.session_state.base_save_dir:
                st.write("Zipping documents, please wait...")
                zip_bytes = zip_folder_contents(st.session_state.base_save_dir)
                st.download_button(
                    label="Download ZIP",
                    data=zip_bytes,
                    file_name="all_documents.zip",
                    mime="application/zip"
                )
            else:
                st.error("No base folder found. Perform a search first.")

if __name__ == "__main__":
    main()
